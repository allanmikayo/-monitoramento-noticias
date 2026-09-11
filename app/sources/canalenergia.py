"""CanalEnergia — não tem feed RSS público (/feed/ não resolve), mas a
página "/noticias" lista as matérias recentes em HTML simples (sem JS),
com links para "/noticias/{id}/{slug}" e data por extenso ("13 de julho de
2026") ao lado de cada uma. Usa parse_html_listing() em vez de um scraper
dedicado."""
from __future__ import annotations

from .base import RawArticle, parse_html_listing


def fetch(url: str) -> list[RawArticle]:
    return parse_html_listing(
        url,
        href_pattern=r"/noticias/\d+/",
        article_type="news",
        base_url="https://www.canalenergia.com.br",
        # VIA NAVEGADOR (11/09/2026). O site passou a responder HTTP 403 ao
        # GET, mesmo com o `curl_cffi` imitando o fingerprint TLS do Chrome
        # -- a proteção olha mais do que o handshake. Com o Chromium do
        # Playwright a página carrega normalmente. Custa alguns segundos por
        # rodada; o teto por fonte em app/pipeline.py garante que isso não
        # vira problema das outras fontes se o site piorar de novo.
        renderizado=True,
    )
