"""Aba Balcão B3 — consultas de volumetria e rotas.

Cobre o que já custou caro em outros módulos deste projeto:

  * módulo escrito e rota NÃO registrada no app.py (bug de 11/08/2026, que
    passou porque só havia teste de unidade);
  * teste que "passa" recebendo a página de login com status 200 (bug de
    13/08, corrigido em 20/08 — por isso o cliente aqui já vem autenticado
    e existe um teste separado exigindo o login);
  * lista vazia de filtro significando "nenhum" em vez de "todos".
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest


@pytest.fixture()
def banco():
    """Base sintética: 3 tickers, 10 pregões, volumes conhecidos.

    Usa o banco do `conftest.py` (arquivo temporário, já apontado por
    `DATABASE_URL` antes de qualquer import de `app.*`) e LIMPA as tabelas
    que este arquivo usa no começo de cada teste.

    A primeira versão trocava `DATABASE_URL` e apagava `app.*` de
    `sys.modules` para forçar um banco próprio. Funcionava isolada e
    quebrava 6 testes de outros arquivos ao rodar a suíte inteira: o
    `app.db` reimportado ficava com um engine apontando para outro arquivo,
    e quem já tinha importado antes seguia com o engine velho.
    """
    from app.db import Base, SessionLocal, engine
    from app.models import (Debenture, DebentureSpread, NegocioB3,
                            NegocioB3Diario)

    Base.metadata.create_all(engine)
    hoje = date(2026, 8, 18)

    with SessionLocal() as db:
        for modelo in (NegocioB3, NegocioB3Diario, DebentureSpread, Debenture):
            db.query(modelo).delete()
        db.commit()

    with SessionLocal() as db:
        db.add(Debenture(codigo="DEBA11", nome="EMISSOR A", indexador="IPCA +",
                         classe="IPCA + Incentivadas"))
        db.add(Debenture(codigo="DEBB22", nome="EMISSOR B", indexador="CDI +",
                         classe="CDI + Tradicionais"))
        # estoque: só DEBA11 tem -> só ele deve ter giro
        db.add(DebentureSpread(codigo="DEBA11", data=hoje, estoque=100.0, spread=250.0))

        for i in range(10):
            d = hoje - timedelta(days=i)
            # DEBA11: volume estável e spread subindo no dia mais recente
            db.add(NegocioB3Diario(
                codigo="DEBA11", data=d, instrument_type="DEB",
                n_negocios=10, volume=1_000_000.0, quantidade=1000.0,
                taxa_media=7.5, spread_medio=300.0 if i == 0 else 250.0,
                preco_medio=1000.0, maior_negocio=500_000.0))
            db.add(NegocioB3Diario(
                codigo="DEBB22", data=d, instrument_type="DEB",
                n_negocios=5, volume=400_000.0, quantidade=400.0,
                taxa_media=1.8, spread_medio=180.0,
                preco_medio=1000.0, maior_negocio=200_000.0))
            # CRI sem cadastro: entra no volume, fica sem giro
            db.add(NegocioB3Diario(
                codigo="CRI0001", data=d, instrument_type="CRI",
                n_negocios=3, volume=200_000.0, quantidade=200.0,
                taxa_media=9.0, spread_medio=None,
                preco_medio=1000.0, maior_negocio=100_000.0))

        # bruto para o tape: um confirmado e um cancelado
        db.add(NegocioB3(trade_code="#1", codigo="DEBA11", data_negocio=hoje,
                         instrument_type="DEB", emissor="EMISSOR A", volume=500_000.0,
                         taxa=7.5, spread=300.0, horario="10:30", situacao="Confirmado"))
        db.add(NegocioB3(trade_code="#2", codigo="DEBA11", data_negocio=hoje,
                         instrument_type="DEB", emissor="EMISSOR A", volume=999.0,
                         taxa=99.0, spread=999.0, horario="11:00", situacao="Cancelado"))
        db.commit()

    yield SessionLocal


@pytest.fixture()
def db(banco):
    with banco() as s:
        yield s


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def test_volumetria_usa_pregoes_como_denominador(db):
    """Média diária tem que dividir por pregão COM negócio, não por dia de
    calendário — senão feriado e fim de semana derrubam a média."""
    from app.spreads import balcao

    r = balcao.volumetria(db)
    semana = next(j for j in r["janelas"] if j["chave"] == "semana")
    assert semana["pregoes"] == 7
    # 7 dias x (1.000.000 + 400.000 + 200.000)
    assert semana["volume"] == pytest.approx(7 * 1_600_000.0)
    assert semana["volume_dia"] == pytest.approx(1_600_000.0)


def test_volumetria_referencia_e_a_data_com_dado(db):
    """Nunca `date.today()`: a B3 publica em D+1 e a tela apareceria vazia
    toda manhã."""
    from app.spreads import balcao
    assert balcao.volumetria(db)["referencia"] == "2026-08-18"


def test_ranking_calcula_giro_so_com_estoque(db):
    from app.spreads import balcao

    linhas = {l["codigo"]: l for l in balcao.ranking_tickers(db, dias=5)["linhas"]}
    # DEBA11: 5 x 1.000.000 sobre estoque de 100 milhões = 5%
    assert linhas["DEBA11"]["giro"] == pytest.approx(5_000_000.0 / 100_000_000.0)
    # sem cadastro de estoque -> None, e não zero nem número inventado
    assert linhas["CRI0001"]["giro"] is None
    assert linhas["CRI0001"]["volume"] > 0


def test_ranking_traz_emissor_e_indexador_do_cadastro(db):
    from app.spreads import balcao
    linhas = {l["codigo"]: l for l in balcao.ranking_tickers(db, dias=5)["linhas"]}
    assert linhas["DEBA11"]["emissor"] == "EMISSOR A"
    assert linhas["DEBB22"]["indexador"] == "CDI +"
    assert linhas["CRI0001"]["emissor"] is None


def test_volume_spread_conta_quem_ficou_de_fora(db):
    """Papel excluído não pode sumir em silêncio — e as duas exclusões são
    diferentes: 'não tem spread' e 'não tem cadastro' contam separado.

    O CRI da base não tem `Debenture`, logo não tem classe. Se o filtro de
    classe rodasse antes da checagem, ele sairia sem entrar em contagem
    nenhuma — foi o bug que este teste pegou."""
    from app.spreads import balcao

    r = balcao.volume_por_spread(db, dias=5, classe="IPCA + Incentivadas")
    assert [p["codigo"] for p in r["pontos"]] == ["DEBA11"]
    assert r["sem_cadastro"] >= 1, "o CRI sem cadastro precisa ser contado"
    # DEBB22 é CDI+, então sai pelo filtro de classe (não é 'sem spread')
    assert r["sem_spread"] == 0


def test_tape_ignora_cancelado(db):
    """~6% do arquivo da B3 é cancelado/ajustado em D+1."""
    from app.spreads import balcao

    linhas = balcao.tape(db)["linhas"]
    assert [l["codigo"] for l in linhas] == ["DEBA11"]
    assert all(l["taxa"] != 99.0 for l in linhas)


def test_destaques_compara_com_baseline_de_3_pregoes(db):
    from app.spreads import balcao

    r = balcao.destaques(db, dias_baseline=3)
    aberturas = {l["codigo"]: l for l in r["aberturas"]}
    assert "DEBA11" in aberturas
    # hoje 300, baseline 250 -> +50 bps
    assert aberturas["DEBA11"]["variacao_bps"] == pytest.approx(50.0)
    assert aberturas["DEBA11"]["spread_baseline"] == pytest.approx(250.0)


def test_destaques_exige_piso_de_liquidez(db, banco):
    """Sem piso, o topo do ranking vira papel ilíquido com um print solto."""
    from app.models import NegocioB3Diario
    from app.spreads import balcao

    hoje = date(2026, 8, 18)
    with banco() as s:
        for i in range(4):
            s.add(NegocioB3Diario(codigo="ILIQ01", data=hoje - timedelta(days=i),
                                  instrument_type="DEB", n_negocios=1,
                                  volume=1000.0, spread_medio=900.0 - i))
        s.commit()

    r = balcao.destaques(db, dias_baseline=3)
    assert "ILIQ01" not in {l["codigo"] for l in r["aberturas"] + r["fechamentos"]}


def test_termometro_compara_com_mediana(db):
    from app.spreads import balcao

    r = balcao.termometro(db)
    assert r["volume"] == pytest.approx(1_600_000.0)
    assert r["mediana"] == pytest.approx(1_600_000.0)
    assert r["razao"] == pytest.approx(1.0)


def test_filtro_de_tipo(db):
    from app.spreads import balcao

    so_cri = balcao.ranking_tickers(db, dias=5, tipos=["CRI"])["linhas"]
    assert {l["codigo"] for l in so_cri} == {"CRI0001"}

    todos = balcao.ranking_tickers(db, dias=5, tipos=None)["linhas"]
    assert len(todos) == 3


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------
ROTAS = [
    "/api/balcao/volumetria",
    "/api/balcao/serie",
    "/api/balcao/ranking",
    "/api/balcao/volume-spread",
    "/api/balcao/tape",
    "/api/balcao/destaques",
    "/api/balcao/termometro",
]


def _token(SessionLocal):
    from app import auth
    from app.models import User
    with SessionLocal() as db:
        u = db.query(User).first()
        if u is None:
            u = User(email="a@a.com", name="Teste", role="admin", active=True,
                     password_hash=auth.hash_password("x" * 10), email_confirmed=True)
            db.add(u)
            db.commit()
        t = auth.create_session(db, u, ip="1", user_agent="teste").token
        db.commit()
        return t


@pytest.fixture()
def cliente(banco):
    from fastapi.testclient import TestClient
    import app.app as A
    c = TestClient(A.app, raise_server_exceptions=False)
    c.cookies.set("session_token", _token(banco))
    return c


@pytest.mark.parametrize("rota", ROTAS)
def test_rota_responde(cliente, rota):
    """Pega o caso 'módulo escrito, rota não registrada no app.py'."""
    r = cliente.get(rota, follow_redirects=False)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("rota", ROTAS)
def test_rota_exige_login(banco, rota):
    from fastapi.testclient import TestClient
    import app.app as A
    anon = TestClient(A.app, raise_server_exceptions=False)
    r = anon.get(rota, follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403), r.status_code


def test_pagina_carrega_e_tem_os_blocos(cliente):
    r = cliente.get("/balcao", follow_redirects=False)
    assert r.status_code == 200
    for termo in ("cartoes-volumetria", "grafico-serie", "tabela-ranking",
                  "grafico-volume-spread", "tabela-tape", "tabela-aberturas",
                  "/static/balcao.js"):
        assert termo in r.text, f"{termo} sumiu do template"


def test_link_no_menu(cliente):
    assert 'href="/balcao"' in cliente.get("/balcao").text


def test_tipo_invalido_recusado(cliente):
    assert cliente.get("/api/balcao/ranking?tipo=LFSN").status_code == 400


def test_tipo_vazio_significa_todos(cliente):
    """Convenção do resto do Hub: nada marcado = tudo, não nada."""
    todos = cliente.get("/api/balcao/ranking").json()
    filtrado = cliente.get("/api/balcao/ranking?tipo=DEB&tipo=CRI").json()
    assert len(todos["linhas"]) == len(filtrado["linhas"]) == 3


def test_dias_fora_do_limite_recusado(cliente):
    assert cliente.get("/api/balcao/ranking?dias=0").status_code == 400
    assert cliente.get("/api/balcao/ranking?dias=9999").status_code == 400
