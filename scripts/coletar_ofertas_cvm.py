"""Coleta diária das ofertas públicas de dívida na CVM (23/09/2026).

Chamado pelo GitHub Actions (.github/workflows/ofertas_cvm.yml) e também
rodável da máquina do Allan:

    python -m scripts.coletar_ofertas_cvm                    # rodada normal
    python -m scripts.coletar_ofertas_cvm --zip data/oferta_distribuicao.zip
    python -m scripts.coletar_ofertas_cvm --sem-backup

Seguro de rodar quantas vezes quiser no mesmo dia: a chave é o número do
requerimento e a trilha de status só ganha linha quando o status MUDA.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, ensure_schema  # noqa: E402
from app.primario import coleta  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
PASTA_BACKUP = Path(__file__).resolve().parent.parent / "data" / "backups_cvm"


def main() -> int:
    p = argparse.ArgumentParser(description="Coleta as ofertas públicas de dívida da CVM.")
    p.add_argument("--zip", help="usa um zip local em vez de baixar (teste/reprocesso)")
    p.add_argument("--sem-backup", action="store_true",
                   help="não guarda o zip baixado (no GitHub Actions o disco some depois)")
    args = p.parse_args()

    ensure_schema()
    conteudo = Path(args.zip).read_bytes() if args.zip else None
    with SessionLocal() as db:
        try:
            resumo = coleta.coletar(
                db, conteudo_zip=conteudo,
                pasta_backup=None if args.sem_backup else PASTA_BACKUP,
            )
        except ValueError as e:
            # Arquivo torto da CVM: falha alto para o job ficar vermelho, e
            # o banco continua com o dado de ontem.
            logging.error("coleta abortada: %s", e)
            return 1
    print(f"ofertas lidas: {resumo['lidas']} | novas: {resumo['novas']} | "
          f"mudaram de status: {resumo['mudancas']} | total no banco: {resumo['no_banco']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
