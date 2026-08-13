"""Backfill histórico do negócio a negócio da B3, guardando o AGREGADO.

Pedido do Allan (05/08/2026): "sobre dados da b3 vc n consegue construir
um histórico para termos mais dados para análise?".

Consegue — a fonte da B3 aceita data histórica (o próprio
`app/spreads/b3_trades.py` registra ter sido testado contra 2024-07-23).
O que não dá é guardar tudo: são ~12.600 negócios de DEB/CRI/CRA por dia
útil, ou **~6,3 milhões de linhas em dois anos**. Não cabe no Supabase
gratuito e trava qualquer consulta que varra o período.

Este script faz dia a dia: captura → grava o bruto → **agrega** → grava o
agregado. O agregado existe para as consultas serem rápidas (~650 mil
linhas em dois anos, contra 6,3 milhões no bruto), NÃO para substituir o
bruto.

GUARDA TUDO POR PADRÃO (decisão do Allan, 12/08/2026: "quero que os
bancos sejam capazes de ir armazenando todas as informações"). Use
`--so-agregado` se quiser abrir mão do negócio a negócio numa janela
específica -- e saiba que ele é ~91% do crescimento do banco
(ver ESTRUTURA_DADOS.md).

    # 6 meses (recomendado para começar)
    python -m scripts.backfill_b3_historico --start 2026-02-01

    # 2 anos
    python -m scripts.backfill_b3_historico --start 2024-08-01

    # reconstruir o agregado a partir do bruto já gravado (sem rede)
    python -m scripts.backfill_b3_historico --do-banco

    # backfill longo sem guardar o negócio a negócio (economiza espaço)
    python -m scripts.backfill_b3_historico --start 2024-08-01 --so-agregado

SEGURO DE INTERROMPER. O agregado é upsert por Código+Data e o script
pula por padrão os dias que já têm agregado, então basta rodar de novo
com o mesmo `--start`.

RITMO: uma pausa entre dias, de propósito. O módulo `b3_trades` já
registra um caso real (24/07/2026) em que duas rodadas seguidas em menos
de um minuto fizeram a segunda voltar vazia SEM ERRO — o pior tipo de
falha, porque parece "não teve negócio". Um backfill de 500 dias é
exatamente o cenário que provoca isso.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import Base, SessionLocal, engine, run_migrations  # noqa: E402
from app.models import NegocioB3Diario  # noqa: E402
from app.spreads import b3_agregado as agg  # noqa: E402
from app.spreads.b3_trades import fetch_trades  # noqa: E402
from app.spreads.persist import save_negocios_b3  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_b3")

PAUSA_ENTRE_DIAS = 2.0


def _dias_uteis(inicio: date, fim: date):
    d = inicio
    while d <= fim:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def _ja_tem_agregado(db, dia: date) -> bool:
    return db.scalar(
        select(func.count()).select_from(NegocioB3Diario).where(NegocioB3Diario.data == dia)
    ) > 0


def rodar(inicio: date, fim: date, *, manter_bruto: bool, refazer: bool,
          pausa: float) -> dict:
    dias = list(_dias_uteis(inicio, fim))
    logger.info("backfill de %s a %s — %d dias úteis", inicio, fim, len(dias))
    if manter_bruto:
        logger.info("guardando bruto + agregado (padrão) — retenção infinita")
    else:
        logger.info("modo SÓ AGREGADO: o negócio a negócio NÃO será guardado")

    processados = pulados = vazios = falhas = 0
    total_negocios = total_linhas = 0

    with SessionLocal() as db:
        for i, dia in enumerate(dias, 1):
            if not refazer and _ja_tem_agregado(db, dia):
                pulados += 1
                continue
            try:
                negocios = fetch_trades(dia, dia)
            except Exception as exc:  # noqa: BLE001
                # Um dia com problema não pode derrubar um backfill de
                # centenas de dias. Fica logado e é repescado numa próxima
                # rodada (o script pula quem já tem agregado).
                falhas += 1
                logger.error("%s: FALHOU (%s)", dia, exc)
                continue

            if not negocios:
                # Feriado ou instabilidade da fonte -- indistinguíveis
                # daqui. NÃO grava agregado vazio: assim o dia continua
                # sendo repescado numa próxima rodada, em vez de ficar
                # marcado como "já processado, sem negócio".
                vazios += 1
                logger.info("%s: sem negócios (feriado ou fonte instável) — será repescado", dia)
                time.sleep(pausa)
                continue

            if manter_bruto:
                save_negocios_b3(db, negocios)

            linhas = agg.agregar_linhas([_como_dict(n) for n in negocios])
            agg.gravar_agregado(db, linhas)

            processados += 1
            total_negocios += len(negocios)
            total_linhas += len(linhas)
            if i % 10 == 0 or i == len(dias):
                logger.info("  [%d/%d] %s — %d negócios -> %d papéis (acum.: %d negócios, %d linhas)",
                            i, len(dias), dia, len(negocios), len(linhas),
                            total_negocios, total_linhas)
            time.sleep(pausa)

    return {"dias": len(dias), "processados": processados, "pulados": pulados,
            "vazios": vazios, "falhas": falhas,
            "negocios": total_negocios, "linhas_agregado": total_linhas}


def _como_dict(n) -> dict:
    """`fetch_trades` devolve dataclass ou dict, dependendo da versão."""
    if isinstance(n, dict):
        return n
    return {c: getattr(n, c, None) for c in
            ("codigo", "data_negocio", "instrument_type", "quantidade",
             "preco", "volume", "taxa", "spread")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="AAAA-MM-DD")
    ap.add_argument("--end", help="AAAA-MM-DD (padrão: hoje)")
    ap.add_argument("--so-agregado", action="store_true",
                    help="NÃO guarda o negócio a negócio, só o agregado diário "
                         "(o bruto é ~91%% do crescimento do banco)")
    ap.add_argument("--refazer", action="store_true",
                    help="reprocessa dias que já têm agregado")
    ap.add_argument("--do-banco", action="store_true",
                    help="reconstrói o agregado a partir do bruto já gravado, sem rede")
    ap.add_argument("--podar", type=int, metavar="DIAS",
                    help="apaga bruto mais antigo que N dias — SÓ com agregado gravado "
                         "para todos os dias afetados. Não roda sozinho: a retenção "
                         "padrão do projeto é infinita.")
    ap.add_argument("--arquivar", type=Path, metavar="ARQUIVO.csv.gz",
                    help="exporta o bruto para CSV comprimido antes de podar")
    ap.add_argument("--pausa", type=float, default=PAUSA_ENTRE_DIAS)
    args = ap.parse_args()

    Base.metadata.create_all(engine)
    run_migrations()

    if args.do_banco:
        with SessionLocal() as db:
            r = agg.agregar_do_banco(db)
        logger.info("agregado reconstruído do banco: %d linhas em %d dias",
                    r["linhas_agregado"], r["dias"])
        return

    if args.podar is not None:
        from datetime import date as _date
        corte = _date.today() - timedelta(days=args.podar)
        with SessionLocal() as db:
            if args.arquivar:
                a = agg.arquivar_bruto(db, args.arquivar, corte)
                logger.info("arquivo: %d negócios -> %s (%s MB)",
                            a["arquivadas"], a["arquivo"], a.get("tamanho_mb"))
                if a["arquivadas"] == 0:
                    logger.info("nada a arquivar — poda cancelada por segurança")
                    return
            elif args.podar:
                logger.warning("podando SEM arquivar — o negócio a negócio "
                               "apagado não poderá ser recuperado")
            r = agg.podar_bruto(db, args.podar)
        logger.info("poda: %d negócios apagados antes de %s (%s)",
                    r["apagados"], r["corte"], r["motivo"])
        for d in r.get("dias_sem_agregado", [])[:5]:
            logger.info("    sem agregado: %s", d)
        return

    if not args.start:
        ap.error("informe --start, --do-banco ou --podar")

    inicio = datetime.strptime(args.start, "%Y-%m-%d").date()
    fim = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()

    r = rodar(inicio, fim, manter_bruto=not args.so_agregado,
              refazer=args.refazer, pausa=args.pausa)
    logger.info("=== FIM ===")
    logger.info("  dias no intervalo   : %d", r["dias"])
    logger.info("  processados         : %d", r["processados"])
    logger.info("  já tinham agregado  : %d", r["pulados"])
    logger.info("  sem negócio         : %d (serão repescados)", r["vazios"])
    logger.info("  falhas              : %d", r["falhas"])
    logger.info("  negócios capturados : %d", r["negocios"])
    logger.info("  linhas de agregado  : %d", r["linhas_agregado"])
    if r["falhas"] or r["vazios"]:
        logger.info("  -> rode o mesmo comando de novo para repescar")


if __name__ == "__main__":
    main()
