"""Calendário de resultados da página inicial (28/10/2026). Ver app/calendario.py.

Guarda:
1. A temporada certa para cada data (3T em out/nov com prazo ITR de 45
   dias; 4T de jan a mar com a DFP e prazo 31/03).
2. A ordem de confiança: DIVULGADA (RAD ou CVM, a mais cedo) > data da
   AGENDA do time (lista importada ou ajuste do admin) > SEM DATA. A
   estimativa pelo histórico (mesmo trimestre do ano anterior + 364 dias,
   limitada ao prazo) só aparece como dica de quem está sem data.
3. Uma entrega ATRASADA do trimestre anterior vista no RAD ("Release 2T26"
   em outubro) não conta como divulgação do 3T.
4. O arquivo da CVM é lido pelo CSV principal do zip, em latin-1 e ";", e
   uma coluna obrigatória que some derruba a coleta em vez de gravar vazio.
5. Só a PRIMEIRA entrega de cada trimestre fica guardada.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import calendario as cal
from app.db import Base
from app.models import (
    Article, Company, CompanyAlias, Debenture, EmpresaTipoCobertura, EntregaResultadoCVM, ResultadoConfirmado,
    Sector,
)

HOJE = date(2026, 10, 28)


@pytest.mark.parametrize("hoje, ref, doc, prazo, rotulo", [
    (date(2026, 10, 28), date(2026, 9, 30), "ITR", date(2026, 11, 14), "3T26"),
    (date(2026, 10, 1), date(2026, 9, 30), "ITR", date(2026, 11, 14), "3T26"),
    (date(2026, 7, 15), date(2026, 6, 30), "ITR", date(2026, 8, 14), "2T26"),
    (date(2026, 4, 20), date(2026, 3, 31), "ITR", date(2026, 5, 15), "1T26"),
    (date(2027, 2, 10), date(2026, 12, 31), "DFP", date(2027, 3, 31), "4T26"),
    (date(2026, 12, 31), date(2026, 9, 30), "ITR", date(2026, 11, 14), "3T26"),
])
def test_temporada(hoje, ref, doc, prazo, rotulo):
    t = cal.temporada_para(hoje)
    assert (t.referencia, t.documento, t.prazo, t.rotulo) == (ref, doc, prazo, rotulo)


def _banco() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = Session(eng)
    s = Sector(name="Saneamento")
    db.add(s)
    db.flush()
    return db, s


def _empresa(db, setor, nome, cnpj=None, aliases=()):
    c = Company(name=nome, sector_id=setor.id, active=True)
    db.add(c)
    db.flush()
    for a in aliases:
        db.add(CompanyAlias(company_id=c.id, alias=a))
    if cnpj:
        db.add(Debenture(codigo=f"D{c.id}", nome=nome, cnpj=cnpj, company_id=c.id, classe="IPCA + Incentivadas"))
    return c


def _entrega(db, cnpj, ref, entrega, doc="ITR", denom=None):
    db.add(EntregaResultadoCVM(cnpj=cnpj, documento=doc, data_referencia=ref, data_entrega=entrega,
                               denominacao=denom, link=f"http://cvm/{cnpj}/{ref}"))


def _por_nome(r):
    return {e["nome"]: e for e in r["empresas"]}


def test_sem_data_traz_a_estimativa_so_como_dica():
    db, s = _banco()
    _empresa(db, s, "Aegea", cnpj="08.827.501/0001-58")
    _entrega(db, "08827501000158", date(2025, 9, 30), date(2025, 11, 6))   # quinta-feira
    db.commit()
    e = _por_nome(cal.agenda(db, HOJE))["Aegea"]
    assert e["status"] == "sem_data" and e["data"] is None and e["na_cvm"] is True
    assert e["estimativa"] == "2026-11-05"           # +364 dias: também quinta-feira
    assert "06/11/2025" in e["estimativa_origem"]


def test_estimativa_nunca_passa_do_prazo():
    db, s = _banco()
    _empresa(db, s, "Tardia", cnpj="11111111000111")
    _entrega(db, "11111111000111", date(2025, 9, 30), date(2025, 11, 20))  # atrasou no ano passado
    db.commit()
    assert _por_nome(cal.agenda(db, HOJE))["Tardia"]["estimativa"] == "2026-11-14"


def test_sem_o_trimestre_do_ano_anterior_usa_o_prazo_do_ultimo_trimestre():
    db, s = _banco()
    _empresa(db, s, "Nova", cnpj="22222222000122")
    _entrega(db, "22222222000122", date(2026, 6, 30), date(2026, 8, 7))    # 38 dias
    db.commit()
    e = _por_nome(cal.agenda(db, HOJE))["Nova"]
    assert e["estimativa"] == "2026-11-07" and "38 dias" in e["estimativa_origem"]


def test_data_da_agenda_que_passou():
    """Na CVM e sem entrega: pendente. Fora da CVM: vale a data da agenda."""
    db, s = _banco()
    lenta = _empresa(db, s, "Lenta", cnpj="33333333000133")
    _entrega(db, "33333333000133", date(2025, 9, 30), date(2025, 10, 23))
    off = _empresa(db, s, "Offshore SA")
    db.commit()
    cal.confirmar(db, lenta.id, date(2026, 10, 22), None, "a", hoje=HOJE)
    cal.confirmar(db, off.id, date(2026, 10, 22), None, "a", hoje=HOJE)
    r = _por_nome(cal.agenda(db, HOJE))
    assert r["Lenta"]["status"] == "pendente" and r["Lenta"]["data"] == "2026-10-22"
    assert r["Offshore SA"]["status"] == "divulgada" and "sem registro" in r["Offshore SA"]["origem"]


def test_divulgada_pela_cvm_vence_tudo():
    db, s = _banco()
    c = _empresa(db, s, "Sabesp", cnpj="43776517000180")
    _entrega(db, "43776517000180", date(2025, 9, 30), date(2025, 11, 12))
    _entrega(db, "43776517000180", date(2026, 9, 30), date(2026, 10, 27))
    db.add(ResultadoConfirmado(company_id=c.id, data_referencia=date(2026, 9, 30),
                               data_prevista=date(2026, 11, 12)))
    db.commit()
    e = _por_nome(cal.agenda(db, HOJE))["Sabesp"]
    assert e["status"] == "divulgada" and e["data"] == "2026-10-27"


def test_divulgada_pelo_rad_em_tempo_real_e_trimestre_errado_nao_conta():
    db, s = _banco()
    c = _empresa(db, s, "Neoenergia", cnpj="01083200000118")
    _entrega(db, "01083200000118", date(2025, 9, 30), date(2025, 10, 23))
    atrasado = Article(url="u1", domain="rad.cvm.gov.br", source_name="CVM", article_type="fato_relevante",
                       title="NEOENERGIA S.A. — Dados Econômico-Financeiros — Press-release",
                       snippet="Release de Resultados 2T26 - | AP",
                       published_at=datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc))
    certo = Article(url="u2", domain="rad.cvm.gov.br", source_name="CVM", article_type="fato_relevante",
                    title="NEOENERGIA S.A. — ITR - Informações Trimestrais", snippet="AP",
                    published_at=datetime(2026, 10, 27, 22, 19, tzinfo=timezone.utc))
    outro = Article(url="u3", domain="rad.cvm.gov.br", source_name="CVM", article_type="fato_relevante",
                    title="NEOENERGIA S.A. — Fato Relevante", snippet="Resultado do leilão",
                    published_at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc))
    for a in (atrasado, certo, outro):
        a.companies = [c]
        db.add(a)
    db.commit()
    e = _por_nome(cal.agenda(db, HOJE))["Neoenergia"]
    assert e["status"] == "divulgada"
    assert e["data"] == "2026-10-27"          # 22h19 UTC = 19h19 BRT do mesmo dia
    assert e["link"] == "u2" and "RAD" in e["origem"]


def test_data_da_agenda_e_tipo_ajustados_pelo_admin():
    db, s = _banco()
    c = _empresa(db, s, "Copasa", cnpj="17281106000103")
    _entrega(db, "17281106000103", date(2025, 9, 30), date(2025, 11, 6))
    db.commit()
    cal.confirmar(db, c.id, date(2026, 11, 10), "após o fechamento", "allan@x", hoje=HOJE, tipo="Local")
    e = _por_nome(cal.agenda(db, HOJE))["Copasa"]
    assert (e["status"], e["data"], e["horario"], e["tipo"]) == ("agendada", "2026-11-10", "após o fechamento", "Local")
    cal.confirmar(db, c.id, None, None, "allan@x", hoje=HOJE)          # apagar volta para sem data
    e = _por_nome(cal.agenda(db, HOJE))["Copasa"]
    assert e["status"] == "sem_data" and e["tipo"] == "Local"          # tipo não muda sem pedir


def test_toda_a_cobertura_aparece_mesmo_fora_da_cvm():
    db, s = _banco()
    _empresa(db, s, "SPE Sem Registro", cnpj="44444444000144")   # tem debênture, nunca entregou ITR
    _empresa(db, s, "Sem CNPJ")
    db.commit()
    r = cal.agenda(db, HOJE)
    assert {e["nome"] for e in r["empresas"]} == {"SPE Sem Registro", "Sem CNPJ"}
    assert all(e["status"] == "sem_data" and not e["na_cvm"] for e in r["empresas"])
    assert r["resumo"]["sem_data"] == 2


def test_grupo_com_varias_entidades_usa_a_primeira_que_divulgar_e_casa_pelo_nome():
    db, s = _banco()
    c = _empresa(db, s, "Equatorial", cnpj="03220438000173", aliases=["Equatorial Energia"])
    # Distribuidora do grupo, ligada pelo CNPJ da debênture
    db.add(Debenture(codigo="EQPA18", nome="EQUATORIAL PARA", cnpj="04895728000180", company_id=c.id))
    # Holding listada sem debênture na base: casa pelo nome normalizado
    _entrega(db, "99999999000199", date(2026, 9, 30), date(2026, 11, 3), denom="EQUATORIAL ENERGIA S.A.")
    _entrega(db, "04895728000180", date(2026, 9, 30), date(2026, 11, 4), denom="EQUATORIAL PARÁ DISTRIBUIDORA")
    db.commit()
    e = _por_nome(cal.agenda(db, HOJE))["Equatorial"]
    assert e["status"] == "divulgada" and e["data"] == "2026-11-03"
    assert len(e["entidades"]) == 2


def test_resumo_semanal():
    db, s = _banco()
    a = _empresa(db, s, "A", cnpj="10000000000101")
    _entrega(db, "10000000000101", date(2026, 9, 30), date(2026, 10, 27))      # divulgou na terça
    b = _empresa(db, s, "B")
    c = _empresa(db, s, "C")
    _empresa(db, s, "D")
    db.commit()
    cal.confirmar(db, a.id, date(2026, 10, 29), None, "x", hoje=HOJE)
    cal.confirmar(db, b.id, date(2026, 10, 30), None, "x", hoje=HOJE)
    cal.confirmar(db, c.id, date(2026, 11, 4), None, "x", hoje=HOJE)
    r = cal.agenda(db, HOJE)
    assert r["semanas"] == {"atual": ["2026-10-26", "2026-10-30"], "proxima": ["2026-11-02", "2026-11-06"]}
    res = r["resumo"]
    assert (res["divulgada"], res["agendada"], res["sem_data"], res["total"]) == (1, 2, 1, 4)
    assert (res["semana_total"], res["semana_divulgadas"], res["proxima_total"]) == (2, 1, 1)
    assert r["temporada"]["rotulo"] == "3T26" and r["temporada"]["dias_para_prazo"] == 17


# ---- lista do time ----------------------------------------------------------
LISTA = """Empresa Data Horário Cobertura Cor (HEX) Significado da cor
Norte Energia 19/10/2026 After Local #FFFF00 Em Execução
Klabin 28/10/2026 Before Offshore + Local #92D050 Publicado
Mercado Libre 04/11/2026 Confirmar Offshore #4D93D9 Offshore
EZtec	05/11/2026	After	Local	#E97132	Local
BRK 11/11/2026 Confirmar Local #FF0000 Restrito
Iguá Rio de Janeiro 12/11/2026 Confirmar Local #E97132 Local
CEEE Geração 12/11/2026 Confirmar Local #E97132 Local
Equatorial Energia 11/11/2026 Confirmar Local #E97132 Local
Empresa Fantasma 12/11/2026 After Local #E97132 Local
Rio + (Local)
"""


def test_le_a_lista_do_time():
    itens, ignoradas = cal.ler_lista(LISTA + "linha sem sentido\n")
    assert ignoradas == ["linha sem sentido"]
    por = {i["nome"]: i for i in itens}
    assert por["Klabin"] == {"nome": "Klabin", "data": date(2026, 10, 28), "horario": "antes da abertura",
                             "tipo": "Offshore + Local", "status_relatorio": "Publicado"}
    assert por["Mercado Libre"]["horario"] is None and por["Mercado Libre"]["tipo"] == "Offshore"
    assert por["EZtec"]["horario"] == "após o fechamento"            # colado do Excel, com tabs
    assert por["BRK"]["status_relatorio"] == "Restrito"
    assert por["Norte Energia"]["status_relatorio"] == "Em execução"
    assert por["Rio +"] == {"nome": "Rio +", "data": None, "horario": None, "tipo": "Local", "status_relatorio": None}


@pytest.mark.parametrize("da_lista, cadastro", [
    ("Norte Energia", "Norte Energia (Belo Monte)"),
    ("Mercado Libre", "Mercado Livre"),
    ("EZtec", "EZ Tec"),
    ("BRK", "BRK Ambiental"),
    ("Iguá Rio de Janeiro", "Iguá RJ"),
    ("CEEE Geração", "CEEE-G"),
    ("Rio +", "Rio+ Saneamento"),
    ("MRV&CO", "MRV&Co"),
    ("Equatorial Energia", "Equatorial"),
])
def test_casa_variacoes_de_nome(da_lista, cadastro):
    db, s = _banco()
    for n in ("Norte Energia (Belo Monte)", "Mercado Livre", "EZ Tec", "BRK Ambiental", "Iguá RJ", "CEEE-G",
              "CEEE-D", "Rio+ Saneamento", "MRV&Co", "Equatorial", "Energisa", "Neoenergia"):
        _empresa(db, s, n)
    db.commit()
    c = cal.casar_nome(da_lista, db.query(Company).all())
    assert c is not None and c.name == cadastro


def test_nome_ambiguo_nao_casa():
    db, s = _banco()
    _empresa(db, s, "Energisa Sergipe")
    _empresa(db, s, "Energisa Paraíba")
    db.commit()
    assert cal.casar_nome("Energisa", db.query(Company).all()) is None


def test_importar_substitui_a_agenda_e_relata_o_que_faltou():
    db, s = _banco()
    for n in ("Norte Energia (Belo Monte)", "Klabin", "Mercado Livre", "EZ Tec", "BRK Ambiental", "Iguá RJ",
              "CEEE-G", "Equatorial", "Rio+ Saneamento"):
        _empresa(db, s, n)
    velha = _empresa(db, s, "Fora da Lista", cnpj="55555555000155")
    _entrega(db, "55555555000155", date(2025, 9, 30), date(2025, 11, 6))
    db.commit()
    cal.confirmar(db, velha.id, date(2026, 11, 3), None, "x", hoje=HOJE)   # some ao importar
    rel = cal.importar_lista(db, LISTA, "allan", hoje=HOJE)
    assert rel["nao_reconhecidas"] == ["Empresa Fantasma"]
    assert rel["com_data"] == 8
    assert rel["sem_data_entrega_cvm"] == ["Fora da Lista"]
    assert rel["sem_data_fora_cvm"] == ["Rio+ Saneamento"]
    r = _por_nome(cal.agenda(db, HOJE))
    assert r["Klabin"]["status"] == "agendada" and r["Klabin"]["tipo"] == "Offshore + Local"
    assert r["BRK Ambiental"]["status_relatorio"] == "Restrito"
    assert r["Rio+ Saneamento"]["tipo"] == "Local" and r["Rio+ Saneamento"]["status"] == "sem_data"
    assert r["Fora da Lista"]["status"] == "sem_data"
    assert cal.ultimo_relatorio(db)["nao_reconhecidas"] == ["Empresa Fantasma"]
    with pytest.raises(ValueError):
        cal.importar_lista(db, "nada aqui", "allan", hoje=HOJE)


# ---- arquivo da CVM ---------------------------------------------------------
def _zip(nome_csv: str, linhas: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(nome_csv, "\n".join(linhas).encode("latin-1"))
        z.writestr("itr_cia_aberta_BPA_con_2026.csv", "lixo;que;nao;importa")
    return buf.getvalue()


CAB = "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;CATEG_DOC;ID_DOC;DT_RECEB;LINK_DOC"


def test_le_o_csv_principal_em_latin1():
    z = _zip("itr_cia_aberta_2026.csv", [
        CAB,
        "08.827.501/0001-58;2026-09-30;1;AEGEA SANEAMENTO E PARTICIPAÇÕES S.A.;022934;ITR;1;2026-11-05;http://x/1",
        "08.827.501/0001-58;2026-09-30;2;AEGEA SANEAMENTO E PARTICIPAÇÕES S.A.;022934;ITR;2;2026-11-20;http://x/2",
        "linha;quebrada",
    ])
    linhas = cal.ler_zip(z, "ITR")
    assert len(linhas) == 2
    assert linhas[0]["cnpj"] == "08827501000158"
    assert linhas[0]["denominacao"].endswith("PARTICIPAÇÕES S.A.")
    assert linhas[0]["data_entrega"] == date(2026, 11, 5) and linhas[0]["versao"] == 1


def test_coluna_obrigatoria_sumindo_derruba_a_coleta():
    z = _zip("itr_cia_aberta_2026.csv", ["CNPJ_CIA;DT_REFER;VERSAO", "1;2026-09-30;1"])
    with pytest.raises(ValueError, match="DT_RECEB"):
        cal.ler_zip(z, "ITR")


def test_grava_so_a_primeira_entrega_e_so_quem_interessa():
    db, s = _banco()
    _empresa(db, s, "Aegea", cnpj="08827501000158")
    db.commit()
    base = {"documento": "ITR", "data_referencia": date(2026, 9, 30), "versao": 1, "link": "l", "codigo_cvm": "1"}
    linhas = [
        {**base, "cnpj": "08827501000158", "denominacao": "AEGEA", "data_entrega": date(2026, 11, 20), "versao": 2},
        {**base, "cnpj": "08827501000158", "denominacao": "AEGEA", "data_entrega": date(2026, 11, 5)},
        {**base, "cnpj": "55555555000155", "denominacao": "PADARIA DO ZE", "data_entrega": date(2026, 11, 1)},
    ]
    r = cal.gravar(db, linhas)
    assert (r["de_interesse"], r["novas"]) == (1, 1)
    e = db.query(EntregaResultadoCVM).one()
    assert e.data_entrega == date(2026, 11, 5)
    # Rodar de novo com uma entrega mais tarde não piora o dado
    assert cal.gravar(db, linhas[:1])["atualizadas"] == 0


# ---- rotas ------------------------------------------------------------------
@pytest.fixture()
def clientes():
    from fastapi.testclient import TestClient

    import app.app as A
    from app import auth
    from app.db import Base as B, SessionLocal, engine
    from app.models import User

    B.metadata.create_all(engine)
    tokens = {}
    with SessionLocal() as db:
        db.query(ResultadoConfirmado).delete()
        for papel in ("admin", "user"):
            email = f"cal-{papel}@x.com"
            u = db.query(User).filter_by(email=email).first()
            if u is None:
                u = User(email=email, name=papel, role=papel, active=True,
                         password_hash=auth.hash_password("x" * 10), email_confirmed=True)
                db.add(u)
                db.commit()
            tokens[papel] = auth.create_session(db, u, ip="1", user_agent="t").token
        s = db.query(Sector).filter_by(name="Cal Teste").first() or Sector(name="Cal Teste")
        db.add(s)
        db.flush()
        c = Company(name="Empresa Cal", sector_id=s.id, active=True)
        db.add(c)
        db.commit()
        cid = c.id
    out = {}
    for papel, tok in tokens.items():
        cl = TestClient(A.app, raise_server_exceptions=False)
        cl.cookies.set("session_token", tok)
        out[papel] = cl
    yield out, cid
    with SessionLocal() as db:
        db.query(ResultadoConfirmado).delete()
        db.query(EmpresaTipoCobertura).delete()
        db.query(Company).filter_by(name="Empresa Cal").delete()
        db.commit()


def test_rotas_do_calendario(clientes):
    cl, cid = clientes
    r = cl["user"].get("/api/inicio/resultados")
    assert r.status_code == 200 and "temporada" in r.json()
    assert cl["user"].post("/api/inicio/resultados/confirmar", json={"company_id": cid, "data": "2026-11-05"}).status_code == 403
    assert cl["admin"].post("/api/inicio/resultados/confirmar", json={"company_id": cid, "data": "05/11"}).status_code == 400
    assert cl["admin"].post("/api/inicio/resultados/confirmar", json={"company_id": 999999, "data": "2026-11-05"}).status_code == 404
    assert cl["admin"].post("/api/inicio/resultados/confirmar", json={"company_id": cid, "data": "2026-11-05"}).status_code == 200
    assert cl["admin"].post("/api/inicio/resultados/confirmar", json={"company_id": cid, "tipo": "Marte"}).status_code == 400
    assert cl["admin"].post("/api/inicio/resultados/confirmar",
                            json={"company_id": cid, "data": "2026-11-05", "tipo": "Offshore"}).status_code == 200
    lista = {"texto": "Empresa Cal 05/11/2026 After Offshore #4D93D9 Offshore"}
    assert cl["user"].post("/api/inicio/resultados/importar", json=lista).status_code == 403
    assert cl["admin"].post("/api/inicio/resultados/importar", json={"texto": "nada"}).status_code == 400
    rel = cl["admin"].post("/api/inicio/resultados/importar", json=lista).json()
    assert rel["com_data"] == 1
    assert "importacao" in cl["admin"].get("/api/inicio/resultados").json()
    assert "importacao" not in cl["user"].get("/api/inicio/resultados").json()


def test_aba_calendario(clientes):
    cl, _ = clientes
    html = cl["admin"].get("/calendario").text
    assert 'id="cal-card"' in html and 'data-admin="sim"' in html and "calendario.js" in html
    assert 'data-admin=""' in cl["user"].get("/calendario").text
    assert 'href="/calendario"' in html                       # link no menu
    assert 'id="hero-brief"' in cl["admin"].get("/").text      # contador continua na abertura
