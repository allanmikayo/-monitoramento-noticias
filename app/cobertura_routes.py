"""Rotas do módulo "Repositório de Relatórios" — catálogo dos relatórios do
time de Fixed Income Credit Research publicados no Smart (pedido do Allan,
13/08/2026; ver models.py, seção "Repositório de Relatórios", pro porquê do
módulo existir).

Router separado, mesmo padrão de spreads_routes.py — incluído em app/app.py
via `app.include_router(register_cobertura_routes(current_user))`. Recebe a
dependência de usuário por parâmetro pra evitar import circular, e usa
`current_user` (opcional, devolve None sem sessão) porque o catálogo é
PÚBLICO: qualquer um com o link consulta. Só a edição de tags exige
`role == "admin"` — os relatórios de research são públicos, mas a
classificação é curadoria do time e não pode ser editada por qualquer um.

Sobre a ingestão: o Smart exige sessão autenticada (SSO + MFA) e a API
`proxy-api.cloud.itau.com.br/research/v1/reports` recusa requisição sem
header de autorização — testado em 13/08/2026 abrindo o endpoint direto no
navegador já logado, o Chrome recebeu erro. Ou seja: NÃO adianta um job do
Actions tentar raspar o Smart como faz com InfoMoney/CVM, e guardar a
credencial pessoal do Allan num secret está fora de cogitação. A coleta roda
onde a sessão existe — no navegador dele, por um bookmarklet (ver
static/cobertura-bookmarklet.js) — e manda o resultado para
`POST /api/cobertura/ingest`, autenticado por chave compartilhada
(COBERTURA_INGEST_TOKEN), mesmo espírito do CRON_SECRET do cron-trigger.
"""
from __future__ import annotations

import os
import unicodedata
from datetime import date, datetime, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import (
    Company,
    CompanyAlias,
    Report,
    Sector,
    User,
    report_company,
    report_sector,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

SMART_URL = "https://www.itau.com.br/itaubba-pt/portal/credit/report/"

# Chave que o bookmarklet manda no header X-Ingest-Token. Sem ela definida o
# endpoint fica desligado (mesma lógica do CRON_SECRET) — evita que uma
# instância mal configurada aceite ingestão de qualquer um.
INGEST_TOKEN = os.getenv("COBERTURA_INGEST_TOKEN", "")

# O bookmarklet roda no domínio do Smart, então o POST é cross-origin e
# precisa de CORS explícito. Restrito a itau.com.br de propósito: é a única
# origem de onde a coleta pode legitimamente partir.
ORIGENS_INGEST = {"https://www.itau.com.br", "https://itau.com.br"}


def _norm(s: str) -> str:
    """Minúsculas sem acento, para casar nome de empresa com texto livre."""
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()


def _exige_admin(user: User | None) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Faça login para editar as tags.")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Só administradores editam a classificação.")
    return user


def _parse_data(valor) -> date | None:
    """Aceita ISO ('2026-08-12') ou o formato que aparece na tela do Smart
    ('12 ago, 2026'). O bookmarklet já manda ISO, mas a ingestão é uma
    fronteira externa — melhor não confiar."""
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    txt = str(valor).strip()
    try:
        return date.fromisoformat(txt[:10])
    except ValueError:
        pass
    meses = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
             "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
    import re
    m = re.match(r"(\d{1,2})\s+(\w{3})\w*,?\s*(\d{4})", _norm(txt))
    if m and m.group(2) in meses:
        return date(int(m.group(3)), meses[m.group(2)], int(m.group(1)))
    return None


def _empresas_por_nome(db: Session) -> dict[str, Company]:
    """Índice nome/alias normalizado -> Company, montado a partir do cadastro
    que já existe para as notícias. É o que garante padronização: a ingestão
    só aceita empresa que exista em Fontes & Empresas, então não há como
    nascer "Petrobras" e "Petrobrás" como coisas diferentes."""
    idx: dict[str, Company] = {}
    for c in db.scalars(select(Company)).all():
        idx[_norm(c.name)] = c
    for a in db.scalars(select(CompanyAlias)).all():
        idx.setdefault(_norm(a.alias), a.company)
    return idx


def _serializar(r: Report) -> dict:
    return {
        "id": r.id,
        "titulo": r.title,
        "data": r.published_at.isoformat() if r.published_at else None,
        "categoria": r.category,
        "tipo_investimento": r.investment_type,
        "analista": r.analyst,
        "url": SMART_URL + r.id,
        "mercado": r.is_market,
        "revisado": r.reviewed,
        "empresas": sorted((c.name for c in r.companies), key=_norm),
        "setores_extra": sorted((s.name for s in r.sector_tags), key=_norm),
    }


