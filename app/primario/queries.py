"""Consultas da aba "Mercado Primário" (23/09/2026).

O arquivo da CVM responde "quanto, de quem, por quem e quando". Ele não traz
remuneração nem prazo (ver `coleta.py`); desde 26/10/2026 a taxa das
debêntures vem do casamento oferta -> ticker (`casamento.py`) e das
características da emissão (`emissoes.py`). Setor de quem deve vem de
`ofertas_cvm_classificacao` ("Não classificado" quando não casa). Oferta
SECUNDÁRIA fica fora de todo volume: não é dinheiro novo.

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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import OfertaCVM, OfertaCVMClassificacao, OfertaCVMStatus
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
        # Oferta SECUNDÁRIA é um detentor vendendo papel que já existe: não
        # é dinheiro novo para o emissor, então fica fora de todo volume.
        or_(OfertaCVM.tipo_oferta.is_(None), OfertaCVM.tipo_oferta != "SECUNDARIA"),
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
            "numero_requerimento": r.numero_requerimento,
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


NAO_CLASSIFICADO = "Não classificado"
LIMIAR_BALANCO = 50.0    # % da oferta com banco para "ficou no balanço"
TOP_EMISSORES = 25
TETO_EMISSOES = 80


def _janela_anterior(janela: str, inicio: date | None, fim: date) -> tuple[date | None, date | None]:
    """O mesmo intervalo um ano antes (24 meses: dois anos antes)."""
    if inicio is None:
        return None, None
    anos = 2 if janela == "24m" else 1

    def recua(d: date) -> date:
        try:
            return d.replace(year=d.year - anos)
        except ValueError:          # 29/02
            return d.replace(year=d.year - anos, day=28)
    return recua(inicio), recua(fim)


def _classificacao(db: Session) -> dict:
    try:
        return {c.numero_requerimento: c for c in db.scalars(select(OfertaCVMClassificacao))}
    except Exception:   # tabela ainda não criada pelo job de casamento
        db.rollback()
        return {}


def _pct_banco(r) -> float | None:
    soma = sum(getattr(r, c) or 0 for c, _ in GRUPOS)
    if not soma or not r.data_encerramento:
        return None
    return round(sum(getattr(r, c) or 0 for c in GRUPOS_BANCO) / soma * 100, 1)


class _Quem:
    """Quem deve: o devedor que o casamento achou, senão o da CVM."""

    def __init__(self, cls: dict):
        self.cls = cls

    def nome(self, r) -> str:
        c = self.cls.get(r.numero_requerimento)
        return (c.devedor if c and c.devedor else None) or _quem_deve(r) or "—"

    def chave(self, r) -> str:
        from .casamento import nome_normalizado
        return nome_normalizado(self.nome(r)) or r.numero_requerimento

    def setor(self, r) -> str:
        c = self.cls.get(r.numero_requerimento)
        return (c.setor if c and c.setor else None) or NAO_CLASSIFICADO

    def grupo(self, r) -> str | None:
        c = self.cls.get(r.numero_requerimento)
        return c.grupo if c and c.grupo and c.grupo != NAO_CLASSIFICADO else None


def _dentro(r, campo: str, inicio: date | None, fim: date | None) -> bool:
    d = getattr(r, campo)
    return d is not None and (inicio is None or d >= inicio) and (fim is None or d <= fim)


def _kpis(linhas, anteriores, estreantes: set | None, quem: _Quem) -> dict:
    total = sum(r.valor_total or 0 for r in linhas)
    total_ant = sum(r.valor_total or 0 for r in anteriores) if anteriores is not None else None
    valores = [r.valor_total for r in linhas if r.valor_total]
    por_grupo, coberto = _distribuicao(linhas)
    banco = sum(por_grupo[c] for c in GRUPOS_BANCO)
    emissores = {quem.chave(r) for r in linhas}
    return {
        "volume": _bi(total),
        "volume_anterior": _bi(total_ant) if total_ant is not None else None,
        "variacao": (round(total / total_ant - 1, 4) if total_ant else None),
        "ofertas": len(linhas),
        "ticket_mediano": round(_mediana(valores) / 1e6, 1),
        "emissores": len(emissores),
        "estreantes": len(emissores & estreantes) if estreantes is not None else None,
        "encarteirado_pct": round(banco / coberto * 100, 1) if coberto else 0.0,
        "fundos_pf_pct": round((por_grupo["qtd_fundos"] + por_grupo["qtd_pessoa_natural"])
                               / coberto * 100, 1) if coberto else 0.0,
        "pessoa_fisica_pct": round(por_grupo["qtd_pessoa_natural"] / coberto * 100, 1)
        if coberto else 0.0,
        "volume_com_quebra": _bi(coberto),
    }


def _setores(linhas, anteriores, quem: _Quem) -> list[dict]:
    atual, ant = collections.Counter(), collections.Counter()
    for r in linhas:
        atual[quem.setor(r)] += r.valor_total or 0
    for r in anteriores or []:
        ant[quem.setor(r)] += r.valor_total or 0
    total = sum(atual.values())
    saida = [{"setor": s, "volume": _bi(v), "volume_anterior": _bi(ant[s]) if anteriores is not None else None,
              "share": round(v / total * 100, 1) if total else 0.0}
             for s, v in atual.most_common()]
    # "Não classificado" vai para o fim: é o tamanho do que não sabemos.
    saida.sort(key=lambda x: (x["setor"] == NAO_CLASSIFICADO, -x["volume"]))
    return saida


def _emissores(linhas, estreantes: set | None, quem: _Quem, campo: str, top: int) -> list[dict]:
    acc: dict[str, dict] = {}
    for r in linhas:
        k = quem.chave(r)
        e = acc.setdefault(k, {"nome": quem.nome(r), "grupo": quem.grupo(r), "setor": quem.setor(r),
                               "volume": 0.0, "ofertas": 0, "instrumentos": set(),
                               "_banco": 0.0, "_coberto": 0.0, "ultima": None})
        v = r.valor_total or 0
        e["volume"] += v
        e["ofertas"] += 1
        e["instrumentos"].add(r.instrumento)
        pb = _pct_banco(r)
        if pb is not None and v:
            e["_banco"] += pb * v
            e["_coberto"] += v
        d = getattr(r, campo)
        if d and (e["ultima"] is None or d > e["ultima"]):
            e["ultima"] = d
        if e["setor"] == NAO_CLASSIFICADO and quem.setor(r) != NAO_CLASSIFICADO:
            e["setor"] = quem.setor(r)
        if not e["grupo"]:
            e["grupo"] = quem.grupo(r)
    saida = []
    for k, e in sorted(acc.items(), key=lambda kv: -kv[1]["volume"])[:top]:
        saida.append({
            "nome": e["nome"], "grupo": e["grupo"], "setor": e["setor"],
            "volume": round(e["volume"] / 1e6, 1), "ofertas": e["ofertas"],
            "instrumentos": sorted(e["instrumentos"]),
            "pct_banco": round(e["_banco"] / e["_coberto"], 1) if e["_coberto"] else None,
            "estreante": bool(estreantes is not None and k in estreantes),
            "ultima": e["ultima"].isoformat() if e["ultima"] else None,
        })
    return saida


def _oferta(r, quem: _Quem, campo: str) -> dict:
    d = getattr(r, campo) or r.data_registro
    return {
        "numero_requerimento": r.numero_requerimento,
        "data": d.isoformat() if d else None,
        "devedor": quem.nome(r), "emissor": r.nome_emissor, "setor": quem.setor(r),
        "instrumento": r.instrumento, "valor": round((r.valor_total or 0) / 1e6, 1),
        "lider": r.lider or "—", "pct_banco": _pct_banco(r), "status": r.status,
        "investidores": r.n_investidores,
    }


def _emissoes_debentures(db: Session, linhas, quem: _Quem, campo: str) -> list[dict]:
    from .emissoes import series_por_oferta

    debs = sorted((r for r in linhas if r.instrumento == "DEB"),
                  key=lambda r: getattr(r, campo) or date.min, reverse=True)[:TETO_EMISSOES]
    try:
        series = series_por_oferta(db, [r.numero_requerimento for r in debs])
    except Exception:   # tabelas do casamento ainda não existem
        db.rollback()
        series = {}
    saida = []
    for r in debs:
        o = _oferta(r, quem, campo)
        s = series.get(r.numero_requerimento) or {}
        o["confianca"] = s.get("confianca")
        o["series"] = s.get("series", [])
        saida.append(o)
    return saida


def _balanco(linhas, quem: _Quem, campo: str, top: int) -> list[dict]:
    """Ofertas em que banco (consórcio + outras IF) ficou com metade ou
    mais: não houve demanda e o papel tende a voltar ao secundário."""
    sel = [r for r in linhas if (_pct_banco(r) or 0) >= LIMIAR_BALANCO]
    sel.sort(key=lambda r: -(r.valor_total or 0))
    return [_oferta(r, quem, campo) for r in sel[:top]]


def painel(db: Session, *, janela: str = "12m", instrumentos=(), incentivada: str = "",
           base: str = "registro", top: int = TOP_PADRAO) -> dict:
    """Tudo que a aba mostra, numa consulta só (26/10/2026: reorganizada
    para crédito -- quem emitiu, quanto, de que setor, a que taxa e quem
    ficou com o papel)."""
    hoje = _hoje(db)
    inicio, fim = janela_para_datas(janela, hoje)
    campo = "data_encerramento" if base == "encerramento" else "data_registro"
    # Uma consulta para tudo: a janela, a do ano anterior e o histórico que
    # diz quem é estreante saem das mesmas linhas.
    todas = _linhas(db, None, fim, instrumentos, incentivada, base)
    cls = _classificacao(db)
    quem = _Quem(cls)
    linhas = [r for r in todas if _dentro(r, campo, inicio, fim)]
    ini_ant, fim_ant = _janela_anterior(janela, inicio, fim)
    anteriores = [r for r in todas if _dentro(r, campo, ini_ant, fim_ant)] if ini_ant else None

    # Estreante = primeira oferta (de qualquer instrumento) desde jan/2023,
    # que é onde a base da CVM começa. Na janela "tudo" a ideia não existe.
    estreantes = None
    if inicio is not None:
        historico = _linhas(db, None, fim, (), "", base) if (instrumentos or incentivada) else todas
        primeira: dict[str, date] = {}
        for r in historico:
            d, k = getattr(r, campo), quem.chave(r)
            if d and (k not in primeira or d < primeira[k]):
                primeira[k] = d
        estreantes = {k for k, d in primeira.items() if d >= inicio}

    fila = pipeline(db, instrumentos, hoje)
    for o in fila:
        c = cls.get(o["numero_requerimento"])
        o["setor"] = (c.setor if c and c.setor else None) or NAO_CLASSIFICADO
        if c and c.devedor:
            o["devedor"] = c.devedor
    ultima_coleta = db.scalar(select(func.max(OfertaCVM.atualizado_em)))
    return {
        "janela": janela,
        "base": base,
        "inicio": inicio.isoformat() if inicio else None,
        "inicio_anterior": ini_ant.isoformat() if ini_ant else None,
        "fim_anterior": fim_ant.isoformat() if fim_ant else None,
        "disponivel_ate": hoje.isoformat(),
        "ultima_coleta": ultima_coleta.isoformat() if ultima_coleta else None,
        "classificacao_disponivel": bool(cls),
        "kpis": _kpis(linhas, anteriores, estreantes, quem),
        "serie": _serie_mensal(linhas, inicio, fim, base),
        "setores": _setores(linhas, anteriores, quem),
        "emissores": _emissores(linhas, estreantes, quem, campo, TOP_EMISSORES),
        "emissoes": _emissoes_debentures(db, linhas, quem, campo),
        "balanco": _balanco(linhas, quem, campo, top),
        "distribuicao": _distribuicao_trimestral(linhas),
        "pipeline": fila,
        "resumo_pipeline": resumo_pipeline(fila),
    }
