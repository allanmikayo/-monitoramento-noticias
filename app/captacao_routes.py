"""Rotas da aba "Cap. Líquida" (29/10/2026). Ver app/captacao.py.

Mesmo padrão dos outros routers: dependências de usuário por parâmetro.
Atrás de login, como as demais abas; a importação é só para admin.
"""
from __future__ import annotations

import os
from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import captacao as cl
from .db import engine, get_db
from .models import CaptacaoDiaria, CaptacaoMensal, User

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

router = APIRouter()

MAX_LINHAS_POR_LOTE = 10000


def register_captacao_routes(require_user_dep, require_admin_dep) -> APIRouter:
    @router.get("/captacao-liquida", response_class=HTMLResponse)
    def pagina(request: Request, user: User = Depends(require_user_dep)):
        return templates.TemplateResponse(request, "captacao.html", {"user": user})

    @router.get("/api/captacao/resumo")
    def api_resumo(user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        return cl.resumo(db)

    @router.get("/api/captacao/mensal")
    def api_mensal(user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        return cl.mensal(db)

    @router.get("/api/captacao/diario")
    def api_diario(dias: int = 15, user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        if dias not in (15, 30, 60):
            raise HTTPException(status_code=400, detail="dias deve ser 15, 30 ou 60")
        return cl.diario(db, dias)

    @router.get("/api/captacao/amplitude")
    def api_amplitude(segmento: str, user: User = Depends(require_user_dep), db: Session = Depends(get_db)):
        if segmento not in cl.SEGMENTOS:
            raise HTTPException(status_code=400, detail=f"segmento inválido — use {list(cl.SEGMENTOS)}")
        return cl.amplitude(db, segmento)

    # ---- importação (Administração) ------------------------------------
    @router.get("/api/admin/captacao/status")
    def api_status(user: User = Depends(require_admin_dep), db: Session = Depends(get_db)):
        return cl.status_importacao(db)

    @router.post("/api/admin/captacao/importar")
    def api_importar(
        payload: dict = Body(...),
        user: User = Depends(require_admin_dep), db: Session = Depends(get_db),
    ):
        """Um LOTE do export, lido pelo navegador: {cabecalho, linhas,
        periodo?}. `periodo` = [de, ate] do ARQUIVO INTEIRO, só no primeiro
        lote: apaga esse período antes de gravar (o export é uma extração
        completa). Em lotes porque a Vercel aceita até 4,5 MB e 10 s por
        chamada."""
        cabecalho, linhas = payload.get("cabecalho"), payload.get("linhas")
        if not isinstance(cabecalho, list) or not isinstance(linhas, list):
            raise HTTPException(status_code=400, detail="envie {cabecalho: [...], linhas: [[...], ...]}")
        if len(linhas) > MAX_LINHAS_POR_LOTE:
            raise HTTPException(status_code=400, detail=f"no máximo {MAX_LINHAS_POR_LOTE} linhas por lote")
        try:
            tipo, registros, descartadas = cl.converter_linhas(cabecalho, linhas)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        apagadas = 0
        periodo = payload.get("periodo")
        if periodo:
            try:
                de, ate = date.fromisoformat(periodo[0]), date.fromisoformat(periodo[1])
            except (TypeError, ValueError, IndexError):
                raise HTTPException(status_code=400, detail="periodo deve ser [AAAA-MM-DD, AAAA-MM-DD]")
            # A Vercel não roda DDL no boot: a tabela nasce aqui, na 1ª carga.
            (CaptacaoDiaria if tipo == "diario" else CaptacaoMensal).__table__.create(bind=engine, checkfirst=True)
            apagadas = cl.apagar_periodo(db, tipo, de, ate)
        resultado = cl.importar(db, tipo, registros)
        db.commit()
        resultado.update(descartadas=descartadas, apagadas=apagadas)
        return resultado

    return router
