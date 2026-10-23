"""Importa um export de volume negociado para a aba Mercado Secundário.

    python -m scripts.importar_mercado_secundario caminho/do/export.xlsx

Mesmo caminho da tela de Administração (que é o uso normal); este script
serve para cargas grandes rodadas da máquina do Allan, sem o limite de
tempo da Vercel. Aceita .xlsx e .csv. Se o Excel avisou que cortou o
export, o dia mais antigo (incompleto) é descartado.
"""
from __future__ import annotations

import sys

from app.db import SessionLocal, engine
from app.models import MercadoSecundario
from app.spreads import mercado_secundario as ms


def main(caminho: str) -> None:
    cab, linhas, truncado = ms.ler_arquivo(caminho)
    registros, descartadas = ms.converter_linhas(cab, linhas)
    if truncado:
        registros, dia = ms.descartar_dia_incompleto(registros)
        print(f"Export cortado pelo Excel: {dia:%d/%m/%Y} veio incompleto e foi descartado.")
    MercadoSecundario.__table__.create(bind=engine, checkfirst=True)
    with SessionLocal() as db:
        r = ms.importar(db, registros)
        db.commit()
    print(f"{r['gravadas']} linhas gravadas ({r['datas']} pregões, {r['de']} a {r['ate']}); "
          f"{descartadas} sem ticker, data ou volume ignoradas.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
