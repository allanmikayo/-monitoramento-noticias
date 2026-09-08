"""Faxina diária do banco: apaga o que passou da janela de retenção.

POR QUE EXISTE (08/09/2026)
---------------------------
A limpeza de artigos antigos rodava dentro de `app/pipeline.run_pipeline`,
ou seja, ao fim de TODA varredura de notícias -- 96 vezes por dia. Cada
execução varria `articles` inteira para, quase sempre, não apagar nada: a
retenção é de 45 dias, então só há o que apagar algumas vezes por dia.

Somado ao resto, isso ajudou a cravar o Disk IO do Supabase em 100% com a
CPU em 18% -- saturação de OPERAÇÕES, não de espaço (o banco tem 111 MB).

Aqui a mesma limpeza roda UMA vez por dia, e apaga por subconsulta, sem
trazer id nenhum para o Python.

O QUE APAGA
-----------
1. `articles` além de `config.CLEANUP_MAX_AGE_HOURS` (45 dias), junto com
   os vínculos em `article_company` / `article_sector`.
2. `run_logs` além de 30 dias. Cada varredura grava uma linha com o
   diagnóstico completo em JSON (`sources_json`); em 55 dias já eram 4.506
   linhas / 1,5 MB, e o dashboard só mostra a ÚLTIMA. 30 dias é folga
   grande para qualquer investigação.
3. `sessions` já expiradas ou revogadas. Sessão de login não tem uso
   depois de expirar, e a tabela leva UPDATE a cada visita ao site.

O que NÃO apaga: nada de dado de mercado. Spreads, securitizados e o
agregado da B3 são para sempre; a poda do negócio a negócio bruto continua
sendo do `b3_fechamento.yml`, que é quem sabe conferir se o dia já tem
agregado antes de apagar.

    python -m scripts.faxina_diaria
    python -m scripts.faxina_diaria --simular   # só conta, não apaga
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402

from app import config, store  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Article, RunLog, Session as SessionRow  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("faxina")

RETENCAO_RUN_LOGS_DIAS = 30


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def contar(db) -> dict:
    """O que SERIA apagado agora -- usado pelo --simular e pelo log."""
    corte_artigos = _agora() - timedelta(hours=config.CLEANUP_MAX_AGE_HOURS)
    corte_logs = _agora() - timedelta(days=RETENCAO_RUN_LOGS_DIAS)
    return {
        "artigos": db.scalar(
            select(func.count()).select_from(Article).where(store.DATA_ARTIGO < corte_artigos)
        ) or 0,
        "run_logs": db.scalar(
            select(func.count()).select_from(RunLog).where(RunLog.started_at < corte_logs)
        ) or 0,
        "sessoes": db.scalar(
            select(func.count()).select_from(SessionRow).where(
                (SessionRow.expires_at < _agora()) | (SessionRow.revoked.is_(True))
            )
        ) or 0,
    }


def faxinar(db, simular: bool = False) -> dict:
    previsto = contar(db)
    logger.info(
        "a apagar: %d artigo(s), %d run_log(s), %d sessão(ões) expirada(s)",
        previsto["artigos"], previsto["run_logs"], previsto["sessoes"],
    )
    if simular:
        return {**previsto, "simulado": True}

    n_artigos = store.cleanup_old_articles(db, config.CLEANUP_MAX_AGE_HOURS)
    db.commit()

    corte_logs = _agora() - timedelta(days=RETENCAO_RUN_LOGS_DIAS)
    n_logs = db.execute(delete(RunLog).where(RunLog.started_at < corte_logs)).rowcount or 0
    n_sessoes = db.execute(
        delete(SessionRow).where(
            (SessionRow.expires_at < _agora()) | (SessionRow.revoked.is_(True))
        )
    ).rowcount or 0
    db.commit()

    logger.info("apagados: %d artigo(s), %d run_log(s), %d sessão(ões)",
                n_artigos, n_logs, n_sessoes)
    return {"artigos": n_artigos, "run_logs": n_logs, "sessoes": n_sessoes, "simulado": False}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--simular", action="store_true",
                    help="só conta o que seria apagado, sem apagar nada")
    args = ap.parse_args()

    # SEM DDL aqui de propósito -- ver app/db.py ensure_schema.
    with SessionLocal() as db:
        try:
            faxinar(db, simular=args.simular)
        except Exception as exc:  # noqa: BLE001
            logger.exception("faxina FALHOU: %s", exc)
            return 1
    logger.info("faxina concluída")
    return 0


if __name__ == "__main__":
    sys.exit(main())
