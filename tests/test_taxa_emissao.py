"""A taxa DA EMISSÃO: leitura da base de características e formatação.

PEDIDO DO ALLAN (11/09/2026): "na tabela de dívida não quero taxa
indicativa, quero a taxa da emissão (nessa base com as características das
debentures você vai ter ela)".

No arquivo do debentures.com.br a remuneração não é uma coluna, são três:
`indice` ("DI", "IPCA", "PRÉ"...), `Percentual Multiplicador/Rentabilidade`
e `Juros Criterio Novo - Taxa`. "CDI + 3,50%" e "116% do CDI" são o MESMO
índice com preenchimentos diferentes. Os testes de formatação abaixo são a
tabela-verdade dessa combinação.

A fixture `caracteristicas_amostra.tsv` é um recorte REAL do arquivo que o
Allan baixou em 11/09/2026 (85 colunas, latin1, cabeçalho na 5ª linha),
amostrado para cobrir Situação × índice × (com e sem taxa). É o que protege
o parser das armadilhas do arquivo de verdade -- nome de coluna com espaço
sobrando, célula vazia, " - " no lugar de número.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.spreads.fetch import parse_caracs, parse_data_br
from app.spreads.queries import formatar_taxa_emissao

FIXTURE = Path(__file__).parent / "fixtures" / "caracteristicas_amostra.tsv"


def _caracs():
    linhas = FIXTURE.read_text(encoding="latin1").splitlines()
    idx = next(i for i, l in enumerate(linhas) if l.startswith("Codigo do Ativo"))
    return {c.codigo: c for c in parse_caracs("\n".join(linhas[idx:]))}


# ---------------------------------------------------------------------------
# Leitura do arquivo
# ---------------------------------------------------------------------------

def test_le_as_condicoes_de_emissao_do_arquivo_real():
    c = _caracs()["AAJR11"]
    assert c.data_emissao == date(2025, 6, 23)
    assert c.indice_emissao == "DI"
    assert c.percentual_emissao == pytest.approx(100.0)
    assert c.taxa_emissao == pytest.approx(3.5)


def test_continua_lendo_cnpj_e_lei_12431():
    """Os três campos que já existiam antes desta mudança não podem ter
    sido perdidos no caminho."""
    c = _caracs()["ABFR12"]
    assert c.incentivada == "S"
    assert c.cnpj and c.cnpj.strip()


def test_multiplicador_sem_numero_vira_none_e_nao_zero():
    """Todo papel IPCA+ traz " - " no multiplicador. Ler isso como 0 faria
    a tela dizer "0% do IPCA", que é falso -- é "não informado"."""
    c = _caracs()["ABFR12"]
    assert c.percentual_emissao is None
    assert c.taxa_emissao == pytest.approx(8.1869)


def test_taxa_vazia_vira_none():
    """AALR11 é 116% do CDI: multiplicador preenchido, taxa em branco."""
    c = _caracs()["AALR11"]
    assert c.percentual_emissao == pytest.approx(116.0)
    assert c.taxa_emissao is None


def test_cabecalho_com_espaco_sobrando_nao_derruba_a_leitura():
    """No arquivo, colunas vêm como "Empresa        " e
    " Data de Vencimento". Antes líamos só três colunas, nenhuma com esse
    problema; agora que lemos "Data de Emissao" e vizinhas, o strip no
    cabeçalho passou a ser obrigatório."""
    bruto = FIXTURE.read_text(encoding="latin1")
    assert "Empresa        \t" in bruto, "a fixture perdeu o espaço que este teste protege"
    assert len(_caracs()) >= 30


def test_layout_diferente_acusa_a_coluna_que_faltou():
    """Se o debentures.com.br mudar o arquivo, o erro tem que dizer O QUE
    sumiu -- não um KeyError cru no meio da captura noturna."""
    with pytest.raises(RuntimeError, match="Data de Emissao"):
        parse_caracs("Codigo do Ativo\tCNPJ\tDeb. Incent. (Lei 12.431)\nAAA11\t1\tN\n")


@pytest.mark.parametrize("bruto,esperado", [
    ("23/06/2025", date(2025, 6, 23)),
    ("", None),
    (" - ", None),
    ("#N/D", None),
    ("2025-06-23", None),  # formato ISO não é o do arquivo: recusa em vez de adivinhar
])
def test_parse_data_br(bruto, esperado):
    assert parse_data_br(bruto) == esperado


# ---------------------------------------------------------------------------
# Formatação para a tela
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("indice,percentual,taxa,esperado", [
    ("DI", 100, 3.5, "CDI + 3,50%"),          # o caso mais comum
    ("DI", 100, 4.35, "CDI + 4,35%"),
    ("DI", 116, None, "116,00% do CDI"),      # percentual do CDI, sem spread
    ("DI", 100, None, "100,00% do CDI"),
    ("DI", 108, 1.0, "108,00% do CDI + 1,00%"),  # raro, mas existe: os dois juntos
    ("IPCA", None, 8.1869, "IPCA + 8,1869%"),
    ("IPCA", None, 6.0, "IPCA + 6,00%"),
    ("IGP-M", None, 7.5, "IGP-M + 7,50%"),
    ("PRÉ", None, 14.476, "14,476% a.a."),    # prefixado: não há índice a somar
    ("SEM-ÍNDICE", None, None, None),
    (None, None, None, None),
])
def test_formatar_taxa_emissao(indice, percentual, taxa, esperado):
    assert formatar_taxa_emissao(indice, percentual, taxa) == esperado


def test_multiplicador_zero_nao_vira_zero_por_cento():
    """Registro antigo traz 0 no multiplicador querendo dizer "não
    informado". "0% do CDI" seria uma afirmação falsa sobre o papel; "100%
    do CDI" seria um chute. Só o índice é honesto."""
    assert formatar_taxa_emissao("IGP-M", 0, 0) == "IGP-M"
    assert formatar_taxa_emissao("DI", 0, None) == "CDI"


def test_o_arquivo_escreve_DI_mas_o_relatorio_diz_CDI():
    assert formatar_taxa_emissao("DI", 100, 2.0).startswith("CDI")


def test_indice_desconhecido_aparece_cru_em_vez_de_sumir():
    """Papel antigo indexado a dólar comercial existe na base. Melhor um
    rótulo estranho na tela do que uma linha sem taxa nenhuma."""
    assert formatar_taxa_emissao("US$ COMERCIAL", None, 12.0) == "US$ COMERCIAL + 12,00%"


def test_nunca_mistura_separador_decimal():
    """Número grande tem que sair no padrão brasileiro inteiro
    (1.234,5678), não com o ponto de milhar do inglês."""
    assert formatar_taxa_emissao("IPCA", None, 1234.5678) == "IPCA + 1.234,5678%"
