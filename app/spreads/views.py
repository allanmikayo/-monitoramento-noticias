"""View `v_spread_rating` — a junção spread × emissor × rating por data.

É a "view final" do Allan reproduzida no banco: spread da Anbima ×
cadastro (setor/grupo) × rating vigente **na data daquela linha**
(junção as-of, `data >=`).

POR QUE UMA VIEW, E NÃO CADA CONSULTA MONTANDO O JOIN
-----------------------------------------------------
A junção tem duas armadilhas que já morderam durante o desenvolvimento, e
ambas passam despercebidas porque o resultado *parece* certo:

1. **Duplicação de linha.** Existem períodos de rating em dois níveis --
   do emissor (`codigo IS NULL`) e da emissão específica (`codigo`
   preenchido, caso COSAN). O join ingênuo casa com os DOIS ao mesmo
   tempo:

       -- ERRADO
       JOIN issuer_rating_periodo p
         ON p.issuer_id = d.issuer_id
        AND (p.codigo = d.codigo OR p.codigo IS NULL)

   Medido em 04/08/2026: 854.268 linhas para uma base de 569.272 --
   **150% de "cobertura"**. Qualquer `SUM(estoque)` sai inflado, e não há
   erro nenhum na tela.

2. **Rating nulo.** 21% das linhas de spread não têm período de rating
   (papel novo, ou anterior ao início do histórico). Sem `COALESCE`, elas
   viram `NULL` -- que some do `GROUP BY`, não casa com `= 'N.A.'` e vira
   buraco silencioso no gráfico. Regra do Allan: rating médio é sempre um
   rating ou `"N.A."`, nunca em branco.

A view resolve as duas: precedência explícita via `LEFT JOIN` separado por
nível + `COALESCE` para `'N.A.'`.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .ratings import SEM_RATING

# Trecho de junção as-of reutilizável. `{alias}` casa com um nível de
# escopo; `{escopo}` é a condição que distingue emissor de emissão.
_ASOF = """
    LEFT JOIN issuer_rating_periodo {alias}
           ON {escopo}
          AND {alias}.data_inicio <= s.data
          AND ({alias}.data_fim IS NULL OR s.data < {alias}.data_fim)
"""

V_SPREAD_RATING = f"""
CREATE VIEW v_spread_rating AS
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
         ELSE '{SEM_RATING}' END AS rating_medio,
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
{_ASOF.format(alias="pth", escopo="pth.codigo = d.codigo AND pth.origem = 'HISTORICO'")}
{_ASOF.format(alias="ptd", escopo="ptd.codigo = d.codigo AND ptd.origem = 'DERIVADO'")}
{_ASOF.format(alias="pih", escopo="pih.issuer_id = d.issuer_id AND pih.codigo IS NULL AND pih.origem = 'HISTORICO'")}
{_ASOF.format(alias="pid", escopo="pid.issuer_id = d.issuer_id AND pid.codigo IS NULL AND pid.origem = 'DERIVADO'")}
"""

VIEWS = {"v_spread_rating": V_SPREAD_RATING}


def criar_views(engine: Engine) -> list[str]:
    """(Re)cria as views. Idempotente — dropa e recria.

    Chamada depois de `Base.metadata.create_all` (a view depende das
    tabelas existirem) e a cada deploy: `CREATE OR REPLACE VIEW` não
    existe no SQLite, e mesmo no Postgres ele falha quando as colunas
    mudam de tipo ou ordem. `DROP` + `CREATE` funciona igual nos dois.
    """
    criadas = []
    with engine.connect() as conn:
        for nome, ddl in VIEWS.items():
            conn.exec_driver_sql(f"DROP VIEW IF EXISTS {nome}")
            conn.exec_driver_sql(ddl)
            conn.commit()
            criadas.append(nome)
    return criadas


def conferir_view(engine: Engine) -> dict:
    """Sanidade da view: contagem igual à base e nenhum rating nulo.

    As duas invariantes que a view existe pra garantir. Vale rodar depois
    de qualquer carga -- as duas falham em silêncio se alguém mexer no
    join.
    """
    with engine.connect() as conn:
        base = conn.exec_driver_sql("SELECT COUNT(*) FROM debenture_spreads").scalar()
        view = conn.exec_driver_sql("SELECT COUNT(*) FROM v_spread_rating").scalar()
        nulos = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM v_spread_rating WHERE rating_medio IS NULL"
        ).scalar()
        sem_rating = conn.exec_driver_sql(
            f"SELECT COUNT(*) FROM v_spread_rating WHERE rating_medio = '{SEM_RATING}'"
        ).scalar()
        # Rating e notch têm que sair do MESMO período -- ver o bug do
        # COALESCE no SELECT da view. "N.A." é o único rating sem notch;
        # qualquer outra combinação é incoerência.
        incoerentes = conn.exec_driver_sql(f"""
            SELECT COUNT(*) FROM v_spread_rating
             WHERE (rating_medio = '{SEM_RATING}' AND notch_medio IS NOT NULL)
                OR (rating_medio <> '{SEM_RATING}' AND notch_medio IS NULL)
        """).scalar()
    return {
        "linhas_base": base,
        "linhas_view": view,
        "duplicou": view != base,
        "rating_nulo": nulos,
        "sem_rating": sem_rating,
        "incoerentes": incoerentes,
        "ok": view == base and nulos == 0 and incoerentes == 0,
    }
