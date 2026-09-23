"""Rotas da aba "Mercado Primário" — ofertas públicas de dívida na CVM.

Mesmo padrão de `balcao_routes.py`: recebe a dependência de usuário por
parâmetro (evita import circular com app.py) e reusa o `templates` de lá,
para herdar o `?v=` dos estáticos (ver o incidente de 21/09 em app.py).
Fica atrás de login, como Notícias, Spreads e Balcão.
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import OfertaCVM, User
from .primario import queries
from .primario.coleta import ROTULO_INSTRUMENTO, STATUS_MORTO


def _validar(janela: str, instrumento: list[str], incentivada: str) -> tuple[str, list[str], str]:
    if janela not in queries.JANELAS:
        raise HTTPException(400, f"janela inválida: {janela} — use {list(queries.JANELAS)}")
    ruins = [i for i in instrumento if i not in queries.INSTRUMENTOS]
    if ruins:
        raise HTTPException(400, f"instrumento inválido: {ruins} — use {list(queries.INSTRUMENTOS)}")
    if incentivada not in queries.INCENTIVADA:
        raise HTTPException(400, "incentivada deve ser vazio, S ou N")
    return janela, instrumento, incentivada


def register_primario_routes(require_user_dep, templates) -> APIRouter:
    router = APIRouter()

    @router.get("/primario", response_class=HTMLResponse)
    def pagina(request: Request, user: User | None = Depends(require_user_dep)):
        return templates.TemplateResponse(
            request, "primario.html",
            {"user": user, "instrumentos": ROTULO_INSTRUMENTO},
        )

    @router.get("/api/primario/dados")
    def api_dados(
        janela: str = "12m", instrumento: list[str] = Query(default=[]), incentivada: str = "",
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        janela, instrumento, incentivada = _validar(janela, instrumento, incentivada)
        return queries.painel(db, janela=janela, instrumentos=instrumento,
                              incentivada=incentivada)

    @router.get("/api/primario/ofertas.csv")
    def api_csv(
        janela: str = "12m", instrumento: list[str] = Query(default=[]), incentivada: str = "",
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        """A janela inteira, uma oferta por linha — para cruzar no Excel.

        Separador ";" e BOM porque é o que o Excel em pt-BR abre sem pedir
        nada (mesma escolha do CSV do painel de uso)."""
        janela, instrumento, incentivada = _validar(janela, instrumento, incentivada)
        hoje = queries._hoje(db)
        inicio, fim = queries.janela_para_datas(janela, hoje)
        q = select(OfertaCVM).where(OfertaCVM.data_registro.is_not(None),
                                    OfertaCVM.data_registro <= fim,
                                    OfertaCVM.status.not_in(STATUS_MORTO))
        if inicio:
            q = q.where(OfertaCVM.data_registro >= inicio)
        if instrumento:
            q = q.where(OfertaCVM.instrumento.in_(instrumento))
        if incentivada in ("S", "N"):
            q = q.where(OfertaCVM.incentivado == incentivada)

        buf = io.StringIO()
        buf.write("﻿")
        w = csv.writer(buf, delimiter=";")
        w.writerow(["data_registro", "data_encerramento", "instrumento", "emissor",
                    "cnpj_emissor", "valor_total_registrado", "coordenador_lider",
                    "coordenador_lider_cvm", "status", "tipo_oferta", "publico_alvo",
                    "regime_distribuicao", "incentivada", "sustentavel", "emissao",
                    "agente_fiduciario", "numero_requerimento"])
        for o in db.scalars(q.order_by(OfertaCVM.data_registro.desc())).all():
            w.writerow([
                o.data_registro, o.data_encerramento, o.instrumento, o.nome_emissor,
                o.cnpj_emissor, f"{o.valor_total or 0:.2f}".replace(".", ","), o.lider,
                o.nome_lider, o.status, o.tipo_oferta, o.publico_alvo,
                o.regime_distribuicao, o.incentivado, o.sustentavel, o.emissao,
                o.agente_fiduciario, o.numero_requerimento,
            ])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":
                                 f'attachment; filename="ofertas_cvm_{janela}.csv"'})

    return router
