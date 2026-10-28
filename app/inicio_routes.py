"""Rotas da página inicial do Hub (26/10/2026). Ver app/inicio.py.

`/` passou a ser esta página; a aba Notícias foi para `/noticias`. Atrás
de login, como as demais abas internas.
"""
from __future__ import annotations

import os

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Request
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


def register_inicio_routes(require_user_dep, require_admin_dep=None) -> APIRouter:
    @router.get("/", response_class=HTMLResponse)
    def pagina(request: Request, user: User = Depends(require_user_dep)):
        return templates.TemplateResponse(
            request, "inicio.html", {"user": user, "equipe": EQUIPE, "email_equipe": EMAIL_EQUIPE,
                                     "pode_confirmar": bool(user and user.role == "admin")},
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

    @router.get("/api/inicio/resultados")
    def api_resultados(user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        r = inicio.resultados(db)
        if user and user.role == "admin":
            from . import calendario
            try:
                r["importacao"] = calendario.ultimo_relatorio(db)
            except Exception:
                db.rollback()
        return r

    def _so_admin(user: User | None) -> None:
        if not user or user.role != "admin":
            raise HTTPException(status_code=403, detail="só admin altera a agenda")

    def _criar_tabelas(db: Session) -> None:
        # Nascem aqui se ainda não existem (a Vercel não roda DDL no boot;
        # checkfirst é uma consulta só).
        from .models import EmpresaTipoCobertura, ResultadoConfirmado
        for t in (ResultadoConfirmado, EmpresaTipoCobertura):
            t.__table__.create(db.get_bind(), checkfirst=True)

    # Data da agenda (e tipo Local/Offshore), ajustada no próprio calendário.
    @router.post("/api/inicio/resultados/confirmar")
    def api_confirmar(
        company_id: int = Body(...), data: str | None = Body(None), horario: str | None = Body(None),
        tipo: str | None = Body(""),
        user: User = Depends(require_admin_dep or require_user_dep), db: Session = Depends(get_db),
    ):
        from . import calendario
        from .models import Company

        _so_admin(user)
        if db.get(Company, company_id) is None:
            raise HTTPException(status_code=404, detail="empresa não encontrada")
        try:
            dia = date.fromisoformat(data) if data else None
        except ValueError:
            raise HTTPException(status_code=400, detail="data no formato AAAA-MM-DD") from None
        if tipo not in ("", None, *calendario.TIPOS_COBERTURA):
            raise HTTPException(status_code=400, detail="tipo inválido")
        _criar_tabelas(db)
        calendario.confirmar(db, company_id, dia, (horario or "").strip()[:20] or None, user.email, tipo=tipo)
        return {"ok": True}

    # Lista revisada do time, colada do Excel. Substitui a agenda da temporada.
    @router.post("/api/inicio/resultados/importar")
    def api_importar(
        texto: str = Body(..., embed=True),
        user: User = Depends(require_admin_dep or require_user_dep), db: Session = Depends(get_db),
    ):
        from . import calendario

        _so_admin(user)
        if len(texto) > 50_000:
            raise HTTPException(status_code=400, detail="lista grande demais")
        _criar_tabelas(db)
        try:
            return calendario.importar_lista(db, texto, user.email)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None

    return router
