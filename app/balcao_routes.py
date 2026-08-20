"""Rotas da aba "Balcão B3" — volumetria de negociação de DEB/CRI/CRA.

Router separado, no mesmo padrão de `spreads_routes.py` e
`cobertura_routes.py`: recebe a dependência de usuário por parâmetro (evita
import circular com app.py) e é incluído lá com `app.include_router(...)`.

Fica atrás de login, como Notícias e Spreads (desde 13/08/2026 a única aba
pública é o Repositório de Relatórios).
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .spreads import balcao

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

router = APIRouter()

TIPOS_VALIDOS = set(balcao.INSTRUMENTOS)


def _validar_tipos(tipo: list[str] | None) -> list[str] | None:
    """`?tipo=DEB&tipo=CRI` — lista vazia significa "todos", não "nenhum".

    Mesma convenção dos filtros multi-select da aba Notícias: sem nada
    marcado a tela abre cheia, que é como o usuário chega nela.
    """
    if not tipo:
        return None
    invalidos = [t for t in tipo if t not in TIPOS_VALIDOS]
    if invalidos:
        raise HTTPException(
            status_code=400,
            detail=f"tipo inválido: {invalidos} — use {sorted(TIPOS_VALIDOS)}",
        )
    return tipo


def _validar_dias(dias: int, maximo: int) -> int:
    if not 1 <= dias <= maximo:
        raise HTTPException(status_code=400, detail=f"dias deve estar entre 1 e {maximo}")
    return dias


def register_balcao_routes(require_user_dep) -> APIRouter:
    @router.get("/balcao", response_class=HTMLResponse)
    # `request: Request` -- SEM a anotação, o FastAPI trata `request` como
    # query param obrigatório e a página inteira devolve 422. Pego pelo teste
    # `test_pagina_carrega_e_tem_os_blocos`.
    def balcao_page(request: Request, user: User | None = Depends(require_user_dep)):
        return templates.TemplateResponse(
            request, "balcao.html", {"user": user, "tipos": balcao.INSTRUMENTOS},
        )

    @router.get("/api/balcao/volumetria")
    def api_volumetria(
        tipo: list[str] = Query(default=[]),
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return balcao.volumetria(db, tipos=_validar_tipos(tipo))

    @router.get("/api/balcao/serie")
    def api_serie(
        dias: int = 90, tipo: list[str] = Query(default=[]),
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return balcao.serie_diaria(db, dias=_validar_dias(dias, 3650),
                                   tipos=_validar_tipos(tipo))

    @router.get("/api/balcao/ranking")
    def api_ranking(
        dias: int = 5, limite: int = 40, tipo: list[str] = Query(default=[]),
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return balcao.ranking_tickers(
            db, dias=_validar_dias(dias, 365),
            limite=max(1, min(limite, 200)), tipos=_validar_tipos(tipo),
        )

    @router.get("/api/balcao/volume-spread")
    def api_volume_spread(
        dias: int = 5, classe: str | None = None, tipo: list[str] = Query(default=[]),
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return balcao.volume_por_spread(
            db, dias=_validar_dias(dias, 365), classe=classe or None,
            tipos=_validar_tipos(tipo),
        )

    # ---- ao vivo -------------------------------------------------------
    @router.get("/api/balcao/tape")
    def api_tape(
        limite: int = 60, tipo: list[str] = Query(default=[]),
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return balcao.tape(db, limite=max(1, min(limite, 300)),
                           tipos=_validar_tipos(tipo))

    @router.get("/api/balcao/destaques")
    def api_destaques(
        dias: int = 3, limite: int = 10, tipo: list[str] = Query(default=[]),
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return balcao.destaques(
            db, dias_baseline=_validar_dias(dias, 30),
            limite=max(1, min(limite, 50)), tipos=_validar_tipos(tipo),
        )

    @router.get("/api/balcao/termometro")
    def api_termometro(
        tipo: list[str] = Query(default=[]),
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return balcao.termometro(db, tipos=_validar_tipos(tipo))

    return router
