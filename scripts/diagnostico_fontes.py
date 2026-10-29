"""Diagnóstico das fontes de notícia (29/10/2026).

Roda o `fetch()` de cada fonte pedida, SEM gravar nada no banco, e imprime
o resultado como anotação do GitHub Actions (`::notice`/`::warning`) -- é o
único jeito de ler o resultado de fora, porque o sandbox onde o Claude
trabalha não alcança os sites nem baixa o log do Actions; as anotações saem
pela API do GitHub.

Para cada fonte: quantos itens vieram, os 3 primeiros títulos, quanto tempo
levou e, quando vier zero, um retrato da página (status HTTP, título,
tamanho, links que parecem matéria) para dizer se o site mudou, bloqueou ou
se o filtro do scraper é que não casa mais.

    python -m scripts.diagnostico_fontes                  # todas as habilitadas
    python -m scripts.diagnostico_fontes canalenergia cvm_rad
"""
from __future__ import annotations

import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse

from app import config


def _anotar(nivel: str, titulo: str, texto: str) -> None:
    # Anotação do Actions: quebras de linha viram %0A.
    texto = texto.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::{nivel} title={titulo[:120]}::{texto[:6000]}", flush=True)


def _retrato(url: str, renderizado: bool) -> str:
    """Como a página chega: status, título, tamanho e os padrões de link."""
    from bs4 import BeautifulSoup

    from app.sources import base

    linhas = []
    try:
        if renderizado:
            html = base.fetch_rendered_html(url)
            linhas.append(f"renderizado: {len(html)} bytes")
        else:
            resp = base.get(url)
            html = resp.text
            linhas.append(f"HTTP {getattr(resp, 'status_code', '?')}, {len(html)} bytes")
    except Exception as e:  # noqa: BLE001
        return f"falha ao abrir a página: {type(e).__name__}: {e}"
    soup = BeautifulSoup(html, "lxml")
    linhas.append("título: " + (soup.title.get_text(strip=True)[:120] if soup.title else "(sem <title>)"))
    texto = soup.get_text(" ", strip=True)
    linhas.append("início do texto: " + texto[:300])
    dominio = urlparse(url).netloc
    hrefs = [urljoin(url, a["href"]) for a in soup.find_all("a", href=True)]
    internos = [h for h in hrefs if urlparse(h).netloc.endswith(dominio.replace("www.", ""))]
    linhas.append(f"links: {len(hrefs)} no total, {len(internos)} internos, "
                  f"{sum(1 for a in soup.find_all('a', href=True) if a['href'].startswith('/'))} relativos")
    formas = Counter(re.sub(r"[a-z0-9-]{15,}", "<slug>", re.sub(r"\d+", "<n>", urlparse(h).path))
                     for h in internos)
    linhas.append("formatos de link mais comuns: " + "; ".join(f"{k} ×{v}" for k, v in formas.most_common(8)))
    longos = [h for h in internos if re.search(r"[a-z0-9-]{25,}", urlparse(h).path)][:6]
    linhas.append("exemplos de links longos: " + " | ".join(longos))
    tabelas = soup.find_all("table")
    if tabelas:
        linhas.append(f"tabelas: {len(tabelas)}; linhas na maior: {max(len(t.find_all('tr')) for t in tabelas)}")
    return "\n".join(linhas)


def _debug_salvo(slug: str) -> str:
    p = Path(__file__).resolve().parent.parent / "data" / f"debug_{slug}.html"
    if not p.exists():
        return ""
    from bs4 import BeautifulSoup

    html = p.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    tit = soup.title.get_text(strip=True)[:120] if soup.title else "(sem <title>)"
    tabelas = soup.find_all("table")
    info = f"HTML salvo pelo scraper ({len(html)} bytes), título: {tit}"
    if tabelas:
        maior = max(tabelas, key=lambda t: len(t.find_all("tr")))
        info += f"; {len(tabelas)} tabela(s), maior com {len(maior.find_all('tr'))} linhas"
        primeira = maior.find("tbody") or maior
        tr = primeira.find("tr")
        if tr:
            info += "; 1ª linha: " + tr.get_text(" | ", strip=True)[:300]
            info += "; classes das células: " + ", ".join(
                " ".join(td.get("class") or []) for td in tr.find_all(["td", "th"]))[:300]
    info += "\ntexto: " + soup.get_text(" ", strip=True)[:500]
    return info


def main(modulos: list[str]) -> int:
    import importlib

    # Sem argumento: todas as habilitadas MENOS as lentas de assembleia
    # (a Vórtx sozinha leva ~28 min e estourava o job; ver assembleias.yml).
    fontes = [s for s in config.KNOWN_SOURCES if s.get("enabled", True) is not False
              and s["scraper_module"] not in config.FONTES_LENTAS]
    if modulos:
        fontes = [s for s in config.KNOWN_SOURCES if s["scraper_module"] in modulos]
    zeradas = 0
    for s in fontes:
        mod = importlib.import_module(f"app.sources.{s['scraper_module']}")
        t0 = time.time()
        try:
            itens = mod.fetch(s["url"])
            erro = None
        except Exception as e:  # noqa: BLE001
            itens, erro = [], f"{type(e).__name__}: {e}"
        dt = time.time() - t0
        titulos = " | ".join((a.title or "")[:90] for a in itens[:3])
        datas = sorted([a.published_at for a in itens if getattr(a, "published_at", None)], reverse=True)
        resumo = (f"{len(itens)} itens em {dt:.0f}s"
                  + (f"; mais recente {datas[0]:%d/%m %H:%M}" if datas else "; nenhum com data")
                  + (f"\nERRO: {erro}" if erro else "")
                  + (f"\n{titulos}" if titulos else ""))
        if itens and not erro:
            _anotar("notice", f"OK {s['name']}", resumo)
            continue
        zeradas += 1
        slug = s["scraper_module"]
        if slug == "moodys_local":
            slug = mod._slug_from_url(s["url"])
        extra = _debug_salvo(slug)
        if not extra and s["category"] == "news" and s.get("kind") == "html":
            extra = _retrato(s["url"], renderizado=s["scraper_module"] == "canalenergia")
        _anotar("warning", f"ZERO {s['name']}", resumo + ("\n" + extra if extra else ""))
    print(f"{len(fontes)} fontes, {zeradas} com problema")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
