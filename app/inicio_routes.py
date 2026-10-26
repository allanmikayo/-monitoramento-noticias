"""Rotas da página inicial do Hub (26/10/2026). Ver app/inicio.py.

`/` passou a ser esta página; a aba Notícias foi para `/noticias`. Atrás
de login, como as demais abas internas.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import inicio
from .db import get_db
from .models import User

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

router = APIRouter()

# Fixed Income Team (lista passada pelo Allan em 26/10/2026). Fica aqui, e
# não no banco, porque muda raramente e não tem tela de edição.
EQUIPE = [
    {"nome": "Paula Toute, CNPI", "email": "paula.toute@itaubba.com"},
    {"nome": "Clara Lustosa, CNPI", "email": "clara.lustosa@itaubba.com"},
    {"nome": "Arthur Almeida Neto, CNPI", "email": "arthur.almeida@itaubba.com"},
    {"nome": "Allan Mikayo, CNPI", "email": "allan.mikayo@itaubba.com"},
]
EMAIL_EQUIPE = "credit.researchext@itaubba.com"


def register_inicio_routes(require_user_dep) -> APIRouter:
    @router.get("/", response_class=HTMLResponse)
    def pagina(request: Request, user: User = Depends(require_user_dep)):
        return templates.TemplateResponse(
            request, "inicio.html", {"user": user, "equipe": EQUIPE, "email_equipe": EMAIL_EQUIPE},
        )

    @router.get("/api/inicio/spreads")
    def api_spreads(user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        return inicio.spreads(db)

    @router.get("/api/inicio/secundario")
    def api_secundario(user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        return inicio.secundario(db)

    @router.get("/api/inicio/primario")
    def api_primario(user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        return inicio.primario_bloco(db)

    @router.get("/api/inicio/curvas")
    def api_curvas(user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        return inicio.curvas(db)

    @router.get("/api/inicio/noticias")
    def api_noticias(user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        return inicio.noticias(db)

    @router.get("/api/inicio/relatorios")
    def api_relatorios(user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        return inicio.relatorios(db)

    return router
