"""S&P Global Ratings Brasil — página "Press Releases"
(brazil.ratings.spglobal.com/ratings/pt/regulatory/press-releases).

Allan pediu pra trocar de página (16/07/2026): a antiga ("ratings-
actions") tem uma tabela detalhada de 10 colunas (classe, tipo de rating,
rating anterior/atual etc.) que não estava sendo encontrada de forma
confiável. A "press-releases" é mais simples -- só data + título/link -- e
Allan confirmou ao vivo o seletor do filtro de período ("Últimos 12
Meses"), o botão "Atualizar" e a paginação (mostra "1-25 de N itens",
seta ">" pra próxima página, `aria-label="Next page"`).

Site é uma SPA (conteúdo só existe depois do JS rodar) -- precisa de
Playwright, igual Moody's/Fitch/CVM.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from .. import config
from .base import RawArticle, brt_to_utc, dump_debug_html, dump_debug_screenshot

logger = logging.getLogger(__name__)

BASE_URL = "https://brazil.ratings.spglobal.com"
_MAX_AGE = timedelta(hours=config.CLEANUP_MAX_AGE_HOURS)
_MAX_PAGES = 15  # a 25/pagina cobre ate 375 itens -- de sobra p/ os 45 dias de folga da limpeza

_MESES_EN = {  # pagina usa abreviacao em ingles direto (Jul, Jun...), nao precisa mapear PT->EN
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_RE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")


def _dismiss_cookie_banner(page) -> None:
    for sel in [
        "#onetrust-accept-btn-handler",
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
        "button:has-text('Aceitar todos')",
        "button:has-text('Aceitar')",
        "#accept-cookies",
        ".cookie-accept",
        "[data-testid='cookie-accept']",
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                page.wait_for_timeout(600)
                return
        except Exception:
            continue


def _clicar_atualizar(page) -> None:
    for sel in ["[data-testid='filterUpdate']", "a.button--red", "a:has-text('Atualizar')", "button:has-text('Atualizar')"]:
        try:
            btn = page.query_selector(sel)
            if btn:
                btn.click()
                page.wait_for_timeout(3000)
                return
        except Exception:
            continue


def _selecionar_ultimos_12_meses(page) -> None:
    """Abre o dropdown de período e seleciona "Últimos 12 Meses".

    BUG CORRIGIDO (17/07/2026): a primeira versão clicava em
    `[data-testid="criteria-dropdown-title"]` -- esse é só o <span> que
    MOSTRA o texto da opção selecionada, não é clicável de verdade. O
    script de referência do Allan (RatingsAction/S&P/coletar_ratings_sp.py,
    função `_filtro_p2`, PROVADO funcionando) confirma que o elemento que
    de fato abre o painel é `[data-testid="criteria-dropdown"]` (sem
    "-title"). Com o seletor errado o dropdown nunca abria, a opção nunca
    era trocada, e a busca ficava presa no filtro padrão da página --
    explica o found=0 mesmo depois do fix de cookie banner.

    Ainda assim o found=0 persistiu depois desse fix (confirmado pelo
    Allan em 17/07/2026) -- por isso agora espera explicitamente o
    dropdown existir (`wait_for_selector`, não só `query_selector`, que
    devolve None silenciosamente se o elemento ainda não tiver sido
    renderizado) e dá mais tempo pro painel abrir antes de procurar a
    opção."""
    try:
        page.wait_for_selector("[data-testid='criteria-dropdown']", timeout=10000)
    except Exception:
        logger.warning("spglobal: dropdown de periodo nao apareceu em 10s")

    dropdown = page.query_selector("[data-testid='criteria-dropdown']")
    if dropdown:
        try:
            dropdown.click()
            page.wait_for_timeout(1200)
        except Exception as e:
            logger.warning("spglobal: falha ao clicar no dropdown de periodo: %s", e)
    else:
        logger.warning("spglobal: dropdown de periodo (criteria-dropdown) nao encontrado")

    clicou_opcao = False
    for sel_opcao in [
        "label:has-text('Últimos 12 Meses')",
        "text=Últimos 12 Meses",
        "li:has-text('Últimos 12 Meses')",
        "[role='option']:has-text('Últimos 12 Meses')",
        "input[type='radio'][value='365']",
    ]:
        try:
            opt = page.query_selector(sel_opcao)
            if opt:
                opt.click()
                page.wait_for_timeout(600)
                clicou_opcao = True
                break
        except Exception:
            continue
    if not clicou_opcao:
        logger.warning("spglobal: opcao 'Ultimos 12 Meses' nao encontrada no painel aberto")

    _clicar_atualizar(page)


def _clicar_proxima_pagina(page) -> bool:
    """Seta '>' confirmada por Allan: `aria-label="Next page" rel="next"`,
    fica `aria-disabled="true"` quando não há mais páginas."""
    for sel in ["a[aria-label='Next page']", "a[rel='next']"]:
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            disabled = (el.get_attribute("aria-disabled") or "").lower()
            if disabled == "true":
                return False
            el.click()
            page.wait_for_timeout(2000)
            return True
        except Exception:
            continue
    return False


def _parse_data(texto: str) -> datetime | None:
    """A coluna de data da press-releases mostra o horário com o sufixo
    "BRT" explícito (ex.: "10-Jul-2026 17:57 BRT") -- já é horário de
    Brasília, não UTC. Usa `brt_to_utc` pra converter de verdade (bug
    corrigido em 17/07/2026, ver `base.brt_to_utc`)."""
    m = _DATE_RE.search(texto or "")
    if not m:
        return None
    dia, mes_abrev, ano, hh, mm = m.groups()
    mes = _MESES_EN.get(mes_abrev.lower())
    if not mes:
        return None
    try:
        if hh and mm:
            return brt_to_utc(int(ano), mes, int(dia), int(hh), int(mm))
        return brt_to_utc(int(ano), mes, int(dia))
    except ValueError:
        return None


def _parse_tabela(soup: BeautifulSoup) -> list[RawArticle]:
    """Estrutura confirmada ao vivo: `.table-module__content` >
    `.table-module__row` > 2 `.table-module__column` (1a = data em
    "10-Jul-2026 17:57 BRT", 2a = `<p><a href>título</a></p>`)."""
    out: list[RawArticle] = []
    rows = soup.select(".table-module__content .table-module__row")
    for row in rows:
        cols = row.select(".table-module__column")
        if len(cols) < 2:
            continue
        data_txt = cols[0].get_text(" ", strip=True)
        a = cols[1].find("a", href=True)
        if not a:
            continue
        titulo = a.get_text(" ", strip=True)
        href = a["href"]
        if href.startswith("/"):
            href = BASE_URL + href
        if not titulo:
            continue
        published = _parse_data(data_txt)
        out.append(RawArticle(url=href, title=titulo, published_at=published, article_type="rating_action"))
    return out


def _parse_fallback_links(soup: BeautifulSoup) -> list[RawArticle]:
    """Rede de segurança do script de referência (Allan, `_extrair_lista_p2`
    Estratégia 2): se o container principal não for encontrado (mudança de
    layout, timing etc.), procura direto por qualquer link de artigo
    (`/article/` no href) na página inteira, ignorando links óbvios de
    navegação."""
    out: list[RawArticle] = []
    ignorar = ("/our-", "/contact", "/events", "/disclaimers")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/article/" not in href or any(x in href for x in ignorar):
            continue
        titulo = a.get_text(" ", strip=True)
        if not titulo or len(titulo) < 5:
            continue
        url = href if href.startswith("http") else BASE_URL + href
        row = a.find_parent(["div", "li", "tr"])
        data_txt = row.get_text(" ", strip=True) if row else ""
        published = _parse_data(data_txt)
        out.append(RawArticle(url=url, title=titulo, published_at=published, article_type="rating_action"))
    return out


def fetch(url: str) -> list[RawArticle]:
    from playwright.sync_api import sync_playwright

    from .base import USER_AGENT

    cutoff = datetime.now(timezone.utc) - _MAX_AGE
    seen: set[str] = set()
    coletados: list[RawArticle] = []
    last_html = ""

    with sync_playwright() as pw:
        # ACHADO NOVO (17/07/2026): o found=0 NUNCA foi o seletor do
        # dropdown -- é a S&P bloqueando a requisição na borda (Akamai
        # Bot Manager), devolvendo uma página de erro ("Access Denied",
        # referrer errors.edgesuite.net) em vez do site de verdade. O
        # debug_spglobal.html/.png confirmam isso ao vivo (rodado por
        # Allan, não no sandbox). O Chromium embutido do Playwright tem
        # uma "impressão digital" (TLS/CDP) bem conhecida por ferramentas
        # anti-bot -- tenta primeiro o Chrome de verdade instalado na
        # máquina (`channel="chrome"`, precisa de `playwright install
        # chrome` uma vez), que se parece muito mais com um navegador
        # comum; se não estiver instalado, cai pro Chromium embutido
        # (mesmo comportamento de antes).
        try:
            browser = pw.chromium.launch(
                channel="chrome", headless=True, args=["--disable-blink-features=AutomationControlled"]
            )
        except Exception as e:
            logger.warning("spglobal: Chrome real nao disponivel (%s) -- usando Chromium embutido", e)
            browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900}, locale="pt-BR")
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3500)
            _dismiss_cookie_banner(page)
            page.wait_for_timeout(500)
            _selecionar_ultimos_12_meses(page)
            _dismiss_cookie_banner(page)
            page.wait_for_timeout(1500)

            for page_num in range(1, _MAX_PAGES + 1):
                html = page.content()
                last_html = html
                soup = BeautifulSoup(html, "lxml")
                linhas = _parse_tabela(soup)
                if not linhas:
                    linhas = _parse_fallback_links(soup)

                novos = 0
                oldest_on_page = None
                for art in linhas:
                    if art.published_at and (oldest_on_page is None or art.published_at < oldest_on_page):
                        oldest_on_page = art.published_at
                    if art.url in seen:
                        continue
                    seen.add(art.url)
                    coletados.append(art)
                    novos += 1

                if novos == 0 and page_num > 1:
                    break
                if oldest_on_page and oldest_on_page < cutoff:
                    break
                if not _clicar_proxima_pagina(page):
                    break

            if not coletados:
                # Screenshot alem do HTML -- mesmo padrao de diagnostico do
                # script de referencia do Allan pra Fitch. Se o dropdown nao
                # abriu ou a opcao nao foi selecionada, a imagem mostra isso
                # de forma muito mais rapida do que ler HTML renderizado.
                dump_debug_screenshot("spglobal", page)
        finally:
            browser.close()

    if not coletados:
        dump_debug_html("spglobal", last_html)
        return []

    recentes = [a for a in coletados if a.published_at is None or a.published_at >= cutoff]
    return recentes
