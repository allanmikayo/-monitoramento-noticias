"""Reseta a senha de um usuário direto no banco, sem precisar da senha
antiga -- o app ainda não tem um fluxo de "esqueci minha senha" (que
precisaria de SMTP configurado pra mandar e-mail), então isso serve de
válvula de escape pra quando alguém esquece a senha.

Mexe no banco que estiver configurado em DATABASE_URL no `.env` no
momento em que você rodar (local SQLite, se estiver vazio/comentado, ou
o Postgres do Supabase, se estiver apontando pra lá).

Uso: `python -m scripts.reset_password` (pede o e-mail e a nova senha).
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.auth import hash_password
from app.db import SessionLocal
from app.models import User


def run() -> None:
    db = SessionLocal()
    try:
        email = input("E-mail da conta: ").strip().lower()
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            print(f"Nenhum usuário encontrado com o e-mail {email!r}.")
            return

        pw1 = getpass.getpass("Nova senha (pelo menos 6 caracteres, não aparece na tela): ")
        if len(pw1) < 6:
            print("Senha muito curta (mínimo 6 caracteres). Cancelado -- nada foi alterado.")
            return
        pw2 = getpass.getpass("Confirme a nova senha: ")
        if pw1 != pw2:
            print("As senhas não bateram. Cancelado -- nada foi alterado.")
            return

        user.password_hash = hash_password(pw1)
        # garante que a conta consegue logar mesmo que por algum motivo
        # estivesse com e-mail não confirmado ou desativada
        user.email_confirmed = True
        user.active = True
        db.commit()
        print(f"\nSenha de {email} atualizada com sucesso. Já pode entrar com a senha nova.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
