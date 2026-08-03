# CLAUDE.md — Monitoramento de Notícias (Crédito Privado)

> Leia este arquivo inteiro antes de qualquer tarefa nesta pasta.

## O que é este projeto

Dashboard de monitoramento de notícias, ações de rating e fatos relevantes
para um analista sell-side de **credit research** (debêntures, CRI, CRA),
cobrindo 96 emissores em 17 setores (ver `data/Setores.xlsx`). Login por
usuário/senha, cadastro com confirmação por e-mail, painel administrativo
(usuários/sessões), varredura automática a cada 5 minutos + botão de forçar
atualização, filtros por janela de tempo (24h / 5 dias / 1 mês).

Inspirado no projeto **clipinator** (equity research de S&M/P&P/Cimento do
Itaú BBA), mas generalizado: em vez de keywords fixas por fonte, aqui a
cobertura é **setor → empresas → keywords/aliases**, e qualquer fonte pode
mencionar qualquer empresa coberta — o motor de relevância é sempre "essa
notícia menciona uma empresa/termo que cobrimos?", não "essa fonte é sobre
este setor?".

## Stack

- **Backend**: FastAPI + Jinja2 (server-rendered, sem frontend framework)
- **Banco**: SQLAlchemy ORM — hoje SQLite local (`data/credit_monitor.db`),
  mas portátil para Postgres/Supabase só trocando `DATABASE_URL` (ver
  roteiro de migração abaixo). **Não foi usado SQL cru** justamente para
  isso funcionar nos dois bancos sem reescrever queries.
- **Login**: sessão própria (bcrypt + tabela `sessions` com expiração),
  desenhada para depois virar uma camada fina sobre o Supabase Auth.
- **Scheduler**: APScheduler (`BackgroundScheduler`), roda dentro do
  próprio processo do FastAPI — varre tudo a cada `SCAN_INTERVAL_MINUTES`
  (default 5) e uma vez no boot.
- **Scraping**: `requests`/`curl_cffi` + `BeautifulSoup`/`feedparser` para
  fontes que devolvem HTML/RSS pronto no primeiro GET (InfoMoney, Money
  Times). S&P Global e Moody's Local usam Playwright (`fetch_rendered_html`
  em `app/sources/base.py`) porque as duas só montam a tabela de ações de
  rating via JavaScript — um GET comum devolve a página vazia.

## Como rodar localmente

```bash
cd credit_monitor
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env                            # ajuste se quiser (opcional p/ rodar local)
python -m scripts.seed                             # popula setores/empresas/fontes + cria admin
python run.py                                       # -> http://localhost:8765
```

Login inicial: `allancruz078@gmail.com` / senha em `BOOTSTRAP_ADMIN_PASSWORD`
no `.env` (default `troque-esta-senha` — **troque em "Minha conta" no
primeiro acesso**, tem tela pra isso).

Rodar o robô manualmente sem subir o site (útil para debug de scraper):

```bash
python -m app.pipeline
```

## Estrutura

```
app/
  models.py       modelos SQLAlchemy (setores, empresas, aliases, fontes,
                   artigos, usuários, sessões, tokens de e-mail, settings)
  db.py           engine/sessão — troca SQLite↔Postgres via DATABASE_URL
  config.py       fontes monitoradas (KNOWN_SOURCES), janelas de tempo,
                   parâmetros de sessão/e-mail
  filter.py       casamento de keywords (normaliza acento/caixa, \b regex)
  taxonomy.py     monta o índice empresa/setor → keywords a partir do banco
  store.py        upsert de artigos com dedupe por URL normalizada
                   ("mantém o corpo mais longo", igual ao clipinator)
  pipeline.py     orquestra: roda cada fonte habilitada, casa keywords,
                   grava só o que é novo e relevante, registra run_logs
  scheduler.py    APScheduler — varredura automática + trigger_now()
  auth.py         hash de senha, cadastro, confirmação por e-mail, sessões
  email_utils.py  envio de e-mail (ou log do link, se SMTP não configurado)
  app.py          rotas FastAPI (login, dashboard, fontes, admin)
  sources/        um módulo por fonte — todos expõem fetch(url) -> list[RawArticle]
scripts/seed.py   popula o banco a partir de data/Setores.xlsx + config.py
templates/, static/   Jinja2 + CSS/JS vanilla (sem build step)
data/Setores.xlsx     planilha de cobertura (Setor | Companhia | Analista) — fonte de verdade
```

## Fontes monitoradas — status

Todas as fontes usam Playwright (`fetch_rendered_html` ou sessão própria em
`app/sources/base.py`) quando o site é SPA/JS-rendered — GET simples só
funciona para RSS e HTML server-rendered puro.

| Fonte | Categoria | Status |
|---|---|---|
| S&P Global Ratings Brasil | rating_agency | ✅ Playwright, página **Press Releases** (trocada de "ratings-actions" em 17/07/2026 a pedido do Allan) — filtro "Últimos 12 Meses" + paginação por seta, estrutura confirmada ao vivo |
| Moody's Local — Ações de Rating | rating_agency | ✅ Playwright, extrai por classe de coluna exata (`column-rating_action_post_date`/`column-rating_action_title_with_link_to_post`, confirmado ao vivo 17/07/2026), usa o `<a href>` real da matéria |
| Moody's Local — Relatórios do Emissor | rating_agency | ✅ mesmo parser, cai no fallback genérico (classes de coluna diferentes, não confirmadas ao vivo ainda) |
| Moody's Local — Relatórios Setoriais | rating_agency | ✅ idem |
| Fitch Ratings (RAC Portuguese) | rating_agency | ✅ Playwright, só lista (título/data/link) — não abre cada artigo |
| CVM RAD (fatos relevantes) | regulatory | ✅ Playwright, pagina `grdDocumentos` — filtra SÓ empresas da cobertura (única fonte com filtro dentro do scraper, não no pipeline) |
| InfoMoney — Renda Fixa | news | ✅ RSS |
| Money Times | news | ✅ RSS |
| Brazil Journal / Infra Journal | news | ✅ RSS |
| Agência Infra | news | ✅ RSS |
| CanalEnergia | news | ✅ HTML simples (sem JS) |
| MegaWhat | news | ✅ RSS — URL não verificada por mim (domínio bloqueado no sandbox), conferir no diagnóstico |
| O Globo, Valor Econômico | news | ⛔ desabilitado — URL do feed não confirmada, Allan precisa indicar |
| Uqbar — Notícias | news | ⛔ desabilitado — SPA (JS), não priorizado ainda |
| B3 Fundos.NET (CRI/CRA/FIDC) | regulatory | ⛔ desabilitado — busca server-driven, precisa mapear API |

