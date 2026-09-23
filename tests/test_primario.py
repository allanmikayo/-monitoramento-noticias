"""Aba Mercado Primário: coleta da CVM, trilha de status e consultas
(23/09/2026). Ver app/primario/coleta.py e app/primario/queries.py."""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db import Base, SessionLocal, engine
from app.models import OfertaCVM, OfertaCVMStatus, User
from app.primario import coleta, queries

AMOSTRA = Path(__file__).parent / "fixtures" / "ofertas_cvm_amostra.csv"


def _csv() -> str:
    return AMOSTRA.read_bytes().decode("latin-1")


def _zip(texto: str | None = None, nome: str = coleta.ARQUIVO_INTERNO) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(nome, (texto if texto is not None else _csv()).encode("latin-1"))
        z.writestr("oferta_distribuicao.csv", b"arquivo antigo, nao usamos")
    return buf.getvalue()


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    s.query(OfertaCVMStatus).delete()
    s.query(OfertaCVM).delete()
    s.commit()
    yield s
    s.query(OfertaCVMStatus).delete()
    s.query(OfertaCVM).delete()
    s.commit()
    s.close()


# ------------------------------- leitura -----------------------------------

def test_le_so_instrumentos_de_divida():
    ofertas = coleta.parse(_csv())
    assert {o.dados["instrumento"] for o in ofertas} == {"DEB", "CRI", "CRA"}
    # O FIDC da amostra não entra: a aba é de dívida corporativa (pedido do
    # Allan) e a CVM publica todo tipo de oferta no mesmo arquivo.
    assert all("FIDC" not in o.dados["nome_emissor"] for o in ofertas)


def test_converte_os_campos_certos():
    o = {x.chave: x.dados for x in coleta.parse(_csv())}["1001"]
    assert o["valor_total"] == 1_000_000_000.0          # decimal com ponto, sem separador de milhar
    assert o["data_registro"] == date(2026, 1, 12)
    assert o["instrumento"] == "DEB" and o["incentivado"] == "S"
    assert o["lider"] == "Itaú BBA"                      # razão social -> nome curto
    assert o["nome_lider"].startswith("ITAU BBA")        # o cru continua guardado
    assert o["invest_fundos_n"] == 42


def test_extrai_o_arquivo_certo_do_zip():
    assert "Numero_Requerimento" in coleta.extrair_csv(_zip())
    with pytest.raises(ValueError, match="não veio no zip"):
        coleta.extrair_csv(_zip(nome="outro.csv"))


def test_csv_sem_as_colunas_esperadas_falha_alto():
    with pytest.raises(ValueError, match="colunas ausentes"):
        coleta.parse("Coluna_A;Coluna_B\r\n1;2\r\n")


def test_limpar_lider_agrupa_o_mesmo_banco():
    assert coleta.limpar_lider("BANCO ITAU BBA S.A.") == coleta.limpar_lider(
        "ITAU BBA ASSESSORIA FINANCEIRA S.A") == "Itaú BBA"
    assert coleta.limpar_lider("EMPRESA QUALQUER DE FOMENTO LTDA") == "Empresa Qualquer De Fomento"
    assert coleta.limpar_lider("") is None


# --------------------------- gravação e trilha ------------------------------

def test_carga_inicial_nao_vira_novidade(db):
    """Três anos de histórico entrando de uma vez não são 'o que aconteceu
    esta semana' -- a trilha passa a valer da segunda rodada em diante."""
    resumo = coleta.coletar(db, conteudo_zip=_zip())
    assert resumo["lidas"] == 5 and resumo["novas"] == 5 and resumo["carga_inicial"] is True
    assert db.query(OfertaCVM).count() == 5
    assert db.query(OfertaCVMStatus).count() == 0


def test_oferta_nova_depois_da_carga_inicial_vira_novidade(db):
    linhas = _csv().splitlines(keepends=True)
    # -2 porque a última linha da amostra é o FIDC, que nem entra na base.
    coleta.coletar(db, conteudo_zip=_zip("".join(linhas[:-2])))
    resumo = coleta.coletar(db, conteudo_zip=_zip())
    assert resumo["novas"] == 1 and resumo["carga_inicial"] is False
    nova = db.query(OfertaCVMStatus).one()
    assert nova.status_anterior is None and nova.numero_requerimento == "1005"


