"""Regra PRECIFICADO e mediana do spread (22/10/2026, pedido do Allan).

Só entra nas contas de spread o papel com spread E PU, % do PU par e
duration publicados na data. Caso real: ASER12 (Águas do Sertão) em
21/10/2026 vinha só com taxa indicativa e levava o spread médio IPCA+ de
108,7 para 109,7 bps.
"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Debenture, DebentureSpread
from app.spreads import queries

IPCA = "IPCA + Incentivadas"
D0, D1 = date(2026, 10, 14), date(2026, 10, 21)


def _banco():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = Session(eng)
    for cod in ("A", "B", "C", "NOVO"):
        db.add(Debenture(codigo=cod, nome=f"EMISSOR {cod}", classe=IPCA, setor="Saneamento"))
    for d in (D0, D1):
        db.add(DebentureSpread(codigo="A", data=d, spread=100.0, estoque=100.0))
        db.add(DebentureSpread(codigo="B", data=d, spread=200.0, estoque=300.0))
        db.add(DebentureSpread(codigo="C", data=d, spread=50.0, estoque=100.0))
        # Igual ao ASER12: taxa/spread e estoque, mas sem PU, % par e duration.
        db.add(DebentureSpread(codigo="NOVO", data=d, spread=900.0, estoque=500.0,
                               pu=None, pct_pu_par=None, duration=None))
    db.commit()
    return db


def test_papel_sem_pu_fica_fora_do_card():
    k = queries.kpi_summary(_banco(), IPCA, dias_comparacao=1)
    assert k["n_ativos"] == 3
    assert k["spread_medio"] == pytest.approx((100 * 100 + 200 * 300 + 50 * 100) / 500, abs=0.1)
    # Estoque do card = só dos precificados (sem o NOVO).
    assert k["estoque_total"] == 500.0


@pytest.mark.parametrize("faltando", ["pu", "pct_pu_par", "duration"])
def test_basta_faltar_um_dos_tres(faltando):
    db = _banco()
    db.query(DebentureSpread).filter_by(codigo="NOVO").update(
        {"pu": 1000.0, "pct_pu_par": 100.0, "duration": 5.0, faltando: None})
    db.commit()
    assert queries.kpi_summary(db, IPCA, dias_comparacao=1)["n_ativos"] == 3


def test_mediana_simples_sem_peso():
    k = queries.kpi_summary(_banco(), IPCA, dias_comparacao=1)
    assert k["spread_mediana"] == 100.0          # 50, 100, 200 -> 100 (sem peso)
    assert k["variacao_mediana_bps"] == 0.0


def test_serie_traz_media_e_mediana_sem_o_papel_incompleto():
    serie = queries.time_series(_banco(), IPCA)
    assert [p["n_ativos"] for p in serie] == [3, 3]
    assert serie[-1]["spread_mediana"] == 100.0
    assert serie[-1]["spread_medio"] == pytest.approx(150.0, abs=0.1)


def test_detalhes_e_setor_seguem_a_mesma_regra():
    db = _banco()
    assert [r["codigo"] for r in queries.detalhes_rows(db, IPCA, D1)["rows"]] == ["A", "B", "C"]
    [linha] = queries.spread_por_setor(db, IPCA, dias_comparacao=1, nivel="setor")["linhas"]
    assert linha["n_ativos"] == 3
