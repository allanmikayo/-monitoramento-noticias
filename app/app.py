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
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from . import auth, config, email_utils, login_codigo, refresh_state, store
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


def _versao_estatica(pasta: str | None = None) -> str:
    """Sufixo de versão pros arquivos de /static (`?v=...`).

    POR QUE EXISTE (11/09/2026). Três vezes num dia só nós olhamos a tela
    depois de um deploy, vimos o comportamento ANTIGO e fomos procurar
    defeito no código -- quando o código estava certo e o navegador é que
    tinha guardado o `.js`/`.css` antigo. O vercel.json manda a borda e o
    navegador guardarem /static por 1 ano (`immutable`), então a ÚNICA
    coisa que força buscar de novo é a URL mudar.

    POR QUE HASH DO CONTEÚDO (21/09/2026). A primeira versão usava o
    `VERCEL_GIT_COMMIT_SHA` e, na falta dele, o mtime dos arquivos. Em
    produção o `?v=` não mudou entre deploys: o HTML novo chegou, mas o
    spreads.js/style.css servidos eram de dias antes (a tela mostrava os
    espaços novos vazios). A variável de sistema não chega à função e, no
    pacote da Vercel, o mtime é fixo -- ou seja, a versão era sempre a
    mesma. O hash do próprio conteúdo não depende de ambiente nenhum: muda
    se, e somente se, algum arquivo de /static mudou.
    """
    import hashlib

    raiz = pasta or os.path.join(BASE_DIR, "static")
    h = hashlib.md5()
    try:
        for dirpath, dirnames, arquivos in os.walk(raiz):
            dirnames.sort()
            for nome in sorted(arquivos):
                caminho = os.path.join(dirpath, nome)
                h.update(os.path.relpath(caminho, raiz).replace(os.sep, "/").encode())
                with open(caminho, "rb") as f:
                    h.update(f.read())
    except OSError:
        # Sem /static legível não há o que versionar -- um valor fixo é
        # melhor do que derrubar o render da página inteira por causa disso.
        return "0"
    return h.hexdigest()[:12]


# Calculado UMA vez, no import: dentro de um mesmo deploy o conteúdo não
# muda, e recalcular por requisição seria ir ao disco à toa.
VERSAO_ESTATICA = _versao_estatica()
templates.env.globals["v"] = VERSAO_ESTATICA
# Spreads, Balcão e Cobertura criam o PRÓPRIO Jinja2Templates. Até 21/09 o
# `v` só existia aqui, então essas páginas saíam com `?v=` VAZIO -- URL
# idêntica a cada deploy, e com o `immutable` de 1 ano do vercel.json o
# navegador nunca buscava o spreads.js/style.css novos. Todos recebem o
# mesmo valor; o teste `test_paginas_renderizam_versao_estatica` guarda.
from . import balcao_routes as _br, cobertura_routes as _cr, spreads_routes as _sr  # noqa: E402

for _t in (_br.templates, _cr.templates, _sr.templates):
    _t.env.globals["v"] = VERSAO_ESTATICA

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
    # DDL NO BOOT SÓ FORA DA VERCEL (20/08/2026).
    #
    # `create_all` confere 29 tabelas uma a uma e `run_migrations` dispara 13
    # ALTERs -- 42 idas e voltas até o Supabase, em TODO cold start. Como a
    # função roda em iad1 (EUA) e o banco está em sa-east-1 (São Paulo), cada
    # ida e volta custa ~200ms: medi 45 SEGUNDOS no primeiro acesso depois de
    # a função dormir, contra ~2,4s nos acessos seguintes.
    #
    # `scripts/init_db.py` existe justamente para fazer esse DDL uma vez, da
    # máquina do Allan, por conexão direta e sem limite de tempo. Ele foi
    # criado em 13/08 para resolver os 504 de boot, mas a chamada aqui nunca
    # foi removida -- então o custo continuava sendo pago a cada cold start.
    #
    # DEPOIS DE MUDAR models.py, rode: python -m scripts.init_db
    if not IS_VERCEL:
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
# MUDOU DE NOVO (13/08/2026): volta a `require_user`. Spreads era público
# desde 27/07; agora a única aba aberta é o Repositório de Relatórios.
app.include_router(register_spreads_routes(require_user))

