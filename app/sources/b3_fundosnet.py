"""B3 Fundos.NET (fnet.bmfbovespa.com.br) — comunicados/fatos relevantes de
FIDCs e securitizadoras (essencial para CRI/CRA). Também é uma busca
server-driven com parâmetros específicos por documento/entidade; precisa
de investigação da API real usada pelo front-end (devtools → Network) ou
Playwright.

Desabilitada por padrão. Ver CLAUDE.md, seção "Próximos passos".
"""
from __future__ import annotations

import logging

from .base import RawArticle

logger = logging.getLogger(__name__)


def fetch(url: str) -> list[RawArticle]:
    logger.warning("b3_fundosnet.fetch(): scraper ainda não implementado — retornando vazio")
    return []
