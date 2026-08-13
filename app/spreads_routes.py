"""Rotas do módulo "Spreads" — monitoramento de spreads de debêntures no
mercado secundário (Anbima/debentures.com.br), segundo módulo do Hub Credit
Research além do monitoramento de notícias (pedido do Allan, 23/07/2026;
ver CLAUDE.md pro desenho completo).

Router separado (em vez de crescer ainda mais o app.py) — incluído em
app/app.py via `app.include_router(spreads_router)`. Recebe a dependência
de usuário definida em app.py por parâmetro (evita import circular) --
desde 27/07/2026 é `current_user` (opcional, devolve `None` sem sessão),
não mais `require_user`: a aba Spreads virou pública (só Fontes & Empresas
e Administração continuam exigindo login, ver app.py)."""
from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .spreads import analitico, queries

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

router = APIRouter()


def _validar_classe(classe: str) -> str:
    if classe not in queries.CLASSES:
        raise HTTPException(status_code=400, detail=f"classe inválida — use uma de {queries.CLASSES}")
    return classe


def _validar_classe_ou_todos(classe: str) -> str:
    """Igual `_validar_classe`, mas aceita `""` como sentinela do filtro
    "Todos" (pedido do Allan, 27/07/2026, botão "Detalhes") -- só faz
    sentido pra listagem granular (`queries.detalhes_rows`), NUNCA pros
    gráficos agregados (que sempre exigem uma classe específica, ver
    docstring de `queries.py` sobre IPCA+/CDI+ não se misturarem)."""
    if classe and classe not in queries.CLASSES:
        raise HTTPException(status_code=400, detail=f"classe inválida — use \"\" (Todos) ou uma de {queries.CLASSES}")
    return classe


def _fmt_num_br(v: float | None, casas: int) -> str:
    """Número no padrão BR (vírgula decimal) pro export CSV -- BUG REAL
    (27/07/2026): Allan reportou números tipo "8.567.994.822.9" no CSV
    aberto no Google Sheets/Excel BR. Causa: o CSV usava `.` como
    separador decimal (`str(float)` do Python, ex. "8567.9948229...") --
    Sheets/Excel configurado em pt-BR espera `,` decimal e `.` como
    separador de milhar; ao ler um campo "8567.99..." ele tenta
    reinterpretar o `.` como milhar e cola os dígitos, virando um número
    gigante sem sentido. Também arredonda pra um número de casas
    razoável -- exportar float cru (`8567.994822900001`, erro de
    ponto flutuante da conta em cascata) só piora a confusão."""
    if v is None:
        return ""
    # f"{v:,.Nf}" formata no padrão US (1,234.5 -- vírgula de milhar, ponto
    # decimal); .translate troca os dois de uma vez só (mapeamento
    # simultâneo char-a-char, não duas chamadas .replace() em sequência,
    # que se pisaria: uma viraria a outra e a segunda desfaria a primeira).
    return f"{round(v, casas):,.{casas}f}".translate(str.maketrans(",.", ".,"))


def _parse_data(data: str | None) -> date | None:
    if not data:
        return None
    try:
        return datetime.strptime(data, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="data inválida — use AAAA-MM-DD")


def _validar_base(base: str) -> int:
    """Traduz o rótulo da base de comparação (d-1/WoW/MoM/QoQ/SoS/YoY,
    pedido do Allan 24/07/2026) pra posições no histórico -- ver
    queries.COMPARACAO_BASES. Validação fica no back-end de propósito (o
    front-end só manda o rótulo, sem saber o número de dias por trás)."""
    if base not in queries.COMPARACAO_BASES:
        raise HTTPException(
            status_code=400,
            detail=f"base de comparação inválida — use uma de {list(queries.COMPARACAO_BASES)}",
        )
    return queries.COMPARACAO_BASES[base]


