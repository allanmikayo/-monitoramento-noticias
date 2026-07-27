"""Negócio a negócio da B3 -- "Boletim Diário do Mercado" (pedido do Allan,
24/07/2026): tabela de negociações individuais (cada operação, não
agregada) de debêntures, CRI e CRA, atualizada a cada 15 min durante o
pregão (aviso oficial da própria B3 na página).

Fonte: https://arquivos.b3.com.br/bdi/tabelas (SPA -- o HTML puro não tem
nada, o conteúdo real vem de uma API JSON por trás). Descoberta inspecionando
o tráfego de rede da página (Chrome DevTools/MCP), 24/07/2026:

    POST https://arquivos.b3.com.br/bdi/table/Trade/{inicio}/{fim}/{pagina}/{tamanho}

`{inicio}`/`{fim}` em AAAA-MM-DD, `{pagina}` 1-based, `{tamanho}` registros
por página (testado até 1000 de forma confiável -- 2000 devolveu corpo vazio
num teste manual, não insista em página maior que isso). Sem autenticação,
sem corpo no POST. Devolve JSON com `table.values` (lista de listas, uma
por negócio) e `table.pageCount` (total de páginas no tamanho pedido).

Layout de coluna (fixo por índice -- a API não manda nome de campo por
linha, só uma lista `table.columns` descritiva em paralelo):
    0 RptDt (data), 1 DtRef (data), 2 InstrumentType, 3 Issuer,
    4 TckrSymb (ticker), 5 Quantity, 6 Price, 7 Vol (R$), 8 Rate (taxa),
    9 Origin, 10 TradeTime (HH:MM), 11 TradeDate, 12 TradeCode (id único
    do negócio, ex. "#1009622879"), 13 ISIN, 14 SettlementDt,
    15 Situation, 16 IdSer.

`InstrumentType` tem BEM mais valores do que o que o Allan pediu (CFF,
CDCA, COE, CPR, LF, LFSN, ...) -- filtramos só DEB/CRI/CRA aqui
(INSTRUMENT_TYPES). Testado contra dado real de 24/07/2026 (dia corrente)
e também 2024-07-23 e 2026-07-01 (histórico de +2 anos funciona igual)."""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime

import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Debenture, DebentureSpread, NtnbReferencia
from .fetch import _normalize_codigo, fetch_ntnb_curve

logger = logging.getLogger(__name__)

BASE_URL = "https://arquivos.b3.com.br/bdi/table/Trade"
PAGE_SIZE = 1000
INSTRUMENT_TYPES = {"DEB", "CRI", "CRA"}
TIMEOUT = 30
# Pausa entre páginas (pedido nenhum, defensivo) e retries por página --
# BUG ENCONTRADO NA PRÁTICA (24/07/2026): rodando o script duas vezes
# seguidas em menos de um minuto, a primeira trouxe 10.780 negócios (15
# páginas) e a segunda voltou vazia já na página 1 (Allan confirmou pelo
# log). Sem confirmação oficial da causa (rate limit? hiccup do lado da
# B3?), mas o padrão -- funcionou, rodou nada e não deu erro nenhum -- é
# clássico de resposta vazia/serviço momentaneamente instável, não de
# "não tem negócio nenhum hoje" (tinha 10 mil segundos antes). Por isso
# agora: retry com backoff por página, e um aviso alto quando a PRIMEIRA
# página vem vazia (isso é sempre suspeito -- só é normal em fim de
# semana/feriado, quando a chamada nem deveria estar rodando).
PAGE_DELAY_SECONDS = 0.3
MAX_RETRIES_PER_PAGE = 3
RETRY_BACKOFF_SECONDS = 2.0

# BUG REAL CONFIRMADO AO VIVO (27/07/2026): o Allan filtrou "Energisa
# Sergipe" na aba Emissores e um negócio real de hoje (ENSEB4,
# trade_code "#1009631632", 10:29:46) não aparecia -- nem na tabela nem
# no card de taxa da B3. Reproduzi direto no navegador contra a B3: a
# consulta de HOJE (`/Trade/{hoje}/{hoje}/...`) às vezes devolve JSON
# 200 válido só que com `table.values: []` e `table.pageCount: 0` -- não
# é erro de rede (por isso o retry de `_fetch_page` sozinho, que só pega
# exceção/corpo vazio, não pegava isso), é um corpo "vazio de verdade"
# só que temporário: 5 tentativas seguidas espaçadas de 0,5s vieram todas
# vazias, e voltou ao normal (1000 negócios na página 1) depois de uns
# 10s de espera -- o negócio do Energisa Sergipe estava lá o tempo todo
# (achado na página 2 assim que a consulta voltou a responder). Layer
# extra de retry abaixo (`_fetch_page_sem_vazio`) insiste quando o corpo
# vem vazio mas SEM erro HTTP -- FORA do retry de rede de `_fetch_page`,
# que não serve pra esse caso.
EMPTY_RETRY_ATTEMPTS = 4
EMPTY_RETRY_BACKOFF_SECONDS = 3.0


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw).date()


