"""Testes da aba Banco de Dados — sobretudo das barreiras do SQL livre.

Executar SQL digitado numa tela é perigoso por natureza; estes testes são
a parte que garante que as barreiras não afrouxem sem alguém perceber.

    python -m pytest tests/test_banco.py -v
"""
from __future__ import annotations

import pytest

from app.spreads.banco_routes import (
    LIMITE_MAXIMO,
    TABELAS,
    validar_consulta,
)


# ---------------------------------------------------------------------------
# O que passa
# ---------------------------------------------------------------------------

def test_select_simples_passa():
    assert "SELECT" in validar_consulta("SELECT * FROM debentures")


def test_with_passa():
    sql = validar_consulta("WITH x AS (SELECT 1 AS a) SELECT * FROM x")
    assert sql.startswith("WITH")


def test_ponto_e_virgula_no_fim_e_aceito():
    """Terminar com ';' é hábito de quem usa cliente SQL — não é ataque."""
    assert validar_consulta("SELECT 1;")


def test_limit_injetado_quando_ausente():
    assert "LIMIT 500" in validar_consulta("SELECT * FROM debentures", 500)


def test_limit_existente_e_respeitado():
    sql = validar_consulta("SELECT * FROM debentures LIMIT 7")
    assert sql.count("LIMIT") == 1 and "LIMIT 7" in sql


def test_limite_e_teto():
    sql = validar_consulta("SELECT * FROM debentures", 999_999)
    assert f"LIMIT {LIMITE_MAXIMO}" in sql


# ---------------------------------------------------------------------------
# O que não passa
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DELETE FROM debentures",
    "UPDATE debentures SET nome='x'",
    "INSERT INTO debentures VALUES (1)",
    "DROP TABLE debentures",
    "TRUNCATE debentures",
    "ALTER TABLE debentures ADD COLUMN x INT",
    "CREATE TABLE t (a INT)",
    "PRAGMA table_info(debentures)",
    "VACUUM",
])
def test_escrita_e_ddl_recusados(sql):
    with pytest.raises(ValueError):
        validar_consulta(sql)


def test_instrucao_encadeada_recusada():
    """O ataque clássico: anexar um DROP a um SELECT legítimo."""
    with pytest.raises(ValueError, match="uma instrução"):
        validar_consulta("SELECT 1; DROP TABLE debentures")


def test_escrita_escondida_dentro_do_select_recusada():
    """Não basta olhar o começo — CTE com DELETE é escrita disfarçada de
    consulta (`WITH x AS (DELETE ... RETURNING *) SELECT * FROM x` é SQL
    válido no Postgres)."""
    with pytest.raises(ValueError):
        validar_consulta("WITH x AS (DELETE FROM debentures RETURNING *) SELECT * FROM x")


def test_consulta_vazia_recusada():
    for v in ("", "   ", None):
        with pytest.raises(ValueError):
            validar_consulta(v)


def test_palavra_proibida_so_casa_por_fronteira():
    """`\\bDELETE\\b` não pode barrar uma coluna chamada `deleted_at` —
    senão a tela recusa consulta legítima e o admin perde a confiança
    nela."""
    assert validar_consulta("SELECT deleted_at, created_at FROM articles")
    assert validar_consulta("SELECT * FROM articles WHERE updated_by IS NULL")


# ---------------------------------------------------------------------------
# Inventário
# ---------------------------------------------------------------------------

def test_tabelas_de_serie_tem_coluna_de_data():
    """Sem a coluna, o filtro de período da tela não funciona."""
    for t in ("debenture_spreads", "securitizado_spreads",
              "negocios_b3", "negocios_b3_diario"):
        assert TABELAS[t], f"{t} precisa de coluna de data"


def test_cadastro_nao_tem_coluna_de_data():
    for t in ("debentures", "issuers", "companies"):
        assert TABELAS[t] is None


def test_inventario_cobre_as_tabelas_que_crescem():
    """Se uma tabela nova de série ficar de fora, ela some da tela de
    extração — e ninguém percebe até precisar do dado."""
    from app.spreads.capacidade import CRESCIMENTO
    for t in CRESCIMENTO:
        assert t in TABELAS, f"{t} cresce mas não aparece na aba Banco de Dados"


# ---------------------------------------------------------------------------
# CSV para Excel BR
# ---------------------------------------------------------------------------

def test_csv_usa_virgula_decimal():
    """BUG JÁ PAGO (27/07/2026): CSV com '.' decimal aberto no Excel pt-BR
    vira "8.567.994.822.9". Este é outro caminho de saída e o erro
    reapareceria igual."""
    from app.spreads.banco_routes import _csv_br
    assert _csv_br(8567.9948229) == "8567,994823"
    assert _csv_br(1.5) == "1,5"
    assert _csv_br(2.0) == "2"


