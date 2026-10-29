"""Aba Cap. Líquida (29/10/2026).

O que estes testes guardam:
  * o tipo do export (diário/mensal) vem do cabeçalho;
  * o nome da gestora NUNCA é gravado -- só um hash estável;
  * reimportar substitui o período do arquivo (gestora que sumiu some);
  * as contas dos quadros: Last PL, ano, %PL do ano (÷ último PL) e do mês
    (÷ PL do mês), mês fechado vs corrente;
  * amplitude e concentração sem nomes;
  * rotas atrás de login, importação só para admin.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import captacao as cl

CAB_D = ["data_anls", "ano", "mes", "dia", "segmento", "nom_clie_gesr", "qtd_fundos", "patr_liqo", "cpit_liqa", "data_extracao"]
CAB_M = ["mes_ref", "ano", "mes", "segmento", "nom_clie_gesr", "qtd_fundos", "patr_liqo", "cpit_liqa_mtd", "data_extracao"]


def _d(data, seg, gest, fundos, pl, cap):
    return [data, data[:4], "1", "1", seg, gest, str(fundos), str(pl), str(cap), "2026-10-29"]


def _m(mes, seg, gest, fundos, pl, cap):
    return [mes, mes[:4], "1", seg, gest, str(fundos), str(pl), str(cap), "2026-10-29"]


@pytest.fixture()
def banco():
    from app.db import Base, SessionLocal, engine
    from app.models import CaptacaoDiaria, CaptacaoMensal

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.query(CaptacaoDiaria).delete()
        db.query(CaptacaoMensal).delete()
        db.commit()
        yield db


def _carregar(db, cab, linhas):
    tipo, regs, _ = cl.converter_linhas(cab, linhas)
    datas = [r["data"] for r in regs]
    cl.apagar_periodo(db, tipo, min(datas), max(datas))
    cl.importar(db, tipo, regs)
    db.commit()


def test_tipo_pelo_cabecalho_e_nome_vira_hash():
    tipo, regs, desc = cl.converter_linhas(CAB_D, [
        _d("2026-10-22", "Infras", "GESTORA ALFA LTDA", 3, "1.5E9", "-2.0E6"),
        _d("2026-10-22", "Outro", "X", 1, 1, 1),  # segmento desconhecido: fora
    ])
    assert tipo == "diario" and desc == 1
    r = regs[0]
    assert r["segmento"] == cl.INFRA and r["pl"] == 1.5e9 and r["captacao"] == -2e6
    assert r["gestora"] == cl.hash_gestora("  gestora   alfa ltda ") and "ALFA" not in r["gestora"]
    assert cl.converter_linhas(CAB_M, [])[0] == "mensal"
    with pytest.raises(ValueError):
        cl.tipo_do_cabecalho(["ticker", "volume"])


def test_nome_da_gestora_nao_e_gravado(banco):
    from app.models import CaptacaoDiaria
    _carregar(banco, CAB_D, [_d("2026-10-22", "CP ex Infras", "GESTORA SECRETA SA", 2, 100, 5)])
    valores = banco.execute(select(CaptacaoDiaria.gestora)).scalars().all()
    assert valores and all("SECRETA" not in v and len(v) == 16 for v in valores)


def test_reimportar_substitui_o_periodo(banco):
    _carregar(banco, CAB_D, [_d("2026-10-21", "Infras", "A", 1, 100, 10),
                             _d("2026-10-22", "Infras", "A", 1, 100, 10),
                             _d("2026-10-22", "Infras", "B", 1, 100, 99)])
    # Nova extração do dia 22: a gestora B saiu. O dia 21 fica intocado.
    _carregar(banco, CAB_D, [_d("2026-10-22", "Infras", "A", 1, 100, 20)])
    d = cl.diario(banco, 15)
    assert d["datas"] == ["2026-10-21", "2026-10-22"]
    assert d["series"][cl.INFRA] == [10, 20]


def test_quadros_do_resumo(banco):
    _carregar(banco, CAB_D, [_d("2026-10-22", "Infras", "A", 3, 200, 1),
                             _d("2026-10-22", "CP ex Infras", "B", 5, 800, 1)])
    _carregar(banco, CAB_M, [
        _m("2025-06-01", "Infras", "A", 3, 150, 30),
        _m("2026-09-01", "Infras", "A", 3, 180, -18),
        _m("2026-09-01", "CP ex Infras", "B", 5, 700, 7),
        _m("2026-10-01", "Infras", "A", 3, 200, -4),
    ])
    r = cl.resumo(banco)
    assert r["referencia"] == "2026-10-22"
    pl = {l["segmento"]: l for l in r["last_pl"]}
    assert pl["Total CP"]["pl"] == 1000 and pl["Total CP"]["fundos"] == 8
    ano = {l["segmento"]: l for l in r["por_ano"]}
    assert r["anos"] == ["2024", "2025", "2026"]
    assert ano[cl.INFRA]["2025"] == 30 and ano[cl.INFRA]["2026"] == -22
    assert ano[cl.INFRA]["pct_pl_ytd"] == -11.0  # -22 / último PL 200
    assert r["mes_fechado"]["mes"] == "2026-09-01" and r["mes_corrente"]["mes"] == "2026-10-01"
    infra_set = next(l for l in r["mes_fechado"]["linhas"] if l["segmento"] == cl.INFRA)
    assert infra_set["pct_pl"] == -10.0  # -18 / PL de setembro 180


def test_amplitude_sem_nomes(banco):
    linhas = [_m("2026-09-01", "Infras", f"G{i}", 1, 100, c) for i, c in enumerate([50, -10, -30, 0])]
    _carregar(banco, CAB_M, linhas)
    a = cl.amplitude(banco, cl.INFRA)["meses"][-1]
    assert a["gestoras_captando"] == 1 and a["gestoras_resgatando"] == 2
    assert a["pct_pl_captando"] == 25.0 and a["pct_pl_resgatando"] == 50.0 and a["pct_pl_neutro"] == 25.0
    assert a["resgate_bruto"] == -40 and a["top_resgate_pct"] == 100.0
    assert "G1" not in str(a)


@pytest.fixture()
def clientes(banco):
    import app.app as A
    from app import auth
    from app.models import User

    def logado(email, role):
        u = banco.query(User).filter_by(email=email).first()
        if u is None:
            u = User(email=email, name="T", role=role, active=True,
                     password_hash=auth.hash_password("x" * 10), email_confirmed=True)
            banco.add(u)
            banco.commit()
        tok = auth.create_session(banco, u, ip="1", user_agent="t").token
        banco.commit()
        c = TestClient(A.app, raise_server_exceptions=False)
        c.cookies.set("session_token", tok)
        return c

    return {"anon": TestClient(A.app, raise_server_exceptions=False),
            "user": logado("cl-user@a.com", "user"), "admin": logado("cl-admin@a.com", "admin")}


def test_rotas(clientes):
    anon, user, admin = clientes["anon"], clientes["user"], clientes["admin"]
    assert anon.get("/captacao-liquida", follow_redirects=False).status_code in (302, 303, 401)
    assert user.get("/captacao-liquida").status_code == 200
    assert 'href="/captacao-liquida"' in user.get("/captacao-liquida").text
    for rota in ("/api/captacao/resumo", "/api/captacao/mensal", "/api/captacao/diario?dias=15",
                 "/api/captacao/amplitude?segmento=Infra"):
        assert user.get(rota).status_code == 200, rota
    assert user.get("/api/captacao/diario?dias=7").status_code == 400
    assert user.get("/api/captacao/amplitude?segmento=X").status_code == 400

    corpo = {"cabecalho": CAB_M, "linhas": [_m("2026-09-01", "Infras", "A", 1, 100, 5)],
             "periodo": ["2026-09-01", "2026-09-01"]}
    assert user.post("/api/admin/captacao/importar", json=corpo).status_code in (302, 303, 401, 403)
    r = admin.post("/api/admin/captacao/importar", json=corpo)
    assert r.status_code == 200 and r.json()["gravadas"] == 1
    assert admin.get("/api/admin/captacao/status").json()["mensal"]["linhas"] == 1
    assert admin.post("/api/admin/captacao/importar", json={"cabecalho": ["x"], "linhas": [[1]]}).status_code == 400
