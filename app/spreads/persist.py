"""Grava as capturas de app/spreads/fetch.py no banco — upsert por Código
(cadastro, tabela `debentures`) e por Código+Data (histórico, tabela
`debenture_spreads`). Ver app/models.py (Debenture, DebentureSpread) para o
desenho das tabelas e CLAUDE.md para o desenho geral do módulo."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import insert
from sqlalchemy.orm import Session

from ..models import Debenture, DebentureSpread, NegocioB3, NtnbReferencia

# Tamanho do lote de inserção do negócio a negócio da B3.
#
# BUG REAL (08/09/2026). A gravação era `db.add(NegocioB3(**t))` num laço e
# um `commit()` no fim. O SQLAlchemy junta isso em `insertmanyvalues` de mil
# linhas por comando -- e, porque são objetos do ORM com chave primária
# `id` autoincremental, cada comando ainda vem com `RETURNING id`. O
# resultado, medido no log do Actions: UM comando de 423 KB com ~16 mil
# parâmetros, cancelado por `statement timeout` depois da captura inteira
# ter dado certo (5.606 negócios em 9 páginas). O job morria no último
# passo e o dia não era gravado -- foi assim que `negocios_b3` chegou a
# zero linha viva com o coletor "funcionando".
#
# 250 é pequeno o bastante para cada comando terminar bem dentro do limite,
# e grande o bastante para não virar tráfego de rede. Com commit por lote,
# uma falha no meio preserva o que já entrou: como o dedupe é por
# `trade_code`, a execução seguinte continua de onde parou em vez de
# recomeçar.
LOTE_NEGOCIOS_B3 = 250
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
        # Condições da emissão (11/09/2026) -- ver models.Debenture. São
        # imutáveis pra um papel já emitido, mas regravar a cada rodada é
        # o que faz o campo APARECER nos papéis que já estavam no cadastro
        # antes desta coluna existir, sem precisar de um backfill à parte.
        deb.data_emissao = c.data_emissao
        deb.indice_emissao = c.indice_emissao
        deb.percentual_emissao = c.percentual_emissao
        deb.taxa_emissao = c.taxa_emissao
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

    # DEDUPE POR FAIXA DE DATA, NÃO POR LISTA DE trade_code (20/08/2026).
    #
    # A versão anterior montava `trade_code IN (...)` com TODOS os códigos do
    # lote. Como `fetch_trades` devolve o dia inteiro a cada consulta e o job
    # roda de 15 em 15 min, no fim do pregão isso virava um IN com ~17 mil
    # literais, mais de 30 vezes por dia -- consulta gigante de montar, de
    # trafegar e de planejar.
    #
    # Filtrar por `data_negocio` faz o mesmo trabalho com uma varredura de
    # índice por faixa: o índice `ix_negocio_data` já existe, o resultado é
    # do mesmo tamanho e o SQL é minúsculo. O `trade_code` continua sendo a
    # chave do dedupe, só que comparado em Python.
    #
    # Motivo: o Disk IO do Supabase estava cravado em 100% (CPU em 18%),
    # com o banco ocupando só 176 MB -- ou seja, saturação de operações, não
    # de espaço.
    datas = {t["data_negocio"] for t in trades_unicos if t.get("data_negocio")}
    if datas:
        existentes = {
            row[0]
            for row in db.query(NegocioB3.trade_code)
            .filter(NegocioB3.data_negocio.in_(datas))
            .all()
        }
    else:
        # Sem data no lote (não deveria acontecer): cai no comportamento
        # antigo em vez de arriscar gravar duplicado.
        existentes = {
            row[0]
            for row in db.query(NegocioB3.trade_code)
            .filter(NegocioB3.trade_code.in_(por_trade_code.keys()))
            .all()
        }
    novos = [t for t in trades_unicos if t["trade_code"] not in existentes]
    if not novos:
        return 0
    # Spread em bps (pedido do Allan, 27/07/2026) calculado aqui, na hora
    # de gravar -- não em `fetch_trades` (que fica só a captura crua da
    # B3) -- ver app/spreads/b3_trades.py::compute_trade_spreads.
    compute_trade_spreads(db, novos)

    # INSERÇÃO EM LOTES, PELO CORE (08/09/2026) -- ver LOTE_NEGOCIOS_B3.
    #
    # `insert(NegocioB3)` com uma lista de dicionários é executemany puro:
    # sem objeto de ORM, sem identity map e, principalmente, SEM
    # `RETURNING id` -- ninguém aqui usa o id gerado. Os defaults de coluna
    # (captured_at) continuam sendo aplicados normalmente.
    gravados = 0
    n_lotes = -(-len(novos) // LOTE_NEGOCIOS_B3)  # divisão para cima
    for i in range(0, len(novos), LOTE_NEGOCIOS_B3):
        lote = novos[i:i + LOTE_NEGOCIOS_B3]
        db.execute(insert(NegocioB3), lote)
        # Commit por lote: o que já entrou fica, mesmo se o próximo falhar.
        db.commit()
        gravados += len(lote)
        # Progresso a cada 20 lotes (5 mil linhas). Sem isso, uma queda no
        # meio da gravação -- inclusive um `Segmentation fault`, que não
        # deixa traceback -- não diz quanto tinha entrado. Aconteceu em
        # 08/09/2026 e a última linha do log era a da captura.
        n_lote = i // LOTE_NEGOCIOS_B3 + 1
        if n_lote % 20 == 0 or n_lote == n_lotes:
            logger.info("  gravando negócios da B3: lote %d/%d (%d linha(s))",
                        n_lote, n_lotes, gravados)
    return gravados
