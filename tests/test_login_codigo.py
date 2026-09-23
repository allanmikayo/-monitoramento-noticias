"""Login por código no e-mail (22/09/2026) -- ver app/login_codigo.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import auth, login_codigo as L
from app.db import Base, SessionLocal, engine
from app.models import LoginCode, User


@pytest.fixture()
def db():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    s.query(LoginCode).delete()
    s.query(User).filter(User.email.like("%@teste-codigo.com")).delete(synchronize_session=False)
    auth.set_setting(s, L.EXIGE_APROVACAO_KEY, "0")
    s.commit()
    yield s
    s.query(LoginCode).delete()
    s.query(User).filter(User.email.like("%@teste-codigo.com")).delete(synchronize_session=False)
    auth.set_setting(s, L.EXIGE_APROVACAO_KEY, "0")
    s.commit()
    s.close()


def test_primeiro_acesso_cria_usuario_sem_senha(db):
    cod = L.pedir_codigo(db, " Ana@Teste-Codigo.com ", "1.1.1.1")
    assert len(cod) == 6 and cod.isdigit()
    u = L.confirmar_codigo(db, "ana@teste-codigo.com", cod, "Ana")
    assert u.name == "Ana" and u.active and u.password_hash == ""
    # sem senha, a entrada por senha nunca aceita
    with pytest.raises(auth.AuthError):
        auth.authenticate(db, "ana@teste-codigo.com", "")


def test_codigo_nao_fica_guardado_em_texto(db):
    cod = L.pedir_codigo(db, "b@teste-codigo.com", None)
    reg = db.query(LoginCode).filter_by(email="b@teste-codigo.com").one()
    assert cod not in reg.code_hash


def test_codigo_vale_uma_vez(db):
    cod = L.pedir_codigo(db, "c@teste-codigo.com", None)
    L.confirmar_codigo(db, "c@teste-codigo.com", cod)
    with pytest.raises(L.CodigoErro):
        L.confirmar_codigo(db, "c@teste-codigo.com", cod)


def test_tentativas_esgotam(db):
    cod = L.pedir_codigo(db, "d@teste-codigo.com", None)
    errado = "000000" if cod != "000000" else "111111"
    for _ in range(L.MAX_TENTATIVAS):
        with pytest.raises(L.CodigoErro):
            L.confirmar_codigo(db, "d@teste-codigo.com", errado)
    with pytest.raises(L.CodigoErro, match="esgotadas"):
        L.confirmar_codigo(db, "d@teste-codigo.com", cod)  # nem o certo passa mais


def test_codigo_expira(db):
    cod = L.pedir_codigo(db, "e@teste-codigo.com", None)
    reg = db.query(LoginCode).filter_by(email="e@teste-codigo.com").one()
    reg.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(L.CodigoErro, match="expirado"):
        L.confirmar_codigo(db, "e@teste-codigo.com", cod)


def test_pedir_de_novo_invalida_o_anterior(db):
    antigo = L.pedir_codigo(db, "f@teste-codigo.com", None)
    novo = L.pedir_codigo(db, "f@teste-codigo.com", None)
    if antigo != novo:
        with pytest.raises(L.CodigoErro):
            L.confirmar_codigo(db, "f@teste-codigo.com", antigo)
    assert L.confirmar_codigo(db, "f@teste-codigo.com", novo)


def test_limite_por_email(db):
    for _ in range(L.MAX_POR_EMAIL_HORA):
        L.pedir_codigo(db, "g@teste-codigo.com", None)
    with pytest.raises(L.CodigoErro, match="Muitos"):
        L.pedir_codigo(db, "g@teste-codigo.com", None)


def test_bloqueado_nao_recebe_codigo(db):
    db.add(User(name="X", email="h@teste-codigo.com", password_hash="", active=False, email_confirmed=True))
    db.commit()
    assert L.pedir_codigo(db, "h@teste-codigo.com", None) is None


def test_com_aprovacao_email_novo_fica_pendente(db):
    auth.set_setting(db, L.EXIGE_APROVACAO_KEY, "1")
    db.commit()
    cod = L.pedir_codigo(db, "i@teste-codigo.com", None)
    with pytest.raises(auth.AuthError, match="aprovar"):
        L.confirmar_codigo(db, "i@teste-codigo.com", cod, "I")
    u = db.query(User).filter_by(email="i@teste-codigo.com").one()
    assert not u.active and not u.email_confirmed
    # pendente pode pedir código de novo (não é bloqueio)
    assert L.pedir_codigo(db, "i@teste-codigo.com", None) is not None


def test_email_invalido(db):
    with pytest.raises(L.CodigoErro):
        L.pedir_codigo(db, "sem-arroba", None)


def test_senha_so_para_admin(db):
    db.add(User(name="U", email="j@teste-codigo.com", password_hash=auth.hash_password("segredo1"),
                role="user", active=True, email_confirmed=True))
    db.add(User(name="A", email="k@teste-codigo.com", password_hash=auth.hash_password("segredo1"),
                role="admin", active=True, email_confirmed=True))
    db.commit()
    with pytest.raises(auth.AuthError):
        auth.authenticate_admin(db, "j@teste-codigo.com", "segredo1")
    assert auth.authenticate_admin(db, "k@teste-codigo.com", "segredo1").role == "admin"


# --------------------------- fluxo pelas rotas ----------------------------

def test_fluxo_completo_pela_tela(db, monkeypatch):
    from fastapi.testclient import TestClient

    import app.app as A

    enviados = {}
    monkeypatch.setattr(A.email_utils, "send_login_code",
                        lambda email, codigo: enviados.update({email: codigo}))
    c = TestClient(A.app)
    r = c.post("/login/codigo", data={"email": "l@teste-codigo.com"}, follow_redirects=False)
    assert r.status_code == 303 and "etapa=codigo" in r.headers["location"]
    assert "l@teste-codigo.com" in enviados

    pagina = c.get(r.headers["location"]).text
    assert 'name="nome"' in pagina          # primeiro acesso pede o nome
    assert "Registramos quais páginas" in pagina  # aviso de registro de uso

    r = c.post("/login/confirmar", data={"codigo": enviados["l@teste-codigo.com"], "nome": "Luiza"},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "session_token" in r.cookies
    assert c.get("/spreads", follow_redirects=False).status_code == 200


def test_falha_de_envio_aparece_na_tela(db, monkeypatch):
    from fastapi.testclient import TestClient

    import app.app as A

    def falha(email, codigo):
        raise A.email_utils.EnvioIndisponivel("smtp caiu")
    monkeypatch.setattr(A.email_utils, "send_login_code", falha)
    monkeypatch.setattr(A.email_utils, "smtp_configurado", lambda: True)
    c = TestClient(A.app)
    r = c.post("/login/codigo", data={"email": "m@teste-codigo.com"}, follow_redirects=False)
    assert "etapa=email" in r.headers["location"] and "erro=" in r.headers["location"]


def test_cadastro_antigo_redireciona():
    from fastapi.testclient import TestClient

    import app.app as A

    r = TestClient(A.app).get("/cadastro", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
