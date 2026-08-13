"""Testes de app/spreads/issuer_key.py.

Os casos vieram da validação contra os 467 nomes de emissor distintos do
Dashboard_Snapshot (04/08/2026). Rodar:

    python -m pytest tests/test_issuer_key.py -v
"""
from __future__ import annotations

import pytest

from app.spreads.issuer_key import issuer_key, issuer_key_com_fallback, remover_acentos


# ---------------------------------------------------------------------------
# O problema que motivou o módulo: mesma Anbima, grafias diferentes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    # Acento: o banco (API oficial) acentua, o snapshot (planilha) não
    ("AEGEA SANEAMENTO E PARTICIPAÇÕES S/A", "AEGEA SANEAMENTO E PARTICIPACOES S.A."),
    ("CEMIG GERAÇÃO E TRANSMISSÃO S/A", "CEMIG GERACAO E TRANSMISSAO S/A"),
    ("USINA SANTA ADÉLIA S.A.", "USINA SANTA ADELIA S.A."),
    ("VIX LOGÍSTICA S/A", "VIX LOGISTICA S/A"),
    # Forma societária escrita de jeitos diferentes
    ("ENGIE BRASIL ENERGIA S.A.", "ENGIE BRASIL ENERGIA SA"),
    ("LOCALIZA FLEET S.A.", "LOCALIZA FLEET S/A"),
    # Marcador "(*)" que a Anbima usa em algumas linhas
    ("DESKTOP S/A", "DESKTOP S/A (*)"),
    ("GSH CORP PARTICIPACOES S.A.", "GSH CORP PARTICIPACOES S.A. (*)"),
    # Sufixo presente numa fonte e ausente na outra
    ("BRISANET SERVICOS DE TELECOMUNICACOES", "BRISANET SERVICOS DE TELECOMUNICACOES S.A."),
])
def test_grafias_do_mesmo_emissor_convergem(a, b):
    assert issuer_key(a) == issuer_key(b) != ""


# ---------------------------------------------------------------------------
# O risco oposto, mais caro: juntar emissores DIFERENTES
# ---------------------------------------------------------------------------

def test_spe_numerada_nao_colide():
    """Concessionárias irmãs só se distinguem pelo número. Colidir aqui
    faria uma herdar o rating da outra."""
    assert issuer_key("AGUAS DO RIO 1 SPE S.A") != issuer_key("AGUAS DO RIO 4 SPE S.A")


def test_letra_identificadora_sobrevive():
    """BUG REAL da 1ª versão: descartar os tokens "S" e "A" soltos (pra
    limpar "S.A.") comia a letra que identifica a SPE.

    "AGUAS DO PARA A SPE S.A." precisa manter o "A" final -- senão um
    eventual "AGUAS DO PARA B" cairia na mesma chave.
    """
    assert issuer_key("AGUAS DO PARA A SPE S.A.") == "AGUAS PARA A"
    assert issuer_key("AGUAS DO PARA A SPE S.A.") != issuer_key("AGUAS DO PARA SPE S.A.")


def test_rent_a_car_mantem_o_a():
    """Mesmo bug, outro sintoma: "RENT A CAR" não pode virar "RENT CAR"."""
    assert issuer_key("LOCALIZA RENT A CAR S/A") == "LOCALIZA RENT A CAR"


def test_grupos_economicos_distintos_nao_colidem():
    nomes = [
        "EDP SAO PAULO DISTRIBUICAO DE ENERGIA S/A",
        "EQUATORIAL GOIAS DISTRIBUIDORA DE ENERGIA S.A.",
        "ENERGISA TOCANTINS DISTRIBUIDORA DE ENERGIA S/A",
    ]
    chaves = {issuer_key(n) for n in nomes}
    assert len(chaves) == 3


# ---------------------------------------------------------------------------
# Bordas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valor", [None, "", "   ", "-", "S.A.", "LTDA", "(*)"])
def test_nao_casavel_devolve_vazio(valor):
    """Nome que é só sufixo/marcador não é emissor. Devolver "" garante que
    quem chama mande pra revisão manual em vez de casar todos entre si."""
    assert issuer_key(valor) == ""


def test_vazios_nao_casam_entre_si():
    """O bug clássico dessa normalização: dois nomes inúteis virarem a
    mesma chave e serem tratados como o mesmo emissor."""
    assert issuer_key("-") == issuer_key("S.A.") == ""
    # ...por isso quem chama TEM que descartar "" antes de indexar.


def test_fallback_exibe_algo_para_a_administracao():
    """`issuer_key` devolve "" pra "S.A.", mas a tela de Admin precisa
    mostrar alguma coisa em vez de linha em branco."""
    assert issuer_key_com_fallback("S.A.") == "S A"
    assert issuer_key_com_fallback("AEGEA SANEAMENTO S/A") == "AEGEA SANEAMENTO"


def test_remover_acentos():
    assert remover_acentos("PARTICIPAÇÕES") == "PARTICIPACOES"
    assert remover_acentos("LOGÍSTICA") == "LOGISTICA"


def test_idempotente():
    """Aplicar a chave duas vezes não pode mudar o resultado -- senão
    reprocessar um seed mudaria os casamentos."""
    for nome in ["AEGEA SANEAMENTO E PARTICIPAÇÕES S/A", "AGUAS DO RIO 1 SPE S.A"]:
        k = issuer_key(nome)
        assert issuer_key(k) == k


# ---------------------------------------------------------------------------
# Regressão agregada contra os 467 nomes reais
# ---------------------------------------------------------------------------

def test_regressao_snapshot_467_nomes():
    """Números travados da validação de 04/08/2026: os 467 nomes distintos
    do snapshot colapsam em 440 chaves, e os 26 grupos com mais de uma
    grafia são todos o MESMO emissor (conferidos um a um).

    Se um refactor mudar esses números, é sinal de que a normalização
    ficou mais agressiva (risco de juntar emissores distintos) ou mais
    frouxa (volta a perder casamento) -- os dois casos merecem revisão
    manual antes de seguir.
    """
    import collections
    import json
    import pathlib

    caminho = pathlib.Path(__file__).parent / "fixtures" / "nomes_emissores.json"
    if not caminho.exists():
        pytest.skip("fixture ausente -- ver VALIDACAO_RATINGS.md pra regerar")

    nomes = json.loads(caminho.read_text(encoding="utf-8"))
    grupos: dict[str, set[str]] = collections.defaultdict(set)
    for n in nomes:
        k = issuer_key(n)
        if k:
            grupos[k].add(n)

    assert len(nomes) == 467
    assert len(grupos) == 440
    assert len([g for g in grupos.values() if len(g) > 1]) == 26
