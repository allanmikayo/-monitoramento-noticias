"""Liga as ofertas da CVM às debêntures que emitiram e ao setor de quem
carrega o risco -- ver app/primario/casamento.py.

    python -m scripts.casar_ofertas                           # só recalcula
    python -m scripts.casar_ofertas --baixar-caracteristicas  # atualiza o arquivo de características antes

Roda no fim da coleta da CVM (ofertas_cvm.yml) e da rodada diária de
spreads (que já baixa as características). Idempotente: refaz tudo.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.db import SessionLocal, engine, garantir_colunas
from app.models import DebentureCaracteristica, OfertaCVMAtivo, OfertaCVMClassificacao
from app.primario import casamento

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _marca(texto: str) -> None:
    print(f"::notice title=casar_ofertas etapa::{texto}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baixar-caracteristicas", action="store_true")
    a = ap.parse_args()

    _marca("início")
    DebentureCaracteristica.__table__.create(bind=engine, checkfirst=True)
    _marca("create checkfirst ok")
    criadas = garantir_colunas(DebentureCaracteristica.__table__)
    if criadas:
        logging.info("colunas criadas em debenture_caracteristicas: %s", criadas)
    _marca(f"garantir_colunas ok: {criadas}")
    OfertaCVMAtivo.__table__.create(bind=engine, checkfirst=True)
    OfertaCVMClassificacao.__table__.create(bind=engine, checkfirst=True)
    _marca("tabelas de ligação ok")

    with SessionLocal() as db:
        if a.baixar_caracteristicas:
            from app.spreads.fetch import build_session, fetch_caracs
            from app.spreads.persist import persist_caracteristicas_completas
            n = persist_caracteristicas_completas(db, fetch_caracs(build_session()))
            logging.info("características gravadas: %d", n)
        stats = casamento.recalcular(db)
    # Anotação do Actions: legível pela API mesmo quando o log não é.
    print("::notice title=casar_ofertas::" + json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
