"""De onde veio o movimento do spread médio (01/10/2026, pedido do Allan).

O treemap no fim da Visão Geral reparte a variação do card SPREAD MÉDIO
entre grupos econômicos (ou setores, ou emissores), com entradas e saídas
da base em blocos próprios. Estes testes seguram o que faz o gráfico ser
confiável:

1. A soma dos blocos É a variação do card -- com papel entrando, saindo,
   mudando de estoque e sem estoque numa das datas. Se não bater, o treemap
   explica um número que não está na tela.
2. Repricing puro de um grupo aparece só nele (100% do movimento).
3. Entrada pura não aparece em grupo nenhum: vai para "Entradas na base".
4. Papel sem grupo cadastrado vira bloco com o nome do emissor, não um
   "Sem classificação" gigante.
5. A dica traz no máximo 5 papéis por bloco, o maior contribuinte primeiro,
   e diz quantos são no total (o "..." da tela).
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Debenture, DebentureSpread
from app.spreads import queries

IPCA = "IPCA + Incentivadas"
D0, D1 = date(2026, 9, 1), date(2026, 9, 2)


def _banco() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return Session(eng)


def _papel(db, codigo, grupo, pontos, nome=None, setor="Saneamento"):
    """`pontos`: {data: (spread, estoque)}."""
    db.add(Debenture(codigo=codigo, nome=nome or f"EMISSOR {codigo}", classe=IPCA,
                     grupo_economico=grupo, setor=setor))
    for d, (spread, estoque) in pontos.items():
        db.add(DebentureSpread(codigo=codigo, data=d, spread=spread, estoque=estoque,
                               duration=4.0))


def _atrib(db, nivel="grupo"):
    return queries.atribuicao_variacao(db, IPCA, dias_comparacao=1, nivel=nivel)


def test_soma_dos_blocos_bate_com_o_card():
    rnd = random.Random(7)
    db = _banco()
    grupos = ["Aegea", "Iguá", "CPFL", "Equatorial", None]
    for i in range(60):
        pontos = {}
        if i % 10 != 0:                      # 1 em 10 estreia na data analisada
            pontos[D0] = (rnd.uniform(50, 250), rnd.uniform(10, 500))
        if i % 9 != 0:                       # 1 em 9 sai da base
            pontos[D1] = (rnd.uniform(50, 250), rnd.uniform(10, 500))
        if i % 13 == 0 and D1 in pontos:     # spread nas duas datas, sem estoque numa
            pontos[D1] = (pontos[D1][0], None)
        if pontos:
            _papel(db, f"T{i:02d}", grupos[i % 5], pontos)
    db.commit()

    card = queries.kpi_summary(db, IPCA, dias_comparacao=1)
    r = _atrib(db)
    soma = sum(b["contribuicao_bps"] for b in r["blocos"])
    assert soma == pytest.approx(r["variacao_bps"], abs=0.01)
    assert r["variacao_bps"] == pytest.approx(card["variacao_bps"], abs=0.051)
    assert r["spread_final"] == pytest.approx(card["spread_medio"], abs=0.051)
    # os três totais também fecham
    assert (r["total_abriu_bps"] + r["total_fechou_bps"] + r["composicao_bps"]
            == pytest.approx(r["variacao_bps"], abs=0.02))
    # papel com spread nas duas datas mas sem estoque numa delas: saída, com motivo
    saidas = next(b for b in r["blocos"] if b["tipo"] == "saida")
    assert saidas["n_tickers"] > 0


def test_repricing_puro_de_um_grupo_explica_tudo():
    db = _banco()
    _papel(db, "AEG1", "Aegea", {D0: (100.0, 100.0), D1: (130.0, 100.0)})
    _papel(db, "CPF1", "CPFL", {D0: (100.0, 100.0), D1: (100.0, 100.0)})
    db.commit()
    r = _atrib(db)
    assert r["variacao_bps"] == pytest.approx(15.0)
    aegea = next(b for b in r["blocos"] if b["rotulo"] == "Aegea")
    assert aegea["contribuicao_bps"] == pytest.approx(15.0)
    assert aegea["pct_variacao"] == pytest.approx(100.0)
    assert aegea["repricing_bps"] == pytest.approx(15.0)
    assert r["composicao_bps"] == pytest.approx(0.0)


def test_entrada_pura_vai_para_composicao_e_nao_para_o_grupo():
    """Ninguém se mexeu; entra um papel a 300 bps. A média sobe, e a culpa
    não pode cair num grupo -- quem entrou não reprecificou."""
    db = _banco()
    _papel(db, "A", "Aegea", {D0: (100.0, 100.0), D1: (100.0, 100.0)})
    _papel(db, "B", "CPFL", {D0: (100.0, 100.0), D1: (100.0, 100.0)})
    _papel(db, "C", "Aegea", {D1: (300.0, 100.0)})
    db.commit()
    r = _atrib(db)
    assert r["variacao_bps"] == pytest.approx(66.67, abs=0.01)
    entradas = next(b for b in r["blocos"] if b["tipo"] == "entrada")
    assert entradas["contribuicao_bps"] == pytest.approx(66.67, abs=0.01)
    assert entradas["tickers"][0]["codigo"] == "C"
    assert entradas["tickers"][0]["motivo"] == "sem spread na data inicial"
    for b in r["blocos"]:
        if b["tipo"] == "grupo":
            assert b["contribuicao_bps"] == pytest.approx(0.0, abs=1e-9)


def test_papel_sem_grupo_vira_bloco_do_emissor():
    db = _banco()
    _papel(db, "X1", None, {D0: (100.0, 100.0), D1: (110.0, 100.0)}, nome="SOLITÁRIA S.A.")
    _papel(db, "X2", None, {D0: (100.0, 100.0), D1: (90.0, 100.0)}, nome="OUTRA S.A.")
    db.commit()
    rotulos = {b["rotulo"]: b for b in _atrib(db)["blocos"]}
    assert "SOLITÁRIA S.A." in rotulos and "OUTRA S.A." in rotulos
    assert queries.SEM_CLASSIFICACAO not in rotulos
    assert rotulos["SOLITÁRIA S.A."]["sem_grupo"] is True


def test_niveis_setor_e_emissor():
    db = _banco()
    _papel(db, "A1", "Aegea", {D0: (100.0, 100.0), D1: (110.0, 100.0)},
           nome="AEGEA SANEAMENTO", setor="Saneamento")
    _papel(db, "A2", "Aegea", {D0: (100.0, 100.0), D1: (120.0, 100.0)},
           nome="AGUAS DO RIO", setor="Saneamento")
    db.commit()
    assert {b["rotulo"] for b in _atrib(db, "setor")["blocos"]} == {"Saneamento"}
    assert {b["rotulo"] for b in _atrib(db, "emissor")["blocos"]} == {
        "AEGEA SANEAMENTO", "AGUAS DO RIO"}
    with pytest.raises(ValueError):
        _atrib(db, "rating")


def test_dica_traz_cinco_papeis_maior_contribuinte_primeiro():
    db = _banco()
    for i in range(8):
        _papel(db, f"G{i}", "Aegea", {D0: (100.0, 100.0), D1: (100.0 + i, 100.0)})
    db.commit()
    aegea = _atrib(db)["blocos"][0]
    assert aegea["n_tickers"] == 8
    assert len(aegea["tickers"]) == queries.TICKERS_NA_DICA == 5
    assert [t["codigo"] for t in aegea["tickers"]] == ["G7", "G6", "G5", "G4", "G3"]
    t = aegea["tickers"][0]
    assert t["variacao_bps"] == pytest.approx(7.0)
    assert t["spread"] == pytest.approx(107.0)
    assert t["estoque"] == pytest.approx(100.0)


def test_variacao_quase_zero_nao_inventa_percentual():
    db = _banco()
    _papel(db, "A", "Aegea", {D0: (100.0, 100.0), D1: (110.0, 100.0)})
    _papel(db, "B", "CPFL", {D0: (100.0, 100.0), D1: (90.0, 100.0)})
    db.commit()
    r = _atrib(db)
    assert r["variacao_bps"] == pytest.approx(0.0)
    assert all(b["pct_variacao"] is None for b in r["blocos"])


def test_sem_data_de_comparacao_devolve_vazio():
    db = _banco()
    _papel(db, "A", "Aegea", {D1: (100.0, 100.0)})
    db.commit()
    r = _atrib(db)
    assert r["blocos"] == [] and r["variacao_bps"] is None
