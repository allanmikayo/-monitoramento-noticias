"""Curva de juros da ANBIMA (ETTJ) — real (IPCA), prefixada e inflação
implícita, por vértice (26/10/2026, página inicial do Hub).

FONTE. A página pública https://www.anbima.com.br/informacoes/est-termo/CZ.asp
só mostra o dia corrente — não dá histórico. A API oficial da ANBIMA (a mesma
dos spreads, `anbima_api.py`) tem o endpoint
`/feed/precos-indices/v1/titulos-publicos/curvas-juros?data=AAAA-MM-DD`, que
devolve os parâmetros do modelo E a tabela "ETTJ / Inflação Implícita" pronta
(campos `vertice_du`, `taxa_prefixadas`, `taxa_ipca`, `taxa_implicita`, em %
a.a. base 252), com histórico. Por isso não reimplementamos o modelo de
Svensson: guardamos a tabela que a própria ANBIMA publica.

A documentação lista os campos mas não mostra como eles se aninham no JSON.
`extrair_vertices` não depende disso: percorre a resposta inteira e junta
todo objeto que traga `vertice_du` e pelo menos uma das três taxas. A
tabela da Circular 3.361 (só `vertice_du` + `taxa`) fica de fora.

Uma linha por dia em `curva_juros`; os vértices vão como JSON (~30 por dia,
não vale uma tabela de vértices).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CurvaJuros

logger = logging.getLogger(__name__)

ENDPOINT = "/feed/precos-indices/v1/titulos-publicos/curvas-juros"
CAMPOS = {"taxa_prefixadas": "pre", "taxa_ipca": "ipca", "taxa_implicita": "implicita"}


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _data(v) -> date | None:
    if not v:
        return None
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def extrair_vertices(resposta) -> tuple[date | None, list[dict]]:
    """(data de referência, [{du, pre, ipca, implicita}] ordenado por du)."""
    por_du: dict[int, dict] = {}
    datas: list[date] = []

    def visitar(no):
        if isinstance(no, list):
            for x in no:
                visitar(x)
            return
        if not isinstance(no, dict):
            return
        if "data_referencia" in no:
            d = _data(no["data_referencia"])
            if d:
                datas.append(d)
        if "vertice_du" in no and any(k in no for k in CAMPOS):
            du = int(_num(no["vertice_du"]) or 0)
            if du > 0:
                alvo = por_du.setdefault(du, {"du": du, "pre": None, "ipca": None, "implicita": None})
                for campo, chave in CAMPOS.items():
                    valor = _num(no.get(campo))
                    if valor is not None:
                        alvo[chave] = valor
        for v in no.values():
            if isinstance(v, (list, dict)):
                visitar(v)

    visitar(resposta)
    vertices = [v for _, v in sorted(por_du.items())]
    return (max(datas) if datas else None), vertices


def buscar(dt: date | None = None):
    """Resposta crua da API (dt=None: a data mais recente publicada)."""
    from . import anbima_api  # import tardio: o site não precisa de requests/oauth
    client_id, _ = anbima_api._client_credentials()
    import requests
    token = anbima_api.get_access_token()
    params = {"data": dt.isoformat()} if dt else {}
    headers = {"client_id": client_id, "access_token": token}
    resp = requests.get(f"{anbima_api.BASE_URL}{ENDPOINT}", headers=headers, params=params, timeout=60)
    if resp.status_code == 401:
        headers["access_token"] = anbima_api.get_access_token(force_refresh=True)
        resp = requests.get(f"{anbima_api.BASE_URL}{ENDPOINT}", headers=headers, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def gravar(db: Session, data_ref: date, vertices: list[dict]) -> bool:
    """Grava/substitui o dia. Devolve False se não havia vértice nenhum."""
    if not vertices:
        return False
    linha = db.get(CurvaJuros, data_ref)
    if linha is None:
        linha = CurvaJuros(data=data_ref)
        db.add(linha)
    linha.vertices = json.dumps([[v["du"], v["pre"], v["ipca"], v["implicita"]] for v in vertices])
    linha.atualizado_em = datetime.now(timezone.utc)
    return True


def _ler(linha: CurvaJuros) -> list[dict]:
    return [{"du": du, "anos": round(du / 252, 2), "pre": pre, "ipca": ipca, "implicita": imp}
            for du, pre, ipca, imp in json.loads(linha.vertices or "[]")]


# Posições, em dias com curva publicada, das linhas que a página inicial
# compara: hoje, 5 e 21 dias úteis atrás (mesma convenção WoW / MoM da aba
# Spreads).
COMPARACOES = (("hoje", 0), ("d5", 5), ("d21", 21))


def curvas_comparadas(db: Session) -> dict:
    datas = list(db.scalars(select(CurvaJuros.data).order_by(CurvaJuros.data.desc()).limit(22)))
    if not datas:
        return {"curvas": []}
    saida = []
    for chave, n in COMPARACOES:
        if n >= len(datas):
            continue
        linha = db.get(CurvaJuros, datas[n])
        saida.append({"chave": chave, "data": datas[n].isoformat(), "vertices": _ler(linha)})
    return {"curvas": saida}