def test_csv_preserva_texto_e_data():
    from datetime import date
    from app.spreads.banco_routes import _csv_br
    assert _csv_br("AEGP14") == "AEGP14"
    assert _csv_br(date(2026, 8, 4)) == "2026-08-04"
    assert _csv_br(None) is None


# ---------------------------------------------------------------------------
# Rotas registradas e protegidas — de ponta a ponta
#
# Estes testes existem porque o módulo passou a existir sem estar LIGADO ao
# app: as rotas não estavam registradas no app.py e o localhost não mudava.
# Teste de unidade não pegaria isso.
# ---------------------------------------------------------------------------

@pytest.fixture()
def cliente():
    """`DATABASE_URL` já vem do conftest (arquivo temporário) — ver lá o
    porquê de não ser banco em memória."""
    from fastapi.testclient import TestClient
    import app.app as A
    from app.db import Base, SessionLocal, engine
    Base.metadata.create_all(engine)
    return TestClient(A.app, raise_server_exceptions=False), SessionLocal


def _login_admin(SessionLocal):
    from app import auth
    from app.models import User
    with SessionLocal() as db:
        u = db.query(User).first()
        if u is None:
            u = User(email="a@a.com", name="Admin", role="admin", active=True,
                     password_hash=auth.hash_password("x" * 10), email_confirmed=True)
            db.add(u)
            db.commit()
        u.role = "admin"
        db.commit()
        s = auth.create_session(db, u, ip="1", user_agent="teste")
        db.commit()
        return s.token


@pytest.mark.parametrize("metodo,url", [
    ("get", "/banco"),
    ("get", "/api/banco/tabela?tabela=debentures"),
    ("get", "/api/banco/export?tabela=debentures"),
    ("post", "/api/banco/consulta"),
])
def test_sem_login_nao_entrega_nada(cliente, metodo, url):
    """Todas as rotas atrás de login — inclusive as de API, que são as que
    entregariam dado se escapassem."""
    c, _ = cliente
    r = (c.post(url, json={"sql": "SELECT 1"}, follow_redirects=False)
         if metodo == "post" else c.get(url, follow_redirects=False))
    assert r.status_code in (302, 303, 401, 403)
    assert "colunas" not in r.text


def test_pagina_carrega_para_admin(cliente):
    c, SessionLocal = cliente
    c.cookies.set("session_token", _login_admin(SessionLocal))
    r = c.get("/banco", follow_redirects=False)
    assert r.status_code == 200
    # "Ocupação" saiu daqui em 12/08/2026: o Allan apontou que ocupação de
    # banco é manutenção, não análise, e não merecia três cards no topo da
    # tela. Virou a linha discreta de "MB usados" — que continua sendo
    # testada, só que pelo texto novo.
    for termo in ("Tabelas armazenadas", "Consulta SQL", "MB usados", "banco.js"):
        assert termo in r.text, f"faltou '{termo}' na página"


def test_sql_de_escrita_recusado_pela_rota(cliente):
    """A validação é testada em unidade acima; aqui é a rota inteira —
    inclui o caso de CTE, que passa por quem só olha o começo da string."""
    c, SessionLocal = cliente
    c.cookies.set("session_token", _login_admin(SessionLocal))
    for sql in ("DELETE FROM debentures",
                "SELECT 1; DROP TABLE debentures",
                "WITH x AS (DELETE FROM debentures RETURNING *) SELECT * FROM x"):
        r = c.post("/api/banco/consulta", json={"sql": sql}, follow_redirects=False)
        assert r.status_code == 400, f"deveria recusar: {sql}"


def test_tabela_desconhecida_recusada(cliente):
    c, SessionLocal = cliente
    c.cookies.set("session_token", _login_admin(SessionLocal))
    r = c.get("/api/banco/tabela?tabela=users", follow_redirects=False)
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Custo do inventário — a página dava 504 em produção (20/08/2026)
# ---------------------------------------------------------------------------

