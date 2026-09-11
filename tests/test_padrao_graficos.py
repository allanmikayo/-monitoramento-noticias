"""O padrão visual dos gráficos vale para TODOS eles.

PEDIDO DO ALLAN (11/09/2026): texto dos eixos, títulos de eixo e legenda em
preto; marcador da legenda como bolinha, não retângulo; linha dos eixos
preta; eixo de data em Mês-Ano ("Mar-26"); e dica de ferramenta mostrando a
data e os valores daquele ponto. "Essas instruções valem para todos os
gráficos."

Por isso o padrão mora em `static/chart-padrao.js`, mexendo em
`Chart.defaults` uma vez, e não copiado dentro de cada `new Chart(...)` --
opção repetida é opção que diverge. Estes testes cuidam do que dá para
conferir sem navegador: que o arquivo é carregado onde há gráfico, na ordem
certa, e que ninguém voltou a formatar a data antes de entregá-la ao eixo.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PADRAO_JS = RAIZ / "static" / "chart-padrao.js"


def _templates_com_grafico() -> list[Path]:
    return [
        p for p in sorted((RAIZ / "templates").glob("*.html"))
        if "chart.js" in p.read_text(encoding="utf-8").lower()
    ]


def test_existe_um_arquivo_de_padrao():
    assert PADRAO_JS.exists()


def test_todo_template_com_grafico_carrega_o_padrao():
    faltando = [
        p.name for p in _templates_com_grafico()
        if "chart-padrao.js" not in p.read_text(encoding="utf-8")
    ]
    assert not faltando, f"template com gráfico sem o padrão: {faltando}"


def test_o_padrao_vem_depois_do_chartjs_e_antes_de_quem_desenha():
    """Ordem importa: `Chart.defaults` só existe depois que o Chart.js
    carrega, e só vale para gráfico criado DEPOIS. Fora dessa janela o
    arquivo carrega, não quebra nada, e não faz efeito nenhum -- falha
    silenciosa, do tipo que se descobre olhando a tela semanas depois."""
    for p in _templates_com_grafico():
        texto = p.read_text(encoding="utf-8")
        i_chartjs = texto.lower().find("chart.umd")
        i_padrao = texto.find("chart-padrao.js")
        assert i_chartjs != -1 and i_padrao != -1, p.name
        assert i_chartjs < i_padrao, f"{p.name}: padrão carrega antes do Chart.js"
        # Todo <script src="/static/...js"> que desenha tem que vir depois.
        for m in re.finditer(r'<script src="/static/([a-z0-9_-]+)\.js', texto):
            if m.group(1) in ("chart-padrao",):
                continue
            assert i_padrao < m.start(), (
                f"{p.name}: {m.group(1)}.js carrega antes do padrão"
            )


@pytest.mark.parametrize("trecho", [
    "Chart.defaults.color",               # texto preto
    "usePointStyle",                      # marcador de legenda em bolinha
    'pointStyle = "circle"',
    "border",                             # linha de eixo preta
    'tooltip.mode = "index"',             # dica por data, não por linha
])
def test_o_padrao_cobre_o_que_foi_pedido(trecho):
    assert trecho in PADRAO_JS.read_text(encoding="utf-8"), trecho


def test_o_eixo_de_data_recebe_iso_e_nao_data_ja_formatada():
    """A regressão concreta: `labels: series.map(r => fmtData(r.data))`
    entregava "15/03/2026" pronto. Com isso o eixo não tem como mostrar
    "Mar-26" e a dica de ferramenta não tem como mostrar a data cheia --
    as duas ficam presas à mesma string."""
    js = (RAIZ / "static" / "spreads.js").read_text(encoding="utf-8")
    assert "labels: series.map((r) => fmtData(r.data))" not in js
    assert "labels: labels.map(fmtData)" not in js
    assert js.count("PADRAO_GRAFICO.eixoData(") == 3, (
        "os três gráficos de série temporal da aba Spreads têm que usar o "
        "eixo de data padrão"
    )
