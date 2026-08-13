"""Consultas analíticas da aba Securitizados (CRA/CRI).

O Allan achou a análise de CRA/CRI do dashboard atual "pobre" e pediu
sugestões (04/08/2026). Este módulo é a proposta — e ela se apoia em três
coisas que o dado de securitizado **tem e o de debênture não**, mais uma
que só existe agora que os dois produtos moram no mesmo banco.

O QUE MUDA EM RELAÇÃO À ABA ATUAL
---------------------------------
1. **Mediana ao lado da média, sempre.** Sem estoque não há ponderação
   (confirmado pelo Allan), e média simples num universo de ~490 papéis
   com spread indo de -466 a +2.242 bps é dominada por outlier. A mediana
   é o número que descreve o mercado; a média mostra o quanto a cauda
   pesa. Divergência grande entre as duas JÁ É informação.

2. **Bid-ask (`taxa_compra` - `taxa_venda`).** Só existe em CRI/CRA. É a
   medida de liquidez mais direta que temos — e substitui bem o volume
   negociado, que aqui não existe. Papel com bid-ask de 5 bps e outro de
   80 bps não merecem a mesma confiança num ranking de "maiores
   aberturas".

3. **% REUNE — confiança no preço.** Quanto da taxa indicativa veio de
   negócio real em vez de modelo. Estava sendo jogado fora. É o filtro
   que separa "spread abriu" de "a marcação mudou".

4. **Valor relativo cruzado debênture × securitizado.** 51 emissores
   emitem OS DOIS produtos — Minerva (1 debênture, 22 CRAs), Marfrig
   (2 e 21), BRF (2 e 16), Raízen (4 e 10). Mesmo risco de crédito, dois
   instrumentos, spreads que podem divergir bastante por questão
   tributária e de demanda. Isso não existia em lugar nenhum antes de os
   dois produtos estarem no mesmo banco com o mesmo `issuer_id`.

REGRA QUE ATRAVESSA TUDO: nunca misturar indexador. IPCA+, CDI+ e %CDI
têm bases de comparação diferentes — a mesma taxa "100" significa
"CDI + 100 p.p." numa e "spread zero" na outra.
"""
from __future__ import annotations

import statistics
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Debenture,
    DebentureSpread,
    Issuer,
    Securitizado,
    SecuritizadoSpread,
)

INDEXADORES = ("IPCA+", "CDI+", "%CDI")
TIPOS = ("CRA", "CRI")


def ultima_data(db: Session) -> date | None:
    return db.scalar(select(func.max(SecuritizadoSpread.data)))


def _stats(valores: list[float]) -> dict:
    """Mediana, média e dispersão de uma amostra.

    Devolve as duas medidas de centro SEMPRE, junto com o gap entre elas.
    `gap_media_mediana` grande = distribuição assimétrica, a média está
    sendo puxada por poucos papéis; é o sinal de "olhe a cauda antes de
    concluir".
    """
    if not valores:
        return {"n": 0, "mediana": None, "media": None, "p25": None, "p75": None,
                "min": None, "max": None, "gap_media_mediana": None}
    ordenados = sorted(valores)
    mediana = statistics.median(ordenados)
    media = statistics.fmean(ordenados)
    n = len(ordenados)
    return {
        "n": n,
        "mediana": round(mediana, 1),
        "media": round(media, 1),
        "p25": round(ordenados[max(0, int(n * 0.25) - 1)], 1),
        "p75": round(ordenados[min(n - 1, int(n * 0.75))], 1),
        "min": round(ordenados[0], 1),
        "max": round(ordenados[-1], 1),
        "gap_media_mediana": round(media - mediana, 1),
    }


def _linhas(db: Session, dia: date, indexador: str | None = None, tipo: str | None = None):
    q = (
        select(SecuritizadoSpread, Securitizado)
        .join(Securitizado, Securitizado.codigo == SecuritizadoSpread.codigo)
        .where(SecuritizadoSpread.data == dia)
    )
    if indexador:
        q = q.where(Securitizado.indexador == indexador)
    if tipo:
        q = q.where(Securitizado.tipo_ativo == tipo)
    return db.execute(q).all()


# ---------------------------------------------------------------------------
# 1. Panorama: mediana E média, por classe
# ---------------------------------------------------------------------------

def panorama(db: Session, dia: date | None = None) -> dict:
    """Spread por indexador × tipo, com mediana e média lado a lado."""
    dia = dia or ultima_data(db)
    if dia is None:
        return {"data": None, "blocos": []}

    blocos = []
    for indexador in INDEXADORES:
        por_tipo = {}
        todos: list[float] = []
        for tipo in TIPOS:
            spreads = [
                sp.spread for sp, _ in _linhas(db, dia, indexador, tipo)
                if sp.spread is not None
            ]
            por_tipo[tipo] = _stats(spreads)
            todos.extend(spreads)
        blocos.append({
            "indexador": indexador,
            "total": _stats(todos),
            "por_tipo": por_tipo,
            # CRA e CRI no mesmo indexador: há prêmio de um sobre o outro?
            # Medido em 04/08/2026: em IPCA+ o CRI paga ~41 bps a mais que
            # o CRA; em %CDI o CRA paga ~309 bps a mais. Não é ruído — são
            # populações de risco diferentes (agro vs. imobiliário).
            "premio_cra_sobre_cri": (
                round(por_tipo["CRA"]["mediana"] - por_tipo["CRI"]["mediana"], 1)
                if por_tipo["CRA"]["mediana"] is not None
                and por_tipo["CRI"]["mediana"] is not None else None
            ),
        })
    return {"data": dia.isoformat(), "blocos": blocos}


