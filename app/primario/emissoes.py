"""A que taxa saiu cada debênture da oferta (26/10/2026).

Usa a ligação oferta -> tickers de `casamento.py` e as condições da emissão
do arquivo de características. Por série:

* REMUNERAÇÃO: a mesma formatação da aba Emissor (`formatar_taxa_emissao`).
* PRAZO: vencimento menos data de emissão, em anos.
* SPREAD NA EMISSÃO, em bps:
    - CDI + x%      -> x × 100 (já é spread sobre o DI);
    - IPCA + x%     -> contra a ETTJ IPCA da ANBIMA (curva real) do dia da
                       emissão, no vértice do PRAZO do papel:
                       ((1 + x) / (1 + real) − 1) × 10.000. É o prazo, não a
                       duration (que o arquivo não traz) -- numa curva real
                       quase plana a diferença é pequena, e a tela diz qual
                       foi a régua;
    - % do CDI, prefixado e o resto -> sem spread (não há régua comparável).
* SPREAD HOJE: o último spread ANBIMA do ticker, quando ele é precificado.
  Emitido a X, negociando a Y: a diferença é o quanto o papel abriu ou
  fechou desde a emissão.
"""
from __future__ import annotations

import bisect
import collections
import json
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CurvaJuros, DebentureCaracteristica, DebentureSpread, OfertaCVMAtivo
from ..spreads.queries import formatar_taxa_emissao

TOLERANCIA_CURVA = 10  # dias: curva de até 10 dias antes da emissão


def _indexador(c) -> str | None:
    idx = (c.indice_emissao or "").upper()
    if "IPCA" in idx:
        return "IPCA+"
    if idx in ("DI", "CDI"):
        return "CDI+" if (c.taxa_emissao or 0) > 0 and c.percentual_emissao in (None, 0, 100) else "%CDI"
    return idx or None


def _interpolar(vertices: list, du: float) -> float | None:
    pts = [(v[0], v[2]) for v in vertices if v[2] is not None]
    if not pts:
        return None
    pts.sort()
    if du <= pts[0][0]:
        return pts[0][1]
    if du >= pts[-1][0]:
        return pts[-1][1]
    for (d0, r0), (d1, r1) in zip(pts, pts[1:]):
        if d0 <= du <= d1:
            return r0 + (r1 - r0) * (du - d0) / (d1 - d0)
    return None


class _Curvas:
    """Curva real do dia útil mais próximo (para trás) de cada data."""

    def __init__(self, db: Session):
        self.db = db
        self.datas = list(db.scalars(select(CurvaJuros.data).order_by(CurvaJuros.data)))
        self.cache: dict[date, list] = {}

    def real(self, quando: date, du: float) -> float | None:
        i = bisect.bisect_right(self.datas, quando) - 1
        if i < 0 or (quando - self.datas[i]).days > TOLERANCIA_CURVA:
            return None
        d = self.datas[i]
        if d not in self.cache:
            linha = self.db.get(CurvaJuros, d)
            self.cache[d] = json.loads(linha.vertices or "[]") if linha else []
        return _interpolar(self.cache[d], du)


def series_por_oferta(db: Session, requerimentos: list[str]) -> dict[str, dict]:
    """numero_requerimento -> {confianca, series: [...]} (só as casadas)."""
    if not requerimentos:
        return {}
    ligs = []
    for k in range(0, len(requerimentos), 800):
        ligs += db.execute(select(OfertaCVMAtivo).where(
            OfertaCVMAtivo.numero_requerimento.in_(requerimentos[k:k + 800]))).scalars().all()
    codigos = sorted({lg.codigo for lg in ligs})
    caracs, atuais = {}, {}
    for k in range(0, len(codigos), 800):
        lote = codigos[k:k + 800]
        for c in db.scalars(select(DebentureCaracteristica).where(DebentureCaracteristica.codigo.in_(lote))):
            caracs[c.codigo] = c
        ult = (select(DebentureSpread.codigo, func.max(DebentureSpread.data).label("d"))
               .where(DebentureSpread.codigo.in_(lote), DebentureSpread.spread.isnot(None))
               .group_by(DebentureSpread.codigo).subquery())
        for codigo, spread, d in db.execute(
                select(DebentureSpread.codigo, DebentureSpread.spread, DebentureSpread.data)
                .join(ult, (DebentureSpread.codigo == ult.c.codigo) & (DebentureSpread.data == ult.c.d))):
            atuais[codigo] = (spread, d)

    try:
        curvas = _Curvas(db)
    except Exception:  # curva ainda não capturada
        db.rollback()
        curvas = None

    saida: dict[str, dict] = collections.defaultdict(lambda: {"confianca": None, "series": []})
    for lg in ligs:
        c = caracs.get(lg.codigo)
        if c is None:
            continue
        idx = _indexador(c)
        prazo = ((c.data_vencimento - c.data_emissao).days / 365.25
                 if c.data_vencimento and c.data_emissao else None)
        spread = None
        if idx == "CDI+":
            spread = round(c.taxa_emissao * 100, 1)
        elif idx == "IPCA+" and c.taxa_emissao and prazo and c.data_emissao and curvas:
            real = curvas.real(c.data_emissao, prazo * 252)
            if real is not None:
                spread = round(((1 + c.taxa_emissao / 100) / (1 + real / 100) - 1) * 10000, 1)
        atual = atuais.get(lg.codigo)
        item = saida[lg.numero_requerimento]
        item["confianca"] = lg.confianca
        item["series"].append({
            "codigo": lg.codigo, "serie": c.serie, "indexador": idx,
            "remuneracao": formatar_taxa_emissao(c.indice_emissao, c.percentual_emissao, c.taxa_emissao),
            "prazo_anos": round(prazo, 1) if prazo else None,
            "volume": (c.quantidade_emitida or 0) * (c.valor_nominal_emissao or 0) or None,
            "spread_emissao": spread,
            "spread_atual": round(atual[0], 1) if atual else None,
            "data_spread_atual": atual[1].isoformat() if atual else None,
            "data_emissao": c.data_emissao.isoformat() if c.data_emissao else None,
        })
    for item in saida.values():
        item["series"].sort(key=lambda s: -(s["volume"] or 0))
    return dict(saida)
