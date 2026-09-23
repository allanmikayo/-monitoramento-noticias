"""Rotas do registro de uso e do painel /admin/uso (22/09/2026).
Ver app/uso.py. Registrado em app.py com as dependências de lá, reusando o
MESMO `templates` (para herdar o `?v=` dos estáticos -- ver o incidente de
21/09 em app.py)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from . import uso
from .db import get_db
from .models import User


def register_uso_routes(current_user, require_admin, templates) -> APIRouter:
    router = APIRouter()

    @router.post("/api/uso")
    def api_registrar_uso(
        request: Request,
        dados: dict = Body(...),
        user: User | None = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        # Só aceita chamada do próprio site: o uso.js manda um cabeçalho
        # próprio, o que obriga um navegador em OUTRO site a pedir
        # permissão antes (e ninguém dá). Não segura script de linha de
        # comando -- para isso servem o vocabulário fixo e o corte de texto.
        if request.headers.get("x-hub-uso") != "1":
            return JSONResponse({"ok": False}, status_code=400)
        ok = uso.registrar(
            db,
            aba=str(dados.get("aba", "")), acao=str(dados.get("acao", "")),
            alvo=str(dados.get("alvo", "")),
            user_id=user.id if user else None,
            visitante=str(dados.get("visitante", "")),
        )
        return {"ok": ok}

    @router.get("/admin/uso", response_class=HTMLResponse)
    def pagina_uso(request: Request, user: User = Depends(require_admin)):
        return templates.TemplateResponse(request, "uso.html", {"user": user})

    @router.get("/api/admin/uso")
    def api_painel_uso(
        dias: int = 30, incluir_admin: int = 0, user_id: str = "",
        user: User = Depends(require_admin), db: Session = Depends(get_db),
    ):
        return uso.painel(db, dias, bool(incluir_admin), user_id or None)

    @router.get("/api/admin/uso.csv")
    def api_uso_csv(
        dias: int = 30, incluir_admin: int = 0, user_id: str = "",
        user: User = Depends(require_admin), db: Session = Depends(get_db),
    ):
        return Response(
            uso.csv_eventos(db, dias, bool(incluir_admin), user_id or None),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="uso_hub_{dias}d.csv"'},
        )

    return router
