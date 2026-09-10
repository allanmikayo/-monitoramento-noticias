-- =========================================================================
-- MIGRACAO MANUAL -- para colar no SQL Editor da Supabase
--
-- POR QUE ISTO EXISTE (09/09/2026). `python -m scripts.init_db` faz o mesmo
-- trabalho, e e o caminho normal. Mas ele depende de a instancia atender um
-- cliente remoto pelo pooler -- e quando a instancia esta no limite, ela nao
-- atende: em 09/09 chegou a cancelar ate um ROLLBACK por statement timeout.
--
-- Este arquivo roda DENTRO do servidor. Sem pooler, sem cliente, sem rede no
-- meio. E o caminho para destravar quando o outro nao anda.
--
-- COMO USAR: Supabase -> SQL Editor -> New query -> colar tudo -> Run.
-- E idempotente: rodar duas vezes nao faz mal nenhum.
--
-- GERADO DE `app/db.py::run_migrations` -- se aquela lista mudar, gere de
-- novo em vez de editar este arquivo a mao.
-- =========================================================================

SET lock_timeout = '10s';
SET statement_timeout = '5min';

-- -------------------------------------------------------------------------
-- 1. Colunas novas. `IF NOT EXISTS` faz cada uma ser um nao-evento quando ja
--    existe -- sem trava, sem custo.
-- -------------------------------------------------------------------------
ALTER TABLE run_logs ADD COLUMN IF NOT EXISTS sources_json TEXT DEFAULT '[]';
ALTER TABLE articles ADD COLUMN IF NOT EXISTS is_covered BOOLEAN DEFAULT TRUE;
ALTER TABLE debentures ADD COLUMN IF NOT EXISTS company_id INTEGER;
ALTER TABLE negocios_b3 ADD COLUMN IF NOT EXISTS spread FLOAT;
ALTER TABLE debentures ADD COLUMN IF NOT EXISTS referencia_ntnb VARCHAR(20);
ALTER TABLE ntnb_referencia ADD COLUMN IF NOT EXISTS curva_json TEXT;
ALTER TABLE debentures ADD COLUMN IF NOT EXISTS issuer_id INTEGER;
ALTER TABLE securitizados ADD COLUMN IF NOT EXISTS issuer_id INTEGER;
ALTER TABLE securitizado_spreads ADD COLUMN IF NOT EXISTS pct_reune FLOAT;
ALTER TABLE debentures ADD COLUMN IF NOT EXISTS setor VARCHAR(80);
ALTER TABLE debentures ADD COLUMN IF NOT EXISTS subsetor VARCHAR(80);
ALTER TABLE debentures ADD COLUMN IF NOT EXISTS grupo_economico VARCHAR(120);
ALTER TABLE securitizados ADD COLUMN IF NOT EXISTS setor VARCHAR(80);
ALTER TABLE securitizados ADD COLUMN IF NOT EXISTS subsetor VARCHAR(80);
ALTER TABLE securitizados ADD COLUMN IF NOT EXISTS grupo_economico VARCHAR(120);

