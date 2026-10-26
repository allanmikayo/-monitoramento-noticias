"""Captura a curva de juros da ANBIMA (ETTJ real, prefixada e inflação
implícita) — ver app/spreads/curva_juros.py.

    python -m scripts.fetch_curva_juros                     # dia mais recente publicado
    python -m scripts.fetch_curva_juros --start 2024-10-01  # backfill até hoje
    python -m scripts.fetch_curva_juros --probe             # mostra a resposta crua (diagnóstico)

No backfill, dia que já está no banco é pulado; dia sem publicação
(feriado) volta vazio da API e é ignorado.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import CurvaJuros
from app.spreads import curva_juros as cj
from app.spreads.mercado_secundario import dias_uteis


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()

    if a.probe:
        bruto = cj.buscar(_d(a.start) if a.start else None)
        texto = json.dumps(bruto, ensure_ascii=False)
        print(texto[:3000])
        print("...", len(texto), "caracteres")
        ref, vert = cj.extrair_vertices(bruto)
        print("data_referencia:", ref, "| vértices:", len(vert), vert[:3])
        return 0

    CurvaJuros.__table__.create(bind=engine, checkfirst=True)
    with SessionLocal() as db:
        if not a.start:
            ref, vert = cj.extrair_vertices(cj.buscar(None))
            if not ref or not vert:
                print("API não devolveu curva reconhecível -- rode com --probe para ver a resposta.")
                return 1
            cj.gravar(db, ref, vert)
            db.commit()
            print(f"Curva de {ref:%d/%m/%Y} gravada ({len(vert)} vértices).")
            return 0

        existentes = set(db.scalars(select(CurvaJuros.data)))
        fim = _d(a.end) if a.end else date.today()
        gravados = vazios = 0
        for dia in dias_uteis(_d(a.start), fim):
            if dia in existentes:
                continue
            try:
                ref, vert = cj.extrair_vertices(cj.buscar(dia))
            except Exception as e:  # um dia ruim não derruba o backfill
                print(f"{dia}: erro {e}")
                continue
            if not vert:
                vazios += 1
                continue
            cj.gravar(db, ref or dia, vert)
            gravados += 1
            if gravados % 20 == 0:
                db.commit()
                print(f"... {gravados} dias gravados (até {dia})")
        db.commit()
        print(f"Backfill: {gravados} dias gravados, {vazios} sem publicação.")
        return 0 if gravados or vazios else 1


if __name__ == "__main__":
    sys.exit(main())
