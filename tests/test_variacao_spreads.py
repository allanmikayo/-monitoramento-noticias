"""Aberturas e fechamentos: composição da base e dispersão por duration.

PEDIDO DO ALLAN (21/09/2026). Dois gráficos lado a lado na Visão Geral, no
formato do relatório semanal:

- "Evolução da Var. Spreads (% da base de ativos)": quatro colunas, cada uma
  a composição da base em quatro faixas (< -10, -10 até 0, 0 até 10, > 10
  bps para IPCA+; ±5 para CDI+). As colunas são espaçadas pela JANELA
  INTEIRA da base selecionada -- com WoW, quatro semanas seguidas.
- "Variação Spreads (bps)" x duration: um ponto por papel, com as maiores
  aberturas e os maiores fechamentos destacados.

A BASE é a mesma nos dois: papéis da classe, duration >= 1 na data, spread
publicado nas DUAS datas da comparação. E a porcentagem é por QUANTIDADE de
papéis -- exceção deliberada à regra "tudo por estoque" do dashboard,
decidida pelo Allan: o gráfico mede quantos nomes se moveram.
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
CDI = "CDI + Tradicionais"
# 12 datas consecutivas, da mais antiga (D[0]) para a mais recente (D[11]).
D = [date(2026, 9, 1) + timedelta(days=i) for i in range(12)]


def _banco() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return Session(eng)


def _papel(db, codigo, classe=IPCA, nome=None):
    db.add(Debenture(codigo=codigo, nome=nome or f"EMISSOR {codigo}", classe=classe))


def _spread(db, codigo, d, spread, duration=5.0, estoque=100.0):
    db.add(DebentureSpread(codigo=codigo, data=d, spread=spread,
                           duration=duration, estoque=estoque))


# ---------------------------------------------------------------------------
# As faixas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variacao,faixa", [
    (-10.1, 0), (-10.0, 1), (-0.1, 1),
    (0.0, 2),     # papel que não se mexeu não fechou
    (10.0, 2), (10.1, 3),
])
def test_bordas_das_faixas(variacao, faixa):
    """Cada valor cai em exatamente uma faixa -- as quatro sempre somam
    100%. As bordas são onde isso costuma falhar."""
    assert queries._indice_faixa(variacao, 10) == faixa


def test_cdi_usa_faixa_de_5_bps():
    assert queries.faixas_variacao(CDI) == [
        "< -5 bps", "-5 até 0 bps", "0 até 5 bps", "> 5 bps"]
    assert queries.faixas_variacao(IPCA)[0] == "< -10 bps"


# ---------------------------------------------------------------------------
# A base: quem entra na conta
# ---------------------------------------------------------------------------

def _colunas_de_um_dia(db, classe=IPCA):
    """Uma coluna só, comparando D[11] com D[10]."""
    return queries.movement_distribution(
        db, classe, dias_comparacao=1, n_snapshots=1, data_referencia=D[11])


def test_duration_abaixo_de_1_fica_de_fora():
    """Perto do vencimento o spread fica instável; esses papéis dominariam
    as caudas sem dizer nada sobre o mercado."""
    db = _banco()
    _papel(db, "LONGO"); _papel(db, "CURTO")
    for d, s in ((D[10], 50.0), (D[11], 70.0)):
        _spread(db, "LONGO", d, s, duration=3.0)
        _spread(db, "CURTO", d, s, duration=0.6)
    db.commit()
    [col] = _colunas_de_um_dia(db)
    assert col["n_ativos"] == 1


def test_papel_sem_spread_na_data_anterior_fica_de_fora():
    """Sem esta regra, um papel que estreou no meio da janela entraria com
    uma "variação" contra nada, e a composição mudaria por causa de quem
    ENTROU na base, não de quem se MOVEU."""
    db = _banco()
    _papel(db, "VELHO"); _papel(db, "ESTREANTE")
    _spread(db, "VELHO", D[10], 50.0); _spread(db, "VELHO", D[11], 55.0)
    _spread(db, "ESTREANTE", D[11], 80.0)
    db.commit()
    [col] = _colunas_de_um_dia(db)
    assert col["n_ativos"] == 1


def test_outra_classe_nao_entra():
    db = _banco()
    _papel(db, "IPCA1"); _papel(db, "CDI1", classe=CDI)
    for c in ("IPCA1", "CDI1"):
        _spread(db, c, D[10], 50.0); _spread(db, c, D[11], 60.0)
    db.commit()
    [col] = _colunas_de_um_dia(db)
    assert col["n_ativos"] == 1


# ---------------------------------------------------------------------------
# A composição
# ---------------------------------------------------------------------------

def test_porcentagem_por_quantidade_e_nao_por_estoque():
    """Decisão do Allan (21/09/2026). Um papel de estoque gigante conta 1,
    igual a um pequeno: o gráfico mede quantos nomes se moveram."""
    db = _banco()
    _papel(db, "GRANDE"); _papel(db, "PEQUENO")
    _spread(db, "GRANDE", D[10], 50.0, estoque=10_000.0)
    _spread(db, "GRANDE", D[11], 80.0, estoque=10_000.0)   # +30 -> "> 10"
    _spread(db, "PEQUENO", D[10], 50.0, estoque=1.0)
    _spread(db, "PEQUENO", D[11], 45.0, estoque=1.0)       # -5 -> "-10 até 0"
    db.commit()
    [col] = _colunas_de_um_dia(db)
    pct = {f["faixa"]: f["pct"] for f in col["faixas"]}
    assert pct["> 10 bps"] == pytest.approx(50.0)
    assert pct["-10 até 0 bps"] == pytest.approx(50.0)


def test_as_quatro_faixas_somam_100():
    db = _banco()
    for i, var in enumerate((-30, -3, 0, 4, 25, 12, -11)):
        _papel(db, f"P{i}")
        _spread(db, f"P{i}", D[10], 100.0)
        _spread(db, f"P{i}", D[11], 100.0 + var)
    db.commit()
    [col] = _colunas_de_um_dia(db)
    assert sum(f["pct"] for f in col["faixas"]) == pytest.approx(100.0, abs=0.2)
    assert sum(f["n"] for f in col["faixas"]) == 7


def test_colunas_espacadas_pela_janela_inteira():
    """Decisão reconfirmada em 21/09/2026: com janela de 2 posições, as
    colunas caem em D[5], D[7], D[9], D[11] -- cada uma comparada com
    exatamente uma janela antes (D[3], D[5], D[7], D[9]), sem sobreposição --
    e saem da mais antiga para a mais recente, que é a ordem do eixo x."""
    db = _banco()
    _papel(db, "P1")
    for i, d in enumerate(D):
        _spread(db, "P1", d, 100.0 + i)
    db.commit()
    cols = queries.movement_distribution(
        db, IPCA, dias_comparacao=2, n_snapshots=4, data_referencia=D[11])
    assert [c["data"] for c in cols] == [d.isoformat() for d in (D[5], D[7], D[9], D[11])]
    assert [c["data_comparacao"] for c in cols] == [
        d.isoformat() for d in (D[3], D[5], D[7], D[9])]


def test_historico_curto_devolve_so_as_colunas_possiveis():
    db = _banco()
    _papel(db, "P1")
    for d in D[-4:]:
        _spread(db, "P1", d, 100.0)
    db.commit()
    cols = queries.movement_distribution(
        db, IPCA, dias_comparacao=1, n_snapshots=4, data_referencia=D[11])
    assert len(cols) == 3   # a 4ª coluna precisaria de uma data antes de D[8]


# ---------------------------------------------------------------------------
# A dispersão
# ---------------------------------------------------------------------------

def test_dispersao_destaca_pelo_sinal_e_nao_pela_posicao():
    """Numa semana em que tudo abriu, "os que menos abriram" não são
    fechamentos. Pintá-los de preto contaria uma história falsa."""
    db = _banco()
    for i, var in enumerate((5, 10, 15, 20, 30)):
        _papel(db, f"P{i}")
        _spread(db, f"P{i}", D[10], 100.0)
        _spread(db, f"P{i}", D[11], 100.0 + var)
    db.commit()
    r = queries.variacao_por_duration(
        db, IPCA, dias_comparacao=1, data_referencia=D[11], top_n=2)
    destaques = {p["codigo"]: p["destaque"] for p in r["pontos"]}
    assert destaques["P4"] == "abertura" and destaques["P3"] == "abertura"
    assert "fechamento" not in destaques.values()
    assert destaques["P0"] is None


def test_dispersao_usa_a_mesma_base_da_composicao():
    """Um ponto na dispersão tem que corresponder a alguém contado na
    composição -- mesmos filtros de duration e de spread nas duas datas."""
    db = _banco()
    _papel(db, "OK"); _papel(db, "CURTO"); _papel(db, "SEM_ANTES")
    _spread(db, "OK", D[10], 50.0); _spread(db, "OK", D[11], 70.0)
    _spread(db, "CURTO", D[10], 50.0, duration=0.5); _spread(db, "CURTO", D[11], 70.0, duration=0.5)
    _spread(db, "SEM_ANTES", D[11], 70.0)
    db.commit()
    r = queries.variacao_por_duration(db, IPCA, dias_comparacao=1, data_referencia=D[11])
    [col] = _colunas_de_um_dia(db)
    assert [p["codigo"] for p in r["pontos"]] == ["OK"]
    assert r["n_ativos"] == col["n_ativos"] == 1


def test_dispersao_sem_historico_nao_explode():
    db = _banco()
    r = queries.variacao_por_duration(db, IPCA, dias_comparacao=5)
    assert r["pontos"] == [] and r["data_referencia"] is None


# ---------------------------------------------------------------------------
# As rotas
# ---------------------------------------------------------------------------

@pytest.fixture()
def cliente():
    from fastapi.testclient import TestClient

    import app.app as A
    from app import auth
    from app.db import Base as B, SessionLocal, engine
    from app.models import User

    B.metadata.create_all(engine)
    with SessionLocal() as db:
        u = db.query(User).first()
        if u is None:
            u = User(email="a@a.com", name="T", role="admin", active=True,
                     password_hash=auth.hash_password("x" * 10), email_confirmed=True)
            db.add(u)
            db.commit()
        token = auth.create_session(db, u, ip="1", user_agent="t").token
        db.commit()
    c = TestClient(A.app, raise_server_exceptions=False)
    c.cookies.set("session_token", token)
    return c


@pytest.mark.parametrize("rota", [
    "/api/spreads/movement-distribution", "/api/spreads/variacao-por-duration"])
def test_rotas_respondem(cliente, rota):
    r = cliente.get(f"{rota}?classe=IPCA%20%2B%20Incentivadas&base=WoW",
                    follow_redirects=False)
    assert r.status_code == 200, r.text[:200]


def test_composicao_devolve_os_rotulos_das_faixas(cliente):
    """A legenda vem do servidor: se o limiar mudar lá, a tela acompanha."""
    r = cliente.get("/api/spreads/movement-distribution?classe=CDI%20%2B%20Tradicionais&base=WoW")
    assert r.json()["faixas"][0] == "< -5 bps"
