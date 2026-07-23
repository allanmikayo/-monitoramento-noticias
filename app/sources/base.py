"""Utilidades compartilhadas pelos scrapers.

Usa curl_cffi (em vez de requests puro) para imitar a "impressao digital"
TLS de um navegador Chrome real. Varios sites (ex.: Moody's Local, atras
de protecao anti-robo) devolvem 403 Forbidden para requests comuns mesmo
com headers de navegador corretos -- o bloqueio acontece no handshake
TLS, nao no conteudo da requisicao. curl_cffi resolve isso; e a mesma
tecnica que o projeto de referencia (clipinator) ja usava para casos
parecidos.

Tambem oferece fetch_rendered_html(), que usa Playwright (navegador real,
headless) para paginas cujo conteudo e montado via JavaScript (SPAs) --
nesses casos um GET comum (curl_cffi/requests) so devolve a casca HTML
vazia, sem as linhas de dados. Confirmado necessario para as paginas de
acoes de rating da S&P Global e da Moody's Local (ver app/sources/
spglobal.py e moodys_local.py) -- o mesmo caso ja havia sido mapeado e
resolvido com Playwright no projeto irmao RatingsAction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BRT = ZoneInfo("America/Sao_Paulo")


def brt_to_utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    """Monta um datetime a partir de campos de data/hora em horário de
    Brasília (BRT, UTC-3) e devolve o equivalente em UTC -- correção
    (17/07/2026) do bug em que vários scrapers pegavam a hora exibida no
    site (já em horário local do Brasil -- CVM, Moody's Local, S&P Brasil
    etc.) e simplesmente rotulavam como se já fosse UTC
    (`tzinfo=timezone.utc`), sem de fato converter. Isso deixava o valor
    guardado 3h "atrasado" em relação ao UTC real; combinado com o fix de
    exibição em app.py (que agora converte UTC -> horário de Brasília
    corretamente pra mostrar no dashboard), o resultado final aparecia com
    a hora errada de novo, só que na direção oposta.

    Quando só temos a DATA (sem hora real -- ex.: Fitch e Moody's só
    trazem "12 Jul 2026" nas suas listagens, sem hora), o padrão é
    meio-dia (12h) em vez de meia-noite: meia-noite BRT vira 21h do dia
    ANTERIOR em UTC, o que faria o dia calendário exibido "andar pra
    trás" depois de converter de volta pra BRT na tela. Meio-dia BRT cai
    dentro do mesmo dia calendário nos dois fusos, então o dia exibido
    sempre bate com o dia informado pela fonte."""
    return datetime(year, month, day, hour, minute, tzinfo=BRT).astimezone(timezone.utc)

try:
    from curl_cffi import requests as _http
    _USE_CURL_CFFI = True
except ImportError:  # pragma: no cover - so acontece se a instalacao falhar
    import requests as _http  # type: ignore[no-redef]
    _USE_CURL_CFFI = False
    logger.warning(
        "curl_cffi nao disponivel -- usando requests puro (alguns sites com "
        "protecao anti-robo podem devolver 403). Rode: pip install curl_cffi"
    )

IMPERSONATE = "chrome124"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 25


@dataclass
class RawArticle:
    """Artigo cru retornado por um scraper, antes de casar keywords/empresas."""
    url: str
    title: str
    snippet: str = ""
    body: str = ""
    published_at: datetime | None = None
    article_type: str = "news"  # news | rating_action | fato_relevante | assembleia | research


def get(url: str, **kwargs):
    """GET via curl_cffi, imitando o TLS fingerprint do Chrome (cai para
    requests puro se curl_cffi nao estiver instalado). Retorna um objeto
    de resposta com a mesma interface do requests (.content, .text,
    .status_code, .raise_for_status())."""
    kwargs.setdefault("headers", DEFAULT_HEADERS)
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    if _USE_CURL_CFFI:
        kwargs.setdefault("impersonate", IMPERSONATE)
    resp = _http.get(url, **kwargs)
    resp.raise_for_status()
    return resp


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_coverage_names() -> list[str]:
    """Nomes + apelidos das empresas ATIVAS da cobertura -- usado pelas
    fontes de AGD/assembleia das securitizadoras (vortx, oliveiratrust,
    pentagono), que -- assim como o CVM RAD -- filtram por empresa da
    cobertura DENTRO do scraper (o volume total de AGDs do mercado inteiro
    é grande demais pra deixar tudo pro pipeline decidir, ver docstring de
    cvm_rad.py). Compartilhado em base.py (23/07/2026) em vez de duplicado
    em cada módulo novo -- cvm_rad.py mantém sua própria cópia local por
    enquanto pra não arriscar mexer numa fonte já calibrada sem necessidade."""
    try:
        from ..db import SessionLocal
        from ..models import Company, CompanyAlias

        with SessionLocal() as db:
            nomes: list[str] = []
            for c in db.query(Company).filter(Company.active.is_(True)).all():
                nomes.append(c.name)
            for a in db.query(CompanyAlias).all():
                if a.alias:
                    nomes.append(a.alias)
            return nomes
    except Exception as e:  # noqa: BLE001
        logger.warning("load_coverage_names: falha carregando empresas da cobertura: %s", e)
        return []


def fetch_rendered_html(
    url: str,
    *,
    wait_ms: int = 3500,
    actions: Callable[[object], None] | None = None,
    timeout_ms: int = 45000,
) -> str:
    """Carrega `url` com um navegador Chromium real (headless, via Playwright)
    e devolve o HTML já renderizado -- necessário para páginas que só montam
    o conteúdo (tabelas, listas) via JavaScript depois do carregamento
    inicial, onde um GET comum devolveria só a casca vazia.

    `actions`, se passado, recebe a Playwright `Page` já carregada para
    cliques/filtros adicionais (ex.: selecionar "últimos 7 dias" e clicar em
    "Atualizar") antes do HTML final ser capturado.

    Requer `playwright install chromium` ter sido rodado uma vez no
    ambiente (o launcher .bat cuida disso automaticamente)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 900},
                locale="pt-BR",
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(wait_ms)
            if actions:
                try:
                    actions(page)
                except Exception as e:  # noqa: BLE001
                    logger.warning("fetch_rendered_html: acao pos-carregamento falhou: %s", e)
            html = page.content()
        finally:
            browser.close()
    return html


