-- =========================================================================
-- REVISAO DO MODELO -- mede o que o esquema so levanta como suspeita
--
-- Rode inteiro no DBeaver (ou no Adminer) e me mande as tres saidas.
-- Nada aqui escreve nada: sao cinco SELECTs.
-- =========================================================================

-- -------------------------------------------------------------------------
-- 1. ORFAOS NAS JUNCOES SEM CHAVE
--
-- Quatro colunas `codigo` se juntam a `debentures` sem chave estrangeira
-- declarada. A pergunta nao e "deveria ter chave?" e sim "os dados hoje
-- aguentariam uma?" -- porque uma chave estrangeira nao e documentacao, e
-- uma REGRA: com ela no lugar, um INSERT com codigo desconhecido passa a
-- falhar, e o coletor quebra em vez de gravar.
-- -------------------------------------------------------------------------
SELECT 'negocios_b3'           AS tabela, COUNT(*) AS linhas_orfas,
       COUNT(DISTINCT n.codigo) AS codigos_distintos_orfaos
  FROM negocios_b3 n
 WHERE n.codigo IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM debentures d WHERE d.codigo = n.codigo)
UNION ALL
SELECT 'negocios_b3_diario', COUNT(*), COUNT(DISTINCT n.codigo)
  FROM negocios_b3_diario n
 WHERE n.codigo IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM debentures d WHERE d.codigo = n.codigo)
UNION ALL
SELECT 'issuer_ratings', COUNT(*), COUNT(DISTINCT r.codigo)
  FROM issuer_ratings r
 WHERE r.codigo IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM debentures d WHERE d.codigo = r.codigo)
UNION ALL
SELECT 'issuer_rating_periodo', COUNT(*), COUNT(DISTINCT r.codigo)
  FROM issuer_rating_periodo r
 WHERE r.codigo IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM debentures d WHERE d.codigo = r.codigo)
 ORDER BY 1;

-- -------------------------------------------------------------------------
-- 2. RISCO DE TRUNCAMENTO NAS COLUNAS REPETIDAS
--
-- `setor` existe em tres tabelas com tres larguras: issuers(120),
-- debentures(80), securitizados(80). Isso nao e so redundancia -- e um
-- valor que cabe numa e nao cabe na outra. Se algum processo copiar de
-- issuers para debentures, o Postgres CORTA em silencio se a coluna for
-- CHAR, ou recusa a linha inteira se for VARCHAR. As duas saidas sao ruins.
--
-- `folga` negativa = ja existe valor que nao caberia na coluna mais estreita.
-- -------------------------------------------------------------------------
SELECT 'setor' AS conceito, 'issuers' AS tabela, MAX(LENGTH(setor)) AS maior_valor,
       120 AS limite_aqui, 80 AS limite_mais_estreito, 80 - MAX(LENGTH(setor)) AS folga
  FROM issuers
UNION ALL
SELECT 'setor', 'debentures', MAX(LENGTH(setor)), 80, 80, 80 - MAX(LENGTH(setor)) FROM debentures
UNION ALL
SELECT 'setor', 'securitizados', MAX(LENGTH(setor)), 80, 80, 80 - MAX(LENGTH(setor)) FROM securitizados
UNION ALL
SELECT 'grupo_economico', 'issuers', MAX(LENGTH(grupo_economico)), 200, 120,
       120 - MAX(LENGTH(grupo_economico)) FROM issuers
UNION ALL
SELECT 'grupo_economico', 'debentures', MAX(LENGTH(grupo_economico)), 120, 120,
       120 - MAX(LENGTH(grupo_economico)) FROM debentures
UNION ALL
SELECT 'indexador', 'debentures', MAX(LENGTH(indexador)), 30, 20,
       20 - MAX(LENGTH(indexador)) FROM debentures
UNION ALL
SELECT 'indexador', 'securitizados', MAX(LENGTH(indexador)), 20, 20,
       20 - MAX(LENGTH(indexador)) FROM securitizados
 ORDER BY 1, 2;

-- -------------------------------------------------------------------------
-- 3. DUAS FONTES DE VERDADE PARA SETOR: ELAS DIVERGEM?
--
-- `debentures.setor` vem da sua planilha; `issuers.setor` vem do cadastro
-- de emissores. A aba Spreads usa a primeira. Se as duas concordam sempre,
-- uma delas e redundante e da para aposentar. Se divergem, a pergunta muda:
-- qual manda?
-- -------------------------------------------------------------------------
SELECT CASE
         WHEN d.setor IS NULL AND i.setor IS NULL THEN 'nenhuma das duas tem'
         WHEN d.setor IS NULL                     THEN 'so issuers tem'
         WHEN i.setor IS NULL                     THEN 'so debentures tem'
         WHEN d.setor = i.setor                   THEN 'concordam'
         ELSE                                          'DIVERGEM'
       END AS situacao,
       COUNT(*) AS papeis
  FROM debentures d
  LEFT JOIN issuers i ON i.id = d.issuer_id
 GROUP BY 1
 ORDER BY 2 DESC;

-- Os 20 casos concretos de divergencia, para voce julgar quem esta certo.
SELECT d.codigo, d.nome, d.setor AS setor_planilha, i.setor AS setor_cadastro,
       d.grupo_economico AS grupo_planilha, i.grupo_economico AS grupo_cadastro
  FROM debentures d
  JOIN issuers i ON i.id = d.issuer_id
 WHERE d.setor IS NOT NULL AND i.setor IS NOT NULL AND d.setor <> i.setor
 ORDER BY d.codigo
 LIMIT 20;

-- -------------------------------------------------------------------------
-- 4. QUAIS TABELAS AINDA GANHAM LINHAS
--
-- "Esta tabela e necessaria?" se responde com dois numeros: tamanho e
-- ultima escrita. Tabela grande e parada ha meses e candidata a arquivo;
-- tabela vazia e candidata a exclusao.
-- -------------------------------------------------------------------------
SELECT relname                              AS tabela,
       n_live_tup                           AS linhas_aprox,
       pg_size_pretty(pg_total_relation_size(relid)) AS tamanho,
       n_tup_ins                            AS insercoes_desde_o_reset,
       GREATEST(COALESCE(last_autoanalyze, '-infinity'),
                COALESCE(last_analyze,     '-infinity')) AS ultima_analise
  FROM pg_stat_user_tables
 WHERE schemaname = 'public'
 ORDER BY n_live_tup DESC;