-- -------------------------------------------------------------------------
-- 2. Alargamento de VARCHAR, so quando faltar de verdade. `ALTER COLUMN TYPE`
--    reconstroi os indices da coluna e pega ACCESS EXCLUSIVE -- caro em
--    `debentures.codigo`, que e a chave primaria, e inutil quando a largura
--    ja esta certa. O DROP da view e necessario porque o Postgres recusa
--    mexer no tipo de coluna que uma view seleciona.
-- -------------------------------------------------------------------------
DO $$
DECLARE precisa boolean := false;
BEGIN
  SELECT bool_or(coalesce(character_maximum_length, 0) < alvo) INTO precisa
    FROM (VALUES ('debentures','codigo',40), ('debentures','indexador',30), ('debentures','incentivada',20), ('debentures','cnpj',30)) AS q(tab, col, alvo)
    JOIN information_schema.columns c
      ON c.table_schema = current_schema()
     AND c.table_name = q.tab AND c.column_name = q.col;

  IF NOT precisa THEN
    RAISE NOTICE 'larguras ja estao certas -- nada a fazer';
    RETURN;
  END IF;

  DROP VIEW IF EXISTS v_spread_rating;
  IF (SELECT coalesce(character_maximum_length, 0)
        FROM information_schema.columns
       WHERE table_schema = current_schema()
         AND table_name = 'debentures' AND column_name = 'codigo') < 40 THEN
    EXECUTE 'ALTER TABLE debentures ALTER COLUMN codigo TYPE VARCHAR(40)';
  END IF;
  IF (SELECT coalesce(character_maximum_length, 0)
        FROM information_schema.columns
       WHERE table_schema = current_schema()
         AND table_name = 'debentures' AND column_name = 'indexador') < 30 THEN
    EXECUTE 'ALTER TABLE debentures ALTER COLUMN indexador TYPE VARCHAR(30)';
  END IF;
  IF (SELECT coalesce(character_maximum_length, 0)
        FROM information_schema.columns
       WHERE table_schema = current_schema()
         AND table_name = 'debentures' AND column_name = 'incentivada') < 20 THEN
    EXECUTE 'ALTER TABLE debentures ALTER COLUMN incentivada TYPE VARCHAR(20)';
  END IF;
  IF (SELECT coalesce(character_maximum_length, 0)
        FROM information_schema.columns
       WHERE table_schema = current_schema()
         AND table_name = 'debentures' AND column_name = 'cnpj') < 30 THEN
    EXECUTE 'ALTER TABLE debentures ALTER COLUMN cnpj TYPE VARCHAR(30)';
  END IF;

  -- A view volta aqui mesmo: deixar para o `init_db` seria contar com um
  -- segundo passo que pode nao acontecer, e sem `v_spread_rating` a aba Banco
  -- de Dados e o `app/spreads/analitico.py` quebram. Copiada de
  -- `app/spreads/views.py::V_SPREAD_RATING`.
  EXECUTE $view$CREATE VIEW v_spread_rating AS
SELECT
    s.codigo,
    s.data,
    s.taxa_indicativa,
    s.pu,
    s.pct_pu_par,
    s.spread,
    s.estoque,
    s.duration,
    d.nome                AS nome_debenture,
    d.indexador,
    d.incentivada,
    d.classe,
    d.issuer_id,
    i.nome                AS emissor,
    i.setor,
    i.sub_setor,
    i.grupo_economico,
    i.company_id,
    -- PRECEDÊNCIA: rating da emissão vence o do emissor; dentro do mesmo
    -- nível, o histórico observado vence o derivado das ações.
    --
    -- CASE (testando `id IS NOT NULL`), NÃO `COALESCE` campo a campo.
    --
    -- BUG REAL (04/08/2026): a 1ª versão usava
    -- `COALESCE(pth.rating_medio, ptd.rating_medio, ...)` em cada coluna
    -- separadamente. O COALESCE avalia coluna por coluna, então os campos
    -- de uma mesma linha podiam vir de PERÍODOS DIFERENTES -- apareceu
    -- `rating_medio = 'N.A.'` (do período da emissão) junto com
    -- `notch_medio = 5` (do período do emissor, porque o da emissão tinha
    -- notch nulo). Rating e notch se contradizendo é o pior caso: o
    -- gráfico ordena por notch e rotula pelo rating.
    --
    -- Testando a EXISTÊNCIA do período (`id IS NOT NULL`) em vez do valor
    -- da coluna, todos os campos saem do mesmo período, sempre.
    CASE WHEN pth.id IS NOT NULL THEN pth.rating_medio
         WHEN ptd.id IS NOT NULL THEN ptd.rating_medio
         WHEN pih.id IS NOT NULL THEN pih.rating_medio
         WHEN pid.id IS NOT NULL THEN pid.rating_medio
         ELSE 'N.A.' END AS rating_medio,
    CASE WHEN pth.id IS NOT NULL THEN pth.notch_medio
         WHEN ptd.id IS NOT NULL THEN ptd.notch_medio
         WHEN pih.id IS NOT NULL THEN pih.notch_medio
         WHEN pid.id IS NOT NULL THEN pid.notch_medio
         END AS notch_medio,
    CASE WHEN pth.id IS NOT NULL THEN pth.fitch
         WHEN ptd.id IS NOT NULL THEN ptd.fitch
         WHEN pih.id IS NOT NULL THEN pih.fitch
         WHEN pid.id IS NOT NULL THEN pid.fitch
         END AS fitch,
    CASE WHEN pth.id IS NOT NULL THEN pth.sp
         WHEN ptd.id IS NOT NULL THEN ptd.sp
         WHEN pih.id IS NOT NULL THEN pih.sp
         WHEN pid.id IS NOT NULL THEN pid.sp
         END AS sp,
    CASE WHEN pth.id IS NOT NULL THEN pth.moodys
         WHEN ptd.id IS NOT NULL THEN ptd.moodys
         WHEN pih.id IS NOT NULL THEN pih.moodys
         WHEN pid.id IS NOT NULL THEN pid.moodys
         END AS moodys,
    CASE
        WHEN pth.id IS NOT NULL OR ptd.id IS NOT NULL THEN 'EMISSAO'
        WHEN pih.id IS NOT NULL OR pid.id IS NOT NULL THEN 'EMISSOR'
        ELSE 'SEM_RATING'
    END AS rating_escopo
