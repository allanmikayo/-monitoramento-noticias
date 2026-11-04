"""Apaga os documentos da Vórtx gravados com link ASSINADO (04/11/2026).

Até 04/11 a coleta gravava a URL assinada do CloudFront, que vence em ~1 h e
muda a cada chamada: o link morria e cada rodada duplicava o documento. A
coleta agora grava um endereço estável (`vortx.link_estavel`); este script
tira as cópias antigas, e a rodada seguinte grava cada documento uma vez,
com o link novo. Idempotente: sem nada a apagar, não faz nada.

    python -m scripts.limpar_links_vortx            # apaga
    python -m scripts.limpar_links_vortx --dry-run  # só conta
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Article


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with SessionLocal() as db:
        arts = list(db.scalars(select(Article).where(
            Article.url.like("%vortx.com.br%"), Article.url.like("%Signature=%"))))
        print(f"{len(arts)} documento(s) da Vórtx com link assinado")
        if args.dry_run or not arts:
            return 0
        for a in arts:
            a.companies = []
            a.sector_tags = []
            db.delete(a)
        db.commit()
        print(f"apagados: {len(arts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
