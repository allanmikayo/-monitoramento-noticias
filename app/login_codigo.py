"""Login por código no e-mail (22/09/2026).

Fluxo: e-mail -> `pedir_codigo` gera 6 dígitos e manda por e-mail ->
`confirmar_codigo` confere e devolve o usuário (criando no primeiro acesso).
Ninguém cria nem digita senha. A única senha que continua existindo é a do
admin, como porta de emergência se o envio de e-mail cair (ver
`auth.authenticate_admin`).

Travas contra abuso, todas contadas no banco (a Vercel não mantém memória
entre chamadas, então contador em memória não serviria):
  - código vale 10 minutos e aceita 5 tentativas;
  - no máximo 5 códigos por e-mail por hora;
  - no máximo 30 códigos por IP por hora;
  - pedir um código novo invalida os anteriores daquele e-mail.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import auth
from .models import LoginCode, User

VALIDADE = timedelta(minutes=10)
MAX_TENTATIVAS = 5
MAX_POR_EMAIL_HORA = 5
MAX_POR_IP_HORA = 30

# Chave em app_settings. "1" = e-mail novo entra como pendente e só acessa
# depois que o admin aprova. Padrão "0": qualquer e-mail entra (decisão do
# Allan, 22/09/2026 -- "a princípio").
EXIGE_APROVACAO_KEY = "login_exige_aprovacao"

_EMAIL_OK = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CodigoErro(Exception):
    pass


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime) -> datetime:
    # SQLite devolve sem tzinfo (ver `_iso_utc` em app.py).
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalizar_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL_OK.match(email) or len(email) > 200:
        raise CodigoErro("Digite um e-mail válido.")
    return email


def _hash(email: str, codigo: str) -> str:
    return hashlib.sha256(f"{email}:{codigo}".encode()).hexdigest()


def exige_aprovacao(db: Session) -> bool:
    return auth.get_setting_int(db, EXIGE_APROVACAO_KEY, 0) == 1


def usuario_por_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def pedir_codigo(db: Session, email: str, ip: str | None) -> str | None:
    """Gera e grava o código. Devolve o código em texto (quem chama manda o
    e-mail) ou None quando a conta existe e está bloqueada -- nesse caso a
    tela mostra a mesma mensagem de sempre, sem revelar o bloqueio."""
    email = normalizar_email(email)
    agora = _agora()
    uma_hora = agora - timedelta(hours=1)

    por_email = db.scalar(select(func.count(LoginCode.id)).where(
        LoginCode.email == email, LoginCode.created_at >= uma_hora)) or 0
    if por_email >= MAX_POR_EMAIL_HORA:
        raise CodigoErro("Muitos códigos pedidos para este e-mail. Tente de novo em 1 hora.")
    if ip:
        por_ip = db.scalar(select(func.count(LoginCode.id)).where(
            LoginCode.ip_address == ip, LoginCode.created_at >= uma_hora)) or 0
        if por_ip >= MAX_POR_IP_HORA:
            raise CodigoErro("Muitos pedidos a partir desta rede. Tente de novo em 1 hora.")

    user = usuario_por_email(db, email)
    # Bloqueado = conta existe, está inativa e NÃO é um pedido pendente
    # (pendente continua podendo confirmar o e-mail; só não entra).
    if user is not None and not user.active and user.email_confirmed:
        return None

    db.execute(update(LoginCode).where(LoginCode.email == email, LoginCode.used.is_(False))
               .values(used=True))
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    db.add(LoginCode(email=email, code_hash=_hash(email, codigo),
                     expires_at=agora + VALIDADE, ip_address=ip))
    db.commit()
    return codigo


def confirmar_codigo(db: Session, email: str, codigo: str, nome: str = "") -> User:
    """Confere o código. Devolve o usuário pronto para ganhar sessão.

    Levanta `CodigoErro` com a mensagem para a tela quando o código não
    vale, e `auth.AuthError` quando o código vale mas a conta ainda espera
    aprovação."""
    email = normalizar_email(email)
    codigo = re.sub(r"\D", "", codigo or "")
    reg = db.scalar(select(LoginCode).where(LoginCode.email == email, LoginCode.used.is_(False))
                    .order_by(LoginCode.created_at.desc()).limit(1))
    if reg is None or _utc(reg.expires_at) < _agora():
        raise CodigoErro("Código expirado. Peça um novo.")
    if reg.attempts >= MAX_TENTATIVAS:
        raise CodigoErro("Tentativas esgotadas. Peça um novo código.")

    reg.attempts += 1
    if len(codigo) != 6 or not secrets.compare_digest(reg.code_hash, _hash(email, codigo)):
        db.commit()
        restam = MAX_TENTATIVAS - reg.attempts
        raise CodigoErro("Código incorreto." + (f" Restam {restam} tentativa(s)." if restam else
                                                " Peça um novo código."))
    reg.used = True

    user = usuario_por_email(db, email)
    if user is None:
        nome = (nome or "").strip()[:120] or email.split("@")[0]
        pendente = exige_aprovacao(db)
        # password_hash vazio: `verify_password` nunca aceita, então essa
        # conta simplesmente não tem como entrar por senha.
        # email_confirmed=False marca "pedido pendente" (não "bloqueado").
        user = User(name=nome, email=email, password_hash="", role="user",
                    email_confirmed=not pendente, active=not pendente)
        db.add(user)
    elif not user.active and not user.email_confirmed:
        pass  # pedido pendente continua pendente
    db.commit()

    if not user.active:
        raise auth.AuthError("E-mail confirmado. Seu acesso fica liberado assim que o administrador aprovar.")
    return user
