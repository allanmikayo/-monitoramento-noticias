"""Verifica se a captura diária de spreads (`spreads_daily.yml`, 21h BRT)
realmente trouxe o dado mais recente -- pedido do Allan (27/07/2026):
"podemos programar uma verificação pra ver se foi atualizado mesmo".

Por que isso não é redundante com o próprio `fetch_debenture_spreads.py`
rodar sem erro: o script pode terminar com "sucesso" (exit 0) mesmo sem
capturar nada de novo -- ex. se a Anbima ainda não tinha publicado o
boletim do dia no momento exato em que o cron rodou (o boletim
historicamente sai tarde, ~18-21h BRT -- ver CLAUDE.md, seção da curva
NTN-B), ou alguma falha silenciosa deixa `rows` vazio sem lançar exceção.
Isso passaria batido sem ninguém notar.

Lógica: pergunta pra própria Anbima (`detect_latest_published_date`,
MESMA chamada que `fetch_debenture_spreads.py` já usa pra saber o que
capturar) qual é o dia mais recente JÁ PUBLICADO por ela, e compara com
o que está de fato gravado no banco (`MAX(DebentureSpread.data)`). Se o
banco não tem esse dia, a captura ficou pra trás -- sai com código 1
(falha), o que faz o GitHub Actions marcar o workflow como falho e
mandar notificação por e-mail pro Allan (comportamento padrão do
GitHub, não precisa de nenhuma integração nova tipo Slack/e-mail).

Pensado pra rodar num workflow SEPARADO (`spreads_verify.yml`), agendado
~1h DEPOIS do workflow de captura -- dá uma folga pro caso da Anbima
publicar um pouco atrasado às vezes, em vez de checar exatamente no
mesmo instante do cron de captura (que geraria alarme falso todo dia em
que a publicação atrasasse alguns minutos).

Uso:
    python -m scripts.verify_spreads_updated
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import DebentureSpread
from app.spreads.fetch import build_session, detect_latest_published_date

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    session = build_session()
    try:
        esperado = detect_latest_published_date(session)
    except Exception:
        logger.exception(
            "Não consegui perguntar pra Anbima qual é o dia mais recente publicado -- "
            "sem isso não dá pra confirmar se a captura de hoje funcionou."
        )
        return 1

    with SessionLocal() as db:
        capturado = db.query(DebentureSpread.data).order_by(DebentureSpread.data.desc()).limit(1).scalar()

    if capturado is None:
        logger.error(
            "Base de spreads está VAZIA -- Anbima já publicou até %s e não há "
            "NENHUM dado no banco. Verifique se a captura diária rodou alguma vez.",
            esperado,
        )
        return 1

    if capturado < esperado:
        logger.error(
            "Captura desatualizada: Anbima já publicou até %s, mas o banco só "
            "tem dado até %s. A captura diária (spreads_daily.yml) rodou sem "
            "pegar o dia mais recente -- verifique o log dela.",
            esperado, capturado,
        )
        return 1

    logger.info(
        "OK: banco atualizado até %s (Anbima publicou até %s -- igual ou mais "
        "recente, tudo certo).", capturado, esperado,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
