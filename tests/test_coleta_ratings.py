"""Coleta das agências de rating (03/11/2026, ajustes vindos do Radar de
Ratings). Guarda os dois formatos que já zeraram uma fonte sem erro:
  * Fitch com mês e ano colados ("24 Sep2026") e data em dois pedaços;
  * S&P com as colunas "table-module__column-pageLists".
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from app.sources import fitch, spglobal


def test_fitch_data_colada_e_em_pedacos():
    assert fitch._parse_data("24 Sep2026").date().isoformat() == "2026-09-24"
    assert fitch._parse_data("28 May\n2026").date().isoformat() == "2026-05-28"
    html = """
    <div class="frw-column__wrapper--1 frw-article-data">
      <div class="frw-article-data--title"><a href="/research/x/1" aria-label="Fitch Afirma Rating da Sabesp">t</a></div>
      <div class="frw-date"><span class="frw-date__1">24 Sep</span><span class="frw-date__2">2026</span></div>
    </div>"""
    [art] = fitch._parse_listagem(BeautifulSoup(html, "lxml"))
    assert art.url == "https://www.fitchratings.com/research/x/1"
    assert art.title == "Fitch Afirma Rating da Sabesp"
    assert art.published_at.date().isoformat() == "2026-09-24"


def test_sp_colunas_page_lists():
    html = """
    <div class="table-module__content">
      <div class="table-module__row">
        <div class="table-module__column-pageLists text">27-May-2026 16:25 BRT</div>
        <div class="table-module__column-pageLists link">
          <a href="/ratings/pt/regulatory/article/-/view/type/PDF/id/1351">Rating da Copel reafirmado</a>
        </div>
      </div>
    </div>"""
    [art] = spglobal._parse_tabela(BeautifulSoup(html, "lxml"))
    assert art.url.startswith("https://brazil.ratings.spglobal.com/ratings/pt/regulatory/article/")
    assert art.title == "Rating da Copel reafirmado"
    assert art.published_at.isoformat() == "2026-05-27T19:25:00+00:00"  # 16:25 BRT
