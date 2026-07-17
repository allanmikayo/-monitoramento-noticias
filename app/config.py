"""Defaults: fontes monitoradas, janelas de tempo, parâmetros de sessão."""
from __future__ import annotations

import os

# Janela de auto-limpeza do banco (artigos mais antigos que isso são removidos)
CLEANUP_MAX_AGE_HOURS = 24 * 45  # 45 dias — cobre o filtro de "último mês" com folga

# Presets de filtro exibidos no dashboard (em horas)
WINDOW_PRESETS = {
    "24h": 24,
    "5d": 24 * 5,
    "30d": 24 * 30,
}
DEFAULT_WINDOW = "5d"  # comeca mostrando 5 dias -- mais generoso logo no inicio de uso

# Intervalo do scanner automático (minutos)
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "5"))

# ---------------------------------------------------------------------------
# Modo nuvem (17/07/2026): quando hospedado no Vercel, o próprio site NÃO
# roda o robô de coleta (Playwright não funciona bem em função serverless, e
# o agendador em processo não sobrevive entre chamadas). Em vez disso, o
# GitHub Actions roda o robô a cada 5 min (.github/workflows/scrape.yml) e
# grava direto no mesmo banco (Supabase). O site, quando em modo nuvem,
# apenas "aciona" esse workflow no botão "Forçar atualização" em vez de
# rodar o pipeline ele mesmo -- ver app.py `_on_startup`/`api_force_refresh`.
#
# Ativado automaticamente quando GITHUB_TOKEN + GITHUB_REPO estão definidos
# (só existem como variável de ambiente na hospedagem na nuvem -- localmente,
# no computador do Allan, essas variáveis não existem, então o app continua
# rodando o agendador em processo como sempre, sem precisar de nenhuma
# configuração extra local).
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")  # formato "usuario/nome-do-repo"
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_WORKFLOW_FILE = os.getenv("GITHUB_WORKFLOW_FILE", "scrape.yml")
CLOUD_MODE = bool(GITHUB_TOKEN and GITHUB_REPO)

# Sessão de login
DEFAULT_SESSION_TTL_MINUTES = int(os.getenv("SESSION_TTL_MINUTES", "480"))  # 8h

# E-mail (confirmação de cadastro) — se SMTP_HOST não estiver configurado,
# o link de confirmação é apenas logado no console (modo dev).
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "monitoramento@localhost")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8765")

# Link exibido pra TODA notícia de CVM ("Documento CVM" / fato_relevante) --
# o Allan confirmou (17/07/2026) que o link direto pro documento específico
# não abre de forma confiável fora do contexto de navegação do próprio
# RAD, então em vez de arriscar um link quebrado, todo artigo de CVM aponta
# pra página de busca do RAD (o usuário busca lá pelo protocolo/empresa).
CVM_SEARCH_URL = "https://www.rad.cvm.gov.br/ENETWeb/frmConsultaExternaCVM.aspx"

# Primeiro admin criado automaticamente se o banco de usuários estiver vazio
BOOTSTRAP_ADMIN_EMAIL = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "allancruz078@gmail.com")
BOOTSTRAP_ADMIN_NAME = os.getenv("BOOTSTRAP_ADMIN_NAME", "Allan")
BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "troque-esta-senha")

