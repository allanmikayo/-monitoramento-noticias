"""Agência Infra — feed RSS de últimas notícias (infraestrutura, energia,
transporte, saneamento -- boa parte do universo de CRI/CRA de infra)."""
from __future__ import annotations

from .base import RawArticle, parse_rss


def fetch(url: str) -> list[RawArticle]:
    return parse_rss(url, article_type="news")
