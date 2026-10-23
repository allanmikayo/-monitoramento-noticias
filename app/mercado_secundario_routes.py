"""Rotas da aba "Mercado Secundário" (23/10/2026) — substitui o Balcão B3.

Mesmo padrão dos outros routers: recebe as dependências de usuário por
parâmetro (evita import circular com app.py). Atrás de login, como as
demais abas; a importação do export é só para admin.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db import engine, get_db
from .models import MercadoSecundario, User
from .spreads import mercado_secundario as ms

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

router = APIRouter()

MAX_LINHAS_POR_LOTE = 10000


def _classe(classe: str) -> str:
    if classe not in ms.CLASSES:
        raise HTTPException(status_code=400, detail=f"classe inválida — use {list(ms.CLASSES)}")
    return classe


def register_mercado_secundario_routes(require_user_dep, require_admin_dep) -> APIRouter:
    @router.get("/mercado-secundario", response_class=HTMLResponse)
    def pagina(request: Request, user: User = Depends(require_user_dep)):
        return templates.TemplateResponse(
            request, "mercado_secundario.html", {"user": user, "classes": ms.CLASSES},
        )

    # A aba se chamava Balcão B3: link salvo continua funcionando.
    @router.get("/balcao")
    def balcao_antigo():
        return RedirectResponse("/mercado-secundario", status_code=301)

    @router.get("/api/mercado-secundario/resumo")
    def api_resumo(user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        return ms.resumo(db)

    @router.get("/api/mercado-secundario/serie")
    def api_serie(classe: str, user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        return ms.serie(db, _classe(classe))

    @router.get("/api/mercado-secundario/ranking")
    def api_ranking(
        classe: str, janela: str = "semana", metrica: str = "volume", limite: int = 15,
        user: User = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        if janela not in ("semana", "mes"):
            raise HTTPException(status_code=400, detail="janela deve ser semana ou mes")
        if metrica not in ("volume", "giro"):
            raise HTTPException(status_code=400, detail="metrica deve ser volume ou giro")
        return ms.ranking(db, _classe(classe), janela, metrica, max(1, min(limite, 100)))

    @router.get("/api/mercado-secundario/pontos")
    def api_pontos(
        classe: str, meses: int = 1,
        user: User = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        if meses not in (0, 1, 3, 6, 12):
            raise HTTPException(status_code=400, detail="meses deve ser 0 (tudo), 1, 3, 6 ou 12")
        return ms.pontos_spread(db, _classe(classe), meses or None)

    # ---- importação (Administração) ------------------------------------
    @router.get("/api/admin/mercado-secundario/status")
    def api_status(user: User = Depends(require_admin_dep), db: Session = Depends(get_db)):
        return ms.status_importacao(db)

    @router.post("/api/admin/mercado-secundario/importar")
    def api_importar(
        payload: dict = Body(...),
        user: User = Depends(require_admin_dep), db: Session = Depends(get_db),
    ):
        """Um LOTE do export, já lido pelo navegador: {cabecalho, linhas}.

        O navegador lê o arquivo e manda em lotes porque a função da Vercel
        aceita corpo de até 4,5 MB e 10 s por chamada — o arquivo inteiro
        (50 mil linhas) não passaria numa chamada só.
        """
        cabecalho, linhas = payload.get("cabecalho"), payload.get("linhas")
        if not isinstance(cabecalho, list) or not isinstance(linhas, list):
            raise HTTPException(status_code=400, detail="envie {cabecalho: [...], linhas: [[...], ...]}")
        if len(linhas) > MAX_LINHAS_POR_LOTE:
            raise HTTPException(status_code=400, detail=f"no máximo {MAX_LINHAS_POR_LOTE} linhas por lote")
        try:
            registros, descartadas = ms.converter_linhas(cabecalho, linhas)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if payload.get("primeiro"):
            # A Vercel não roda DDL no boot (ver db.ensure_schema); a tabela
            # nasce aqui, na primeira importação. É uma consulta só.
            MercadoSecundario.__table__.create(bind=engine, checkfirst=True)
        resultado = ms.importar(db, registros)
        db.commit()
        resultado["descartadas"] = descartadas
        return resultado

    return router