# ---------------------------------------------------------------------------
# Fontes monitoradas — mercado de crédito privado (debêntures, CRI, CRA) Brasil.
# category: rating_agency | regulatory | news
# kind: rss | html | api  (usado pelo pipeline para saber como interpretar o scraper)
# mode: all (aceita tudo, filtra só por empresa) | specific (usa keywords próprias da fonte também)
# ---------------------------------------------------------------------------
KNOWN_SOURCES: list[dict] = [
    # Agências de rating
    {
        "domain": "brazil.ratings.spglobal.com",
        "name": "S&P Global Ratings Brasil",
        "category": "rating_agency",
        "kind": "html",
        "scraper_module": "spglobal",
        "url": "https://brazil.ratings.spglobal.com/ratings/pt/regulatory/press-releases",
        "mode": "all",
        "notes": "Trocado de 'ratings-actions' p/ 'press-releases' (16/07/2026, a pedido do Allan) "
                 "-- pagina mais simples, filtro 'Ultimos 12 Meses' + paginacao confirmados ao vivo.",
    },
    {
        "domain": "moodyslocal.com.br",
        "name": "Moody's Local — Ações de Rating",
        "category": "rating_agency",
        "kind": "html",
        "scraper_module": "moodys_local",
        "url": "https://moodyslocal.com.br/relatorios/acoes-de-rating/",
        "mode": "all",
    },
    {
        "domain": "moodyslocal.com.br",
        "name": "Moody's Local — Relatórios do Emissor",
        "category": "rating_agency",
        "kind": "html",
        "scraper_module": "moodys_local",
        "url": "https://moodyslocal.com.br/relatorios/research/relatorios-do-emissor/",
        "mode": "all",
    },
    {
        "domain": "moodyslocal.com.br",
        "name": "Moody's Local — Relatórios Setoriais",
        "category": "rating_agency",
        "kind": "html",
        "scraper_module": "moodys_local",
        "url": "https://moodyslocal.com.br/relatorios/research/relatorios-setoriais/",
        "mode": "all",
    },
    {
        "domain": "fitchratings.com",
        "name": "Fitch Ratings — Rating Action Commentary (BR)",
        "category": "rating_agency",
        "kind": "html",
        "scraper_module": "fitch",
        "url": (
            "https://www.fitchratings.com/search?dateValue=lastWeek&expanded=racs"
            "&filter.sector=&filter.language=Portuguese&filter.region=&filter.country="
            "&filter.reportType=Rating+Action+Commentary&filter.topic=&viewType=data"
        ),
        "mode": "all",
        "notes": "dateValue=lastWeek (nao lastMonth -- valor confirmado pelo script de referencia do "
                 "Allan; lastMonth nunca foi testado ao vivo e pode nao ser um valor aceito pelo site). "
                 "Listagem via Playwright (paginada), so' titulo/data/link (sem abrir cada artigo).",
    },
    # Reguladores / bolsa
    {
        "domain": "rad.cvm.gov.br",
        "name": "CVM — Fatos Relevantes e Comunicados (RAD)",
        "category": "regulatory",
        "kind": "api",
        "scraper_module": "cvm_rad",
        "url": "https://www.rad.cvm.gov.br/ENETWeb/frmConsultaExternaCVM.aspx",
        "mode": "all",
        "notes": "Playwright pagina a tabela grdDocumentos (ordenada por Data Entrega desc) e filtra "
                 "SOMENTE pelas empresas da cobertura ativa (pedido explicito do Allan, dado o volume "
                 "de arquivamentos do mercado inteiro nesta fonte).",
    },
    {
        "domain": "fnet.bmfbovespa.com.br",
        "name": "B3 Fundos.NET — Comunicados (FIDC/Securitizadoras)",
        "category": "regulatory",
        "kind": "api",
        "scraper_module": "b3_fundosnet",
        "url": "https://fnet.bmfbovespa.com.br/fnet/publico/abrirGerenciadorDocumentosCVM",
        "mode": "all",
        "enabled": False,
        "notes": "Scraper ainda nao implementado (JS/SPA ou busca server-driven) - ver app/sources/b3_fundosnet.py",
    },
    # Imprensa financeira
    {
        "domain": "infomoney.com.br",
        "name": "InfoMoney — Renda Fixa",
        "category": "news",
        "kind": "rss",
        "scraper_module": "infomoney",
        "url": "https://www.infomoney.com.br/tudo-sobre/renda-fixa/feed/",
        "mode": "specific",
    },
    {
        "domain": "moneytimes.com.br",
        "name": "Money Times",
        "category": "news",
        "kind": "rss",
        "scraper_module": "moneytimes",
        "url": "https://www.moneytimes.com.br/feed/",
        "mode": "specific",
    },
    {
        "domain": "uqbar.com.br",
        "name": "Uqbar — Notícias (CRI/CRA/FIDC)",
        "category": "news",
        "kind": "html",
        "scraper_module": "uqbar",
        "url": "https://uqbar.com.br/noticias/",
        "mode": "all",
        "enabled": False,
        "notes": "Scraper ainda nao implementado (JS/SPA ou busca server-driven) - ver app/sources/uqbar.py",
    },
    {
        "domain": "braziljournal.com",
        "name": "Brazil Journal",
        "category": "news",
        "kind": "rss",
        "scraper_module": "braziljournal",
        "url": "https://braziljournal.com/feed/",
        "mode": "specific",
    },
    {
        "domain": "braziljournal.com",
        "name": "Brazil Journal — Infra Journal",
        "category": "news",
        "kind": "rss",
        "scraper_module": "braziljournal",
        "url": "https://braziljournal.com/infra-journal/feed/",
        "mode": "specific",
    },
    {
        "domain": "agenciainfra.com",
        "name": "Agência Infra",
        "category": "news",
        "kind": "rss",
        "scraper_module": "agenciainfra",
        "url": "https://agenciainfra.com/blog/ultimas-noticias/feed/",
        "mode": "specific",
    },
    {
        "domain": "canalenergia.com.br",
        "name": "CanalEnergia",
        "category": "news",
        "kind": "html",
        "scraper_module": "canalenergia",
        "url": "https://www.canalenergia.com.br/noticias",
        "mode": "specific",
        "notes": "Sem RSS publico -- varre a pagina /noticias direto (HTML simples, sem JS).",
    },
    {
        "domain": "megawhat.uol.com.br",
        "name": "MegaWhat",
        "category": "news",
        "kind": "rss",
        "scraper_module": "megawhat",
        "url": "https://megawhat.uol.com.br/feed/",
        "mode": "specific",
        "notes": "URL do feed nao verificada (dominio bloqueado para inspecao automatica aqui) "
                 "-- confira o painel de diagnostico apos a primeira varredura; se vier com 0 "
                 "resultados, me avise que ajusto a URL certa.",
    },
    {
        "domain": "oglobo.globo.com",
        "name": "O Globo",
        "category": "news",
        "kind": "rss",
        "scraper_module": "generic_rss",
        "url": "https://oglobo.globo.com/rss/oglobo",
        "mode": "specific",
        "enabled": False,
        "notes": "URL do feed NAO confirmada (dominio bloqueado para inspecao automatica e nao "
                 "achei a URL certa por busca) -- deixei desabilitada de proposito. Me manda a "
                 "URL do RSS (ou a secao especifica que te interessa, ex. Economia) que eu ligo.",
    },
    {
        "domain": "valor.globo.com",
        "name": "Valor Econômico",
        "category": "news",
        "kind": "rss",
        "scraper_module": "generic_rss",
        "url": "https://valor.globo.com/rss/valor",
        "mode": "specific",
        "enabled": False,
        "notes": "URL do feed NAO confirmada (dominio bloqueado para inspecao automatica; achei "
                 "so o feed do Valor International em ingles, nao o site em portugues). Me manda "
                 "a URL do RSS (ou a secao especifica, ex. Financas/Empresas) que eu ligo.",
    },
    # Pedido do Allan (17/07/2026)
    {
        "domain": "bloomberglinea.com.br",
        "name": "Bloomberg Línea — Negócios",
        "category": "news",
        "kind": "html",
        "scraper_module": "bloomberglinea",
        "url": "https://www.bloomberglinea.com.br/negocios/",
        "mode": "specific",
        "notes": "Sem RSS publico -- pagina renderizada no servidor (Arc Publishing), varre o HTML "
                 "direto igual CanalEnergia. Mesmo scraper_module usado nas 4 secoes cadastradas.",
    },
    {
        "domain": "bloomberglinea.com.br",
        "name": "Bloomberg Línea — Mercados",
        "category": "news",
        "kind": "html",
        "scraper_module": "bloomberglinea",
        "url": "https://www.bloomberglinea.com.br/mercados/",
        "mode": "specific",
    },
    {
        "domain": "bloomberglinea.com.br",
        "name": "Bloomberg Línea — Agro",
        "category": "news",
        "kind": "html",
        "scraper_module": "bloomberglinea",
        "url": "https://www.bloomberglinea.com.br/agro/",
        "mode": "specific",
    },
    {
        "domain": "bloomberglinea.com.br",
        "name": "Bloomberg Línea — Saúde",
        "category": "news",
        "kind": "html",
        "scraper_module": "bloomberglinea",
        "url": "https://www.bloomberglinea.com.br/saude/",
        "mode": "specific",
    },
    {
        "domain": "metroquadrado.com",
        "name": "Metro Quadrado",
        "category": "news",
        "kind": "html",
        "scraper_module": "metroquadrado",
        "url": "https://metroquadrado.com/",
        "mode": "specific",
        "notes": "Mercado imobiliario (Brazil Journal). Sem RSS publico -- home renderizada no "
                 "servidor, varre o HTML direto. Exclui /brands/ (conteudo patrocinado) e /tag/.",
    },
]
