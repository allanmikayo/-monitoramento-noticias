"""Consultas da aba "Mercado Primário" (23/09/2026).

A base responde "quanto, de quem, por quem e quando" -- nunca "a que taxa".
O arquivo da CVM não traz remuneração, prazo nem rating (ver o cabeçalho de
`coleta.py`), então nada aqui tenta inferir spread: para isso existe a aba
Spreads, que lê a Anbima.

DECISÕES QUE VALEM PARA TODOS OS BLOCOS
---------------------------------------
* O eixo do tempo é a DATA DE REGISTRO. A mediana entre registro e
  encerramento é de 4 dias nas debêntures, então registro é a melhor proxy
  de "veio a mercado" e é a única data preenchida em praticamente todas as
  linhas vivas.
* Volume é `Valor_Total_Registrado`, o valor da oferta registrada. Não é o
  efetivamente distribuído -- a CVM não publica o colocado nesse arquivo.
* "Incentivada" tem três estados, não dois: S, N e VAZIO (a CVM deixa em
  branco em ~26% das linhas). Em todo lugar que mostra incentivada, o não
  informado aparece separado; somá-lo a "não" seria inventar dado.
* As linhas vêm com poucas colunas de propósito: são ~4.400 ofertas, e
  trazer as 30 colunas de cada uma para agregar em Python custaria alguns MB
  por carga de página sem nenhum ganho.
"""
from __future__ import annotations

import collections
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import OfertaCVM, OfertaCVMStatus
from .coleta import ROTULO_INSTRUMENTO, STATUS_ABERTO, STATUS_MORTO

JANELAS = ("12m", "24m", "ano", "tudo")
INSTRUMENTOS = tuple(ROTULO_INSTRUMENTO)          # DEB, CRI, CRA
INCENTIVADA = ("", "S", "N")                       # "" = todas

# Uma oferta aberta há muito tempo quase sempre é entulho: registro que não
# virou emissão e vai caducar. A tela marca em vez de esconder -- sumir com
# a linha seria decidir pelo analista.
DIAS_PARADA = 90
TOP_PADRAO = 15


def _hoje(db: Session) -> date:
    """Data mais recente com registro na base -- não o relógio.

    A CVM publica de madrugada; usar `date.today()` faria a aba parecer
    vazia nas primeiras horas ou num dia sem publicação.
    """
    ultimo = db.scalar(select(func.max(OfertaCVM.data_registro)))
    return ultimo or date.today()


def janela_para_datas(janela: str, hoje: date) -> tuple[date | None, date]:
    if janela == "24m":
        return hoje - timedelta(days=730), hoje
    if janela == "ano":
        return date(hoje.year, 1, 1), hoje
    if janela == "tudo":
        return None, hoje
    return hoje - timedelta(days=365), hoje


_COLUNAS = (
    OfertaCVM.numero_requerimento, OfertaCVM.data_registro, OfertaCVM.data_encerramento,
    OfertaCVM.instrumento, OfertaCVM.valor_total, OfertaCVM.nome_emissor,
    OfertaCVM.lider, OfertaCVM.status, OfertaCVM.incentivado,
    OfertaCVM.regime_distribuicao, OfertaCVM.publico_alvo, OfertaCVM.sustentavel,
)


def _linhas(db: Session, inicio: date | None, fim: date, instrumentos, incentivada: str):
    q = select(*_COLUNAS).where(
        OfertaCVM.data_registro.is_not(None),
        OfertaCVM.data_registro <= fim,
        OfertaCVM.status.not_in(STATUS_MORTO),
    )
    if inicio:
        q = q.where(OfertaCVM.data_registro >= inicio)
    if instrumentos:
        q = q.where(OfertaCVM.instrumento.in_(instrumentos))
    if incentivada in ("S", "N"):
        q = q.where(OfertaCVM.incentivado == incentivada)
    return db.execute(q).all()


def _bi(valor: float) -> float:
    return round((valor or 0) / 1e9, 3)


def _mediana(valores: list[float]) -> float:
    if not valores:
        return 0.0
    v = sorted(valores)
    meio = len(v) // 2
    return v[meio] if len(v) % 2 else (v[meio - 1] + v[meio]) / 2