def _fetch_page(url: str) -> dict:
    """Busca uma página com retry -- corpo vazio/JSON inválido/erro de
    rede contam como falha retryable (ver nota acima sobre a página 1
    vindo vazia num teste real do Allan sem nenhum erro HTTP)."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES_PER_PAGE + 1):
        try:
            resp = requests.post(url, timeout=TIMEOUT)
            resp.raise_for_status()
            if not resp.text:
                raise ValueError("resposta vazia da B3")
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < MAX_RETRIES_PER_PAGE:
                logger.warning(
                    "Falha buscando %s (tentativa %d/%d): %s -- tentando de novo em %.1fs",
                    url, attempt, MAX_RETRIES_PER_PAGE, exc, RETRY_BACKOFF_SECONDS * attempt,
                )
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"Falha buscando {url} após {MAX_RETRIES_PER_PAGE} tentativas") from last_exc


def _fetch_page_sem_vazio(url: str, page: int) -> dict:
    """Busca uma página e insiste (`EMPTY_RETRY_ATTEMPTS` vezes) se vier
    com `table.values: []` sem nenhum erro HTTP/rede -- ver bug real
    documentado acima (`EMPTY_RETRY_ATTEMPTS`). Se acabar as tentativas
    ainda vazio, devolve a última resposta mesmo assim (o chamador decide
    o que fazer -- ver `fetch_trades`)."""
    data: dict = {}
    for attempt in range(1, EMPTY_RETRY_ATTEMPTS + 1):
        data = _fetch_page(url)
        table = data.get("table") or {}
        if table.get("values"):
            return data
        if attempt < EMPTY_RETRY_ATTEMPTS:
            logger.warning(
                "Página %d (%s) veio vazia sem erro HTTP (tentativa %d/%d) -- "
                "pode ser fim de dado de verdade ou instabilidade passageira da "
                "B3 (já vimos isso na prática, recupera sozinha em segundos) -- insistindo...",
                page, url, attempt, EMPTY_RETRY_ATTEMPTS,
            )
            time.sleep(EMPTY_RETRY_BACKOFF_SECONDS * attempt)
    return data


def fetch_trades(start: date, end: date) -> list[dict]:
    """Busca negócio a negócio no intervalo [start, end] (inclusive),
    paginando PAGE_SIZE em PAGE_SIZE, já filtrado pra DEB/CRI/CRA. Cada
    chamada pega o período inteiro de novo (a B3 não tem um "desde a
    última consulta") -- dedupe de verdade fica por conta de quem grava
    (ver `save_trades`, chave `trade_code`).

    Se uma página falhar de vez (rede exaurida OU continuar vazia mesmo
    depois de todas as tentativas), NÃO derruba a captura inteira --
    loga um erro e devolve o que já tinha coletado até ali (parcial é
    melhor que nada; a próxima rodada do agendador, 15 min depois,
    tenta o dia inteiro de novo do zero e tende a preencher o que faltou)."""
    resultados: list[dict] = []
    page = 1
    while True:
        url = f"{BASE_URL}/{start.isoformat()}/{end.isoformat()}/{page}/{PAGE_SIZE}"
        try:
            data = _fetch_page_sem_vazio(url, page)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Falha buscando página %d de %s a %s -- seguindo com o que já "
                "foi coletado (%d negócio(s) até aqui)",
                page, start, end, len(resultados),
            )
            break
        table = data.get("table") or {}
        values = table.get("values") or []
        if not values:
            if page == 1:
                logger.warning(
                    "Negócio a negócio %s a %s: página 1 continuou vazia mesmo "
                    "depois de %d tentativas -- pode ser fim de semana/feriado "
                    "(esperado) ou instabilidade da B3 mais longa que o normal "
                    "(rode de novo se era esperado ter negócio hoje).",
                    start, end, EMPTY_RETRY_ATTEMPTS,
                )
            else:
                logger.warning(
                    "Negócio a negócio %s a %s: página %d continuou vazia mesmo "
                    "depois de %d tentativas -- parando aqui com %d negócio(s) "
                    "coletado(s) (pode ter ficado faltando negócio de páginas "
                    "seguintes, a próxima rodada tenta de novo).",
                    start, end, page, EMPTY_RETRY_ATTEMPTS, len(resultados),
                )
            break
        for row in values:
            tipo = row[2]
            if tipo not in INSTRUMENT_TYPES:
                continue
            resultados.append({
                "trade_code": str(row[12]),
                "data_negocio": _parse_date(row[0]),
                "instrument_type": tipo,
                "emissor": (row[3] or "").strip() or None,
                "codigo": _normalize_codigo(row[4]),
                "isin": row[13],
                "quantidade": row[5],
                "preco": row[6],
                "volume": row[7],
                "taxa": row[8],
                "origem": row[9],
                "horario": row[10],
                "data_liquidacao": _parse_date(row[14]),
                "situacao": row[15],
            })
        page_count = table.get("pageCount", page)
        if page >= page_count:
            break
        page += 1
        time.sleep(PAGE_DELAY_SECONDS)
    logger.info(
        "Negócio a negócio %s a %s: %d negócios DEB/CRI/CRA (%d página(s))",
        start, end, len(resultados), page,
    )
    return resultados


# BUG REAL CONFIRMADO AO VIVO (27/07/2026, mesma rodada): `compute_trade_spreads`
# usava a NTN-B do PRÓPRIO dia do negócio (`t["data_negocio"]`) pra achar a
# taxa de referência. O boletim da Anbima de hoje só fica pronto/cacheado
# tarde da noite (~18-21h BRT, quando o job diário roda) -- o Allan reportou
# um caso real (BRK AMBIENTAL, BRKP28, 27/07/2026): 9 negócios capturados
# entre 13h e 17h ficaram com spread=None PRA SEMPRE (o cálculo só roda uma
# vez, na hora de gravar; não recalcula sozinho depois), mesmo a curva tendo
# sido cacheada mais tarde no mesmo dia. Investigado direto no banco: a linha
# de `NtnbReferencia` de 27/07/2026 só tinha `captured_at` às 21:42 UTC
# (~18:42 BRT), depois de todos os 9 negócios.
#
# Allan pediu o fix arquitetural (não só rodar backfill mais vezes): "A curva
# de referência da NTN-B tem que ser a data do dado Anbima. O padrão sempre
# vai ser d-1, mas deixe uma caixa que eu possa alterar essa data." Ou seja:
# a referência de QUALQUER negócio (de hoje ou de um backfill histórico) tem
# que ser o ÚLTIMO BOLETIM ANBIMA JÁ PUBLICADO antes desse negócio -- nunca a
# curva do próprio dia do negócio, mesmo que por acaso já esteja cacheada
# (ele quer consistência metodológica, não só "usa o que tiver"). Na prática
# isso é sempre d-1 (dia útil anterior) contra dia normal, mas pode ser mais
# se tiver buraco (fim de semana/feriado) -- por isso a resolução é dinâmica
# (`_resolve_ntnb_referencia_date`), não um "menos 1 dia corrido" fixo.
#
# Efeito colateral bom: como a referência agora é SEMPRE de um dia anterior
# (que o job diário já cacheou de véspera), qualquer negócio capturado ao
# longo do pregão de hoje já sai com spread calculado na hora, sem esperar
# nada -- o bug original desaparece por construção, não só fica mais raro.
#
# NOTA (27/07/2026, mesmo dia, rodada seguinte): a primeira versão veio com
# uma caixa em Administração pra travar essa data manualmente
# (`NTNB_REFERENCIA_OVERRIDE_SETTING_KEY`) -- Allan pediu pra tirar: já
# existe uma data selecionável na aba Emissores (Visão Geral/ranking) e não
# precisa de mais uma em outro lugar. Removida; a resolução é sempre
# automática agora.


def _resolve_ntnb_referencia_date(db: Session, trade_date: date) -> date | None:
    """Data cuja curva de NTN-B deve servir de referência pro negócio de
    `trade_date` -- ver nota longa acima (bug real + pedido do Allan,
    27/07/2026). Acha o último dia com boletim Anbima PUBLICADO antes de
    `trade_date` (nunca o próprio dia, mesmo que por acaso já tenha curva
    cacheada) -- na prática quase sempre `trade_date - 1 dia útil`, mas
    calculado de verdade contra `DebentureSpread.data` em vez de assumir
    "menos 1 dia corrido" (evita errar em feriado/fim de semana). `None` só
    no caso extremo de não haver NENHUM boletim anterior a essa data (base
    ainda sem histórico)."""
    return db.query(func.max(DebentureSpread.data)).filter(DebentureSpread.data < trade_date).scalar()


def _get_ntnb_curve(db: Session, dt: date) -> tuple[dict[str, float], float | None, str | None]:
    """Curva de NTN-B (taxa por vencimento) do dia `dt`, com cache em
    banco (`NtnbReferencia`) -- pedido do Allan (27/07/2026): a referência
    não muda ao longo do dia, então não faz sentido bater na API da
    Anbima de novo a cada captura de negócio a negócio da B3 (a cada 15
    min). Normalmente já vem cacheada pelo job diário de spreads
    (`scripts/fetch_debenture_spreads.py`, via `fetch.fetch_ntnb_curve` +
    `persist.cache_ntnb_referencia`) -- só busca ao vivo na Anbima aqui se
    ainda não tiver sido cacheada pra esse dia (ex.: antes do job diário
    rodar, ou primeira vez que o dia aparece), e já grava o resultado no
    cache pra próxima chamada não precisar buscar de novo.

    Devolve `(ntnb_rates, min_ntnb, min_venc)` -- `ntnb_rates` é o dict
    `{vencimento: taxa}` completo, usado por `compute_trade_spreads` pra
    achar a taxa do vencimento ESPECÍFICO de cada papel
    (`Debenture.referencia_ntnb`); `min_ntnb`/`min_venc` (vértice mais
    curto) ficam só como fallback pra papel sem referência própria."""
    cached = db.get(NtnbReferencia, dt)
    if cached is not None and cached.curva_json:
        return json.loads(cached.curva_json), cached.min_ntnb, cached.min_venc
    try:
        ntnb_rates, min_ntnb, min_venc = fetch_ntnb_curve(dt)
    except Exception:
        logger.warning(
            "NTN-B indisponível pra %s -- spread dos negócios IPCA+ "
            "desse dia fica sem cálculo (spread=None)", dt,
        )
        return {}, None, None
    if min_ntnb is not None:
        curva_json = json.dumps(ntnb_rates)
        if cached is None:
            db.add(NtnbReferencia(data=dt, min_ntnb=min_ntnb, min_venc=min_venc, curva_json=curva_json))
        else:
            # Linha já existia mas sem curva_json (cache antigo, gravado
            # antes dessa coluna existir) -- completa em vez de duplicar.
            cached.min_ntnb = min_ntnb
            cached.min_venc = min_venc
            cached.curva_json = curva_json
        db.commit()
    return ntnb_rates, min_ntnb, min_venc


def compute_trade_spreads(db: Session, trades: list[dict]) -> None:
    """Preenche `spread` (bps) em cada negócio -- pedido do Allan
    (27/07/2026): os cards da aba Emissores mostram spread, não a taxa
    crua da B3, mesma unidade usada no resto do dashboard inteiro. MESMA
    fórmula de `fetch.fetch_spreads`:
    - "CDI + Tradicionais": spread = taxa * 100.
    - "IPCA + Incentivadas": spread = ((1+taxa/100)/(1+taxa_ntnb/100)-1)*10000,
      usando a NTN-B de referência ESPECÍFICA do papel
      (`Debenture.referencia_ntnb`, o mesmo vencimento que a Anbima
      associa a ele no boletim diário -- ver `fetch.fetch_spreads`).
      CORRIGIDO (27/07/2026, mesmo dia): a primeira versão usava sempre a
      NTN-B de vértice mais curto pra TODO negócio IPCA+ da B3 (o negócio
      a negócio não traz `referencia_ntnb` por papel como o boletim da
      Anbima traz). Allan apontou que isso está errado -- ex. ASER12
      (Águas do Sertão) caiu no vértice mais curto do dia (NTN-B vencendo
      em ~3 semanas, taxa distorcida por efeito de iliquidez/"pull to
      par" perto do vencimento) e deu spread negativo, sem sentido. Agora
      só cai no vértice mais curto quando o papel NÃO tem
      `referencia_ntnb` própria cadastrada (mesma regra que
      `fetch_spreads` já usa pro card Anbima) -- não é uma fórmula nova,
      é aplicar a MESMA regra que já existia, só que também no lado B3.

      CORRIGIDO (27/07/2026, 2ª rodada): a curva de NTN-B usada como
      referência NÃO é mais a do próprio dia do negócio -- ver nota longa
      em `_resolve_ntnb_referencia_date` (bug real: boletim de hoje só
      cacheia tarde da noite, negócio capturado de manhã/tarde ficava com
      spread=None pra sempre). Agora é sempre a do último boletim Anbima
      JÁ PUBLICADO antes do negócio (padrão d-1, calculado de verdade
      contra o histórico).
    - Qualquer outro indexador (classe "Outros") ou ticker sem
      `Debenture` cadastrado (típico de CRI/CRA, que não têm cadastro
      próprio ainda): `spread` fica None -- mesma regra de nunca forçar
      uma classe que não bate, usada no resto do dashboard.

    Muta `trades` (lista de dict, formato de `fetch_trades`) IN PLACE,
    adicionando a chave "spread" em cada um. Chamado de
    `persist.save_negocios_b3` (só nos negócios NOVOS que vão ser
    gravados) e de `scripts/backfill_b3_trade_spreads.py` (nos negócios
    antigos que já foram gravados antes dessa correção existir, sem
    `spread` nenhum)."""
    if not trades:
        return

    codigos = {t["codigo"] for t in trades}
    info_por_codigo = {
        codigo: (classe, referencia_ntnb)
        for codigo, classe, referencia_ntnb in db.query(
            Debenture.codigo, Debenture.classe, Debenture.referencia_ntnb
        ).filter(Debenture.codigo.in_(codigos)).all()
    }

    # Referência de NTN-B por DATA DO NEGÓCIO -- CORRIGIDO (27/07/2026, 2ª
    # rodada, ver nota longa em `_resolve_ntnb_referencia_date`): não é
    # mais a curva do próprio dia do negócio, é a do último boletim Anbima
    # JÁ PUBLICADO antes dele (padrão d-1). Resolvida uma vez por data
    # distinta de negócio (várias datas de negócio podem apontar pro MESMO
    # boletim de referência -- ex. trades de dias seguidos sem boletim novo
    # no meio), e a curva em si só é buscada uma vez por data de
    # REFERÊNCIA distinta (não por data de negócio), cacheada dentro desta
    # chamada E no banco entre chamadas (`_get_ntnb_curve` -- a referência
    # não muda ao longo do dia, não faz sentido bater na Anbima a cada
    # captura de 15 em 15 min).
    datas_negocio_ipca = {
        t["data_negocio"]
        for t in trades
        if info_por_codigo.get(t["codigo"], (None, None))[0] == "IPCA + Incentivadas"
        and t.get("taxa") is not None
        and t.get("data_negocio") is not None
    }
    referencia_por_data_negocio = {
        dt: _resolve_ntnb_referencia_date(db, dt) for dt in datas_negocio_ipca
    }
    datas_referencia = {dt for dt in referencia_por_data_negocio.values() if dt is not None}
    curva_por_referencia = {dt: _get_ntnb_curve(db, dt) for dt in datas_referencia}

    for t in trades:
        classe, referencia_ntnb = info_por_codigo.get(t["codigo"], (None, None))
        taxa = t.get("taxa")
        if taxa is None or classe not in ("IPCA + Incentivadas", "CDI + Tradicionais"):
            t["spread"] = None
            continue
        if classe == "CDI + Tradicionais":
            t["spread"] = taxa * 100
        else:  # IPCA + Incentivadas
            data_referencia = referencia_por_data_negocio.get(t.get("data_negocio"))
            ntnb_rates, min_ntnb, _min_venc = curva_por_referencia.get(data_referencia, ({}, None, None))
            # Referência específica do papel primeiro (igual fetch_spreads
            # faz pro card Anbima); só cai no vértice mais curto (min_ntnb)
            # quando o papel não tem referência própria cadastrada.
            if referencia_ntnb and referencia_ntnb in ntnb_rates:
                taxa_ntnb = ntnb_rates[referencia_ntnb]
            else:
                taxa_ntnb = min_ntnb
            t["spread"] = (
                ((1 + taxa / 100) / (1 + taxa_ntnb / 100) - 1) * 10000
                if taxa_ntnb is not None else None
            )