**Fitch e CVM RAD acabaram de ser ativados no código (17/07/2026).**
`scripts/seed.py` nunca sincroniza o campo `enabled` de fontes já
existentes no banco (de propósito — é controlado pela aba "Fontes &
Empresas"). Se o seu banco já tinha essas duas fontes cadastradas como
desabilitadas de antes, **ligue as duas manualmente** em "Fontes &
Empresas" depois de reiniciar — o seed só atualiza URL/notes, não liga
sozinho.

**Importante — leia antes de reportar bug**: os scrapers ✅ foram escritos
com base na estrutura HTML observada (via fetch), mas **não pude testar
contra a internet real dentro do ambiente onde este código foi gerado**
(sandbox com proxy que bloqueia a maioria dos domínios externos). O fluxo
completo (login, cadastro, dedupe, filtros, admin, agendador) foi testado
de ponta a ponta com dados sintéticos e está funcionando. A primeira
execução real (`python run.py` no seu computador) é o teste de verdade dos
scrapers — se algum vier com 0 resultados ou título/data errados, me diga
qual fonte que eu ajusto o parser (provavelmente só precisa de ajuste fino
de seletor CSS, não é um problema estrutural).

## Como funciona a relevância (setor → empresa → keyword)

1. `scripts/seed.py` lê `data/Setores.xlsx` e cria `Sector` + `Company`
   (com `analyst`). Um dicionário `ALIASES` no próprio script adiciona
   tickers/variações para nomes ambíguos (ex.: "Vale" sozinho geraria
   falso-positivo com "vale a pena" — por isso empresa usa nome + aliases).
2. `taxonomy.build_index()` monta, a cada rodada do pipeline, a lista
   completa de keywords (nome de empresa + aliases + termos extras do
   setor) e um mapa `keyword → company_id(s)`.
3. `pipeline._run_source()` roda o `fetch()` de cada fonte habilitada,
   casa título+resumo contra essa lista inteira — **um artigo só é
   guardado se mencionar pelo menos uma empresa/termo coberto**. Isso
   evita que ações de rating de empresas fora da nossa cobertura (ex.:
   milhares de emissores da S&P Brasil) lotem o banco.
4. Empresas casadas ficam associadas ao artigo (`article_company`) —
   é isso que alimenta o filtro por setor/empresa no dashboard.

Para adicionar cobertura: aba "Fontes & Empresas" no site (adicionar
alias/ticker a uma empresa, ou termo de setor) — não precisa mexer em
código nem reiniciar o servidor.

## Autenticação e admin

**MUDOU (27/07/2026)** -- login deixou de ser obrigatório pra tudo. Ver
seção dedicada mais abaixo ("Login virou opcional...") pro desenho
completo. Resumo:

- **Notícias (`/`) e Spreads (`/spreads`) são públicas** -- não precisa
  de login pra ver. Login vira uma opção ("Entrar") no canto superior
  direito do header, só necessário pra **Fontes & Empresas** e
  **Administração**.
- Cadastro (`/cadastro`) cria usuário com `active=False` (**pendente de
  aprovação**) -- sem e-mail de confirmação nenhum. Só entra depois que
  o Allan aprova em `/admin` (botão "Aprovar" na tabela de usuários,
  mesmo mecanismo que já existia pra ativar/desativar).
- Usuários criados pelo admin (`/admin`) já entram aprovados
  (`active=True`) -- não passam pelo estado pendente.
- Sessão expira em `SESSION_TTL_MINUTES` (default 480 = 8h), configurável
  em `/admin`. Painel admin lista sessões ativas (usuário, início, última
  atividade, expiração, IP) com botão "Encerrar".
- `role` (`admin`/`user`) controla quem vê `/admin`. O primeiro admin é
  criado pelo seed a partir de `BOOTSTRAP_ADMIN_EMAIL`. Um usuário comum
  (`role=user`) aprovado já vê "Fontes & Empresas" no header, mas não
  "Administração" (`require_admin` continua exigindo `role=admin`).

## Limitações conhecidas / próximos passos

1. **Uqbar, B3 Fundos.NET** — ainda não implementados; precisam de
   Playwright (SPA) ou engenharia reversa da API interna de busca (B3).
   Padrão a seguir: igual ao `login.py`/`*_scraper.py` do clipinator —
   abrir o Playwright com browser visível, usar `page.on("request")` para
   capturar o endpoint JSON real usado pela busca, replicar com `requests`
   quando possível (mais leve que manter um browser headless rodando).
1b. **CVM RAD — volume de mercado**: `LOOKBACK_DAYS=10` e `MAX_PAGES=20`
   em `app/sources/cvm_rad.py` são um chute conservador (o sandbox onde
   este código foi escrito não conseguia acessar o site real pra medir o
   volume verdadeiro). Se o diagnóstico mostrar "found" muito alto (tipo
   500+) ou a varredura ficar lenta, ou se pedir para reduzir Playwright
   time, reduza `LOOKBACK_DAYS`. Se vier "found=0" com empresas que
   deveriam aparecer, é sinal de que `MAX_PAGES` não chega até elas —
   aumente, ou (melhor) mapear o formulário de busca por empresa do RAD
   (Allan só me mostrou a tabela de resultados, não os campos de busca) e
   trocar a estratégia de "paginar tudo e filtrar" por "buscar direto por
   empresa" — bem mais eficiente pra um scan de 5 min.
1c. **Fitch — só listagem**: `app/sources/fitch.py` não abre cada artigo
   pra extrair a tabela de rating detalhada (o script de referência em
   `RatingsAction/FitchRatings` faz isso, via `ReactTable`) — só título,
   data e link, suficiente pra um feed de notícias. Se Allan quiser o
   detalhe completo (rating anterior/novo, outlook) direto no card, dá
   pra reaproveitar a lógica de extração JS daquele script.
2. **Sem tradução automática** (clipinator traduzia Valor/Estadão/El
   Financiero para inglês) — não pedido aqui, mas o padrão do clipinator
   (`_translate_to_english` em `clipping.py`, Claude Haiku) pode ser
   reaproveitado se um dia fizer sentido.
3. **Sem exportação para Word/e-mail** (o clipinator gera um clipping
   diário em .docx) — não pedido nesta primeira fase; avaliar depois.
4. **Fontes pagas (Valor, Estadão, Broadcast, Uqbar PRO)** — combinamos
   começar só com fontes abertas. Para adicionar depois, reaproveitar o
   padrão de login do clipinator (`login_manager.py`, Playwright +
   `*_state.json` salvo em disco).

## Roteiro de migração para a nuvem (gratuito) — IMPLEMENTADO (17/07/2026)

Allan escolheu o "Caminho 1" depois de eu pesquisar o estado atual (2026)
de Vercel/Railway/Render/Fly/Supabase/GitHub Actions ao vivo (preços e
free tiers mudam com frequência, não confiar em conhecimento antigo sobre
isso). Achados que definiram a arquitetura:

- **Vercel free (Hobby)**: função serverless com timeout DURO de 10s e sem
  processo persistente entre chamadas -- não dá pra rodar o agendador em
  processo nem o Playwright (que sozinho já passa de 10s por fonte) lá.
  Serve bem, isso sim, pra hospedar só o SITE (paginas + leituras rápidas
  no banco), que é pra isso que ele é usado aqui.
- **Railway**: não é mais de graça de verdade (crédito único de 30 dias,
  depois plano free com só 0,5GB RAM -- não aguenta Playwright).
- **Render free**: dorme depois de 15 min sem acesso e apaga o Postgres
  free depois de 30 dias -- não usamos.
- **Fly.io**: não tem mais free tier desde 2024.
- **GitHub Actions**: minutos ILIMITADOS de graça em repositório
  **público**, cron mínimo de 5 em 5 minutos (bate exato com o que a
  gente precisa), roda Playwright numa VM Ubuntu de verdade sem problema.
  É quem faz a coleta agora.
- **Supabase**: Postgres free real (500MB), só pausa depois de 7 dias
  **sem nenhum acesso** -- não é problema com o site sendo usado.

**Decisão importante que SIMPLIFICOU a migração**: em vez de trocar pro
Supabase Auth (like o rascunho antigo deste roteiro sugeria), mantivemos
o `app/auth.py` (bcrypt + sessão própria) exatamente como estava --
ele já é 100% SQLAlchemy ORM puro, sem nenhum SQL específico de SQLite,
então funciona em Postgres sem NENHUMA mudança de código. Isso evitou a
parte mais arriscada/trabalhosa do plano original. Supabase aqui é usado
só como Postgres gerenciado gratuito, não como serviço de Auth.

### Arquitetura final

- **Site** (login, dashboard, admin) continua sendo o MESMO FastAPI +
  Jinja2 de sempre, sem reescrita pra JS/estático -- só hospedado no
  Vercel em vez de rodar no computador do Allan. Vercel detecta
  `app/app.py` automaticamente como o entrypoint (`app = FastAPI(...)`,
  já era assim).
- **Robô de coleta** (Playwright, todas as fontes) roda via
  `.github/workflows/scrape.yml`, cron a cada 5 min + `workflow_dispatch`
  (pro botão "Forçar atualização"), chamando `scripts/run_once.py` (novo
  -- roda `run_pipeline()` uma vez e sai, diferente do
  `app/scheduler.py` que só existe rodando localmente).
- **Banco**: Postgres do Supabase, via `DATABASE_URL`. Tanto o site
  (Vercel) quanto o robô (GitHub Actions) escrevem/leem do MESMO banco.
- **"Forçar atualização" na nuvem**: como o site (Vercel) e o robô
  (GitHub Actions) são processos totalmente separados, o botão não roda
  mais o pipeline no mesmo processo -- ele aciona o workflow do GitHub via
  API (`_dispatch_github_workflow()` em `app.py`, usa `GITHUB_TOKEN` +
  `GITHUB_REPO` como variável de ambiente do Vercel) e não dá pra
  acompanhar progresso em tempo real (processos/máquinas diferentes) --
  o dashboard só avisa que foi disparado e reconsulta depois de 90s.
  Só ativa nesse modo quando `GITHUB_TOKEN`+`GITHUB_REPO` existem
  (`config.CLOUD_MODE`); localmente continua exatamente como sempre foi.

### Mudanças de código feitas pra viabilizar isso

- `app/db.py`: `DATABASE_URL` já era suportado, mas tinha 2 bugs que só
  apareceriam rodando em Postgres/serverless: (1) `DATA_DIR.mkdir(...)`
  rodava incondicionalmente na importação do módulo -- quebra na hora no
  Vercel, cujo sistema de arquivos do deploy é read-only fora de `/tmp`;
  agora só cria/usa a pasta `data/` quando de fato é SQLite. (2) adicionado
  `NullPool` pro engine quando não é SQLite -- em serverless, várias
  funções chamam o banco em paralelo/curto prazo, e empilhar o pool
  próprio do SQLAlchemy por cima do PgBouncer (connection pooler) do
  Supabase pode conflitar; `NullPool` deixa o PgBouncer cuidar disso.
  **Usar a connection string "pooler"/"transaction mode" do Supabase (não
  a "direct connection") no `DATABASE_URL` do Vercel.** (3) Descoberto na
  primeira tentativa real de seed contra o Supabase (17/07/2026): o
  "Transaction pooler" quebra com `psycopg.errors.DuplicatePreparedStatement`
  se o psycopg (v3) tentar preparar comandos do lado do servidor (padrão
  dele) -- cada transação pode cair numa conexão física diferente atrás do
  PgBouncer, e o nome do prepared statement colide entre elas. Corrigido
  com `connect_args={"prepare_threshold": None}` pro Postgres (desativa
  prepared statements do lado do servidor -- é a recomendação oficial do
  psycopg pra uso com pooler em modo transação).
- `app/db.py` `run_migrations()`: `BOOLEAN DEFAULT 1` -> `DEFAULT TRUE`
  (Postgres não aceita inteiro cru como default de coluna booleana).
- `requirements.txt`: adicionado `psycopg[binary]` (driver Postgres).
- `app/app.py`: cookie de sessão ganhou `secure=IS_VERCEL` (só exige
  HTTPS quando de fato hospedado -- não quebra o uso local via
  `http://localhost`). Agendador em processo só inicia quando
  `not config.CLOUD_MODE`.
- `.gitignore`: `data/Setores.xlsx` (lista de cobertura -- não sobe pro
  repositório público), `data/debug_*.{html,png}`, `.vercel/`.

### O que falta (ações do Allan, fora do código)

1. Criar projeto no Supabase, pegar a connection string "pooler".
2. Rodar `python -m scripts.seed` UMA vez localmente, com `.env` apontando
   temporariamente pro Supabase (isso cria o schema + importa
   `Setores.xlsx` + fontes + o admin inicial) -- só depois disso o site
   hospedado tem dado pra mostrar.
3. Definir uma senha de verdade em `BOOTSTRAP_ADMIN_PASSWORD` nesse mesmo
   `.env` local ANTES de rodar o seed (o padrão "troque-esta-senha" não
   pode ir pro banco que vai ficar exposto na internet).
4. Criar repositório no GitHub, subir o código (repo público, pra minutos
   ilimitados de Actions), configurar o secret `DATABASE_URL`.
5. Criar projeto no Vercel a partir do repositório, configurar as
   variáveis de ambiente (`DATABASE_URL`, `GITHUB_TOKEN`, `GITHUB_REPO`,
   `BOOTSTRAP_ADMIN_PASSWORD` etc.).
6. Gerar um GitHub Personal Access Token com escopo `actions:write` (ou
   fine-grained equivalente) pro Vercel poder acionar o workflow.

### Cards da aba Emissores: taxa → spread (bps) — 27/07/2026

Allan pediu pra trocar os dois cards do topo da aba Emissores (criados na
rodada anterior, ver seção logo abaixo) de TAXA pra SPREAD em bps --
mesma unidade do resto do dashboard inteiro:
- **Card Anbima**: `anbima_spread`/`anbima_spread_3m` agora usam
  `DebentureSpread.spread` (já calculado, em bps) em vez de
  `taxa_indicativa` -- mudança pequena, o dado já existia.
- **Card B3**: pedido mais complexo -- "IPCA+ incentivadas tem que
  buscar a NTNB referência, para CDI não precisa de cálculos adicionais,
  igual você já aprendeu a fazer na atualização de spreads". O negócio a
  negócio da B3 só tem a `taxa` crua, não o spread -- precisa calcular,
  reaproveitando EXATAMENTE a fórmula de `fetch.fetch_spreads`:
  - CDI + Tradicionais: `spread = taxa * 100`.
  - IPCA + Incentivadas: `spread = ((1+taxa/100)/(1+taxa_ntnb/100)-1)*10000`.
    **CORRIGIDO (27/07/2026, mesmo dia, ver seção "Bug real: spread B3
    negativo" logo abaixo)**: a primeira versão usava sempre a NTN-B de
    vértice mais curto do dia pra `taxa_ntnb`, já que o negócio a negócio
    da B3 não traz `referencia_ntnb` por papel como o boletim da Anbima
    traz. Allan apontou que isso está errado -- agora `taxa_ntnb` usa a
    referência ESPECÍFICA que a Anbima associa a cada papel
    (`Debenture.referencia_ntnb`, persistida a partir do boletim diário),
    e só cai no vértice mais curto quando o papel não tem essa referência
    própria (mesma regra que `fetch_spreads` já usa pro card Anbima --
    não inventamos fórmula nova, só paramos de pular essa etapa no lado
    B3).
  - Indexador fora dessas duas classes (`classe == "Outros"`) ou ticker
    sem `Debenture` cadastrado (CRI/CRA, que não têm cadastro próprio
    ainda): `spread` fica `None`.

  **Decisão de arquitetura**: calcular o spread NA HORA DE GRAVAR (
  `b3_trades.compute_trade_spreads`, chamado de
  `persist.save_negocios_b3`), não em tempo real quando o dashboard
  carrega -- evita bater na API da Anbima (OAuth + rate limit) a cada
  carregamento de página; o card só faz uma agregação simples sobre um
  valor já calculado e guardado (`NegocioB3.spread`, coluna nova). Isso
  significa que **o job de negócio a negócio (`scripts/fetch_b3_trades.py`
  / `b3_trades.yml`) agora também depende de `ANBIMA_CLIENT_ID`/
  `ANBIMA_CLIENT_SECRET`** -- adicionado como env do step no workflow
  (mesmos secrets já pedidos pro `spreads_daily.yml`, não é segredo novo
  se o Allan já configurou pra rodada de deploy anterior).

  Allan também pediu uma referência discreta pro card B3, no mesmo
  espírito da "média 3M" do card Anbima, só que numa janela mais curta
  (negócio da B3 é intradiário, 3 meses não faria sentido): **spread
  ponderado das últimas 24 HORAS CORRIDAS** (não posições de pregão) --
  `b3_spread_24h` em `queries.emissor_taxas`, calculado combinando
  `data_negocio`+`horario` num datetime de verdade em `America/Sao_Paulo`
  (fuso fixo, sem horário de verão) e comparando contra `agora - 24h`.

**Migração**: `NegocioB3.spread` é coluna nova -- `ALTER TABLE
negocios_b3 ADD COLUMN spread FLOAT` em `run_migrations()`. Como o
dedupe por `trade_code` nunca REESCREVE linha já gravada, todo negócio
capturado ANTES dessa correção fica com `spread=NULL` pra sempre a
menos que alguém preencha manualmente -- por isso
`scripts/backfill_b3_trade_spreads.py` (novo, roda uma vez, sem
argumento): recalcula `spread` de todo `NegocioB3` com `spread IS NULL`,
reaproveitando a mesma `compute_trade_spreads` (idempotente, seguro
rodar de novo).

Testado contra uma cópia do banco real (11.761 negócios já capturados,
11.758 sem spread) com a chamada de NTN-B mockada: `compute_trade_spreads`
bate a fórmula manual pros dois indexadores e devolve `None` certinho
pra "Outros"/ticker sem cadastro/taxa nula; a janela de 24h testada com
3 negócios sintéticos (agora, 20h atrás, 30h atrás) inclui certinho os
dois primeiros e exclui o terceiro; o backfill rodou em ~0,5s fazendo só
2 chamadas de NTN-B (uma por DATA distinta, cacheado -- não uma por
negócio) e preencheu 4.101 dos 11.758 (o resto é legitimamente CRI/CRA/
"Outros", sem o que calcular).

### Bug real: negócio sumindo da captura B3 — 27/07/2026

Allan filtrou "Energisa Sergipe" na aba Emissores e um negócio de hoje
(ENSEB4, `trade_code` "#1009631632", 10:29:46) não aparecia -- nem na
tabela de negociações nem no card de taxa B3. Descartei bug de
matching/código primeiro (ENSEB4 bate certinho com o `Debenture` da
Energisa Sergipe, `_normalize_codigo` não era o problema). Reproduzi
direto no navegador contra a B3 pra investigar a fundo:

- A consulta do dia CORRENTE (`/Trade/{hoje}/{hoje}/...`) às vezes
  devolve HTTP 200 com JSON válido só que `table.values: []` e
  `table.pageCount: 0` -- **não é erro de rede** (por isso o retry que já
  existia em `_fetch_page`, que só cobria exceção/corpo HTTP vazio, não
  pegava esse caso). Reproduzido com **5 tentativas seguidas** espaçadas
  de 0,5s todas vazias, recuperando sozinho depois de ~10s (voltou a
  devolver 1000 negócios na página 1 normalmente).
- Confirmei que o negócio do Energisa Sergipe estava lá o tempo todo --
  assim que a consulta voltou a responder, achei ele de primeira na
  página 2 fazendo a mesma varredura que `fetch_trades` faz.
- Ou seja: não é dado perdido/atrasado do lado da B3, é uma instabilidade
  passageira da consulta do dia corrente especificamente (histórico de
  dias passados nunca falhou nos testes) que o nosso código não estava
  tratando -- se uma rodada do agendador (a cada 15 min) batesse
  exatamente nessa janela, perdia o dia inteiro (ou parava cedo demais na
  página que travou) sem nenhum erro visível no log.

**Corrigido em `app/spreads/b3_trades.py`** com duas mudanças:
1. `_fetch_page_sem_vazio()` -- camada de retry NOVA, separada do retry
   de rede que já existia, que insiste (`EMPTY_RETRY_ATTEMPTS = 4`,
   backoff de 3s/6s/9s) quando uma página vem com `values: []` sem
   nenhum erro HTTP. Só desiste depois de esgotar as tentativas (aí sim
   pode ser fim de semana/feriado de verdade, ou uma instabilidade mais
   longa que o normal).
2. `fetch_trades()` não derruba a captura inteira mais se uma página
   falhar de vez (rede exaurida OU continuar vazia) -- loga um erro e
   devolve o que já tinha coletado até ali em vez de propagar a exceção e
   perder tudo (parcial é sempre melhor que nada; a próxima rodada do
   agendador, 15 min depois, refaz o dia inteiro do zero e tende a
   preencher o que ficou faltando).

Testado com 3 cenários sintéticos (mock de `requests.post`): página 1
vazia 2x seguido de recuperação (acha o negócio normalmente), página 1
vazia em TODAS as tentativas (fim de semana de verdade -- não trava,
devolve lista vazia), e falha de rede persistente na página 2 depois da
página 1 ter funcionado (devolve o resultado parcial da página 1 em vez
de perder tudo).

### Cards de taxa no topo da aba Emissores — 24/07/2026

Allan pediu dois cards acima da tabela de tickers: taxa Anbima mais
recente do papel (ponderada por Estoque se o emissor tiver vários
tickers, com uma média dos últimos 3 meses em destaque menor ao lado) e a
taxa que está sendo negociada na B3 (ponderada por taxa × volume). Nova
função `queries.emissor_taxas(db, nomes_emissor, classe)` +
`GET /api/spreads/emissor/taxas` + os dois `kpi-card` no topo de
`#emissor-conteudo` (reaproveita o CSS `.kpi-row`/`.kpi-card`/`.kpi-tag`
que já existia na Visão Geral).

**Bug real pego ANTES de mandar pro Allan** (testando com a Coelba, que
tem papel de indexador fora do normal): a primeira versão ponderava
`taxa_indicativa` de TODOS os tickers do emissor juntos, sem filtrar por
`classe`. `taxa_indicativa` não é a mesma unidade pra todo indexador --
"DI PERCENTUAL" guarda um número tipo 104,7 (% do CDI), enquanto
"PREFIXADO"/"IPCA +"/"CDI +" guardam uma taxa normal tipo 0,7-15. Esses
indexadores fora do padrão caem em `classe == "Outros"` (`compute_classe`)
-- misturados com o resto, geravam uma "taxa média" sem sentido nenhum
(9,41% pra Coelba, puxada por um papel de 104,7%). Corrigido exigindo
`classe` como parâmetro obrigatório e filtrando os tickers por ela --
MESMA regra de nunca misturar "IPCA + Incentivadas"/"CDI + Tradicionais"/
"Outros" que já vale pro resto do dashboard inteiro, agora estendida
também pros negócios da B3 (taxa de um CDI+ e de um IPCA+ não são
comparáveis entre si tampouco). Os cards agora reagem ao filtro de classe
já existente na aba (`#emissor-classe-tabs`) -- igual o gráfico de spread
já fazia; só a tabela de tickers continua mostrando todos os tickers
juntos (comportamento antigo, intencional, é só listagem informativa).

Testado contra o banco real (cópia) nas duas classes da Coelba
separadamente -- números batem com o esperado (IPCA+ ~8,4%, CDI+ ~0,86%
de spread) depois do fix.

### Cache da NTN-B de referência (evitar bater na Anbima a cada 15min) — 27/07/2026

Allan notou que o card B3 (seção acima) faz `compute_trade_spreads` buscar
a NTN-B na Anbima toda vez que roda -- e como o job de negócio a negócio
roda de 15 em 15 min durante o pregão inteiro (~30-40 ciclos/dia), isso é
~30-40 chamadas OAuth+API por dia só pra um valor que "não vai mudar ao
longo do dia" (a taxa indicativa da NTN-B é publicada uma vez por dia).
Pediu pra cachear.

- **`NtnbReferencia`** (`app/models.py`) -- tabela nova, chave primária
  `data` (Date), guarda `min_ntnb`/`min_venc` (vértice mais curto do dia,
  usado como FALLBACK -- ver correção abaixo), `curva_json` (a curva
  inteira, `{vencimento: taxa}`, ampliado no mesmo dia) e `captured_at`.
  Tabela nova = `Base.metadata.create_all` cria sozinho; `curva_json` foi
  ADICIONADO depois (coluna nova numa tabela que já existia nesse mesmo
  dia) -- por isso tem entrada em `run_migrations()`.
- **`fetch.fetch_ntnb_curve(dt)`** -- extraído do meio de `fetch_spreads`
  (que já buscava a curva de NTN-B pra calcular spread IPCA+ sem
  `referencia_ntnb` própria) pra virar uma função só, reaproveitada em
  dois lugares em vez de duas cópias da mesma lógica. `fetch_spreads`
  agora devolve `(rows, ntnb_rates, min_ntnb, min_venc)` -- **mudança de
  assinatura**, o único chamador (`scripts/fetch_debenture_spreads.py`)
  foi atualizado junto.
- **`persist.cache_ntnb_referencia(db, dt, ntnb_rates, min_ntnb, min_venc)`**
  -- chamado logo depois de `persist_day` no job diário de spreads
  (`scripts/fetch_debenture_spreads.py`, que já roda 1x/dia às 21h BRT,
  ver seção de deploy abaixo), reaproveitando o que `fetch_spreads` já
  calculou na mesma chamada -- **zero requisição extra** à Anbima só pra
  popular o cache.
- **`b3_trades._get_ntnb_curve(db, dt)`** -- ponto de leitura do cache:
  olha `NtnbReferencia` primeiro, só bate na Anbima ao vivo (e já grava o
  resultado pro próximo uso) se ainda não tiver cache pra aquele dia --
  ex. antes do job diário rodar às 21h, ou primeira vez que o dia aparece
  num negócio da B3.

Efeito prático: na maioria dos dias, a partir das 21h (quando o job diário
já rodou e cacheou a curva do dia), o job de negócio a negócio
(`b3_trades.yml`, 15 em 15 min) **não bate mais na Anbima nenhuma vez** --
só lê o cache. `ANBIMA_CLIENT_ID`/`ANBIMA_CLIENT_SECRET` continuam
configurados nesse workflow como rede de segurança (fallback ao vivo pra
cache-miss), mas o uso deve cair a quase zero.

#### Bug real: spread B3 negativo/sem sentido — ASER12, 27/07/2026 (mesmo dia)

Allan testou o card B3 depois do cache acima e reportou spread de
**−47,5 bps** pra ASER12 (Águas do Sertão), estranho pra um papel de
crédito privado. Passo a passo do que a fórmula original estava fazendo:
taxa negociada 12,04% contra a NTN-B de **vértice mais curto do dia**
(12,58%, vencimento 15/08/2026 -- só ~3 semanas depois do negócio).
Allan apontou o erro de raiz: **a referência de NTN-B não é sempre a mais
curta** -- tem que checar, pra CADA papel, a `referencia_ntnb` que a
própria Anbima já manda no boletim de debêntures (mesmo campo que
`fetch_spreads` sempre usou pro card Anbima); só cair no vértice mais
curto quando esse papel não tiver essa referência própria.

O bug: o negócio a negócio da B3 não traz `referencia_ntnb` por papel
(só a Anbima traz isso, no boletim de debêntures) -- e a primeira versão
de `compute_trade_spreads` não tinha de onde puxar essa informação, então
sempre caía direto no fallback (vértice mais curto), que é o comportamento
CORRETO só quando não existe referência própria -- e NTN-B de vértice bem
curto perto do vencimento tem taxa tipicamente distorcida (iliquidez,
efeito "pull to par"), o que explica o número sem sentido.

**Correção**: `Debenture.referencia_ntnb` (coluna nova) passa a guardar a
referência que a Anbima associa a cada papel, persistida a cada captura
diária (`persist.persist_day`, igual `indexador`/`classe`) -- assim
`compute_trade_spreads` consegue consultar essa referência por ticker
mesmo sem o negócio da B3 trazer o dado. `_get_ntnb_curve` (acima) agora
cacheia a CURVA INTEIRA (não só o vértice mais curto) pra permitir essa
consulta por vencimento específico. `compute_trade_spreads`: pra cada
negócio IPCA+, busca `referencia_ntnb` do ticker; se existir E estiver na
curva do dia, usa essa taxa; senão cai no vértice mais curto (fallback,
comportamento antigo preservado só pros casos sem referência própria).

Testado contra cópia do banco real, com o ASER12 de verdade e a mesma
curva de NTN-B do caso reportado (vértice mais curto 12,58%/15-08-2026
distorcido, vs. uma referência de prazo mais longo simulada em 7,10%):
com `referencia_ntnb` cadastrada, spread vira positivo e plausível (~462
bps, contra os −47 bps errados); sem `referencia_ntnb` (papel hipotético),
cai certinho no fallback e reproduz o número antigo (confirma que o
fallback não quebrou); segunda chamada no mesmo dia reaproveita a curva
cacheada (0 chamadas extras à API). `scripts/fetch_debenture_spreads.py`
testado fim-a-fim com a nova 4-tupla, gravando `referencia_ntnb` no
cadastro e a curva inteira no cache na mesma passada.

#### Bug real (2): backfill não recalcula negócio que JÁ tinha spread errado — 27/07/2026 (mesmo dia)

Allan aplicou a correção acima e rodou `fetch_debenture_spreads` +
`backfill_b3_trade_spreads` de novo -- só 4 de 12245 negócios ganharam
spread, muito abaixo do esperado. Causa raiz: `backfill_b3_trade_spreads`
só processa negócio com `spread IS NULL`
(`db.query(NegocioB3).filter(NegocioB3.spread.is_(None))`). Todo negócio
IPCA+ que Allan já tinha rodado backfill ANTES dessa correção (ver
diagnóstico do task #42, mais abaixo) ficou com o `spread` ERRADO da
fórmula antiga (vértice mais curto) já gravado -- e como não é mais
`NULL`, o backfill normal nunca mais toca nesses registros. Confirmado
contra a cópia do banco real: ASER12 continuava com −47,3 bps mesmo
depois da correção + backfill.

**Correção**: nova flag `--recompute-ipca` em
`scripts/backfill_b3_trade_spreads.py` -- reseta `spread=NULL` só nos
negócios cujo ticker é classe "IPCA + Incentivadas" (não mexe em CDI+,
que nunca teve esse bug -- a fórmula `taxa*100` não depende de NTN-B) e
então roda o preenchimento normal, agora usando a referência certa.
Rodar UMA VEZ só, logo depois de aplicar a correção acima:

```
python -m scripts.backfill_b3_trade_spreads --recompute-ipca
```

Testado contra cópia do banco real: reseta os ~2800 negócios IPCA+ já
preenchidos, recalcula ASER12 pra spread positivo (confirmado diferente
do valor antigo errado), e confirma que um negócio CDI+ de controle
(já com spread) NÃO é alterado pelo reset.

Depois de rodar essa flag uma vez, o fluxo normal (`backfill_b3_trade_spreads`
sem flag, ou o próprio job de 15 em 15 min via `save_negocios_b3`)
continua funcionando do jeito de sempre -- a flag é só pra essa correção
pontual, não precisa virar rotina.

**Diagnóstico do card "Negócios sem spread calculado" (Allan reportou,
27/07/2026)**: card B3 mostrando "—" mesmo com a tabela de negociações
exibindo dados. Investigando a cópia do banco real: 0 de ~12 mil
`NegocioB3` tinham `spread` preenchido, incluindo a captura mais recente
do próprio dia -- ou seja, **não era bug novo**, era o código de spread
(seção "Cards da aba Emissores: taxa → spread" acima) ainda não ter sido
aplicado (app não reiniciado) nem o backfill rodado. Resolve reiniciando
o app e rodando `python -m scripts.backfill_b3_trade_spreads` -- mesmos
passos de sempre, agora cobrindo cache + correção de referência também
(não precisa repetir várias vezes por causa de cada correção separada).

### Deploy do módulo Spreads (spreads + negócio a negócio B3) — 24/07/2026

Até aqui (rodada 1-4 do módulo Spreads, ver seção própria mais abaixo) tudo
rodava só LOCAL, no computador do Allan (agendador em processo, ver
`app/scheduler.py`) — nunca tinha ido pro Vercel/Supabase/GitHub Actions.
Allan pediu pra subir pro dashboard oficial online, com:
- **Spreads de debêntures**: atualização 1x por dia, às 21h (horário de
  Brasília).
- **Negócio a negócio B3** (DEB/CRI/CRA): a cada 15 min, só enquanto o
  mercado está aberto.

**Dois workflows novos** (mesmo padrão do `scrape.yml` já existente):
- `.github/workflows/spreads_daily.yml` — roda
  `python -m scripts.fetch_debenture_spreads` (sem `--start`, captura só o
  último dia útil publicado). `schedule: cron: "0 0 * * *"` (00h UTC =
  21h BRT, Brasil não tem mais horário de verão desde 2019 -- fuso fixo,
  sem ajuste sazonal no cron). Só isso já basta: 1x/dia não precisa da
  mesma pontualidade que o negócio a negócio, o atraso documentado do
  `schedule:` nativo do GitHub em horário de pico (minutos, não horas) não
  importa aqui.
- `.github/workflows/b3_trades.yml` — roda `python -m scripts.fetch_b3_trades`
  (sem argumentos, captura só hoje). Precisa de verdade dos 15 em 15 min
  -- mesma limitação de precisão do `schedule:` nativo que já motivou o
  relay via cron-job.org pra notícias (17/07/2026), então o `schedule:`
  aqui é só FALLBACK (1x/hora, 9h-18h BRT, seg-sex) e quem dispara de
  verdade é o mesmo mecanismo de relay externo, ampliado:

**`/api/cron-trigger` ganhou um parâmetro `job`** (`app/app.py`,
`_CRON_JOBS`): `?job=news` (default, mantém compatibilidade com a
configuração já existente no cron-job.org) aciona `scrape.yml`;
`?job=b3_trades` aciona `b3_trades.yml`. `_dispatch_github_workflow()`
foi generalizada pra aceitar qualquer arquivo de workflow (antes só
disparava `config.GITHUB_WORKFLOW_FILE`). O job `b3_trades` tem uma
checagem extra server-side (`_b3_market_aberto_agora()`, fuso
`America/Sao_Paulo` fixo): fora de 9h-18h em dia útil, o endpoint devolve
`{"dispatched": false, "reason": "..."}` sem acionar o GitHub Actions --
proteção contra desperdiçar minutos do Actions/bater na B3 à toa se o
cron externo disparar fora de hora (folga de 1h antes/depois do pregão
oficial de 10h-16h pra cobrir negócios de Registro tardios, mesma lógica
de `app/spreads/b3_trades.py`). `spreads_daily` NÃO passa por esse relay
-- só `news` e `b3_trades` (`_CRON_JOBS`).

**Segredos NOVOS que faltam no GitHub** (Settings → Secrets → Actions do
repositório) -- `DATABASE_URL` já existia (usado por `scrape.yml`), esses
dois são específicos do módulo Spreads e NUNCA foram configurados porque
o módulo nunca rodou na nuvem antes:
- `ANBIMA_CLIENT_ID`
- `ANBIMA_CLIENT_SECRET`

**Configuração nova no cron-job.org** (o mesmo serviço que já dispara
`?job=news` a cada 5 min) -- duplicar aquele job, apontando pra
`POST https://<url-do-vercel>/api/cron-trigger?job=b3_trades`, mesmo
header `X-Cron-Secret`, intervalo de 15 min. Pode rodar 24/7 no
cron-job.org sem se preocupar em restringir horário lá -- o guard de
horário já vive no servidor (`_b3_market_aberto_agora`). `spreads_daily`
NÃO precisa de nenhuma entrada nova no cron-job.org (roda só pelo
`schedule:` nativo do workflow).

**IMPORTANTE -- primeiro deploy do módulo inteiro, não só desta rodada**:
nenhum arquivo do módulo Spreads (`app/spreads/`, `app/spreads_routes.py`,
`templates/spreads.html`, `static/spreads.js`, os scripts de captura, os
models `Debenture`/`DebentureSpread`/`NegocioB3`) tinha sido commitado no
git ainda -- `git status` mostrou tudo como untracked/modified na hora de
preparar esse deploy. Ou seja, isso não é "subir só as mudanças de hoje",
é a primeira vez que o módulo inteiro vai pro Supabase/Vercel. Duas
consequências:
1. O banco Postgres do Supabase começa **sem nenhum spread/negócio
   histórico** -- só o `DATABASE_URL` local (SQLite) tem os ~2 anos de
   backfill que o Allan já rodou. **Decisão tomada (27/07/2026)**: o
   Supabase NÃO recebe o backfill completo de 2 anos -- só os últimos 3
   meses, pra começar mais leve (ver seção "Backfill de 3 meses pro site
   online" mais abaixo pro comando exato).
2. `data/` aparece inteiro como untracked no `git status` -- o
   `.gitignore` cobre `data/*.db`/`.xlsx`/`debug_*`, mas tem outros
   arquivos soltos ali (exports de mapeamento de rating, log) que NÃO
   deveriam ir pro repositório. **Não dar `git add data/`** -- adicionar
   só os arquivos/pastas específicos do deploy (ver checklist que mandei
   no chat).

**Por que eu (Claude) não rodei `git add`/`commit`/`push` sozinho**: essa
pasta é montada via OneDrive, e o sandbox onde rodo já teve um incidente
de `disk I/O error` tentando ESCREVER no `.db` real por esse mesmo mount
(ver seção de negócio a negócio B3 acima) -- o `.git/index.lock` já
aparece com "Operation not permitted" só de rodar `git status` por aqui,
sinal de que o mount não sustenta o locking que o git precisa pra
escrever com segurança. Risco real de deixar o repositório do Allan num
estado ruim. Além disso, não tenho credencial pra dar push no GitHub dele
mesmo que quisesse. Preparei todo o código; quem roda `git add`/`commit`/
`push` é o Allan, do computador dele mesmo (comandos exatos mandados no
chat).

### Botão "Detalhes" — dado granular (Código+Data) + export CSV — 27/07/2026

Allan pediu um jeito de ver o dado cru por trás dos gráficos da aba
Visão Geral: "uma aba com o menor nível de dados que o gráfico mostra" --
colunas Ticker, Taxa, % PU Par, PU, Data Referência, Indexador, Deb
Incentivada, Spread, Estoque, Duration (o registro cru de
`DebentureSpread`+`Debenture`, sem agregação nenhuma), com filtro de
classe (IPCA+/CDI+/**Todos** -- novo, só faz sentido aqui já que é
listagem e não gráfico) e export CSV/Excel dos dados filtrados.

**Pedido original era um filtro de "data até"** (histórico inteiro até
uma data, podendo passar de meio milhão de linhas -- o banco tem 569 mil
`DebentureSpread` acumuladas). Allan simplificou no mesmo dia pra **um
dia só** ("pode deixar apenas o filtro de data apenas para um dia").
Isso elimina de vez a preocupação de escala: um dia tem no máximo ~1700
linhas (total de debêntures cadastradas), então não precisa de
paginação nem de streaming especial -- a mesma função serve a tela E o
CSV.

- **`queries.detalhes_rows(db, classe, data)`** -- `classe=""` (Todos,
  sentinela que NÃO faz parte de `CLASSES`) devolve todas as classes,
  inclusive "Outros"; `data=None` usa `detalhes_latest_date` (dia mais
  recente disponível pra essa classe). Devolve `{rows, data}` -- o
  `data` ecoado de volta é usado pelo front-end pra preencher o campo de
  data automaticamente no primeiro carregamento (sem isso o Allan veria
  o campo vazio mesmo com dado na tela).
- **`/api/spreads/detalhes`** (JSON, pra tela) e **`/api/spreads/detalhes/export`**
  (CSV via `StreamingResponse`, mesmo filtro) -- rotas separadas só pra
  o export forçar download de arquivo (`Content-Disposition: attachment`)
  em vez de JSON. CSV com BOM UTF-8 na frente (senão o Excel BR lê
  acento errado) e `;` como separador (Excel BR abre certo sem passar
  por "Dados > Texto em colunas" -- `,` exigiria isso).
- Botão "Detalhes" na aba Visão Geral (`templates/spreads.html`), abre
  um painel (`#detalhes-wrap`, mesmo padrão visual do `#drilldown-wrap`
  já existente) com tabs de classe próprias, um `<input type="date">` e
  o botão "Exportar CSV" (um `<a href>` direto pra rota de export --
  download tratado pelo próprio navegador, sem passar dado nenhum por
  JS/Blob).

Testado contra cópia do banco real: classe=Todos sem data usa o dia mais
recente (1277 linhas); classe=IPCA+/CDI+ filtra certo (subconjunto,
indexador bate em 100% das linhas da página); classe inválida e data em
formato errado devolvem 400; dia sem publicação nenhuma devolve
`rows=[]` sem quebrar; export CSV bate exatamente com o JSON equivalente
(mesma contagem), BOM e header corretos.

### Base de spread: excluir papel não precificado + lista manual de exclusão (Administração) — 27/07/2026

Allan notou (testando o botão "Detalhes", seção acima) que papel sem
`taxa_indicativa` publicada pela Anbima no dia (não precificado)
continuava aparecendo na base e entrando na conta de spread. Pediu dois
ajustes:

1. **Papel sem spread calculado nunca deve aparecer em nenhuma conta/base.**
   A maioria das funções agregadas de `queries.py` já filtrava
   `DebentureSpread.spread.isnot(None)` (`kpi_summary`,
   `movement_distribution`, `emissor_series`, `emissor_taxas`) -- mas
   `detalhes_rows`/`detalhes_latest_date` (o botão "Detalhes", literalmente
   "sem agregação nenhuma" por design) NÃO filtravam, e `movers` só
   descartava DEPOIS de já ter buscado (mesmo resultado final, mas
   inconsistente). Agora todo mundo filtra `spread.isnot(None)` de forma
   consistente, incluindo `_weighted_avg_duration` (duration média do KPI
   também não deve contar papel sem spread).
2. **"Na base quero apenas IPCA + Incentivadas, CDI+ Tradicionais e
   todos (os dois)"** -- `classe=""` ("Todos", só existe no botão
   "Detalhes") antes devolvia TODAS as classes cadastradas, inclusive
   "Outros" (indexador fora do padrão IPCA+/CDI+, ex. PREFIXADO, DI
   PERCENTUAL). Agora `classe=""` filtra explicitamente
   `Debenture.classe.in_(CLASSES)` -- nunca inclui "Outros". "Outros"
   nunca apareceu em gráfico nenhum do resto do dashboard (sempre exigiu
   uma classe específica das duas válidas), só vazava no "Todos" do
   Detalhes.

**Lista de tickers excluídos manualmente** (segundo pedido, mesma
mensagem): aba Administração ganhou um campo de texto (separado por
`;`, ex. `AAAA11;BBBB22`) pra Allan tirar tickers específicos da conta de
spread à mão -- útil pra papel com dado errático/ruim conhecido, sem
precisar esperar a Anbima corrigir ou remover o cadastro inteiro.

- Guardado em `AppSetting` (chave-valor genérico já usado pra outras
  configs simples do admin, ex. `session_ttl_minutes`) sob a chave
  `queries.TICKERS_EXCLUIDOS_SETTING_KEY = "spread_tickers_excluidos"`
  -- não criou tabela nova, reaproveitou o padrão existente
  (`auth.set_setting`/`db.get(AppSetting, key)`).
- `queries.tickers_excluidos_spread(db)` lê e normaliza cada ticker com a
  MESMA função que cruza código de ativo em `fetch.py`
  (`_normalize_codigo` -- maiúscula, sem espaço/caractere invisível) pra
  não depender do Allan digitar com a pontuação exata do cadastro.
  Salvo cru no banco (só `.strip()` nas pontas), normalizado só na
  leitura -- assim o campo do formulário sempre mostra de volta
  exatamente o que o Allan digitou da última vez.
- Aplicado em TODAS as funções que entram na "conta de spread":
  `kpi_summary` (+ `_weighted_avg_duration`), `time_series` (modo
  agregado), `movers`, `movement_distribution`, `emissor_series`,
  `emissor_taxas` (afeta os dois lados, Anbima E B3, já que ambos
  derivam da mesma lista `codigos`), `detalhes_rows`. NÃO aplicado em
  `emissor_tickers`/`emissor_trades` (tabela de tickers/negociações da
  aba Emissores -- listagem informativa/cadastro, não é "conta de
  spread").
- Rota nova `POST /admin/configuracoes/spread-tickers-excluidos`
  (separada do form de `session_ttl_minutes` -- propósito diferente,
  validação diferente).

Testado contra cópia do banco real: papel sem spread (22 IPCA+ e 23
CDI+ num mesmo dia) confirmado ausente de `detalhes_rows` E de
`kpi_summary.n_ativos` (bate com contagem manual filtrada); classe=Todos
nunca inclui as 96 debêntures cadastradas como "Outros"; excluir um
ticker via `AppSetting` faz ele sumir de `detalhes_rows`, `kpi_summary`
e `emissor_series` ao mesmo tempo, e limpar a exclusão restaura o total
original; a rota POST grava o valor cru e `tickers_excluidos_spread`
normaliza certo na leitura.

### Bug real: CSV do Detalhes com número ilegível no Excel/Sheets BR — 27/07/2026

Allan reportou números tipo "8.567.994.822,9" ao abrir o export CSV no
Google Sheets. Causa: o CSV escrevia os floats no formato cru do Python
(`str(8567.9948229...)`, ponto como decimal) -- mas Excel/Sheets
configurado em pt-BR espera vírgula decimal e ponto como separador de
milhar; ao importar um campo tipo "8567.99", a planilha tenta reencaixar
no padrão BR e cola os dígitos como se o ponto fosse milhar, virando um
número gigante sem sentido.

**Correção**: `_fmt_num_br(v, casas)` em `app/spreads_routes.py` --
arredonda pro número de casas fixo por coluna (Taxa/%PUPar: 2, PU: 4,
Spread/Duration: 1/2, Estoque: 1) e formata no padrão BR (`f"{v:,.Nf}"`
gera padrão US "1,234.5"; `.translate(str.maketrans(",.", ".,"))` troca
vírgula↔ponto num mapeamento simultâneo -- NÃO dois `.replace()`
sequenciais, que se pisariam). Aplicado nas 6 colunas numéricas do
export (Ticker/Data/Indexador/Incentivada continuam texto puro).

Testado contra cópia do banco real: 612 linhas do export, todas batendo
com regex de formato BR válido (`-?\d{1,3}(\.\d{3})*,\d+`) -- inclusive
número com milhar (`1.075,6808`) e negativo (`-38,4`).

### Bug real: card "SPREAD MÉDIO" não era ponderado por Estoque — 27/07/2026

Allan reparou que o card "SPREAD MÉDIO" da Visão Geral mostrava 46,3 bps
pra "IPCA + Incentivadas" em 24/07/2026, e perguntou explicitamente se o
cálculo seguia a metodologia certa: spread de cada ativo na data ×
Estoque do ativo, soma tudo (`Σ spread·estoque`), soma todo o Estoque
(`Σ estoque`), divide o primeiro pelo segundo. Conferindo o código,
`kpi_summary` (`app/spreads/queries.py`) usava `AVG(spread)` -- média
simples, cada ticker com peso igual -- enquanto o resto do dashboard
inteiro (`_weighted_avg_duration`, `emissor_series`, `emissor_taxas`)
já usava média ponderada por Estoque desde antes. Esse card era o único
lugar que tinha ficado pra trás nessa convenção.

**Correção**: nova função `_weighted_avg_spread(db, classe, data,
excluidos)` em `app/spreads/queries.py`, estrutura idêntica a
`_weighted_avg_duration` (mesmo fallback: se NENHUM ticker da
classe/data tiver Estoque cruzado, cai pra média simples e sinaliza via
flag). `kpi_summary` agora chama essa função tanto pra `media_hoje`
quanto pra `media_anterior` (a variação também fica ponderada-vs-ponderada,
não só o valor do dia). Resposta da API ganhou o campo
`spread_medio_fallback` (mesmo padrão de `duration_ponderada_fallback`),
e o card na Visão Geral (`templates/spreads.html` / `static/spreads.js`)
ganhou uma tag "pond. estoque"/"sem estoque" ao lado do rótulo, igual à
que já existia no card de Duration.

Testado contra cópia do banco real: "IPCA + Incentivadas" em 24/07/2026
(612 papéis, 610 com Estoque cruzado) -- média simples (bug) dava 46,3
bps, batendo exatamente com o valor que o Allan reportou; média
ponderada por Estoque (correta) dá **36,4 bps**. "CDI + Tradicionais" no
mesmo dia rodou limpo (133,4 bps, sem fallback).

### Campo de "data analisada" na Visão Geral — 27/07/2026

Allan pediu um campo de data na Visão Geral pra escolher a data
analisada (por padrão continua sempre olhando a última data disponível,
como sempre foi). Ficou no espaço vazio da `filters-bar`, ao lado das
abas de base de comparação.

**Implementação**: `kpi_summary`, `movers` e `movement_distribution`
(`app/spreads/queries.py`) ganharam parâmetro opcional
`data_referencia: date | None`. Sem ele, "hoje" continua sendo
`dates_desc[0]` (mais recente) -- comportamento de sempre, zero mudança
pra quem não mexe no campo. Com ele, nova função `_resolve_hoje` acha a
data disponível mais próxima **pra trás** da escolhida (ex.: Allan
escolhe um sábado sem boletim -- cai no último dia útil anterior com
dado, sem devolver vazio à toa); nova `_index_from` re-acha a posição de
"hoje" na lista de datas com dado pra andar N posições a partir dali
(antes disso, "hoje" era sempre índice 0 e dava pra usar
`_date_n_back` direto). `movement_distribution` ganhou a mesma lógica
pra ancorar os `n_snapshots` a partir da data escolhida em vez de sempre
da mais recente.

Rotas (`app/spreads_routes.py`) ganharam parâmetro `data` (AAAA-MM-DD,
reaproveitando `_parse_data` que já existia pro botão Detalhes) em
`/api/spreads/summary`, `/movers` e `/movement-distribution`. O gráfico
"Evolução do Spread Médio" (linha de tendência) NÃO usa esse campo --
continua mostrando o histórico inteiro de qualquer forma, não faz
sentido "recortar" uma linha de tendência numa data.

Front-end: `<input type="date" id="visao-data">` em `templates/spreads.html`,
plugado em `static/spreads.js` (recarrega KPI/movers/distribuição no
`change`, `max` travado na última data disponível quando o campo está
vazio).

Testado contra cópia do banco real: sem data (comportamento de sempre),
com uma data exata que tem boletim, com uma data sem boletim (cai pro
dia útil anterior), e com uma data anterior a todo o histórico (devolve
vazio sem erro) -- todos batendo com o esperado, incluindo `movers` e
`movement_distribution` ancorados na mesma data resolvida do KPI.

**Dois bugs de borda pegos em revisão de código, corrigidos no mesmo
dia**: (1) `movers()` -- quando `hoje` resolve pra data MAIS ANTIGA do
histórico (ex. Allan escolhe justamente o primeiro dia capturado), o
fallback `dates_desc[-1]` (usado quando não há histórico suficiente pra
completar a base de comparação inteira) virava a PRÓPRIA `hoje` --
comparando a data contra ela mesma, dando 0 bps de variação pra todo
ativo silenciosamente errado em vez de "sem dado suficiente". Corrigido
com um `!= hoje` antes de usar esse fallback. (2)
`movement_distribution()` -- quando `data_referencia` é anterior a TODO
o histórico, `_resolve_hoje` devolve `None` (igual em `kpi_summary`/
`movers`), mas aqui caía silenciosamente pro índice 0 (mostrava a data
mais RECENTE em vez de vazio) -- inconsistente com os outros dois
cards/gráficos da mesma tela, que ficariam vazios enquanto esse mostrava
"hoje". Corrigido pra devolver `[]` nesse caso, igual aos outros.
Testado com a data mais antiga real do histórico (2024-07-23, 504 datas
no banco) e com uma data 30 dias antes dela.

### Backfill de 3 meses pro site online (em vez dos ~2 anos do local) — 27/07/2026

Retomando a "decisão pendente" da seção de deploy acima (o Postgres do
Supabase ainda não recebeu nenhum backfill de spreads): Allan decidiu
que o site online **não** precisa do histórico completo de ~2 anos que
ele já rodou localmente -- só os últimos 3 meses, pra começar mais leve.
Isso não exigiu mudança de código -- `scripts/fetch_debenture_spreads.py`
já aceita `--start`/`--end`, e todo o resto do dashboard (KPIs,
comparações WoW/MoM/etc.) já lida bem com histórico curto (comparações
que pedirem mais posições atrás do que existe, tipo SoS/YoY logo no
início, simplesmente devolvem "sem dado suficiente pra comparar" em vez
de erro).

**Comando pro Allan rodar** (uma vez, apontando `DATABASE_URL` pro
Postgres do Supabase em vez do SQLite local -- ver variável de ambiente
no `.env`/configuração do Vercel):

```
python -m scripts.fetch_debenture_spreads --start 2026-04-27
```

(3 meses antes de 27/07/2026. Se rodar depois dessa data, ajustar
`--start` pra 3 meses antes do dia em que rodar de verdade.) Sem
`--end`, vai até o último dia útil já publicado na Anbima. Dali em
diante, a captura diária de produção (`spreads_daily.yml`, 21h BRT)
mantém a base sempre atualizada sozinha -- só esse backfill inicial
precisa ser rodado à mão.

### Login virou opcional: Notícias e Spreads públicas, aprovação manual — 27/07/2026

Até aqui **todo** o dashboard exigia login -- `require_user` era
dependência padrão de virtualmente toda rota, e sem sessão válida
`app.py` redirecionava pra `/login` antes de mostrar qualquer coisa
(inclusive a Visão Geral de notícias e a aba Spreads). Allan pediu pra
inverter isso: "retire a página de login. Quero que ele fique como
opção num menu superior direito pra fazer login apenas quem quiser.
Esse login precisa ser aprovado por mim, não precisa de e-mail de
confirmação. Esse login dá acesso a aba fontes e empresas e a aba
administração."

**Modelo novo**:
- **Público, sem login**: `/` (Notícias) e `/spreads` (+ todas as APIs
  que essas páginas usam: `/api/articles`, `/api/refresh-status`,
  `/api/status`, e TODAS as rotas em `app/spreads_routes.py`).
- **Continua exigindo login**: `/fontes` (Fontes & Empresas),
  `/minha-conta`, e `/api/force-refresh` (ação que dispara o robô de
  varredura no GitHub Actions -- deixada atrás de login de propósito,
  pra não virar superfície de abuso público).
- **Continua exigindo `role=admin`**: `/admin` e todas as ações dele
  (inalterado).

**Implementação**:
- `app/app.py`: `dashboard`, `api_articles`, `api_refresh_status`,
  `api_status` trocaram `user: User = Depends(require_user)` por
  `user: User | None = Depends(current_user)` (não redireciona mais --
  só devolve `user=None` quando não há sessão). O router de Spreads
  (`register_spreads_routes`) agora é registrado com `current_user` em
  vez de `require_user` -- os handlers de `spreads_routes.py` nunca
  liam `user.alguma_coisa` (só usavam como gate), então aceitar `None`
  ali é seguro (conferido antes de trocar).
- `templates/base.html`: o `<header>` inteiro era condicionado a
  `{% if user %}` (por isso login parecia "obrigatório" -- sem sessão,
  nem o header aparecia). Agora o header sempre renderiza; só os links
  de "Fontes & Empresas" (`{% if user %}`) e "Administração"
  (`{% if user and user.role == 'admin' %}`) continuam condicionais.
  Canto superior direito: usuário logado vê nome + "Sair" (como antes);
  anônimo vê um link "Entrar" -- essa é a "opção no menu superior
  direito" que o Allan pediu.
- **Bug real pego no teste**: `templates/dashboard.html` também escondeu
  o botão "Forçar atualização" pra anônimo (`{% if user %}`), mas
  `static/app.js` acessava `refreshBtn.disabled`/`.textContent` direto
  em ~10 pontos e registrava `refreshBtn.addEventListener(...)` sem
  checar se o elemento existia -- pra visitante anônimo (`refreshBtn ===
  null`) isso jogava `TypeError` já na inicialização da IIFE, travando a
  página inteira (nem `loadArticles()` rodava). Corrigido com um helper
  `setRefreshBtnState(disabled, text)` que não faz nada se o botão não
  existe, e `if (refreshBtn)` nos dois pontos que checavam
  `.disabled`/registravam o listener direto.
- `app/auth.py::register_user`: sem confirmação por e-mail -- novo
  cadastro nasce `active=False` (reaproveita o MESMO campo que o admin
  já usava pra ativar/desativar usuário, ver `admin_toggle_active`) em
  vez de `email_confirmed=False`. `email_confirmed` passou a ser sempre
  `True` (campo mantido só por compatibilidade de schema, não bloqueia
  mais nada). `authenticate()` barra login de conta com `active=False`
  com mensagem "pendente de aprovação". Rota `/confirmar-email` e a
  função `confirm_email` foram removidas (fluxo não existe mais);
  `email_utils.send_confirmation_email` ficou sem uso (não removido,
  só parou de ser chamado).
- `admin_create_user` (rota `/admin/usuarios`, POST): como
  `register_user` agora nasce `active=False` por padrão, um usuário
  criado PELO PRÓPRIO Allan direto no painel precisa ser marcado
  `active=True` explicitamente depois de chamar `register_user` -- senão
  o próprio Allan teria que "aprovar" um usuário que ele acabou de criar
  com as próprias mãos.
- `templates/admin.html`: coluna "Status" simplificada (não depende mais
  de `email_confirmed`) -- mostra "pendente de aprovação / inativo" ou
  "ativo"; botão que era "Ativar" virou "Aprovar" (mesma ação/rota,
  `/admin/usuarios/{id}/ativo`, só o rótulo mudou pra deixar claro que é
  isso que aprova um cadastro novo).

Testado (sem TestClient contra `app.app` direto -- mesmo cuidado de
sempre com o lifespan/scheduler, ver seção de sandbox safety rules --
registrando os MESMOS objetos de função de `app.app` num `FastAPI()`
novo e vazio): `current_user`/`require_user`/`require_admin` isolados;
`GET /` anônimo → 200 com "Entrar" no header e sem os links/botão
restritos; `GET /fontes` e `GET /admin` anônimo → 303 pro `/login`
(inalterado); `GET /spreads` e `GET /api/spreads/summary` anônimo → 200;
`GET /` logado como admin → 200 com header completo (Fontes,
Administração, nome, Sair, botão de refresh); cadastro novo nasce
`active=False`, `authenticate()` barra até aprovar, funciona normal
depois de `active=True`.

### Bug real: mais lugares calculando spread com média simples em vez de ponderada — 27/07/2026

Depois da correção do card "SPREAD MÉDIO" (mesmo dia, seção acima),
Allan comparou o card contra o gráfico "Evolução do Spread Médio" e
achou a MESMA inconsistência lá: "você não está calculando da maneira
correta (igual o card). Garanta que em todas as visualizações de
cálculo de spread ele está sendo calculado da maneira correta,
inclusive nos cálculos de composição da base por nível de
abertura/fechamento." Achou por comparação visual -- o gráfico e o card
mostravam números diferentes pro mesmo dia, mesma classe.

**`time_series()` (gráfico "Evolução do Spread Médio", `app/spreads/queries.py`)**
-- usava `AVG(spread)` (SQL puro) igual ao bug original do card, só que
pra CADA dia do histórico inteiro, não só o mais recente. Reescrito pra
agrupar em Python (`itertools.groupby` sobre as linhas cruas ordenadas
por data) e aplicar a mesma ponderação por Estoque com fallback pra
média simples (padrão `_weighted_avg_spread`) -- feito em Python (não
SQL) porque já precisávamos das linhas cruas mesmo assim pra calcular a
**mediana** (pedido no mesmo request: "Aqui você pode inserir a mediana
dos spreads também" -- `statistics.median`, sem peso, é o valor "típico"
por definição, não faz sentido ponderar mediana por Estoque). Novo
campo `spread_mediano` por data; `static/spreads.js::loadSeriesChart()`
ganhou uma segunda linha tracejada cinza no mesmo gráfico. Testado: o
último ponto da série bate exatamente com `kpi_summary` (36,4 bps,
24/07/2026), contra 46,3 bps da média simples antiga -- e a mediana
revelou algo notável: **-3,3 bps** no mesmo dia, bem abaixo da média
ponderada de 36,4 -- ou seja, a maioria dos papéis individuais negocia
com spread bem menor que a média, que é puxada pra cima por poucos
papéis de Estoque grande e spread alto. Vale mencionar pro Allan.

**`movement_distribution()`** ("Evolução da Variação de Spreads (% da
base de ativos)") -- não calculava uma MÉDIA de spread (classifica cada
ticker num bucket de variação: `< -10bps`, `-10 a 0`, `0 a 10`, `>
10bps`), mas o jeito de agregar tinha o mesmo problema de fundo: cada
bucket somava CONTAGEM de tickers (1 ticker = 1 voto), não Estoque --
ou seja, um papel gigante que abriu 50bps pesava exatamente igual a um
papel pequeno que fechou 5bps. Reescrito pra pesar cada ticker pelo seu
Estoque na data "hoje" do snapshot (exclui da ponderação quem não tem
Estoque cruzado naquele dia, igual ao resto do dashboard; cai pra
contagem simples só se NENHUM ticker do snapshot tiver Estoque). `%` de
cada faixa agora representa "% do Estoque da base que abriu/fechou X
bps", não "% dos tickers". `n_ativos` continua sendo a contagem de
tickers (não mudou, só a base do `%`). Testado: percentuais de cada
snapshot somam ~100% e os dois snapshots testados batem com a
reconstrução manual ponderada.

### Card "SPREAD NEGOCIADO (B3)": janela de 24h trocada por 7 dias — 27/07/2026

Allan pediu: "No spreads negociado você poderia colocar a média da
última semana, e não apenas 24h." Campo `b3_spread_24h` de
`emissor_taxas` virou `b3_spread_7d` (`app/spreads/queries.py`) --
mesma lógica de ponderação por VOLUME dos negócios individuais, só
trocando a janela móvel de `timedelta(hours=24)` pra
`timedelta(days=7)`. `static/spreads.js` atualizado pro novo nome de
campo e rótulo "Média 7d" (era "Média 24h"). Justificativa: numa janela
de 24h a maioria dos emissores não tem negócio nenhum (mercado
secundário de debênture não é líquido todo dia pra todo papel), então o
número saía vazio (`—`) quase sempre; 7 dias dá amostra bem maior sem
perder o sentido de "recente" (o card principal `b3_spread`, sem
janela, já cobre "o dia mais recente que teve negócio" pra isso).

### Tabela de negócio a negócio (B3): taxa com 4 casas decimais — 27/07/2026

Pedido direto do Allan. `static/spreads.js::loadEmissorNegociacoes()` --
coluna "Taxa" da tabela "Últimas negociações (B3)" (aba Emissores)
mudou de `maximumFractionDigits: 2` pra
`minimumFractionDigits: 4, maximumFractionDigits: 4` (fixa em 4 casas,
não só um teto).

### Mediana revertida do gráfico "Evolução do Spread Médio" — 27/07/2026

A mediana foi adicionada no mesmo dia a pedido do próprio Allan (ver
seção acima) e removida no dia seguinte -- "não gostei". Revertido só a
parte da mediana: `time_series()` (`app/spreads/queries.py`) voltou a
devolver só `spread_medio`/`n_ativos` por data (tirado `spread_mediano`
e o import de `statistics`, agora sem uso), mantendo a correção de
ponderação por Estoque (essa sim ficou -- era um bug real, não uma
preferência). `static/spreads.js::loadSeriesChart()` voltou a ter um
único dataset no gráfico.

### Ranking B3 vs. Anbima por emissor — tela inicial da aba Emissores — 27/07/2026

Allan reparou que a tela da aba Emissores, antes de selecionar algum
emissor, só mostrava uma mensagem vazia ("Busque e selecione..."). Pediu
uma tabela ali: "Emissor, Taxa Anbima na data selecionada e Taxa B3
(média ponderada da última semana, com base na data Anbima) e uma
coluna de variação. De um lado o top 15 diferenças positivas e do outro
o top 15 negativas."

**Nova função `emissor_ranking_diferencas(db, classe, top_n=15)`**
(`app/spreads/queries.py`) -- roda pra TODOS os emissores da classe de
uma vez (não um por vez como `emissor_taxas`, que existia só pra
emissor(es) já selecionado(s)):
- Lado Anbima: última linha com spread de cada ticker (subquery de
  `MAX(data)` agrupado por código + join), ponderado por Estoque por
  emissor -- mesma metodologia de sempre.
- Lado B3: **MUDANÇA DE DESENHO IMPORTANTE** -- a janela de 7 dias não é
  ancorada em "agora" (como o `b3_spread_7d` de `emissor_taxas`, que faz
  sentido pra um emissor específico sendo olhado ao vivo), e sim na
  PRÓPRIA data do boletim Anbima de cada emissor ("com base na data
  Anbima", pedido explícito do Allan) -- emissores com boletim publicado
  em dias diferentes (raro, mas acontece) cada um usa sua própria janela
  `[anbima_data - 7d, anbima_data]`. Busca os negócios em MASSA (uma
  query só, pra todos os tickers da classe, numa janela ampla de 60 dias
  antes da data mais recente entre os emissores) e filtra fino por
  emissor em Python -- evita uma query por emissor (poderia ser
  centenas).
- `variacao_bps = b3_spread - anbima_spread` -- mesmo sinal de
  "aberturas"/"fechamentos" do resto do dashboard (positivo = B3
  negociando mais largo que a Anbima).
- Só entram no ranking emissores com AMBOS os lados calculáveis (Anbima
  E pelo menos 1 negócio B3 na janela) -- a maioria dos emissores não
  tem negócio B3 recente, então o ranking cobre só uma fração da base
  (esperado, não bug).

Nova rota `GET /api/spreads/emissor/ranking-diferencas?classe=...&top=15`.
Front-end: duas tabelas lado a lado (`movers-grid`, mesmo layout de
Maiores Aberturas/Fechamentos da Visão Geral, cores reaproveitadas
`cell-abertura`/`cell-fechamento`) em `templates/spreads.html`, dentro
do bloco que já ficava visível só quando nenhum emissor está
selecionado (`#emissor-vazio`/`#emissor-ranking-wrap` mostrados juntos,
escondidos junto com `#emissor-conteudo` quando o Allan seleciona algum
emissor). Recarrega ao abrir a aba Emissores e ao trocar a classe
(IPCA+/CDI+) -- só quando não há emissor selecionado.

Testado contra cópia do banco real: ordenação (aberturas decrescente,
fechamentos crescente), maior abertura ≥ maior fechamento, reconstrução
manual completa de um emissor do resultado (CSN, +552,7 bps -- Anbima
1.231,2 bps vs. B3 1.784,0 bps) conferindo os dois lados E a ancoragem
da janela B3 na data do boletim Anbima (não em "hoje"), e a rota
end-to-end via TestClient.

### Ranking B3 vs. Anbima: data de referência + renomeação — 27/07/2026 (2ª rodada)

No dia seguinte à entrega do ranking acima, Allan pediu dois ajustes
finos:

1. **"Faltou a opção de eu poder alterar a data de referência, essa data
   precisa ficar explícita em algum lugar."** `emissor_ranking_diferencas`
   ganhou parâmetro opcional `data_referencia: date | None` -- quando
   informado, o subquery de `MAX(data)` por ticker (lado Anbima) passa a
   filtrar `data <= data_referencia`, então cada ticker resolve pra
   última publicação ATÉ aquela data em vez da mais recente de verdade
   (mesma ideia de "hoje" da Visão Geral, `_resolve_hoje`, só que
   aplicada por ticker -- esse ranking já tolerava emissores com datas
   Anbima ligeiramente diferentes entre si mesmo antes desse parâmetro,
   já que cada um usa o `MAX(data)` da sua PRÓPRIA série). O resultado
   ganhou um campo `data_referencia` de nível superior (a MAIOR data
   Anbima entre os emissores do ranking) -- é isso que fica "explícito
   em algum lugar": `templates/spreads.html` ganhou um campo de data
   (`#emissor-ranking-data`, mesmo padrão do "Data analisada" da Visão
   Geral) e um rótulo "Dados até: X" (`#emissor-ranking-dados-ate`) acima
   das duas tabelas, dentro do mesmo bloco que só aparece quando nenhum
   emissor está selecionado. Rota `/api/spreads/emissor/ranking-diferencas`
   ganhou query param `data` (reaproveita `_parse_data`).
2. **Renomeação**: "Maiores Aberturas — B3 vs. Anbima" virou "Maiores
   Prêmios sobre a Anbima"; "Maiores Fechamentos — B3 vs. Anbima" virou
   "Maiores Descontos sobre a Anbima" -- só o texto exibido em
   `templates/spreads.html` (`<h2>`), os IDs de tabela/campos internos
   (`aberturas`/`fechamentos`, classes CSS `cell-abertura`/
   `cell-fechamento`) continuam com o nome antigo por consistência com o
   resto do dashboard (mesma convenção de sinal em todo lugar).

Testado contra cópia do banco real: sem data (comportamento de sempre),
com uma data ~1 semana atrás (todo `anbima_data` das linhas devolvidas
≤ a data escolhida, `data_referencia` do resultado bate com a data
escolhida), com uma data anterior a todo o histórico (vazio, sem erro),
e a rota end-to-end (incluindo `data` inválida → 400).

### Bug real: tabela de ranking sumia na aba Emissores antes de filtrar — 27/07/2026

Allan reportou (screenshot): "cadê a tabela daqui enquanto eu ainda não
filtrei? sumiu". Causa: `#emissor-ranking-wrap` (o bloco com as duas
tabelas de ranking) tinha `style="display:none;"` como padrão no HTML,
e o único trecho de JS que já a deixava visível
(`atualizarPainelEmissor()`) só roda quando um chip de emissor é
adicionado/removido — nunca no carregamento inicial da aba. Corrigido
removendo o `display:none` do HTML (fica visível desde o carregamento,
igual `#emissor-vazio` já era); `atualizarPainelEmissor()` continua
responsável só por ESCONDER o bloco quando um emissor é selecionado.

### Bug real: tabela de negociações B3 não filtrava por classe — 27/07/2026

Allan reparou olhando a BRK Ambiental: o card "SPREAD NEGOCIADO (B3)"
mostrava "27/07/2026 · 9 negócio(s)", mas a tabela "Últimas negociações
(B3)" logo abaixo mostrava bem mais linhas que 9. Causa raiz:
`emissor_trades` (`app/spreads/queries.py`) buscava os tickers do
emissor só por `Debenture.nome.in_(nomes_emissor)`, SEM filtrar por
`classe` -- enquanto `emissor_taxas` (que alimenta o card, e já
filtrava por classe desde sempre) só contava negócios dos tickers da
classe selecionada. A BRK Ambiental tem 6 séries: 1 IPCA + Incentivada
(BRKP28) e 5 CDI + Tradicionais (BRKPA0-4) -- com "IPCA + Incentivadas"
selecionado, o card contava só BRKP28 (9 negócios), mas a tabela
misturava negócios dos 6 tickers juntos, de QUALQUER classe.

**Correção**: `emissor_trades` ganhou parâmetro obrigatório `classe`,
filtrando os tickers do emissor igual a todo o resto da aba. Rota
`/api/spreads/emissor/negociacoes` ganhou query param `classe`
(reaproveita `_validar_classe`). `static/spreads.js` passa
`currentEmissorClasse` na chamada e recarrega a tabela ao trocar de
classe (antes só o card/gráfico recarregavam).

Testado contra cópia do banco real com a própria BRK Ambiental: com
"IPCA + Incentivadas", card e tabela agora batem exatamente (9 e 9);
com "CDI + Tradicionais", card mostra 9 (só do dia mais recente) e a
tabela mostra 17 (últimos negócios de vários dias, `limit=100`) --
diferença esperada (escopos diferentes por desenho: card é "só o dia
mais recente", tabela é "últimos N negócios, vários dias"), o que
importa é que agora nenhuma linha da tabela pertence a um ticker de
fora da classe selecionada -- conferido nos dois casos.

### Tabela de negociações B3: coluna Indexador no lugar de Quantidade — 27/07/2026

Pedido direto do Allan. `emissor_trades` passou a devolver `indexador`
por linha (vem do cadastro `Debenture`, não do negócio em si -- a B3
não manda indexador negócio a negócio). `templates/spreads.html`
(cabeçalho da tabela) e `static/spreads.js::loadEmissorNegociacoes()`
trocaram a coluna "Quantidade" por "Indexador". Campo `quantidade`
continua sendo devolvido pela API (não removido, só parou de aparecer
na tabela) -- sem uso conhecido em outro lugar, mas inofensivo manter.

### Intervalo explícito nos rótulos "Média 3M" / "Média 7d" — 27/07/2026

Allan pediu: "sempre que colocar algum indicativo como 'Média 7d:'
coloque (data-data) explícito." `emissor_taxas` ganhou 4 campos novos:
`anbima_spread_3m_inicio`/`_fim` (as datas real e mais antiga/mais nova
das 63 posições usadas -- não é janela de calendário fixa, já que o
boletim Anbima só publica em dia útil) e `b3_spread_7d_inicio`/`_fim`
(aqui sim janela de calendário nominal, `hoje - 7 dias` até `hoje`,
já que negócio B3 não é diário garantido pra nenhum papel).
`static/spreads.js` ganhou helper `fmtIntervalo(inicio, fim)` (formato
curto `dd/mm-dd/mm`, sem ano) usado nos dois rótulos, ex.: "Média 3M:
270,6 bps (27/04-24/07)" e "Média 7d: 268,5 bps (20/07-27/07)".

### Bug real + redesenho: curva NTN-B de referência dos negócios B3 — 27/07/2026 (2ª rodada)

**Bug reportado pelo Allan** (screenshot, BRK AMBIENTAL): o card "SPREAD
NEGOCIADO (B3)" mostrava "Negócios sem spread calculado" mesmo com a
tabela de negociações trazendo 9 negócios do BRKP28 com taxa válida.
Allan chutou "não aparece o IPCA, é porque os negócios são não
incentivados?" — não era isso (BRKP28 é `incentivada='S'`, confirmado
direto no banco).

**Causa raiz**: `compute_trade_spreads` usava a curva de NTN-B do
**próprio dia do negócio** (`_get_ntnb_curve(db, t["data_negocio"])`) pra
achar a taxa de referência. O boletim da Anbima de hoje só fica
pronto/cacheado tarde da noite (~18-21h BRT, quando o job diário roda) —
os 9 negócios do BRKP28 foram capturados entre 13h e 17h, TODOS antes de
`ntnb_referencia` de 27/07 existir (`captured_at` real: 21:42 UTC ≈
18:42 BRT). Como o spread só é calculado UMA VEZ, na hora de gravar (não
recalcula sozinho depois), esses negócios ficavam com `spread=NULL` pra
sempre — só um backfill manual (`scripts/backfill_b3_trade_spreads.py`)
resolvia, e só depois da curva ter sido cacheada.

**Pedido do Allan pro fix de verdade** (não só "roda o backfill mais
vezes"): *"A curva de referência da NTN-B tem que ser a data do dado
Anbima. O padrão sempre vai ser d-1, mas deixe uma caixa que eu possa
alterar essa data."*

**Fix implementado** — redesenho arquitetural em `app/spreads/b3_trades.py`:
- `_resolve_ntnb_referencia_date(db, trade_date)`: a referência de
  QUALQUER negócio (de hoje ou de backfill histórico) passa a ser o
  **último boletim Anbima JÁ PUBLICADO antes desse negócio**
  (`MAX(DebentureSpread.data) < trade_date`), nunca a curva do próprio
  dia — mesmo que por acaso ela já esteja cacheada (consistência
  metodológica, não só "usa o que tiver"). Na prática isso é quase
  sempre `trade_date - 1 dia útil`, mas calculado de verdade contra o
  histórico (não um "menos 1 dia corrido" fixo, que erraria em fim de
  semana/feriado).
- `compute_trade_spreads` reescrito: antes buscava uma curva por DATA DE
  NEGÓCIO distinta; agora resolve a data de referência de cada data de
  negócio primeiro, depois busca uma curva por DATA DE REFERÊNCIA
  distinta (várias datas de negócio podem cair no mesmo boletim de
  referência, ex. trades de dias seguidos sem boletim novo no meio).

**Caixa de override removida na hora**: a primeira versão veio com uma
caixa em Administração pra travar a data manualmente (pedido original do
Allan incluía isso). No mesmo dia, revisando o resultado, ele pediu pra
tirar — já existe uma data selecionável na aba Emissores e não precisa
de mais um controle noutro lugar. Revertido: `admin.html` sem o form
extra, `app.py` sem a rota `/admin/configuracoes/ntnb-referencia-override`,
`b3_trades.py` sem `AppSetting`/`get_ntnb_referencia_override` — a
resolução é sempre automática.

**Efeito colateral bom, não só o bug reportado**: como a referência
agora é sempre de um dia ANTERIOR (que o job diário já cacheou de
véspera), qualquer negócio capturado ao longo do pregão de hoje já sai
com spread calculado na hora da gravação — o bug de "curva do próprio
dia não pronta ainda" desaparece por construção, não fica só mais raro.
Os valores de spread também mudam de verdade (não é só "deixou de dar
None"): antes, um backfill tardio calculava contra a curva de HOJE; agora
sempre contra a curva do ÚLTIMO BOLETIM PUBLICADO — números diferentes
por desenho, é a mudança que o Allan pediu.

Testado contra cópia do banco real: `_resolve_ntnb_referencia_date`
resolveu 24/07 pra negócio de 27/07 (último boletim na base naquele
momento — 27/07 ainda não tinha sido publicado), nunca o próprio dia;
rodando `scripts/backfill_b3_trade_spreads.py` de ponta a ponta contra a
cópia, os 9 negócios reais do BRKP28 (caso do Allan) passaram a calcular
spread pra TODOS (antes: nenhum); CDI+ Tradicionais confirmado
inalterado (`taxa*100`, não depende de NTN-B).

**Pendência real no banco do Allan**: o código só vale pra negócios
NOVOS captados a partir de agora. Os já gravados (ex. os 9 do BRKP28 de
27/07, que ainda aparecem "sem spread calculado" no card, embora a
tabela mostre os negócios com taxa) continuam com `spread=NULL` até ele
rodar `python -m scripts.backfill_b3_trade_spreads` no ambiente real
(local ou nuvem) — sem flag nenhuma, idempotente, só recalcula o que
ainda estiver `NULL`.

### Deploy do módulo Spreads pro site online + verificação de atualização — 27/07/2026

Todo o módulo Spreads (Visão Geral, Emissores, negócio a negócio B3, e
todas as correções desta rodada) tinha sido construído rodando só local
-- nunca tinha ido pro Supabase/Vercel/GitHub Actions de verdade
(`git status` mostrava o módulo inteiro como untracked, mesmo dias
depois de "pronto"). Allan pediu pra subir pra valer, com a mesma
cadência já decidida antes: spreads 1x/dia depois das 20h, negócio a
negócio B3 a cada 15 min -- **mais um pedido novo**: "podemos programar
uma verificação pra ver se foi atualizado mesmo" (spreads).

**Checklist completo movido pro `DEPLOY.md`** (Parte 6, nova) -- mesmo
padrão das Partes 1-5 já existentes (linguagem pro Allan seguir sozinho,
passo a passo). Cobre: comandos exatos de `git add`/`commit`/`push`
(lista explícita de arquivo, NUNCA `git add -A`/`git add data/` -- a
pasta `data/` tem o `.db` local e exports de outro projeto, ratings, que
não devem subir pro repositório público), os 2 segredos novos no GitHub
(`ANBIMA_CLIENT_ID`/`ANBIMA_CLIENT_SECRET` -- só no GitHub Actions, o
Vercel não precisa deles), o segundo cronjob no cron-job.org
(`?job=b3_trades`, reaproveitando o `CRON_SECRET` que já existe), e o
comando de backfill de 3 meses (já tinha sido decidido, só não tinha
sido executado ainda porque o módulo nunca tinha sido commitado).

**Peça nova pro pedido de verificação**: `scripts/verify_spreads_updated.py`
+ `.github/workflows/spreads_verify.yml`. Por que isso não é redundante
com o próprio `fetch_debenture_spreads.py` "rodar sem erro": o script
pode terminar com sucesso (exit 0) mesmo sem capturar nada de novo -- ex.
se a Anbima ainda não tinha publicado o boletim no momento exato em que
o cron rodou (histórico já documentado de publicar tarde, ~18-21h BRT --
mesmo motivo do bug da curva NTN-B desta rodada) -- isso passaria batido
sem ninguém notar. `verify_spreads_updated.py` pergunta pra própria
Anbima (`detect_latest_published_date`, MESMA chamada que o fetch já usa)
qual é o dia mais recente publicado e compara com `MAX(DebentureSpread.data)`
no banco; se o banco ficou pra trás (ou está vazio), sai com código 1 --
o workflow então FALHA de propósito, e o GitHub manda e-mail de
notificação de falha automaticamente (comportamento padrão dele, não
precisou de nenhuma integração nova tipo Slack/SMTP).

Agendado **1h depois** da captura (22h BRT / 01h UTC, não junto com o
`spreads_daily` de 21h) de propósito -- dá uma folga pro caso da Anbima
publicar um pouco atrasada, evitando alarme falso todo dia em que a
publicação atrasar só alguns minutos.

Testado localmente (mock da chamada pra Anbima, já que o sandbox não
alcança o domínio dela) contra cópia do banco real, cobrindo os 3
cenários: banco atualizado (exit 0), banco desatualizado (exit 1, com
mensagem indicando as duas datas), banco vazio (exit 1). **Nunca rodou
de ponta a ponta contra a Anbima de verdade nem dentro do GitHub Actions**
-- mesma ressalva de sempre pra código novo que bate em fonte externa
bloqueada pro sandbox; Allan deve rodar "Run workflow" manualmente uma
vez depois do deploy pra confirmar.

### Filtros de Setor/Empresa/Cobertura viram multi-select — 03/08/2026

Pedido do Allan: poder marcar mais de uma opção nos 3 filtros da barra da
aba Notícias (`filter-type` ficou de fora de propósito, não foi pedido).
`<select>` nativo não faz multi-seleção de um jeito usável (Ctrl+clique
não é descobrível), então os 3 viraram um componente próprio: um botão
que abre um painel flutuante de checkboxes (`.ms*` em `static/style.css`,
`initMultiSelect()` em `static/app.js`, reaproveitado pros 3 -- só
"Empresa" ganha uma caixa de busca dentro do painel, já que a lista pode
passar de 90 itens).

**Backend** (`app/store.py::list_articles`, `app/app.py::api_articles`):
`sector_id`/`company_id`/`coverage` passam a aceitar valor repetido na
querystring (`?sector_id=1&sector_id=2`) via `Query(default=[])` do
FastAPI, que já junta isso numa lista sozinho. Dentro da query,
setor/empresa continuam usando `EXISTS` (não `JOIN`) pra "bate com
QUALQUER um dos selecionados" (OR) sem multiplicar linha quando um
artigo casa com mais de uma empresa/setor marcado ao mesmo tempo --
mesma proteção que já existia pro filtro de setor sozinho, agora também
pro de empresa (o `JOIN` antigo do filtro de empresa nunca tinha esse
problema porque só filtrava 1 valor; com lista, passou a ter o mesmo
risco de duplicar linha, por isso trocado). `coverage` vira lista também
(`["minha"]`/`["todos"]`/os dois juntos) -- "todos" já é superset de
"minha", então marcar os dois se comporta exatamente igual a marcar só
"todos" (`"todos" not in coverage` decide, igual antes só que checando
lista em vez de string).

**Frontend**: `selectedSectors`/`selectedCompanies`/`selectedCoverage`
(JS `Set`), refletidos no rótulo do botão ("Setor: Todos" / "Setor:
Energia" / "Setor: 3 selecionados"). Trocar o setor invalida da seleção
de empresa qualquer empresa que não pertença mais a nenhum setor
marcado (mesmo efeito que o `<select>` antigo já tinha ao reconstruir a
lista do zero). Cobertura nunca fica com 0 selecionado -- desmarcar a
última opção volta sozinho pra "Minha cobertura" (evita o botão mostrar
rótulo errado com o filtro "sem cobertura nenhuma" aplicado por engano).

Testado contra cópia do banco real: multi-select de 2 setores = união
EXATA dos dois individuais (sem linha duplicada); multi-select de 2
empresas = mesma união exata; `coverage=["minha","todos"]` devolve
exatamente a mesma contagem que `coverage=["todos"]`; ausência de
`coverage` cai no default `["minha"]`, igual antes. Também testado de
ponta a ponta via `TestClient` contra a rota `/api/articles` de verdade
com parâmetro repetido na querystring (o formato que o `app.js` novo
manda). `dashboard.html` e `app.js` conferidos renderizando/sem erro de
sintaxe.

## Regras a manter

1. **Nunca** trocar `store.upsert_article` para sobrescrever corpo/título
   com algo mais curto — mantém sempre o maior (mesma lógica do clipinator).
2. Toda fonte nova precisa: entrada em `config.KNOWN_SOURCES` + módulo em
   `app/sources/<nome>.py` com `fetch(url) -> list[RawArticle]`.
3. Relevância é sempre por empresa/setor coberto (`taxonomy` + `filter`),
   nunca por fonte isolada — não reintroduzir o modelo de "keywords fixas
   por domínio" do clipinator, é o que estamos generalizando aqui.
4. `data/Setores.xlsx` é a fonte de verdade da cobertura — se o time mudar
   de setor/empresa, atualizar a planilha e rodar `python -m scripts.seed`
   de novo (idempotente, não duplica nem apaga edições feitas na UI).
5. **`store._set_companies` precisa SEMPRE substituir a lista de empresas
   do artigo pelo resultado do casamento mais recente, nunca só adicionar.**
   Motivo: até 17/07/2026 só adicionava, e qualquer vínculo errado gravado
   uma vez (bug de scraper, colisão de dedupe) ficava associado ao artigo
   pra sempre, mesmo depois do bug corrigido — foi o que causou "Boa
   Safra" aparecer marcada em notícias sobre Localiza/Movida que não a
   citavam. Não reintroduzir a versão "union-only".
6. **`filter.match_keywords` exige maiúscula no texto original pra nome de
   empresa contar como menção** (nomes próprios não aparecem em minúscula
   no meio de uma frase; se aparecem, é coincidência com uma palavra comum
   -- ex.: "movida" verbo vs. empresa "Movida"). Termos de SETOR (não são
   nomes próprios) ficam isentos via o parâmetro
   `keywords_sem_checagem_maiuscula` -- sempre passar
   `taxonomy.sector_only_keywords` nessa posição ao chamar
   `match_keywords` com a lista completa de `taxonomy.all_keywords`, senão
   termos de setor como "varejo" param de bater.
7. **Se nenhuma empresa específica bate mas um termo de SETOR bate**,
   `taxonomy.resolve_company_ids` associa o artigo a TODAS as empresas
   daquele setor (pedido explícito do Allan: notícia setorial/macro sem
   citar emissor ainda é relevante pra quem cobre o setor). Se uma empresa
   específica bate, usa só ela (nunca mistura os dois).
8. **Depois de qualquer mudança na lógica de casamento de keywords**
   (`filter.py`/`taxonomy.py`/`pipeline.py`), rodar o backfill pra ver o
   que mudaria no banco real e aplicar -- corrige artigos antigos que
   ficaram com vínculo errado gravado antes do fix (o self-heal do
   `_set_companies` só corrige na próxima vez que a MESMA url for
   reprocessada, o que pode nunca acontecer pra itens de RSS que já
   saíram do feed). Allan não roda Python direto -- usar sempre
   `Corrigir Vinculos de Empresa.bat` (chama
   `python -m scripts.rebuild_company_links` por trás, com um passo de
   `--dry-run` primeiro).
9. **Allan não sabe rodar comandos Python** — qualquer script novo
   (backfill, migração, diagnóstico) precisa vir com um `.bat`
   correspondente na raiz do projeto, nunca só a instrução
   `python -m scripts.algo` pra ele digitar.
10. **Editar o código NÃO atualiza o programa que o Allan já tem aberto.**
    `Abrir Monitoramento.bat` detecta que a porta 8765 já está em uso e
    só abre o navegador de novo na versão ANTIGA rodando em segundo
    plano — fechar a janela preta manualmente é fácil de esquecer/errar.
    **Depois de qualquer mudança em `.py`/`.html`/`.js`/`.css`, sempre
    instruir o Allan a rodar `Reiniciar Monitoramento.bat`** (mata o
    processo antigo na porta 8765 antes de reabrir) em vez de só pedir
    pra ele "testar de novo" — isso já causou pelo menos um relato de
    "bug não corrigido" (17/07/2026) que na real era só o processo
    antigo ainda rodando.

## Correções de precisão de scraper (17/07/2026)

Allan revisou o resultado ao vivo e mandou HTML real de 3 fontes para
recalibrar os parsers — se algo parecer errado de novo nessas fontes,
comece relendo esta seção antes de adivinhar de novo:

- **CVM RAD**: a célula de ações tem vários ícones, cada um com seu
  próprio `onclick`. O antigo código pegava o PRIMEIRO onclick que
  encontrasse (podia ser o de download, formato não confirmado). Agora
  `_extrair_url_documento` varre todos os onclick da célula e prioriza
  `OpenPopUpVer(...)` — é o ícone "Visualizar o Documento"
  (`fi-page-search`) que Allan confirmou abrir de verdade.
- **Moody's Local**: estrutura real é `table#table_1` (wpDataTable) com
  colunas `td.column-rating_action_post_date` (data) e
  `td.column-rating_action_title_with_link_to_post` (título + `<a href>`
  real). `_from_table_rows` agora usa essas classes exatas como estratégia
  primária pra "Ações de Rating"; as outras 2 páginas (Relatórios do
  Emissor/Setoriais) ainda usam o fallback genérico porque não tenho HTML
  confirmado delas.
- **S&P Global**: Allan pediu pra trocar a URL de `ratings-actions` para
  `press-releases` — página mais simples (`.table-module__row` com só 2
  colunas: data "10-Jul-2026 17:57 BRT" e `<a href>` relativo). Filtro
  "Últimos 12 Meses" + botão Atualizar + paginação por seta
  (`a[aria-label="Next page"]`, `aria-disabled="true"` quando acaba)
  confirmados ao vivo.

## Correções usando os scripts de referência do Allan (17/07/2026, 2ª rodada)

Allan tem scripts Playwright próprios e PROVADOS funcionando em
`C:\Users\allan\OneDrive\Documentos\IBBA\Claude\RatingsAction\` (Fitch e
S&P) que fazem uma coleta bem mais completa (abre cada artigo, extrai
tabela de rating detalhada) — não é isso que queremos aqui (Allan foi
explícito: só notícia + link, não a tabela), mas os SELETORES e o FLUXO de
navegação confirmados nesses scripts são ouro pra calibrar os nossos:

- **S&P — bug real do found=0**: o dropdown de período em
  `/press-releases` tem DOIS elementos parecidos —
  `data-testid="criteria-dropdown-title"` é só o `<span>` que MOSTRA o
  texto da opção atual (não clicável de forma útil), e
  `data-testid="criteria-dropdown"` é o elemento que de fato ABRE o
  painel. A primeira versão daqui usava o seletor errado (`-title`), então
  o dropdown nunca abria e a busca ficava presa no filtro padrão da
  página. Confirmado contra `RatingsAction/S&P/coletar_ratings_sp.py`,
  função `_filtro_p2`. Se o found continuar 0, o próximo suspeito é o
  texto exato do label "Últimos 12 Meses" ter mudado — mandar o HTML do
  painel aberto.
- **Fitch — `dateValue`**: a URL usava `dateValue=lastMonth`, um valor que
  eu nunca vi confirmado em lugar nenhum. O script de referência
  (`RatingsAction/FitchRatings/coletar_ratings_fitch.py`) usa
  `dateValue=lastWeek`, PROVADO funcionando — troquei pra esse valor.
  Se `lastMonth` (ou outro) também for um valor válido, tudo bem trocar
  de volta, mas só com confirmação — não adivinhar de novo.
- **Fitch — paginação**: simplifiquei `_clicar_proxima_pagina` pra ser
  idêntico ao do script de referência (`a[title="Go to next page"]` +
  checar `closest('li').className` por "disabled") em vez da versão mais
  complicada com `evaluate_handle` que eu tinha escrito antes.
- Também adicionei um `page.wait_for_selector(".frw-article-data--title
  a[href]", timeout=20000)` explícito no Fitch antes de extrair, igual o
  script de referência faz — mais robusto que só um `wait_for_timeout`
  fixo se o React demorar mais que o esperado pra renderizar.

**Se voltar found=0 de novo nessas duas fontes**: reler os dois scripts de
referência ANTES de tentar adivinhar — eles têm anos de calibração ao
vivo que eu não tenho como reproduzir no sandbox (não alcanço a internet
real daqui).

## Correção de link CVM e bug de fuso horário (17/07/2026, 3ª rodada)

- **CVM RAD — link do documento corrompido**: `_parse_tabela` (em
  `cvm_rad.py`) sempre grudava um fragmento `#codigo-data-...` no fim da
  URL do documento, pra evitar duas linhas diferentes caírem na mesma URL
  (dedupe). O problema é que isso corrompia até um link REAL e específico
  do `OpenPopUpVer` (que já é único por natureza, por causa do
  `NumeroProtocoloEntrega`), fazendo o link dar erro ao abrir. Agora só
  gruda o fragmento quando cai no fallback genérico (`GENERIC_URL`) — um
  link real nunca é mais tocado. Confirmado com o protocolo 1545488 que o
  Allan reportou.

- **Bug de fuso horário (hora errada, data certa)** — dois bugs
  independentes que se mascaravam um ao outro:

  1. **Exibição (`app.py`)**: o SQLite não guarda timezone de verdade —
     mesmo as colunas sendo `DateTime(timezone=True)`, o SQLAlchemy
     devolve os datetimes SEM tzinfo depois de ler do banco, apesar de
     todo valor ser gravado com `tzinfo=timezone.utc`. `datetime.isoformat()`
     de um valor naive não inclui sufixo de fuso, e o JavaScript interpreta
     uma string ISO sem fuso como se já fosse horário LOCAL do navegador —
     então um horário UTC aparecia no dashboard 3h adiantado (a data batia
     porque o erro raramente cruza a virada do dia). **Corrigido** com o
     helper `_iso_utc()` em `app.py` (atribui `tzinfo=timezone.utc`
     explicitamente antes do `isoformat()`) e o filtro Jinja `brt` (mesma
     ideia, usado no `admin.html` que formata datas direto no servidor).

  2. **Coleta (scrapers)**: vários scrapers pegavam a hora exibida no
     site — já em horário de Brasília (CVM, S&P Brasil) — e simplesmente
     rotulavam como `tzinfo=timezone.utc` sem converter de verdade, então
     o valor gravado ficava 3h atrasado em relação ao UTC real. Sozinho
     isso "cancelava" o bug de exibição por coincidência (dois erros de
     sinais opostos); depois de corrigir só a exibição, esse bug ficaria
     exposto (mostraria 3h ATRASADO em vez de adiantado). **Corrigido**
     com o helper `brt_to_utc(ano, mes, dia, hora=12, minuto=0)` em
     `app/sources/base.py`, que converte de verdade usando
     `ZoneInfo("America/Sao_Paulo")`. Aplicado em:
     - `cvm_rad.py` e `spglobal.py` (têm hora real capturada — S&P
       inclusive mostra "BRT" explícito no texto da data).
     - `fitch.py`, `moodys_local.py._parse_date` e
       `base.py._parse_date_pt_extenso` (só têm DATA, sem hora — usam o
       padrão de meio-dia do helper pra não arriscar o dia calendário
       "andar pra trás" na conversão de ida e volta).
     - Fontes por RSS (`parse_rss` em `base.py`) NÃO precisaram de
       correção — o `feedparser` já normaliza pro UTC certo a partir do
       offset que o próprio feed declara (ex.: "-0300" no `pubDate`).

  **Regra geral daqui pra frente**: qualquer scraper novo que capture
  hora de um site brasileiro deve usar `brt_to_utc()` (nunca
  `tzinfo=timezone.utc` direto num valor que veio do HTML/texto do site) —
  e qualquer novo lugar que serialize datetime pra JSON/HTML deve passar
  por `_iso_utc()` (em `app.py`) ou pelo filtro `brt` (em templates),
  nunca `.isoformat()`/`.strftime()` cru.

- **S&P Global found=0 — causa raiz real encontrada**: os
  `data/debug_spglobal.html`/`.png` gerados numa rodada real do Allan
  mostram que a página nunca chegou a carregar de verdade — a S&P
  bloqueia a requisição na BORDA (Akamai Bot Manager), devolvendo uma
  página de "Access Denied" (`errors.edgesuite.net`) no lugar do site.
  Ou seja, TODAS as tentativas anteriores de calibrar o seletor do
  dropdown de período (`criteria-dropdown` vs `criteria-dropdown-title`)
  foram irrelevantes — a extração nunca chegava nem perto de rodar,
  porque o HTML capturado sempre foi essa página de erro de ~350 bytes,
  não o site real. **Tentativa de correção (17/07/2026, ainda não
  confirmada funcionando)**: o Chromium embutido do Playwright tem uma
  "impressão digital" bem conhecida por ferramentas anti-bot;
  `spglobal.py` agora tenta abrir com `channel="chrome"` (Chrome de
  verdade instalado na máquina, via `playwright install chrome` —
  adicionado no `Abrir Monitoramento.bat`) antes de cair pro Chromium
  padrão. Se ainda assim continuar bloqueado depois do próximo teste do
  Allan, o problema é bot-detection de verdade (não seletor), e as
  opções ficam mais limitadas: usar um serviço de scraping com IP
  residencial/rotativo, ou aceitar que essa fonte específica pode não
  dar pra automatizar do jeito atual.

- **CVM RAD só mostrava os documentos DO DIA**: por padrão a busca do RAD
  vem sem filtro de período (equivale a "hoje"), então antes de paginar a
  tabela o scraper nunca via mais do que isso. Corrigido (17/07/2026)
  com `_selecionar_periodo_mes()`: marca o radio "Período" (`#rdPeriodo`,
  não "Semana"/`#rdSemana`), preenche `#txtDataIni`/`#txtDataFim` com
  hoje-1-mês e hoje (horário de Brasília, `dd/mm/aaaa`) e clica em
  "Consultar" (`#btnConsulta`) antes de começar a paginar. Um mês é de
  propósito mais largo que a janela real (`LOOKBACK_DAYS = 10`) -- o
  corte final continua sendo feito depois, então pedir mais do site não
  tem desvantagem, só reduz o risco de perder documento por causa de um
  filtro de período curto demais.

## Deploy na nuvem: primeira rodada real (17/07/2026)

Allan seguiu o `DEPLOY.md` do início ao fim: Supabase criado, banco
seedado, GitHub Actions rodando a cada 5 min, site no Vercel
(`monitoramento-noticias.vercel.app`) respondendo login e dashboard.
Erros encontrados e corrigidos ao vivo (guardar pra não repetir):

- **`DATABASE_URL` colado com lixo junto**: tanto no Vercel quanto no
  secret do GitHub, ao copiar do Bloco de Notas o Allan grudou texto de
  outra linha junto (ex.: `...supabase.com:6543/postgres\n   BOOTSTRAP_...`
  ou uma senha errada) -- causou `database "postgres\n..." does not
  exist` e `password authentication failed`. Nem sempre dá pra "revelar"
  o valor salvo (variáveis marcadas "Sensitive" no Vercel são write-only,
  igual GitHub Secrets) -- quando isso acontece, a saída é ler o `.env`
  LOCAL dele (que já está confirmado funcionando) e mandar a string exata
  pra ele colar de novo, em vez de pedir pra digitar/copiar de novo.
- **N+1 queries -> timeout 504 em `/fontes`**: sem eager loading,
  `sector.companies` + `company.aliases` (e `article.companies` +
  `company.sector` no dashboard) disparavam uma consulta por linha. No
  SQLite local isso nunca apareceu (latência ~0), mas no Postgres do
  Supabase com `NullPool` (conexão nova a cada consulta) virava uma
  cascata de idas-e-voltas de rede que estourava os 10s de timeout do
  Vercel. Corrigido com `selectinload` em `store.list_articles` e
  `app.py:sources_page`. **Lição**: qualquer novo `.all()`/loop que
  acesse relationship (`.companies`, `.aliases`, `.sector`, etc.) precisa
  vir com eager loading pensado pra Postgres+serverless, não só testado
  local em SQLite.
- Repositório GitHub saiu com hífen extra no nome
  (`allanmikayo/-monitoramento-noticias`) -- sem efeito prático, só
  atenção redobrada ao copiar a URL em `GITHUB_REPO`/remote git.

## Tag de setor + fontes sem empresa específica (17/07/2026)

Pedido do Allan: notícias setoriais/macro (ex.: "nova lei do saneamento",
Copom) que não citam nenhuma empresa específica da cobertura estavam
sendo capturadas mas ficando de fora de "Minha cobertura" -- porque
faltava termo de SETOR cadastrado que batesse com o texto (o mecanismo de
fallback setorial já existia, só faltava dado). Duas mudanças:

1. **`taxonomy.resolve_coverage`** (renomeado de `resolve_company_ids`):
   se bateu empresa específica, só ela. Se só bateu termo de setor, o
   artigo NÃO fica mais grudado em toda empresa do setor (poluía os chips
   com empresas que a notícia nem cita) -- em vez disso ganha uma
   **tag de setor própria** (`Article.sector_tags`, tabela nova
   `article_sector`, mesma estrutura de `article_company`). Aparece no
   card como um chip laranja sólido "Setor: Nome", separado dos chips de
   empresa. Continua contando como "minha cobertura" normalmente.
   `store.list_articles` filtra por `sector_id` considerando as DUAS
   formas de vínculo (empresa do setor OU tag direta), via `EXISTS` (não
   `JOIN`, pra não multiplicar linha).
2. **Setor sem empresa nenhuma agora é permitido** -- criado um setor
   "Economia" (botão "+ Novo setor" em Fontes & Empresas, rota
   `POST /fontes/setor`) só com termos de setor (Copom, Selic, Banco
   Central), sem nenhuma empresa cadastrada. Serve pra conteúdo
   puramente macro que não é sobre nenhum emissor específico.
3. **Bulk-add de termos/aliases com `;`**: os campos de "termo extra do
   setor" e "alias de empresa" em Fontes & Empresas agora aceitam vários
   valores de uma vez, separados por `;` (ex.: `ANEEL; tarifa de
   energia`). Sempre foi salvo direto no banco (nunca era "só da sessão")
   -- isso já era assim antes, só não estava claro pro Allan.
4. **Fonte nova: Banco Central — Comunicados do Copom**
   (`https://www.bcb.gov.br/api/feed/sitebcb/sitefeeds/comunicadoscopom`,
   feed Atom confirmado ao vivo, `generic_rss`). Precisa do termo "Copom"
   (e opcionalmente "Selic"/"Banco Central") cadastrado no setor
   "Economia" pra aparecer em "Minha cobertura".
5. **Valor Econômico religado**: a URL antiga (`valor.globo.com/rss/valor`)
   nunca foi confirmada; achada a de verdade
   (`https://www.valor.com.br/rss`) via a página pública de descoberta do
   feeder.co (o link "Follow now" de lá expõe a URL original do feed --
   não precisou de login nenhum, o gate de login do feeder.co é só pro
   produto deles). O domínio `valor.com.br`/`valor.globo.com` é bloqueado
   pras ferramentas de pesquisa deste assistente inspecionarem direto,
   mas o robô do GitHub Actions roda separado e deve conseguir buscar
   normalmente -- **fonte ainda está desabilitada no banco** (sync nunca
   mexe em `enabled` de fonte existente), Allan precisa habilitar manual
   em Fontes & Empresas e conferir o painel de diagnóstico depois.
6. **`sync_known_sources` (novo, `app/seed_sources.py`)**: antes só o
   seed manual local sincronizava `config.KNOWN_SOURCES` -> banco, então
   cadastrar fonte nova exigia rodar `python -m scripts.seed` contra o
   Supabase toda vez. Agora `scripts/run_once.py` (GitHub Actions) também
   chama isso a cada rodada -- fonte nova no `config.py` + `git push`
   já aparece sozinha na próxima execução agendada, sem passo manual.

## Cron pontual de verdade (17/07/2026)

Allan reparou que o robô não rodava exatamente a cada 5 min no GitHub
Actions -- confirmado que é limitação conhecida/documentada do GitHub
(`schedule:` não tem garantia de pontualidade, atrasa em horário de
pico). Solução: `.github/workflows/scrape.yml` agora só mantém o
`schedule:` como fallback horário (`0 * * * *`); quem dispara de verdade
a cada 5 min é um serviço externo gratuito (**cron-job.org**, até 1x/min
grátis) chamando o novo endpoint `POST /api/cron-trigger` (`app/app.py`),
protegido por um header `X-Cron-Secret` comparado contra a env var
`CRON_SECRET` (não usa sessão de usuário -- quem chama é um serviço
externo, não um navegador logado). Esse endpoint só chama a mesma
`_dispatch_github_workflow()` já usada pelo botão "Forçar atualização".
Passo a passo de configuração no cron-job.org: `DEPLOY.md`, Parte 5.

## Assembleias (AGD/AGT) de debêntures — Oliveira Trust, Vórtx, Pentágono (23/07/2026)

Pedido do Allan: incluir documentos de Assembleia Geral de Debenturistas
(AGD/AGT) das 3 securitizadoras que ele acompanha, seguindo a mesma ideia
de marcar empresa da cobertura vs. fora dela. Antes de codar, explorei ao
vivo os 4 links que o Allan mandou usando o Chrome dele (não o sandbox
deste ambiente, que não alcança esses domínios) — cada site tem uma
arquitetura BEM diferente por baixo do capô, então cada scraper novo usa
uma estratégia diferente:

- **`app/sources/oliveiratrust.py`** — o melhor caso: achei uma API JSON
  pública sem autenticação (`services-ft.oliveiratrust.com.br/app/v1/
  titulos/documentos`) por trás da página "Central de Documentos". Coleta
  é só `base.get()` (curl_cffi), sem Playwright. Resolve o link real do
  PDF via 2 chamadas extra (`/titulos/{tit}` -> `codigo_operacao`, depois
  `/titulos/fundos/downloads/{codigo_operacao}` -> lista com o link
  final), só para os itens que batem com a cobertura.
- **`app/sources/vortx.py`** — SPA em Next.js, precisa de Playwright pra
  ler a aba "Assembleias" de cada operação (confirmado ao vivo que um GET
  comum na página de detalhe não traz esse conteúdo, só a busca inicial é
  server-rendered). Busca por empresa, pega os ids de operação (inclusive
  da página 2+ via um truque com o header `RSC: 1` do Next.js, evitando
  Playwright só pra paginar a busca), abre cada operação e filtra CRI/CRA
  fora olhando o `<title>` da página.
- **`app/sources/pentagono.py`** — o mais simples dos 3: site ASP.NET
  clássico, server-rendered de verdade (confirmado que um GET comum já
  traz o conteúdo da aba "Publicações", sem Playwright). Filtra por nome
  de arquivo (AGD/AGT/edital/convocação) porque essa aba mistura fato
  relevante e aviso aos debenturistas também.

**Escopo desta 1ª versão (decisão do Allan, pergunta direta feita antes de
codar)**: só **debêntures**, nas 3 fontes. CRI/CRA ficaram de fora de
propósito — nos 3 sites, o nome do "ativo" que aparece na listagem de
CRI/CRA é o veículo da securitizadora (ex.: "CIA PROVINCIA SEC 42E"), não
a empresa devedora, então o casamento por keyword de empresa não bate
direto — precisaria de uma 2ª etapa (abrir a escritura/documento pra achar
o devedor real) antes de valer a pena ligar. Relatórios anuais também
ficaram de fora por enquanto (secundário no pedido original, e a Oliveira
Trust nem tem data exata pra relatório, só o ano).

**Tipo de artigo novo**: `assembleia` (`Article.article_type`), com badge
roxo próprio no dashboard (`static/style.css`/`app.js`/
`templates/dashboard.html`). Assim como o CVM RAD, essas 3 fontes novas
filtram por empresa da cobertura **dentro do próprio scraper** (não
deixam pro pipeline decidir) — o volume total de AGD do mercado inteiro é
grande demais (Oliveira Trust sozinha tem 1.612 registros históricos só
de debênture) pra deixar sem filtro. Nova função compartilhada
`base.load_coverage_names()` (mesma lógica que já existia isolada em
`cvm_rad.py`) evita triplicar esse helper.

**Verificação ao vivo (23/07/2026)**: nos últimos 30 dias (23/06 a
23/07/2026), confirmei pelo menos 1 AGD de empresa da cobertura na
Oliveira Trust — **Hidrovias do Brasil**, AGD de 26/06/2026, 2ª
série/4ª emissão. Não achei exemplo ao vivo de AGD com conteúdo em Vórtx
nem Pentágono nas poucas empresas que testei manualmente (Suzano, Cosan,
JSL, Vale, Cogna, MRV, Oncoclínicas) — não é evidência de que não exista
(só testei uma fração das 96 empresas à mão), mas também significa que o
parser de linha do Vórtx (`_extrair_assembleias_do_painel`) NÃO foi
calibrado contra um painel de Assembleias com conteúdo de verdade — está
propositalmente genérico (procura qualquer linha com data dd/mm/aaaa
dentro do painel ativo). Se vier `found` baixo ou título estranho no
primeiro uso real, o próprio scraper salva `data/debug_vortx.html`
automaticamente pra eu calibrar, igual foi feito com CVM RAD/Moody's/
Fitch nas rodadas anteriores.

**Custo de rede — atenção**: diferente das fontes antigas (que trazem tudo
num único GET/tabela), Vórtx e Pentágono não têm uma página central "todas
as assembleias do mercado" — a única forma de achar documento por empresa
é buscar as ~96 empresas da cobertura uma a uma. Pentágono é HTTP puro
(rápido, mas ainda ~100-250 requisições por rodada); Vórtx é pior porque
cada operação encontrada precisa de uma navegação Playwright inteira (aba
de Assembleias). Se isso deixar a varredura muito mais lenta que o
intervalo de 5 minutos, ou parecer abuso pro servidor de alguma das duas
(rate limit, IP bloqueado), me avisa que a gente ajusta — reduzir a
frequência só dessas 2 fontes, ou trocar por uma lista fixa de
ativos/operações já conhecidos em vez de buscar tudo de novo toda rodada.

**Depois de puxar o código**, além do `Reiniciar Monitoramento.bat`
de sempre, é preciso rodar `python -m scripts.seed` (ou deixar o próprio
`scripts/run_once.py`/agendador sincronizar sozinho, já que
`sync_known_sources` roda a cada execução) pra essas 3 fontes novas
aparecerem em "Fontes & Empresas" — elas entram **habilitadas** por
padrão, então vão rodar já na primeira varredura depois do deploy.

### Revisão da coleta de AGD — bug real no Vórtx + CRI/CRA + diagnóstico Pentágono (03/08/2026)

Pedido do Allan: revisar a coleta de AGD nas 3 fontes (o "propositalmente
genérico" ficou sem calibrar de verdade na 1ª versão — ver seção acima) e
avaliar incluir CRI/CRA. Testei ao vivo (Chrome, não sandbox) contra as 3
fontes e achei uma mistura de bug real, extensão possível e um problema
maior que precisa de decisão do Allan:

**`app/sources/vortx.py` — bug real confirmado e corrigido.** Testei
contra uma operação com conteúdo de verdade (LIGHT - Emissão 22/Série 1,
id=91018, 11 assembleias de 2023-2024 — a mesma exploração manual da 1ª
versão não tinha achado nenhuma operação com AGD cadastrada, só testei
Suzano/Cosan/JSL/Vale/Cogna/MRV/Oncoclínicas). Confirmei que:

- O link que o parser antigo tentava achar (`<a href>` dentro da linha)
  **nunca existe** — cada assembleia é um acordeão (Radix UI) sem link
  algum, mesmo expandido. Todo `RawArticle.url` gerado até aqui caía no
  fallback `#assembleia-ddmmaaaa`, que não abre documento nenhum. Ou seja,
  a fonte rodava sem erro mas nunca produzia um link utilizável.
- Pior: o nome dos documentos (Edital/Ata) só existe no DOM **depois de
  clicar** no gatilho da assembleia — nem o HTML inicial nem o payload RSC
  do Next.js trazem esse texto antes do clique. Então mesmo consertando só
  o link, o parser antigo (que lia `page.content()` uma vez só, sem
  clicar nas linhas) nunca teria achado nome de arquivo nenhum.
- Descobri o mecanismo real inspecionando a aba Network ao clicar no botão
  de download: ele abre direto
  `https://vxmeetings-arquivos-prd.s3.us-east-1.amazonaws.com/Operacoes/{nome-do-arquivo}`
  — bucket S3 público, sem token, montado só com o nome do arquivo
  (`urllib.parse.quote`, preservando a caixa original — a UI mostra tudo
  maiúsculo só por CSS `text-transform`, o nome real no DOM é misto, ex.:
  `AGD - LIGHT - 22E (23.05.24) - Assinada.pdf`). Confirmei em 2
  documentos reais (Edital e Ata) que a URL montada bate byte a byte com a
  URL real capturada no navegador.

**Fix implementado**: `fetch()` agora clica em cada gatilho de assembleia
dentro de `LOOKBACK_DAYS=40` (mesma janela da Oliveira Trust — assembleias
mais antigas nem são clicadas, pra não gastar tempo com histórico
irrelevante) antes de ler o HTML final; `_extrair_assembleias_do_painel`
foi reescrita pra usar `aria-controls` do gatilho pra achar o painel de
documentos certo e extrair `nome_arquivo`/`tipo_doc` da estrutura real
(`div.inline-flex` = badge Edital/Ata, `span.break-all` = nome do
arquivo). Testado com um fixture que reproduz a estrutura real confirmada
ao vivo (gatilho aberto vs. fechado, 2 documentos, URL final) — os 2
testes de URL bateram exatamente com as URLs reais capturadas no
navegador. **Custo de rede sobe** (1 clique extra por assembleia dentro da
janela, além da navegação Playwright por operação) — se ficar lento
demais, é o primeiro lugar pra otimizar.

**CRI/CRA no Vórtx — incluído.** Diferente da Oliveira Trust, aqui o
Apelido/`<title>` da página (ex.: "LIGHT - DEB | Vórtx") já é o nome do
EMISSOR/DEVEDOR de verdade mesmo pra CRI/CRA, não do veículo
securitizador — então o casamento por keyword de empresa funciona sem
precisar de 2ª etapa. Troquei o filtro que só aceitava `" - DEB "` pra
aceitar `" - DEB "`, `" - CRI "` e `" - CRA "` (`_TIPOS_ATIVO_ACEITOS`).

**`app/sources/oliveiratrust.py` — sem bug, completude confirmada.**
Consultei a API ao vivo sem filtro de `tipo_documento`: só existem 2
categorias (`Assembleias`, 1.629 registros, e `Relatórios`, o resto) — não
tem nenhuma categoria de AGD sendo perdida por conta do filtro atual.
CRI/CRA continuam de fora (decisão da 1ª versão, reconfirmada ao vivo: o
nome do "ativo" ainda é o veículo securitizador, não o devedor —
precisaria abrir e parsear a escritura/PDF pra achar o devedor real, um
projeto à parte, maior que os outros dois pontos).

**`app/sources/pentagono.py` — achado preocupante, possivelmente zerado em
produção.** Duas coisas:

1. `_listar_ativos()` faz `GET /Site/Investidores?emissor=X` sem o
   parâmetro `tipo` — descobri ao vivo que o site exige `tipo=N` pra saber
   qual categoria buscar (`tipo=1`=Debênture, `tipo=3`=CRI, e por analogia
   CRA/NP/LF também têm seu próprio número). Sem isso, o servidor nem
   tenta processar a busca direito.
2. Mesmo corrigindo o `tipo`, testei ao vivo no navegador (emissor=JBS,
   emissor=Multiplan, com e sem `tipo=1`) e a busca **sempre** devolve
   "Houve problemas no seu acesso. Tente acessar novamente ou contacte o
   administrador" — a página carrega `google.com/recaptcha/api.js`, então
   a suspeita é que a busca por emissor está atrás de verificação
   anti-bot (provavelmente reCAPTCHA v3 invisível, que pontua a sessão em
   vez de mostrar checkbox) e o `curl_cffi` (que não roda JS, não gera
   token nenhum) nunca vai conseguir passar por isso — com ou sem `tipo`
   corrigido.

Conferi no banco local (`data/credit_monitor.db`, que roda o pipeline de
verdade localmente e tem artigos de dezenas de outras fontes até
27/07/2026 22:30): **zero artigos de `pentagonotrustee.com.br` e zero de
`vortx.com.br` desde sempre** (Oliveira Trust tem exatamente 1 — a
Hidrovias já documentada — o que é esperado, AGD é evento raro). O total
zero do Vórtx é consistente com o bug de link/parsing agora corrigido
acima. Já o zero do Pentágono é mais preocupante: não achei evidência de
que ele algum dia tenha funcionado de verdade contra produção (mesmo
"confirmado ao vivo" na 1ª versão pode ter testado um emissor sem
resultado, sem diferenciar "0 resultados" de "erro de acesso").

**Não mexi no código da Pentágono ainda** — corrigir só o `tipo=` não
resolve se o bloqueio for mesmo reCAPTCHA (só Playwright *talvez* ajude, e
mesmo assim não é garantido passar por reCAPTCHA v3 de forma confiável, e
o custo de rede da Pentágono já era a maior preocupação documentada da 1ª
versão — trocar pra Playwright pioraria isso). Preciso decidir com o Allan
se vale investir mais tempo tentando confirmar/contornar isso, ou se essa
fonte fica pausada por ora.

## Spreads de debêntures — "Hub Credit Research" (23/07/2026)

Segundo módulo do app, além do monitoramento de notícias — pedido do Allan
pra acompanhar o **spread de mercado secundário de debêntures locais Brasil**
(Anbima + debentures.com.br), com **histórico** (diferente do script Excel
original dele, que sobrescrevia um arquivo a cada rodada) e um dashboard
próprio em `/spreads`. É o começo do app virar um "Hub Credit Research" de
verdade (por isso a marca no topo virou "Hub Credit Research" — ver
`templates/base.html` — com "Notícias" e "Spreads" como as duas primeiras
abas; mais módulos devem vir depois).

### Origem e adaptação

Allan forneceu um script Python que ele já usa/roda localmente (colado no
chat em 23/07/2026, depois substituído por uma versão mais nova que ele
subiu como `spreads.docx` no mesmo dia — essa segunda versão foi a usada
como referência final). O script busca 4 fontes pra uma data (ou range de
datas):

1. **Boletim de indicativos da Anbima** (`anbima.com.br/informacoes/
   merc-sec-debentures/arqs/d{aamesdd}.xls`) — abas `DI_SPREAD` e
   `IPCA_SPREAD`, uma linha por Código com Taxa Indicativa, PU, Duration,
   % Pu Par, Referência NTN-B.
2. **Curva de NTN-B da Anbima** (`anbima.com.br/informacoes/merc-sec/arqs/
   m{aamesdd}.xls`, aba `NTN-B`) — usada como referência pro cálculo do
   spread de papéis IPCA+.
3. **Estoque por ativo** (`debentures.com.br/exploreosnd/consultaadados/
   estoque/estoqueporativo_r1.asp`) — tabela HTML antiga (ASP), estoque em
   R$ mil por Código.
4. **Características das emissões** (`debentures.com.br/exploreosnd/
   consultaadados/emissoesdedebentures/caracteristicas_e.asp`) — TSV com
   CNPJ e se a debênture é incentivada (Lei 12.431). Não varia por data —
   buscado uma vez por rodada, não uma vez por dia do backfill.

Fórmula do Spread (bps), preservada fielmente do script original:
- Papel com "Referência NTN-B" preenchida (qualquer indexador): `(1+taxa/100)/(1+taxa_da_ntnb_referenciada/100) - 1`, ×10000.
- CDI+ sem referência: o próprio valor da Taxa Indicativa (já é spread sobre o DI).
- IPCA+ sem referência: `(1+taxa/100)/(1+taxa_ntnb_de_vertice_mais_curto/100) - 1`, ×10000.

Estoque é dividido por 1000 (R$ mil → R$ milhões) e Duration por 252 (dias
úteis → anos) — mesmo ajuste do script original.

### Pivô pra API oficial da Anbima (24/07/2026)

Allan rodou o backfill de 2 anos localmente e descobriu que o boletim
`.xls` público da Anbima (fonte 1 e 2 acima) **só fica disponível pros
últimos ~5 dias úteis** — inviável pro histórico de 2 anos pedido. Ele já
tinha (ou já tinha se cadastrado pra ter) acesso à **API oficial da Anbima**
(`developers.anbima.com.br`, OAuth2 client_credentials — credenciais em
`ANBIMA_CLIENT_ID`/`ANBIMA_CLIENT_SECRET` no `.env`, cadastro em
`admin-developers.anbima.com.br/api-portal/user`).

Como o sandbox onde o Claude escreve código não alcança domínios da Anbima
(proxy allowlist bloqueia até `google.com`), a resposta real da API foi
validada por fora, com o Allan rodando `scripts/anbima_api_probe.py`
(script de diagnóstico, não faz parte do pipeline) e colando o JSON de
volta no chat — só depois disso o `fetch.py` foi reescrito, pra não
escrever um parser "no escuro" contra um formato só documentado em texto.
Confirmado: `data=2024-07-23` devolveu dado no mesmo formato da data mais
recente, tanto pra debêntures (940 linhas) quanto pra NTN-B (49 títulos,
incluindo NTN-B) — ~2 anos de profundidade histórica confirmados.

Dois endpoints substituem as fontes 1 e 2 (fontes 3 e 4 — estoque e
características, ambas debentures.com.br — continuam iguais, ver
`app/spreads/fetch.py`):
- `GET /feed/precos-indices/v1/debentures/mercado-secundario?data=AAAA-MM-DD`
  — substitui o boletim `.xls`. Uma linha por debênture, já com `grupo`
  ("DI SPREAD"/"IPCA SPREAD"), `taxa_indicativa`, `pu`, `percent_pu_par`,
  `duration` (d.u.), `referencia_ntnb` (quando aplicável) e `emissor`.
- `GET /feed/precos-indices/v1/titulos-publicos/mercado-secundario-TPF?data=AAAA-MM-DD`
  — substitui a aba "NTN-B" do `.xls` de curva. Traz **taxa discreta por
  vencimento** pra vários tipos de título (`tipo_titulo`: `"NTN-B"`, `"LTN"`,
  `"LFT"`, `"NTN-F"` — filtramos só `"NTN-B"`, confirmado via probe). Existe
  também um endpoint `/titulos-publicos/curvas-juros` que devolve parâmetros
  de curva paramétrica (Nelson-Siegel-Svensson, `b1..b4`/`l1`/`l2`) — **não
  usado**, porque o endpoint acima já dá a taxa pronta por vencimento (igual
  à aba antiga), evitando reimplementar a fórmula da curva à toa.

Cliente da API isolado em `app/spreads/anbima_api.py` (OAuth2 com cache de
token em memória de processo, renovação automática em 401). `fetch.py`
chama esse módulo em vez de baixar/parsear `.xls`.

**Correção de unidade encontrada nessa reescrita**: o script original do
Allan deixava o Spread de papéis CDI+ em pontos percentuais (ex.: `1.6`),
sem multiplicar por 100 — mas a coluna `DebentureSpread.spread` já é
documentada como "em bps" e o Allan pediu explicitamente que o spread
sempre apareça em bps. Sem a correção, CDI+ e IPCA+ ficariam em unidades
diferentes na mesma coluna (~160 vs ~16000) — corrigido em
`fetch_spreads()` (`spread = taxa_indicativa * 100` pro caso CDI+).

Efeito colateral: o campo `nome` da debênture agora vem do `emissor` da API
(nome do emissor, com marcadores de rodapé tipo `(*)`/`(**)`/`(#)` removidos
via regex) em vez de um "Nome" de papel mais curto que vinha do `.xls`
antigo — é o único dado equivalente disponível na API oficial.

`fetch_ntnb_rates()` foi removida (fundida em `fetch_spreads()`, já que os
dois endpoints novos são buscados juntos). `detect_latest_published_date()`
ficou mais simples: a API devolve a data mais recente direto quando chamada
sem o parâmetro `data`, então não precisa mais tentar dia por dia até achar
publicação.

**Ainda não confirmado**: se `fetch_estoque()`/`fetch_caracs()`
(debentures.com.br, fontes 3 e 4) têm a mesma limitação de retenção do
boletim antigo da Anbima pra datas históricas — Allan só confirmou o
problema no boletim `.xls`, não testou essas duas. Já degrada
graciosamente (dia sem estoque cruzado não é descartado, só fica com
`Estoque=None` — ver `fetch_spreads()`), então não bloqueia o backfill
mesmo se also tiverem retenção curta.

### Arquitetura no projeto

- `app/spreads/anbima_api.py` — cliente da API oficial da Anbima (OAuth2
  client_credentials, cache de token em memória de processo). Ver "Pivô pra
  API oficial da Anbima" acima.
- `app/spreads/fetch.py` — rede + parsing (`fetch_estoque`, `fetch_spreads`,
  `fetch_caracs`), devolvendo dataclasses Python (`SpreadRow`,
  `Caracteristicas`) em vez de escrever Excel direto — é isso que permite
  manter histórico. Também tem `compute_classe()` (ver abaixo) e
  `detect_latest_published_date()` (acha a data mais recente já publicada
  chamando a API sem o parâmetro `data`).
- `app/spreads/persist.py` — upsert no banco (`persist_day`,
  `persist_caracteristicas`). Idempotente: rodar de novo pro mesmo dia só
  atualiza aquele dia (chave única `codigo+data`), nunca duplica nem apaga
  dias já gravados.
- `app/spreads/queries.py` — todas as agregações que o dashboard consome
  (série histórica, KPIs, maiores variações/scatter, distribuição de
  variação %). **Testado com dados sintéticos** (não reais — ver nota de
  rede abaixo) via `SessionLocal` direto, incluindo um bug real encontrado
  e corrigido nessa etapa (seleção de snapshots de `movement_distribution`
  pegava as datas mais ANTIGAS em vez das mais RECENTES do histórico
  disponível — corrigido antes de entregar).
- `app/spreads_routes.py` — `APIRouter` com a página (`GET /spreads`) e a
  API (`GET /api/spreads/summary|series|movers|movement-distribution|
  search|debenture/{codigo}`). Registrado em `app/app.py` via
  `app.include_router(register_spreads_routes(require_user))` logo depois
  de `require_user` ser definido (evita import circular — o router recebe a
  dependência de autenticação por parâmetro em vez de importar de
  `app.py`).
- `app/models.py` — duas tabelas novas: `Debenture` (cadastro por Código,
  sempre sobrescrito com a versão mais recente — nome, indexador, CNPJ,
  incentivada, `classe`) e `DebentureSpread` (histórico de verdade, uma
  linha por Código+Data, `UniqueConstraint("codigo","data")`). Tabelas
  novas não precisam de `run_migrations()` — `Base.metadata.create_all`
  já cria sozinho.
- `scripts/fetch_debenture_spreads.py` — CLI (`python -m
  scripts.fetch_debenture_spreads [--start AAAA-MM-DD [--end AAAA-MM-DD]]`).
  Sem argumentos, detecta e captura só o último dia publicado (uso diário).
  Com `--start`, faz backfill dia útil por dia útil até `--end` (ou hoje).
  **Ainda não tem agendamento automático** (nem no `scheduler.py` local nem
  no GitHub Actions) — por ora é rodado manualmente; se o Allan quiser
  automatizar depois (ex.: 1x por dia, fora do horário de pregão), dá pra
  imitar o padrão de `scripts/run_once.py` + `.github/workflows/scrape.yml`.
- `templates/spreads.html` + `static/spreads.js` + trecho novo em
  `static/style.css` — dashboard: toggle de classe (pílulas, igual ao
  `.win-btn` do dashboard de notícias), toggle de período de comparação
  (1/5/21 dias úteis), cards de KPI, gráfico de linha (spread médio no
  tempo), scatter (variação × duration, com aberturas em laranja/
  fechamentos em preto/resto em cinza — réplica do gráfico do relatório
  semanal do Allan), gráfico de barras empilhadas (% da base que abriu/
  fechou spread), tabelas de maiores aberturas/fechamentos, busca +
  drill-down de um ativo específico (série própria dele). Usa Chart.js via
  CDN (`cdnjs.cloudflare.com`, único lugar do projeto que carrega uma lib
  de gráfico — o dashboard de notícias não tem gráfico nenhum).

### "Classe" — o filtro que nunca se mistura

Pedido explícito do Allan (23/07/2026): **"IPCA + Incentivadas" e "CDI +
Tradicionais" não são comparáveis entre si** (referências diferentes — NTN-B
vs DI) — todo gráfico/KPI do dashboard filtra por uma dessas duas classes,
nunca mostra as duas juntas. `compute_classe(indexador, incentivada)` em
`app/spreads/fetch.py`:
- `indexador == "IPCA +"` e incentivada = "Sim" → **"IPCA + Incentivadas"**
- `indexador == "CDI +"` e não incentivada → **"CDI + Tradicionais"**
- qualquer outra combinação (ex.: CDI+ incentivada, IPCA+ não incentivada —
  raras mas existem) → **"Outros"** (não aparece no toggle do dashboard,
  fica de fora das duas classes principais de propósito).

`classe` é recalculado em `persist_day` (quando `indexador` muda) e em
`persist_caracteristicas` (quando `incentivada`/CNPJ chegam, geralmente
depois, já que características são buscadas uma vez por rodada) — então
numa base nova, `classe` só fica correto depois que **as duas** rodam pelo
menos uma vez pro mesmo Código (a ordem no script já garante isso: spreads
de todos os dias primeiro, características por último).

### Ampliação 24/07/2026 — bases de comparação nomeadas + aba "Marcação Emissores"

Depois do primeiro backfill real (940 debêntures capturadas com sucesso),
Allan pediu uma leva de ajustes:

**Bases de comparação nomeadas.** As pílulas "1/5/21 dias" viraram
`d-1 / WoW / MoM / QoQ / SoS / YoY` (`queries.COMPARACAO_BASES`, em posições
no histórico: 1/5/21/63/126/252 — aproximação de 252 dias úteis/ano,
mesma convenção já usada em `Duration`). O front-end manda só o rótulo
(`base=WoW`); `_validar_base()` em `app/spreads_routes.py` traduz pra
posição — o front-end nunca sabe o número por trás. Uma notinha discreta
(`#nota-base-comparacao`) mostra a data real resolvida pra cada base (ex.:
"Base de comparação: WoW (16/07/2026)").

**Duration média ponderada por Estoque** substituiu o card "DATA DE
COMPARAÇÃO" (`_weighted_avg_duration` em `queries.py`) — cai pra média
simples (sem peso) se nenhuma linha da data tiver Estoque cruzado, e o
dashboard sinaliza esse fallback (`kpi-duration-tag`: "pond. estoque" vs.
"sem estoque") pra Allan não confundir com ponderada de verdade.

**Header "Dados até".** `#dados-ate` no topo da página, populado a partir
de `data_referencia` do próprio `/api/spreads/summary` (sem endpoint novo).

**Distribuição de variação (gráfico de barras empilhadas) reescrita.**
Antes: espalhava snapshots por igual ao longo de TODO o histórico
disponível. Agora (pedido explícito): o STEP entre snapshots é a própria
base de comparação selecionada — d-1 mostra os últimos 5 DIAS, MoM mostra
os últimos 5 MESES, etc. (`movement_distribution`, snapshot `i` = 
`dates_desc[i*dias_comparacao]` vs. `dates_desc[(i+1)*dias_comparacao]`).
Muito mais simples que a versão anterior.

**Aba "Marcação Emissores"** (segunda aba dentro de `/spreads`, troca de
painel via JS, sem rota nova) — visão por empresa em vez de por classe
inteira:
- Filtro de emissor (`Debenture.nome`, dropdown populado por
  `/api/spreads/emissores`) + filtro de classe próprio dessa aba (não
  compartilha estado com a Visão Geral) + toggle "nível emissor" (spread
  médio ponderado por Estoque entre os tickers do emissor naquela classe)
  vs. "nível ticker" (uma linha por ticker).
- Tabela acima do gráfico: tickers do emissor, indexador, classe,
  incentivada, Estoque mais recente (`emissor_tickers`).
- Gráfico de spread no tempo com uma linha extra, discreta e pontilhada,
  do spread médio de MERCADO da classe (sem filtrar por emissor) —
  reaproveita `time_series(classe)`, já existia.
- Sidebar de últimas notícias da empresa (`company_news`, junção com
  `Article.companies` — infraestrutura que já existia pro dashboard de
  notícias, `article_company`).

**Ligação emissor → empresa da cobertura** (`Debenture.company_id`, FK
solta pra `companies.id`, migração simples `ADD COLUMN` em `db.py`) — sem
isso a sidebar de notícias não tem o que buscar. `app/spreads/
company_match.py` faz a heurística (normaliza nome — remove acento,
pontuação, sufixos societários tipo S/A, LTDA, PARTICIPAÇÕES — e casa por
igualdade ou contenção de token; token único precisa ter 6+ caracteres pra
evitar falso positivo) e `scripts/match_debenture_issuers.py` é o CLI
(`--apply` grava; sem `--apply` só mostra o relatório). Roda DEPOIS do
backfill, não faz parte do pipeline diário — é revisão manual, imprecisa
de propósito conservador (prefere "sem match" a match errado). Quando
casa, também grava o nome do emissor como `CompanyAlias` novo (mesma
tabela que já alimenta o matching de notícias em `app/taxonomy.py`), então
passa a contar pra notícias também, não só pra essa aba. Allan revisa/
corrige em `/fontes` (CRUD de aliases já existia, não precisou UI nova).

**Cuidado de rota**: os 3 endpoints de emissor (`/api/spreads/emissor`,
`/series`, `/noticias`) recebem `nome` por QUERY STRING, não path param —
nomes de emissor reais têm "/" de verdade (ex. "... S/A"), e o Starlette
não casa "%2F" codificado dentro de um segmento de path por padrão (dava
404 — pego em teste com `TestClient` antes de entregar).

Tudo testado com dados sintéticos (`TestClient` + SQLite em memória,
incluindo um emissor com "/" no nome de propósito) — sem acesso de rede
pra validar contra o `/fontes` real do Allan (matching de empresa
existente), então o relatório do `match_debenture_issuers.py` deve ser
conferido por ele antes de rodar com `--apply`.

**Bug real encontrado e corrigido (24/07/2026): Estoque cruzando errado.**
Allan reportou muita debênture sem Estoque no backfill, o que não era
esperado. `scripts/estoque_probe.py` confirmou que `fetch_estoque()`
sozinho funciona perfeitamente (0 linhas vazias em 4 datas testadas,
inclusive 2 anos atrás) — o problema não era a fonte, era o CRUZAMENTO
com o código da Anbima. Allan avisou que bases scrapeadas do
debentures.com.br costumam vir com espaço sobrando (ex.: `"RISP14   "`).
`.strip()` (já usado antes) não cobre todo tipo de espaço "invisível" —
`​` (zero-width space) não é reconhecido como whitespace pelo Python
e passa reto pelo `strip()`, testado e confirmado. Trocado por
`_normalize_codigo()` (mantém só `[A-Za-z0-9]`, maiusculiza) aplicado nos
3 pontos onde um código de ativo é extraído (Anbima em `fetch_spreads`,
"Código" em `fetch_estoque` e em `fetch_caracs`) — muito mais robusto que
tentar enumerar toda variação de espaço possível. Reproduzido com teste
sintético (código sujo com espaço + zero-width space) antes de confirmar
a correção.

### O que o relatório semanal do Allan tem que este dashboard NÃO cobre

Allan é analista do time de Renda Fixa do Itaú BBA e anexou o relatório
semanal do time (`RENDA FIXA — 20/07/2026`) como referência visual. Vários
gráficos de lá **não são cobertos** por este módulo porque dependem de
fontes de dado completamente diferentes das 4 que o script original usa:
- **Spread médio por RATING** (AAA / Total ex-AAA / Total) — precisaria de
  rating por debênture/emissor cruzado; não existe isso hoje no projeto
  (o mapeamento mais próximo, `scripts/mapear_ratings_2026.py`, produz
  AÇÕES de rating por emissor pro mercado inteiro, não um rating atual por
  Código de debênture — cruzar os dois é trabalho futuro, não feito aqui).
- **Abertura de spread por SETOR** — precisaria de setor por debênture;
  hoje só temos setor pras ~96 empresas da cobertura (via `Company.sector`)
  e nenhum link automático entre `Debenture.codigo`/`nome` e `Company` foi
  construído nesta entrega (ficou de fora de propósito — ligar por
  keyword/nome é frágil sem dado real pra calibrar contra, ver nota de
  rede abaixo). Se quiser essa visão, o próximo passo é construir esse
  cruzamento e testar contra uma base real.
- **Volume negociado (B3)** e **Mercado primário (CVM — ofertas registradas/
  aguardando bookbuilding)** — fontes de dado inteiramente diferentes
  (B3 e CVM, não Anbima/debentures.com.br), não implementadas.
- **Ações de rating** — já existe em outro lugar do app (scrapers de S&P/
  Moody's/Fitch, ver seção de fontes acima), não faz parte deste módulo.

O que FOI replicado do relatório: gráfico de spread médio no tempo (sem
quebra por rating — só "Total" da classe), gráfico de distribuição de
variação % da base, scatter de variação × duration com aberturas/
fechamentos destacados, e as tabelas de maiores aberturas/fechamentos.

### NOTA DE REDE — nada disto rodou de ponta a ponta ainda

As 4 URLs (Anbima ×2, debentures.com.br ×2) **não estão na allowlist do
sandbox** onde todo este código foi escrito — até `google.com` volta
`403 Forbidden` com `X-Proxy-Error: blocked-by-allowlist` de dentro do
sandbox. Validações feitas sem essa rede:
- Confirmei (via ferramenta de busca externa, fora do sandbox de código)
  que a URL de estoque responde e devolve uma tabela real com "Emissor"/
  códigos reais (VALE, PETR) pro dia 22/07/2026.
- Testei toda a camada de banco/consultas/rotas/template com **dados
  sintéticos** inseridos direto no banco (não vieram da rede real) — isso
  pegou e corrigiu um bug real de lógica em `movement_distribution`, então
  valeu a pena, mas **não substitui rodar contra dado de verdade**.
- **Nunca rodei `fetch_estoque`/`fetch_ntnb_rates`/`fetch_spreads`/
  `fetch_caracs` contra a rede real** — a lógica foi preservada o mais
  fiel possível ao script do Allan (que ele já usa/roda), mas parsing de
  Excel/HTML de terceiro é sempre um risco (colunas podem estar em posição
  ligeiramente diferente do esperado, etc.).

**Antes do backfill de 2 anos, rode primeiro pra 1 dia só**:
```
python -m scripts.fetch_debenture_spreads
```
Confira o log — se dessem erro de parsing (`KeyError`, `IndexError`,
coluna não encontrada), me manda a mensagem completa que eu ajusto. Só
depois disso rode o backfill de verdade:
```
python -m scripts.fetch_debenture_spreads --start 2024-07-23
```
(Allan pediu histórico de ~2 anos, 23/07/2026 → 23/07/2024). **É lento**:
cada dia útil processado faz 3-4 requisições HTTP + parsing de planilhas de
milhares de linhas — ~500 dias úteis não é questão de minutos. Seguro de
interromper (Ctrl+C) e rodar de novo com o mesmo `--start`: dias já
gravados não são reprocessados desnecessariamente (mas também não há
"resume automático" — ele tenta todos os dias do range de novo; se isso
for um problema real na prática, dá pra adicionar um "pula dia que já
tem dado" depois).

### Pendências conhecidas (falar com o Allan antes de assumir)

- `requirements.txt` ganhou `xlrd>=2.0` (necessário pra ler `.xls`, formato
  antigo que a Anbima usa) — rodar `pip install -r requirements.txt` de
  novo antes do backfill.
- Sem agendamento automático ainda (rodar manualmente por enquanto, como
  combinado — "podemos trabalhar localmente e subir depois").
- Sem link Debênture ↔ Company/Setor (rating e setor-por-papel ficaram de
  fora, ver seção acima).
- Vercel/GitHub Actions: nada foi mexido no deploy pra este módulo ainda
  (nem novo cron, nem novo passo no workflow) — combinado que a fase 1 é
  local.

> As duas seções acima ("NOTA DE REDE" e itens de rede/xlrd nas
> "Pendências") são do desenho inicial, pré-pivot pra API oficial da
> Anbima — hoje já rodou o backfill real de 2 anos (1.663 debêntures,
> ~568 mil linhas de spread, 2024-07-23 a 2026-07-23) e o dashboard usa
> dado de verdade. Ficaram registradas por histórico, não representam o
> estado atual.

### Ampliação 24/07/2026 (3ª rodada) — bug do Chart.js, aba "Emissores", totalizador, busca multi-select

**Bug: nenhum gráfico aparecia.** Allan reportou com screenshot; hipótese
inicial (bloqueio de rede corporativa) foi descartada por ele mesmo
("Não estou na rede do banco nesse teste local"). Console do navegador
mostrou a causa real: `<script src="https://cdnjs.cloudflare.com/ajax/
libs/Chart.js/4.4.4/chart.umd.min.js">` devolvia **404** — o path do
cdnjs é *case-sensitive* e o nome certo é `chart.js` minúsculo, não
`Chart.js`. Como o script nunca carregava, toda chamada a `new Chart(...)`
falhava com `ReferenceError: Chart is not defined`. Corrigido trocando
pra URL oficial documentada do jsDelivr:
`https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js`
(`templates/spreads.html`). **Lição**: sempre usar a URL exata da doc
oficial da lib pra CDN, não "adivinhar" capitalização.

**Aba renomeada**: "Marcação Emissores" → **"Emissores"** — Allan avisou
que essa aba vai ganhar dado de negociações recentes no futuro além de
spread, então o nome antigo parava de fazer sentido. Nome curto e neutro
o suficiente pra caber conteúdo futuro; só o rótulo mudou
(`data-secao="emissores"` já era esse desde a criação, não precisou
mexer em rota/JS além do texto do botão).

**Totalizador de Estoque** na tabela de tickers do emissor (pedido do
Allan) — `<tfoot>` no template com uma linha "Total", populada em
`loadEmissorTabela()` (`static/spreads.js`) somando `t.estoque` de todos
os tickers retornados (ignora `null`); mostra "—" se nenhum ticker tiver
estoque.

**Busca por nome + seleção múltipla de emissores** — trocado o
`<select>` único por um campo de busca (`#emissor-busca`) com dropdown
de resultados (mesmo padrão visual do `#busca-ativo` da Visão Geral) e
chips removíveis (`#emissor-chips`) pros emissores selecionados. Exigiu
mudança em toda a cadeia:
- `queries.py`: `emissor_tickers`, `emissor_series`,
  `companies_for_emissores` (renomeada de `company_for_emissor`) e
  `company_news` agora recebem **listas** de nomes/ids em vez de um só.
  Em `emissor_series(nivel="emissor")`, cada emissor selecionado vira
  uma linha própria no gráfico (agregação por `(nome, data)`).
- `spreads_routes.py`: `/api/spreads/emissor(/series|/noticias)` usam
  `nome: list[str] = Query(...)` — múltiplos `?nome=A&nome=B` na query
  string (um só emissor é só uma lista de tamanho 1, não quebra nada
  que já existia).
- `spreads.js`: `fetchJSON()` reescrito pra aceitar valores em lista
  (`Array.isArray(v)` → `usp.append` repetido); estado
  `currentEmissores` agora é array; resposta de notícias mudou de
  `data.empresa` (singular) pra `data.empresas` (dict por nome).

Testado com `TestClient` contra uma **cópia** do banco real (ver nota
abaixo) selecionando 2 emissores ao mesmo tempo — tabela combinada,
totalizador batendo com a soma manual, gráfico com uma linha por
emissor, notícias agregadas das duas empresas. Sem regressão nos
endpoints de Visão Geral (`summary` com base "MoM" testado também).

### Ampliação 24/07/2026 (4ª rodada) — negócio a negócio da B3 (DEB/CRI/CRA) na aba Emissores

Allan pediu pra trazer as **últimas negociações na B3** pra dentro da aba
Emissores (é inclusive por isso que a aba deixou de se chamar "Marcação
Emissores" na rodada anterior — ele avisou que isso vinha por aí). Pediu
"a tabela negócio a negócio" especificamente, com foco em Debêntures, CRI
e CRA, atualizada a cada 15 min (cadência real da B3 pra essa tabela).

**Fonte**: `https://arquivos.b3.com.br/bdi/tabelas` — é uma SPA (o HTML
puro não tem nada; sem navegador com JS, `web_fetch` só devolve "You need
to enable JavaScript"). Descoberta usando o Chrome MCP pra navegar,
selecionar "Renda fixa" → "Negócio a negócio" no seletor de tabela, e
inspecionar a rede: o front-end chama

    POST https://arquivos.b3.com.br/bdi/table/Trade/{início}/{fim}/{página}/{tamanho}

sem autenticação, sem corpo — `{início}`/`{fim}` em `AAAA-MM-DD`,
`{página}` 1-based, `{tamanho}` registros por página (testado até 1000 de
forma confiável; 2000 devolveu corpo vazio numa chamada manual, não usar
tamanho maior que 1000). Devolve JSON com `table.values` (lista de listas,
uma por negócio, campos por ÍNDICE fixo — ver docstring de
`app/spreads/b3_trades.py` pro layout completo) e `table.pageCount`.
Testado contra o dia corrente (24/07/2026) e contra histórico de até 2
anos atrás (2024-07-23) — a fonte tem retenção longa, ao contrário do
boletim `.xls` da Anbima que motivou o pivot pra API na 1ª rodada.

`InstrumentType` tem BEM mais valores do que o pedido (`CFF`, `CDCA`,
`COE`, `CPR`, `LF`, `LFSN`...) — filtramos só `DEB`/`CRI`/`CRA`
(`b3_trades.INSTRUMENT_TYPES`). Cada dia tem ~15 mil negócios no total,
~700 já filtrando só os 3 tipos pedidos.

**Escopo, decidido com o Allan antes de construir** (tinha ambiguidade
real o suficiente pra valer perguntar em vez de assumir):
- **Filtro**: a tabelinha fica restrita aos tickers do(s) emissor(es)
  selecionado(s) na busca — MESMA lógica que já filtra a tabela de
  tickers (`Debenture.nome.in_(nomes_emissor)` → lista de códigos →
  `NegocioB3.codigo.in_(codigos)`). Não é um feed geral do mercado.
  Consequência direta: hoje só aparece coisa pra **DEB** de verdade — CRI
  e CRA não têm `Debenture`/emissor ligado no cadastro (não são
  debêntures), então nunca vão casar com nenhum emissor buscado até
  ganharem seu próprio cadastro (fora de escopo por ora).
- **Atualização**: salva no banco a cada 15 min via um segundo job no
  `app/scheduler.py` (`b3_trades_scan`, ao lado do `news_scan` que já
  existia) — não busca ao vivo toda vez que a aba abre. Mesma ressalva
  de sempre: só roda localmente (`CLOUD_MODE` desligado); em produção
  seria GitHub Actions, não implementado ainda (mesma pendência que o
  módulo de spreads já tinha).

**Peças novas**:
- `NegocioB3` (`app/models.py`) — uma linha por negócio, chave de dedupe
  é `trade_code` (id que a própria B3 dá pro negócio, ex. `"#1009622879"`)
  porque a fonte reenvia o dia inteiro a cada consulta, não só o que
  mudou desde a última vez.
- `app/spreads/b3_trades.py` — `fetch_trades(start, end)`: pagina a API e
  já filtra DEB/CRI/CRA, normaliza o ticker com o mesmo `_normalize_codigo`
  do módulo de spreads (reaproveitado, não duplicado — mesma robustez
  contra espaço/zero-width space da 2ª rodada).
- `persist.save_negocios_b3()` — grava só negócio novo (dedupe por
  `trade_code`), idempotente.
- `queries.emissor_trades(db, nomes_emissor, limit=30)` — junta
  `Debenture.codigo` dos emissores selecionados com `NegocioB3.codigo`,
  ordenado do negócio mais recente pro mais antigo.
- `GET /api/spreads/emissor/negociacoes?nome=...` (lista, mesma seleção
  múltipla da rodada anterior).
- `scripts/fetch_b3_trades.py` — CLI pra rodar manualmente/backfill
  pontual (`--start`/`--end`); sem --start roda só o dia de hoje (é o que
  o scheduler chama). Ao contrário do backfill de spreads, **não** veio
  com 2 anos de histórico por padrão — Allan pediu "últimas negociações"
  (uso corrente), e o volume é grande o suficiente (~700 linhas/dia só
  dos 3 tipos) pra não valer a pena um backfill longo sem pedido
  explícito. Se quiser histórico de um período, é só rodar com
  `--start`/`--end`.

Testado com dado sintético (payload no formato exato capturado da B3, TCP
mockado — sandbox não alcança `arquivos.b3.com.br`, mesma restrição de
allowlist da Anbima) cobrindo: filtro de InstrumentType, normalização de
ticker sujo (espaço + zero-width space), dedupe por `trade_code` rodando
duas vezes, e o endpoint de verdade via `TestClient` contra uma cópia do
banco real (join batendo com o emissor certo). **A chamada real
`fetch_trades` → B3 nunca rodou de ponta a ponta** — só a chamada crua
(`fetch` no console do navegador) foi validada contra a B3 de verdade; o
parsing em Python roda contra um payload sintético no mesmo formato.
Rode `python -m scripts.fetch_b3_trades` (sem argumentos, só hoje) antes
de confiar no scheduler automático.

**Bug real encontrado no primeiro run do Allan** (mesmo dia, 24/07/2026):
`UNIQUE constraint failed: negocios_b3.trade_code` capturando o dia
inteiro (10.780 negócios, 15 páginas). Causa: o mesmo `trade_code` pode
aparecer mais de uma vez DENTRO da mesma chamada de `fetch_trades` (não só
entre uma chamada e a próxima, que já era tratado) -- como a captura pagina
~15 páginas em sequência e a B3 segue recebendo negócios novos durante
esse tempo (a cada 15 min ela reprocessa o dia inteiro), um negócio pode
"empurrar" outro de página e aparecer duplicado entre duas páginas da
mesma consulta. `save_negocios_b3` só deduplicava contra o que já existia
no banco, não dentro do próprio lote recebido -- corrigido deduplicando
primeiro por `trade_code` dentro do lote (fica a última ocorrência) antes
de checar contra o banco. Reproduzido com um lote sintético com
`trade_code` repetido antes de confirmar a correção.

**Nota operacional — banco real via mount do OneDrive**: uma tentativa
de abrir sessão de teste direto no `data/credit_monitor.db` real (pelo
mount do sandbox) deu `disk I/O error` no commit e deixou um
`credit_monitor.db-journal` órfão. Comparei os dados por uma conexão
somente-leitura (`mode=ro&immutable=1`) e confirmaram-se intactos (1.663
debêntures, 2 usuários) — o mount não sustenta o locking que o SQLite
precisa pra escrever/fazer rollback de journal com segurança, não é
corrupção de dado. O journal órfão deve ser limpo automaticamente na
próxima vez que o Windows do Allan abrir o banco normalmente (recovery
padrão do SQLite). **Lição**: nunca escrever no `.db` real pelo mount do
sandbox — pra testar com dado de verdade, copiar primeiro
(`sqlite3.connect(...).backup(...)`) e testar na cópia.
