"""A tabela de spread por setor, com drill-down de quatro níveis.

DOIS BUGS NUM SÓ LUGAR (11/09/2026).

1. A ROTA NÃO EXISTIA. `static/spreads.js` chamava `/api/spreads/por-setor`
   e o servidor respondia 404, embora `queries.spread_por_setor` estivesse
   inteira. Na tela isso não aparece como erro: a promessa do `fetch` é
   rejeitada, a tabela fica vazia e o resto da Visão Geral continua
   desenhando normalmente. Foi assim que a tabela "sumiu" sem ninguém ver
   um erro -- e é exatamente por isso que existe aqui um teste de ROTA, e
   não só da consulta.

2. FALTAVA O NÍVEL DE EMISSOR. Abrir um subsetor despejava todos os papéis
   de todas as empresas de uma vez. Um subsetor movido por uma emissora só
   ficava indistinguível de um movido pelo setor inteiro -- e "quem puxou
   isso" é a pergunta que traz alguém a esta tabela.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.spreads import queries

CLASSE = "IPCA + Incentivadas"
CLASSE_Q = "IPCA%20%2B%20Incentivadas"


@pytest.fixture()
def banco():
    from app.db import Base, SessionLocal, engine
    from app.models import Debenture, DebentureSpread

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.query(DebentureSpread).delete()
        db.query(Debenture).delete()
        # Um setor, um subsetor, DOIS emissores -- é o caso que o nível novo
        # serve para separar.
        papeis = [
            ("TRAN1", "CPFL TRANSMISSAO", "Energia Elétrica", "Transmissão", 40.0, 100.0),
            ("TRAN2", "CPFL TRANSMISSAO", "Energia Elétrica", "Transmissão", 60.0, 100.0),
            ("ISA1", "ISA ENERGIA", "Energia Elétrica", "Transmissão", 200.0, 100.0),
            ("GER1", "AES BRASIL", "Energia Elétrica", "Geração", 80.0, 50.0),
            ("SAN1", "AEGEA", "Saneamento", "Água e Esgoto", 90.0, 70.0),
        ]
        for cod, nome, setor, sub, spread, estoque in papeis:
            db.add(Debenture(codigo=cod, nome=nome, classe=CLASSE, indexador="IPCA +",
                             setor=setor, subsetor=sub))
            for d in (date(2026, 9, 9), date(2026, 9, 10)):
                db.add(DebentureSpread(codigo=cod, data=d, spread=spread,
                                       estoque=estoque, duration=4.0))
        db.commit()
    yield SessionLocal
    with SessionLocal() as db:
        db.query(DebentureSpread).delete()
        db.query(Debenture).delete()
        db.commit()


@pytest.fixture()
def cliente(banco):
    import app.app as A
    from app import auth
    from app.models import User

    with banco() as db:
        u = db.query(User).first()
        if u is None:
            u = User(email="a@a.com", name="Teste", role="admin", active=True,
                     password_hash=auth.hash_password("x" * 10), email_confirmed=True)
            db.add(u)
            db.commit()
        token = auth.create_session(db, u, ip="1", user_agent="teste").token
        db.commit()
    c = TestClient(A.app, raise_server_exceptions=False)
    c.cookies.set("session_token", token)
    return c


def _url(**kw):
    partes = "&".join(f"{k}={v}" for k, v in kw.items())
    return f"/api/spreads/por-setor?classe={CLASSE_Q}&{partes}"


# ---------------------------------------------------------------------------
# A rota
# ---------------------------------------------------------------------------

def test_a_rota_existe(cliente):
    """O teste que teria evitado a tabela vazia: a consulta funcionava, a
    rota é que não estava lá."""
    r = cliente.get(_url(nivel="setor"), follow_redirects=False)
    assert r.status_code == 200, r.status_code


def test_a_rota_exige_login():
    import app.app as A

    anon = TestClient(A.app, raise_server_exceptions=False)
    r = anon.get(_url(nivel="setor"), follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403)


@pytest.mark.parametrize("kw,falta", [
    ({"nivel": "subsetor"}, "setor"),
    ({"nivel": "emissor", "setor": "Energia+El%C3%A9trica"}, "subsetor"),
    ({"nivel": "ticker", "setor": "Energia+El%C3%A9trica", "subsetor": "Transmiss%C3%A3o"}, "emissor"),
])
def test_nivel_sem_o_pai_da_400_em_vez_de_dado_errado(cliente, kw, falta):
    """Pedir "ticker" sem emissor devolveria o subsetor inteiro achatado
    numa lista -- um 200 com o conteúdo errado, que é pior que um erro."""
    r = cliente.get(_url(**kw))
    assert r.status_code == 400, r.status_code
    # O app tem um handler próprio de HTTPException que devolve HTML, não
    # JSON -- por isso a checagem é no texto. A mensagem precisa NOMEAR o
    # que faltou: "400" sozinho não diz nada a quem está depurando.
    assert falta in r.text, r.text[:200]


def test_nivel_invalido_da_400(cliente):
    assert cliente.get(_url(nivel="grupo")).status_code == 400


# ---------------------------------------------------------------------------
# Os quatro níveis
# ---------------------------------------------------------------------------

def test_nivel_setor_agrega_o_mercado(banco):
    with banco() as db:
        r = queries.spread_por_setor(db, CLASSE, nivel="setor")
    por_rotulo = {l["rotulo"]: l for l in r["linhas"]}
    assert set(por_rotulo) == {"Energia Elétrica", "Saneamento"}
    assert por_rotulo["Energia Elétrica"]["n_ativos"] == 4


def test_nivel_emissor_separa_quem_puxou(banco):
    """O nível novo. No subsetor Transmissão há dois emissores com spreads
    bem diferentes (50 e 200 bps) -- sem esta linha, os dois viravam uma
    média só e a pergunta "quem puxou" não tinha resposta."""
    with banco() as db:
        r = queries.spread_por_setor(
            db, CLASSE, nivel="emissor",
            setor="Energia Elétrica", subsetor="Transmissão")
    por_rotulo = {l["rotulo"]: l for l in r["linhas"]}
    assert set(por_rotulo) == {"CPFL TRANSMISSAO", "ISA ENERGIA"}
    assert por_rotulo["CPFL TRANSMISSAO"]["n_ativos"] == 2
    assert por_rotulo["CPFL TRANSMISSAO"]["spread_medio"] == pytest.approx(50.0)
    assert por_rotulo["ISA ENERGIA"]["spread_medio"] == pytest.approx(200.0)


def test_nivel_ticker_fica_dentro_do_emissor_escolhido(banco):
    """A regressão que este teste trava: se o filtro de emissor for
    esquecido, o nível de ticker volta a mostrar o subsetor inteiro."""
    with banco() as db:
        r = queries.spread_por_setor(
            db, CLASSE, nivel="ticker", setor="Energia Elétrica",
            subsetor="Transmissão", emissor="CPFL TRANSMISSAO")
    assert {l["rotulo"] for l in r["linhas"]} == {"TRAN1", "TRAN2"}
    assert all(l["nome"] == "CPFL TRANSMISSAO" for l in r["linhas"])


def test_cada_nivel_devolve_o_caminho_que_esta_percorrendo(banco):
    """A trilha de navegação no topo da tabela se apoia nisto."""
    with banco() as db:
        r = queries.spread_por_setor(
            db, CLASSE, nivel="ticker", setor="Energia Elétrica",
            subsetor="Transmissão", emissor="CPFL TRANSMISSAO")
    assert (r["setor"], r["subsetor"], r["emissor"]) == (
        "Energia Elétrica", "Transmissão", "CPFL TRANSMISSAO")


def test_soma_dos_emissores_bate_com_o_subsetor(banco):
    """Descer um nível não pode criar nem sumir com papel -- é a conferência
    que o Allan faria à mão na primeira vez que usasse a tela."""
    with banco() as db:
        sub = queries.spread_por_setor(db, CLASSE, nivel="subsetor",
                                       setor="Energia Elétrica")
        emis = queries.spread_por_setor(db, CLASSE, nivel="emissor",
                                        setor="Energia Elétrica",
                                        subsetor="Transmissão")
    n_sub = {l["rotulo"]: l["n_ativos"] for l in sub["linhas"]}["Transmissão"]
    assert sum(l["n_ativos"] for l in emis["linhas"]) == n_sub
