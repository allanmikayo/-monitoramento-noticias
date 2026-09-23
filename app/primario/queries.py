"""Consultas da aba "Mercado Primário" (23/09/2026).

A base responde "quanto, de quem, por quem e quando" -- nunca "a que taxa".
O arquivo da CVM não traz remuneração, prazo nem rating (ver o cabeçalho de
`coleta.py`), então nada aqui tenta inferir spread: para isso existe a aba
Spreads, que lê a Anbima.

DECISÕES QUE VALEM PARA TODOS OS BLOCOS
---------------------------------------
* DUAS DATAS, e a tela escolhe qual usar. REGISTRO é quando a oferta ficou
  apta a sair -- é a data de todas as ofertas, inclusive as que ainda não
  saíram, e é a proxy de "veio a mercado" (a mediana entre registro e
  encerramento é de 4 dias nas debêntures). ENCERRAMENTO é quando a oferta
  de fato se completou, e só existe para quem já encerrou. O fluxo aceita as
  duas; a quebra por investidor é SEMPRE por encerramento, porque é lá que
  a CVM publica quem ficou com o papel.
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
BASES_DATA = ("registro", "encerramento")

# Subscritores. A ordem é a da leitura: primeiro quem encarteirou,
# depois quem comprou de verdade.
GRUPOS = (
    ("qtd_bancos_consorcio", "Bancos do consórcio"),
    ("qtd_outras_if", "Outras instituições financeiras"),
    ("qtd_fundos", "Fundos"),
    ("qtd_pessoa_natural", "Pessoa física"),
    ("qtd_institucionais", "Previdência e seguradoras"),
    ("qtd_estrangeiro", "Estrangeiro"),
    ("qtd_outros", "Outros"),
)
# O que conta como "ficou no balanço de banco" no KPI de encarteiramento.
GRUPOS_BANCO = ("qtd_bancos_consorcio", "qtd_outras_if")

# Uma oferta aberta há muito tempo quase sempre é entulho: registro que não
# virou emissão e vai caducar. A tela marca em vez de esconder -- sumir com
# a linha seria decidir pelo analista.
DIAS_PARADA = 90
TOP_PADRAO = 15


def _coluna_data(base: str):
    return OfertaCVM.data_encerramento if base == "encerramento" else OfertaCVM.data_registro


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
    OfertaCVM.bookbuilding, OfertaCVM.n_investidores, OfertaCVM.devedor_curto,
    OfertaCVM.qtd_bancos_consorcio, OfertaCVM.qtd_outras_if, OfertaCVM.qtd_fundos,
    OfertaCVM.qtd_pessoa_natural, OfertaCVM.qtd_institucionais,
    OfertaCVM.qtd_estrangeiro, OfertaCVM.qtd_outros,
)


def _linhas(db: Session, inicio: date | None, fim: date, instrumentos, incentivada: str,
            base: str = "registro"):
    col = _coluna_data(base)
    q = select(*_COLUNAS).where(
        col.is_not(None), col <= fim, OfertaCVM.status.not_in(STATUS_MORTO),
    )
    if inicio:
        q = q.where(col >= inicio)
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


def _distribuicao(linhas) -> tuple[dict, float]:
    """Volume por grupo de investidor, só nas ofertas JÁ ENCERRADAS.

    A CVM publica a quantidade por tipo de investidor, não o valor. Em vez
    de multiplicar quantidade por preço unitário (que varia por série e
    estoura em oferta com PU atípico), usamos a PARTICIPAÇÃO de cada grupo
    dentro da própria oferta e aplicamos sobre o valor dela. Assim a conta
    não depende da unidade que a CVM usou naquela linha.

    Devolve também o volume coberto -- a tela precisa dizer sobre quanto do
    mercado a leitura vale (na prática, ~99% do volume encerrado).
    """
    por_grupo = {campo: 0.0 for campo, _ in GRUPOS}
    coberto = 0.0
    for r in linhas:
        valor = r.valor_total or 0
        soma = sum(getattr(r, campo) or 0 for campo, _ in GRUPOS)
        if valor <= 0 or soma <= 0 or not r.data_encerramento:
            continue
        coberto += valor
        for campo, _ in GRUPOS:
            por_grupo[campo] += (getattr(r, campo) or 0) / soma * valor
    return por_grupo, coberto


def _kpis(linhas) -> dict:
    valores = [r.valor_total for r in linhas if r.valor_total]
    total = sum(valores)
    firme = sum(r.valor_total or 0 for r in linhas
                if (r.regime_distribuicao or "").startswith("Garantia Firme"))
    livro = sum(r.valor_total or 0 for r in linhas
                if (r.bookbuilding or "").strip().upper() == "S")
    por_grupo, coberto = _distribuicao(linhas)
    banco = sum(por_grupo[c] for c in GRUPOS_BANCO)
    return {
        "volume": _bi(total),
        "ofertas": len(linhas),
        "ticket_mediano": round(_mediana(valores) / 1e6, 1),   # R$ milhões
        "garantia_firme_pct": round(firme / total * 100, 1) if total else 0.0,
        "bookbuilding_pct": round(livro / total * 100, 1) if total else 0.0,
        # % do volume JÁ ENCERRADO que ficou com instituição financeira.
        "encarteirado_pct": round(banco / coberto * 100, 1) if coberto else 0.0,
        "pessoa_fisica_pct": round(por_grupo["qtd_pessoa_natural"] / coberto * 100, 1)
        if coberto else 0.0,
        "volume_com_quebra": _bi(coberto),
    }


def _serie_mensal(linhas, inicio: date | None, fim: date, base: str = "registro") -> list[dict]:
    """Volume por mês e instrumento, com os meses vazios preenchidos.

    Mês sem nenhuma oferta existe (janeiro é sempre fraco) e precisa aparecer
    como zero: uma barra ausente no meio da série faz o eixo mentir.
    """
    campo = "data_encerramento" if base == "encerramento" else "data_registro"
    por_mes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in linhas:
        por_mes[getattr(r, campo).strftime("%Y-%m")][r.instrumento] += (r.valor_total or 0)
    if not inicio:
        datas = [getattr(r, campo) for r in linhas]
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


def _incentivadas(linhas, base: str = "registro") -> list[dict]:
    """Debêntures por trimestre: incentivada, não incentivada, não informado.

    É a disputa direta com CRI e CRA pelo mesmo bolso isento -- e o pedaço
    "não informado" fica à vista porque muda a leitura do share.
    """
    campo = "data_encerramento" if base == "encerramento" else "data_registro"
    tri: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in linhas:
        if r.instrumento != "DEB":
            continue
        d = getattr(r, campo)
        t = f"{d.year}-T{(d.month - 1) // 3 + 1}"
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


def _distribuicao_trimestral(linhas) -> list[dict]:
    """Ofertas por subscritor, trimestre a trimestre, em % do volume.

    Responde a pergunta que interessa ao analista de crédito: o banco está
    ficando com o papel ou está conseguindo distribuir? Trimestre e não mês
    porque uma única oferta grande distorce um mês inteiro.
    """
    por_tri: dict[str, list] = collections.defaultdict(list)
    for r in linhas:
        if r.data_encerramento:
            t = f"{r.data_encerramento.year}-T{(r.data_encerramento.month - 1) // 3 + 1}"
            por_tri[t].append(r)
    saida = []
    for t in sorted(por_tri):
        grupos, coberto = _distribuicao(por_tri[t])
        if not coberto:
            continue
        linha = {"trimestre": t, "volume": _bi(coberto)}
        for campo, rotulo in GRUPOS:
            linha[campo] = round(grupos[campo] / coberto * 100, 1)
        linha["encarteirado"] = round(
            sum(grupos[c] for c in GRUPOS_BANCO) / coberto * 100, 1)
        saida.append(linha)
    return saida


def _quem_deve(r) -> str:
    """Quem realmente tomou o dinheiro.

    Em CRI e CRA o emissor é a securitizadora -- somar por emissor colocaria
    Opea e Virgo no topo do ranking de captação, o que não diz nada de
    crédito. Quando a CVM identifica o devedor num formato aproveitável,
    é ele que conta; senão, volta para o emissor.
    """
    return r.devedor_curto or r.nome_emissor


def _captadores(linhas, top: int) -> list[dict]:
    """Quem mais captou na janela, com quantas vezes voltou ao mercado.

    Emissor que acessa três, quatro vezes no ano é o que muda a leitura de
    crédito: ou está rolando dívida, ou está com um programa de investimento
    pesado. Volume sozinho não mostra isso; volume com contagem, sim.
    """
    vol, n, ultima, instrumentos = (collections.Counter(), collections.Counter(),
                                    {}, collections.defaultdict(set))
    for r in linhas:
        nome = _quem_deve(r)
        vol[nome] += (r.valor_total or 0)
        n[nome] += 1
        data = r.data_encerramento or r.data_registro
        if data and (nome not in ultima or data > ultima[nome]):
            ultima[nome] = data
        instrumentos[nome].add(r.instrumento)
    return [{"nome": e, "volume": round(v / 1e6, 1), "ofertas": n[e],
             "instrumentos": sorted(instrumentos[e]),
             "ultima": ultima[e].isoformat() if ultima.get(e) else None}
            for e, v in vol.most_common(top)]


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
        "data": (r.data_registro or r.data_encerramento).isoformat(),
        "encerramento": r.data_encerramento.isoformat() if r.data_encerramento else None,
        "emissor": r.nome_emissor, "devedor": r.devedor_curto,
        "instrumento": r.instrumento, "valor": round((r.valor_total or 0) / 1e6, 1),
        "lider": r.lider or "—", "incentivada": (r.incentivado or "").strip(),
        "status": r.status, "investidores": r.n_investidores,
    } for r in ordenadas]


def pipeline(db: Session, instrumentos=(), hoje: date | None = None) -> list[dict]:
    """Ofertas com registro concedido e ainda não encerradas -- o que está
    registrado e ainda pode sair."""
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
            "emissor": r.nome_emissor, "devedor": r.devedor_curto,
            "instrumento": r.instrumento,
            "valor": round((r.valor_total or 0) / 1e6, 1), "lider": r.lider or "—",
            "status": r.status, "publico": r.publico_alvo or "—",
        })
    saida.sort(key=lambda x: (x["data_registro"] or ""), reverse=True)
    return saida


def resumo_pipeline(fila: list[dict]) -> dict:
    """Tamanho da fila por instrumento, separando o que ainda está de pé.

    Oferta parada há mais de 90 dias quase sempre é registro que não virou
    emissão: entra no total, mas contada à parte -- somar tudo num número só
    faria a fila parecer maior do que é."""
    vivas = [o for o in fila if not o["parada"]]
    por_instrumento = collections.Counter()
    for o in vivas:
        por_instrumento[o["instrumento"]] += o["valor"]
    return {
        "ofertas": len(fila), "vivas": len(vivas),
        "paradas": len(fila) - len(vivas),
        "volume_vivas": round(sum(o["valor"] for o in vivas) / 1000, 2),   # R$ bi
        "volume_total": round(sum(o["valor"] for o in fila) / 1000, 2),
        "por_instrumento": [{"instrumento": i, "volume": round(v / 1000, 2)}
                            for i, v in por_instrumento.most_common()],
    }


TETO_NOVIDADES = 60


def movimentos(db: Session, dias: int = 30, instrumentos=()) -> list[dict]:
    """O que mudou de status na janela, pela trilha própria.

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
            "emissor": oferta.nome_emissor, "devedor": oferta.devedor_curto,
            "instrumento": oferta.instrumento,
            "valor": round((oferta.valor_total or 0) / 1e6, 1),
            "de": mudanca.status_anterior or "—", "para": mudanca.status_novo,
            "nova": mudanca.status_anterior is None, "lider": oferta.lider or "—",
        })
    return saida


