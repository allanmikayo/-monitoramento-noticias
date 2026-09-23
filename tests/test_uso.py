"""Registro de uso e painel /admin/uso (22/09/2026) -- ver app/uso.py."""
from __future__ import annotations

import pytest

from app import auth, uso
from app.db import Base, SessionLocal, engine
from app.models import UsoEvento, User


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    s.query(UsoEvento).delete()
    s.query(User).filter(User.email.like("%@teste-uso.com")).delete(synchronize_session=False)
    s.commit()
    yield s
    s.query(UsoEvento).delete()
    s.query(User).filter(User.email.like("%@teste-uso.com")).delete(synchronize_session=False)
    s.commit()
    s.close()


def _user(db, email, role="user"):
    u = User(name=email.split("@")[0], email=email, password_hash="", role=role,
             active=True, email_confirmed=True)
    db.add(u)
    db.commit()
    return u


def test_vocabulario_fechado(db):
    assert not uso.registrar(db, aba="Qualquer", acao="visita")
    assert not uso.registrar(db, aba="Spreads", acao="apagar_tudo", alvo="x")
    assert not uso.registrar(db, aba="Spreads", acao="empresa", alvo="   ")
    assert uso.registrar(db, aba="Spreads", acao="empresa", alvo="x" * 500)
    assert len(db.query(UsoEvento).one().alvo) == 200


def test_painel_soma_empresa_de_abas_diferentes(db):
    ana = _user(db, "ana@teste-uso.com")
    uso.registrar(db, aba="Repositório", acao="empresa", alvo="Sabesp", user_id=ana.id)
    uso.registrar(db, aba="Notícias", acao="empresa", alvo="Sabesp", user_id=ana.id)
    uso.registrar(db, aba="Spreads", acao="emissor", alvo="Sabesp", visitante="v1")
    uso.registrar(db, aba="Repositório", acao="empresa", alvo="Copasa", visitante="v2")
    p = uso.painel(db, 30)
    top = p["empresas"][0]
    assert top["nome"] == "Sabesp" and top["vezes"] == 3 and top["pessoas"] == 2
    assert top["onde"] == ["Notícias", "Repositório", "Spreads"]
    assert p["kpis"]["pessoas"] == 1 and p["kpis"]["anonimos"] == 2


def test_admin_fica_fora_por_padrao(db):
    adm = _user(db, "adm@teste-uso.com", "admin")
    bia = _user(db, "bia@teste-uso.com")
    uso.registrar(db, aba="Spreads", acao="visita", user_id=adm.id)
    uso.registrar(db, aba="Notícias", acao="visita", user_id=bia.id)
    assert [a["nome"] for a in uso.painel(db, 30)["abas"]] == ["Notícias"]
    assert {a["nome"] for a in uso.painel(db, 30, incluir_admin=True)["abas"]} == {"Notícias", "Spreads"}


def test_filtro_por_pessoa_e_tabela_por_pessoa(db):
    ana = _user(db, "ana@teste-uso.com")
    bia = _user(db, "bia@teste-uso.com")
    uso.registrar(db, aba="Spreads", acao="visita", user_id=ana.id)
    uso.registrar(db, aba="Spreads", acao="setor", alvo="Saneamento", user_id=ana.id)
    uso.registrar(db, aba="Notícias", acao="visita", user_id=bia.id)
    p = uso.painel(db, 30, user_id=ana.id)
    assert p["kpis"]["pessoas"] == 1 and p["setores"][0]["nome"] == "Saneamento"
    linha = p["por_pessoa"][0]
    assert linha["email"] == "ana@teste-uso.com" and linha["aba_preferida"] == "Spreads"
    assert linha["interesses"] == ["Saneamento"]


def test_serie_tem_todos_os_dias(db):
    assert len(uso.painel(db, 7)["serie"]) == 7


def test_csv_abre_no_excel(db):
    ana = _user(db, "ana@teste-uso.com")
    uso.registrar(db, aba="Repositório", acao="relatorio", alvo="Sabesp 2T26", user_id=ana.id)
    txt = uso.csv_eventos(db, 30, False, None)
    assert txt.startswith("﻿") and "ana@teste-uso.com;;Repositório;Relatório aberto;Sabesp 2T26" in txt


# ------------------------------- rotas -------------------------------------

@pytest.fixture()
def cliente(db):
    from fastapi.testclient import TestClient

    import app.app as A

    adm = db.query(User).filter_by(role="admin").first() or _user(db, "chefe@teste-uso.com", "admin")
    comum = _user(db, "comum@teste-uso.com")
    tok_adm = auth.create_session(db, adm, ip="1", user_agent="t").token
    tok_comum = auth.create_session(db, comum, ip="1", user_agent="t").token
    return TestClient(A.app, raise_server_exceptions=False), tok_adm, tok_comum


def test_api_registra_com_e_sem_login(cliente, db):
    c, tok_adm, tok_comum = cliente
    h = {"X-Hub-Uso": "1"}
    assert c.post("/api/uso", json={"aba": "Repositório", "acao": "visita", "visitante": "abc"},
                  headers=h).json() == {"ok": True}
    c.cookies.set("session_token", tok_comum)
    c.post("/api/uso", json={"aba": "Spreads", "acao": "ticker", "alvo": "SBSPA1"}, headers=h)
    evs = db.query(UsoEvento).order_by(UsoEvento.id).all()
    assert evs[0].user_id is None and evs[0].visitante == "abc"
    assert evs[1].user_id is not None and evs[1].alvo == "SBSPA1"


def test_api_exige_cabecalho_do_site(cliente, db):
    c, _, _ = cliente
    r = c.post("/api/uso", json={"aba": "Spreads", "acao": "visita"})
    assert r.status_code == 400 and db.query(UsoEvento).count() == 0


def test_painel_so_admin(cliente):
    c, tok_adm, tok_comum = cliente
    c.cookies.set("session_token", tok_comum)
    assert c.get("/admin/uso", follow_redirects=False).status_code == 403
    assert c.get("/api/admin/uso", follow_redirects=False).status_code == 403
    assert c.get("/api/admin/uso.csv", follow_redirects=False).status_code == 403
    c.cookies.set("session_token", tok_adm)
    r = c.get("/admin/uso")
    assert r.status_code == 200 and "uso-painel.js?v=" in r.text and "?v=\"" not in r.text
    assert c.get("/api/admin/uso?dias=7").json()["dias"] == 7


def test_todas_as_paginas_carregam_o_registro(cliente):
    c, tok_adm, _ = cliente
    c.cookies.set("session_token", tok_adm)
    for pagina in ("/", "/spreads", "/balcao", "/cobertura", "/fontes", "/admin"):
        assert "/static/uso.js?v=" in c.get(pagina).text, pagina
