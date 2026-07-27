"""Autenticação local: hash de senha, cadastro com aprovação manual do
admin (ver `register_user`, mudou 27/07/2026 -- não é mais confirmação
por e-mail), sessões com expiração. Schema pensado para migrar depois
para o Supabase Auth sem mudar a interface usada pelo app.py (ver
CLAUDE.md)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import AppSetting, Session as SessionModel, User


def hash_password(password: str) -> str:
    # bcrypt so aceita ate 72 bytes -- trunca por seguranca (senhas normais
    # nunca chegam perto disso; isso so evita um crash com senhas gigantes).
    pw_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        pw_bytes = password.encode("utf-8")[:72]
        return bcrypt.checkpw(pw_bytes, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def get_setting_int(db: Session, key: str, default: int) -> int:
    row = db.get(AppSetting, key)
    if row is None or not row.value:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


class AuthError(Exception):
    pass


def register_user(db: Session, *, name: str, email: str, password: str) -> User:
    """Cadastro de auto-atendimento (`/cadastro`).

    MUDOU (27/07/2026, pedido do Allan): Notícias e Spreads viraram
    públicas -- login passou a ser opcional, só necessário pra Fontes &
    Empresas e Administração. Junto com isso, ele tirou o fluxo de
    confirmação por e-mail (nunca dependia de e-mail transacional de
    verdade rodando em produção) e trocou por APROVAÇÃO MANUAL: quem se
    cadastra sozinho nasce com `active=False` (mesmo campo que o admin já
    usava pra desativar usuário -- reaproveitado aqui como "pendente de
    aprovação"/"aprovado", ver `admin_toggle_active` em app.py, que já
    tinha o botão pronto no painel Administração) e só consegue entrar
    depois que o Allan ativa a conta pela aba Administração.
    `email_confirmed` fica sempre `True` (o campo continua existindo no
    schema só por compatibilidade com o desenho de migração pro Supabase
    Auth -- ver docstring do módulo -- mas não bloqueia mais nada aqui)."""
    email = email.strip().lower()
    if not name.strip() or not email or len(password) < 6:
        raise AuthError("Preencha nome, e-mail e uma senha com pelo menos 6 caracteres.")

    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise AuthError("Já existe uma conta com este e-mail.")

    user = User(
        name=name.strip(),
        email=email,
        password_hash=hash_password(password),
        role="user",
        email_confirmed=True,
        active=False,  # pendente de aprovação -- Allan ativa em /admin
    )
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("E-mail ou senha inválidos.")
    if not user.active:
        raise AuthError("Cadastro pendente de aprovação (ou desativado) — fale com o administrador.")
    return user


def create_session(db: Session, user: User, *, ip: str | None, user_agent: str | None) -> SessionModel:
    ttl_minutes = get_setting_int(db, "session_ttl_minutes", config.DEFAULT_SESSION_TTL_MINUTES)
    sess = SessionModel(
        user_id=user.id,
        token=uuid.uuid4().hex,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
        ip_address=ip,
        user_agent=(user_agent or "")[:300],
    )
    db.add(sess)
    db.commit()
    return sess


def get_valid_session(db: Session, token_str: str) -> SessionModel | None:
    if not token_str:
        return None
    sess = db.scalar(select(SessionModel).where(SessionModel.token == token_str))
    if sess is None or sess.revoked:
        return None
    now = datetime.now(timezone.utc)
    if sess.expires_at.replace(tzinfo=timezone.utc) < now:
        return None
    sess.last_seen_at = now
    db.commit()
    return sess


def revoke_session(db: Session, token_str: str) -> None:
    sess = db.scalar(select(SessionModel).where(SessionModel.token == token_str))
    if sess:
        sess.revoked = True
        db.commit()


def change_password(db: Session, user: User, *, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        raise AuthError("Senha atual incorreta.")
    if len(new_password) < 6:
        raise AuthError("A nova senha precisa ter pelo menos 6 caracteres.")
    user.password_hash = hash_password(new_password)
    db.commit()
