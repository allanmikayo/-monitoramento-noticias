"""Vórtx — Assembleias de debêntures.

Diferente de Oliveira Trust (API JSON pública, ver oliveiratrust.py) e
Pentágono (HTML server-rendered simples, ver pentagono.py), o site da
Vórtx é uma SPA em Next.js/App Router -- confirmado ao vivo (23/07/2026)
que um GET comum na página de busca (`/investidor/dcm?busca=NOME`) já
devolve o HTML final com a lista de operações (é server-rendered), mas a
página de DETALHE de cada operação (`/investidor/dcm/operacao?id=X`) só
mostra o conteúdo de cada aba (Assembleias, Relatórios Anuais etc.) depois
de clicar na aba de verdade -- um GET comum na mesma URL NÃO traz esse
conteúdo (confirmado comparando um `fetch()` cru com o DOM depois de
clicar via mouse de verdade). Por isso este scraper é o único dos 3 que
precisa de Playwright.

Fluxo, por empresa da cobertura:
1. Busca (`GET /investidor/dcm?busca={nome}`, plain HTTP, sem Playwright)
   -- pega todos os ids de operação (`dcm/operacao?id=X`) encontrados, sem
   filtrar tipo ainda (Vórtx também lista CRI/CRA na mesma busca). A busca
   pagina 10 por página; a página 1 é HTML normal, mas a paginação em si só
   existe via o client-side router do Next.js -- confirmado ao vivo
   (23/07/2026) que um `&page=2` comum devolve de novo a página 1. As
   páginas seguintes só saem enviando o header `RSC: 1` (protocolo interno
   do Next.js App Router p/ navegação client-side) -- o payload não é HTML
   normal, mas os ids de operação continuam aparecendo como texto puro
   (`dcm/operacao?id=NNNNN`), então um regex simples já resolve sem
   precisar entender o formato inteiro do RSC.
2. Pra cada id de operação (dedupe entre empresas -- algumas aparecem em
   mais de uma busca por coincidência de substring): abre a página com
   Playwright. O `<title>` da página segue o padrão "APELIDO - TIPO |
   Vórtx" (confirmado ao vivo: "SUZANO - DEB | Vórtx") -- segue se
   `TIPO` for DEB, CRI ou CRA (24/07/2026: diferente da Oliveira Trust,
   aqui o Apelido/título já é o nome do EMISSOR/DEVEDOR de verdade mesmo
   pra CRI/CRA -- não do veículo securitizador -- então o casamento por
   keyword de empresa funciona igual ao de debênture, sem precisar de
   etapa extra). Clica na aba "Assembleias", espera o painel renderizar e
   lê o texto. Se vier "Nenhuma assembleia encontrada", pula.

Calibrado ao vivo em 24/07/2026 contra uma operação com conteúdo real
(LIGHT - Emissão 22/Série 1, id=91018, 11 assembleias de 2023-2024):

- Cada assembleia é um `<button>` (gatilho de um Accordion Radix UI,
  `type="multiple"` -- expandir um não fecha os outros) com a data
  dd/mm/aaaa no texto e um atributo `aria-controls` apontando pro `id` do
  painel de documentos correspondente.
- IMPORTANTE: o painel de documentos (Edital/Ata + nome do arquivo) só
  existe no DOM depois de clicar no gatilho -- confirmado ao vivo que o
  HTML/RSC inicial da página NÃO traz esses nomes de arquivo em lugar
  nenhum (nem em texto visível nem nos `<script>` de streaming do
  Next.js). Por isso `fetch()` agora clica em cada gatilho de assembleia
  dentro da janela de `LOOKBACK_DAYS` antes de ler o HTML final -- ver
  função `_extrair_assembleias_do_painel`.
- Dentro do painel expandido, cada documento é uma `<tr>` com uma
  `<div class="...inline-flex...">` (o badge "Edital"/"Ata") e um
  `<span class="break-all uppercase">` com o nome do arquivo -- o nome no
  DOM mantém a caixa original (ex.: "AGD - LIGHT - 22E (23.05.24) -
  Assinada.pdf"); é só a CSS (`uppercase`) que exibe tudo maiúsculo.
- O link de download NÃO é um `<a href>` (é um `<button>` que dispara uma
  chamada JS) -- mas descobri ao vivo (inspecionando a aba Network ao
  clicar no botão de download) que ele só abre
  `https://vxmeetings-arquivos-prd.s3.us-east-1.amazonaws.com/Operacoes/{nome-do-arquivo}`
  -- um bucket S3 público, sem token/assinatura, montado direto do nome do
  arquivo. Confirmado em 2 documentos (Edital e Ata) que esse padrão
  bate exatamente. Antes desta correção, o scraper tentava achar um
  `<a href>` dentro da linha (que nunca existe) e caía num fallback
  `#assembleia-ddmmaaaa` que não abre documento nenhum -- ou seja, TODO
  link gerado em produção até 24/07/2026 estava quebrado.

Custo de rede/tempo: ~96 buscas (rápidas, HTTP puro) + 1 navegação
Playwright por operação de debênture/CRI/CRA encontrada, + 1 clique extra
por assembleia dentro dos últimos `LOOKBACK_DAYS` dias (assembleias mais
antigas que isso nem são clicadas, pra não gastar tempo expandindo
histórico irrelevante -- mesma janela usada pela Oliveira Trust). Se isso
deixar a varredura lenta demais pro intervalo do pipeline, me avise que a
gente ajusta (reduzir frequência só desta fonte, ou cachear operações já
vistas sem assembleia nova por mais tempo)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from bs4 import BeautifulSoup

from .base import DEFAULT_HEADERS, RawArticle, USER_AGENT, brt_to_utc, dump_debug_html, get, load_coverage_names

logger = logging.getLogger(__name__)

BASE_SITE = "https://www.vortx.com.br"
SEARCH_URL = f"{BASE_SITE}/investidor/dcm"
OPERACAO_URL = f"{BASE_SITE}/investidor/dcm/operacao"

# Bucket S3 publico de onde o botao de download dos documentos de
# assembleia baixa de verdade (descoberto ao vivo, 24/07/2026 -- ver
# docstring do modulo). Sem token/assinatura, monta direto do nome do
# arquivo tal como aparece no DOM.
S3_DOCUMENTOS_BASE = "https://vxmeetings-arquivos-prd.s3.us-east-1.amazonaws.com/Operacoes"

_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
_TIPOS_ATIVO_ACEITOS = ("DEB", "CRI", "CRA")

_OP_ID_RE = re.compile(r"dcm/operacao\?id=(\d+)")
MAX_SEARCH_PAGES = 5  # cada pagina tem 10 linhas -- 5 paginas = ate 50 operacoes por empresa (generoso)
LOOKBACK_DAYS = 40  # mesma janela da Oliveira Trust -- nao vale clicar/expandir assembleias antigas


def _buscar_ids_operacao(nome_empresa: str) -> list[str]:
    """Devolve os ids de operação encontrados na busca por emissor, sem
    filtrar por tipo ainda (o filtro DEB/CRI/CRA é feito depois, na página
    de cada operação, checando o `<title>` -- ver `fetch()`).

    A lista de busca (`/investidor/dcm?busca=NOME`) pagina 10 por página,
    mas a paginação é só client-side (React) na página 1 -- não dá pra só
    trocar `&page=N` num GET comum (confirmado ao vivo, 23/07/2026: devolve
    sempre a página 1). As páginas seguintes só existem via o protocolo RSC
    do Next.js (App Router), que devolve um payload parcial em vez de HTML
    -- mais barato que abrir isso tudo no Playwright, mesmo sem parsear a
    árvore RSC direito: os ids de operação continuam aparecendo como texto
    puro (`dcm/operacao?id=NNNNN`) dentro do payload, então um regex simples
    já resolve sem precisar entender o formato inteiro."""
    ids: list[str] = []
    vistos: set[str] = set()

    for page_num in range(1, MAX_SEARCH_PAGES + 1):
        try:
            if page_num == 1:
                resp = get(f"{SEARCH_URL}?busca={quote(nome_empresa)}")
            else:
                resp = get(
                    f"{SEARCH_URL}?busca={quote(nome_empresa)}&page={page_num}",
                    headers={**DEFAULT_HEADERS, "RSC": "1"},
                )
            texto = resp.text
        except Exception as e:  # noqa: BLE001
            logger.warning("vortx: falha buscando emissor %r (pagina %d): %s", nome_empresa, page_num, e)
            break

        pagina_ids = [m for m in _OP_ID_RE.findall(texto) if m not in vistos]
        if not pagina_ids:
            break
        for op_id in pagina_ids:
            vistos.add(op_id)
            ids.append(op_id)
        if len(pagina_ids) < 10:
            break  # ultima pagina (menos de 10 = nao tem proxima)

    return ids


def _extrair_assembleias_do_painel(html: str, operacao_id: str) -> list[dict]:
    """Varre o painel de aba ATIVO (Radix UI: `[role=tabpanel]` sem
    `hidden`) procurando os gatilhos de assembleia (data dd/mm/aaaa) e, pra
    cada um, o painel de documentos (Edital/Ata) apontado por
    `aria-controls` -- ver docstring do módulo pra como essa estrutura foi
    calibrada ao vivo. Só acha algo nos gatilhos que `fetch()` já clicou
    (os de fora da janela de `LOOKBACK_DAYS` continuam fechados/sem
    conteúdo de propósito, pra não gastar clique à toa com histórico
    velho)."""
    soup = BeautifulSoup(html, "lxml")
    painel = soup.select_one('[role="tabpanel"]:not([hidden])')
    if painel is None:
        return []

    texto_painel = painel.get_text(" ", strip=True)
    if "nenhuma assembleia" in texto_painel.lower():
        return []

    out: list[dict] = []
    gatilhos = painel.find_all("button")
    for gatilho in gatilhos:
        cabecalho = gatilho.get_text(" ", strip=True)
        m = _DATE_RE.search(cabecalho)
        if not m:
            continue

        content_id = gatilho.get("aria-controls")
        content = soup.find(id=content_id) if content_id else None
        if content is None:
            continue  # gatilho fora da janela de LOOKBACK_DAYS -- nunca foi clicado

        for row in content.find_all("tr"):
            badge = row.find("div", class_=lambda c: bool(c) and "inline-flex" in c)
            arquivo_el = row.find("span", class_=lambda c: bool(c) and "break-all" in c)
            nome_arquivo = arquivo_el.get_text(strip=True) if arquivo_el else ""
            if not nome_arquivo:
                continue
            out.append({
                "data": m.groups(),
                "tipo_doc": badge.get_text(strip=True) if badge else "",
                "nome_arquivo": nome_arquivo,
                "cabecalho": cabecalho[:150],
            })
    return out


def fetch(url: str) -> list[RawArticle]:
    from playwright.sync_api import sync_playwright

    nomes_cobertura = load_coverage_names()
    if not nomes_cobertura:
        logger.warning("vortx: lista de empresas da cobertura vazia -- nada sera' coletado")
        return []

    op_ids: set[str] = set()
    for nome_empresa in nomes_cobertura:
        op_ids.update(_buscar_ids_operacao(nome_empresa))

    if not op_ids:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    out: list[RawArticle] = []
    last_html = ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900}, locale="pt-BR")
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()

            for op_id in op_ids:
                op_url = f"{OPERACAO_URL}?id={op_id}"
                try:
                    page.goto(op_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1500)

                    # O <title> da pagina segue o padrao "APELIDO - TIPO |
                    # Vortx" (confirmado ao vivo: "SUZANO - DEB | Vórtx",
                    # "LIGHT - DEB | Vórtx"). Aceita DEB/CRI/CRA -- ver
                    # docstring do modulo pra por que CRI/CRA da Vórtx (ao
                    # contrario da Oliveira Trust) ja' vem com o nome do
                    # devedor certo no Apelido.
                    titulo_pagina = page.title()
                    apelido = titulo_pagina.split(" - ")[0].strip() if " - " in titulo_pagina else titulo_pagina
                    if not any(f" - {tipo} " in titulo_pagina for tipo in _TIPOS_ATIVO_ACEITOS):
                        continue

                    aba = page.get_by_role("tab", name="Assembleias")
                    aba.click(timeout=8000)
                    page.wait_for_timeout(1200)

                    # Expande (clica) cada assembleia dentro da janela de
                    # LOOKBACK_DAYS -- e' so' depois desse clique que o nome
                    # dos documentos (Edital/Ata) aparece no DOM (ver
                    # docstring do modulo). A data ja' aparece no cabecalho
                    # do gatilho fechado, entao filtramos ANTES de clicar
                    # pra nao gastar tempo expandindo historico velho.
                    painel = page.locator('[role="tabpanel"]:not([hidden])')
                    gatilhos = painel.get_by_role("button").filter(has_text=_DATE_RE)
                    for i in range(gatilhos.count()):
                        gatilho = gatilhos.nth(i)
                        try:
                            cabecalho = gatilho.inner_text()
                        except Exception:  # noqa: BLE001
                            continue
                        m = _DATE_RE.search(cabecalho)
                        if not m:
                            continue
                        dia, mes, ano = m.groups()
                        try:
                            data_assembleia = brt_to_utc(int(ano), int(mes), int(dia))
                        except ValueError:
                            continue
                        if data_assembleia < cutoff:
                            continue
                        try:
                            gatilho.click(timeout=4000)
                            page.wait_for_timeout(250)
                        except Exception as e:  # noqa: BLE001
                            logger.warning(
                                "vortx: falha expandindo assembleia %s (operacao %s): %s", cabecalho, op_id, e
                            )
                            continue

                    html = page.content()
                    last_html = html
                except Exception as e:  # noqa: BLE001
                    logger.warning("vortx: falha abrindo operacao %s: %s", op_id, e)
                    continue

                documentos = _extrair_assembleias_do_painel(html, op_id)
                if not documentos:
                    continue

                for doc in documentos:
                    dia, mes, ano = doc["data"]
                    try:
                        published = brt_to_utc(int(ano), int(mes), int(dia))
                    except ValueError:
                        published = None

                    nome_arquivo = doc["nome_arquivo"]
                    doc_url = f"{S3_DOCUMENTOS_BASE}/{quote(nome_arquivo)}"
                    tipo_doc = doc["tipo_doc"] or "Documento"

                    out.append(
                        RawArticle(
                            url=doc_url,
                            title=f"{apelido} — {tipo_doc} — {nome_arquivo}",
                            snippet=f"Vórtx — Assembleias — {doc['cabecalho']}",
                            published_at=published,
                            article_type="assembleia",
                        )
                    )
        finally:
            browser.close()

    if not out and last_html:
        dump_debug_html("vortx", last_html)

    return out