FROM debenture_spreads s
JOIN debentures d ON d.codigo = s.codigo
LEFT JOIN issuers i ON i.id = d.issuer_id

    LEFT JOIN issuer_rating_periodo pth
           ON pth.codigo = d.codigo AND pth.origem = 'HISTORICO'
          AND pth.data_inicio <= s.data
          AND (pth.data_fim IS NULL OR s.data < pth.data_fim)


    LEFT JOIN issuer_rating_periodo ptd
           ON ptd.codigo = d.codigo AND ptd.origem = 'DERIVADO'
          AND ptd.data_inicio <= s.data
          AND (ptd.data_fim IS NULL OR s.data < ptd.data_fim)


    LEFT JOIN issuer_rating_periodo pih
           ON pih.issuer_id = d.issuer_id AND pih.codigo IS NULL AND pih.origem = 'HISTORICO'
          AND pih.data_inicio <= s.data
          AND (pih.data_fim IS NULL OR s.data < pih.data_fim)


    LEFT JOIN issuer_rating_periodo pid
           ON pid.issuer_id = d.issuer_id AND pid.codigo IS NULL AND pid.origem = 'DERIVADO'
          AND pid.data_inicio <= s.data
          AND (pid.data_fim IS NULL OR s.data < pid.data_fim)$view$;

  RAISE NOTICE 'larguras ajustadas e v_spread_rating recriada';
END $$;

-- -------------------------------------------------------------------------
-- 3. Indices.
-- -------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_articles_data
    ON articles (coalesce(published_at, found_at) DESC);
CREATE INDEX IF NOT EXISTS ix_debenture_taxonomia
    ON debentures (classe, setor, subsetor);

-- -------------------------------------------------------------------------
-- 4. Conferencia. As tres colunas da taxonomia e os dois indices tem que
--    aparecer aqui. Se aparecerem, o `importar_taxonomia` roda.
-- -------------------------------------------------------------------------
SELECT 'coluna' AS tipo, column_name AS nome,
       coalesce(character_maximum_length::text, '-') AS detalhe
  FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'debentures'
   AND column_name IN ('setor', 'subsetor', 'grupo_economico', 'codigo')
UNION ALL
SELECT 'coluna securitizados', column_name, '-'
  FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = 'securitizados'
   AND column_name IN ('setor', 'subsetor', 'grupo_economico')
UNION ALL
SELECT 'indice', indexname, '-'
  FROM pg_indexes
 WHERE indexname IN ('ix_articles_data', 'ix_debenture_taxonomia')
UNION ALL
SELECT 'view', viewname, '-'
  FROM pg_views WHERE viewname = 'v_spread_rating'
 ORDER BY 1, 2;