def _kpis(linhas) -> dict:
    valores = [r.valor_total for r in linhas if r.valor_total]
    firme = sum(r.valor_total or 0 for r in linhas
                if (r.regime_distribuicao or "").startswith("Garantia Firme"))
    total = sum(valores)
    return {
        "volume": _bi(total),
        "ofertas": len(linhas),
        "ticket_mediano": round(_mediana(valores) / 1e6, 1),   # R$ milhões
        "garantia_firme_pct": round(firme / total * 100, 1) if total else 0.0,
    }


def _serie_mensal(linhas, inicio: date | None, fim: date) -> list[dict]:
    """Volume por mês e instrumento, com os meses vazios preenchidos.

    Mês sem nenhuma oferta existe (janeiro é sempre fraco) e precisa aparecer
    como zero: uma barra ausente no meio da série faz o eixo mentir.
    """
    por_mes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in linhas:
        por_mes[r.data_registro.strftime("%Y-%m")][r.instrumento] += (r.valor_total or 0)
    if not inicio:
        datas = [r.data_registro for r in linhas]
        inicio = min(datas) if datas else fim
    meses, cursor = [], date(inicio.year, inicio.month, 1)
    while cursor <= fim:
        chave = cursor.strftime("%Y-%m")
        linha = {"mes": chave + "-01", "total": 0.0}
        for instr in INSTRUMENTOS:
            linha[instr] = _bi(por_mes[chave][instr])
            linha["total"] += linha[instr]
        linha["total"] = round(linha["total"], 3)
        meses.append(linha)
        cursor = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)
    return meses


def _incentivadas(linhas) -> list[dict]:
    """Debêntures por trimestre: incentivada, não incentivada, não informado.

    É a disputa direta com CRI e CRA pelo mesmo bolso isento -- e o pedaço
    "não informado" fica à vista porque muda a leitura do share.
    """
    tri: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in linhas:
        if r.instrumento != "DEB":
            continue
        t = f"{r.data_registro.year}-T{(r.data_registro.month - 1) // 3 + 1}"
        chave = {"S": "incentivada", "N": "nao"}.get((r.incentivado or "").strip(), "sem_info")
        tri[t][chave] += (r.valor_total or 0)
    saida = []
    for t in sorted(tri):
        c = tri[t]
        total = sum(c.values())
        saida.append({
            "trimestre": t,
            "incentivada": _bi(c["incentivada"]), "nao": _bi(c["nao"]),
            "sem_info": _bi(c["sem_info"]),
            "share_incentivada": round(c["incentivada"] / total * 100, 1) if total else 0.0,
        })
    return saida


def _lideres(linhas, top: int) -> list[dict]:
    vol, n = collections.Counter(), collections.Counter()
    for r in linhas:
        nome = r.lider or "Não informado"
        vol[nome] += (r.valor_total or 0)
        n[nome] += 1
    total = sum(vol.values())
    return [{"lider": k, "volume": _bi(v), "ofertas": n[k],
             "share": round(v / total * 100, 1) if total else 0.0}
            for k, v in vol.most_common(top)]


def _maiores(linhas, top: int) -> list[dict]:
    ordenadas = sorted(linhas, key=lambda r: -(r.valor_total or 0))[:top]
    return [{
        "data": r.data_registro.isoformat(), "emissor": r.nome_emissor,
        "instrumento": r.instrumento, "valor": round((r.valor_total or 0) / 1e6, 1),
        "lider": r.lider or "—", "incentivada": (r.incentivado or "").strip(),
        "status": r.status,
    } for r in ordenadas]


def _estreantes(db: Session, linhas, inicio: date | None, top: int) -> list[dict]:
    """Emissores cuja PRIMEIRA oferta da base caiu dentro da janela.

    "Primeira da base" e não "primeira da vida": a série começa em jan/2023.
    A tela diz isso no subtítulo.
    """
    primeira = dict(db.execute(
        select(OfertaCVM.nome_emissor, func.min(OfertaCVM.data_registro))
        .where(OfertaCVM.data_registro.is_not(None), OfertaCVM.status.not_in(STATUS_MORTO))
        .group_by(OfertaCVM.nome_emissor)
    ).all())
    novos = []
    for r in linhas:
        if primeira.get(r.nome_emissor) == r.data_registro and (
                inicio is None or r.data_registro >= inicio):
            novos.append(r)
    novos.sort(key=lambda r: -(r.valor_total or 0))
    return [{"data": r.data_registro.isoformat(), "emissor": r.nome_emissor,
             "instrumento": r.instrumento, "valor": round((r.valor_total or 0) / 1e6, 1),
             "lider": r.lider or "—"} for r in novos[:top]]


