"""Página inicial do Hub (26/10/2026).

Guarda: a leitura da curva da ANBIMA não depende de como o JSON se aninha;
o primário compara mês fechado com o mesmo mês do ano anterior e YTD com o
mesmo intervalo do ano anterior, sempre pela data de ENCERRAMENTO; "/" é a
página inicial (atrás de login) e Notícias mudou para "/noticias".
"""
from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import inicio
from app.spreads import curva_juros as cj


# ---- curva -------------------------------------------------------------
@pytest.mark.parametrize("resposta", [
    # tudo num objeto só
    {"data_referencia": "2026-10-23",
     "ettj": [{"vertice_du": 252, "taxa_prefixadas": 12.8569, "taxa_ipca": 6.294, "taxa_implicita": 6.1742},
              {"vertice_du": 2520, "taxa_prefixadas": 12.9023, "taxa_ipca": 6.6343, "taxa_implicita": 5.878}],
     "circular_3361": [{"vertice_du": 21, "taxa": 13.52}]},
    # lista por grupo, com taxas em texto brasileiro
    [{"data_referencia": "2026-10-23", "grupo_indexador": "PREFIXADO", "b1": 0.1,
      "vertices": [{"vertice_du": "252", "taxa_prefixadas": "12,8569"}, {"vertice_du": "2520", "taxa_prefixadas": "12,9023"}]},
     {"grupo_indexador": "IPCA",
      "vertices": [{"vertice_du": 252, "taxa_ipca": 6.294, "taxa_implicita": 6.1742},
                   {"vertice_du": 2520, "taxa_ipca": 6.6343, "taxa_implicita": 5.878}]}],
])
def test_curva_lida_em_qualquer_aninhamento(resposta):
    ref, vert = cj.extrair_vertices(resposta)
    assert ref == date(2026, 10, 23)
    assert [v["du"] for v in vert] == [252, 2520]          # a Circular 3.361 fica de fora
    assert vert[0] == {"du": 252, "pre": 12.8569, "ipca": 6.294, "implicita": 6.1742}


@pytest.fixture()
def banco():
    from app.db import Base, SessionLocal, engine
    from app.models import CurvaJuros, OfertaCVM

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.query(CurvaJuros).delete()
        db.query(OfertaCVM).delete()
        db.commit()
        yield db
        db.query(CurvaJuros).delete()
        db.query(OfertaCVM).delete()
        db.commit()


def test_curvas_comparam_hoje_5_e_21_dias(banco):
    from app.spreads.mercado_secundario import dias_uteis

    dias = dias_uteis(date(2026, 9, 1), date(2026, 10, 23))
    for i, d in enumerate(dias):
        cj.gravar(banco, d, [{"du": 2520, "pre": 12.0, "ipca": 6.0 + i / 100, "implicita": 5.8}])
    banco.commit()
    c = {x["chave"]: x for x in inicio.curvas(banco)["curvas"]}
    assert c["hoje"]["data"] == "2026-10-23"
    assert c["d5"]["data"] == dias[-6].isoformat()
    assert c["d21"]["data"] == dias[-22].isoformat()
    assert c["hoje"]["vertices"][0]["anos"] == 10.0


# ---- primário ------------------------------------------------------------
def _oferta(n, encerramento, valor, status="Oferta Encerrada"):
    from app.models import OfertaCVM
    return OfertaCVM(numero_requerimento=str(n), data_registro=encerramento, data_encerramento=encerramento,
                     status=status, instrumento="DEB", valor_total=valor, nome_emissor="X")


def test_primario_mes_fechado_e_ytd_contra_o_mesmo_periodo(banco):
    banco.add_all([
        _oferta(1, date(2026, 9, 10), 3e9), _oferta(2, date(2026, 9, 30), 1e9),     # set/26 = 4 bi
        _oferta(3, date(2025, 9, 15), 2e9),                                          # set/25 = 2 bi
        _oferta(4, date(2026, 10, 20), 5e9),                                         # out/26: só no YTD
        _oferta(5, date(2025, 10, 20), 1e9),                                         # out/25 até 20/10: YTD anterior
        _oferta(6, date(2025, 11, 5), 9e9),                                          # depois de 20/11/25: fora
        _oferta(7, date(2026, 9, 12), 7e9, status="Registro Caducado"),              # morta: fora
    ])
    banco.commit()
    p = inicio.primario_bloco(banco, hoje=date(2026, 10, 20))
    assert p["mes"]["inicio"] == "2026-09-01" and p["mes"]["fim"] == "2026-09-30"
    assert p["mes"]["volume"] == 4e9 and p["mes"]["volume_ano_anterior"] == 2e9
    assert p["mes"]["variacao"] == pytest.approx(1.0)
    assert p["ytd"]["volume"] == 9e9 and p["ytd"]["volume_ano_anterior"] == 3e9
    assert p["ytd"]["variacao"] == pytest.approx(2.0)


# ---- rotas ---------------------------------------------------------------
@pytest.fixture()
def cliente():
    import app.app as A
    from app import auth
    from app.db import Base, SessionLocal, engine
    from app.models import User

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        u = db.query(User).filter_by(email="inicio@a.com").first()
        if u is None:
            u = User(email="inicio@a.com", name="Allan Mikayo", role="admin", active=True,
                     password_hash=auth.hash_password("x" * 10), email_confirmed=True)
            db.add(u)
            db.commit()
        tok = auth.create_session(db, u, ip="1", user_agent="t").token
        db.commit()
    c = TestClient(A.app, raise_server_exceptions=False)
    c.cookies.set("session_token", tok)
    return c


def test_inicio_e_noticias_mudaram_de_endereco(cliente):
    import app.app as A

    anon = TestClient(A.app, raise_server_exceptions=False)
    assert anon.get("/", follow_redirects=False).status_code in (302, 303, 401)
    r = cliente.get("/")
    assert r.status_code == 200
    assert "Fixed Income Team" in r.text and "paula.toute@itaubba.com" in r.text
    assert "Bom dia, Allan" in r.text
    assert cliente.get("/noticias").status_code == 200
    for bloco in ("spreads", "secundario", "primario", "curvas", "noticias", "relatorios"):
        resp = cliente.get(f"/api/inicio/{bloco}")
        assert resp.status_code == 200, (bloco, resp.text[:300])
        json.dumps(resp.json())
