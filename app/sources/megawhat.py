"""MegaWhat (UOL) — feed RSS. URL não pôde ser confirmada em tempo real ao
escrever este scraper (domínio bloqueado para inspeção automática) -- usa o
padrão WordPress convencional (/feed/). Se vier com 0 resultados no painel
de diagnóstico, é sinal de que a URL do feed é outra; me avise o que
aparecer lá que eu ajusto."""
from __future__ import annotations

from .base import RawArticle, parse_rss


def fetch(url: str) -> list[RawArticle]:
    return parse_rss(url, article_type="news")
