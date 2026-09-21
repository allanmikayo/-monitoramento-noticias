"""Linha "Base comparável" e ranking de deságio em % do PU par.

BASE COMPARÁVEL (pedido do Allan, 21/09/2026). O spread médio de um dia é a
média de QUEM ESTÁ NA BASE naquele dia -- ele se move por repricing e também
por mudança de composição (papel entrando, vencendo, saindo da precificação).
A linha comparável é um índice encadeado: a cada par de dias, só os papéis
presentes nos dois contam, e os passos se acumulam. Ancorada no fim, termina
no mesmo número do card.

As duas propriedades abaixo DEFINEM a linha. Se alguma quebrar, ela deixou de
medir o que promete:

1. Mudança de composição pura (ninguém se mexe, um papel entra) move a média
   e NÃO move a comparável.
2. Repricing puro (mesma base, todo mundo abre) move as duas igual.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Debenture, DebentureSpread
from app.spreads import queries

IPCA = "IPCA + Incentivadas"
D = [date(2026, 9, 1) + timedelta(days=i) for i in range(4)]


def _banco() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return Session(eng)


def _add(db, codigo, pontos, classe=IPCA, estoque=100.0, pu=None, nome=None):
    db.add(Debenture(codigo=codigo, nome=nome or f"EMISSOR {codigo}", classe=classe))
    for d, spread in pontos:
        db.add(DebentureSpread(codigo=codigo, data=d, spread=spread, estoque=estoque,
                               duration=4.0, pct_pu_par=pu))


def _serie(db):
    return {p["data"]: p for p in queries.time_series(db, IPCA)}


def test_composicao_pura_move_a_media_e_nao_a_comparavel():
    """Ninguém reprecificou: A e B ficaram parados a 100 bps. No último dia
    entra C, a 300 bps. A média salta; a comparável tem que ficar parada --
    o salto é de quem ENTROU, não de quem se MOVEU."""
    db = _banco()
    _add(db, "A", [(d, 100.0) for d in D])
    _add(db, "B", [(d, 100.0) for d in D])
    _add(db, "C", [(D[3], 300.0)])
    db.commit()
    s = _serie(db)
    assert s[D[3].isoformat()]["spread_medio"] == pytest.approx(166.7, abs=0.1)
    assert s[D[2].isoformat()]["spread_medio"] == pytest.approx(100.0)
    # comparável: plana, e ancorada no nível de hoje
    for d in D:
        assert s[d.isoformat()]["spread_comparavel"] == pytest.approx(166.7, abs=0.1)


def test_repricing_puro_move_as_duas_linhas_igual():
    """Mesma base o tempo todo, todo mundo abre 10 bps por dia: não há efeito
    composição, então as duas linhas têm que coincidir."""
    db = _banco()
    _add(db, "A", [(d, 100.0 + 10 * i) for i, d in enumerate(D)])
    _add(db, "B", [(d, 200.0 + 10 * i) for i, d in enumerate(D)])
    db.commit()
    for p in queries.time_series(db, IPCA):
        assert p["spread_comparavel"] == pytest.approx(p["spread_medio"], abs=0.05)


def test_termina_no_mesmo_numero_do_card():
    """Ancoragem no fim: o último ponto da comparável é o spread médio de
    hoje. Se divergisse, a primeira pergunta seria "qual dos dois é o
    spread de hoje?"."""
    db = _banco()
    _add(db, "A", [(d, 100.0 + i * 3) for i, d in enumerate(D)])
    _add(db, "B", [(D[0], 400.0), (D[1], 410.0)])     # sai da base no meio
    _add(db, "C", [(D[2], 50.0), (D[3], 55.0)])       # entra no meio
    db.commit()
    ultimo = queries.time_series(db, IPCA)[-1]
    assert ultimo["spread_comparavel"] == pytest.approx(ultimo["spread_medio"])


def test_papel_que_sai_nao_arrasta_a_comparavel():
    """B (400 bps) sai da base no dia 2. A média despenca por causa da saída;
    a comparável só registra o que A e os demais efetivamente fizeram."""
    db = _banco()
    _add(db, "A", [(d, 100.0) for d in D])
    _add(db, "B", [(D[0], 400.0), (D[1], 400.0)])
    db.commit()
    s = _serie(db)
    queda_media = s[D[1].isoformat()]["spread_medio"] - s[D[2].isoformat()]["spread_medio"]
    queda_comp = s[D[1].isoformat()]["spread_comparavel"] - s[D[2].isoformat()]["spread_comparavel"]
    assert queda_media == pytest.approx(150.0)
    assert queda_comp == pytest.approx(0.0)


def test_serie_de_um_papel_so_nao_ganha_linha_comparavel():
    """O drill-down de um papel usa a mesma função com `codigo` -- ali não
    existe composição, e a resposta não pode mudar de formato."""
    db = _banco()
    _add(db, "A", [(d, 100.0) for d in D])
    db.commit()
    assert "spread_comparavel" not in queries.time_series(db, IPCA, codigo="A")[0]


# ---------------------------------------------------------------------------
# Maiores deságios
# ---------------------------------------------------------------------------

def test_desagios_do_menor_pu_para_o_maior():
    db = _banco()
    for cod, pu in (("A", 99.5), ("B", 71.2), ("C", 88.0), ("D", 101.3)):
        _add(db, cod, [(D[3], 200.0)], pu=pu)
    db.commit()
    r = queries.maiores_desagios(db, IPCA, top_n=3)
    assert [p["codigo"] for p in r["papeis"]] == ["B", "C", "A"]
    assert r["data_referencia"] == D[3].isoformat()


def test_desagios_respeita_data_classe_e_exclusao():
    from app.models import AppSetting

    db = _banco()
    _add(db, "HOJE", [(D[3], 100.0)], pu=90.0)
    _add(db, "CDI", [(D[3], 100.0)], classe="CDI + Tradicionais", pu=50.0)
    _add(db, "FORA", [(D[3], 100.0)], pu=40.0)
    db.add(AppSetting(key=queries.TICKERS_EXCLUIDOS_SETTING_KEY, value="FORA"))
    db.commit()
    codigos = [p["codigo"] for p in queries.maiores_desagios(db, IPCA)["papeis"]]
    assert codigos == ["HOJE"]


def test_desagio_mostra_o_emissor_para_o_eixo():
    db = _banco()
    _add(db, "X1", [(D[3], 100.0)], pu=80.0, nome="EMISSORA X S.A.")
    db.commit()
    [p] = queries.maiores_desagios(db, IPCA)["papeis"]
    assert p["emissor"] == "EMISSORA X S.A." and p["pct_pu_par"] == pytest.approx(80.0)
