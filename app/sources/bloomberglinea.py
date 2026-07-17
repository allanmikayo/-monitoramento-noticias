"""Bloomberg Línea Brasil — seções Negócios, Mercados, Agro e Saúde
(bloomberglinea.com.br). Não tem feed RSS público, mas a página de cada
seção é renderizada no servidor (Arc Publishing/Fusion, mesma plataforma
usada por vários grandes jornais) -- os links das matérias já vêm prontos
no HTML, sem precisar de JavaScript/Playwright. Usa `parse_html_listing()`,
igual `canalenergia.py`.

Uma fonte só (`fetch`) serve as 4 seções -- cada uma é cadastrada em
`config.py` com a mesma `scraper_module` e uma `url` diferente.

Os links de matéria seguem o padrão `/<secao>/<slug-longo-da-materia>/`
(ex.: `/negocios/bayer-enfrenta-resistencia-de-juiz-dos-eua-a-acordo-de-
us-725-bi-do-roundup/`), enquanto links de menu/categoria são bem mais
curtos (ex.: `/negocios/linha-executiva/`, `/mercados/cotacoes/`). Exigir
um slug de pelo menos 20 caracteres depois da seção separa as matérias
de verdade dos links de navegação, sem precisar listar todas as seções
manualmente."""
from __future__ import annotations

from .base import RawArticle, parse_html_listing


def fetch(url: str) -> list[RawArticle]:
    return parse_html_listing(
        url,
        href_pattern=r"bloomberglinea\.com\.br/[a-z-]+/[a-z0-9][a-z0-9-]{20,}/?(?:$|[?#])",
        article_type="news",
        base_url="https://www.bloomberglinea.com.br",
    )
