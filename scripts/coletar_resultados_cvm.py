"""Coleta das datas de entrega de ITR/DFP na CVM -- calendário de
resultados da página inicial (28/10/2026). Ver app/calendario.py.

Chamado pelo GitHub Actions (.github/workflows/resultados_cvm.yml). A CVM
atualiza esses arquivos UMA VEZ POR SEMANA; o job roda todo dia porque é
barato e pega a atualização no primeiro dia em que ela sai. O "divulgou
hoje" não depende deste job: vem do coletor do RAD, a cada 5 minutos.

    python -m scripts.coletar_resultados_cvm
    python -m scripts.coletar_resultados_cvm --zip itr_cia_aberta_2026.zip --documento ITR

Seguro de rodar quantas vezes quiser: guarda a PRIMEIRA entrega de cada
(CNPJ, documento, trimestre) e só atualiza se aparecer uma ainda mais cedo.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import calendario, models  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def criar_tabelas() -> None:
    """As duas tabelas do calendário nascem aqui (checkfirst) -- a Vercel
    não roda DDL e o init_db é manual. Mesmo padrão de scripts/fechar_b3.py."""
    Base.metadata.create_all(engine, tables=[models.EntregaResultadoCVM.__table__,
                                             models.ResultadoConfirmado.__table__])


def main() -> int:
    p = argparse.ArgumentParser(description="Coleta datas de ITR/DFP da CVM.")
    p.add_argument("--zip", help="usa um zip local em vez de baixar (teste/reprocesso)")
    p.add_argument("--documento", choices=["ITR", "DFP"], help="obrigatório com --zip")
    args = p.parse_args()

    criar_tabelas()
    if args.zip:
        if not args.documento:
            p.error("--documento é obrigatório com --zip")
        alvos = [(args.documento, Path(args.zip).read_bytes())]
    else:
        alvos = []
        for doc, url in calendario.arquivos_a_coletar(date.today()):
            try:
                alvos.append((doc, calendario.baixar(url)))
                logging.info("baixado %s", url)
            except FileNotFoundError:
                # Arquivo do ano corrente pode ainda não existir em janeiro.
                logging.warning("ainda não publicado: %s", url)

    linhas = []
    for doc, conteudo in alvos:
        try:
            linhas += calendario.ler_zip(conteudo, doc)
        except ValueError as e:
            logging.error("arquivo da CVM fora do formato esperado: %s", e)
            return 1
    if not linhas:
        logging.error("nenhuma linha lida -- nada gravado")
        return 1
    with SessionLocal() as db:
        r = calendario.gravar(db, linhas)
    print(f"::notice title=resultados_cvm::linhas lidas: {r['lidas']} | de interesse: {r['de_interesse']} | "
          f"novas: {r['novas']} | antecipadas: {r['atualizadas']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
