"""Ponto de entrada usado pelo GitHub Actions (.github/workflows/scrape.yml)
pra rodar UMA varredura e sair -- diferente do agendador em processo
(app/scheduler.py), que só existe rodando localmente ou numa hospedagem que
mantém o processo vivo o tempo todo.

Uso: `python -m scripts.run_once` (precisa de DATABASE_URL apontando pro
Supabase já configurado como variável de ambiente/segredo do workflow).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine, run_migrations
from app.pipeline import run_pipeline
from app.seed_sources import sync_known_sources


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Idempotente e barato -- garante que o schema existe mesmo se por
    # algum motivo esta for a primeira coisa a tocar o banco (normalmente
    # quem cria o schema é o `python -m scripts.seed` rodado uma vez do
    # computador local apontando pro Supabase, ver CLAUDE.md).
    Base.metadata.create_all(engine)
    run_migrations()

    # Sincroniza fontes novas do config.py automaticamente (17/07/2026) --
    # antes só o seed manual local fazia isso, então cadastrar uma fonte
    # nova exigia rodar o seed contra o Supabase toda vez. Agora a própria
    # rodada agendada do GitHub Actions já traz fontes novas sozinha.
    with SessionLocal() as db:
        n_new, n_synced = sync_known_sources(db)
        if n_new or n_synced:
            logging.getLogger(__name__).info(
                "Fontes sincronizadas: %d nova(s), %d atualizada(s)", n_new, n_synced
            )

    summary = run_pipeline(triggered_by="scheduler")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if summary.get("errors"):
        logging.getLogger(__name__).warning(
            "Varredura terminou com %d erro(s) -- ver detalhes acima. "
            "Não falha o job do GitHub Actions por causa disso (uma fonte "
            "fora do ar não deve impedir as outras de serem salvas).",
            len(summary["errors"]),
        )


if __name__ == "__main__":
    main()
