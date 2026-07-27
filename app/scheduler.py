"""Agendador do scanner automático (a cada N minutos, configurável)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from . import config
from .pipeline import run_pipeline

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# Negócio a negócio da B3 (aba Emissores -- pedido do Allan, 24/07/2026):
# a própria B3 atualiza essa tabela a cada 15 min durante o pregão, então
# não faz sentido varrer com mais frequência que isso.
B3_TRADES_INTERVAL_MINUTES = 15


def _job():
    try:
        summary = run_pipeline(triggered_by="scheduler")
        logger.info(
            "Scan automático concluído: %s novos artigos (%s fontes, %s erros)",
            summary["n_new"], len(summary["sources"]), len(summary["errors"]),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Falha no scan automático")


def _job_b3_trades():
    try:
        from datetime import date

        from .db import SessionLocal
        from .spreads.b3_trades import fetch_trades
        from .spreads.persist import save_negocios_b3

        today = date.today()
        trades = fetch_trades(today, today)
        with SessionLocal() as db:
            n_novos = save_negocios_b3(db, trades)
        logger.info(
            "Negócio a negócio B3: %d capturados hoje, %d novo(s)",
            len(trades), n_novos,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao capturar negócio a negócio da B3")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    import datetime as dt
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _job,
        "interval",
        minutes=config.SCAN_INTERVAL_MINUTES,
        id="news_scan",
        next_run_time=dt.datetime.now(dt.timezone.utc),  # primeira varredura já dispara no boot
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _job_b3_trades,
        "interval",
        minutes=B3_TRADES_INTERVAL_MINUTES,
        id="b3_trades_scan",
        next_run_time=dt.datetime.now(dt.timezone.utc),
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    return _scheduler


def trigger_now() -> None:
    """Agenda uma execução imediata (usada pelo botão 'Forçar atualização')."""
    if _scheduler is None:
        return
    import datetime as dt
    _scheduler.modify_job("news_scan", next_run_time=dt.datetime.now(dt.timezone.utc))