# Aba "Balcão B3" (20/08/2026) -- volumetria de negociação de DEB/CRI/CRA a
# partir do Boletim Diário do Mercado da B3. Lê `negocios_b3_diario` (o
# agregado, guardado para sempre) nos blocos históricos e `negocios_b3` (o
# bruto, retenção de 5 dias) só na seção "ao vivo". Atrás de login, como
# Notícias e Spreads.
from .balcao_routes import register_balcao_routes  # noqa: E402

app.include_router(register_balcao_routes(require_user))

# Módulo "Repositório de Relatórios" (13/08/2026) -- catálogo dos relatórios
# do Smart tagueados por empresa/setor. É a ÚNICA aba pública do Hub: recebe
# `current_user` (opcional) de propósito. Consultar não exige login; editar
# tag exige role admin, conferido dentro do módulo em `_exige_admin`.
app.include_router(register_cobertura_routes(current_user))

# Aba "Mercado Primário" (23/09/2026) -- ofertas públicas de dívida
# registradas na CVM (Dados Abertos). Atrás de login, como as demais.
from .primario_routes import register_primario_routes  # noqa: E402

app.include_router(register_primario_routes(require_user, templates))

# Registro de uso + painel "Uso do Hub" (22/09/2026) -- ver app/uso.py.
# `current_user` (opcional) no registro porque o Repositório é público.
from .uso_routes import register_uso_routes  # noqa: E402

app.include_router(register_uso_routes(current_user, require_admin, templates))

# A ABA "BANCO DE DADOS" SAIU (11/09/2026, pedido do Allan). Ela existia
# desde 12/08 para consultar e extrair o que está armazenado, com SQL livre
# atrás de `require_admin` e uma lista de barreiras (somente leitura, limite
# de linhas, validação de comando). Desde a migração para o Postgres próprio
# da OCI o Allan tem o DBeaver ligado direto no banco, que faz o mesmo
# melhor e sem manter uma superfície de SQL exposta na web.
#
# Foram removidos junto: app/spreads/banco_routes.py, templates/banco.html,
# static/banco.js e tests/test_banco.py. Nada mais no app importava esses
# arquivos -- a aba era autocontida.


@app.exception_handler(OperationalError)
async def _banco_indisponivel(request: Request, exc: OperationalError):
    """Banco fora do ar vira uma página explicando, não Internal Server Error.

    ERRO REAL (20/08/2026, log da Vercel):

        psycopg.errors.ConnectionFailure: Failed to connect to database:
        authentication did not complete within 15000ms
        [SQL: select pg_catalog.version()]

    Ou seja, falhava ao ABRIR a conexão -- antes de qualquer consulta da
    aplicação, dentro do `current_user`. Como isso sobe de uma dependência
    do FastAPI, todas as rotas caem juntas e o usuário via só "Internal
    Server Error", sem nenhuma pista de que o problema era o Supabase.

    Causa provável: o Disk IO Budget do projeto no Supabase esgotado -- o
    próprio alerta deles avisa que "your instance may become unresponsive".
    """
    logging.getLogger(__name__).error(
        "banco indisponível em %s: %s", request.url.path, exc)
    return HTMLResponse(
        """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
        <title>Banco indisponível</title>
        <link rel="stylesheet" href="/static/style.css"></head><body>
        <div style="max-width:560px;margin:80px auto;padding:0 20px;">
          <h1 style="font-size:1.3rem;">Banco de dados indisponível</h1>
          <p>O site não conseguiu se conectar ao banco. Isso costuma ser
             temporário — recarregue em alguns instantes.</p>
          <p class="muted small">Se persistir, verifique o consumo de
             <b>Disk IO</b> no painel do Supabase: com o orçamento esgotado a
             instância para de aceitar conexões.</p>
          <p><a href="/">Voltar ao início</a></p>
        </div></body></html>""",
        status_code=503,
    )


