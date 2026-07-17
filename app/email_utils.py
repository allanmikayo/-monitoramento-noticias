"""Envio de e-mail (confirmação de cadastro). Se SMTP não estiver configurado
(.env vazio), o link fica só logado — permite testar o fluxo localmente sem
precisar de um provedor de e-mail configurado."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from . import config

logger = logging.getLogger(__name__)


def _smtp_configured() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD)


def send_confirmation_email(to_email: str, name: str, token: str) -> str:
    """Envia (ou loga) o e-mail de confirmação. Retorna o link gerado —
    útil para exibir na tela em modo dev quando não há SMTP configurado."""
    link = f"{config.APP_BASE_URL}/confirmar-email?token={token}"
    subject = "Confirme seu cadastro — Monitoramento de Notícias"
    body = (
        f"Olá, {name}!\n\n"
        f"Confirme seu cadastro clicando no link abaixo:\n{link}\n\n"
        f"Se você não pediu este cadastro, ignore este e-mail."
    )

    if not _smtp_configured():
        logger.warning(
            "SMTP não configurado — link de confirmação para %s: %s", to_email, link
        )
        return link

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.FROM_EMAIL
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("E-mail de confirmação enviado para %s", to_email)
    except Exception:  # noqa: BLE001
        logger.exception("Falha ao enviar e-mail para %s — link: %s", to_email, link)

    return link
