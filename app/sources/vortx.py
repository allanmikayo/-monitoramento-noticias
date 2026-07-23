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
   Vórtx" (confirmado ao vivo: "SUZANO - DEB | Vórtx") -- só segue se
   `TIPO == DEB` (CRI/CRA ficam de fora nesta primeira versão, mesma
   decisão do Allan pra Oliveira Trust -- ver oliveiratrust.py). Clica na
   aba "Assembleias", espera o painel renderizar e lê o texto. Se vier
   "Nenhuma assembleia encontrada", pula. Senão, tenta achar linhas/datas
   dentro do painel ativo.

ATENÇÃO -- calibração pendente: em nenhuma das operações que consegui
testar ao vivo (Suzano) havia assembleia cadastrada, então NÃO tenho HTML
real de um painel de Assembleias COM conteúdo pra calibrar o parser das
linhas (datas, links). O parser abaixo é propositalmente genérico
(procura qualquer link com data de padrão dd/mm/aaaa dentro do painel
ativo) -- se vier "found" muito baixo ou os títulos estranhos no primeiro
uso real, me manda o HTML de debug (`data/debug_vortx.html`, salvo
automaticamente quando uma operação COM assembleia não gerar nenhuma linha
reconhecida) que eu calibro certinho, igual foi feito com CVM RAD/Moody's/
Fitch.

Custo de rede/tempo: ~96 buscas (rápidas, HTTP puro) + 1 navegação
Playwright por operação de debênture encontrada (pode ser bastante --
Suzano sozinha tinha ~14 séries). Cada abertura de página com Playwright
leva alguns segundos; se isso deixar a varredura lenta demais pro
intervalo de 5 minutos, me avise que a gente ajusta (reduzir frequência só
desta fonte, ou cachear operações já vistas sem assembleia por mais
tempo)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

from bs4 import BeautifulSoup

from .base import DEFAULT_HEADERS, RawArticle, USER_AGENT, brt_to_utc, dump_debug_html, get, load_coverage_names

logger = logging.getLogger(__name__)

BASE_SITE = "https://www.vortx.com.br"
SEARCH_URL = f"{BASE_SITE}/investidor/dcm"
OPERACAO_URL = f"{BASE_SITE}/investidor/dcm/operacao"

_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")


_OP_ID_RE = re.compile(r"dcm/operacao\?id=(\d+)")
MAX_SEARCH_PAGES = 5  # cada pagina tem 10 linhas -- 5 paginas = ate 50 operacoes por empresa (generoso)


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
    `hidden`) procurando linhas com data dd/mm/aaaa. Formato genérico
    (ver aviso de calibração pendente no docstring do módulo)."""
    soup = BeautifulSoup(html, "lxml")
    painel = soup.select_one('[role="tabpanel"]:not([hidden])')
    if painel is None:
        painel = soup  # fallback -- melhor varrer a pagina toda que nao achar nada

    texto_painel = painel.get_text(" ", strip=True)
    if "nenhuma assembleia" in texto_painel.lower():
        return []

    out: list[dict] = []
    linhas = painel.find_all(["tr", "li", "div"], recursive=True)
    vistos: set[str] = set()
    for linha in linhas:
        texto = linha.get_text(" ", strip=True)
        m = _DATE_RE.search(texto)
        if not m or texto in vistos:
            continue
        vistos.add(texto)
        link = linha.find("a", href=True)
        out.append({
            "texto": texto[:300],
            "data": m.groups(),
            "href": link.get("href") if link else None,
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

                    # Filtra fora CRI/CRA aqui (nao na busca -- ver docstring
                    # de `_buscar_ids_operacao`): o <title> da pagina segue o
                    # padrao "APELIDO - TIPO | Vortx" (confirmado ao vivo:
                    # "SUZANO - DEB | Vórtx"). So' segue se for debenture.
                    titulo_pagina = page.title()
                    apelido = titulo_pagina.split(" - ")[0].strip() if " - " in titulo_pagina else titulo_pagina
                    if " - DEB " not in titulo_pagina:
                        continue

                    aba = page.get_by_role("tab", name="Assembleias")
                    aba.click(timeout=8000)
                    page.wait_for_timeout(1200)
                    html = page.content()
                    last_html = html
                except Exception as e:  # noqa: BLE001
                    logger.warning("vortx: falha abrindo operacao %s: %s", op_id, e)
                    continue

                linhas = _extrair_assembleias_do_painel(html, op_id)
                if not linhas:
                    continue

                for linha in linhas:
                    dia, mes, ano = linha["data"]
                    try:
                        published = brt_to_utc(int(ano), int(mes), int(dia))
                    except ValueError:
                        published = None

                    href = linha["href"]
                    if href and href.startswith("/"):
                        href = BASE_SITE + href
                    doc_url = href or f"{op_url}#assembleia-{dia}{mes}{ano}"

                    out.append(
                        RawArticle(
                            url=doc_url,
                            title=f"{apelido} — Assembleia — {linha['texto'][:150]}",
                            snippet="Vórtx — Assembleias",
                            published_at=published,
                            article_type="assembleia",
                        )
                    )
        finally:
            browser.close()

    if not out and last_html:
        dump_debug_html("vortx", last_html)

    return out
