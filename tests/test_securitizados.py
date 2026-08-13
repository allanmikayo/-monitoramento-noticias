"""Testes de app/spreads/securitizados.py — CRI/CRA.

Os números vieram da validação contra as 95.413 observações da aba
CRA/CRI do Dashboard_Snapshot (04/08/2026), onde as três regras
(indexador, tipo de ativo e spread) bateram com **zero divergência**.

    python -m pytest tests/test_securitizados.py -v
"""
from __future__ import annotations

from datetime import date

import pytest

from app.spreads.securitizados import (
    DIAS_UTEIS_ANO,
    calcular_spread,
    classificar_indexador,
    classificar_tipo_ativo,
    normalizar,
    resumo,
)


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("remuneracao,esperado", [
    ("IPCA", "IPCA+"),
    ("DI ADITIVO", "CDI+"),
    ("DI MULTIPLICATIVO", "%CDI"),
    ("PRE FIXADO", "OUTRO"),
    ("di aditivo", "CDI+"),          # a API não garante caixa
    ("  IPCA  ", "IPCA+"),
])
def test_indexador_a_partir_da_remuneracao(remuneracao, esperado):
    assert classificar_indexador(remuneracao) == esperado


@pytest.mark.parametrize("valor", [None, "", "ALGO NOVO DA ANBIMA"])
def test_remuneracao_desconhecida_vira_outro(valor):
    """Tipo novo não pode cair numa fórmula existente por acidente — vira
    OUTRO e fica sem spread, visível."""
    assert classificar_indexador(valor) == "OUTRO"


@pytest.mark.parametrize("codigo,esperado", [
    ("CRA018002XN", "CRA"),
    ("18H0014828", "CRI"),
    ("19C0000001", "CRI"),
    ("cra0210012", "CRA"),
])
def test_tipo_ativo_pelo_codigo(codigo, esperado):
    """A API não traz o tipo; a regra do prefixo bate 100% no snapshot
    (61.117 CRA com prefixo, 34.296 CRI numéricos)."""
    assert classificar_tipo_ativo(codigo) == esperado


# ---------------------------------------------------------------------------
# Spread — uma fórmula por indexador
# ---------------------------------------------------------------------------

def test_ipca_usa_spread_composto_nao_diferenca():
    """CASO REAL do snapshot: taxa 9,4187 contra NTN-B 9,2258 dá 17,66 bps.

    A diferença aritmética daria 19,29 — 1,6 bps a mais, num spread de 17.
    É a mesma fórmula que `fetch.py` usa pras debêntures, então os dois
    produtos ficam comparáveis no mesmo gráfico.
    """
    assert calcular_spread("IPCA+", 9.4187, 9.2258) == pytest.approx(17.6607, abs=0.01)
    assert calcular_spread("IPCA+", 9.8805, 7.4815) == pytest.approx(223.2023, abs=0.01)


def test_cdi_aditivo():
    assert calcular_spread("CDI+", 1.3804) == pytest.approx(138.04, abs=0.01)


def test_pct_cdi_pode_ser_negativo():
    """Papel que paga 97,2% do CDI tem spread -278 bps. **Negativo é
    normal aqui** — só existe em securitizado, e confundir com erro de
    sinal levaria a "consertar" um dado correto."""
    assert calcular_spread("%CDI", 97.2182) == pytest.approx(-278.18, abs=0.01)
    assert calcular_spread("%CDI", 105.0) == pytest.approx(500.0, abs=0.01)


def test_ipca_sem_ntnb_nao_inventa_spread():
    assert calcular_spread("IPCA+", 9.4187, None) is None
    assert calcular_spread("IPCA+", 9.4187, 0) is None


def test_pre_fixado_nao_tem_spread():
    """Não há curva de referência aplicável — `None` é melhor que um
    número que ninguém sabe interpretar."""
    assert calcular_spread("OUTRO", 12.5) is None


def test_taxa_zero_vira_none_e_nao_zero():
    """A Anbima devolve 0 para papel não precificado no dia (9.182 linhas
    no snapshot). Tratar como spread zero colocaria papel inexistente no
    meio da distribuição e puxaria toda média pra baixo."""
    for idx in ("IPCA+", "CDI+", "%CDI"):
        assert calcular_spread(idx, 0, 5.0) is None
        assert calcular_spread(idx, None, 5.0) is None


def test_classes_nao_sao_comparaveis():
    """Sanidade conceitual: a mesma taxa numérica significa coisas
    completamente diferentes em cada classe."""
    taxa = 100.0
    assert calcular_spread("CDI+", taxa) == 10000.0    # CDI + 100 p.p.
    assert calcular_spread("%CDI", taxa) == 0.0        # exatamente 100% do CDI


# ---------------------------------------------------------------------------
# Normalização de uma linha da API
# ---------------------------------------------------------------------------

def _linha_api(**over):
    base = {
        "codigo_ativo": "CRA018002XN",
        "data_referencia": "2026-07-13",
        "emissor": "OPEA SECURITIZADORA S/A",
        "originador_credito": "RAÍZEN ENERGIA S.A.",
        "serie": "103", "emissao": "1",
        "data_vencimento": "2030-08-15",
        "tipo_remuneracao": "IPCA",
        "referencia_ntnb": "2030-08-15",
        "taxa_indicativa": "9.4187",
        "taxa_compra": "9.6698", "taxa_venda": "9.1251",
        "desvio_padrao": "0.12", "pu": "1421.45",
        "percent_pu_par": "97.51", "percent_vne": "0",
        "duration": "504",
    }
    base.update(over)
    return base


