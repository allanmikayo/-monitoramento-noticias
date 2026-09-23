"""Segunda linha de filtros, base "Personalizado" e os dois modos do gráfico
Spread x Duration (24/09/2026, pedido do Allan).

O QUE ESTES TESTES SEGURAM
--------------------------
1. O recorte (setor/subsetor/grupo) vale para a PÁGINA INTEIRA. Um bloco que
   ignorasse o filtro mostraria o mercado inteiro ao lado de um bloco
   filtrado -- e os dois pareceriam certos.
2. "Personalizado" não é um caminho paralelo: a data escolhida vira o mesmo
   `dias_comparacao` das bases nomeadas (ver `queries.passos_ate`).
3. A média 3M NÃO se mexe quando muda a base de comparação. O Allan levantou
   isso olhando a tela (24/09/2026); o que muda ali é a ORDEM das linhas,
   que a tabela ordena pela variação.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.spreads import queries

CLASSE = "IPCA + Incentivadas"
CLASSE_Q = "IPCA%20%2B%20Incentivadas"
DATAS = [date(2026, 9, 1) + timedelta(days=i) for i in range(10)]   # 01 a 10/09


@pytest.fixture()
def banco():
    from app.db import Base, SessionLocal, engine
    from app.models import Debenture, DebentureSpread

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.query(DebentureSpread).delete()
        db.query(Debenture).delete()
        # setor, subsetor, grupo, spread inicial, estoque
        papeis = [
            ("SAN1", "AEGEA", "Saneamento", "Água e Esgoto", "Grupo Aegea", 100.0, 100.0),
            ("SAN2", "IGUA", "Saneamento", "Resíduos", "Grupo Iguá", 140.0, 100.0),
            ("ENE1", "CPFL", "Energia Elétrica", "Transmissão", "Grupo CPFL", 60.0, 100.0),
            ("ENE2", "EQUATORIAL", "Energia Elétrica", "Distribuição", "Grupo Equatorial", 80.0, 100.0),
        ]
        for cod, nome, setor, sub, grupo, spread, estoque in papeis:
            db.add(Debenture(codigo=cod, nome=nome, classe=CLASSE, indexador="IPCA +",
                             setor=setor, subsetor=sub, grupo_economico=grupo))
            for i, d in enumerate(DATAS):
                db.add(DebentureSpread(codigo=cod, data=d, spread=spread + i,
                                       estoque=estoque, duration=4.0, pct_pu_par=95.0 - i))
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


# --------------------------- o recorte da barra -----------------------------

def test_opcoes_vem_da_base_e_nao_de_uma_lista_fixa(banco):
    with banco() as db:
        op = queries.opcoes_filtro(db, CLASSE)
    assert op["setores"] == ["Energia Elétrica", "Saneamento"]
    assert {x["subsetor"] for x in op["subsetores"]} == {
        "Água e Esgoto", "Resíduos", "Transmissão", "Distribuição"}
    # O subsetor vem com o setor ao lado -- é o que permite a tela mostrar só
    # os subsetores dos setores escolhidos.
    assert {x["setor"] for x in op["subsetores"] if x["subsetor"] == "Resíduos"} == {"Saneamento"}
    assert "Grupo Aegea" in op["grupos"]


def test_filtro_de_setor_vale_para_kpi_tabela_e_desagios(banco):
    with banco() as db:
        so_saneamento = queries.Filtros(setores=("Saneamento",))
        kpi = queries.kpi_summary(db, CLASSE, filtros=so_saneamento)
        assert kpi["n_ativos"] == 2
        # spreads de 109 e 149 no último dia, estoques iguais -> média 129
        assert kpi["spread_medio"] == 129.0

        tabela = queries.spread_por_setor(db, CLASSE, filtros=so_saneamento)
        assert [l["rotulo"] for l in tabela["linhas"]] == ["Saneamento"]

        desagios = queries.maiores_desagios(db, CLASSE, filtros=so_saneamento)
        assert {p["codigo"] for p in desagios["papeis"]} == {"SAN1", "SAN2"}

        serie = queries.time_series(db, CLASSE, filtros=so_saneamento)
        assert serie[-1]["n_ativos"] == 2


def test_filtros_se_somam_com_e_entre_categorias(banco):
    with banco() as db:
        f = queries.Filtros(setores=("Saneamento", "Energia Elétrica"),
                            grupos=("Grupo Aegea",))
        kpi = queries.kpi_summary(db, CLASSE, filtros=f)
        assert kpi["n_ativos"] == 1          # só AEGEA, que é dos dois recortes
        # Subsetor de outro setor não devolve nada -- e isso é o certo.
        vazio = queries.kpi_summary(
            db, CLASSE, filtros=queries.Filtros(setores=("Saneamento",),
                                                subsetores=("Transmissão",)))
        assert vazio["n_ativos"] == 0


def test_dispersao_traz_spread_e_variacao_no_mesmo_ponto(banco):
    """Os dois modos do gráfico (nível e movimento) leem o MESMO payload."""
    with banco() as db:
        d = queries.variacao_por_duration(db, CLASSE, dias_comparacao=5)
    ponto = {p["codigo"]: p for p in d["pontos"]}["SAN1"]
    assert ponto["spread"] == 109.0 and ponto["variacao"] == 5.0


# ------------------------------- personalizado ------------------------------

def test_passos_ate_converte_data_em_posicoes(banco):
    with banco() as db:
        datas = queries.distinct_dates(db, CLASSE)
    hoje = datas[0]
    assert queries.passos_ate(datas, hoje, DATAS[-3]) == 2
    # Data sem pregão cai na primeira anterior COM dado.
    assert queries.passos_ate(datas, hoje, DATAS[0] - timedelta(days=30)) == len(datas) - 1


def test_rota_personalizado_exige_data_inicial(cliente):
    r = cliente.get(f"/api/spreads/summary?classe={CLASSE_Q}&base=Personalizado")
    assert r.status_code == 400 and "inicio" in r.text
    r = cliente.get(f"/api/spreads/summary?classe={CLASSE_Q}&base=Personalizado"
                    f"&inicio={DATAS[-1].isoformat()}")
    assert r.status_code == 400   # data inicial não pode ser a própria data analisada


def test_personalizado_compara_contra_a_data_escolhida(cliente):
    alvo = DATAS[2].isoformat()
    dados = cliente.get(f"/api/spreads/summary?classe={CLASSE_Q}&base=Personalizado"
                        f"&inicio={alvo}").json()
    assert dados["data_comparacao"] == alvo
    assert dados["data_referencia"] == DATAS[-1].isoformat()
    # +1 bps por dia em todos os papéis, 7 pregões de diferença.
    assert dados["variacao_bps"] == 7.0


def test_bases_da_tela_sao_so_tres_mais_personalizado(cliente):
    html = cliente.get("/spreads").text
    for base in ("d-1", "WoW", "MoM", "Personalizado"):
        assert f'data-base="{base}"' in html
    for fora in ("QoQ", "SoS", "YoY"):
        assert f'data-base="{fora}"' not in html


# ------------------------------- média 3M -----------------------------------

def test_media_3m_nao_muda_com_a_base_de_comparacao(banco):
    """Regressão da dúvida do Allan (24/09/2026): trocar WoW por MoM não
    mexe na média de 3 meses -- o que muda é a ordem das linhas, porque a
    tabela ordena pela variação."""
    with banco() as db:
        por_base = {}
        for dias in (1, 5, 21):
            linhas = queries.spread_por_setor(db, CLASSE, dias_comparacao=dias)["linhas"]
            por_base[dias] = {l["rotulo"]: l["spread_3m"] for l in linhas}
    assert por_base[1] == por_base[5] == por_base[21]


def test_media_3m_muda_com_a_data_final(banco):
    """...mas muda com a data analisada, que é o que ela promete: os três
    meses que ANTECEDEM a data."""
    with banco() as db:
        cedo = queries.spread_por_setor(db, CLASSE, data_referencia=DATAS[2])["linhas"]
        tarde = queries.spread_por_setor(db, CLASSE, data_referencia=DATAS[-1])["linhas"]
    assert ({l["rotulo"]: l["spread_3m"] for l in cedo}
            != {l["rotulo"]: l["spread_3m"] for l in tarde})


def test_desagio_inclui_papel_sem_taxa_indicativa(banco):
    """Dúvida do Allan (24/09/2026), olhando a Raízen na lista: a Anbima
    publica o % do PU par de papéis que não tiveram taxa indicativa na
    data. Eles entram aqui de propósito -- o deságio é dado publicado --,
    mas ficam fora de toda conta de spread. A tela marca com asterisco."""
    from app.db import SessionLocal
    from app.models import Debenture, DebentureSpread

    with SessionLocal() as db:
        db.add(Debenture(codigo="SEMTX1", nome="SEM TAXA", classe=CLASSE,
                         indexador="IPCA +", setor="Saneamento", subsetor="Resíduos"))
        db.add(DebentureSpread(codigo="SEMTX1", data=DATAS[-1], spread=None,
                               estoque=100.0, duration=4.0, pct_pu_par=60.0))
        db.commit()

        desagios = queries.maiores_desagios(db, CLASSE)
        primeiro = desagios["papeis"][0]
        assert primeiro["codigo"] == "SEMTX1" and primeiro["spread"] is None
        # ...e não contamina nenhuma média:
        assert queries.kpi_summary(db, CLASSE)["n_ativos"] == 4


# --------------------------------- a tela -----------------------------------

def test_tela_sem_busca_de_ativo_e_com_a_segunda_linha(cliente):
    html = cliente.get("/spreads").text
    assert 'id="busca-ativo"' not in html and 'id="busca-resultados"' not in html
    assert 'id="barra-filtros-2"' in html and 'id="btn-mais-filtros"' in html
    for menu in ("menu-setor", "menu-subsetor", "menu-grupo"):
        assert f'id="{menu}"' in html
    assert "Spread x Duration" in html and 'id="dispersao-tabs"' in html


def test_rotas_aceitam_o_recorte(cliente):
    recorte = "&setor=Saneamento&grupo=Grupo+Aegea"
    for rota in ("summary", "movers", "movement-distribution",
                 "variacao-por-duration", "desagios", "series"):
        r = cliente.get(f"/api/spreads/{rota}?classe={CLASSE_Q}{recorte}")
        assert r.status_code == 200, (rota, r.text[:200])
    r = cliente.get(f"/api/spreads/por-setor?classe={CLASSE_Q}"
                    "&filtro_setor=Saneamento&filtro_grupo=Grupo+Aegea")
    assert r.status_code == 200 and [l["rotulo"] for l in r.json()["linhas"]] == ["Saneamento"]