def pipeline(db: Session, instrumentos=(), hoje: date | None = None) -> list[dict]:
    """Ofertas com registro concedido e ainda não encerradas."""
    hoje = hoje or _hoje(db)
    q = select(*_COLUNAS).where(OfertaCVM.status.in_(STATUS_ABERTO))
    if instrumentos:
        q = q.where(OfertaCVM.instrumento.in_(instrumentos))
    linhas = db.execute(q).all()
    saida = []
    for r in linhas:
        dias = (hoje - r.data_registro).days if r.data_registro else None
        saida.append({
            "data_registro": r.data_registro.isoformat() if r.data_registro else None,
            "dias": dias, "parada": bool(dias is not None and dias > DIAS_PARADA),
            "emissor": r.nome_emissor, "instrumento": r.instrumento,
            "valor": round((r.valor_total or 0) / 1e6, 1), "lider": r.lider or "—",
            "status": r.status, "publico": r.publico_alvo or "—",
        })
    saida.sort(key=lambda x: (x["data_registro"] or ""), reverse=True)
    return saida


TETO_NOVIDADES = 60


def novidades(db: Session, dias: int = 7, instrumentos=()) -> list[dict]:
    """O que mudou desde a última semana, pela trilha de status.

    Só existe porque guardamos a mudança: o arquivo da CVM é sobrescrito e
    não guarda de onde a oferta veio.
    """
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    q = (select(OfertaCVMStatus, OfertaCVM)
         .join(OfertaCVM, OfertaCVM.numero_requerimento == OfertaCVMStatus.numero_requerimento)
         .where(OfertaCVMStatus.visto_em >= desde)
         .order_by(OfertaCVMStatus.visto_em.desc())
         .limit(TETO_NOVIDADES))
    if instrumentos:
        q = q.where(OfertaCVM.instrumento.in_(instrumentos))
    saida = []
    for mudanca, oferta in db.execute(q).all():
        saida.append({
            "visto_em": (mudanca.visto_em.replace(tzinfo=timezone.utc)
                         if mudanca.visto_em.tzinfo is None else mudanca.visto_em).isoformat(),
            "emissor": oferta.nome_emissor, "instrumento": oferta.instrumento,
            "valor": round((oferta.valor_total or 0) / 1e6, 1),
            "de": mudanca.status_anterior or "—", "para": mudanca.status_novo,
            "nova": mudanca.status_anterior is None, "lider": oferta.lider or "—",
        })
    return saida


def painel(db: Session, *, janela: str = "12m", instrumentos=(), incentivada: str = "",
           top: int = TOP_PADRAO) -> dict:
    """Tudo que a aba mostra, numa consulta só.

    Uma chamada e não oito: a base tem ~4.400 linhas e cada bloco reusa as
    MESMAS linhas filtradas -- repetir a consulta por bloco seria pagar oito
    idas ao banco (a função roda na Vercel, em outra região) para montar a
    mesma tabela na memória.
    """
    hoje = _hoje(db)
    inicio, fim = janela_para_datas(janela, hoje)
    linhas = _linhas(db, inicio, fim, instrumentos, incentivada)
    ultima_coleta = db.scalar(select(func.max(OfertaCVM.atualizado_em)))
    return {
        "janela": janela,
        "inicio": inicio.isoformat() if inicio else None,
        "disponivel_ate": hoje.isoformat(),
        "ultima_coleta": ultima_coleta.isoformat() if ultima_coleta else None,
        "kpis": _kpis(linhas),
        "serie": _serie_mensal(linhas, inicio, fim),
        "incentivadas": _incentivadas(linhas),
        "lideres": _lideres(linhas, top),
        "maiores": _maiores(linhas, top),
        "estreantes": _estreantes(db, linhas, inicio, top),
        "pipeline": pipeline(db, instrumentos, hoje),
        "novidades": novidades(db, 7, instrumentos),
    }