def test_normaliza_linha_completa():
    n = normalizar(_linha_api(), {"2030-08-15": 9.2258})
    assert n["codigo"] == "CRA018002XN"
    assert n["tipo_ativo"] == "CRA"
    assert n["indexador"] == "IPCA+"
    assert n["data"] == date(2026, 7, 13)
    assert n["taxa_ntnb_ref"] == 9.2258
    assert n["spread"] == pytest.approx(17.6607, abs=0.01)


def test_duration_convertida_para_anos():
    """A API devolve dias úteis (o snapshot guardava 58, 143...). Sem
    converter, securitizado e debênture não podem ir pro mesmo gráfico de
    spread × duration — um estaria em anos e o outro em dias."""
    n = normalizar(_linha_api(duration="504"))
    assert n["duration"] == pytest.approx(504 / DIAS_UTEIS_ANO)
    assert n["duration"] == pytest.approx(2.0)


def test_risco_e_do_originador_nao_da_securitizadora():
    """A securitizadora é veículo (12 delas para 218 originadores). Os dois
    campos precisam sobreviver separados até a persistência."""
    n = normalizar(_linha_api())
    assert n["emissor"] == "OPEA SECURITIZADORA S/A"
    assert n["originador_credito"] == "RAÍZEN ENERGIA S.A."


def test_linha_sem_codigo_e_descartada():
    assert normalizar(_linha_api(codigo_ativo="")) is None
    assert normalizar({}) is None


def test_campos_vazios_viram_none_nao_zero():
    n = normalizar(_linha_api(taxa_compra="", desvio_padrao=None, pu="abc"))
    assert n["taxa_compra"] is None
    assert n["desvio_padrao"] is None
    assert n["pu"] is None


def test_curva_ntnb_ausente_nao_quebra():
    n = normalizar(_linha_api(), None)
    assert n["taxa_ntnb_ref"] is None and n["spread"] is None


# ---------------------------------------------------------------------------
# Resumo (canário do job diário)
# ---------------------------------------------------------------------------

def test_resumo_conta_por_tipo_e_indexador():
    linhas = [
        normalizar(_linha_api(), {"2030-08-15": 9.2258}),
        normalizar(_linha_api(codigo_ativo="18H0014828", tipo_remuneracao="DI ADITIVO",
                              taxa_indicativa="1.38")),
        normalizar(_linha_api(codigo_ativo="19C0000002", tipo_remuneracao="PRE FIXADO",
                              taxa_indicativa="12.5")),
    ]
    r = resumo(linhas)
    assert r["total"] == 3
    assert r["por_tipo"] == {"CRA": 1, "CRI": 2}
    assert r["por_indexador"] == {"IPCA+": 1, "CDI+": 1, "OUTRO": 1}
    assert r["sem_spread"] == 1          # o pré-fixado
    assert r["data"] == date(2026, 7, 13)


def test_resumo_de_lista_vazia():
    """Dia sem publicação (fim de semana, feriado) não é erro."""
    r = resumo([])
    assert r["total"] == 0 and r["data"] is None


# ---------------------------------------------------------------------------
# NTN-B: referência do papel, senão vértice mais curto
# ---------------------------------------------------------------------------

from app.spreads.securitizados import resolver_taxa_ntnb  # noqa: E402


def test_prefere_a_referencia_do_papel():
    """Regra confirmada pelo Allan: buscar a data de referência primeiro."""
    taxa, origem = resolver_taxa_ntnb("2030-08-15", {"2030-08-15": 7.2}, min_ntnb=6.0)
    assert (taxa, origem) == (7.2, "REFERENCIA")


def test_cai_no_vertice_mais_curto_sem_referencia():
    """"...ou usar o menor vértice". É o mesmo fallback de `fetch.py` —
    lá essa regra já tinha sido corrigida em 27/07/2026, quando o cálculo
    usava o vértice curto para TODO papel em vez de só como fallback."""
    taxa, origem = resolver_taxa_ntnb(None, {"2030-08-15": 7.2}, min_ntnb=6.0)
    assert (taxa, origem) == (6.0, "VERTICE_CURTO")


def test_referencia_fora_da_curva_cai_no_vertice():
    """Vencimento que não existe na curva do dia não pode virar `None`
    silencioso enquanto há fallback disponível."""
    taxa, origem = resolver_taxa_ntnb("2099-01-01", {"2030-08-15": 7.2}, min_ntnb=6.0)
    assert (taxa, origem) == (6.0, "VERTICE_CURTO")


def test_sem_curva_nem_vertice_e_indisponivel():
    assert resolver_taxa_ntnb(None, None, None) == (None, "INDISPONIVEL")


def test_normalizar_usa_o_fallback():
    n = normalizar(_linha_api(referencia_ntnb=""), {"2030-08-15": 7.2}, min_ntnb=6.0)
    assert n["origem_ntnb"] == "VERTICE_CURTO"
    assert n["spread"] is not None


def test_origem_ntnb_aparece_no_resumo():
    """Canário: se um dia quase tudo cair em VERTICE_CURTO, ou a curva ou
    o campo `referencia_ntnb` da Anbima quebrou."""
    linhas = [
        normalizar(_linha_api(), {"2030-08-15": 9.2258}),
        normalizar(_linha_api(codigo_ativo="CRA9", referencia_ntnb=""), {}, min_ntnb=6.0),
    ]
    assert resumo(linhas)["origem_ntnb"] == {"REFERENCIA": 1, "VERTICE_CURTO": 1}
