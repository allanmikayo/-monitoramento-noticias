"""Consultas da aba "Balcão B3" — volumetria de negociação de DEB/CRI/CRA.

Pedido do Allan (14/08/2026): uma aba focada em VOLUMETRIA, separada da aba
Spreads (que olha marcação a mercado da Anbima). Aqui a pergunta é "o quanto
e a que taxa este papel girou", não "quanto ele vale".

DE ONDE VEM CADA COISA — as duas tabelas têm papéis diferentes e retenções
diferentes (ver o cabeçalho de `b3_agregado.py`):

    negocios_b3_diario  guardado para sempre   -> blocos históricos
    negocios_b3         últimos 5 dias         -> só a seção "ao vivo"

Isso não é detalhe de implementação, é o desenho: perguntar histórico ao
bruto seria varrer milhões de linhas para responder o que o agregado já
responde, e é justamente o que estourou o Disk IO do Supabase em agosto.

O QUE ESTE MÓDULO NÃO FAZ: calcular spread. O spread de cada negócio já vem
calculado de `b3_trades.compute_trade_spreads`, na hora da gravação, e o
agregado diário já traz `spread_medio` ponderado por volume. Aqui só se
agrega o que já existe.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.orm import Session

from ..models import Debenture, DebentureSpread, NegocioB3, NegocioB3Diario

logger = logging.getLogger(__name__)

INSTRUMENTOS = ("DEB", "CRI", "CRA")
BRT = ZoneInfo("America/Sao_Paulo")

# Janelas dos cartões de volumetria. "Média diária" precisa de denominador
# explícito: aqui é a contagem de PREGÕES COM NEGÓCIO na janela, não dias de
# calendário -- senão feriado e fim de semana derrubam a média sem motivo.
JANELAS = {
    "semana": 7,
    "mes": 30,
    "ano": None,       # tratada à parte: ano corrente
    "ano_passado": None,
}


def _hoje(db: Session) -> date | None:
    """Data mais recente COM DADO, não `date.today()`.

    A B3 publica em D+1 e o agregado fecha à noite; usar o relógio faria a
    tela aparecer vazia toda manhã.
    """
    return db.scalar(select(func.max(NegocioB3Diario.data)))


def _filtro_tipo(q, tipos: list[str] | None):
    if tipos:
        return q.where(NegocioB3Diario.instrument_type.in_(tipos))
    return q.where(NegocioB3Diario.instrument_type.in_(INSTRUMENTOS))


# ---------------------------------------------------------------------------
# Bloco 1 — cartões de volumetria
# ---------------------------------------------------------------------------
def volumetria(db: Session, tipos: list[str] | None = None) -> dict:
    """Média diária de negócios e de volume em quatro janelas.

    Devolve também `pregoes` de cada janela: uma média diária sem o
    denominador à vista é fácil de ler errado.
    """
    ref = _hoje(db)
    if ref is None:
        return {"referencia": None, "janelas": []}

    def agrega(inicio: date, fim: date) -> dict:
        q = select(
            func.count(func.distinct(NegocioB3Diario.data)).label("pregoes"),
            func.sum(NegocioB3Diario.n_negocios).label("negocios"),
            func.sum(NegocioB3Diario.volume).label("volume"),
            func.count(func.distinct(NegocioB3Diario.codigo)).label("tickers"),
        ).where(NegocioB3Diario.data >= inicio, NegocioB3Diario.data <= fim)
        r = db.execute(_filtro_tipo(q, tipos)).one()
        pregoes = r.pregoes or 0
        return {
            "pregoes": pregoes,
            "negocios": int(r.negocios or 0),
            "volume": float(r.volume or 0.0),
            "tickers": int(r.tickers or 0),
            "negocios_dia": (r.negocios or 0) / pregoes if pregoes else None,
            "volume_dia": (r.volume or 0.0) / pregoes if pregoes else None,
        }

    janelas = [
        {"chave": "semana", "rotulo": "Última semana",
         "inicio": ref - timedelta(days=6), "fim": ref},
        {"chave": "mes", "rotulo": "Último mês",
         "inicio": ref - timedelta(days=29), "fim": ref},
        {"chave": "ano", "rotulo": f"{ref.year} (YTD)",
         "inicio": date(ref.year, 1, 1), "fim": ref},
        {"chave": "ano_passado", "rotulo": str(ref.year - 1),
         "inicio": date(ref.year - 1, 1, 1), "fim": date(ref.year - 1, 12, 31)},
    ]
    saida = []
    for j in janelas:
        saida.append({**j, "inicio": j["inicio"].isoformat(),
                      "fim": j["fim"].isoformat(), **agrega(j["inicio"], j["fim"])})
    return {"referencia": ref.isoformat(), "janelas": saida}


# ---------------------------------------------------------------------------
# Bloco 2 — série diária
# ---------------------------------------------------------------------------
def serie_diaria(db: Session, dias: int = 90, tipos: list[str] | None = None) -> dict:
    """Volume e número de negócios por dia e por tipo."""
    ref = _hoje(db)
    if ref is None:
        return {"pontos": []}
    inicio = ref - timedelta(days=dias)

    q = select(
        NegocioB3Diario.data,
        NegocioB3Diario.instrument_type,
        func.sum(NegocioB3Diario.volume).label("volume"),
        func.sum(NegocioB3Diario.n_negocios).label("negocios"),
    ).where(NegocioB3Diario.data >= inicio).group_by(
        NegocioB3Diario.data, NegocioB3Diario.instrument_type
    ).order_by(NegocioB3Diario.data)

    linhas = db.execute(_filtro_tipo(q, tipos)).all()
    por_data: dict[str, dict] = {}
    for r in linhas:
        d = r.data.isoformat()
        alvo = por_data.setdefault(d, {"data": d, "DEB": 0.0, "CRI": 0.0, "CRA": 0.0,
                                       "negocios": 0})
        alvo[r.instrument_type] = float(r.volume or 0.0)
        alvo["negocios"] += int(r.negocios or 0)
    return {"pontos": [por_data[k] for k in sorted(por_data)]}


# ---------------------------------------------------------------------------
# Bloco 3 — ranking de tickers
# ---------------------------------------------------------------------------
def ranking_tickers(db: Session, dias: int = 5, limite: int = 40,
                    tipos: list[str] | None = None) -> dict:
    """Tickers mais negociados na janela, com giro sobre estoque.

    SOBRE O DENOMINADOR DO GIRO: usa `DebentureSpread.estoque` (R$ milhões,
    da Anbima) na data mais recente disponível do papel -- não a quantidade
    emitida do cadastro da B3. Motivo: estoque da Anbima já é o que está em
    mercado, enquanto quantidade emitida ignora amortização e recompra.

    Consequência honesta: CRI e CRA quase sempre ficam SEM giro, porque não
    têm cadastro em `debentures`/`debenture_spreads`. Aparecem no ranking
    por volume, com giro vazio -- melhor que um número inventado.
    """
    ref = _hoje(db)
    if ref is None:
        return {"referencia": None, "linhas": []}
    inicio = ref - timedelta(days=dias - 1)

    q = select(
        NegocioB3Diario.codigo,
        func.min(NegocioB3Diario.instrument_type).label("tipo"),
        func.sum(NegocioB3Diario.volume).label("volume"),
        func.sum(NegocioB3Diario.n_negocios).label("negocios"),
        func.sum(NegocioB3Diario.quantidade).label("quantidade"),
        func.count(func.distinct(NegocioB3Diario.data)).label("pregoes"),
        func.max(NegocioB3Diario.maior_negocio).label("maior_negocio"),
        # Ponderadas por volume do dia, para não dar o mesmo peso a um dia de
        # 1 negócio e a um de 200.
        (func.sum(NegocioB3Diario.spread_medio * NegocioB3Diario.volume)
         / func.nullif(func.sum(case((NegocioB3Diario.spread_medio.isnot(None),
                                      NegocioB3Diario.volume), else_=0.0)), 0.0)
         ).label("spread_medio"),
        (func.sum(NegocioB3Diario.taxa_media * NegocioB3Diario.volume)
         / func.nullif(func.sum(case((NegocioB3Diario.taxa_media.isnot(None),
                                      NegocioB3Diario.volume), else_=0.0)), 0.0)
         ).label("taxa_media"),
    ).where(
        NegocioB3Diario.data >= inicio, NegocioB3Diario.data <= ref
    ).group_by(NegocioB3Diario.codigo).order_by(
        func.sum(NegocioB3Diario.volume).desc()
    ).limit(limite)

    linhas = db.execute(_filtro_tipo(q, tipos)).all()
    codigos = [r.codigo for r in linhas]
    estoques = _estoque_por_codigo(db, codigos)
    cadastro = _cadastro_por_codigo(db, codigos)

    saida = []
    for r in linhas:
        vol = float(r.volume or 0.0)
        est = estoques.get(r.codigo)          # R$ milhões
        est_reais = est * 1e6 if est else None
        cad = cadastro.get(r.codigo, {})
        saida.append({
            "codigo": r.codigo,
            "tipo": r.tipo,
            "emissor": cad.get("nome"),
            "indexador": cad.get("indexador"),
            "classe": cad.get("classe"),
            "volume": vol,
            "negocios": int(r.negocios or 0),
            "pregoes": int(r.pregoes or 0),
            "maior_negocio": float(r.maior_negocio) if r.maior_negocio else None,
            "spread_medio": float(r.spread_medio) if r.spread_medio is not None else None,
            "taxa_media": float(r.taxa_media) if r.taxa_media is not None else None,
            "estoque": est_reais,
            "giro": (vol / est_reais) if est_reais else None,
        })
    return {"referencia": ref.isoformat(), "inicio": inicio.isoformat(),
            "dias": dias, "linhas": saida}


def _estoque_por_codigo(db: Session, codigos: list[str]) -> dict[str, float]:
    """Estoque mais recente de cada código (R$ milhões)."""
    if not codigos:
        return {}
    sub = select(
        DebentureSpread.codigo,
        func.max(DebentureSpread.data).label("data"),
    ).where(
        DebentureSpread.codigo.in_(codigos),
        DebentureSpread.estoque.isnot(None),
    ).group_by(DebentureSpread.codigo).subquery()

    linhas = db.execute(
        select(DebentureSpread.codigo, DebentureSpread.estoque)
        .join(sub, (DebentureSpread.codigo == sub.c.codigo)
              & (DebentureSpread.data == sub.c.data))
    ).all()
    return {r.codigo: float(r.estoque) for r in linhas if r.estoque}


def _cadastro_por_codigo(db: Session, codigos: list[str]) -> dict[str, dict]:
    if not codigos:
        return {}
    linhas = db.execute(
        select(Debenture.codigo, Debenture.nome, Debenture.indexador, Debenture.classe)
        .where(Debenture.codigo.in_(codigos))
    ).all()
    return {r.codigo: {"nome": r.nome, "indexador": r.indexador, "classe": r.classe}
            for r in linhas}


# ---------------------------------------------------------------------------
# Bloco 4 — volume x spread
# ---------------------------------------------------------------------------
def volume_por_spread(db: Session, dias: int = 5, tipos: list[str] | None = None,
                      classe: str | None = None) -> dict:
    """Um ponto por ticker: volume da janela contra spread médio negociado.

    Quem fica de fora é contado, não descartado em silêncio -- a tela precisa
    poder dizer o tamanho do que não apareceu. São DUAS exclusões diferentes,
    e misturá-las esconde justamente o caso mais comum:

      `sem_spread`   -> é da classe pedida, mas não tem spread calculado
      `sem_cadastro` -> não tem classe nenhuma, então nem dá para saber se
                        pertence à classe. É quase todo CRI e CRA: eles não
                        têm cadastro em `debentures`.

    A primeira versão filtrava por classe ANTES de checar o spread, então
    todo papel sem cadastro sumia sem entrar em contagem alguma -- exatamente
    o problema que esta função existe para evitar.
    """
    r = ranking_tickers(db, dias=dias, limite=500, tipos=tipos)
    com, sem_spread, sem_cadastro = [], 0, 0
    for linha in r["linhas"]:
        if linha.get("classe") is None:
            sem_cadastro += 1
            continue
        if classe and linha["classe"] != classe:
            continue
        if linha["spread_medio"] is None:
            sem_spread += 1
            continue
        com.append(linha)
    return {"referencia": r["referencia"], "inicio": r.get("inicio"),
            "pontos": com, "sem_spread": sem_spread,
            "sem_cadastro": sem_cadastro}


# ---------------------------------------------------------------------------
# Bloco 5 — ao vivo
# ---------------------------------------------------------------------------
def tape(db: Session, limite: int = 60, tipos: list[str] | None = None) -> dict:
    """Últimos negócios do bruto (`negocios_b3`, retenção de 5 dias).

    Só `situacao == 'Confirmado'`: a B3 reprocessa em D+1 e ~6% do arquivo
    são cancelados/ajustados.
    """
    q = select(NegocioB3).where(
        NegocioB3.situacao == "Confirmado",
        NegocioB3.instrument_type.in_(tipos or INSTRUMENTOS),
    ).order_by(NegocioB3.data_negocio.desc(), NegocioB3.horario.desc()).limit(limite)

    negocios = db.execute(q).scalars().all()
    ultima = db.scalar(select(func.max(NegocioB3.captured_at)))
    return {
        "atualizado_em": ultima.astimezone(BRT).isoformat() if ultima else None,
        "linhas": [{
            "data": n.data_negocio.isoformat() if n.data_negocio else None,
            "horario": n.horario,
            "codigo": n.codigo,
            "tipo": n.instrument_type,
            "emissor": n.emissor,
            "quantidade": n.quantidade,
            "preco": n.preco,
            "volume": n.volume,
            "taxa": n.taxa,
            "spread": n.spread,
            "origem": n.origem,
        } for n in negocios],
    }


# Piso de liquidez para o ticker entrar no ranking de destaques. Sem ele o
# topo é sempre papel ilíquido com um print solto -- o alerta perde a
# credibilidade na primeira semana e ninguém olha mais.
MIN_NEGOCIOS_BASELINE = 3
MIN_VOLUME_BASELINE = 1_000_000.0


def destaques(db: Session, dias_baseline: int = 3, limite: int = 10,
              tipos: list[str] | None = None) -> dict:
    """Maiores diferenças entre o spread do dia e a baseline do papel.

    BASELINE = mediana ponderada por volume dos `dias_baseline` pregões
    ANTERIORES ao mais recente (escolha do Allan, 16/08/2026). Três dias e
    mediana, não D-1 e média, por dois motivos medidos: a maioria dos
    tickers faz pouquíssimos negócios por dia, então D-1 sozinho dá baseline
    de 1-2 negócios; e a mediana ponderada ignora o print de R$ 2 mil fora
    de mercado sem descartar o bloco de R$ 20 milhões.
    """
    ref = _hoje(db)
    if ref is None:
        return {"referencia": None, "aberturas": [], "fechamentos": []}

    ini_base = ref - timedelta(days=dias_baseline)
    fim_base = ref - timedelta(days=1)

    hoje_q = select(
        NegocioB3Diario.codigo,
        func.min(NegocioB3Diario.instrument_type).label("tipo"),
        NegocioB3Diario.spread_medio.label("spread"),
        NegocioB3Diario.volume.label("volume"),
        NegocioB3Diario.n_negocios.label("negocios"),
    ).where(
        NegocioB3Diario.data == ref,
        NegocioB3Diario.spread_medio.isnot(None),
    ).group_by(NegocioB3Diario.codigo, NegocioB3Diario.spread_medio,
               NegocioB3Diario.volume, NegocioB3Diario.n_negocios)
    hoje = {r.codigo: r for r in db.execute(_filtro_tipo(hoje_q, tipos)).all()}
    if not hoje:
        return {"referencia": ref.isoformat(), "aberturas": [], "fechamentos": []}

    base_q = select(
        NegocioB3Diario.codigo,
        (func.sum(NegocioB3Diario.spread_medio * NegocioB3Diario.volume)
         / func.nullif(func.sum(case((NegocioB3Diario.spread_medio.isnot(None),
                                      NegocioB3Diario.volume), else_=0.0)), 0.0)
         ).label("spread"),
        func.sum(NegocioB3Diario.n_negocios).label("negocios"),
        func.sum(NegocioB3Diario.volume).label("volume"),
    ).where(
        NegocioB3Diario.data >= ini_base,
        NegocioB3Diario.data <= fim_base,
        NegocioB3Diario.codigo.in_(list(hoje)),
        NegocioB3Diario.spread_medio.isnot(None),
    ).group_by(NegocioB3Diario.codigo)

    cadastro = _cadastro_por_codigo(db, list(hoje))
    linhas = []
    for r in db.execute(base_q).all():
        if (r.negocios or 0) < MIN_NEGOCIOS_BASELINE:
            continue
        if (r.volume or 0.0) < MIN_VOLUME_BASELINE:
            continue
        if r.spread is None:
            continue
        h = hoje[r.codigo]
        cad = cadastro.get(r.codigo, {})
        linhas.append({
            "codigo": r.codigo,
            "tipo": h.tipo,
            "emissor": cad.get("nome"),
            "indexador": cad.get("indexador"),
            "spread_hoje": float(h.spread),
            "spread_baseline": float(r.spread),
            "variacao_bps": float(h.spread) - float(r.spread),
            "negocios_hoje": int(h.negocios or 0),
            "volume_hoje": float(h.volume or 0.0),
            "negocios_baseline": int(r.negocios or 0),
        })

    linhas.sort(key=lambda x: x["variacao_bps"], reverse=True)
    return {
        "referencia": ref.isoformat(),
        "baseline_inicio": ini_base.isoformat(),
        "baseline_fim": fim_base.isoformat(),
        "aberturas": linhas[:limite],
        "fechamentos": list(reversed(linhas[-limite:])) if linhas else [],
    }


def termometro(db: Session, janela: int = 20, tipos: list[str] | None = None) -> dict:
    """Volume do dia mais recente contra a mediana dos `janela` pregões.

    Responde "o dia está movimentado?" sem precisar comparar números na mão.
    """
    ref = _hoje(db)
    if ref is None:
        return {"referencia": None}
    inicio = ref - timedelta(days=janela * 2)

    q = select(
        NegocioB3Diario.data,
        func.sum(NegocioB3Diario.volume).label("volume"),
    ).where(NegocioB3Diario.data >= inicio).group_by(NegocioB3Diario.data)
    por_dia = {r.data: float(r.volume or 0.0)
               for r in db.execute(_filtro_tipo(q, tipos)).all()}
    if not por_dia:
        return {"referencia": ref.isoformat()}

    hoje_vol = por_dia.get(ref, 0.0)
    anteriores = sorted((v for d, v in por_dia.items() if d < ref), reverse=True)
    anteriores = anteriores[:janela]
    if not anteriores:
        return {"referencia": ref.isoformat(), "volume": hoje_vol, "mediana": None}

    ordenado = sorted(anteriores)
    meio = len(ordenado) // 2
    mediana = (ordenado[meio] if len(ordenado) % 2
               else (ordenado[meio - 1] + ordenado[meio]) / 2)
    return {
        "referencia": ref.isoformat(),
        "volume": hoje_vol,
        "mediana": mediana,
        "razao": (hoje_vol / mediana) if mediana else None,
        "pregoes_comparados": len(ordenado),
    }
