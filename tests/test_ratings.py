"""Testes de app/spreads/ratings.py.

Os casos numéricos aqui NÃO são inventados: saíram da validação contra as
93.764 linhas da base do Dashboard_Snapshot e as 2.364 linhas da tabela de
ratings por emissor (04/08/2026). Ver VALIDACAO_RATINGS.md pro relatório
completo.

Rodar:  python -m pytest tests/test_ratings.py -v
"""
from __future__ import annotations

import pytest

from app.spreads.ratings import (
    PESO_TO_PADRAO,
    SEM_RATING,
    calcular_rating_medio,
    eh_vazio,
    ordenar_ratings,
    peso_de,
    rating_medio,
    ratings_desconhecidos,
)


# ---------------------------------------------------------------------------
# Lookup de peso por agência
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agencia,valor,esperado", [
    ("fitch", "AAA(bra)", 1),
    ("fitch", "AA-(bra)", 4),
    ("fitch", "C(bra)", 21),
    ("sp", "brAAA", 1),
    ("sp", "brBBB-", 10),
    ("moodys", "AAA.br", 1),
    ("moodys", "BBB.br", 9),
    # "D" é a única chave compartilhada pelas três tabelas
    ("fitch", "D", 22),
    ("sp", "D", 22),
    ("moodys", "D", 22),
])
def test_peso_de_reconhece(agencia, valor, esperado):
    assert peso_de(agencia, valor) == esperado


@pytest.mark.parametrize("agencia,valor", [
    # Escala errada pra agência -- formato da S&P na coluna da Fitch
    ("fitch", "brAAA"),
    ("moodys", "AAA(bra)"),
    # Case sensitive, igual ao MATCH do Excel
    ("fitch", "aaa(bra)"),
    # Buracos deliberados das tabelas (ver "REGRAS A MANTER" no módulo)
    ("fitch", "CCC(bra)"),
    ("sp", "brC"),
    ("moodys", "C.br"),
])
def test_peso_de_nao_reconhece(agencia, valor):
    assert peso_de(agencia, valor) is None


def test_agencia_invalida_levanta():
    with pytest.raises(ValueError):
        peso_de("standard_poors", "brAAA")


# ---------------------------------------------------------------------------
# Marcadores de "sem rating"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valor", [
    "-", "", "  ", None, "N.A.", "n/a",
    # Vistos na base real -- ver comentário de _VAZIOS
    "N.R.",       # 33.427 ocorrências (Moody's)
    "0",          # célula vazia lida como zero pelo Excel
    "(blank)",    # marcador de tabela dinâmica
])
def test_eh_vazio(valor):
    assert eh_vazio(valor)


def test_vazio_nao_vira_desconhecido():
    """`N.R.`/`0`/`-` são ausência de rating, não erro de dado -- não podem
    poluir a tela de Administração."""
    assert ratings_desconhecidos(fitch="-", sp="0", moodys="N.R.") == {}


def test_desconhecido_e_sinalizado():
    """Valor não-vazio que não casa na tabela TEM que aparecer -- senão um
    formato novo de agência sumiria da média em silêncio."""
    fora = ratings_desconhecidos(fitch="A+", sp="brCCC", moodys="AA+")
    assert fora == {"fitch": "A+", "sp": "brCCC", "moodys": "AA+"}


# ---------------------------------------------------------------------------
# Cálculo do rating médio
# ---------------------------------------------------------------------------

def test_uma_agencia_so():
    r = calcular_rating_medio(fitch="AA(bra)", sp="-", moodys="N.R.")
    assert r["rating"] == "AA"
    assert r["peso"] == 3
    assert r["n_agencias"] == 1


def test_tres_agencias_media_exata():
    """Caso real: AEGEA SANEAMENTO na base -- (3 + 2 + 4) / 3 = 3,0 -> AA."""
    r = calcular_rating_medio(fitch="AA(bra)", sp="brAA+", moodys="AA-.br")
    assert r["media"] == 3.0
    assert r["rating"] == "AA"
    assert r["n_agencias"] == 3


def test_agencia_ausente_nao_puxa_media():
    """Ausência não vale zero nem D: AAA + (nada) continua AAA, não some
    pra baixo."""
    assert rating_medio(fitch="AAA(bra)", sp="-", moodys="-") == "AAA"


