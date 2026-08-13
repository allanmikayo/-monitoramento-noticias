"""Captura diária de CRI/CRA da API da Anbima.

Espelha `scripts/fetch_debenture_spreads.py`. Divulgação da Anbima é
diária a partir das 20h de Brasília.

    # só hoje (uso normal, chamado pelo GitHub Actions)
    python -m scripts.fetch_securitizados

    # um dia específico
    python -m scripts.fetch_securitizados --start 2026-07-10

    # backfill de um intervalo
    python -m scripts.fetch_securitizados --start 2025-01-02 --end 2026-07-31

    # carga inicial a partir do snapshot, sem tocar na rede
    python -m scripts.fetch_securitizados --seed-json data/cra_cri_snapshot.json

Seguro de interromper e rodar de novo: o upsert é por Código+Data.

SOBRE A CURVA DE NTN-B
----------------------
Papel IPCA+ precisa da taxa da NTN-B de referência pra ter spread. A
curva do dia já é buscada e cacheada pelo job de debêntures
(`NtnbReferencia`), então aqui ela é LIDA do cache — bater na Anbima de
novo pelo mesmo dado seria uma requisição a mais por dia sem ganho algum.
Se o cache não existir pro dia (ex.: este job rodou antes do de
debêntures), busca ao vivo.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine, run_migrations  # noqa: E402
from app.models import NtnbReferencia  # noqa: E402
from app.spreads import securitizados as sec  # noqa: E402
from app.spreads import persist_securitizados as psec  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fetch_securitizados")


def _curva_ntnb(db, dia: date) -> dict[str, float]:
    """Curva `{vencimento: taxa}` do dia — do cache, ou ao vivo."""
    cache = db.get(NtnbReferencia, dia)
    if cache is not None and cache.curva_json:
        try:
            return json.loads(cache.curva_json)
        except json.JSONDecodeError:
            logger.warning("curva_json inválida para %s — buscando ao vivo", dia)
    try:
        from app.spreads.fetch import fetch_ntnb_curve
        curva, _, _ = fetch_ntnb_curve(dia)
        return curva or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("não consegui a curva NTN-B de %s (%s) — IPCA+ ficará sem spread", dia, exc)
        return {}


def _dias(inicio: date, fim: date):
    d = inicio
    while d <= fim:
        if d.weekday() < 5:   # a Anbima não publica em fim de semana
            yield d
        d += timedelta(days=1)


def rodar_dia(db, dia: date) -> dict:
    curva = _curva_ntnb(db, dia)
    linhas = sec.buscar(dia, curva)
    if not linhas:
        logger.info("%s: sem publicação", dia)
        return {"data": dia.isoformat(), "linhas": 0}
    r = psec.persistir_dia(db, dia, linhas)
    res = sec.resumo(linhas)
    logger.info(
        "%s: %d papéis (%s) | indexadores %s | sem spread %d | novos %d | ligados %d",
        dia, res["total"], res["por_tipo"], res["por_indexador"],
        res["sem_spread"], r["novos"], r["ligados"],
    )
    return r


def carregar_seed(db, caminho: Path) -> dict:
    """Carga inicial a partir do JSON extraído do Dashboard_Snapshot.

    O snapshot já traz `indexador`, `tipo_ativo` e `spread_bps` calculados
    — mas aqui eles são RECALCULADOS pelas funções do módulo, não
    copiados. Motivo: as fórmulas foram validadas contra esse mesmo
    snapshot com zero divergência (95.413 linhas), então recalcular dá o
    mesmo número e garante que a carga inicial e a diária produzam dado
    idêntico. Copiar deixaria as duas origens sujeitas a divergir sem
    ninguém notar.
    """
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    por_dia: dict[date, list[dict]] = {}
    ignoradas = 0
    for row in dados:
        # O snapshot tem os nomes de campo da própria API, exceto que já
        # traz a taxa da NTN-B resolvida (`taxa_ntnb_ref`) em vez do
        # vencimento de referência.
        norm = sec.normalizar(row)
        if norm is None or norm["data"] is None:
            ignoradas += 1
            continue
        taxa_ntnb = row.get("taxa_ntnb_ref") or None
        norm["taxa_ntnb_ref"] = taxa_ntnb
        norm["spread"] = sec.calcular_spread(norm["indexador"], norm["taxa_indicativa"], taxa_ntnb)
        por_dia.setdefault(norm["data"], []).append(norm)

    total = 0
    for dia in sorted(por_dia):
        r = psec.persistir_dia(db, dia, por_dia[dia])
        total += r["linhas"]
    return {"dias": len(por_dia), "linhas": total, "ignoradas": ignoradas}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="AAAA-MM-DD (padrão: hoje)")
    ap.add_argument("--end", help="AAAA-MM-DD (padrão: igual a --start)")
    ap.add_argument("--seed-json", type=Path, help="carga inicial de um JSON, sem rede")
    args = ap.parse_args()

    Base.metadata.create_all(engine)
    run_migrations()
    db = SessionLocal()
    try:
        if args.seed_json:
            logger.info("carga inicial a partir de %s", args.seed_json.name)
            r = carregar_seed(db, args.seed_json)
            logger.info("  %d dias, %d linhas (%d ignoradas)", r["dias"], r["linhas"], r["ignoradas"])
        else:
            inicio = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else date.today()
            fim = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else inicio
            for dia in _dias(inicio, fim):
                try:
                    rodar_dia(db, dia)
                except Exception as exc:  # noqa: BLE001
                    # Um dia com problema não pode derrubar um backfill de
                    # centenas de dias -- loga e segue.
                    logger.error("%s: FALHOU (%s)", dia, exc)

        v = psec.vincular_originadores(db)
        logger.info(
            "vínculo originador -> emissor: %d de %d (%d ligados agora, %d originadores sem match)",
            v["com_issuer"], v["total"], v["ligados_agora"], len(v["sem_match"]),
        )
        for nome, _ in sorted(v["sem_match"].items())[:10]:
            logger.info("    sem match: %s", nome[:70])
    finally:
        db.close()


if __name__ == "__main__":
    main()
