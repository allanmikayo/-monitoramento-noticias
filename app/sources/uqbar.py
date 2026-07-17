"""Uqbar — noticias (uqbar.com.br/noticias). A página é um SPA que exige
JavaScript ("You need to enable JavaScript to run this app") — requests
simples não trazem conteúdo. Precisa de Playwright ou de localizar a API
usada pelo front-end (provavelmente um endpoint REST/GraphQL próprio).

Desabilitada por padrão — ver CLAUDE.md, seção "Próximos passos".
"""
from __future__ import annotations

import logging

from .base import RawArticle

logger = logging.getLogger(__name__)


def fetch(url: str) -> list[RawArticle]:
    logger.warning("uqbar.fetch(): scraper ainda não implementado (SPA JS) — retornando vazio")
    return []