@app.exception_handler(HTTPException)
async def _redirect_on_303(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)


# ---------------------------------------------------------------------------
# Login / cadastro
# ---------------------------------------------------------------------------

# LOGIN POR CÓDIGO NO E-MAIL (22/09/2026). O TI do Allan pediu para o Hub
# não coletar senha. Três etapas na mesma página /login, escolhidas por
# `?etapa=`: "email" (padrão) -> "codigo" -> entra. "senha" é a porta de
# emergência só do admin (ver `auth.authenticate_admin`). Regras e travas
# em app/login_codigo.py.
COOKIE_LOGIN_EMAIL = "login_email"
COOKIE_LOGIN_NOVO = "login_novo"


def _ip(request: Request) -> str | None:
    # Na Vercel o IP real vem no x-forwarded-for; request.client é o proxy.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def _redir_login(etapa: str = "", **q: str) -> RedirectResponse:
    from urllib.parse import urlencode
    params = {"etapa": etapa} if etapa else {}
    params.update({k: v for k, v in q.items() if v})
    return RedirectResponse(url="/login" + ("?" + urlencode(params) if params else ""), status_code=303)


def _abrir_sessao(request: Request, db: Session, user: User) -> RedirectResponse:
    sess = auth.create_session(db, user, ip=_ip(request), user_agent=request.headers.get("user-agent"))
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, sess.token, httponly=True, samesite="lax",
        secure=IS_VERCEL, max_age=60 * 60 * 24,
    )
    resp.delete_cookie(COOKIE_LOGIN_EMAIL)
    resp.delete_cookie(COOKIE_LOGIN_NOVO)
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_form(
    request: Request, etapa: str = "email", erro: str | None = None, msg: str | None = None,
    user: User | None = Depends(current_user),
):
    email = request.cookies.get(COOKIE_LOGIN_EMAIL, "")
    if etapa == "codigo" and not email:
        etapa = "email"
    if etapa not in ("email", "codigo", "senha"):
        etapa = "email"
    return templates.TemplateResponse(request, "login.html", {
        "erro": erro, "msg": msg, "user": user, "etapa": etapa, "email": email,
        "novo": request.cookies.get(COOKIE_LOGIN_NOVO) == "1",
    })