def dump_debug_html(source_slug: str, html: str) -> None:
    """Salva o HTML renderizado em data/debug_<fonte>.html quando um scraper
    baseado em Playwright não encontra nada -- ajuda a diagnosticar depois
    (ex.: mandar o arquivo para revisão) sem precisar reproduzir o problema
    ao vivo. Sobrescreve a cada chamada (só guarda a última tentativa)."""
    try:
        from pathlib import Path

        debug_dir = Path(__file__).resolve().parent.parent.parent / "data"
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"debug_{source_slug}.html").write_text(html, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("dump_debug_html(%s) falhou: %s", source_slug, e)


def dump_debug_screenshot(source_slug: str, page) -> None:
    """Salva um screenshot da página em data/debug_<fonte>.png -- mesmo
    padrão de diagnóstico do script de referência do Allan
    (RatingsAction/FitchRatings). Útil quando o HTML sozinho não deixa
    claro o que está na tela (ex.: um painel/dropdown que devia ter
    aberto mas não abriu)."""
    try:
        from pathlib import Path

        debug_dir = Path(__file__).resolve().parent.parent.parent / "data"
        debug_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(debug_dir / f"debug_{source_slug}.png"))
    except Exception as e:  # noqa: BLE001
        logger.warning("dump_debug_screenshot(%s) falhou: %s", source_slug, e)


def parse_rss(feed_url: str, article_type: str = "news") -> list[RawArticle]:
    """Baixa e interpreta um feed RSS/Atom genérico (WordPress etc.)."""
    import feedparser
    from dateutil import parser as dtparser

    resp = get(feed_url)
    parsed = feedparser.parse(resp.content)
    out: list[RawArticle] = []
    for entry in parsed.entries:
        link = entry.get("link") or ""
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        summary = entry.get("summary", "") or entry.get("description", "")
        snippet = _strip_html(summary)[:600]

        # Prioriza os campos *_parsed que o feedparser ja normalizou (struct_time
        # em UTC) -- sao mais confiaveis que reinterpretar a string crua (ex.:
        # "pubDate") com dateutil, que pode falhar silenciosamente em formatos
        # de data incomuns e deixar published_at como None (o artigo entao so
        # aparece pelo found_at, com janela de 1h em vez da data real).
        published = None
        for key in ("published_parsed", "updated_parsed"):
            st = entry.get(key)
            if st:
                try:
                    published = datetime(*st[:6], tzinfo=timezone.utc)
                    break
                except (TypeError, ValueError):
                    continue
        if published is None:
            for key in ("published", "updated", "pubDate"):
                raw = entry.get(key)
                if raw:
                    try:
                        published = dtparser.parse(raw)
                        if published.tzinfo is None:
                            published = published.replace(tzinfo=timezone.utc)
                        break
                    except (ValueError, TypeError):
                        continue
        out.append(
            RawArticle(
                url=link,
                title=title,
                snippet=snippet,
                published_at=published,
                article_type=article_type,
            )
        )
    return out


_MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}
_DATE_PT_RE_TXT = (
    r"\b(\d{1,2})\s+de\s+("
    + "|".join(_MESES_PT.keys())
    + r")\s+de\s+(\d{4})\b"
)


def _parse_date_pt_extenso(text: str):
    """Reconhece datas por extenso em português, ex.: '13 de julho de 2026'
    (padrão comum em sites de notícia brasileiros que não usam RSS)."""
    import re as _re

    m = _re.search(_DATE_PT_RE_TXT, text or "", _re.IGNORECASE)
    if not m:
        return None
    day, month_name, year = m.groups()
    month = _MESES_PT.get(month_name.lower())
    if not month:
        return None
    try:
        return brt_to_utc(int(year), month, int(day))
    except ValueError:
        return None


def parse_html_listing(
    url: str,
    *,
    href_pattern: str,
    article_type: str = "news",
    base_url: str | None = None,
    min_title_len: int = 8,
) -> list[RawArticle]:
    """Varre uma página de listagem HTML "de verdade" (sem JS, sem RSS) --
    para sites que publicam pouco e não têm feed, mas têm uma página de
    notícias simples com links para cada matéria. `href_pattern` é um
    regex que o href precisa bater para ser considerado um link de artigo
    (evita pegar links de menu/categoria/rodapé)."""
    import re as _re

    from bs4 import BeautifulSoup

    resp = get(url)
    soup = BeautifulSoup(resp.content, "lxml")
    pat = _re.compile(href_pattern)

    out: list[RawArticle] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not pat.search(href):
            continue
        if href.startswith("/"):
            href = (base_url or "").rstrip("/") + href
        if href in seen:
            continue

        title = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        if len(title) < min_title_len:
            continue
        seen.add(href)

        block = a.find_parent(["li", "article", "div"]) or a
        published = _parse_date_pt_extenso(block.get_text(" ", strip=True))

        out.append(RawArticle(url=href, title=title, published_at=published, article_type=article_type))
    return out


def _strip_html(html: str) -> str:
    from bs4 import BeautifulSoup

    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)
