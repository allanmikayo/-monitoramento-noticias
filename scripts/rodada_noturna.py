"""Rodada noturna: spreads, securitizados e fechamento do Balcão B3.

Pedido do Allan (04/08/2026): *"essa atualização pode ser realizada uma
vez no dia pela noite, quando saem os dados de spread"*.

Substitui jobs separados por um orquestrador — a ordem entre eles é
DEPENDÊNCIA, não conveniência:

    1. DEBÊNTURES  — busca a curva de NTN-B do dia e a cacheia
                     (`ntnb_referencia`).
    2. SECURITIZADOS — LÊ essa curva do cache. Papel IPCA+ precisa dela pra
                     ter spread; sem o passo 1, seria uma requisição a mais
                     pelo mesmo dado.
    3. B3          — fecha o agregado diário do negócio a negócio e poda o
                     bruto além de 5 dias. A captura em si é do
                     `b3_trades.yml` (a cada 15 min no pregão); aqui é só o
                     fechamento. É esta etapa que segura o tamanho do banco.

RATINGS SAIU (20/08/2026). Havia mais duas etapas, `ratings` e `periodos`.
Foram removidas com o resto do pipeline de coleta e consolidação de rating,
que ficou para o futuro — ver o bloco de comentário acima de `FUNCOES`.
As **notícias** de ação de rating continuam normalmente, pelos coletores de
S&P/Moody's/Fitch em `app/sources/`, que é outro caminho.

Uma etapa que falha NÃO derruba as seguintes: cada uma é independente do
ponto de vista de erro (a de securitizados degrada pra busca ao vivo da
curva se a de debêntures falhar). O código de saída é != 0 se alguma
falhou, pra o GitHub Actions marcar o job em vermelho sem ter deixado de
gravar o que dava pra gravar.

    python -m scripts.rodada_noturna
    python -m scripts.rodada_noturna --data 2026-08-01
    python -m scripts.rodada_noturna --pular securitizados
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine, run_migrations  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rodada_noturna")

ETAPAS = ("debentures", "securitizados", "b3")


def etapa_debentures(db, dia: date | None) -> dict:
    from scripts import fetch_debenture_spreads as fds
    if hasattr(fds, "rodar_dia"):
        return fds.rodar_dia(db, dia) if dia else fds.rodar_dia(db)
    # O script de debêntures é anterior a este orquestrador e expõe só
    # `main()`; chamado assim, ele detecta sozinho o último dia publicado.
    argv = sys.argv
    sys.argv = ["fetch_debenture_spreads"] + (["--start", dia.isoformat()] if dia else [])
    try:
        fds.main()
        return {"via": "main()"}
    finally:
        sys.argv = argv


def etapa_securitizados(db, dia: date | None) -> dict:
    from scripts.fetch_securitizados import rodar_dia
    from app.spreads.persist_securitizados import vincular_originadores
    alvo = dia or date.today()
    r = rodar_dia(db, alvo)
    v = vincular_originadores(db)
    logger.info("  originador -> emissor: %d de %d ligados", v["com_issuer"], v["total"])
    return {**r, "vinculo": v}


def etapa_b3(db, dia: date | None) -> dict:
    """Consolida o negócio a negócio do dia no agregado diário.

    A CAPTURA do negócio a negócio continua sendo do `b3_trades.yml`, que
    roda a cada 15 min durante o pregão — não faz sentido repetir aqui. O
    que falta é o passo de fim de dia: fechar o agregado e liberar espaço.

    APAGA, SIM: depois de agregar, `podar_bruto()` remove o bruto além de
    `RETENCAO_BRUTO_DIAS` (5 dias). O comentário anterior aqui dizia "NÃO
    apaga nada / acumulado indefinidamente", contradizendo a chamada logo
    abaixo — corrigido em 20/08/2026.

    ESTA ETAPA É O QUE SEGURA O TAMANHO DO BANCO. Sem ela, `negocios_b3`
    cresce ~17 mil linhas por pregão sem nunca diminuir. Foi o que
    aconteceu entre a criação deste script (04/08) e 20/08: não existia
    workflow chamando a rodada noturna, e o Supabase começou a alertar
    estouro de Disk IO Budget.
    """
    from app.spreads import b3_agregado as agg

    r = agg.agregar_do_banco(db)
    logger.info("  consolidado: %d linhas em %d dias (guardado para sempre)",
                r["linhas_agregado"], r["dias"])

    # AGORA a poda roda — e é deliberada, não escondida. Desenho do Allan
    # (12/08/2026): consolidado para sempre, negócio a negócio só nos
    # últimos 5 dias. A ORDEM importa: agregar primeiro, podar depois, e
    # `podar_bruto` ainda confere dia a dia se o consolidado existe antes
    # de apagar qualquer coisa.
    p = agg.podar_bruto(db)
    logger.info("  negócio a negócio: %d apagados antes de %s (%s)",
                p["apagados"], p["corte"], p["motivo"])
    for d in p.get("dias_sem_agregado", [])[:5]:
        logger.warning("    dia sem consolidado, NÃO apagado: %s", d)
    return {**r, "poda": p}


# RATINGS SAÍRAM DAQUI (20/08/2026, decisão do Allan)
#
# Existiam duas etapas, `ratings` e `periodos`. Foram removidas junto com o
# resto do pipeline de coleta e consolidação de rating -- o assunto ficou
# para o futuro.
#
# O que continua funcionando, e é o que o Allan quis manter: as **notícias**
# de ação de rating, que os coletores de S&P, Moody's Local e Fitch em
# `app/sources/` já trazem para a tabela `articles` na varredura de 5 min.
# Isso é outro caminho, independente deste, e não foi tocado.
#
# O que ficou dormente, de propósito, para quando o assunto voltar:
#   - tabelas `issuers`, `issuer_ratings`, `issuer_rating_periodo`
#   - `app/spreads/issuers.py`, incluindo `registrar_acao_rating()`, que já
#     está pronta para receber os eventos e nunca teve quem a chamasse
#   - os quatro blocos de rating da aba Spreads (curva, dispersão, resumo,
#     compressão). Eles já saíam vazios antes desta remoção: a tabela de
#     ratings está zerada no Supabase desde sempre, porque as importações
#     só rodaram no banco local.
#
# A view `v_spread_rating` era criada dentro de `etapa_periodos`. Mudou para
# `scripts/init_db.py`, que é onde mora o resto do DDL -- ela é lida por
# `analitico.py` e pela aba Banco de Dados, e funciona com rating vazio.

FUNCOES = {
    "debentures": etapa_debentures,
    "securitizados": etapa_securitizados,
    "b3": etapa_b3,
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="AAAA-MM-DD (padrão: último dia publicado)")
    ap.add_argument("--pular", nargs="*", default=[], choices=ETAPAS,
                    help="etapas a não executar")
    args = ap.parse_args()

    dia = datetime.strptime(args.data, "%Y-%m-%d").date() if args.data else None

    Base.metadata.create_all(engine)
    run_migrations()
    db = SessionLocal()

    falhas = []
    try:
        for etapa in ETAPAS:
            if etapa in args.pular:
                logger.info("[%s] pulada", etapa.upper())
                continue
            logger.info("=== %s ===", etapa.upper())
            try:
                FUNCOES[etapa](db, dia)
            except Exception as exc:  # noqa: BLE001
                # Falha isolada não impede as outras -- é melhor gravar
                # spread sem securitizado do que não gravar nada.
                falhas.append(etapa)
                logger.error("[%s] FALHOU: %s", etapa, exc)
                logger.debug(traceback.format_exc())
    finally:
        db.close()

    if falhas:
        logger.error("rodada terminou com falha em: %s", ", ".join(falhas))
        sys.exit(1)
    logger.info("rodada noturna concluída sem erros")


if __name__ == "__main__":
    main()
