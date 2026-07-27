"""Captura o histórico de spreads de debêntures (Anbima + debentures.com.br)
e grava no banco do credit_monitor — módulo "Spreads" do Hub Credit
Research (pedido do Allan, 23/07/2026; ver CLAUDE.md pro desenho completo).

Uso:
    python -m scripts.fetch_debenture_spreads
        Sem argumentos: acha sozinho o dia útil mais recente já publicado
        na Anbima e captura só esse dia (uso do dia a dia / cron).

    python -m scripts.fetch_debenture_spreads --start 2024-07-23
        Backfill de uma data até hoje (ou até o último dia publicado).

    python -m scripts.fetch_debenture_spreads --start 2024-07-23 --end 2024-12-31
        Backfill de um intervalo específico.

IMPORTANTE — rodar localmente primeiro: as URLs da Anbima/debentures.com.br
não são alcançáveis do sandbox onde o Claude escreveu este código (ver
docstring de app/spreads/fetch.py) — ou seja, este script nunca rodou de
ponta a ponta antes de chegar até você. Rode primeiro pra um único dia
(sem argumentos) e confira o log antes de mandar um backfill de 2 anos.

Backfill de 2 anos é MUITO mais lento que a varredura de notícias: cada dia
útil processado faz de 3 a 4 requisições (estoque, NTN-B, boletim de
spread) mais o parsing de planilhas Excel de milhares de linhas -- espere
bem mais que alguns minutos para ~500 dias úteis. O script é seguro de
interromper (Ctrl+C) e rodar de novo -- cada dia já gravado fica intacto
(upsert por Código+Data), então rodar de novo só refaz os dias que faltam
se você apontar o mesmo --start.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine, run_migrations
from app.spreads.fetch import (
    build_session,
    business_days,
    detect_latest_published_date,
    fetch_caracs,
    fetch_spreads,
)
from app.spreads.persist import cache_ntnb_referencia, persist_caracteristicas, persist_day

logger = logging.getLogger(__name__)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    p = argparse.ArgumentParser(description="Captura spreads de debêntures (Anbima/debentures.com.br)")
    p.add_argument("--start", type=_parse_date, default=None, help="Data inicial (AAAA-MM-DD). Padrão: detecta o último dia útil publicado.")
    p.add_argument("--end", type=_parse_date, default=None, help="Data final (AAAA-MM-DD). Padrão: igual a --start.")
    args = p.parse_args()

    Base.metadata.create_all(engine)
    run_migrations()

    session = build_session()

    if args.start is None:
        logger.info("Sem --start: detectando o último dia útil já publicado na Anbima...")
        start = detect_latest_published_date(session)
        end = start
    else:
        start = args.start
        end = args.end or args.start
        if end < start:
            p.error("--end não pode ser antes de --start")

    total_rows = 0
    total_days_com_dado = 0
    dias = list(business_days(start, end))
    logger.info("Capturando spreads de %s a %s (%d dias úteis)", start, end, len(dias))

    with SessionLocal() as db:
        for i, dt in enumerate(dias, start=1):
            try:
                rows, ntnb_rates, min_ntnb, min_venc = fetch_spreads(session, dt)
            except Exception:
                logger.exception("Falha ao capturar %s — seguindo pro próximo dia", dt)
                continue
            # Cacheia a curva de NTN-B do dia (mesmo quando `rows` vem
            # vazio -- ex. só a tabela de NTN-B publicou, o boletim de
            # debêntures ainda não) pra `b3_trades.compute_trade_spreads`
            # não precisar bater na Anbima de novo ao longo do dia (pedido
            # do Allan, 27/07/2026 -- ver app/models.py::NtnbReferencia).
            cache_ntnb_referencia(db, dt, ntnb_rates, min_ntnb, min_venc)
            if not rows:
                logger.info("[%d/%d] %s: sem boletim (feriado/fim de semana/não publicado ainda)", i, len(dias), dt)
                continue
            summary = persist_day(db, dt, rows)
            total_rows += summary["n_rows"]
            total_days_com_dado += 1
            logger.info(
                "[%d/%d] %s: %d debêntures (%d novas na base)",
                i, len(dias), dt, summary["n_rows"], summary["n_new_debentures"],
            )

        try:
            caracs = fetch_caracs(session)
            n = persist_caracteristicas(db, caracs)
            logger.info("Características atualizadas: %d debênture(s)", n)
        except Exception:
            logger.exception("Falha ao buscar características (CNPJ/incentivada) — não impede o resto")

    logger.info(
        "Concluído: %d dia(s) com dado, %d linha(s) de spread no total.",
        total_days_com_dado, total_rows,
    )


if __name__ == "__main__":
    main()
