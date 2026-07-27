"""Captura o negócio a negócio da B3 (debêntures, CRI, CRA) e grava no banco
do credit_monitor -- aba "Emissores" do módulo Spreads (pedido do Allan,
24/07/2026; ver CLAUDE.md e app/spreads/b3_trades.py pro desenho completo).

Uso:
    python -m scripts.fetch_b3_trades
        Sem argumentos: captura só o dia de hoje (uso do dia a dia --
        é o que o agendador em app/scheduler.py chama a cada 15 min).

    python -m scripts.fetch_b3_trades --start 2026-07-01
        Backfill de uma data até hoje.

    python -m scripts.fetch_b3_trades --start 2026-07-01 --end 2026-07-15
        Backfill de um intervalo específico.

Ao contrário do backfill de spreads, aqui NÃO tem por padrão um histórico de
2 anos -- Allan pediu "últimas negociações" (uso corrente, não análise
histórica longa), e o volume é grande (~700 negócios/dia só de DEB/CRI/CRA).
Se quiser histórico de um período específico, rode com --start explícito.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine, run_migrations
from app.spreads.b3_trades import fetch_trades
from app.spreads.persist import save_negocios_b3

logger = logging.getLogger(__name__)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def run(start: date, end: date) -> dict:
    Base.metadata.create_all(engine)
    run_migrations()

    trades = fetch_trades(start, end)
    with SessionLocal() as db:
        n_novos = save_negocios_b3(db, trades)
    logger.info(
        "Negócio a negócio %s a %s: %d capturados, %d novo(s) gravado(s)",
        start, end, len(trades), n_novos,
    )
    return {"start": start.isoformat(), "end": end.isoformat(), "n_capturados": len(trades), "n_novos": n_novos}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    p = argparse.ArgumentParser(description="Captura negócio a negócio da B3 (DEB/CRI/CRA)")
    p.add_argument("--start", type=_parse_date, default=None, help="Data inicial (AAAA-MM-DD). Padrão: hoje.")
    p.add_argument("--end", type=_parse_date, default=None, help="Data final (AAAA-MM-DD). Padrão: igual a --start.")
    args = p.parse_args()

    start = args.start or date.today()
    end = args.end or start
    if end < start:
        p.error("--end não pode ser antes de --start")

    run(start, end)


if __name__ == "__main__":
    main()
