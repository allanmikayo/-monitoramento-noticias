"""Aba Mercado Secundário (23/10/2026) — substitui o Balcão B3.

O que estes testes guardam:
  * a leitura do export (cabeçalho fora da 1ª linha, número em padrão
    brasileiro, data como série do Excel ou AAAAMMDD) e o descarte do dia
    cortado pelo limite de 50 mil linhas do Excel;
  * reimportar o mesmo dia substitui, não duplica;
  * o TICKER é a chave da classificação: cadastro Anbima, depois arquivo de
    características, e sem cadastro o papel fica fora das duas classes;
  * a média diária usa os pregões da base como denominador e avisa quando a
    janela começa antes da base;
  * o giro usa o estoque do fim da janela;
  * rotas atrás de login, importação só para admin, /balcao redireciona.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.spreads import mercado_secundario as ms

IPCA = "IPCA + Incentivadas"
CDI = "CDI + Tradicionais"
CAB = ["devedor", "ticker", "rating", "agência", "data de ref. do rating", "index.",
       "data de ref. do negócio", "duration", "vencimento", "NTN-B/DI de referência",
       "setor", "subsetor", "taxa", "indicativo Anbima", "taxa NTN-B/DI", "volume", "spread (BPS)"]


def _linha(ticker, data, volume, spread, devedor="EMPRESA SA", index="IPCA"):
    return [devedor, ticker, None, None, "20261021", index, data, 3.0, None, None,
            "ENERGIA", "TRANSMISSAO", 7.5, 7.4, 6.8, volume, spread]


@pytest.fixture()
def banco():
    from app.db import Base, SessionLocal, engine
    from app.models import Debenture, DebentureCaracteristica, DebentureSpread, MercadoSecundario

    Base.metadata.create_all(engine)

    def limpar(db):
        for m in (MercadoSecundario, DebentureSpread, DebentureCaracteristica, Debenture):
            db.query(m).delete()
        db.commit()

    with SessionLocal() as db:
        limpar(db)
        db.add(Debenture(codigo="TRAN11", nome="Transmissora A", indexador="IPCA +", incentivada="Sim",
                         classe=IPCA, setor="Energia Elétrica", subsetor="Transmissão", grupo_economico="Grupo A"))
        db.add(Debenture(codigo="BANC11", nome="Locadora B", indexador="CDI +", incentivada="Não",
                         classe=CDI, setor="Locação", grupo_economico="Grupo B"))
        db.add(Debenture(codigo="PREF11", nome="Prefixada C", indexador="PRE", classe="Outros"))
        # Não precificado pela Anbima: classe sai da remuneração da emissão.
        db.add(DebentureCaracteristica(codigo="NOVA11", empresa="Nova Energia", indice_emissao="IPCA",
                                       taxa_emissao=7.0, incentivada="S", situacao="Registrado",
                                       estoque=50.0, data_estoque=date(2026, 10, 20)))
        db.add(DebentureSpread(codigo="TRAN11", data=date(2026, 10, 1), spread=90.0, estoque=200.0))
        db.add(DebentureSpread(codigo="TRAN11", data=date(2026, 10, 20), spread=95.0, estoque=100.0))
        db.add(DebentureSpread(codigo="TRAN11", data=date(2026, 10, 30), spread=95.0, estoque=999.0))
        db.commit()
        yield db
        limpar(db)


def _carregar(db, dias, extras=()):
    linhas = []
    for d in dias:
        linhas += [_linha("TRAN11", d, 10e6, 100.0), _linha("BANC11", d, 4e6, 150.0, index="DI+"),
                   _linha("PREF11", d, 1e6, None, index="PREFIXADO"), _linha("XXXX11", d, 5e6, 80.0)]
    linhas += list(extras)
    regs, _ = ms.converter_linhas(CAB, linhas)
    ms.importar(db, regs)
    db.commit()


# ---- leitura ---------------------------------------------------------------
def test_cabecalho_fora_da_primeira_linha_e_tipos_brasileiros():
    todas = [["VOLUME NEGOCIADO"], [None], ["Warning: Not all data was exported."], CAB,
             _linha(" tran11 ", 46316, "1.234.567,89", "95,5"), _linha("BANC11", "20261021", 10.0, 1.0),
             _linha(None, "20261021", 10.0, 1.0), _linha("SEMVOL", "20261021", None, 1.0)]
    i = ms.achar_cabecalho(todas)
    assert i == 3
    regs, descartadas = ms.converter_linhas(todas[i], todas[i + 1:])
    assert descartadas == 2
    assert regs[0]["codigo"] == "TRAN11"
    assert regs[0]["data"] == date(2026, 10, 21)          # série do Excel
    assert regs[0]["volume"] == pytest.approx(1234567.89)
    assert regs[0]["spread"] == pytest.approx(95.5)
    assert regs[1]["data"] == date(2026, 10, 21)          # AAAAMMDD


def test_cabecalho_sem_coluna_obrigatoria_explica_qual():
    with pytest.raises(ValueError, match="volume"):
        ms.converter_linhas(["ticker", "data de ref. do negócio"], [])


def test_dia_mais_antigo_do_export_cortado_e_descartado():
    regs, _ = ms.converter_linhas(CAB, [_linha("A1", date(2026, 5, 21), 1, 1), _linha("A1", date(2026, 5, 22), 1, 1)])
    sobra, dia = ms.descartar_dia_incompleto(regs)
    assert dia == date(2026, 5, 21) and [r["data"] for r in sobra] == [date(2026, 5, 22)]


def test_reimportar_o_mesmo_dia_substitui(banco):
    from app.models import MercadoSecundario

    _carregar(banco, [date(2026, 10, 21)])
    regs, _ = ms.converter_linhas(CAB, [_linha("TRAN11", date(2026, 10, 21), 77e6, 100.0)])
    ms.importar(banco, regs)
    banco.commit()
    linhas = banco.query(MercadoSecundario).filter_by(codigo="TRAN11").all()
    assert len(linhas) == 1 and linhas[0].volume == 77e6


# ---- cadastro por ticker ---------------------------------------------------
def test_ticker_e_a_chave_da_classificacao(banco):
    _carregar(banco, [date(2026, 10, 21)], [_linha("NOVA11", date(2026, 10, 21), 2e6, 70.0)])
    m = ms.mapa_tickers(banco)
    assert m["TRAN11"]["classe"] == IPCA and m["TRAN11"]["setor"] == "Energia Elétrica"
    assert m["TRAN11"]["emissor"] == "Transmissora A"       # do cadastro, não "EMPRESA SA" do export
    assert m["BANC11"]["classe"] == CDI
    assert m["PREF11"]["classe"] == "Outros"
    assert m["NOVA11"]["classe"] == IPCA and m["NOVA11"]["origem"] == "caracteristicas"
    assert m["XXXX11"]["classe"] == ms.SEM_CADASTRO
    assert m["XXXX11"]["emissor"] == "Empresa Sa"          # reserva do export, só para exibir


# ---- resumo ----------------------------------------------------------------
def test_resumo_media_diaria_e_janela_parcial(banco):
    dias = [date(2026, 10, 1) + timedelta(days=k) for k in range(21)]
    _carregar(banco, dias)
    r = ms.resumo(banco)
    assert r["referencia"] == "2026-10-21"
    jan = {j["chave"]: j for j in r["janelas"]}
    assert jan["5d"]["pregoes"] == 5 and not jan["5d"]["parcial"]
    assert jan["ano"]["parcial"] is True                    # base começa em 01/10
    assert jan["ano_anterior"]["pregoes"] == 0
    linha = {l["classe"]: l["janelas"] for l in r["linhas"]}
    assert linha[IPCA]["5d"]["media"] == pytest.approx(10e6)
    assert linha[CDI]["mes"]["media"] == pytest.approx(4e6)
    assert linha[IPCA]["ano_anterior"] is None
    assert r["cobertura"] == pytest.approx(14 / 20)


def test_dia_sem_negocio_da_classe_conta_como_zero(banco):
    _carregar(banco, [date(2026, 10, 20), date(2026, 10, 21)])
    from app.models import MercadoSecundario
    banco.query(MercadoSecundario).filter_by(codigo="BANC11", data=date(2026, 10, 20)).delete()
    banco.commit()
    linha = {l["classe"]: l["janelas"] for l in ms.resumo(banco)["linhas"]}
    assert linha[CDI]["5d"]["media"] == pytest.approx(2e6)


# ---- por classe ------------------------------------------------------------
def test_serie_e_ranking_com_giro_pelo_estoque_do_fim_da_janela(banco):
    _carregar(banco, [date(2026, 10, 20), date(2026, 10, 21)])
    s = ms.serie(banco, IPCA)
    assert [p["volume"] for p in s["pontos"]] == [10e6, 10e6]
    assert s["pontos"][0]["spread"] == pytest.approx(100.0)

    rk = ms.ranking(banco, IPCA, "semana", "giro")
    top = rk["linhas"][0]
    assert top["codigo"] == "TRAN11"
    assert top["estoque"] == 100.0                        # 20/10, não o de 30/10 (depois da janela)
    assert top["giro"] == pytest.approx(20e6 / 100e6)
    assert rk["pregoes"] == 2


def test_pontos_do_grafico_spread_por_data(banco):
    _carregar(banco, [date(2026, 10, 20), date(2026, 10, 21)])
    p = ms.pontos_spread(banco, IPCA, meses=None)
    assert p["datas"] == ["2026-10-20", "2026-10-21"]
    assert [t["codigo"] for t in p["tickers"]] == ["TRAN11"]
    assert p["pontos"][0][1:3] == [100.0, 10.0]


# ---- rotas -----------------------------------------------------------------
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
            "user": logado("ms-user@a.com", "user"), "admin": logado("ms-admin@a.com", "admin")}


def test_rotas_atras_de_login_e_importacao_so_para_admin(clientes):
    c = clientes
    assert c["anon"].get("/api/mercado-secundario/resumo", follow_redirects=False).status_code in (302, 303, 401)
    assert c["user"].get("/mercado-secundario").status_code == 200
    r = c["user"].get("/balcao", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/mercado-secundario"
    corpo = {"cabecalho": CAB, "linhas": [_linha("TRAN11", "20261021", 5e6, 90.0)], "primeiro": True}
    assert c["user"].post("/api/admin/mercado-secundario/importar", json=corpo, follow_redirects=False).status_code in (302, 303, 403)
    r = c["admin"].post("/api/admin/mercado-secundario/importar", json=corpo)
    assert r.status_code == 200, r.text
    assert r.json()["gravadas"] == 1
    d = c["user"].get("/api/mercado-secundario/resumo").json()
    assert d["referencia"] == "2026-10-21"
    assert c["user"].get("/api/mercado-secundario/serie?classe=Outros").status_code == 400
    assert c["user"].get("/api/mercado-secundario/ranking",
                         params={"classe": IPCA, "metrica": "giro", "janela": "mes"}).status_code == 200
    assert c["user"].get("/api/mercado-secundario/pontos", params={"classe": IPCA, "meses": 3}).status_code == 200
