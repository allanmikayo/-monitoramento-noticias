"""InfoMoney — feed RSS (ex.: Renda Fixa). Fonte aberta, sem login."""
from __future__ import annotations

from .base import RawArticle, parse_rss


def fetch(url: str) -> list[RawArticle]:
    return parse_rss(url, article_type="news")
