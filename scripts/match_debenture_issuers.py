"""Liga `Debenture.nome` (emissor, vindo da Anbima) a `Company` (cadastro
do monitoramento de notícias) -- aba "Marcação Emissores" do Hub Credit
Research (pedido do Allan, 24/07/2026). Ver app/spreads/company_match.py
pra heurística de matching (nome normalizado + contenção de token --
imprecisa de propósito conservador, revise antes de aplicar).

Uso:
    python -m scripts.match_debenture_issuers
        Só mostra o relatório (nada é gravado) -- confira antes de aplicar,
        principalmente os matches por "contenção" e a lista de "sem match".

    python -m scripts.match_debenture_issuers --apply
        Grava `company_id` em Debenture pros matches encontrados. Também
        adiciona o nome do emissor como CompanyAlias novo na empresa
        casada, se ainda não existir -- assim ele passa a contar pra casar
        notícias também (não só pra essa aba).

Rode de novo depois de ajustar nomes/aliases de empresa em /fontes (ex.:
adicionar um alias como "Aegea" numa empresa cadastrada como "Aegea
Saneamento") pra pegar os "sem match" que sobraram.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import CompanyAlias, Debenture
from app.spreads.company_match import match_all


def main() -> None:
    p = argparse.ArgumentParser(description="Liga emissores de debêntures a empresas da cobertura")
    p.add_argument("--apply", action="store_true", help="Grava os matches encontrados (padrão: só mostra o relatório)")
    args = p.parse_args()

    with SessionLocal() as db:
        results = match_all(db)

        exatos = [r for r in results if r.motivo == "exato"]
        contidos = [r for r in results if r.motivo == "contencao"]
        sem_match = [r for r in results if r.motivo == "sem_match"]

        print(f"Emissores distintos: {len(results)}")
        print(f"  match exato:                 {len(exatos)}")
        print(f"  match por contenção de nome:  {len(contidos)}")
        print(f"  sem match:                    {len(sem_match)}")

        if contidos:
            print("\n--- Matches por contenção (confira antes de confiar) ---")
            for r in contidos:
                print(f"  {r.emissor!r} -> {r.company_name!r} (company_id={r.company_id})")

        if sem_match:
            print("\n--- Sem match (não aparecem em 'Marcação Emissores' até isso ser resolvido) ---")
            for r in sem_match:
                print(f"  {r.emissor!r}")

        if not args.apply:
            print("\nNada foi gravado (rode com --apply pra persistir os matches acima).")
            return

        n_novos_matches = 0
        n_novos_aliases = 0
        for r in exatos + contidos:
            debs = db.query(Debenture).filter(Debenture.nome == r.emissor).all()
            for d in debs:
                if d.company_id != r.company_id:
                    d.company_id = r.company_id
                    n_novos_matches += 1
            existentes = {a.alias for a in db.query(CompanyAlias).filter_by(company_id=r.company_id).all()}
            if r.emissor not in existentes:
                db.add(CompanyAlias(company_id=r.company_id, alias=r.emissor))
                n_novos_aliases += 1
        db.commit()
        print(f"\n[OK] {n_novos_matches} debênture(s) atualizada(s), {n_novos_aliases} alias(es) novo(s) em Company.")


if __name__ == "__main__":
    main()
