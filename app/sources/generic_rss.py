"""Wrapper genérico para qualquer fonte que só precise de parse_rss() puro
(sem lógica extra) — evita criar um arquivo quase-vazio por fonte nova."""
from __future__ import annotations

from .base import RawArticle, parse_rss


def fetch(url: str) -> list[RawArticle]:
    return parse_rss(url, article_type="news")
