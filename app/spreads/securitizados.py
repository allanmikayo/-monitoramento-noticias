"""Captura e cálculo de spread de CRI/CRA (Anbima).

Endpoint: `/feed/precos-indices/v1/cri-cra/mercado-secundario`
(divulgação diária a partir das 20h de Brasília). Mesma credencial OAuth2
das debêntures — ver `anbima_api.py`.

Espelha `fetch.py`, mas as regras de negócio são diferentes o bastante pra
justificar módulo próprio: existe `%CDI`, não existe estoque, e o risco é
do originador e não do emissor (ver o cabeçalho de `models.Securitizado`).

FÓRMULAS DE SPREAD — derivadas do dado do Allan, não inventadas
--------------------------------------------------------------
Conferidas linha a linha contra as 95.413 observações da aba CRA/CRI do
Dashboard_Snapshot (04/08/2026):

- **IPCA+** (`tipo_remuneracao = "IPCA"`) — spread COMPOSTO contra a NTN-B
  de referência do papel:

      ((1 + taxa/100) / (1 + ntnb/100) - 1) * 10000

  Confirmado: taxa 9,4187 e NTN-B 9,2258 dão 17,66 bps no snapshot; a
  diferença aritmética daria 19,29. É a mesma fórmula de `fetch.py`
  (debêntures), então os dois produtos ficam comparáveis.

- **CDI+** (`"DI ADITIVO"`) — `taxa * 100`. Idem debênture.

- **%CDI** (`"DI MULTIPLICATIVO"`) — `(taxa - 100) * 100`. Só existe em
  securitizado. Papel que paga 97,2% do CDI vira -278 bps: **spread
  negativo é normal aqui**, não é erro de sinal.

- **PRÉ** (`"PRE FIXADO"`) — sem spread. Não há curva de referência
  aplicável no mesmo arcabouço; devolve `None` em vez de um número que
  ninguém saberia interpretar.

NUNCA misturar as classes num mesmo agregado: as bases de comparação são
diferentes (mesma regra que já vale pra debênture, ver `queries.py`).
"""
from __future__ import annotations

from datetime import date, datetime

from . import anbima_api

CAMINHO = "/feed/precos-indices/v1/cri-cra/mercado-secundario"

# `tipo_remuneracao` (texto cru da Anbima) -> classe analítica.
# Mapeamento observado no dado real; qualquer valor fora daqui cai em
# "OUTRO" e fica sem spread, em vez de ser forçado numa fórmula errada.
INDEXADOR_POR_REMUNERACAO = {
    "IPCA": "IPCA+",
    "DI ADITIVO": "CDI+",
    "DI MULTIPLICATIVO": "%CDI",
    "PRE FIXADO": "OUTRO",
}

INDEXADORES = ("IPCA+", "CDI+", "%CDI", "OUTRO")

# Dias úteis por ano — mesma constante usada em fetch.py pra debêntures.
DIAS_UTEIS_ANO = 252


def classificar_indexador(tipo_remuneracao: str | None) -> str:
    if not tipo_remuneracao:
        return "OUTRO"
    return INDEXADOR_POR_REMUNERACAO.get(str(tipo_remuneracao).strip().upper(), "OUTRO")


def classificar_tipo_ativo(codigo: str | None) -> str | None:
    """CRA ou CRI a partir do código do ativo.

    Regra tirada do dado (04/08/2026): código de CRA começa com "CRA"
    (ex. `CRA018002XN`, 61.117 observações); CRI vem só numérico
    (ex. `18H0014828`, 34.296). A API não traz um campo de tipo, e no
    snapshot do Allan a classificação bate 100% com essa regra.
    """
    if not codigo:
        return None
    c = str(codigo).strip().upper()
    if c.startswith("CRA"):
        return "CRA"
    if c.startswith("CRI"):
        return "CRI"
    return "CRI"


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _data(v) -> date | None:
    if not v:
        return None
    try:
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def calcular_spread(
    indexador: str,
    taxa: float | None,
    taxa_ntnb: float | None = None,
) -> float | None:
    """Spread em bps, ou `None` quando não é calculável.

    `None` (e não zero) para papel sem taxa: a Anbima devolve `0` para
    papel que não foi precificado no dia, e tratar isso como spread zero
    coloca um papel inexistente no meio da distribuição. O snapshot do
    Allan tem 9.182 linhas assim.
    """
    if taxa is None or taxa == 0:
        return None
    if indexador == "CDI+":
        return taxa * 100
    if indexador == "%CDI":
        return (taxa - 100) * 100
    if indexador == "IPCA+":
        if not taxa_ntnb:
            return None
        return ((1 + taxa / 100) / (1 + taxa_ntnb / 100) - 1) * 10000
    return None


def resolver_taxa_ntnb(
    ref_ntnb: str | None,
    ntnb_rates: dict[str, float] | None,
    min_ntnb: float | None = None,
) -> tuple[float | None, str]:
    """Taxa de NTN-B a usar num papel IPCA+, e de onde ela veio.

    Regra confirmada pelo Allan (04/08/2026): *"para o cálculo do spread
    IPCA+ obrigatoriamente eu uso a referência NTN-B, usando o conceito de
    mercado de buscar a data ou usar o menor vértice"*.

    Ordem:
    1. **Referência do próprio papel** (`referencia_ntnb`, vencimento que
       a Anbima associa àquele CRI/CRA) casada na curva do dia;
    2. **Vértice mais curto da curva** (`min_ntnb`) como fallback.

    É a mesma regra de `fetch.py` para debêntures — foi lá, aliás, que
    ela já tinha sido corrigida (27/07/2026): o cálculo usava sempre o
    vértice mais curto para todo papel, quando deveria preferir a
    referência específica. Repetir o erro aqui seria repetir um bug já
    pago.

    Devolve `(taxa, origem)`, com origem em `"REFERENCIA" | "VERTICE_CURTO"
    | "INDISPONIVEL"` — a origem existe para o log do job diário poder
    avisar se um dia começar a cair tudo no fallback, que indicaria a
    curva ou o campo de referência quebrados.
    """
    if ref_ntnb and ntnb_rates:
        taxa = ntnb_rates.get(ref_ntnb)
        if taxa:
            return taxa, "REFERENCIA"
    if min_ntnb:
        return min_ntnb, "VERTICE_CURTO"
    return None, "INDISPONIVEL"


