"""Preenche o spread (bps) dos negócios da B3 que já foram capturados
ANTES da correção que passou a calcular isso na hora da gravação (pedido
do Allan, 27/07/2026 -- ver app/spreads/b3_trades.py::compute_trade_spreads
e CLAUDE.md). Sem essa correção retroativa, todo negócio capturado nas
rodadas anteriores fica com `spread=NULL` pra sempre (o dedupe por
`trade_code` nunca atualiza linha já gravada, só insere linha nova) --
distorce pra menos os cards de "spread ponderado" da aba Emissores
(negócio sem spread simplesmente não entra na ponderação, mas isso ainda
é bem menos negócio contando do que devia).

Uso (roda uma vez só, sem argumento nenhum -- idempotente, pode rodar de
novo à toa que não faz mal, só recalcula o que ainda estiver None):
    python -m scripts.backfill_b3_trade_spreads

    python -m scripts.backfill_b3_trade_spreads --recompute-ipca
        BUG REAL (27/07/2026, mesmo dia da correção acima): a correção de
        `compute_trade_spreads` que passou a usar a NTN-B de referência
        ESPECÍFICA de cada papel (em vez de sempre o vértice mais curto --
        ver CLAUDE.md, seção "Bug real: spread B3 negativo/sem sentido")
        só vale pros negócios que ainda estão com `spread=NULL`. Todo
        negócio IPCA+ que já tinha rodado por este script ANTES dessa
        correção ficou com o valor ANTIGO (errado) gravado -- e como o
        script só mexe em linha `NULL`, rodar de novo sem essa flag NÃO
        conserta esses valores já preenchidos. Esta flag reseta
        `spread=NULL` só nos negócios de papel "IPCA + Incentivadas"
        (não mexe em CDI+, que nunca teve esse bug) antes de recalcular
        tudo -- rodar UMA VEZ só, logo depois de aplicar a correção do
        vértice mais curto.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine, run_migrations
from app.models import Debenture, NegocioB3
from app.spreads.b3_trades import compute_trade_spreads

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    p = argparse.ArgumentParser(description="Preenche/recalcula spread (bps) dos negócios da B3")
    p.add_argument(
        "--recompute-ipca", action="store_true",
        help="Reseta e recalcula TODO negócio IPCA+ (não só os com spread=NULL) -- "
             "rodar uma vez só depois da correção do vértice mais curto (27/07/2026).",
    )
    args = p.parse_args()

    Base.metadata.create_all(engine)
    run_migrations()

    with SessionLocal() as db:
        if args.recompute_ipca:
            codigos_ipca = {
                codigo for (codigo,) in db.query(Debenture.codigo)
                .filter(Debenture.classe == "IPCA + Incentivadas").all()
            }
            n_reset = (
                db.query(NegocioB3)
                .filter(NegocioB3.codigo.in_(codigos_ipca), NegocioB3.spread.isnot(None))
                .update({NegocioB3.spread: None}, synchronize_session=False)
            )
            db.commit()
            logger.info(
                "--recompute-ipca: %d negócio(s) IPCA+ resetados pra recálculo "
                "com a referência correta (vértice mais curto só como fallback agora).",
                n_reset,
            )

        rows = db.query(NegocioB3).filter(NegocioB3.spread.is_(None)).all()
        logger.info("%d negócio(s) sem spread calculado -- calculando...", len(rows))
        if not rows:
            logger.info("Nada pra fazer.")
            return

        trades = [{"codigo": r.codigo, "taxa": r.taxa, "data_negocio": r.data_negocio} for r in rows]
        compute_trade_spreads(db, trades)
        for row, t in zip(rows, trades):
            row.spread = t["spread"]
        db.commit()

        n_preenchidos = sum(1 for t in trades if t["spread"] is not None)
        logger.info(
            "Concluído: %d de %d negócio(s) ganharam spread calculado "
            "(o resto ficou sem spread -- indexador fora de IPCA+/CDI+, "
            "ticker sem cadastro, ou NTN-B indisponível pro dia do negócio).",
            n_preenchidos, len(rows),
        )


if __name__ == "__main__":
    main()
