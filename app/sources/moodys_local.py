"""Moody's Local Brasil — Ações de Rating / Relatórios do Emissor / Relatórios
Setoriais. As 3 páginas compartilham o mesmo template WordPress: uma tabela
"Data | Título" com um plugin de listagem (DataTables) por trás.

Precisa de navegador real (Playwright), confirmado em uso real:
1. As linhas só existem no DOM depois do JavaScript rodar.
2. A tabela pagina (~25 linhas por página). Ordenar por data desc (clicando
   no cabeçalho "Fecha") já funciona -- confirmado pelo usuário vendo a
   página ao vivo -- mas isso sozinho só resolve a ORDEM, não a
   QUANTIDADE: ainda pegávamos só a 1ª página (25 mais recentes). Por isso
   agora, além de ordenar, clicamos em "Próximo" e acumulamos várias
   páginas até cobrir a janela de interesse (config.CLEANUP_MAX_AGE_HOURS)
   ou até não haver mais próxima página."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from .. import config
from .base import RawArticle, brt_to_utc, dump_debug_html, fetch_rendered_html  # noqa: F401 (fetch_rendered_html mantido p/ compat)

_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")

_NAV_PATH_HINTS = (
    "/setores/", "/onde-estamos", "/nossa-equipe", "/relatorios/research/",
    "/relatorios/metodologias", "/relatorios/acoes-de-rating/", "moodyslocal.com",
)

_MAX_AGE = timedelta(hours=config.CLEANUP_MAX_AGE_HOURS)
_MAX_PAGES = 8  # trava de segurança -- nunca pagina mais que isso numa varredura


def _parse_date(text: str) -> datetime | None:
    """A tabela só traz a data (sem hora) -- usa `brt_to_utc` com o padrão
    de meio-dia pra garantir que o dia calendário exibido de volta em
    horário de Brasília seja sempre o mesmo dia informado pela fonte (ver
    `base.brt_to_utc`, bug corrigido em 17/07/2026)."""
    m = _DATE_RE.search(text)
    if not m:
        return None
    try:
        d = dtparser.parse(m.group(1), dayfirst=True)
        return brt_to_utc(d.year, d.month, d.day)
    except (ValueError, TypeError):
        return None


def _maximizar_itens_por_pagina(page) -> None:
    for sel in [
        "select[name='length']", ".dataTables_length select", "select[id*='length']",
        "select[aria-label*='entries']", "select[aria-label*='itens']",
    ]:
        el = page.query_selector(sel)
        if not el:
            continue
        try:
            options = el.eval_on_selector_all("option", "els => els.map(e => e.value)")
        except Exception:
            options = []
        alvo = None
        if "-1" in options:
            alvo = "-1"
        else:
            numericos = sorted((int(o) for o in options if o.lstrip("-").isdigit()), reverse=True)
            if numericos:
                alvo = str(numericos[0])
        if alvo:
            try:
                el.select_option(value=alvo)
                page.wait_for_timeout(1500)
            except Exception:
                pass
        return


def _primeira_data_visivel(page) -> str:
    try:
        return page.eval_on_selector("table tbody tr:first-child", "el => el.innerText || ''") or ""
    except Exception:
        return ""


def _ordenar_por_data_desc(page) -> None:
    header = None
    for sel in ["th:has-text('Data')", "th:has-text('Fecha')", "table thead th:first-child"]:
        try:
            el = page.query_selector(sel)
        except Exception:
            el = None
        if el:
            header = el
            break
    if header is None:
        return

    for _ in range(2):
        antes = _primeira_data_visivel(page)
        try:
            header.click()
        except Exception:
            return
        page.wait_for_timeout(1200)
        depois = _primeira_data_visivel(page)

        d_antes = _parse_date(antes)
        d_depois = _parse_date(depois)
        if d_depois and d_antes and d_depois >= d_antes:
            try:
                segunda = page.eval_on_selector("table tbody tr:nth-child(2)", "el => el.innerText || ''") or ""
            except Exception:
                segunda = ""
            d_segunda = _parse_date(segunda)
            if d_depois and d_segunda and d_depois >= d_segunda:
                return


def _clicar_proxima_pagina(page) -> bool:
    """Tenta ir para a próxima página da tabela. Retorna True se clicou."""
    for sel in [
        ".paginate_button.next:not(.disabled)",
        "a.paginate_button.next",
        "a:has-text('Próximo'):not(.disabled)",
        "a:has-text('Proximo'):not(.disabled)",
        "a:has-text('Next'):not(.disabled)",
        "button:has-text('Próximo'):not([disabled])",
        "li.next:not(.disabled) a",
    ]:
        try:
            el = page.query_selector(sel)
        except Exception:
            el = None
        if not el:
            continue
        try:
            classe = (el.get_attribute("class") or "")
            if "disabled" in classe:
                continue
            el.click()
            page.wait_for_timeout(1200)
            return True
        except Exception:
            continue
    return False


def _from_table_rows(soup: BeautifulSoup) -> list[RawArticle]:
    out: list[RawArticle] = []
    for tr in soup.find_all("tr"):
        # Estrutura real confirmada por Allan ao vivo (16/07/2026) na página
        # "Ações de Rating" (wpDataTable): coluna de data tem a classe
        # "column-rating_action_post_date" e a de título/link tem
        # "column-rating_action_title_with_link_to_post" -- usa essas
        # classes exatas quando presentes (mais preciso que adivinhar por
        # regex na linha toda, e garante que o link salvo é o <a href> real
        # da matéria, não uma URL sintética).
        td_data = tr.select_one("td.column-rating_action_post_date")
        td_titulo = tr.select_one("td.column-rating_action_title_with_link_to_post")
        if td_data is not None and td_titulo is not None:
            a = td_titulo.find("a", href=True)
            if a:
                title = a.get_text(" ", strip=True)
                href = a["href"]
                published = _parse_date(td_data.get_text(" ", strip=True))
                if title and href.startswith("http"):
                    out.append(RawArticle(url=href, title=title, published_at=published, article_type="rating_action"))
            continue

        # Relatórios do Emissor / Relatórios Setoriais usam o mesmo template
        # wpDataTable mas provavelmente outras classes de coluna (ainda não
        # confirmadas ao vivo) -- fallback genérico: qualquer <a> na linha +
        # data por regex no texto inteiro da linha, igual antes.
        a = tr.find("a", href=True)
        if not a:
            continue
        row_text = tr.get_text(" ", strip=True)
        published = _parse_date(row_text)
        title = a.get_text(" ", strip=True)
        href = a["href"]
        if not title or not href.startswith("http"):
            continue
        out.append(RawArticle(url=href, title=title, published_at=published, article_type="rating_action"))
    return out


def _from_link_fallback(soup: BeautifulSoup) -> list[RawArticle]:
    out: list[RawArticle] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(" ", strip=True)
        if len(title) < 20 or not href.startswith("https://moodyslocal.com.br/"):
            continue
        if href.rstrip("/").endswith(_NAV_PATH_HINTS) or "/setores/" in href:
            continue
        parent = a.find_parent(["li", "div", "p", "tr"]) or a
        published = _parse_date(parent.get_text(" ", strip=True))
        out.append(RawArticle(url=href, title=title, published_at=published, article_type="research"))
    return out


def _recent_enough(a: RawArticle, cutoff: datetime) -> bool:
    return a.published_at is None or a.published_at >= cutoff


def _slug_from_url(url: str) -> str:
    for slug, hint in (("acoes_rating", "acoes-de-rating"), ("emissor", "relatorios-do-emissor"), ("setoriais", "relatorios-setoriais")):
        if hint in url:
            return f"moodys_{slug}"
    return "moodys_outros"


def fetch(url: str) -> list[RawArticle]:
    from playwright.sync_api import sync_playwright

    from .base import DEFAULT_HEADERS, USER_AGENT  # noqa: F401

    cutoff = datetime.now(timezone.utc) - _MAX_AGE
    seen: set[str] = set()
    collected: list[RawArticle] = []
    last_html = ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            context = browser.new_context(
                user_agent=USER_AGENT, viewport={"width": 1280, "height": 900}, locale="pt-BR",
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)

            _maximizar_itens_por_pagina(page)
            _ordenar_por_data_desc(page)

            for page_num in range(1, _MAX_PAGES + 1):
                html = page.content()
                last_html = html
                soup = BeautifulSoup(html, "lxml")
                page_rows = _from_table_rows(soup)
                if not page_rows:
                    page_rows = _from_link_fallback(soup)

                new_on_this_page = 0
                oldest_on_page: datetime | None = None
                for r in page_rows:
                    if r.published_at and (oldest_on_page is None or r.published_at < oldest_on_page):
                        oldest_on_page = r.published_at
                    if r.url in seen:
                        continue
                    seen.add(r.url)
                    collected.append(r)
                    new_on_this_page += 1

                # Para de paginar se: não achou nada de novo nesta página (evita
                # loop se "proximo" nao mudar o conteudo), ou se a linha mais
                # antiga desta pagina ja passou do corte de recencia.
                if new_on_this_page == 0:
                    break
                if oldest_on_page and oldest_on_page < cutoff:
                    break
                if not _clicar_proxima_pagina(page):
                    break
        finally:
            browser.close()

    recentes = [a for a in collected if _recent_enough(a, cutoff)]
    if collected and not recentes:
        dump_debug_html(_slug_from_url(url), last_html)
    elif not collected:
        dump_debug_html(_slug_from_url(url), last_html)
    return recentes
