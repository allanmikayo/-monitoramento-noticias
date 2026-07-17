"""Brazil Journal — feed RSS (site principal e o vertical Infra Journal
compartilham o mesmo motor WordPress; uma URL de feed por Source)."""
from __future__ import annotations

from .base import RawArticle, parse_rss


def fetch(url: str) -> list[RawArticle]:
    return parse_rss(url, article_type="news")