# ---------------------------------------------------------------------------
# 2. Liquidez pelo bid-ask
# ---------------------------------------------------------------------------

def _bid_ask_bps(sp: SecuritizadoSpread) -> float | None:
    """Amplitude de cotação em bps.

    `taxa_compra - taxa_venda`, NESSA ORDEM. Em renda fixa cotada por
    taxa, comprar o papel significa exigir taxa MAIOR (preço menor), então
    `taxa_compra > taxa_venda` e a subtração inversa daria sempre
    negativo. Conferido no dado real: com `venda - compra` o resultado é
    negativo em todas as classes, o que denunciaria o erro.
    """
    if not sp.taxa_compra or not sp.taxa_venda:
        return None
    return (sp.taxa_compra - sp.taxa_venda) * 100


def liquidez(db: Session, dia: date | None = None, indexador: str | None = None) -> dict:
    """Amplitude de cotação e qualidade do preço.

    Substitui o "volume negociado" que existe em debênture (via B3) e não
    existe aqui. Bid-ask estreito + REUNE alto = preço em que dá pra
    confiar; o oposto = marcação teórica.
    """
    dia = dia or ultima_data(db)
    if dia is None:
        return {"data": None}

    linhas = _linhas(db, dia, indexador)
    amplitudes, reunes, papeis = [], [], []
    for sp, sec in linhas:
        ba = _bid_ask_bps(sp)
        if ba is not None:
            amplitudes.append(ba)
        if sp.pct_reune is not None:
            reunes.append(sp.pct_reune)
        papeis.append({
            "codigo": sec.codigo,
            "tipo": sec.tipo_ativo,
            "indexador": sec.indexador,
            "originador": sec.originador_credito,
            "spread": sp.spread,
            "bid_ask": round(ba, 1) if ba is not None else None,
            "desvio_padrao": sp.desvio_padrao,
            "pct_reune": sp.pct_reune,
        })

    com_ba = [p for p in papeis if p["bid_ask"] is not None]
    return {
        "data": dia.isoformat(),
        "bid_ask": _stats(amplitudes),
        "reune": _stats(reunes),
        # Os menos líquidos primeiro: é onde o spread indicativo merece
        # menos confiança e onde um "movimento" pode ser só marcação.
        "menos_liquidos": sorted(com_ba, key=lambda p: -p["bid_ask"])[:20],
        "mais_liquidos": sorted(com_ba, key=lambda p: p["bid_ask"])[:20],
    }


def confiabilidade(db: Session, dia: date | None = None, reune_minimo: float = 50.0) -> dict:
    """Divide a base entre preço observado e preço estimado.

    Serve de filtro para o resto da aba: um ranking de "maiores aberturas
    de spread" montado sobre papel de REUNE baixo mostra ruído de
    marcação, não movimento de mercado.
    """
    dia = dia or ultima_data(db)
    if dia is None:
        return {"data": None}
    observado, estimado, sem_info = [], [], []
    for sp, sec in _linhas(db, dia):
        alvo = sem_info if sp.pct_reune is None else (
            observado if sp.pct_reune >= reune_minimo else estimado
        )
        alvo.append(sp.spread)
    return {
        "data": dia.isoformat(),
        "reune_minimo": reune_minimo,
        "observado": _stats([s for s in observado if s is not None]),
        "estimado": _stats([s for s in estimado if s is not None]),
        "sem_info": len(sem_info),
    }


# ---------------------------------------------------------------------------
# 3. Concentração por originador
# ---------------------------------------------------------------------------

def por_originador(db: Session, dia: date | None = None, minimo_papeis: int = 2) -> list[dict]:
    """Originadores com mais de um papel, ordenados por dispersão interna.

    Dispersão alta entre papéis do MESMO originador é um sinal específico
    de securitização: como o risco de crédito é o mesmo, a diferença vem
    de estrutura (subordinação, garantia, prazo). Papel com spread muito
    acima dos irmãos costuma ser a tranche subordinada — ou um erro de
    classificação, e as duas hipóteses valem a olhada.
    """
    dia = dia or ultima_data(db)
    if dia is None:
        return []
    grupos: dict[str, list] = {}
    for sp, sec in _linhas(db, dia):
        if sp.spread is None or not sec.originador_credito:
            continue
        grupos.setdefault(sec.originador_credito, []).append((sp, sec))

    saida = []
    for nome, itens in grupos.items():
        if len(itens) < minimo_papeis:
            continue
        spreads = [sp.spread for sp, _ in itens]
        st = _stats(spreads)
        saida.append({
            "originador": nome,
            "papeis": len(itens),
            "indexadores": sorted({sec.indexador for _, sec in itens}),
            "spread": st,
            "amplitude": round(st["max"] - st["min"], 1),
        })
    return sorted(saida, key=lambda x: -x["amplitude"])


