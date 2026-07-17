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

- Cadastro (`/cadastro`) cria usuário com `email_confirmed=False` e envia
  (ou loga, se SMTP não configurado) um link de confirmação válido por 48h.
- Sem SMTP configurado no `.env`, o link aparece no **log do servidor**
  (stdout) — suficiente para testar localmente. Para produção, configure
  `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD` (Gmail com senha de app, ou um
  serviço como Resend/SendGrid, que têm free tier).
- Usuários criados pelo admin (`/admin`) já entram confirmados.
- Sessão expira em `SESSION_TTL_MINUTES` (default 480 = 8h), configurável
  em `/admin`. Painel admin lista sessões ativas (usuário, início, última
  atividade, expiração, IP) com botão "Encerrar".
- `role` (`admin`/`user`) controla quem vê `/admin`. O primeiro admin é
  criado pelo seed a partir de `BOOTSTRAP_ADMIN_EMAIL`.

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
