"""Rodada diária das fontes de assembleia de debenturistas.

POR QUE EXISTE (11/09/2026). Vórtx, Oliveira Trust e Pentágono não têm uma
listagem geral: cada uma busca no site DE CADA NOME da cobertura -- empresas
mais apelidos, ~250 buscas HTTP em sequência -- e só então abre o navegador
para visitar operação por operação. Medido no GitHub Actions: a Vórtx
sozinha consumiu ~16 minutos e o job bateu no `timeout-minutes: 20`,
matando a varredura de notícias inteira antes da última fonte.

Assembleia é convocada com prazo legal de antecedência. Saber no mesmo dia
basta; saber em 15 minutos não muda decisão nenhuma. Então elas saíram da
varredura de 15 em 15 minutos (`scripts/run_once.py`, que agora as exclui
via `config.FONTES_LENTAS`) e ganharam esta rotina, 1x/dia, com teto de
tempo por fonte folgado.

Uso: `python -m scripts.rodada_assembleias`
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app.db import SessionLocal
from app.pipeline import run_pipeline
from app.seed_sources import sync_known_sources

# 15 minutos por fonte. O teto da varredura de notícias (180s) existe para
# uma fonte lenta não atrapalhar as outras 23; aqui são só três, o job é
# diário e o trabalho é legitimamente longo -- o teto serve para uma fonte
# travada não impedir as outras duas de rodar, não para apressá-las.
TETO_POR_FONTE_S = "900"


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger(__name__)

    # `TEMPO_MAXIMO_POR_FONTE` é lido do ambiente no import de app.pipeline,
    # então precisa estar posto antes -- e `pipeline` já foi importado acima.
    # Por isso o valor é aplicado no módulo, não via os.environ.
    from app import pipeline

    pipeline.TEMPO_MAXIMO_POR_FONTE = int(
        os.getenv("SCRAPE_TIMEOUT_FONTE", TETO_POR_FONTE_S)
    )

    with SessionLocal() as db:
        n_new, n_synced = sync_known_sources(db)
        if n_new or n_synced:
            log.info("Fontes sincronizadas: %d nova(s), %d atualizada(s)", n_new, n_synced)

    log.info(
        "Rodando só as fontes de assembleia: %s (teto de %ds por fonte)",
        ", ".join(sorted(config.FONTES_LENTAS)), pipeline.TEMPO_MAXIMO_POR_FONTE,
    )
    summary = run_pipeline(
        triggered_by="assembleias", apenas_modulos=config.FONTES_LENTAS
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if not summary["sources"]:
        # Nenhuma fonte rodou: ou todas estão desabilitadas na aba Fontes &
        # Empresas, ou o nome do módulo em FONTES_LENTAS não bate com o que
        # está no banco. Silêncio aqui viraria "nunca mais tivemos
        # assembleia" sem ninguém perceber.
        log.warning(
            "Nenhuma fonte de assembleia rodou -- confira se %s estão "
            "habilitadas em Fontes & Empresas e se o scraper_module bate.",
            sorted(config.FONTES_LENTAS),
        )

    if summary.get("errors"):
        log.warning(
            "Terminou com %d erro(s) -- ver acima. Não falha o job: uma "
            "fonte fora do ar não deve impedir as outras de serem salvas.",
            len(summary["errors"]),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
