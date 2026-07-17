"""Fitch Ratings — Rating Action Commentary (filtro Portuguese/BR).

A página de busca (fitchratings.com/search) é uma SPA -- só monta a lista
via JavaScript, precisa de Playwright (igual S&P/Moody's). A URL já vem
com os filtros certos (dateValue=lastWeek, language=Portuguese,
reportType=Rating Action Commentary) definidos em app/config.py --
dateValue=lastWeek é o valor confirmado pelo script de referência do
Allan (RatingsAction/FitchRatings/coletar_ratings_fitch.py), não um
chute.

Diferente do script de referência em RatingsAction/FitchRatings (que abre
cada notícia e extrai a tabela de ratings detalhada via ReactTable, para
gerar um Excel completo), aqui só precisamos de título + data + link de
cada item da LISTAGEM -- suficiente para um feed de notícias, e bem mais
rápido (não abre uma aba por artigo a cada scan de 5min).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup

from .base import RawArticle, brt_to_utc, dump_debug_html

logger = logging.getLogger(__name__)

BASE_URL = "https://www.fitchratings.com"
MAX_PAGES = 6

_MESES_EN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})")


def _dismiss_cookie_banner(page) -> None:
    for sel in [
        "#onetrust-accept-btn-handler",
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
        "button:has-text('Aceitar')",
        "#accept-cookies",
        ".cookie-accept",
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def _parse_data(texto: str) -> datetime | None:
    """A listagem só traz a data (sem hora) -- usa `brt_to_utc` com o
    padrão de meio-dia pra garantir que o dia calendário exibido de volta
    em horário de Brasília seja sempre o mesmo dia informado pela fonte
    (ver `base.brt_to_utc`, bug corrigido em 17/07/2026)."""
    m = _DATE_RE.search(texto or "")
    if not m:
        return None
    dia, mes_abrev, ano = m.groups()
    mes = _MESES_EN.get(mes_abrev.lower()[:3])
    if not mes:
        return None
    try:
        return brt_to_utc(int(ano), mes, int(dia))
    except ValueError:
        return None


def _clicar_proxima_pagina(page) -> bool:
    """Mesma lógica do script de referência do Allan (PROVADO funcionando):
    `a[title="Go to next page"]`, checa se o <li> pai tem classe "disabled"."""
    el = page.query_selector('a[title="Go to next page"]')
    if not el:
        return False
    try:
        li_class = el.evaluate("el => el.closest('li') ? el.closest('li').className : ''")
        if "disabled" in (li_class or "").lower():
            return False
        el.click()
        page.wait_for_timeout(3500)
        return True
    except Exception:
        return False


def _parse_listagem(soup: BeautifulSoup) -> list[RawArticle]:
    out: list[RawArticle] = []
    linhas = soup.select(".frw-column__wrapper--1.frw-article-data") or soup.select(".frw-article-data")
    for linha in linhas:
        a = linha.select_one(".frw-article-data--title a[href]") or linha.select_one("a[href]")
        if not a:
            continue
        href = a.get("href") or ""
        if href.startswith("/"):
            href = BASE_URL + href
        titulo = (a.get("aria-label") or a.get_text(" ", strip=True) or "").strip()
        if not titulo or not href:
            continue

        data_el = linha.select_one(".frw-date")
        published = _parse_data(data_el.get_text(" ", strip=True) if data_el else linha.get_text(" ", strip=True))

        out.append(
            RawArticle(
                url=href,
                title=titulo,
                published_at=published,
                article_type="rating_action",
            )
        )
    return out


def fetch(url: str) -> list[RawArticle]:
    from playwright.sync_api import sync_playwright

    from .base import USER_AGENT

    coletados: list[RawArticle] = []
    seen: set[str] = set()
    last_html = ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900}, locale="pt-BR")
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            _dismiss_cookie_banner(page)
            page.wait_for_timeout(1000)

            # Mesmo diagnóstico do script de referência: espera especificamente
            # pelos links de artigo aparecerem (React renderiza depois do HTML
            # inicial) em vez de confiar só num wait_for_timeout fixo -- se o
            # conteúdo demorar mais que o timeout fixo antigo (4s), a extração
            # rodava em cima de uma página ainda vazia.
            try:
                page.wait_for_selector(".frw-article-data--title a[href]", timeout=20000)
            except Exception:
                logger.warning("fitch.fetch(): conteudo nao apareceu em 20s -- pagina pode ter mudado ou filtro invalido")

            for page_num in range(1, MAX_PAGES + 1):
                html = page.content()
                last_html = html
                soup = BeautifulSoup(html, "lxml")
                linhas = _parse_listagem(soup)
                novos_nesta_pagina = 0
                for art in linhas:
                    if art.url not in seen:
                        seen.add(art.url)
                        coletados.append(art)
                        novos_nesta_pagina += 1
                if novos_nesta_pagina == 0:
                    break
                if not _clicar_proxima_pagina(page):
                    break
        finally:
            browser.close()

    if not coletados:
        dump_debug_html("fitch", last_html)

    return coletados
