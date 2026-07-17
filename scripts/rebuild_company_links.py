"""Recalcula do zero as empresas vinculadas a TODOS os artigos já
guardados no banco, usando a lógica atual de casamento de keywords.

Por que isso existe (17/07/2026): até agora, `store._set_companies` só
ADICIONAVA vínculos de empresa a um artigo, nunca removia -- então um
vínculo errado gravado uma vez (por um bug de scraper antigo, ou por uma
palavra comum colidindo com nome de empresa, ex.: "movida" verbo vs.
"Movida" empresa) ficava PRA SEMPRE, mesmo depois do bug corrigido. Esse
fix já foi feito em `store.py` (agora sincroniza certinho a cada
re-varredura), mas artigos de fontes RSS que já saíram do feed nunca mais
são revisitados pelo pipeline normal -- então o vínculo errado antigo
continuava preso no banco. Este script varre TODO o histórico uma vez e
corrige.

Uso:
    python -m scripts.rebuild_company_links          # aplica de verdade
    python -m scripts.rebuild_company_links --dry-run  # só mostra o que mudaria
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.filter import match_keywords
from app.models import Article
from app.store import _set_companies
from app.taxonomy import build_index, resolve_company_ids


def run(dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        taxonomy = build_index(db)
        articles = db.query(Article).all()
        print(f"Recalculando vínculos de {len(articles)} artigos...")

        n_mudou_empresas = 0
        n_mudou_cobertura = 0
        exemplos: list[str] = []

        for art in articles:
            haystack = f"{art.title}\n{art.snippet}"
            matched = match_keywords(haystack, taxonomy.all_keywords, taxonomy.sector_only_keywords)
            is_covered_novo = bool(matched) or art.article_type == "rating_action"
            company_ids_novo = resolve_company_ids(matched, taxonomy)

            ids_antes = {c.id for c in art.companies}
            nomes_antes = sorted(c.name for c in art.companies)

            if ids_antes != company_ids_novo:
                n_mudou_empresas += 1
                nomes_novo = sorted(taxonomy.company_names.get(i, f"#{i}") for i in company_ids_novo)
                if len(exemplos) < 25:
                    exemplos.append(
                        f"  [{art.id}] {art.title[:70]!r}\n"
                        f"      antes: {nomes_antes or '(nenhuma)'}\n"
                        f"      agora: {nomes_novo or '(nenhuma)'}"
                    )
                if not dry_run:
                    _set_companies(db, art, sorted(company_ids_novo))

            # is_covered: recalcula igual (nao so' upgrade) -- se um falso
            # positivo fazia um artigo parecer "coberto" antes, corrige aqui
            # tambem. Acoes de rating continuam sempre visiveis via OR no
            # filtro do dashboard independente deste campo.
            cobertura_bruta_nova = bool(matched)
            if art.is_covered != cobertura_bruta_nova:
                n_mudou_cobertura += 1
                if not dry_run:
                    art.is_covered = cobertura_bruta_nova

        if not dry_run:
            db.commit()

        print(f"\nEmpresas vinculadas mudaram em {n_mudou_empresas} artigo(s).")
        print(f"is_covered mudou em {n_mudou_cobertura} artigo(s).")
        if exemplos:
            print("\nExemplos (até 25):")
            print("\n".join(exemplos))
        if dry_run:
            print("\n(--dry-run: nada foi salvo. Rode sem essa flag pra aplicar.)")
        else:
            print("\nAplicado e salvo no banco.")
    finally:
        db.close()


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