# ---------------------------------------------------------------------------
# 4. Valor relativo cruzado: debênture × securitizado do MESMO emissor
# ---------------------------------------------------------------------------

def valor_relativo_cruzado(db: Session, dia: date | None = None) -> list[dict]:
    """Compara o spread de debênture e de securitizado do mesmo emissor.

    A análise que só passou a ser possível com os dois produtos no mesmo
    banco ligados pelo mesmo `issuer_id`. São 51 emissores nessa situação
    (Minerva, Marfrig, BRF, Raízen, Vamos, Atacadão...).

    Mesmo risco de crédito, dois instrumentos. A diferença de spread vem
    de tributação (CRA/CRI é isento para pessoa física, debênture
    incentivada também, comum não), base de investidor e estrutura de
    garantia. Divergência grande é oportunidade de arbitragem de curva —
    ou sinal de que o CRA tem subordinação que a debênture não tem.

    **Só compara dentro do mesmo indexador.** Comparar o CDI+ de um com o
    IPCA+ do outro não significa nada.
    """
    dia = dia or ultima_data(db)
    if dia is None:
        return []

    # Debêntures do dia (a data de spread pode não ser exatamente a mesma
    # do securitizado; usa a última disponível até o dia).
    dia_deb = db.scalar(
        select(func.max(DebentureSpread.data)).where(DebentureSpread.data <= dia)
    )
    if dia_deb is None:
        return []

    debs = db.execute(
        select(DebentureSpread, Debenture)
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .where(DebentureSpread.data == dia_deb, Debenture.issuer_id.is_not(None))
    ).all()
    secs = db.execute(
        select(SecuritizadoSpread, Securitizado)
        .join(Securitizado, Securitizado.codigo == SecuritizadoSpread.codigo)
        .where(SecuritizadoSpread.data == dia, Securitizado.issuer_id.is_not(None))
    ).all()

    # `Debenture.indexador` vem como "IPCA +"/"CDI +"; securitizado usa
    # "IPCA+"/"CDI+". Normaliza pra poder casar.
    def _norm(idx: str | None) -> str:
        return (idx or "").replace(" ", "").upper()

    por_emissor: dict[int, dict] = {}
    for sp, d in debs:
        if sp.spread is None:
            continue
        e = por_emissor.setdefault(d.issuer_id, {"deb": {}, "sec": {}})
        e["deb"].setdefault(_norm(d.indexador), []).append(sp.spread)
    for sp, s in secs:
        if sp.spread is None:
            continue
        e = por_emissor.setdefault(s.issuer_id, {"deb": {}, "sec": {}})
        e["sec"].setdefault(_norm(s.indexador), []).append(sp.spread)

    saida = []
    for issuer_id, dados in por_emissor.items():
        comuns = set(dados["deb"]) & set(dados["sec"])
        if not comuns:
            continue
        issuer = db.get(Issuer, issuer_id)
        for indexador in sorted(comuns):
            md = statistics.median(dados["deb"][indexador])
            ms = statistics.median(dados["sec"][indexador])
            saida.append({
                "emissor": issuer.nome if issuer else str(issuer_id),
                "setor": issuer.setor if issuer else None,
                "indexador": indexador,
                "n_debentures": len(dados["deb"][indexador]),
                "n_securitizados": len(dados["sec"][indexador]),
                "spread_debenture": round(md, 1),
                "spread_securitizado": round(ms, 1),
                # Positivo = o securitizado paga MAIS que a debênture do
                # mesmo emissor.
                "diferenca": round(ms - md, 1),
            })
    return sorted(saida, key=lambda x: -abs(x["diferenca"]))


# ---------------------------------------------------------------------------
# 5. Movimentação no tempo (mediana, não média)
# ---------------------------------------------------------------------------

def evolucao(db: Session, indexador: str, tipo: str | None = None,
             desde: date | None = None) -> list[dict]:
    """Série temporal do spread por classe — mediana E média.

    Duas linhas de propósito. Quando elas se descolam, a média está sendo
    movida por poucos papéis, não pelo mercado; num gráfico só de média
    isso passa como movimento geral.
    """
    q = (
        select(SecuritizadoSpread.data, SecuritizadoSpread.spread)
        .join(Securitizado, Securitizado.codigo == SecuritizadoSpread.codigo)
        .where(Securitizado.indexador == indexador, SecuritizadoSpread.spread.is_not(None))
    )
    if tipo:
        q = q.where(Securitizado.tipo_ativo == tipo)
    if desde:
        q = q.where(SecuritizadoSpread.data >= desde)

    por_dia: dict[date, list[float]] = {}
    for dt, spread in db.execute(q).all():
        por_dia.setdefault(dt, []).append(spread)

    return [
        {"data": dt.isoformat(), **_stats(por_dia[dt])}
        for dt in sorted(por_dia)
    ]
