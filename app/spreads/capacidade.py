"""Ocupação do banco e projeção de quando o limite aperta.

MOTIVO (12/08/2026): a retenção do projeto passou a ser INFINITA — nada é
apagado automaticamente. Isso é o que o Allan quer, mas transforma o
espaço em algo que precisa ser acompanhado, não descoberto quando o banco
para de aceitar escrita.

Este módulo mede o tamanho real por tabela e projeta o crescimento a
partir da cadência observada, para o painel "Fontes de dados" da capa
poder avisar com meses de antecedência.

O QUE A MEDIÇÃO MOSTROU (12/08/2026)
------------------------------------
    negócio a negócio, guardado para sempre   ~740 MB/ano  (91% do total)
    todo o resto somado                        ~76 MB/ano

O desenho adotado resolve isso sem perder análise (ver
`b3_agregado.RETENCAO_BRUTO_DIAS`): o **consolidado diário** fica para
sempre e o **negócio a negócio** só nos últimos 5 dias — ou seja, ~15 MB
FIXOS, que não crescem. O banco inteiro passa a crescer ~76 MB/ano e o
plano gratuito do Supabase dura ~5 anos.

`projetar(..., com_bruto=True)` continua existindo para mostrar o custo do
cenário alternativo (guardar o bruto inteiro), que é o que justifica a
decisão -- e é o número que aparece no painel quando alguém pergunta
"por que só 5 dias?".
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

# Limites dos planos do Supabase (GB de banco). Fonte: supabase.com/pricing.
# Guardado aqui e não hardcoded na tela pra ficar fácil de corrigir quando
# a Supabase mudar o plano.
logger = logging.getLogger(__name__)

LIMITES_MB = {"free": 500, "pro": 8_192}

# Crescimento observado, em bytes por linha e linhas por dia útil. Medido
# no banco real em 12/08/2026 -- ver docstring do módulo.
CRESCIMENTO = {
    "debenture_spreads":     {"linhas_dia": 1200,  "bytes_linha": 80},
    "securitizado_spreads":  {"linhas_dia": 490,   "bytes_linha": 100},
    # Não entra na projeção de crescimento: com retenção de 5 dias, o
    # bruto tem tamanho de REGIME (~15 MB), não taxa de crescimento.
    "negocios_b3":           {"linhas_dia": 12600, "bytes_linha": 233},
    "negocios_b3_diario":    {"linhas_dia": 1340,  "bytes_linha": 102},
    "articles":              {"linhas_dia": 25,    "bytes_linha": 784},
    "issuer_ratings":        {"linhas_dia": 4,     "bytes_linha": 123},
    "issuer_rating_periodo": {"linhas_dia": 4,     "bytes_linha": 62},
}
DIAS_UTEIS_ANO = 252


def tamanho_por_tabela(engine: Engine) -> list[dict]:
    """Tamanho ocupado por tabela, em MB.

    Usa `dbstat` no SQLite (módulo padrão, mas nem toda build o traz) e
    `pg_total_relation_size` no Postgres. Cai para contagem de linhas ×
    bytes/linha estimado se nenhum dos dois funcionar -- um número
    aproximado é melhor que nenhum painel.
    """
    saida: list[dict] = []
    with engine.connect() as conn:
        dialeto = engine.dialect.name
        if dialeto == "postgresql":
            # `c.relname` QUALIFICADO. `pg_class` e `pg_stat_user_tables`
            # têm ambos uma coluna `relname`, então o nome cru dá
            # "column reference relname is ambiguous" e derruba a página
            # inteira com 500.
            #
            # BUG REAL (12/08/2026): a aba Banco de Dados funcionava no
            # SQLite local e dava Internal Server Error no servidor do
            # Allan -- que roda contra o Supabase. O ramo Postgres nunca
            # tinha sido exercido.
            rs = conn.execute(text("""
                SELECT c.relname                      AS tabela,
                       pg_total_relation_size(c.oid)  AS bytes,
                       COALESCE(s.n_live_tup, 0)      AS linhas
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                  LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                 WHERE n.nspname = 'public' AND c.relkind = 'r'
                 ORDER BY pg_total_relation_size(c.oid) DESC
            """))
            saida = [{"tabela": r.tabela, "mb": (r.bytes or 0) / 1e6,
                      "linhas": r.linhas or 0} for r in rs]
        else:
            tabelas = [r[0] for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite%'"
            )]
            tem_dbstat = True
            try:
                conn.exec_driver_sql("SELECT 1 FROM dbstat LIMIT 1")
            except Exception:  # noqa: BLE001
                tem_dbstat = False
            for t in tabelas:
                try:
                    n = conn.exec_driver_sql(f'SELECT COUNT(*) FROM "{t}"').scalar() or 0
                except Exception:  # noqa: BLE001
                    continue
                if tem_dbstat:
                    mb = (conn.exec_driver_sql(
                        "SELECT COALESCE(SUM(pgsize),0) FROM dbstat WHERE name=:n",
                    ).scalar() if False else conn.execute(
                        text("SELECT COALESCE(SUM(pgsize),0) FROM dbstat WHERE name=:n"), {"n": t}
                    ).scalar() or 0) / 1e6
                else:
                    bl = CRESCIMENTO.get(t, {}).get("bytes_linha", 120)
                    mb = n * bl / 1e6
                saida.append({"tabela": t, "mb": mb, "linhas": n})
    return sorted(saida, key=lambda x: -x["mb"])


# Tamanho de regime do negócio a negócio com a retenção de 5 dias:
# 12.600 linhas/dia × 5 dias × 233 bytes.
BRUTO_REGIME_MB = 12600 * 5 * 233 / 1e6


def projetar(mb_atual: float, plano: str = "free", com_bruto: bool = False) -> dict:
    """Quando o banco atinge o limite do plano, no ritmo atual.

    `com_bruto=False` simula o cenário sem `negocios_b3` — é a informação
    que decide entre migrar de plano e arquivar, então a tela deve mostrar
    os dois.
    """
    mb_ano = sum(
        v["linhas_dia"] * DIAS_UTEIS_ANO * v["bytes_linha"] / 1e6
        for t, v in CRESCIMENTO.items()
        if com_bruto or t != "negocios_b3"
    )
    limite = LIMITES_MB.get(plano, LIMITES_MB["free"])
    folga = limite - mb_atual
    meses = (folga / mb_ano * 12) if mb_ano > 0 else None
    return {
        "plano": plano,
        "limite_mb": limite,
        "atual_mb": round(mb_atual, 1),
        "uso_pct": round(100 * mb_atual / limite, 1),
        "crescimento_mb_ano": round(mb_ano, 1),
        "meses_ate_limite": round(meses, 1) if meses and meses > 0 else 0,
        "com_bruto": com_bruto,
    }


def diagnostico(engine: Engine, plano: str = "free") -> dict:
    """Pacote pronto para o painel: tamanho, projeção e recomendação.

    NUNCA levanta. Medir espaço é informação auxiliar -- se a consulta de
    catálogo falhar (permissão, dialeto novo, `dbstat` ausente), a aba
    Banco de Dados tem que continuar servindo a extração, que é a função
    principal dela. A primeira versão deixava a exceção subir e o
    resultado era Internal Server Error na página inteira.
    """
    try:
        tabelas = tamanho_por_tabela(engine)
    except Exception as exc:  # noqa: BLE001
        logger.warning("não consegui medir o tamanho do banco: %s", exc)
        return {
            "tabelas": [], "total_mb": 0.0,
            "regime": projetar(0.0, plano), "se_guardasse_tudo": projetar(0.0, plano, True),
            "bruto_regime_mb": round(BRUTO_REGIME_MB, 1),
            "alerta": "indisponivel",
            "mensagem": f"Não foi possível medir a ocupação do banco ({type(exc).__name__}).",
            "medido_em": date.today().isoformat(),
        }
    atual = sum(t["mb"] for t in tabelas)
    # `regime` é o cenário REAL (bruto com retenção de 5 dias);
    # `tudo` é o contrafactual, para justificar a decisão no painel.
    regime = projetar(atual, plano, com_bruto=False)
    tudo = projetar(atual, plano, com_bruto=True)

    if regime["meses_ate_limite"] > 24:
        alerta = "ok"
        msg = (f"~{regime['meses_ate_limite']/12:.1f} anos de folga no plano {plano}. "
               f"Guardando o negócio a negócio inteiro seriam "
               f"~{tudo['meses_ate_limite']:.0f} meses.")
    elif regime["meses_ate_limite"] > 6:
        alerta = "atencao"
        msg = f"O limite do plano {plano} chega em ~{regime['meses_ate_limite']:.0f} meses."
    else:
        alerta = "critico"
        msg = (f"Menos de {regime['meses_ate_limite']:.0f} meses de folga — "
               f"hora de migrar de plano.")
    return {
        "tabelas": tabelas[:12],
        "total_mb": round(atual, 1),
        "regime": regime,
        "se_guardasse_tudo": tudo,
        "bruto_regime_mb": round(BRUTO_REGIME_MB, 1),
        "alerta": alerta,
        "mensagem": msg,
        "medido_em": date.today().isoformat(),
    }