def normalizar(
    row: dict,
    ntnb_rates: dict[str, float] | None = None,
    min_ntnb: float | None = None,
) -> dict | None:
    """Uma linha crua da API -> dicionário pronto pra persistir.

    `ntnb_rates` é a curva `{vencimento: taxa}` do dia (a mesma que
    `fetch.fetch_ntnb_curve` já busca pras debêntures) — reaproveitada em
    vez de refeita, porque é o mesmo dado e uma requisição a menos por dia.
    `min_ntnb` é o vértice mais curto, usado como fallback (ver
    `resolver_taxa_ntnb`).
    """
    codigo = (row.get("codigo_ativo") or "").strip()
    if not codigo:
        return None

    indexador = classificar_indexador(row.get("tipo_remuneracao"))
    ref_ntnb = (row.get("referencia_ntnb") or "").strip() or None
    taxa = _num(row.get("taxa_indicativa"))

    taxa_ntnb = None
    origem_ntnb = None
    if indexador == "IPCA+":
        taxa_ntnb, origem_ntnb = resolver_taxa_ntnb(ref_ntnb, ntnb_rates, min_ntnb)

    duration_du = _num(row.get("duration"))

    return {
        "codigo": codigo,
        "tipo_ativo": classificar_tipo_ativo(codigo),
        "emissor": (row.get("emissor") or "").strip() or None,
        "originador_credito": (row.get("originador_credito") or "").strip() or None,
        "serie": (row.get("serie") or "").strip() or None,
        "emissao": (row.get("emissao") or "").strip() or None,
        "data_vencimento": _data(row.get("data_vencimento")),
        "tipo_remuneracao": (row.get("tipo_remuneracao") or "").strip() or None,
        "indexador": indexador,
        "referencia_ntnb": ref_ntnb,
        "data": _data(row.get("data_referencia")),
        "taxa_indicativa": taxa,
        "taxa_compra": _num(row.get("taxa_compra")),
        "taxa_venda": _num(row.get("taxa_venda")),
        "desvio_padrao": _num(row.get("desvio_padrao")),
        "pu": _num(row.get("pu")),
        "pct_pu_par": _num(row.get("percent_pu_par")),
        "pct_vne": _num(row.get("percent_vne")),
        "pct_reune": _num(row.get("percent_reune")),
        # Dias úteis -> anos, pra ficar na mesma unidade de
        # DebentureSpread.duration (senão os dois produtos não podem ir
        # pro mesmo gráfico de spread × duration).
        "duration": duration_du / DIAS_UTEIS_ANO if duration_du else None,
        "taxa_ntnb_ref": taxa_ntnb,
        "origem_ntnb": origem_ntnb,
        "spread": calcular_spread(indexador, taxa, taxa_ntnb),
    }


def buscar(
    dia: date | None = None,
    ntnb_rates: dict[str, float] | None = None,
    min_ntnb: float | None = None,
) -> list[dict]:
    """Busca e normaliza os CRI/CRA de um dia.

    Sem `dia`, a Anbima devolve a data mais recente disponível. Lista
    vazia quando não há publicação (fim de semana, feriado, ou antes das
    20h) — não é erro.
    """
    params = {"data": dia.isoformat()} if dia else {}
    brutos = anbima_api._get(CAMINHO, params)
    saida = []
    for row in brutos or []:
        norm = normalizar(row, ntnb_rates, min_ntnb)
        if norm and norm["data"]:
            saida.append(norm)
    return saida


def resumo(linhas: list[dict]) -> dict:
    """Contagens por tipo e indexador — pro log do job diário.

    Serve de canário: se um dia vier com 300 papéis em vez de ~490, ou
    com tudo em "OUTRO", alguma coisa mudou na fonte.
    """
    por_tipo: dict[str, int] = {}
    por_indexador: dict[str, int] = {}
    origem_ntnb: dict[str, int] = {}
    sem_spread = 0
    for l in linhas:
        por_tipo[l["tipo_ativo"] or "?"] = por_tipo.get(l["tipo_ativo"] or "?", 0) + 1
        por_indexador[l["indexador"]] = por_indexador.get(l["indexador"], 0) + 1
        if l.get("origem_ntnb"):
            origem_ntnb[l["origem_ntnb"]] = origem_ntnb.get(l["origem_ntnb"], 0) + 1
        if l["spread"] is None:
            sem_spread += 1
    return {
        "total": len(linhas),
        "por_tipo": por_tipo,
        "por_indexador": por_indexador,
        # Canário: se um dia quase tudo cair em VERTICE_CURTO, ou a curva
        # ou o campo `referencia_ntnb` da Anbima quebrou.
        "origem_ntnb": origem_ntnb,
        "sem_spread": sem_spread,
        "data": linhas[0]["data"] if linhas else None,
    }
