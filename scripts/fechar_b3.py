"""Fechamento diário do Balcão B3: agrega o negócio a negócio e poda o bruto.

ESTA É A PEÇA QUE NUNCA TEVE WORKFLOW. A captura do negócio a negócio roda
de 15 em 15 min (`b3_trades.yml`) e grava ~17 mil linhas por pregão em
`negocios_b3`. O fechamento -- consolidar em `negocios_b3_diario` e apagar o
bruto além de 5 dias -- só existia dentro de `scripts/rodada_noturna.py`,
que por sua vez não tinha workflow. Resultado: a tabela bruta cresceu sem
limite desde julho e o Supabase começou a alertar estouro de Disk IO Budget
em 13/08 e 20/08/2026.

Separado num script próprio em 20/08/2026, a pedido do Allan: *"separe em
actions individuais, assim se um der erro não quebra o fluxo inteiro"*.
`rodada_noturna.etapa_b3` chama esta mesma função, então não há duas
implementações para divergir.

A ORDEM IMPORTA e está garantida aqui: agregar primeiro, podar depois. A
poda ainda confere, dia a dia, se o consolidado existe antes de apagar
qualquer coisa -- apagar bruto sem agregado perderia o dado de vez.

    python -m scripts.fechar_b3
    python -m scripts.fechar_b3 --sem-poda     # só agrega, não apaga nada
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine, run_migrations  # noqa: E402
# Sem este import o `Base.metadata` fica VAZIO e o `create_all` abaixo não
# cria nada -- num banco novo o script morre com "no such table:
# negocios_b3_diario". Mesma armadilha que `scripts/init_db.py` já
# documenta; peguei rodando o script contra um SQLite limpo.
from app import models  # noqa: E402,F401

logger = logging.getLogger("fechar_b3")


def fechar(db, podar: bool = True) -> dict:
    """Agrega o dia e (opcionalmente) poda o bruto. Devolve o resumo."""
    from app.spreads import b3_agregado as agg

    r = agg.agregar_do_banco(db)
    logger.info("consolidado: %d linhas em %d dias (guardado para sempre)",
                r["linhas_agregado"], r["dias"])

    if not podar:
        logger.info("poda pulada (--sem-poda)")
        return {**r, "poda": {"apagados": 0, "motivo": "pulada por --sem-poda"}}

    p = agg.podar_bruto(db)
    logger.info("negócio a negócio: %d apagados antes de %s (%s)",
                p["apagados"], p["corte"], p["motivo"])
    for d in p.get("dias_sem_agregado", [])[:5]:
        logger.warning("  dia sem consolidado, NÃO apagado: %s", d)
    return {**r, "poda": p}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sem-poda", action="store_true",
                    help="agrega mas não apaga o bruto antigo")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    Base.metadata.create_all(engine)
    run_migrations()

    db = SessionLocal()
    try:
        fechar(db, podar=not args.sem_poda)
    except Exception as exc:  # noqa: BLE001
        logger.exception("fechamento da B3 FALHOU: %s", exc)
        return 1
    finally:
        db.close()

    logger.info("fechamento concluído")
    return 0


if __name__ == "__main__":
    sys.exit(main())