def test_inventario_nao_conta_views(cliente):
    """`COUNT(*)` numa VIEW obriga o Postgres a materializar o join inteiro.
    `v_spread_rating` tem ~98 mil linhas; era parte do que estourava os 60s
    de limite da função."""
    from sqlalchemy import event

    from app.db import SessionLocal, engine
    from app.spreads import banco_routes as br

    sqls = []
    ouvinte = lambda conn, cur, sql, *a: sqls.append(sql)  # noqa: E731
    event.listen(engine, "before_cursor_execute", ouvinte)
    try:
        with SessionLocal() as db:
            # `diag` pronto: isola o custo de `inventario`. O `diagnostico`
            # tem um COUNT(*) por tabela no ramo do SQLite (local, base
            # pequena) -- em Postgres ele lê `pg_class` e não conta nada.
            br.inventario(db, diag={"tabelas": []})
    finally:
        event.remove(engine, "before_cursor_execute", ouvinte)

    for view in br.VIEWS:
        agregados = [s for s in sqls
                     if view in s and ("COUNT(" in s.upper() or "MIN(" in s.upper())]
        assert not agregados, f"agregado rodado sobre a view {view}: {agregados}"


def test_inventario_usa_catalogo_no_postgres(monkeypatch, cliente):
    """No Postgres a contagem vem de `pg_class.reltuples`, não de COUNT(*).

    Aproximado é suficiente para um inventário e é instantâneo: lê catálogo,
    não dado. O teste finge um Postgres e confere que nenhum COUNT(*) sobra.
    """
    from sqlalchemy import event

    from app.db import SessionLocal, engine
    from app.spreads import banco_routes as br

    monkeypatch.setattr(
        br, "_contagens_estimadas",
        lambda db: {t: 123 for t in br.TABELAS if t not in br.VIEWS},
    )

    sqls = []
    ouvinte = lambda conn, cur, sql, *a: sqls.append(sql)  # noqa: E731
    event.listen(engine, "before_cursor_execute", ouvinte)
    try:
        with SessionLocal() as db:
            linhas = br.inventario(db, diag={"tabelas": []})
    finally:
        event.remove(engine, "before_cursor_execute", ouvinte)

    assert not [s for s in sqls if "COUNT(*)" in s.upper()], "sobrou COUNT(*)"
    reais = [l for l in linhas if l["tabela"] not in br.VIEWS]
    assert reais and all(l["linhas_aproximadas"] for l in reais)
    assert all(l["linhas"] is None for l in linhas if l["tabela"] in br.VIEWS)


def test_diagnostico_calculado_uma_vez_por_pagina(cliente, monkeypatch):
    """Era chamado na rota E dentro de `inventario` — a consulta de catálogo
    mais cara da tela, feita em dobro."""
    from app.spreads import banco_routes as br

    chamadas = {"n": 0}
    original = br.diagnostico

    def contando(*a, **k):
        chamadas["n"] += 1
        return original(*a, **k)

    monkeypatch.setattr(br, "diagnostico", contando)
    c, SessionLocal = cliente
    c.cookies.set("session_token", _login_admin(SessionLocal))
    r = c.get("/banco", follow_redirects=False)
    assert r.status_code == 200
    assert chamadas["n"] == 1, f"diagnostico chamado {chamadas['n']}x"


def test_pagina_sobrevive_a_inventario_quebrado(cliente, monkeypatch):
    """A função da tela é consultar e extrair; o inventário é acessório.

    Em 20/08/2026 a página devolveu Internal Server Error e não dava pista
    nenhuma do motivo. Agora o erro aparece na própria tela e o resto
    continua utilizável.
    """
    from app.spreads import banco_routes as br

    def explode(*a, **k):
        raise RuntimeError("boom no catalogo")

    monkeypatch.setattr(br, "inventario", explode)
    c, SessionLocal = cliente
    c.cookies.set("session_token", _login_admin(SessionLocal))
    r = c.get("/banco", follow_redirects=False)

    assert r.status_code == 200, "a página não pode cair junto com o inventário"
    assert "boom no catalogo" in r.text, "o erro precisa aparecer na tela"
    assert "SELECT" in r.text, "a área de consulta tem que continuar lá"


def test_banco_fora_do_ar_vira_503_explicativo(cliente, monkeypatch):
    """Falha ao CONECTAR não pode virar "Internal Server Error" mudo.

    Erro real de produção (20/08/2026): a conexão com o Supabase estourava a
    autenticação em 15s dentro do `current_user`, ou seja numa dependência
    comum a todas as rotas. O usuário via só 500, sem pista de que o
    problema era o banco.
    """
    from sqlalchemy.exc import OperationalError

    from app import auth

    def sem_banco(*a, **k):
        raise OperationalError("select pg_catalog.version()", {},
                               Exception("authentication did not complete"))

    monkeypatch.setattr(auth, "get_valid_session", sem_banco)
    c, SessionLocal = cliente
    c.cookies.set("session_token", "qualquer-coisa")
    r = c.get("/banco", follow_redirects=False)

    assert r.status_code == 503, f"esperava 503, veio {r.status_code}"
    assert "Banco de dados indisponível" in r.text
    assert "Disk IO" in r.text, "a tela precisa apontar onde investigar"