def painel(db: Session, *, janela: str = "12m", instrumentos=(), incentivada: str = "",
           base: str = "registro", top: int = TOP_PADRAO) -> dict:
    """Tudo que a aba mostra, numa consulta só.

    Uma chamada e não oito: a base tem ~4.400 linhas e cada bloco reusa as
    MESMAS linhas filtradas -- repetir a consulta por bloco seria pagar oito
    idas ao banco (a função roda na Vercel, em outra região) para montar a
    mesma tabela na memória.
    """
    hoje = _hoje(db)
    inicio, fim = janela_para_datas(janela, hoje)
    linhas = _linhas(db, inicio, fim, instrumentos, incentivada, base)
    fila = pipeline(db, instrumentos, hoje)
    ultima_coleta = db.scalar(select(func.max(OfertaCVM.atualizado_em)))
    return {
        "janela": janela,
        "base": base,
        "inicio": inicio.isoformat() if inicio else None,
        "disponivel_ate": hoje.isoformat(),
        "ultima_coleta": ultima_coleta.isoformat() if ultima_coleta else None,
        "kpis": _kpis(linhas),
        "serie": _serie_mensal(linhas, inicio, fim, base),
        "incentivadas": _incentivadas(linhas, base),
        "distribuicao": _distribuicao_trimestral(linhas),
        "lideres": _lideres(linhas, 10),
        "maiores": _maiores(linhas, top),
        "captadores": _captadores(linhas, top),
        "pipeline": fila,
        "resumo_pipeline": resumo_pipeline(fila),
        "movimentos": movimentos(db, 30, instrumentos),
        # Na primeira coleta a trilha está vazia de propósito (carga inicial
        # não é novidade) -- a tela precisa dizer isso em vez de sugerir que
        # o mercado ficou um mês parado.
        "trilha_vazia": not db.scalar(select(func.count(OfertaCVMStatus.id))),
    }
