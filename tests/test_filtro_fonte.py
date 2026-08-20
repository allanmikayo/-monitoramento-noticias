"""Filtro de FONTE nas notícias (pedido do Allan, 12/08/2026).

Multi-select no mesmo padrão de Setor e Empresa. Testa a camada de
consulta e a rota — a primeira versão da aba existia só no protótipo e
nunca chegou ao app, então o teste de rota é o que garante que chegou.

    python -m pytest tests/test_filtro_fonte.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import store
from app.models import Article


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        agora = datetime.now(timezone.utc)
        for i, fonte in enumerate(["Valor Econômico", "O Globo", "Money Times", "O Globo"]):
            s.add(Article(
                url=f"http://x/{i}", domain="x.com", source_name=fonte,
                title=f"n{i}", published_at=agora - timedelta(hours=1),
                found_at=agora, is_covered=True,
            ))
        s.commit()
        yield s


def _fontes(artigos):
    return sorted({a.source_name for a in artigos})


def test_sem_filtro_traz_todas(db):
    a = store.list_articles(db, window_hours=24, coverage=["todos"])
    assert len(a) == 4
    assert len(_fontes(a)) == 3


def test_uma_fonte(db):
    a = store.list_articles(db, window_hours=24, coverage=["todos"],
                            source_names=["O Globo"])
    assert len(a) == 2
    assert _fontes(a) == ["O Globo"]


def test_multiplas_fontes(db):
    """É o ponto do pedido: seleção múltipla, como no filtro de setor."""
    a = store.list_articles(db, window_hours=24, coverage=["todos"],
                            source_names=["O Globo", "Money Times"])
    assert len(a) == 3
    assert _fontes(a) == ["Money Times", "O Globo"]


def test_lista_vazia_nao_filtra(db):
    """Nenhuma marcada = todas, não nenhuma. Se `[]` filtrasse, a tela
    abriria vazia — que é como o usuário chega nela."""
    a = store.list_articles(db, window_hours=24, coverage=["todos"], source_names=[])
    assert len(a) == 4


def test_fonte_inexistente_devolve_vazio(db):
    a = store.list_articles(db, window_hours=24, coverage=["todos"],
                            source_names=["Não Existe"])
    assert a == []


def test_filtra_por_nome_e_nao_por_dominio(db):
    """Usa `source_name` ("Valor Econômico"), não `domain`
    ("valor.globo.com") — é o que aparece no card e o que o usuário
    reconhece na lista."""
    a = store.list_articles(db, window_hours=24, coverage=["todos"],
                            source_names=["Valor Econômico"])
    assert len(a) == 1
    a2 = store.list_articles(db, window_hours=24, coverage=["todos"],
                             source_names=["x.com"])
    assert a2 == []


# ---------------------------------------------------------------------------
# Rota e template
# ---------------------------------------------------------------------------

def _cliente_logado():
    """TestClient autenticado.

    O dashboard de notícias voltou a exigir login em 13/08/2026 (só o
    Repositório de Relatórios é público). Sem cookie, `require_user`
    redireciona para /login e o TestClient SEGUE o redirect, devolvendo a
    tela de login com status 200 -- então `assert status_code == 200`
    passa, e só o assert seguinte (procurando um elemento do template)
    denuncia o problema. Corrigido em 20/08/2026.
    """
    from fastapi.testclient import TestClient
    from app import auth
    from app.db import Base, SessionLocal, engine
    from app.models import User
    import app.app as A

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
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


def test_botao_de_fonte_aparece_na_tela():
    c = _cliente_logado()
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 200, r.status_code
    assert "ms-source-btn" in r.text, "o botão Fonte não chegou ao template"
    assert 'data-ms="source"' in r.text or "Fonte: Todas" in r.text


def test_api_aceita_source_name_repetido():
    """`?source_name=A&source_name=B` — mesma mecânica de `sector_id`."""
    c = _cliente_logado()
    r = c.get("/api/articles?window=24h&coverage=todos&source_name=A&source_name=B",
              follow_redirects=False)
    assert r.status_code == 200, r.status_code
    assert "articles" in r.json()
