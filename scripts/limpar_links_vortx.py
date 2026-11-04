"""Apaga os documentos da Vórtx gravados com link ASSINADO (04/11/2026) e os
relatórios que a Vórtx GERA na hora (Relatório de Covenants/Garantias, com
data falsa de "hoje"; ver `vortx.GRUPOS_GERADOS`).

Até 04/11 a coleta gravava a URL assinada do CloudFront, que vence em ~1 h e
muda a cada chamada: o link morria e cada rodada duplicava o documento. A
coleta agora grava um endereço estável (`vortx.link_estavel`); este script
tira as cópias antigas, e a rodada seguinte grava cada documento uma vez,
com o link novo. Idempotente: sem nada a apagar, não faz nada.

    python -m scripts.limpar_links_vortx            # apaga
    python -m scripts.limpar_links_vortx --dry-run  # só conta
    python -m scripts.limpar_links_vortx --refazer-desde 2026-11-03
        # apaga TODOS os documentos dos três agentes fiduciários gravados
        # desde a data, para a rodada seguinte gravar de novo com a regra de
        # empresa atual (04/11/2026: "Bevap" saía como Vale).
"""
from __future__ import annotations

import argparse

from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.db import SessionLocal
from app.models import Article


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refazer-desde", default="")
    args = ap.parse_args()
    with SessionLocal() as db:
        if args.refazer_desde:
            desde = datetime.fromisoformat(args.refazer_desde).replace(tzinfo=timezone.utc)
            arts = list(db.scalars(select(Article).where(
                Article.article_type == "assembleia", Article.found_at >= desde,
                or_(*[Article.url.like(f"%{d}%") for d in ("vortx.com.br", "oliveiratrust.com.br", "pentagonotrustee.com.br")]))))
            print(f"refazer: {len(arts)} documento(s) dos agentes fiduciários desde {args.refazer_desde}")
            if not args.dry_run:
                for a in arts:
                    a.companies = []
                    a.sector_tags = []
                    db.delete(a)
                db.commit()
                print(f"apagados: {len(arts)}")
        arts = list(db.scalars(select(Article).where(
            Article.url.like("%vortx.com.br%"),
            or_(Article.url.like("%Signature=%"),
                Article.title.like("% — Relatório de Covenants — %"),
                Article.title.like("% — Relatório de Garantias — %")))))
        print(f"{len(arts)} documento(s) da Vórtx com link assinado ou relatório gerado")
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
