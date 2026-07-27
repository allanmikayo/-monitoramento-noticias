"""Captura de spreads de debêntures no mercado secundário — API oficial da
Anbima (developers.anbima.com.br, ver app/spreads/anbima_api.py) +
debentures.com.br (estoque por ativo, características das emissões).

HISTÓRICO (24/07/2026): a primeira versão deste módulo usava o boletim
`.xls` público diário da Anbima — Allan descobriu ao rodar o backfill que
esse arquivo só fica disponível pros últimos ~5 dias úteis, insuficiente
pro histórico de 2 anos pedido. Pivotamos pra API oficial (OAuth2,
client_credentials — credenciais de Allan em admin-developers.anbima.com.br),
com profundidade histórica confirmada manualmente via
scripts/anbima_api_probe.py (`data=2024-07-23` devolveu dado no mesmo
formato da data mais recente, tanto pra debêntures quanto pra NTN-B). Ver
CLAUDE.md, seção "Spreads de debêntures", pro histórico completo da
migração.

Fontes:
- Debêntures (Taxa Indicativa, PU, Duration, % Pu Par, Referência NTN-B):
  API Anbima, endpoint /debentures/mercado-secundario.
- Taxas de NTN-B (referência pro cálculo do spread de papéis IPCA+): API
  Anbima, endpoint /titulos-publicos/mercado-secundario-TPF (filtrado por
  tipo_titulo == "NTN-B") — taxa discreta por vencimento, equivalente
  exato da aba "NTN-B" do `.xls` antigo (não usamos a curva paramétrica
  de /titulos-publicos/curvas-juros, que exigiria reimplementar
  Nelson-Siegel-Svensson à toa).
- Estoque por ativo: debentures.com.br (ASP antigo, tabela HTML em
  latin1) — NÃO confirmado se tem a mesma limitação de retenção do boletim
  antigo da Anbima; degradação graciosa já embutida (linha sem estoque
  cruzado não é descartada, só fica com Estoque=None).
- Características (CNPJ, se é incentivada Lei 12.431): debentures.com.br
  (TSV com cabeçalho variável) — não é parametrizado por data porque esse
  dado não muda ao longo do tempo pra uma debênture já emitida, então uma
  captura por rodada (não uma por dia do backfill) é suficiente mesmo pro
  backfill histórico.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import StringIO

import pandas as pd
import requests

from . import anbima_api

logger = logging.getLogger(__name__)

URL_ESTOQUE = (
    "https://www.debentures.com.br/exploreosnd/"
    "consultaadados/estoque/estoqueporativo_r1.asp"
)
URL_CARACS = (
    "https://www.debentures.com.br/exploreosnd/"
    "consultaadados/emissoesdedebentures/"
    "caracteristicas_e.asp?tip_deb=publicas&op_exc=Nada"
)


def compute_classe(indexador: str | None, incentivada: str | None) -> str:
    """Bucket analítico pedido pelo Allan (23/07/2026): 'IPCA + Incentivadas'
    e 'CDI + Tradicionais' NÃO são comparáveis entre si (bases de referência
    diferentes — NTN-B vs DI), então todo o dashboard usa essa classe como
    filtro principal, não o indexador sozinho. Papéis que não caem em
    nenhum dos dois padrões de mercado (ex.: CDI+ incentivada, IPCA+ não
    incentivada, ou grupo desconhecido vindo da Anbima) caem em 'Outros'
    em vez de forçados numa das duas classes principais."""
    idx = (indexador or "").strip()
    inc = (incentivada or "").strip().lower()
    is_incentivada = inc.startswith("s")  # "Sim" -- Anbima não usa outro valor positivo conhecido
    if idx == "IPCA +" and is_incentivada:
        return "IPCA + Incentivadas"
    if idx == "CDI +" and not is_incentivada:
        return "CDI + Tradicionais"
    return "Outros"


def build_session() -> requests.Session:
    """Sessão com proxy corporativo opcional — usada só pras chamadas a
    debentures.com.br (estoque/características); a API oficial da Anbima
    (app/spreads/anbima_api.py) usa suas próprias chamadas `requests`."""
    s = requests.Session()
    if any([
        os.getenv("HTTP_PROXY"), os.getenv("HTTPS_PROXY"),
        os.getenv("http_proxy"), os.getenv("https_proxy"),
    ]):
        return s
    host = os.getenv("PROXY_HOST")
    if host:
        user = os.getenv("PROXY_USER")
        pwd = os.getenv("PROXY_PASS")
        proxy_url = f"http://{user}:{pwd}@{host}" if user and pwd else f"http://{host}"
        s.proxies.update({"http": proxy_url, "https": proxy_url})
    return s


def _normalize_codigo(raw) -> str:
    """Código de ativo (debênture), normalizado pra cruzar com segurança
    entre a Anbima e o debentures.com.br. Allan avisou (24/07/2026): as
    bases scrapeadas do debentures.com.br costumam vir com espaço em
    branco sobrando (ex.: "RISP14   ") -- `.strip()` sozinho NÃO cobre
    todo tipo de espaço "invisível" (ex.: zero-width space \\u200b não é
    reconhecido como whitespace pelo Python, passa reto pelo strip()).
    Em vez de tentar enumerar toda variação possível, filtra pra manter só
    [A-Za-z0-9] (código de debênture nunca tem outra coisa) e maiusculiza
    -- elimina qualquer lixo invisível de uma vez, dos dois lados do
    cruzamento (fetch_estoque, fetch_caracs, e o codigo_ativo da Anbima em
    fetch_spreads usam essa mesma função)."""
    return re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()


def parse_num(val) -> float | None:
    """Converte string numérica no formato Anbima (vírgula decimal, ponto
    de milhar, '#N/D' etc.) pra float."""
    s = str(val).strip().replace(" ", "")
    if not s or s.startswith("#") or s.lower() == "nan":
        return None
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def fetch_estoque(session: requests.Session, date_str: str) -> pd.DataFrame:
    """Estoque (R$ mil) por Código na data (formato dd/mm/aaaa). Devolve
    colunas ['Código', 'Estoque']."""
    resp = session.get(URL_ESTOQUE, params={
        "Op_exc": "Nada", "Moeda": "1",
        "dt_ini": date_str, "dt_fim": date_str, "cab": "s",
    }, timeout=60)
    resp.raise_for_status()
    html = resp.content.decode("latin1")
    tables = pd.read_html(StringIO(html), header=None)
    for df in tables:
        mask = df.eq("Emissor").any(axis=1)
        if mask.any():
            hdr = df.loc[mask, :].iloc[0].astype(str).str.strip()
            data = df.loc[mask.idxmax() + 1:].reset_index(drop=True)
            data.columns = hdr
            # Posição fixa das colunas (igual ao script original) — a
            # página não expõe nomes de coluna estáveis pra todas, só pra
            # algumas (inclusive "Emissor", usada só pra achar a linha de
            # cabeçalho). Se o layout mudar, isso quebra com IndexError.
            sel = data.iloc[:, [2, 15]].copy()
            sel.columns = ["Código", "EstoqueRaw"]
            sel["Código"] = sel["Código"].apply(_normalize_codigo)
            sel["Estoque"] = sel["EstoqueRaw"].apply(parse_num)
            return sel[["Código", "Estoque"]]
    raise RuntimeError(f"Estoque não encontrado para {date_str} (layout da página mudou?)")


@dataclass
class SpreadRow:
    codigo: str
    nome: str | None
    indexador: str  # "CDI +" | "IPCA +"
    taxa_indicativa: float | None
    pu: float | None
    pct_pu_par: float | None
    duration: float | None  # já em anos (dividido por 252)
    spread: float | None  # em bps
    estoque: float | None  # já em R$ milhões (dividido por 1000)
    # Vencimento da NTN-B que a Anbima usa como referência PRA ESTE papel
    # (campo cru `referencia_ntnb` do boletim -- só vem preenchido em IPCA
    # SPREAD). Persistido em Debenture.referencia_ntnb (27/07/2026) pra
    # `b3_trades.compute_trade_spreads` conseguir usar a referência certa
    # de cada papel em vez de sempre cair no vértice mais curto.
    referencia_ntnb: str | None = None


def fetch_ntnb_curve(dt: date) -> tuple[dict[str, float], float | None, str | None]:
    """Busca a tabela de NTN-B (taxa por vencimento) do dia `dt` na API da
    Anbima e já calcula a taxa de vértice mais curto (`min_ntnb`/
    `min_venc`) -- usada como fallback pra papel IPCA+ sem
    `referencia_ntnb` própria (ver `fetch_spreads`) e reaproveitada por
    `b3_trades.compute_trade_spreads` pro spread do negócio a negócio da
    B3 (extraído daqui, 27/07/2026, pra virar uma função só chamada de
    dois lugares em vez de duas cópias da mesma lógica).

    Devolve (ntnb_rates, min_ntnb, min_venc). Deixa exceção de rede
    propagar -- cada chamador decide como reagir (ver `fetch_spreads` e
    `b3_trades._get_ntnb_curve`)."""
    titulos = anbima_api.fetch_titulos_publicos_mercado_secundario(dt)
    ntnb_rates: dict[str, float] = {}
    min_ntnb: float | None = None
    min_venc: str | None = None
    for t in titulos:
        if t.get("tipo_titulo") != "NTN-B":
            continue
        venc = t.get("data_vencimento")
        taxa = t.get("taxa_indicativa")
        if venc is None or taxa is None:
            continue
        ntnb_rates[venc] = float(taxa)
        if min_venc is None or venc < min_venc:
            min_venc = venc
            min_ntnb = float(taxa)
    return ntnb_rates, min_ntnb, min_venc


def fetch_spreads(session: requests.Session, dt: date) -> tuple[list[SpreadRow], dict[str, float], float | None, str | None]:
    """Busca debêntures + taxas de NTN-B do dia `dt` na API oficial da
    Anbima, cruza com estoque (debentures.com.br) e calcula o Spread (bps)
    de cada debênture. Fórmula preservada do script original do Allan
    (spreads.docx, 23/07/2026), com uma correção de unidade:

    - Papel com `referencia_ntnb` preenchida (observado sempre em IPCA
      SPREAD, nunca em DI SPREAD): Spread = ((1+taxa/100)/(1+taxa_ntnb/100)
      - 1) * 10000, em bps.
    - IPCA+ sem referência: mesma fórmula, usando a taxa da NTN-B de
      vértice mais curto do dia (min_ntnb).
    - CDI+ (DI SPREAD): Spread = Taxa Indicativa * 100.
      CORRIGIDO (24/07/2026): o script original do Allan deixava esse
      valor em pontos percentuais (sem multiplicar por 100) — mas a coluna
      `DebentureSpread.spread` no banco já é documentada como "em bps"
      (ver app/models.py) e o Allan pediu explicitamente que o spread
      SEMPRE apareça em bps no dashboard. Sem essa correção, CDI+ e IPCA+
      ficariam em unidades diferentes na mesma coluna (~160 vs ~16000),
      o que quebraria qualquer comparação/gráfico.

    Devolve `([], {}, None, None)` se a Anbima não tiver publicação pro dia
    `dt` (feriado/fim de semana/não publicado ainda). Devolve também
    `(ntnb_rates, min_ntnb, min_venc)` -- ADICIONADO (27/07/2026) pra
    `scripts/fetch_debenture_spreads.py` poder cachear a curva inteira
    (`persist.cache_ntnb_referencia`) e o negócio a negócio da B3
    reaproveitar sem bater na Anbima nos outros ~95 ciclos de captura do
    mesmo dia (pedido do Allan: "a taxa de referência não vai mudar ao
    longo do dia") -- ver `b3_trades._get_ntnb_curve`. `ntnb_rates`
    (dict `{vencimento: taxa}`) foi ADICIONADO de novo, no mesmo dia:
    Allan apontou que negócio a negócio da B3 estava usando sempre o
    vértice mais curto (`min_ntnb`) como referência, quando deveria usar a
    referência ESPECÍFICA de cada papel (`referencia_ntnb`, também
    devolvido agora em cada `SpreadRow`) -- só cai no vértice mais curto
    quando o papel não tem essa referência própria.
    """
    date_str = dt.strftime("%d/%m/%Y")

    try:
        estoque_df = fetch_estoque(session, date_str)
        estoque_map = dict(zip(estoque_df["Código"], estoque_df["Estoque"]))
    except Exception as e:
        # CORRIGIDO (24/07/2026): antes engolia a exceção sem mostrar o
        # motivo (só "Estoque indisponível"), impossível diagnosticar por
        # que estava faltando estoque com tanta frequência no backfill do
        # Allan -- ver scripts/estoque_probe.py pro diagnóstico isolado.
        logger.warning("Estoque indisponível para %s — seguindo sem cruzar estoque. Motivo: %s: %s", date_str, type(e).__name__, e)
        estoque_map = {}

    debentures = anbima_api.fetch_debentures_mercado_secundario(dt)
    if not debentures:
        return [], {}, None, None  # sem publicação nesse dia

    try:
        ntnb_rates, min_ntnb, min_venc = fetch_ntnb_curve(dt)
    except Exception:
        logger.warning(
            "Taxas de NTN-B indisponíveis para %s — spreads IPCA+ sem "
            "referência própria ficarão sem cálculo", date_str,
        )
        ntnb_rates, min_ntnb, min_venc = {}, None, None

    out: list[SpreadRow] = []
    for row in debentures:
        codigo = _normalize_codigo(row.get("codigo_ativo"))
        if not codigo:
            continue
        grupo = row.get("grupo") or ""
        taxa_raw = row.get("taxa_indicativa")
        taxa = float(taxa_raw) if taxa_raw is not None else None
        ref_ntnb = row.get("referencia_ntnb")

        if grupo == "DI SPREAD":
            indexador_label = "CDI +"
        elif grupo == "IPCA SPREAD":
            indexador_label = "IPCA +"
        else:
            # Grupo fora dos dois observados no probe -- preserva o texto
            # cru; compute_classe() joga em "Outros" automaticamente (não
            # bate com "CDI +" nem "IPCA +"), então ainda aparece no
            # dashboard (cobertura de "todo o mercado" pedida pelo Allan),
            # só não entra nos dois filtros principais.
            indexador_label = grupo

        spread: float | None = None
        if ref_ntnb and ref_ntnb in ntnb_rates and taxa is not None:
            spread = ((1 + taxa / 100) / (1 + ntnb_rates[ref_ntnb] / 100) - 1) * 10000
        elif grupo == "DI SPREAD" and taxa is not None:
            spread = taxa * 100
        elif grupo == "IPCA SPREAD" and taxa is not None and min_ntnb is not None:
            spread = ((1 + taxa / 100) / (1 + min_ntnb / 100) - 1) * 10000

        duration_du = row.get("duration")
        duration = float(duration_du) / 252 if duration_du is not None else None

        estoque_raw = estoque_map.get(codigo)
        estoque = estoque_raw / 1000 if estoque_raw is not None else None

        emissor = row.get("emissor")
        nome = None
        if emissor:
            # Remove os marcadores de rodapé da Anbima ("(*)"/"(**)"/"(#)")
            # do nome do emissor -- ver doc da API (Debêntures / Mercado
            # Secundário): (*) cláusula de resgate/amortização antecipados,
            # (**) cláusula em período de exercício, (#) negociação em
            # "combo". Não são parte do nome, só poluiriam a UI.
            nome = re.sub(r"\s*\((\*+|#)\)", "", str(emissor)).strip() or None

        out.append(SpreadRow(
            codigo=codigo,
            nome=nome,
            indexador=indexador_label,
            taxa_indicativa=taxa,
            pu=float(row["pu"]) if row.get("pu") is not None else None,
            pct_pu_par=float(row["percent_pu_par"]) if row.get("percent_pu_par") is not None else None,
            duration=duration,
            spread=spread,
            estoque=estoque,
            referencia_ntnb=ref_ntnb,
        ))
    return out, ntnb_rates, min_ntnb, min_venc


@dataclass
class Caracteristicas:
    codigo: str
    incentivada: str | None
    cnpj: str | None


def fetch_caracs(session: requests.Session) -> list[Caracteristicas]:
    """Características das emissões (CNPJ, se é incentivada Lei 12.431) —
    não varia por data, chamar uma vez por rodada (não uma vez por dia do
    backfill). Continua vindo de debentures.com.br -- a API oficial da
    Anbima não tem um endpoint equivalente (só Mercado Secundário, Curvas
    de Crédito e Projeções pra debêntures, conferido na documentação)."""
    resp = session.get(URL_CARACS, timeout=60)
    resp.raise_for_status()
    lines = resp.content.decode("latin1").splitlines()
    try:
        idx = next(i for i, l in enumerate(lines) if l.startswith("Codigo do Ativo"))
    except StopIteration:
        raise RuntimeError("Layout do arquivo de características mudou (cabeçalho 'Codigo do Ativo' não encontrado)")
    df = pd.read_csv(StringIO("\n".join(lines[idx:])), sep="\t", dtype=str)
    df.rename(columns={"Codigo do Ativo": "Código"}, inplace=True)
    cols = ["Código", "Deb. Incent. (Lei 12.431)", "CNPJ"]
    df = df[cols].apply(lambda s: s.astype("string").str.strip())
    # Código passa por normalização mais forte que strip() (ver
    # _normalize_codigo) -- é usado pra CRUZAR com Debenture.codigo (vindo
    # da Anbima), diferente de incentivada/CNPJ que só são exibidos, não
    # comparados/casados com outra fonte.
    df["Código"] = df["Código"].apply(_normalize_codigo)
    return [
        Caracteristicas(
            codigo=r["Código"],
            incentivada=(r["Deb. Incent. (Lei 12.431)"] or None),
            cnpj=(r["CNPJ"] or None),
        )
        for r in df.to_dict("records")
        if r["Código"]
    ]


def business_days(start: date, end: date):
    """Gera datas de start a end (inclusive), pulando sábado/domingo —
    feriados nacionais ainda passam por aqui (fetch_spreads trata dia sem
    boletim devolvendo lista vazia, sem quebrar o backfill)."""
    d = start
    one_day = timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:  # 0=segunda ... 4=sexta
            yield d
        d += one_day


def detect_latest_published_date(session: requests.Session) -> date:
    """Acha o dia mais recente já publicado na Anbima. Diferente da versão
    antiga baseada em `.xls` (que tinha que tentar dia a dia porque não
    tinha como perguntar "qual a data mais recente"), a API oficial
    devolve isso direto quando chamada sem o parâmetro `data` (documentado
    e confirmado via scripts/anbima_api_probe.py). O parâmetro `session`
    é aceito só por compatibilidade com o chamador (scripts/
    fetch_debenture_spreads.py) -- não é usado aqui, a API oficial usa
    suas próprias chamadas (ver anbima_api.py)."""
    rows = anbima_api.fetch_debentures_mercado_secundario(dt=None)
    if not rows:
        raise RuntimeError(
            "API da Anbima não devolveu nenhuma debênture (sem parâmetro "
            "de data) -- verifique credenciais/serviço."
        )
    data_str = rows[0].get("data_referencia")
    if not data_str:
        raise RuntimeError(f"Resposta da Anbima sem 'data_referencia': {rows[0]}")
    d = datetime.strptime(data_str, "%Y-%m-%d").date()
    logger.info("Data publicada mais recente encontrada: %s", d)
    return d
