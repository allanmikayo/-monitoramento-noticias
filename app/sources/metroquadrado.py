"""Metro Quadrado (metroquadrado.com) — mercado imobiliário brasileiro
(Brazil Journal). Relevante pro setor de incorporadoras/imobiliário da
cobertura (cita diretamente emissores como Eztec, MRV, Melnick etc. na
própria home). Sem feed RSS público, mas a home é renderizada no servidor
-- os links das matérias já vêm prontos no HTML. Usa `parse_html_listing()`,
igual `canalenergia.py`/`bloomberglinea.py`.

Links de matéria seguem `/<categoria>/<slug-longo>` (ex.:
`/residencial/na-eztec-distratos-e-estoque-pronto-acendem-sinal-amarelo`).
Exclui `/brands/...` (conteúdo patrocinado, marcado como "Um conteúdo X"
no site -- não é matéria jornalística independente) e `/tag/...` (páginas
de categoria, não matéria)."""
from __future__ import annotations

from .base import RawArticle, parse_html_listing


def fetch(url: str) -> list[RawArticle]:
    return parse_html_listing(
        url,
        href_pattern=r"metroquadrado\.com/(?!brands/|tag/)[a-z-]+/[a-z0-9][a-z0-9-]{15,}(?:$|[/?#])",
        article_type="news",
        base_url="https://metroquadrado.com",
    )
