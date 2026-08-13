"""Aplicação FastAPI: login/cadastro, dashboard de notícias, gerenciamento
de fontes/empresas/keywords e painel administrativo."""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import Cookie, Depends, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, selectinload

from . import auth, config, refresh_state, store
from .db import Base, SessionLocal, engine, get_db, run_migrations
from .models import AppSetting, Company, RunLog, Sector, SectorKeyword, Session as SessionModel, Source, User
from .pipeline import run_pipeline
from .scheduler import start_scheduler, trigger_now
from .spreads import queries as spreads_queries
from .cobertura_routes import register_cobertura_routes
from .spreads_routes import register_spreads_routes
from .taxonomy import build_index

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

_BRT = ZoneInfo("America/Sao_Paulo")


def _brt_filter(dt: datetime | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Filtro Jinja pra exibir datas do painel admin em horário de Brasília.

    Mesma causa-raiz do bug de horário do dashboard principal (ver
    `_iso_utc` acima): o SQLite devolve os datetimes sem tzinfo mesmo eles
    representando UTC, e o admin.html chamava `.strftime()` direto nesses
    valores -- ou seja, mostrava a hora UTC crua rotulada como se já fosse
    horário local. Aqui assumimos UTC quando falta tzinfo e convertemos
    explicitamente pra America/Sao_Paulo antes de formatar."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BRT).strftime(fmt)


templates.env.filters["brt"] = _brt_filter

app = FastAPI(title="Monitoramento de Notícias — Crédito Privado")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

SESSION_COOKIE = "session_token"

# Vercel define essa variável automaticamente em produção -- usada só pra
# marcar o cookie de sessão como Secure (só trafega por HTTPS) na nuvem,
# sem quebrar o uso local (http://localhost não é HTTPS, então Secure
# bloquearia o cookie de funcionar no .bat do Allan).
IS_VERCEL = bool(os.getenv("VERCEL"))


def _iso_utc(dt: datetime | None) -> str | None:
    """Serializa um datetime pra ISO 8601 garantindo o sufixo de fuso UTC.

    BUG CORRIGIDO (17/07/2026): o SQLite não guarda timezone de verdade --
    mesmo as colunas sendo `DateTime(timezone=True)` e todo datetime sendo
    criado com `tzinfo=timezone.utc` antes de gravar, o SQLAlchemy devolve
    esses valores SEM tzinfo (naive) depois de ler de volta do banco. Como
    `datetime.isoformat()` de um valor naive não inclui nenhum sufixo de
    fuso (ex.: "2026-07-16T22:56:00", sem "Z" nem "+00:00"), o navegador do
    Allan interpretava esse texto como se já fosse horário LOCAL (regra do
    JavaScript pra strings ISO sem fuso) -- então um horário que era UTC
    (3h à frente do horário de Brasília) aparecia no dashboard como se já
    fosse horário de Brasília, adiantando toda hora exibida em 3h (a data
    batia porque o erro raramente cruza a virada do dia). Corrigido
    atribuindo explicitamente `tzinfo=timezone.utc` aqui antes do
    isoformat(), pra o navegador converter certinho pro fuso local dele."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


@app.on_event("startup")
def _on_startup():
    Base.metadata.create_all(engine)
    run_migrations()
    # Modo nuvem (config.CLOUD_MODE): quem roda o robô de coleta é o GitHub
    # Actions (.github/workflows/scrape.yml), não este processo -- rodar o
    # agendador em processo aqui não funcionaria mesmo (Playwright não roda
    # de forma confiável numa função serverless do Vercel, e o processo não
    # fica vivo entre chamadas pra um agendador de verdade funcionar).
    # Localmente (sem GITHUB_TOKEN/GITHUB_REPO configurados) continua igual
    # a sempre foi: agendador em processo, sem precisar mudar nada.
    if not config.CLOUD_MODE:
        start_scheduler()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def current_user(
    db: Session = Depends(get_db),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User | None:
    if not session_token:
        return None
    sess = auth.get_valid_session(db, session_token)
    if sess is None:
        return None
    return db.get(User, sess.user_id)


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador.")
    return user


# Módulo "Spreads" (Hub Credit Research, 23/07/2026) -- registrado aqui (não
# via decorator direto) pra reusar a mesma dependência de app.py sem criar
# import circular entre app.py e spreads_routes.py. MUDOU (27/07/2026):
# passava `require_user` (toda a aba Spreads exigia login); agora passa
# `current_user` (opcional) -- Allan pediu que Notícias e Spreads virassem
# públicas, só Fontes & Empresas e Administração continuam atrás de login
# (ver `require_user`/`require_admin` nas rotas correspondentes abaixo).
# Os handlers em spreads_routes.py só usam esse `user` como parâmetro de
# dependência (nunca leem `user.algumacoisa`), então aceitar `None` aqui é
# seguro -- conferido antes de trocar.
app.include_router(register_spreads_routes(current_user))

# Módulo "Repositório de Relatórios" (13/08/2026) -- catálogo dos relatórios
# do Smart tagueados por empresa/setor. Mesma dependência opcional das outras
# abas públicas: consultar não exige login, editar tag exige role admin
# (conferido dentro do módulo, em `_exige_admin`, não aqui).
app.include_router(register_cobertura_routes(current_user))

# Aba "Banco de Dados" (12/08/2026) -- consulta e extração do que está
# armazenado. Ao contrário de Notícias e Spreads, esta é RESTRITA: recebe
# `require_admin`, não `current_user`. Ver app/spreads/banco_routes.py
# para as barreiras do SQL livre.
from .spreads.banco_routes import registrar_rotas as _registrar_banco  # noqa: E402

_registrar_banco(app, require_admin, templates)


@app.exception_handler(HTTPException)
async def _redirect_on_303(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)


# ---------------------------------------------------------------------------
# Login / cadastro
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(
    request: Request, erro: str | None = None, msg: str | None = None,
    user: User | None = Depends(current_user),
):
    return templates.TemplateResponse(request, "login.html", {"erro": erro, "msg": msg, "user": user})


@app.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        user = auth.authenticate(db, email, password)
    except auth.AuthError as e:
        return RedirectResponse(url=f"/login?erro={e}", status_code=303)

    sess = auth.create_session(
        db, user,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, sess.token, httponly=True, samesite="lax",
        secure=IS_VERCEL, max_age=60 * 60 * 24,
    )
    return resp


@app.get("/logout")
def logout(response: RedirectResponse, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE), db: Session = Depends(get_db)):
    if session_token:
        auth.revoke_session(db, session_token)
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/cadastro", response_class=HTMLResponse)
def signup_form(
    request: Request, erro: str | None = None, msg: str | None = None,
    user: User | None = Depends(current_user),
):
    return templates.TemplateResponse(request, "signup.html", {"erro": erro, "msg": msg, "user": user})


@app.post("/cadastro")
def signup_submit(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        auth.register_user(db, name=name, email=email, password=password)
    except auth.AuthError as e:
        return RedirectResponse(url=f"/cadastro?erro={e}", status_code=303)
    # MUDOU (27/07/2026): sem confirmação por e-mail -- cadastro fica
    # pendente até o Allan aprovar manualmente na aba Administração (ver
    # docstring de auth.register_user).
    msg = "Cadastro enviado! Sua conta fica pendente até o administrador aprovar o acesso."
    return RedirectResponse(url=f"/login?msg={msg}", status_code=303)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User | None = Depends(current_user), db: Session = Depends(get_db)):
    import json as _json
    sectors = db.query(Sector).order_by(Sector.name).all()
    companies = db.query(Company).filter(Company.active.is_(True)).order_by(Company.name).all()
    companies_json = _json.dumps(
        [{"id": c.id, "name": c.name, "sector_id": c.sector_id} for c in companies]
    ).replace("</", "<\\/")  # evita fechar a tag <script> se algum nome contiver "</"
    last_run = db.query(RunLog).order_by(RunLog.id.desc()).first()
    # Fontes pro filtro multi-select (12/08/2026). Vem de `sources` (o
    # cadastro), não de um DISTINCT em `articles`: assim a lista fica
    # estável mesmo numa janela de tempo em que a fonte não publicou nada
    # -- uma lista que muda de tamanho conforme o filtro de data é
    # confusa de usar.
    fontes = db.query(Source).order_by(Source.name).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user, "sectors": sectors, "fontes": fontes,
            "companies_json": companies_json,
            "window_presets": config.WINDOW_PRESETS, "default_window": config.DEFAULT_WINDOW,
            "scan_interval_minutes": config.SCAN_INTERVAL_MINUTES,
            "last_run": last_run,
        },
    )


@app.get("/api/articles")
def api_articles(
    window: str = "24h",
    # Setor/empresa/cobertura agora aceitam mais de um valor (pedido do
    # Allan, 03/08/2026) -- `?sector_id=1&sector_id=2` vira uma lista aqui
    # (FastAPI junta parâmetros repetidos com o mesmo nome automaticamente).
    sector_id: list[int] = Query(default=[]),
    company_id: list[int] = Query(default=[]),
    source_domain: str | None = None,
    # `?source_name=A&source_name=B` -- mesma mecânica de sector_id.
    source_name: list[str] = Query(default=[]),
    article_type: str | None = None,
    coverage: list[str] = Query(default=["minha"]),
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    hours = config.WINDOW_PRESETS.get(window, 24)
    articles = store.list_articles(
        db, window_hours=hours, sector_ids=sector_id, company_ids=company_id,
        source_domain=source_domain, source_names=source_name,
        article_type=article_type, coverage=coverage,
    )
    out = []
    for a in articles:
        # CVM ("Documento CVM"/fato_relevante): o Allan confirmou que o link
        # direto pro documento não abre de forma confiável fora do contexto
        # do próprio site do RAD (a popup/sessão do frmExibirArquivoIPE
        # Externo.aspx depende de navegação interna, não de acesso direto
        # por URL) -- por pedido dele (17/07/2026), toda notícia de CVM
        # aponta pra página de busca do RAD em vez do documento específico.
        # `a.url` continua guardando o link específico internamente (usado
        # só pra dedupe, nunca mais exibido pro usuário nesse tipo).
        link_url = config.CVM_SEARCH_URL if a.article_type == "fato_relevante" else a.url
        out.append({
            "id": a.id,
            "title": a.title,
            "url": link_url,
            "snippet": a.snippet,
            "source_name": a.source_name,
            "domain": a.domain,
            "article_type": a.article_type,
            "published_at": _iso_utc(a.published_at),
            "found_at": _iso_utc(a.found_at),
            "is_covered": a.is_covered,
            "companies": [{"id": c.id, "name": c.name, "sector": c.sector.name} for c in a.companies],
            "sector_tags": [{"id": s.id, "name": s.name} for s in a.sector_tags],
        })
    return {"count": len(out), "articles": out}


def _run_pipeline_in_background():
    def _progress(current: int, total: int, name: str) -> None:
        refresh_state.update(current, total, name)

    try:
        summary = run_pipeline(triggered_by="manual", progress_cb=_progress)
        refresh_state.finish(summary)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).exception("Falha na varredura manual")
        refresh_state.fail(f"{type(e).__name__}: {e}")


def _dispatch_github_workflow(workflow_file: str = None) -> tuple[bool, str | None]:
    """Aciona um workflow do GitHub Actions via `workflow_dispatch` --
    usado no lugar de rodar o pipeline neste processo quando
    `config.CLOUD_MODE` está ativo (ver `_on_startup`). Generalizado
    (24/07/2026) pra aceitar QUALQUER arquivo de workflow, não só
    `scrape.yml` -- os módulos de spreads/negócio a negócio da B3 ganharam
    workflows próprios (`spreads_daily.yml`, `b3_trades.yml`), ver
    `/api/cron-trigger` e CLAUDE.md. Sem argumento, mantém o comportamento
    antigo (dispara `config.GITHUB_WORKFLOW_FILE`, usado pelo botão
    "Forçar atualização" de notícias). Retorna (sucesso, mensagem_de_erro)."""
    import requests

    workflow_file = workflow_file or config.GITHUB_WORKFLOW_FILE
    url = (
        f"https://api.github.com/repos/{config.GITHUB_REPO}/actions/"
        f"workflows/{workflow_file}/dispatches"
    )
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {config.GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
            json={"ref": config.GITHUB_BRANCH},
            timeout=10,
        )
        if resp.status_code == 204:
            return True, None
        return False, f"GitHub respondeu {resp.status_code}: {resp.text[:200]}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


@app.post("/api/force-refresh")
def api_force_refresh(user: User = Depends(require_user), db: Session = Depends(get_db)):
    # Modo nuvem: não existe robô rodando neste processo (é o GitHub Actions
    # que faz a coleta) -- "forçar atualização" aqui significa "acionar o
    # workflow agora" em vez de esperar o próximo horário do cron. Não dá
    # pra acompanhar progresso em tempo real (são processos/máquinas
    # diferentes), então o dashboard só avisa que foi disparado.
    if config.CLOUD_MODE:
        ok, err = _dispatch_github_workflow()
        if not ok:
            raise HTTPException(status_code=502, detail=f"Falha ao acionar o GitHub Actions: {err}")
        return {"already_running": False, "dispatched_to_github": True}

    total = db.query(Source).filter(Source.enabled.is_(True)).count()
    started = refresh_state.start(total)
    if not started:
        return {"already_running": True, **refresh_state.snapshot()}
    thread = threading.Thread(target=_run_pipeline_in_background, daemon=True)
    thread.start()
    return {"already_running": False, **refresh_state.snapshot()}


# Workflows que o /api/cron-trigger sabe acionar, por `job` (24/07/2026 --
# antes só existia a varredura de notícias). `news` mantém o nome do
# arquivo configurável (`config.GITHUB_WORKFLOW_FILE`, já existia);
# `b3_trades` é fixo porque é novo e não precisa da mesma flexibilidade.
_CRON_JOBS = {
    "news": None,  # None = usa config.GITHUB_WORKFLOW_FILE (default do _dispatch_github_workflow)
    "b3_trades": "b3_trades.yml",
}

# Janela de pregão de renda fixa da B3 pra debêntures/CRI/CRA (10h-16h,
# com folga até 18h pra cobrir negócios de Registro que aparecem um pouco
# depois do fechamento -- ver app/spreads/b3_trades.py). Usado só pra
# evitar acionar o workflow de negócio a negócio fora de hora se o cron
# externo (cron-job.org) disparar 24/7 por engano -- fora da janela o
# `/api/cron-trigger?job=b3_trades` simplesmente não faz nada (200, sem
# acionar o GitHub Actions), não é erro. Brasil não tem mais horário de
# verão desde 2019, então `_BRT` (fuso fixo, já usado no resto do app)
# cobre isso sem sustos.
_B3_MARKET_OPEN_HOUR = 9
_B3_MARKET_CLOSE_HOUR = 18


def _b3_market_aberto_agora() -> bool:
    agora = datetime.now(_BRT)
    if agora.weekday() >= 5:  # sábado/domingo
        return False
    return _B3_MARKET_OPEN_HOUR <= agora.hour < _B3_MARKET_CLOSE_HOUR


@app.post("/api/cron-trigger")
def api_cron_trigger(
    job: str = "news",
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    # Endpoint SEM login -- pensado pra ser chamado por um serviço externo
    # de cron gratuito (cron-job.org), já que o `schedule:` do GitHub
    # Actions não é pontual (17/07/2026: Allan reparou que não batia certo
    # a cada 5 min -- é limitação documentada do próprio GitHub, não bug
    # daqui). Protegido por um segredo simples no header (não por sessão de
    # usuário, porque quem chama não é um navegador logado). Ampliado
    # (24/07/2026) pra aceitar `?job=b3_trades` além do padrão `news` --
    # cada `job` aciona um workflow diferente, ver `_CRON_JOBS`.
    if not config.CRON_SECRET or x_cron_secret != config.CRON_SECRET:
        raise HTTPException(status_code=403, detail="Segredo inválido ou não configurado (CRON_SECRET)")
    if not config.CLOUD_MODE:
        raise HTTPException(status_code=400, detail="Só funciona em modo nuvem (GITHUB_TOKEN/GITHUB_REPO)")
    if job not in _CRON_JOBS:
        raise HTTPException(status_code=400, detail=f"job inválido -- use um de {list(_CRON_JOBS)}")

    if job == "b3_trades" and not _b3_market_aberto_agora():
        return {"dispatched": False, "reason": "fora do horário de pregão (9h-18h, seg-sex)"}

    ok, err = _dispatch_github_workflow(_CRON_JOBS[job])
    if not ok:
        raise HTTPException(status_code=502, detail=f"Falha ao acionar o GitHub Actions: {err}")
    return {"dispatched": True}


@app.get("/api/refresh-status")
def api_refresh_status(user: User | None = Depends(current_user)):
    return refresh_state.snapshot()


@app.get("/api/status")
def api_status(user: User | None = Depends(current_user), db: Session = Depends(get_db)):
    last_run = db.query(RunLog).order_by(RunLog.id.desc()).first()
    if not last_run:
        return {"last_run": None}
    try:
        sources_detail = json.loads(last_run.sources_json or "[]")
    except (json.JSONDecodeError, TypeError):
        sources_detail = []
    return {
        "last_run": {
            "started_at": _iso_utc(last_run.started_at),
            "finished_at": _iso_utc(last_run.finished_at),
            "n_found": last_run.n_found,
            "triggered_by": last_run.triggered_by,
            "sources": sources_detail,
        }
    }


# ---------------------------------------------------------------------------
# Fontes / setores / empresas / keywords
# ---------------------------------------------------------------------------

@app.get("/minha-conta", response_class=HTMLResponse)
def account_page(request: Request, erro: str | None = None, msg: str | None = None, user: User = Depends(require_user)):
    return templates.TemplateResponse(request, "account.html", {"user": user, "erro": erro, "msg": msg})


@app.post("/minha-conta/senha")
def account_change_password(
    current_password: str = Form(...), new_password: str = Form(...),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    try:
        auth.change_password(db, user, current_password=current_password, new_password=new_password)
    except auth.AuthError as e:
        return RedirectResponse(url=f"/minha-conta?erro={e}", status_code=303)
    return RedirectResponse(url="/minha-conta?msg=Senha+atualizada+com+sucesso.", status_code=303)


@app.get("/fontes", response_class=HTMLResponse)
def sources_page(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    # BUG CORRIGIDO (17/07/2026): sem eager loading, o template percorre
    # sector.companies e, pra CADA empresa, company.aliases -- ~17 setores x
    # ~96 empresas vira mais de 100 consultas separadas ao banco. No Postgres
    # hospedado (Vercel + NullPool = conexao nova a cada consulta) isso
    # estourava os 10s de timeout da funcao serverless (504 em /fontes).
    # selectinload agrupa tudo em poucas consultas.
    sectors = (
        db.query(Sector)
        .options(
            selectinload(Sector.companies).selectinload(Company.aliases),
            selectinload(Sector.extra_keywords),
        )
        .order_by(Sector.name)
        .all()
    )
    sources = db.query(Source).order_by(Source.category, Source.name).all()
    return templates.TemplateResponse(
        request, "sources.html", {"user": user, "sectors": sectors, "sources": sources}
    )


@app.post("/fontes/setor/{sector_id}/keyword")
def add_sector_keyword(sector_id: int, keyword: str = Form(...), user: User = Depends(require_user), db: Session = Depends(get_db)):
    # Aceita varios termos de uma vez, separados por ";" (pedido do Allan,
    # 17/07/2026) -- ex.: "saneamento; ANEEL; tarifa de energia". Cada um
    # vira uma SectorKeyword própria (fica salvo no banco, visível pra
    # todo mundo, não some ao reiniciar).
    termos = [t.strip() for t in keyword.split(";")]
    existentes = {k.keyword for k in db.query(SectorKeyword).filter_by(sector_id=sector_id).all()}
    for termo in termos:
        if termo and termo not in existentes:
            db.add(SectorKeyword(sector_id=sector_id, keyword=termo))
            existentes.add(termo)
    db.commit()
    return RedirectResponse(url="/fontes", status_code=303)


@app.post("/fontes/setor")
def add_sector(nome: str = Form(...), user: User = Depends(require_user), db: Session = Depends(get_db)):
    # Cria um setor novo, sem empresa nenhuma ainda -- util pra temas
    # macro/transversais (ex.: "Economia", que so' usa termos de setor tipo
    # "Copom"/"Selic" pra bater noticia, sem estar ligado a uma empresa
    # especifica). Pedido do Allan, 17/07/2026.
    nome = nome.strip()
    if nome:
        ja_existe = db.query(Sector).filter_by(name=nome).first()
        if not ja_existe:
            db.add(Sector(name=nome))
            db.commit()
    return RedirectResponse(url="/fontes", status_code=303)


@app.post("/fontes/setor/{sector_id}/empresa")
def add_company(
    sector_id: int, nome: str = Form(...), analista: str = Form(""),
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    # Antes só dava pra cadastrar empresa importando Setores.xlsx (rodando
    # o seed local) -- pedido do Allan (17/07/2026): adicionar direto pela
    # aba Fontes & Empresas, sem precisar mexer em planilha/script.
    nome = nome.strip()
    analista = analista.strip() or None
    if nome:
        ja_existe = db.query(Company).filter_by(sector_id=sector_id, name=nome).first()
        if not ja_existe:
            db.add(Company(sector_id=sector_id, name=nome, analyst=analista))
            db.commit()
    return RedirectResponse(url="/fontes", status_code=303)


@app.post("/fontes/setor-keyword/{kw_id}/remover")
def remove_sector_keyword(kw_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    kw = db.get(SectorKeyword, kw_id)
    if kw:
        db.delete(kw)
        db.commit()
    return RedirectResponse(url="/fontes", status_code=303)


@app.post("/fontes/empresa/{company_id}/alias")
def add_company_alias(company_id: int, alias: str = Form(...), user: User = Depends(require_user), db: Session = Depends(get_db)):
    from .models import CompanyAlias
    # Mesma ideia do bulk-add de termos de setor: aceita varios aliases
    # separados por ";" (ex.: "VALE3; Vale S.A.; Vale mining").
    aliases = [a.strip() for a in alias.split(";")]
    existentes = {a.alias for a in db.query(CompanyAlias).filter_by(company_id=company_id).all()}
    for al in aliases:
        if al and al not in existentes:
            db.add(CompanyAlias(company_id=company_id, alias=al))
            existentes.add(al)
    db.commit()
    return RedirectResponse(url="/fontes", status_code=303)


@app.post("/fontes/alias/{alias_id}/remover")
def remove_company_alias(alias_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    from .models import CompanyAlias
    a = db.get(CompanyAlias, alias_id)
    if a:
        db.delete(a)
        db.commit()
    return RedirectResponse(url="/fontes", status_code=303)


@app.post("/fontes/fonte/{source_id}/toggle")
def toggle_source(source_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    src = db.get(Source, source_id)
    if src:
        src.enabled = not src.enabled
        db.commit()
    return RedirectResponse(url="/fontes", status_code=303)


# ---------------------------------------------------------------------------
# Admin — usuários e sessões
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    now = datetime.now(timezone.utc)
    sessions = (
        db.query(SessionModel)
        .filter(SessionModel.revoked.is_(False), SessionModel.expires_at >= now)
        .order_by(SessionModel.last_seen_at.desc())
        .all()
    )
    ttl = auth.get_setting_int(db, "session_ttl_minutes", config.DEFAULT_SESSION_TTL_MINUTES)
    # Lista de tickers excluídos da conta de spread (pedido do Allan,
    # 27/07/2026) -- guardada em AppSetting como texto cru, separado por
    # ";" (ver app/spreads/queries.py::tickers_excluidos_spread, que
    # normaliza na leitura -- não precisa normalizar aqui, só mostrar de
    # volta pro Allan exatamente o que ele digitou da última vez).
    tickers_excluidos_row = db.get(AppSetting, spreads_queries.TICKERS_EXCLUIDOS_SETTING_KEY)
    spread_tickers_excluidos = tickers_excluidos_row.value if tickers_excluidos_row else ""
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "user": user, "users": users, "sessions": sessions, "session_ttl_minutes": ttl, "now": now,
            "spread_tickers_excluidos": spread_tickers_excluidos,
        },
    )


@app.post("/admin/usuarios")
def admin_create_user(
    name: str = Form(...), email: str = Form(...), password: str = Form(...),
    role: str = Form("user"), user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    try:
        new_user = auth.register_user(db, name=name, email=email, password=password)
        # `register_user` agora nasce com active=False por padrão (pendente
        # de aprovação -- pedido do Allan, 27/07/2026, ver docstring da
        # função) pensando no cadastro de auto-atendimento em /cadastro.
        # Criado AQUI pelo próprio admin já é "aprovado" na hora -- não faz
        # sentido o Allan aprovar uma conta que ele mesmo acabou de criar.
        new_user.active = True
        new_user.role = role if role in ("admin", "user") else "user"
        db.commit()
    except auth.AuthError:
        pass
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/usuarios/{user_id}/remover")
def admin_remove_user(user_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == user.id:
        return RedirectResponse(url="/admin", status_code=303)  # não se auto-remove
    target = db.get(User, user_id)
    if target:
        db.delete(target)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/usuarios/{user_id}/role")
def admin_toggle_role(user_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target and target.id != user.id:
        target.role = "user" if target.role == "admin" else "admin"
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/usuarios/{user_id}/ativo")
def admin_toggle_active(user_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target and target.id != user.id:
        target.active = not target.active
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/sessao/{session_id}/revogar")
def admin_revoke_session(session_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    sess = db.get(SessionModel, session_id)
    if sess:
        sess.revoked = True
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/configuracoes")
def admin_update_settings(session_ttl_minutes: int = Form(...), user: User = Depends(require_admin), db: Session = Depends(get_db)):
    auth.set_setting(db, "session_ttl_minutes", str(max(5, session_ttl_minutes)))
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


# Lista de tickers excluídos manualmente da conta de spread (pedido do
# Allan, 27/07/2026) -- rota separada da de cima (propósito diferente,
# formulário próprio em templates/admin.html) pra não misturar validação
# de um campo numérico com um campo de texto livre. `queries.py` lê essa
# mesma chave (`tickers_excluidos_spread`) e normaliza cada ticker na
# leitura -- salva aqui exatamente o texto cru que o Allan digitou.
@app.post("/admin/configuracoes/spread-tickers-excluidos")
def admin_update_spread_tickers_excluidos(
    spread_tickers_excluidos: str = Form(""),
    user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    auth.set_setting(db, spreads_queries.TICKERS_EXCLUIDOS_SETTING_KEY, spread_tickers_excluidos.strip())
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)
