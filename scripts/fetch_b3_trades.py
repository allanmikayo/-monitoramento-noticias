"""Captura o negócio a negócio da B3 (debêntures, CRI, CRA) e grava no banco
do credit_monitor -- aba "Emissores" do módulo Spreads (pedido do Allan,
24/07/2026; ver CLAUDE.md e app/spreads/b3_trades.py pro desenho completo).

Uso:
    python -m scripts.fetch_b3_trades
        Sem argumentos: captura só o dia de hoje (uso do dia a dia --
        é o que o agendador em app/scheduler.py chama a cada 15 min).

    python -m scripts.fetch_b3_trades --start 2026-07-01
        Backfill de uma data até hoje.

    python -m scripts.fetch_b3_trades --start 2026-07-01 --end 2026-07-15
        Backfill de um intervalo específico.

Ao contrário do backfill de spreads, aqui NÃO tem por padrão um histórico de
2 anos -- Allan pediu "últimas negociações" (uso corrente, não análise
histórica longa), e o volume é grande (~700 negócios/dia só de DEB/CRI/CRA).
Se quiser histórico de um período específico, rode com --start explícito.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine, ensure_schema
from app.spreads.b3_trades import fetch_trades
from app.spreads.persist import save_negocios_b3

logger = logging.getLogger(__name__)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def run(start: date, end: date) -> dict:
    """Captura e grava DIA A DIA, mesmo quando o intervalo é longo.

    MUDOU EM 08/09/2026. Antes um backfill pedia o intervalo inteiro numa
    tacada: `fetch_trades(start, end)` juntava tudo em memória e só então
    gravava. No catch-up de 03/09 a 08/09 isso deu 57.358 negócios em 84
    páginas -- e o processo morreu com `Segmentation fault` (exit 139),
    sem exceção nenhuma, logo depois da captura.

    Dia a dia é melhor por três motivos independentes da causa daquele
    segfault:

      - o pico de memória cai para o tamanho de UM pregão (~10 mil
        negócios) em vez do intervalo inteiro;
      - cada dia gravado é um ponto de retomada: se o quinto dia falhar,
        os quatro primeiros ficam no banco e a execução seguinte só
        precisa do que faltou (o dedupe é por `trade_code`);
      - o log passa a dizer EM QUE DIA parou, o que um intervalo único
        nunca disse.

    Fim de semana é pulado: a B3 não negocia, e pedir um sábado faz o
    coletor gastar as tentativas de "página 1 vazia" à toa. Feriado não dá
    para saber de antemão -- esse continua caindo na regra de página vazia.
    """
    ensure_schema()

    total_capturados = 0
    total_novos = 0
    dias_com_erro: list[str] = []

    dia = start
    while dia <= end:
        if dia.weekday() >= 5:  # sábado/domingo
            logger.info("%s: fim de semana, pulado", dia)
            dia += timedelta(days=1)
            continue
        try:
            trades = fetch_trades(dia, dia)
            with SessionLocal() as db:
                n_novos = save_negocios_b3(db, trades)
            total_capturados += len(trades)
            total_novos += n_novos
            logger.info("%s: %d capturado(s), %d novo(s) gravado(s)", dia, len(trades), n_novos)
        except Exception:  # noqa: BLE001
            # Um dia que falha não derruba os outros -- é a mesma regra que
            # já vale entre as etapas da rodada noturna.
            dias_com_erro.append(dia.isoformat())
            logger.exception("%s: FALHOU -- seguindo para o próximo dia", dia)
        dia += timedelta(days=1)

    logger.info(
        "Negócio a negócio %s a %s: %d capturados, %d novo(s) gravado(s)%s",
        start, end, total_capturados, total_novos,
        f" -- dias com erro: {', '.join(dias_com_erro)}" if dias_com_erro else "",
    )
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "n_capturados": total_capturados, "n_novos": total_novos,
        "dias_com_erro": dias_com_erro,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    p = argparse.ArgumentParser(description="Captura negócio a negócio da B3 (DEB/CRI/CRA)")
    p.add_argument("--start", type=_parse_date, default=None, help="Data inicial (AAAA-MM-DD). Padrão: hoje.")
    p.add_argument("--end", type=_parse_date, default=None, help="Data final (AAAA-MM-DD). Padrão: igual a --start.")
    args = p.parse_args()

    start = args.start or date.today()
    end = args.end or start
    if end < start:
        p.error("--end não pode ser antes de --start")

    run(start, end)


if __name__ == "__main__":
    main()
