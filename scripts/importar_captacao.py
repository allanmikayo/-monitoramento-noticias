"""Importa os exports de captação líquida (diário e/ou mensal) para a aba
Cap. Líquida.

    python -m scripts.importar_captacao cp_diario.csv cp_mensal.csv

Mesmo caminho da tela de Administração (o uso normal); este script serve
para rodar da máquina do Allan sem o limite de tempo da Vercel. Cada
arquivo substitui o período que cobre. O nome da gestora vira hash e não é
gravado (ver app/captacao.py).
"""
from __future__ import annotations

import csv
import sys

from app import captacao as cl
from app.db import SessionLocal, engine
from app.models import CaptacaoDiaria, CaptacaoMensal


def importar_arquivo(caminho: str) -> str:
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        texto = f.read()
    dialeto = csv.Sniffer().sniff(texto[:5000], delimiters=";,")
    todas = [r for r in csv.reader(texto.splitlines(), dialeto) if any(c.strip() for c in r)]
    tipo, registros, descartadas = cl.converter_linhas(todas[0], todas[1:])
    if not registros:
        return f"{caminho}: nenhuma linha válida"
    (CaptacaoDiaria if tipo == "diario" else CaptacaoMensal).__table__.create(bind=engine, checkfirst=True)
    datas = [r["data"] for r in registros]
    with SessionLocal() as db:
        cl.apagar_periodo(db, tipo, min(datas), max(datas))
        r = cl.importar(db, tipo, registros)
        db.commit()
    return (f"{tipo}: {r['gravadas']} linhas gravadas, de {r['de']} a {r['ate']}"
            + (f"; {descartadas} ignoradas" if descartadas else ""))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for caminho in sys.argv[1:]:
        print(importar_arquivo(caminho))