def test_sem_nenhum_rating():
    r = calcular_rating_medio(fitch="-", sp="-", moodys="N.R.")
    assert r["rating"] == SEM_RATING
    assert r["peso"] is None
    assert r["media"] is None
    assert r["n_agencias"] == 0


# --- arredondamento: o ponto mais delicado do porte -----------------------

@pytest.mark.parametrize("fitch,sp,media,esperado", [
    # (1 + 2) / 2 = 1,5 -> ROUND do Excel sobe pra 2 = AA+
    ("AAA(bra)", "brAA+", 1.5, "AA+"),
    # (3 + 2) / 2 = 2,5 -> 3 = AA
    ("AA(bra)", "brAA+", 2.5, "AA"),
    # (5 + 4) / 2 = 4,5 -> 5 = A+
    ("A+(bra)", "brAA-", 4.5, "A+"),
])
def test_empate_arredonda_para_cima(fitch, sp, media, esperado):
    """`ROUND` do Excel é half UP; `round()` do Python é banker's rounding
    (`round(2.5) == 2`) e daria o rating MELHOR em metade dos empates.

    Validado contra a base: 231 empates .5 na tabela de ratings por
    emissor, 231 arredondam pra cima, 0 pra baixo."""
    r = calcular_rating_medio(fitch=fitch, sp=sp)
    assert r["media"] == media
    assert r["rating"] == esperado


def test_media_de_tres_nao_sofre_erro_de_float():
    """Média de 3 agências é dízima em binário -- `int(x + 0.5)` pode errar
    na borda. A implementação usa aritmética inteira; este teste trava isso.

    (1 + 2 + 2) / 3 = 1,666... -> 2 = AA+
    """
    r = calcular_rating_medio(fitch="AAA(bra)", sp="brAA+", moodys="AA+.br")
    assert r["rating"] == "AA+"
    assert r["peso"] == 2


def test_valor_desconhecido_nao_entra_na_media():
    """`A+` na coluna da Fitch é formato errado (falta `(bra)`): tem que
    ser ignorado no cálculo E sinalizado -- nunca virar A+ silenciosamente."""
    r = calcular_rating_medio(fitch="A+", moodys="AAA.br")
    assert r["rating"] == "AAA"          # só a Moody's entrou
    assert r["n_agencias"] == 1
    assert r["desconhecidos"] == {"fitch": "A+"}


def test_espacos_em_volta_sao_tolerados():
    assert rating_medio(fitch="  AA(bra) ") == "AA"


# ---------------------------------------------------------------------------
# Ordenação
# ---------------------------------------------------------------------------

def test_ordenar_por_risco_nao_alfabetico():
    """Ordem alfabética poria A antes de AA antes de AAA -- errado."""
    assert ordenar_ratings(["BBB", "AAA", "A", "AA", "N.A.", "AA+"]) == [
        "AAA", "AA+", "AA", "A", "BBB", "N.A.",
    ]


def test_escala_padrao_completa_e_sem_buraco():
    """A escala de saída (ao contrário das tabelas por agência) tem que
    cobrir 1..22 sem lacuna -- é o eixo dos gráficos por rating."""
    assert sorted(PESO_TO_PADRAO) == list(range(1, 23))
    assert PESO_TO_PADRAO[1] == "AAA" and PESO_TO_PADRAO[22] == "D"


# ---------------------------------------------------------------------------
# Regressão contra a base real (roda só se o JSON extraído estiver presente)
# ---------------------------------------------------------------------------

def test_regressao_tabela_emissores():
    """Os 2.364 emissores da tabela `ratings` do snapshot batem 100%.

    É o teste que mais importa: essa tabela é o output direto da planilha
    do Allan, sem o problema de defasagem que a base diária tem.
    """
    import json
    import pathlib

    caminho = pathlib.Path(__file__).parent / "fixtures" / "ratings_emissores.json"
    if not caminho.exists():
        pytest.skip("fixture ausente -- ver VALIDACAO_RATINGS.md pra regerar")

    linhas = json.loads(caminho.read_text(encoding="utf-8"))
    erros = [
        (r, calcular_rating_medio(r["fitch"], r["sp"], r["moodys"])["rating"])
        for r in linhas
        if calcular_rating_medio(r["fitch"], r["sp"], r["moodys"])["rating"]
        != (r["rating"] or "").strip()
    ]
    assert not erros, f"{len(erros)} divergências, ex.: {erros[:3]}"
