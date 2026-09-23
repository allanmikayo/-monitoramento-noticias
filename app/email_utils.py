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


class EnvioIndisponivel(Exception):
    """SMTP não configurado, ou o servidor de e-mail recusou/caiu."""


def smtp_configurado() -> bool:
    return _smtp_configured()


def send_login_code(to_email: str, codigo: str) -> None:
    """Manda o código de acesso (22/09/2026). Diferente do e-mail de
    confirmação antigo, aqui falhar NÃO pode ser silencioso: sem o e-mail a
    pessoa fica esperando um código que nunca chega. Por isso levanta
    `EnvioIndisponivel` e a tela mostra o problema."""
    if not _smtp_configured():
        raise EnvioIndisponivel("envio de e-mail não configurado")

    msg = EmailMessage()
    msg["Subject"] = f"{codigo} é o seu código de acesso — Hub Credit Research"
    msg["From"] = config.FROM_EMAIL
    msg["To"] = to_email
    msg.set_content(
        f"Seu código de acesso ao Hub Credit Research é:\n\n"
        f"    {codigo}\n\n"
        f"Ele vale por 10 minutos. Se não foi você que pediu, ignore este e-mail "
        f"— ninguém entra sem o código.\n"
    )
    msg.add_alternative(
        f"""<div style="font-family:Arial,sans-serif;max-width:420px;color:#111">
  <p style="font-size:15px;font-weight:bold;color:#FF6200;margin:0 0 16px">Hub Credit Research</p>
  <p style="margin:0 0 8px">Seu código de acesso:</p>
  <p style="font-size:30px;letter-spacing:6px;font-weight:bold;margin:0 0 16px">{codigo}</p>
  <p style="font-size:13px;color:#4a4a4a;margin:0">Vale por 10 minutos. Se não foi você que pediu,
  ignore este e-mail — ninguém entra sem o código.</p>
</div>""",
        subtype="html",
    )
    try:
        porta = config.SMTP_PORT
        if porta == 465:
            with smtplib.SMTP_SSL(config.SMTP_HOST, porta, timeout=15) as server:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, porta, timeout=15) as server:
                server.starttls()
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.send_message(msg)
    except Exception as e:  # noqa: BLE001
        logger.exception("Falha ao enviar código para %s", to_email)
        raise EnvioIndisponivel(str(e)) from e
    logger.info("Código de acesso enviado para %s", to_email)
