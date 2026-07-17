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
    )