def test_rodar_de_novo_nao_duplica(db):
    coleta.coletar(db, conteudo_zip=_zip())
    resumo = coleta.coletar(db, conteudo_zip=_zip())
    assert resumo["novas"] == 0 and resumo["mudancas"] == 0
    assert db.query(OfertaCVM).count() == 5
    assert db.query(OfertaCVMStatus).count() == 0


def test_mudanca_de_status_vira_linha_na_trilha(db):
    """O ARQUIVO da CVM é sobrescrito: sem esta trilha, a passagem de
    'Registro Concedido' para 'Oferta Encerrada' desapareceria."""
    coleta.coletar(db, conteudo_zip=_zip())
    novo = _csv().replace(
        "1003;Automático;;2026-02-20;2026-02-25;;Registro Concedido",
        "1003;Automático;;2026-02-20;2026-02-25;2026-09-20;Oferta Encerrada")
    resumo = coleta.coletar(db, conteudo_zip=_zip(novo))
    assert resumo["mudancas"] == 1
    t = db.query(OfertaCVMStatus).filter_by(numero_requerimento="1003").order_by(
        OfertaCVMStatus.id.desc()).first()
    assert t.status_anterior == "Registro Concedido" and t.status_novo == "Oferta Encerrada"
    assert db.get(OfertaCVM, "1003").data_encerramento == date(2026, 9, 20)


def test_arquivo_truncado_nao_apaga_o_que_ja_temos(db):
    """Se a CVM publicar um arquivo pela metade, a rodada aborta: um número
    que encolhe sozinho seria a falha mais perigosa aqui -- silenciosa e
    idêntica a 'o mercado parou'."""
    coleta.coletar(db, conteudo_zip=_zip())
    linhas = _csv().splitlines(keepends=True)
    with pytest.raises(ValueError, match="abortando"):
        coleta.coletar(db, conteudo_zip=_zip("".join(linhas[:2])))
    assert db.query(OfertaCVM).count() == 5


def test_backup_guarda_o_zip_e_limpa_os_antigos(tmp_path):
    for i in range(coleta.BACKUPS_MANTIDOS + 3):
        (tmp_path / f"oferta_distribuicao_2026-01-{i + 1:02d}.zip").write_bytes(b"velho")
    coleta.salvar_backup(_zip(), tmp_path)
    guardados = sorted(tmp_path.glob("oferta_distribuicao_*.zip"))
    assert len(guardados) == coleta.BACKUPS_MANTIDOS
    assert guardados[-1].name.endswith(f"{date.today().isoformat()}.zip")


# ------------------------------- consultas ---------------------------------

def test_painel_resume_a_janela(db):
    coleta.coletar(db, conteudo_zip=_zip())
    p = queries.painel(db, janela="tudo")
    # Caducada fora: 1,0 + 0,5 (DEB) + 0,3 (CRI) + 0,2 (CRA) = 2,0 bi
    assert p["kpis"]["volume"] == 2.0 and p["kpis"]["ofertas"] == 4
    assert p["kpis"]["ticket_mediano"] == 400.0
    assert p["kpis"]["garantia_firme_pct"] == 50.0
    # "Dados até" é o registro mais recente que a CVM publicou, mesmo que
    # essa oferta tenha caducado depois -- é data de publicação, não de vida.
    assert p["disponivel_ate"] == "2026-03-12"
    assert [l["lider"] for l in p["lideres"]][0] == "Itaú BBA"
    assert p["maiores"][0]["emissor"].startswith("CIA SANEAMENTO")


def test_serie_mensal_nao_pula_mes_vazio(db):
    coleta.coletar(db, conteudo_zip=_zip())
    serie = queries.painel(db, janela="tudo")["serie"]
    assert [s["mes"] for s in serie] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert serie[0]["DEB"] == 1.0 and serie[1]["CRI"] == 0.3 and serie[2]["CRA"] == 0.2