@app.post("/login/codigo")
def login_pedir_codigo(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    try:
        email_ok = login_codigo.normalizar_email(email)
        codigo = login_codigo.pedir_codigo(db, email_ok, _ip(request))
    except login_codigo.CodigoErro as e:
        return _redir_login("email", erro=str(e))

    msg = f"Enviamos um código para {email_ok}. Confira também a caixa de spam."
    if codigo is not None:
        try:
            email_utils.send_login_code(email_ok, codigo)
        except email_utils.EnvioIndisponivel:
            if not IS_VERCEL and not email_utils.smtp_configurado():
                # Rodando local sem SMTP: mostra o código na tela para dar
                # para testar o fluxo. Nunca acontece na Vercel.
                msg = f"Modo local, sem e-mail configurado. Seu código: {codigo}"
            else:
                return _redir_login("email", erro=(
                    "Não consegui enviar o e-mail agora. Tente de novo em alguns minutos "
                    "ou fale com o administrador."))

    novo = login_codigo.usuario_por_email(db, email_ok) is None
    resp = _redir_login("codigo", msg=msg)
    opts = dict(httponly=True, samesite="lax", secure=IS_VERCEL, max_age=15 * 60)
    resp.set_cookie(COOKIE_LOGIN_EMAIL, email_ok, **opts)
    resp.set_cookie(COOKIE_LOGIN_NOVO, "1" if novo else "0", **opts)
    return resp


@app.post("/login/confirmar")
def login_confirmar(
    request: Request, codigo: str = Form(...), nome: str = Form(""),
    db: Session = Depends(get_db),
):
    email = request.cookies.get(COOKIE_LOGIN_EMAIL, "")
    if not email:
        return _redir_login("email", erro="O código expirou. Digite seu e-mail de novo.")
    try:
        user = login_codigo.confirmar_codigo(db, email, codigo, nome)
    except login_codigo.CodigoErro as e:
        return _redir_login("codigo", erro=str(e))
    except auth.AuthError as e:
        resp = _redir_login("email", msg=str(e))
        resp.delete_cookie(COOKIE_LOGIN_EMAIL)
        resp.delete_cookie(COOKIE_LOGIN_NOVO)
        return resp
    return _abrir_sessao(request, db, user)


@app.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Entrada por senha: só admin (porta de emergência)."""
    try:
        user = auth.authenticate_admin(db, email, password)
    except auth.AuthError as e:
        return _redir_login("senha", erro=str(e))
    return _abrir_sessao(request, db, user)


@app.get("/logout")
def logout(response: RedirectResponse, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE), db: Session = Depends(get_db)):
    if session_token:
        auth.revoke_session(db, session_token)
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# /cadastro SAIU (22/09/2026): com o login por código, o primeiro acesso
# JÁ é o cadastro (a tela pede o nome junto com o código). O endereço
# antigo continua respondendo para não quebrar link salvo.
@app.get("/cadastro")
@app.post("/cadastro")
def signup_antigo():
    return RedirectResponse(url="/login", status_code=303)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

# MUDOU (13/08/2026, pedido do Allan): o dashboard de notícias voltou a
# exigir login. Desde 27/07/2026 ele era público; agora a ÚNICA aba aberta
# é o Repositório de Relatórios -- os relatórios de research são públicos,
# o resto (notícias captadas, spreads, cadastro) é interno e só entra quem
# o Allan aprovar. Ver também `require_user` no /spreads e o menu em
# base.html, que esconde os links de quem não está logado.
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
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
    # Fechado junto com o dashboard (13/08/2026): é o endpoint que devolve
    # as notícias captadas, não faria sentido a página exigir login e a API
    # que a alimenta continuar aberta.
    user: User = Depends(require_user),
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
            "exige_aprovacao": login_codigo.exige_aprovacao(db),
            "smtp_ok": email_utils.smtp_configurado(),
        },
    )


@app.post("/admin/usuarios")
def admin_create_user(
    name: str = Form(...), email: str = Form(...),
    role: str = Form("user"), user: User = Depends(require_admin), db: Session = Depends(get_db),
):
    """Pré-cadastra alguém (22/09/2026: sem senha -- a pessoa entra pelo
    código no e-mail). Criado pelo admin já nasce aprovado."""
    try:
        email_ok = login_codigo.normalizar_email(email)
    except login_codigo.CodigoErro:
        return RedirectResponse(url="/admin", status_code=303)
    if login_codigo.usuario_por_email(db, email_ok) is None and name.strip():
        db.add(User(name=name.strip()[:120], email=email_ok, password_hash="",
                    role=role if role in ("admin", "user") else "user",
                    email_confirmed=True, active=True))
        db.commit()
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
        # Aprovado uma vez = e-mail confirmado. A partir daí, "inativo"
        # passa a significar BLOQUEADO (não recebe mais código) em vez de
        # "pedido pendente" -- ver login_codigo.pedir_codigo.
        if target.active:
            target.email_confirmed = True
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/sessao/{session_id}/revogar")
def admin_revoke_session(session_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    sess = db.get(SessionModel, session_id)
    if sess:
        sess.revoked = True
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/configuracoes/aprovacao")
def admin_toggle_aprovacao(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    atual = login_codigo.exige_aprovacao(db)
    auth.set_setting(db, login_codigo.EXIGE_APROVACAO_KEY, "0" if atual else "1")
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
