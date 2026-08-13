"""Grava as capturas de `securitizados.py` no banco.

Upsert por Código (cadastro, `securitizados`) e por Código+Data
(histórico, `securitizado_spreads`). Mesmo desenho de `persist.py`, que
faz isso pras debêntures.

Idempotente: rodar de novo pro mesmo dia atualiza aquele dia, sem
duplicar linha nem tocar nos outros dias.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from ..models import Securitizado, SecuritizadoSpread
from . import issuers as isv

logger = logging.getLogger(__name__)

# Campos que pertencem ao CADASTRO (mudam pouco, sobrescritos a cada
# captura) vs. os do HISTÓRICO (uma linha por dia, nunca sobrescritos
# fora do próprio dia).
CAMPOS_CADASTRO = (
    "tipo_ativo", "emissor", "originador_credito", "serie", "emissao",
    "data_vencimento", "tipo_remuneracao", "indexador", "referencia_ntnb",
)
CAMPOS_SERIE = (
    "taxa_indicativa", "taxa_compra", "taxa_venda", "desvio_padrao",
    "pu", "pct_pu_par", "pct_vne", "pct_reune", "duration", "taxa_ntnb_ref", "spread",
)


def persistir_dia(db: Session, dt: date, linhas: list[dict], *, ligar_emissor: bool = True) -> dict:
    """Upsert de um dia inteiro de CRI/CRA."""
    if not linhas:
        return {"data": dt.isoformat(), "linhas": 0, "novos": 0, "ligados": 0}

    agora = datetime.now(timezone.utc)
    codigos = [l["codigo"] for l in linhas]

    cadastro = {
        s.codigo: s
        for s in db.query(Securitizado).filter(Securitizado.codigo.in_(codigos)).all()
    }
    serie_do_dia = {
        s.codigo: s
        for s in db.query(SecuritizadoSpread)
        .filter(SecuritizadoSpread.data == dt, SecuritizadoSpread.codigo.in_(codigos))
        .all()
    }

    novos = ligados = 0
    for linha in linhas:
        codigo = linha["codigo"]

        sec = cadastro.get(codigo)
        novo_registro = sec is None
        if novo_registro:
            sec = Securitizado(codigo=codigo, first_seen_at=agora)
            db.add(sec)
            cadastro[codigo] = sec
            novos += 1
        for campo in CAMPOS_CADASTRO:
            valor = linha.get(campo)
            if valor is not None:
                setattr(sec, campo, valor)
        sec.last_seen_at = agora

        # Emissor canônico vem do ORIGINADOR do crédito, não da
        # securitizadora -- ela é veículo, não devedora (decisão do Allan,
        # 04/08/2026). `resolver_issuer` NÃO cria emissor: originador que
        # não existe no cadastro fica sem ligação e aparece na
        # Administração, em vez de virar um emissor fantasma sem
        # taxonomia.
        #
        # SÓ NO PAPEL NOVO (`novo_registro`), não a cada dia.
        #
        # BUG REAL DE DESEMPENHO (05/08/2026): antes tentava resolver
        # sempre que `issuer_id` estivesse vazio. Como ~155 originadores
        # NUNCA casam (agro e bancos que não emitem debênture), eram 2
        # consultas por papel por dia, para sempre -- na importação de 302
        # dias do banco do Allan isso virou centenas de milhares de
        # SELECTs inúteis e a carga praticamente parou na metade.
        # `vincular_originadores()` roda no fim e pega os que passaram a
        # existir no cadastro depois.
        if ligar_emissor and novo_registro and linha.get("originador_credito"):
            issuer = isv.resolver_issuer(db, linha["originador_credito"])
            if issuer is not None:
                sec.issuer_id = issuer.id
                ligados += 1

        spread = serie_do_dia.get(codigo)
        if spread is None:
            spread = SecuritizadoSpread(codigo=codigo, data=dt)
            db.add(spread)
            serie_do_dia[codigo] = spread
        for campo in CAMPOS_SERIE:
            setattr(spread, campo, linha.get(campo))

    db.commit()
    # LIMPA A IDENTITY MAP da sessão. Sem isto, uma carga de muitos dias
    # fica quadrática: a sessão continua rastreando TODOS os objetos já
    # gravados, e cada commit seguinte reprocessa a coleção inteira.
    #
    # BUG REAL (05/08/2026): a importação do banco do Allan (101 mil
    # linhas em 302 dias) andou até ~74 mil linhas e praticamente parou.
    # Não era o banco nem o índice — era a sessão carregando 74 mil
    # objetos vivos a cada dia novo. Dia já gravado não precisa continuar
    # rastreado.
    db.expunge_all()
    return {
        "data": dt.isoformat(),
        "linhas": len(linhas),
        "novos": novos,
        "ligados": ligados,
    }


def vincular_originadores(db: Session) -> dict:
    """(Re)liga securitizados a emissores pelo originador do crédito.

    Roda depois de uma carga de taxonomia, pra pegar os originadores que
    passaram a existir no cadastro. Idempotente.

    Só preenche o que está vazio: uma ligação já feita (inclusive
    corrigida à mão na Administração) não é sobrescrita.
    """
    ligados = 0
    sem_match: dict[str, int] = {}
    for sec in db.query(Securitizado).all():
        if sec.issuer_id is not None:
            continue
        nome = sec.originador_credito
        if not nome:
            continue
        issuer = isv.resolver_issuer(db, nome)
        if issuer is None:
            sem_match[nome] = sem_match.get(nome, 0) + 1
            continue
        sec.issuer_id = issuer.id
        ligados += 1
    db.commit()
    total = db.query(Securitizado).count()
    com_issuer = db.query(Securitizado).filter(Securitizado.issuer_id.is_not(None)).count()
    return {
        "ligados_agora": ligados,
        "total": total,
        "com_issuer": com_issuer,
        "sem_match": sem_match,
    }