def test_incentivadas_separa_o_nao_informado(db):
    coleta.coletar(db, conteudo_zip=_zip())
    tri = queries.painel(db, janela="tudo")["incentivadas"]
    t1 = [t for t in tri if t["trimestre"] == "2026-T1"][0]
    assert t1["incentivada"] == 1.0 and t1["nao"] == 0.5 and t1["sem_info"] == 0.0


def test_pipeline_so_traz_oferta_viva_e_marca_a_parada(db):
    coleta.coletar(db, conteudo_zip=_zip())
    fila = queries.pipeline(db, hoje=date(2026, 9, 23))
    assert [o["emissor"] for o in fila] == ["OPEA SECURITIZADORA S.A."]   # a caducada fica de fora
    assert fila[0]["parada"] is True and fila[0]["dias"] == 210


def test_filtro_de_instrumento_e_de_incentivada(db):
    coleta.coletar(db, conteudo_zip=_zip())
    so_cri = queries.painel(db, janela="tudo", instrumentos=["CRI"])
    assert so_cri["kpis"]["ofertas"] == 1 and so_cri["kpis"]["volume"] == 0.3
    so_inc = queries.painel(db, janela="tudo", incentivada="S")
    assert so_inc["kpis"]["ofertas"] == 1 and so_inc["maiores"][0]["incentivada"] == "S"


def test_estreantes_sao_a_primeira_oferta_do_emissor(db):
    coleta.coletar(db, conteudo_zip=_zip())
    nomes = {e["emissor"] for e in queries.painel(db, janela="tudo")["estreantes"]}
    assert "CIA SANEAMENTO ALFA S.A." in nomes and len(nomes) == 4


def test_novidades_olha_a_trilha_e_nao_o_arquivo(db):
    """O arquivo da CVM é sobrescrito e não sabe o que mudou; a trilha sabe.
    E o que ficou para trás no tempo sai da janela de 7 dias."""
    linhas = _csv().splitlines(keepends=True)
    coleta.coletar(db, conteudo_zip=_zip("".join(linhas[:-2])))
    coleta.coletar(db, conteudo_zip=_zip())
    assert len(queries.novidades(db, dias=7)) == 1
    antiga = db.query(OfertaCVMStatus).one()
    antiga.visto_em = datetime.now(timezone.utc) - timedelta(days=30)
    db.commit()
    assert queries.novidades(db, dias=7) == []


# --------------------------------- rotas -----------------------------------

@pytest.fixture()
def cliente(db):
    from fastapi.testclient import TestClient

    import app.app as A
    from app import auth

    u = db.query(User).first()
    if u is None:
        u = User(name="Teste", email="primario@teste.com", password_hash="", role="admin",
                 active=True, email_confirmed=True)
        db.add(u)
        db.commit()
    token = auth.create_session(db, u, ip="1", user_agent="teste").token
    c = TestClient(A.app, raise_server_exceptions=False)
    c.cookies.set("session_token", token)
    return c


def test_pagina_e_api_respondem(cliente, db):
    coleta.coletar(db, conteudo_zip=_zip())
    pagina = cliente.get("/primario")
    assert pagina.status_code == 200
    assert "/static/primario.js?v=" in pagina.text and "Mercado Primário" in pagina.text
    dados = cliente.get("/api/primario/dados?janela=tudo").json()
    assert dados["kpis"]["ofertas"] == 4 and dados["janela"] == "tudo"


def test_api_recusa_filtro_invalido(cliente):
    assert cliente.get("/api/primario/dados?janela=decada").status_code == 400
    assert cliente.get("/api/primario/dados?instrumento=XPTO").status_code == 400
    assert cliente.get("/api/primario/dados?incentivada=talvez").status_code == 400


def test_csv_sai_pronto_para_o_excel(cliente, db):
    coleta.coletar(db, conteudo_zip=_zip())
    r = cliente.get("/api/primario/ofertas.csv?janela=tudo")
    assert r.status_code == 200 and r.text.startswith("﻿")
    assert "CIA SANEAMENTO ALFA S.A.;11.111.111/0001-11;1000000000,00;Itaú BBA" in r.text


def test_aba_exige_login():
    from fastapi.testclient import TestClient

    import app.app as A

    r = TestClient(A.app).get("/primario", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
