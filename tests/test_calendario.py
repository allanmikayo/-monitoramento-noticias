"""Calendário de resultados da página inicial (28/10/2026). Ver app/calendario.py.

Guarda:
1. A temporada certa para cada data (3T em out/nov com prazo ITR de 45
   dias; 4T de jan a mar com a DFP e prazo 31/03).
2. A ordem de confiança: DIVULGADA (RAD ou CVM, a mais cedo) > CONFIRMADA
   (admin) > ESTIMADA (mesmo trimestre do ano anterior + 364 dias, limitada
   ao prazo) > SEM ESTIMATIVA.
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
    Article, Company, CompanyAlias, Debenture, EntregaResultadoCVM, ResultadoConfirmado, Sector,
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


def test_estimada_pelo_mesmo_trimestre_do_ano_anterior():
    db, s = _banco()
    _empresa(db, s, "Aegea", cnpj="08.827.501/0001-58")
    _entrega(db, "08827501000158", date(2025, 9, 30), date(2025, 11, 6))   # quinta-feira
    db.commit()
    e = _por_nome(cal.agenda(db, HOJE))["Aegea"]
    assert e["status"] == "estimada"
    assert e["data"] == "2026-11-05"                 # +364 dias: também quinta-feira
    assert date.fromisoformat(e["data"]).weekday() == date(2025, 11, 6).weekday()
    assert "06/11/2025" in e["origem"]


def test_estimativa_nunca_passa_do_prazo():
    db, s = _banco()
    _empresa(db, s, "Tardia", cnpj="11111111000111")
    _entrega(db, "11111111000111", date(2025, 9, 30), date(2025, 11, 20))  # atrasou no ano passado
    db.commit()
    assert _por_nome(cal.agenda(db, HOJE))["Tardia"]["data"] == "2026-11-14"


def test_sem_o_trimestre_do_ano_anterior_usa_o_prazo_do_ultimo_trimestre():
    db, s = _banco()
    _empresa(db, s, "Nova", cnpj="22222222000122")
    _entrega(db, "22222222000122", date(2026, 6, 30), date(2026, 8, 7))    # 38 dias
    db.commit()
    e = _por_nome(cal.agenda(db, HOJE))["Nova"]
    assert e["data"] == "2026-11-07" and "38 dias" in e["origem"]


def test_estimada_no_passado_sem_divulgacao_vira_atrasada():
    db, s = _banco()
    _empresa(db, s, "Lenta", cnpj="33333333000133")
    _entrega(db, "33333333000133", date(2025, 9, 30), date(2025, 10, 23))
    db.commit()
    e = _por_nome(cal.agenda(db, HOJE))["Lenta"]
    assert e["status"] == "atrasada" and e["data"] == "2026-10-22"


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


def test_confirmada_vence_estimativa():
    db, s = _banco()
    c = _empresa(db, s, "Copasa", cnpj="17281106000103")
    _entrega(db, "17281106000103", date(2025, 9, 30), date(2025, 11, 6))
    db.commit()
    cal.confirmar(db, c.id, date(2026, 11, 10), "após o fechamento", "allan@x", hoje=HOJE)
    e = _por_nome(cal.agenda(db, HOJE))["Copasa"]
    assert (e["status"], e["data"], e["horario"]) == ("confirmada", "2026-11-10", "após o fechamento")
    cal.confirmar(db, c.id, None, None, "allan@x", hoje=HOJE)          # apagar volta para a estimativa
    assert _por_nome(cal.agenda(db, HOJE))["Copasa"]["status"] == "estimada"


def test_empresa_fora_da_cvm_nao_entra_e_e_contada():
    db, s = _banco()
    _empresa(db, s, "SPE Sem Registro", cnpj="44444444000144")   # tem debênture, nunca entregou ITR
    _empresa(db, s, "Sem CNPJ")
    db.commit()
    r = cal.agenda(db, HOJE)
    assert r["empresas"] == [] and r["resumo"]["fora_cvm"] == 2


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


def test_resumo_conta_cada_status():
    db, s = _banco()
    _empresa(db, s, "A", cnpj="10000000000101")
    _entrega(db, "10000000000101", date(2026, 9, 30), date(2026, 10, 20))
    _empresa(db, s, "B", cnpj="10000000000102")
    _entrega(db, "10000000000102", date(2025, 9, 30), date(2025, 11, 6))
    db.commit()
    r = cal.agenda(db, HOJE)
    assert r["resumo"]["divulgada"] == 1 and r["resumo"]["estimada"] == 1 and r["resumo"]["total"] == 2
    assert r["temporada"]["rotulo"] == "3T26" and r["temporada"]["dias_para_prazo"] == 17


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


def test_pagina_inicial_tem_a_agenda(clientes):
    cl, _ = clientes
    html = cl["admin"].get("/").text
    assert 'id="cal-card"' in html and 'data-admin="sim"' in html and 'id="hero-brief"' in html
    assert 'data-admin=""' in cl["user"].get("/").text