def register_spreads_routes(require_user_dep) -> APIRouter:
    """Recebe a dependência `require_user` de app.py (evita import
    circular) e registra as rotas nela. Chamado uma vez em app.py."""

    @router.get("/spreads", response_class=HTMLResponse)
    def spreads_page(request: Request, user: User | None = Depends(require_user_dep)):
        return templates.TemplateResponse(
            request, "spreads.html", {
                "user": user, "classes": queries.CLASSES,
                "bases_comparacao": list(queries.COMPARACAO_BASES),
            }
        )

    @router.get("/api/spreads/summary")
    def api_spreads_summary(
        classe: str, base: str = "WoW", data: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return queries.kpi_summary(
            db, _validar_classe(classe), dias_comparacao=_validar_base(base), data_referencia=_parse_data(data),
        )

    @router.get("/api/spreads/series")
    def api_spreads_series(
        classe: str, codigo: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return {"series": queries.time_series(db, _validar_classe(classe), codigo=codigo)}

    @router.get("/api/spreads/movers")
    def api_spreads_movers(
        classe: str, base: str = "WoW", top: int = 10, data: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return queries.movers(
            db, _validar_classe(classe), dias_comparacao=_validar_base(base), top_n=top,
            data_referencia=_parse_data(data),
        )

    @router.get("/api/spreads/movement-distribution")
    def api_spreads_movement_distribution(
        classe: str, base: str = "WoW", data: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return {
            "snapshots": queries.movement_distribution(
                db, _validar_classe(classe), dias_comparacao=_validar_base(base), data_referencia=_parse_data(data),
            )
        }

    # ------------------------------------------------------------------
    # Análises de valor relativo (pedido do Allan, 12/08/2026) — ver
    # app/spreads/analitico.py pro desenho e pros números medidos que
    # justificam cada uma. Rotas separadas (não um único /analitico que
    # devolve tudo) de propósito: o bloco de valor relativo é o mais caro
    # de calcular (~0,4 s) e a tela carrega os quatro blocos em paralelo,
    # então uma consulta lenta não segura as outras três.
    # ------------------------------------------------------------------

    @router.get("/api/spreads/posicao-historica")
    def api_spreads_posicao_historica(
        classe: str, data: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return analitico.posicao_historica(
            db, _validar_classe(classe), data_referencia=_parse_data(data),
        )

    @router.get("/api/spreads/curva")
    def api_spreads_curva(
        classe: str, data: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return analitico.curva_por_rating(
            db, _validar_classe(classe), data_referencia=_parse_data(data),
        )

    @router.get("/api/spreads/dispersao")
    def api_spreads_dispersao(
        classe: str, data: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return analitico.dispersao_intra_rating(
            db, _validar_classe(classe), data_referencia=_parse_data(data),
        )

    @router.get("/api/spreads/compressao")
    def api_spreads_compressao(
        classe: str,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return analitico.compressao_entre_ratings(db, _validar_classe(classe))

    @router.get("/api/spreads/valor-relativo")
    def api_spreads_valor_relativo(
        classe: str, data: str | None = None, top: int = 12,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return analitico.decomposicao(
            db, _validar_classe(classe), data_referencia=_parse_data(data),
            top_n=max(1, min(top, 50)),
        )

    @router.get("/api/spreads/por-rating")
    def api_spreads_por_rating(
        classe: str, data: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return analitico.resumo_por_rating(
            db, _validar_classe(classe), data_referencia=_parse_data(data),
        )

    @router.get("/api/spreads/search")
    def api_spreads_search(
        q: str = "", classe: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return {"results": queries.search_debentures(db, q, classe=classe)}

    @router.get("/api/spreads/debenture/{codigo}")
    def api_spreads_debenture(
        codigo: str,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        detail = queries.bond_detail(db, codigo)
        if detail is None:
            raise HTTPException(status_code=404, detail="Debênture não encontrada")
        return detail

    @router.get("/api/spreads/emissores")
    def api_spreads_emissores(
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return {"emissores": queries.list_emissores(db)}

    # Tela inicial da aba Emissores, antes de selecionar algum emissor
    # (pedido do Allan, 27/07/2026): ranking de diferença B3 vs. Anbima
    # pra classe inteira -- ver docstring de `emissor_ranking_diferencas`
    # pro desenho (janela B3 de 7 dias ancorada na própria data do
    # boletim Anbima de cada emissor, não em "agora").
    @router.get("/api/spreads/emissor/ranking-diferencas")
    def api_spreads_emissor_ranking_diferencas(
        classe: str, top: int = 15, data: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return queries.emissor_ranking_diferencas(
            db, _validar_classe(classe), top_n=top, data_referencia=_parse_data(data),
        )

    # NOTA (24/07/2026): nome_emissor vai por QUERY STRING, não path param --
    # nomes de emissor reais têm "/" de verdade (ex.: "... S/A"), e o
    # Starlette não casa "%2F" codificado dentro de um segmento de path por
    # padrão (vira 404 -- confirmado testando com TestClient). Query string
    # não tem essa pegadinha. `nome` é uma LISTA (repetido na query string,
    # `?nome=A&nome=B`) desde a seleção múltipla (pedido do Allan,
    # 24/07/2026) -- um só emissor é só uma lista de tamanho 1.
    @router.get("/api/spreads/emissor")
    def api_spreads_emissor_detalhe(
        nome: list[str] = Query(...),
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        """Tabela de tickers dos emissores selecionados + empresa da
        cobertura ligada a cada um (quando já casada, ver
        scripts/match_debenture_issuers.py)."""
        tickers = queries.emissor_tickers(db, nome)
        if not tickers:
            raise HTTPException(status_code=404, detail="Emissor não encontrado")
        return {"tickers": tickers, "empresas": queries.companies_for_emissores(db, nome)}

    @router.get("/api/spreads/emissor/series")
    def api_spreads_emissor_series(
        nome: list[str] = Query(...), classe: str = "", nivel: str = "emissor",
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        if nivel not in ("emissor", "ticker"):
            raise HTTPException(status_code=400, detail="nivel inválido — use 'emissor' ou 'ticker'")
        emissor = queries.emissor_series(db, nome, _validar_classe(classe), nivel=nivel)
        mercado = queries.time_series(db, classe)  # linha discreta de referência, sem filtrar por emissor
        return {**emissor, "mercado": mercado}

    @router.get("/api/spreads/emissor/noticias")
    def api_spreads_emissor_noticias(
        nome: list[str] = Query(...),
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        empresas = queries.companies_for_emissores(db, nome)
        company_ids = [e["company_id"] for e in empresas.values()]
        return {"empresas": empresas, "noticias": queries.company_news(db, company_ids)}

    # Negócio a negócio da B3 (pedido do Allan, 24/07/2026) -- ver
    # app/spreads/b3_trades.py pro desenho da captura (roda a cada 15 min
    # via app/scheduler.py). Filtrado pelos mesmos tickers dos emissores
    # selecionados, igual à tabela de tickers.
    @router.get("/api/spreads/emissor/negociacoes")
    def api_spreads_emissor_negociacoes(
        nome: list[str] = Query(...), classe: str = "",
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return {"negociacoes": queries.emissor_trades(db, nome, _validar_classe(classe))}

    # Cards de taxa no topo da aba Emissores (pedido do Allan, 24/07/2026)
    # -- ver queries.emissor_taxas pro porquê de Anbima e B3 nunca se
    # misturarem num único número.
    @router.get("/api/spreads/emissor/taxas")
    def api_spreads_emissor_taxas(
        nome: list[str] = Query(...), classe: str = "",
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return queries.emissor_taxas(db, nome, _validar_classe(classe))

    # Botão "Detalhes" da aba Visão Geral (pedido do Allan, 27/07/2026):
    # nível mais granular de dado que os gráficos mostram -- uma linha por
    # Código+Data, sem agregação nenhuma. `classe=""` == "Todos" (só faz
    # sentido aqui, ver `_validar_classe_ou_todos`). `data=None` usa o dia
    # mais recente disponível. SIMPLIFICADO (27/07/2026, mesmo dia): Allan
    # pediu pra trocar o filtro de "até uma data" (histórico inteiro) por
    # um dia só -- sem paginação, um dia tem no máximo ~1700 linhas
    # (debêntures cadastradas).
    @router.get("/api/spreads/detalhes")
    def api_spreads_detalhes(
        classe: str = "", data: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        return queries.detalhes_rows(db, _validar_classe_ou_todos(classe), _parse_data(data))

    # Export CSV dos MESMOS filtros (classe/data) da tabela acima --
    # simplificado (27/07/2026, mesmo dia) pra reaproveitar `detalhes_rows`
    # direto (um dia só, sem paginação nem streaming especial -- não passa
    # de ~1700 linhas). Continua como rota separada (não o JSON) pra
    # baixar como arquivo de verdade (Content-Disposition), com BOM UTF-8
    # e `;` de separador (Excel BR abre certo sem "Dados > Texto em
    # colunas").
    @router.get("/api/spreads/detalhes/export")
    def api_spreads_detalhes_export(
        classe: str = "", data: str | None = None,
        user: User | None = Depends(require_user_dep), db: Session = Depends(get_db),
    ):
        classe_validada = _validar_classe_ou_todos(classe)
        resultado = queries.detalhes_rows(db, classe_validada, _parse_data(data))

        def gerar_csv():
            yield "﻿"  # BOM -- Excel só detecta UTF-8 certo (acentos) com isso na frente
            buf = io.StringIO()
            writer = csv.writer(buf, delimiter=";")
            writer.writerow([
                "Ticker", "Taxa (%)", "% PU Par", "PU", "Data Referência",
                "Indexador", "Deb Incentivada", "Spread (bps)", "Estoque (R$ mm)", "Duration (anos)",
            ])
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)
            for row in resultado["rows"]:
                writer.writerow([
                    row["codigo"],
                    _fmt_num_br(row["taxa"], 2),
                    _fmt_num_br(row["pct_pu_par"], 2),
                    _fmt_num_br(row["pu"], 4),
                    row["data"],
                    row["indexador"],
                    row["incentivada"],
                    _fmt_num_br(row["spread"], 1),
                    _fmt_num_br(row["estoque"], 1),
                    _fmt_num_br(row["duration"], 2),
                ])
                yield buf.getvalue()
                buf.seek(0); buf.truncate(0)

        classe_arquivo = (classe_validada or "todos").lower().replace(" ", "-").replace("+", "")
        data_arquivo = resultado["data"] or "sem-dado"
        nome_arquivo = f"spreads_detalhes_{classe_arquivo}_{data_arquivo}.csv"
        return StreamingResponse(
            gerar_csv(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'},
        )

    return router
