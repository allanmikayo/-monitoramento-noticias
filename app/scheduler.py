"""Agendador do scanner automático (a cada N minutos, configurável)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from . import config
from .pipeline import run_pipeline

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _job():
    try:
        summary = run_pipeline(triggered_by="scheduler")
        logger.info(
            "Scan automático concluído: %s novos artigos (%s fontes, %s erros)",
            summary["n_new"], len(summary["sources"]), len(summary["errors"]),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Falha no scan automático")


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
    _scheduler.start()
    return _scheduler


def trigger_now() -> None:
    """Agenda uma execução imediata (usada pelo botão 'Forçar atualização')."""
    if _scheduler is None:
        return
    import datetime as dt
    _scheduler.modify_job("news_scan", next_run_time=dt.datetime.now(dt.timezone.utc))
