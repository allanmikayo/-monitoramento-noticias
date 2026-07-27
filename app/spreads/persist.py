"""Grava as capturas de app/spreads/fetch.py no banco — upsert por Código
(cadastro, tabela `debentures`) e por Código+Data (histórico, tabela
`debenture_spreads`). Ver app/models.py (Debenture, DebentureSpread) para o
desenho das tabelas e CLAUDE.md para o desenho geral do módulo."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from ..models import Debenture, DebentureSpread, NegocioB3, NtnbReferencia
from .b3_trades import compute_trade_spreads
from .fetch import Caracteristicas, SpreadRow, compute_classe

logger = logging.getLogger(__name__)


def persist_day(db: Session, dt: date, rows: list[SpreadRow]) -> dict:
    """Upsert de um dia inteiro. Idempotente: rodar de novo pro mesmo dia
    atualiza os valores daquele dia, sem duplicar linha nem mexer em outros
    dias (chave única codigo+data em DebentureSpread)."""
    if not rows:
        return {"date": dt.isoformat(), "n_rows": 0, "n_new_debentures": 0}

    now = datetime.now(timezone.utc)
    codigos = [r.codigo for r in rows]

    existing_debentures = {
        d.codigo: d for d in db.query(Debenture).filter(Debenture.codigo.in_(codigos)).all()
    }
    existing_spreads = {
        s.codigo: s
        for s in db.query(DebentureSpread)
        .filter(DebentureSpread.data == dt, DebentureSpread.codigo.in_(codigos))
        .all()
    }

    n_new_debentures = 0
    for row in rows:
        deb = existing_debentures.get(row.codigo)
        if deb is None:
            deb = Debenture(codigo=row.codigo, first_seen_at=now)
            db.add(deb)
            existing_debentures[row.codigo] = deb
            n_new_debentures += 1
        if row.nome:
            deb.nome = row.nome
        deb.indexador = row.indexador
        deb.classe = compute_classe(row.indexador, deb.incentivada)
        # CORRIGIDO (27/07/2026): Allan apontou que o card B3 usava sempre
        # a NTN-B de vértice mais curto pra papel IPCA+ -- deveria usar a
        # referência ESPECÍFICA que a própria Anbima associa a cada papel.
        # Guardada aqui no cadastro (persistida a cada captura, igual
        # indexador/classe) pra `b3_trades.compute_trade_spreads` poder
        # consultar sem rebuscar o boletim inteiro -- ver
        # app/models.py Debenture.referencia_ntnb.
        deb.referencia_ntnb = row.referencia_ntnb
        deb.last_seen_at = now

        spread_row = existing_spreads.get(row.codigo)
        if spread_row is None:
            spread_row = DebentureSpread(codigo=row.codigo, data=dt)
            db.add(spread_row)
            existing_spreads[row.codigo] = spread_row
        spread_row.taxa_indicativa = row.taxa_indicativa
        spread_row.pu = row.pu
        spread_row.pct_pu_par = row.pct_pu_par
        spread_row.spread = row.spread
        spread_row.estoque = row.estoque
        spread_row.duration = row.duration

    db.commit()
    return {"date": dt.isoformat(), "n_rows": len(rows), "n_new_debentures": n_new_debentures}


def cache_ntnb_referencia(
    db: Session, dt: date, ntnb_rates: dict[str, float], min_ntnb: float | None, min_venc: str | None
) -> None:
    """Guarda a curva de NTN-B (taxa por vencimento) do dia `dt` (pedido
    do Allan, 27/07/2026 -- ver `models.NtnbReferencia`). Chamado de
    `scripts/fetch_debenture_spreads.py` logo depois de `persist_day`,
    reaproveitando o `(ntnb_rates, min_ntnb, min_venc)` que
    `fetch.fetch_spreads` já calculou nessa mesma chamada (zero requisição
    extra à Anbima). Só grava quando `min_ntnb` não é None -- não vale a
    pena cachear uma falha/dia sem publicação, deixa
    `b3_trades._get_ntnb_curve` tentar de novo na próxima vez em vez de
    ficar preso num cache vazio.

    AMPLIADO (27/07/2026, mesmo dia): antes só guardava `min_ntnb`/
    `min_venc` (vértice mais curto) -- `compute_trade_spreads` usava isso
    como referência única pra TODO negócio IPCA+, errado (Allan: cada
    papel tem sua própria referência de NTN-B). Agora guarda a curva
    inteira em `curva_json` pra permitir consultar a taxa do vencimento
    específico de cada papel; `min_ntnb`/`min_venc` continuam gravados só
    como fallback."""
    if min_ntnb is None:
        return
    curva_json = json.dumps(ntnb_rates)
    existente = db.get(NtnbReferencia, dt)
    if existente is None:
        db.add(NtnbReferencia(data=dt, min_ntnb=min_ntnb, min_venc=min_venc, curva_json=curva_json))
    else:
        existente.min_ntnb = min_ntnb
        existente.min_venc = min_venc
        existente.curva_json = curva_json
    db.commit()


def persist_caracteristicas(db: Session, caracs: list[Caracteristicas]) -> int:
    """Atualiza CNPJ / se é incentivada (e recalcula `classe`) no cadastro —
    só mexe em debêntures que já existem na tabela (características sem
    nenhum spread capturado ainda não têm o que atualizar)."""
    if not caracs:
        return 0
    by_codigo = {c.codigo: c for c in caracs}
    existentes = db.query(Debenture).filter(Debenture.codigo.in_(by_codigo.keys())).all()
    n = 0
    for deb in existentes:
        c = by_codigo.get(deb.codigo)
        if c is None:
            continue
        deb.incentivada = c.incentivada
        deb.cnpj = c.cnpj
        deb.classe = compute_classe(deb.indexador, deb.incentivada)
        n += 1
    db.commit()
    return n


def save_negocios_b3(db: Session, trades: list[dict]) -> int:
    """Grava só negócios NOVOS (dedupe por `trade_code`, o id que a
    própria B3 dá pra cada operação) -- necessário porque
    `b3_trades.fetch_trades` devolve o dia inteiro de novo a cada consulta
    (a fonte não tem um "só o que mudou desde a última vez"). Idempotente:
    rodar de novo pro mesmo período não duplica nada.

    BUG CORRIGIDO (24/07/2026): o mesmo `trade_code` pode aparecer mais de
    uma vez DENTRO do próprio `trades` recebido nesta chamada -- não só
    entre uma chamada e outra. Confirmado contra a B3 de verdade (Allan
    rodou o backfill do dia e bateu em
    `UNIQUE constraint failed: negocios_b3.trade_code` tentando inserir o
    mesmo trade_code duas vezes na MESMA transação): como a captura pagina
    ~15 páginas em sequência e a B3 segue recebendo negócios novos durante
    esse tempo, um negócio pode "empurrar" outro de página e aparecer
    duplicado entre duas páginas da mesma consulta. Dedupe dentro do lote
    (mantém a última ocorrência) além do dedupe contra o que já existe no
    banco."""
    if not trades:
        return 0
    por_trade_code = {t["trade_code"]: t for t in trades}  # último ganha se repetir no lote
    trades_unicos = list(por_trade_code.values())

    existentes = {
        row[0]
        for row in db.query(NegocioB3.trade_code)
        .filter(NegocioB3.trade_code.in_(por_trade_code.keys()))
        .all()
    }
    novos = [t for t in trades_unicos if t["trade_code"] not in existentes]
    # Spread em bps (pedido do Allan, 27/07/2026) calculado aqui, na hora
    # de gravar -- não em `fetch_trades` (que fica só a captura crua da
    # B3) -- ver app/spreads/b3_trades.py::compute_trade_spreads.
    compute_trade_spreads(db, novos)
    for t in novos:
        db.add(NegocioB3(**t))
    db.commit()
    return len(novos)