def register_cobertura_routes(current_user) -> APIRouter:
    router = APIRouter()

    # -----------------------------------------------------------------
    # Página
    # -----------------------------------------------------------------
    @router.get("/cobertura", response_class=HTMLResponse)
    def pagina(request: Request, user: User | None = Depends(current_user)):
        return templates.TemplateResponse(
            "cobertura.html",
            {"request": request, "user": user, "is_admin": bool(user and user.role == "admin")},
        )

    # -----------------------------------------------------------------
    # Dados para a tela
    # -----------------------------------------------------------------
    @router.get("/api/cobertura/dados")
    def dados(db: Session = Depends(get_db)):
        relatorios = db.scalars(
            select(Report).order_by(Report.published_at.desc().nullslast())
        ).unique().all()

        # principal (empresa do título) vive na tabela de associação; uma
        # query só evita N+1 numa base de ~1.300 relatórios.
        principais: dict[str, set[int]] = {}
        for rid, cid in db.execute(
            select(report_company.c.report_id, report_company.c.company_id)
            .where(report_company.c.principal.is_(True))
        ).all():
            principais.setdefault(rid, set()).add(cid)

        out = []
        for r in relatorios:
            d = _serializar(r)
            pri = principais.get(r.id, set())
            d["empresa_principal"] = sorted((c.name for c in r.companies if c.id in pri), key=_norm)
            d["revisar"] = not (r.companies or r.sector_tags or r.is_market)
            out.append(d)

        empresas = [
            {"empresa": c.name, "setor": c.sector.name, "analista": c.analyst or "—"}
            for c in db.scalars(select(Company).where(Company.active.is_(True))).all()
        ]
        empresas.sort(key=lambda e: _norm(e["empresa"]))

        ultima = db.scalar(select(Report.ingested_at).order_by(Report.ingested_at.desc()))
        return {
            "gerado_em": (ultima or datetime.now(timezone.utc)).date().isoformat(),
            "empresas": empresas,
            "relatorios": out,
        }

    # -----------------------------------------------------------------
    # Edição de tags (admin)
    # -----------------------------------------------------------------
    @router.post("/api/cobertura/relatorio/{report_id}/tags")
    def editar_tags(
        report_id: str,
        payload: dict = Body(...),
        user: User | None = Depends(current_user),
        db: Session = Depends(get_db),
    ):
        _exige_admin(user)
        r = db.get(Report, report_id)
        if not r:
            raise HTTPException(status_code=404, detail="Relatório não encontrado.")

        idx = _empresas_por_nome(db)
        novas: list[Company] = []
        for nome in payload.get("empresas", []):
            c = idx.get(_norm(nome))
            if not c:
                raise HTTPException(
                    status_code=400,
                    detail=f'"{nome}" não está no cadastro. Cadastre em Fontes & Empresas antes de usar como tag.',
                )
            if c not in novas:
                novas.append(c)

        setores_idx = {_norm(s.name): s for s in db.scalars(select(Sector)).all()}
        setores: list[Sector] = []
        for nome in payload.get("setores", []):
            s = setores_idx.get(_norm(nome))
            if not s:
                raise HTTPException(status_code=400, detail=f'Setor "{nome}" não existe.')
            if s not in setores:
                setores.append(s)

        r.companies = novas
        r.sector_tags = setores
        r.is_market = bool(payload.get("mercado", False))
        r.reviewed = True   # a partir daqui a reingestão não sobrescreve
        db.commit()
        return {"ok": True, "id": r.id}

    # -----------------------------------------------------------------
    # Instalação do bookmarklet (admin) — a página monta o link já com o
    # token embutido, por isso é restrita: quem tiver o link tem o poder de
    # escrever na base.
    # -----------------------------------------------------------------
    @router.get("/cobertura/bookmarklet", response_class=HTMLResponse)
    def bookmarklet(request: Request, user: User | None = Depends(current_user)):
        _exige_admin(user)
        base = str(request.base_url).rstrip("/")
        return templates.TemplateResponse(
            "cobertura_bookmarklet.html",
            {
                "request": request,
                "user": user,
                "base": base,
                "token": INGEST_TOKEN,
                "configurado": bool(INGEST_TOKEN),
            },
        )

    # -----------------------------------------------------------------
    # Ingestão (bookmarklet)
    # -----------------------------------------------------------------
    @router.options("/api/cobertura/ingest")
    def ingest_preflight(origin: str | None = Header(default=None)):
        return JSONResponse({}, headers=_cors(origin))

    @router.options("/api/cobertura/empresas")
    def empresas_preflight(origin: str | None = Header(default=None)):
        return JSONResponse({}, headers=_cors(origin))

    @router.get("/api/cobertura/empresas")
    def empresas_termos(
        origin: str | None = Header(default=None),
        x_ingest_token: str | None = Header(default=None),
        db: Session = Depends(get_db),
    ):
        """Nomes + aliases de cada empresa, para o bookmarklet casar contra o
        texto do resumo. Vem do cadastro do app em vez de uma lista fixa
        dentro do script: cadastrar empresa em Fontes & Empresas passa a
        melhorar o casamento aqui e no monitor de notícias ao mesmo tempo."""
        if not INGEST_TOKEN or x_ingest_token != INGEST_TOKEN:
            raise HTTPException(status_code=401, detail="Token de ingestão inválido.")
        termos = []
        for c in db.scalars(select(Company).where(Company.active.is_(True))).all():
            termos.append({"empresa": c.name, "termos": [c.name] + [a.alias for a in c.aliases]})
        return JSONResponse({"termos": termos}, headers=_cors(origin))

    @router.post("/api/cobertura/ingest")
    def ingest(
        payload: dict = Body(...),
        origin: str | None = Header(default=None),
        x_ingest_token: str | None = Header(default=None),
        db: Session = Depends(get_db),
    ):
        if not INGEST_TOKEN:
            raise HTTPException(status_code=503, detail="Ingestão desligada (COBERTURA_INGEST_TOKEN não definido).")
        if x_ingest_token != INGEST_TOKEN:
            raise HTTPException(status_code=401, detail="Token de ingestão inválido.")

        itens = payload.get("relatorios") or []
        idx = _empresas_por_nome(db)
        criados = atualizados = ignorados = 0

        for it in itens:
            rid = (it.get("id") or "").strip()
            if not rid:
                continue
            r = db.get(Report, rid)
            novo = r is None
            if novo:
                r = Report(id=rid)
                db.add(r)

            r.title = it.get("titulo") or (r.title if not novo else "(sem título)")
            r.published_at = _parse_data(it.get("data")) or (None if novo else r.published_at)
            r.category = it.get("categoria") or (None if novo else r.category)
            r.investment_type = it.get("tipo_investimento") or (None if novo else r.investment_type)
            r.analyst = it.get("analista") or (None if novo else r.analyst)

            # Curadoria humana ganha do robô: se alguém já revisou as tags,
            # a reingestão atualiza só os metadados e não mexe na
            # classificação. Sem isso, cada rodada do bookmarklet desfaria
            # a revisão feita na tela.
            if r.reviewed and not novo:
                ignorados += 1
            else:
                empresas: list[Company] = []
                principais: set[int] = set()
                for nome in it.get("empresas", []):
                    c = idx.get(_norm(nome))
                    if c and c not in empresas:
                        empresas.append(c)
                for nome in it.get("empresa_principal", []):
                    c = idx.get(_norm(nome))
                    if c:
                        principais.add(c.id)
                        if c not in empresas:
                            empresas.append(c)
                r.companies = empresas
                r.is_market = bool(it.get("mercado", False))
                db.flush()
                # `principal` é coluna da associação; o ORM não a expõe pelo
                # relationship, então marca direto.
                if principais:
                    db.execute(
                        report_company.update()
                        .where(report_company.c.report_id == r.id)
                        .where(report_company.c.company_id.in_(principais))
                        .values(principal=True)
                    )
            r.ingested_at = datetime.now(timezone.utc)
            criados += 1 if novo else 0
            atualizados += 0 if novo else 1

        db.commit()
        return JSONResponse(
            {
                "ok": True,
                "recebidos": len(itens),
                "criados": criados,
                "atualizados": atualizados,
                "preservados_por_revisao": ignorados,
                "total_na_base": db.scalar(select(func.count()).select_from(Report)),
            },
            headers=_cors(origin),
        )

    return router


def _cors(origin: str | None) -> dict:
    """Libera só as origens do Smart. Se vier de outro lugar, devolve sem o
    header — o navegador do chamador bloqueia a leitura da resposta."""
    if origin in ORIGENS_INGEST:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Headers": "Content-Type, X-Ingest-Token",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Max-Age": "86400",
        }
    return {}
