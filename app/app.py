"""Aplicação FastAPI: login/cadastro, dashboard de notícias, gerenciamento
de fontes/empresas/keywords e painel administrativo."""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import auth, config, refresh_state, store
from .db import Base, SessionLocal, engine, get_db, run_migrations
from .models import Company, RunLog, Sector, SectorKeyword, Session as SessionModel, Source, User
from .pipeline import run_pipeline
from .scheduler import start_scheduler, trigger_now
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


@app.exception_handler(HTTPException)
async def _redirect_on_303(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)


# ---------------------------------------------------------------------------
# Login / cadastro
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, erro: str | None = None, msg: str | None = None):
    return templates.TemplateResponse(request, "login.html", {"erro": erro, "msg": msg})


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
def signup_form(request: Request, erro: str | None = None, msg: str | None = None):
    return templates.TemplateResponse(request, "signup.html", {"erro": erro, "msg": msg})


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
    msg = "Cadastro criado! Confira seu e-mail para confirmar o acesso."
    return RedirectResponse(url=f"/login?msg={msg}", status_code=303)


@app.get("/confirmar-email")
def confirm_email_route(token: str, db: Session = Depends(get_db)):
    user = auth.confirm_email(db, token)
    if user is None:
        return RedirectResponse(url="/login?erro=Link+de+confirmação+inválido+ou+expirado.", status_code=303)
    return RedirectResponse(url="/login?msg=E-mail+confirmado!+Você+já+pode+entrar.", status_code=303)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    import json as _json
    sectors = db.query(Sector).order_by(Sector.name).all()
    companies = db.query(Company).filter(Company.active.is_(True)).order_by(Company.name).all()
    companies_json = _json.dumps(
        [{"id": c.id, "name": c.name, "sector_id": c.sector_id} for c in companies]
    ).replace("</", "<\\/")  # evita fechar a tag <script> se algum nome contiver "</"
    last_run = db.query(RunLog).order_by(RunLog.id.desc()).first()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user, "sectors": sectors,
            "companies_json": companies_json,
            "window_presets": config.WINDOW_PRESETS, "default_window": config.DEFAULT_WINDOW,
            "scan_interval_minutes": config.SCAN_INTERVAL_MINUTES,
            "last_run": last_run,
        },
    )


@app.get("/api/articles")
def api_articles(
    window: str = "24h",
    sector_id: int | None = None,
    company_id: int | None = None,
    source_domain: str | None = None,
    article_type: str | None = None,
    coverage: str = "minha",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    hours = config.WINDOW_PRESETS.get(window, 24)
    articles = store.list_articles(
        db, window_hours=hours, sector_id=sector_id, company_id=company_id,
        source_domain=source_domain, article_type=article_type, coverage=coverage,
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


def _dispatch_github_workflow() -> tuple[bool, str | None]:
    """Aciona o workflow do GitHub Actions (`scrape.yml`) via
    `workflow_dispatch` -- usado no lugar de rodar o pipeline neste
    processo quando `config.CLOUD_MODE` está ativo (ver `_on_startup`).
    Retorna (sucesso, mensagem_de_erro)."""
    import requests

    url = (
        f"https://api.github.com/repos/{config.GITHUB_REPO}/actions/"
        f"workflows/{config.GITHUB_WORKFLOW_FILE}/dispatches"
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


@app.get("/api/refresh-status")
def api_refresh_status(user: User = Depends(require_user)):
    return refresh_state.snapshot()


@app.get("/api/status")
def api_status(user: User = Depends(require_user), db: Session = Depends(get_db)):
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
    sectors = db.query(Sector).order_by(Sector.name).all()
    sources = db.query(Source).order_by(Source.category, Source.name).all()
    return templates.TemplateResponse(
        request, "sources.html", {"user": user, "sectors": sectors, "sources": sources}
    )


@app.post("/fontes/setor/{sector_id}/keyword")
def add_sector_keyword(sector_id: int, keyword: str = Form(...), user: User = Depends(require_user), db: Session = Depends(get_db)):
    keyword = keyword.strip()
    if keyword:
        exists = db.query(SectorKeyword).filter_by(sector_id=sector_id, keyword=keyword).first()
        if not exists:
            db.add(SectorKeyword(sector_id=sector_id, keyword=keyword))
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
    alias = alias.strip()
    if alias:
        exists = db.query(CompanyAlias).filter_by(company_id=company_id, alias=alias).first()
        if not exists:
            db.add(CompanyAlias(company_id=company_id, alias=alias))
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
    return templates.TemplateResponse(
        request,
        "admin.html",
        {"user": user, "users": users, "sessions": sessions, "session_ttl_minutes": ttl, "now": now},
    )


@app.post("/admin/usuarios")
def admin_create_user(
    name: str = Form(...), email: str = Form(...), password: str = Form(...),
    role: str = Form("user"), user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    try:
        new_user = auth.register_user(db, name=name, email=email, password=password)
        new_user.email_confirmed = True  # criado pelo admin — não precisa confirmar por e-mail
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
