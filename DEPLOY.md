# Colocar o Monitoramento online — passo a passo

Este guia parte do princípio de que você nunca fez nada disso antes. Vamos
usar três serviços, todos gratuitos:

- **Supabase** — o banco de dados (substitui o arquivo local `data/credit_monitor.db`)
- **GitHub** — guarda o código e roda o robô de coleta a cada 5 minutos
- **Vercel** — hospeda o site (o dashboard que você acessa no navegador)

Faça os passos NA ORDEM. Cada um depende do anterior. Reserve uns 40-60
minutos com calma.

---

## Parte 1 — Supabase (banco de dados)

1. Acesse **https://supabase.com** e crie uma conta (pode entrar com o
   Google).
2. Clique em **"New project"**. Dê um nome (ex.: `monitoramento-noticias`),
   escolha uma **senha do banco** (guarde essa senha em um lugar seguro —
   você vai precisar dela daqui a pouco) e escolha a região mais próxima
   do Brasil (`South America (São Paulo)` se aparecer).
3. Espere uns 2 minutos o projeto ser criado.
4. No menu do projeto, procure **"Connect"** (ou **Project Settings →
   Database**). Você vai ver algumas connection strings diferentes.
   **Copie a que estiver marcada como "Connection pooling" / "Transaction
   mode"** (não a "Direct connection") — é essa que o site vai usar.
   Ela se parece com:
   ```
   postgresql://postgres.xxxxxxxxxxxx:[YOUR-PASSWORD]@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
   ```
5. Troque `[YOUR-PASSWORD]` pela senha do banco que você escolheu no
   passo 2. Guarde essa string completa — é o seu `DATABASE_URL`. Vamos
   usá-la várias vezes daqui pra frente.

   **Importante:** troque o começo de `postgresql://` para
   `postgresql+psycopg://` (o programa precisa desse prefixo pra saber
   qual driver usar). Fica assim:
   ```
   postgresql+psycopg://postgres.xxxxxxxxxxxx:SUASENHA@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
   ```

---

## Parte 2 — Criar o banco pela primeira vez (do seu computador)

Isso só precisa ser feito **uma vez**, do seu computador, pra criar as
tabelas e importar sua planilha de cobertura no Supabase.

1. Abra a pasta do projeto (`credit_monitor`) e ache o arquivo `.env`
   (se não existir, copie `.env.example` e renomeie pra `.env`).
2. Abra o `.env` com o Bloco de Notas e adicione/edite estas linhas:
   ```
   DATABASE_URL=postgresql+psycopg://postgres.xxxx:SUASENHA@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
   BOOTSTRAP_ADMIN_PASSWORD=escolha-uma-senha-forte-aqui
   ```
   (a segunda linha é importante — sem ela, sua conta de admin no banco
   novo nasce com a senha padrão "troque-esta-senha", que não pode ficar
   valendo num banco que vai estar na internet).
3. Feche o `.env` e salve.
4. Rode **"Abrir Monitoramento.bat"** — na primeira execução ele já roda
   `python -m scripts.seed`, que vai criar as tabelas e importar tudo pro
   Supabase.
5. Espere o programa abrir no navegador normalmente. Se aparecer sua
   tela de login pedindo pra entrar com `allancruz078@gmail.com` e a
   senha que você definiu no passo 2, deu certo — o banco na nuvem já tem
   tudo.
6. **Agora apague ou comente a linha `DATABASE_URL=...` do seu `.env`**
   (coloque um `#` na frente: `#DATABASE_URL=...`) e feche o programa.
   Isso faz o programa no seu computador voltar a usar o banco local de
   sempre — o Supabase é só pro site hospedado, não precisa ficar ligado
   direto do seu PC.

---

## Parte 3 — GitHub (código + robô de coleta)

1. Acesse **https://github.com** e crie uma conta, se ainda não tiver.
2. Clique em **"New repository"**. Dê um nome (ex.: `monitoramento-noticias`).
   Marque como **Public** (precisa ser público pra rodar o robô de graça
   sem limite de minutos — o código fica visível pra qualquer um, mas
   nenhuma senha/dado sensível vai junto, isso já foi configurado).
3. **Não** marque "Add a README" (a pasta já tem os arquivos prontos).
4. Depois de criar, o GitHub mostra uns comandos pra "subir" o código.
   Isso precisa do **Git** instalado no seu computador — se não tiver,
   baixe em https://git-scm.com/downloads (instalação padrão, next-next-next).
5. Abra o Prompt de Comando dentro da pasta `credit_monitor` (Shift +
   botão direito na pasta → "Abrir janela do PowerShell aqui", ou digite
   `cmd` na barra de endereço do Explorer) e rode, um de cada vez:
   ```
   git init
   git add .
   git commit -m "Primeira versao"
   git branch -M main
   git remote add origin https://github.com/SEU-USUARIO/monitoramento-noticias.git
   git push -u origin main
   ```
   (troque `SEU-USUARIO`/o nome do repositório pelo que você criou — o
   próprio GitHub mostra esse comando exato na tela depois de criar o
   repositório, é só copiar de lá em vez de digitar).
   Na primeira vez, ele vai pedir pra você logar no GitHub — segue o que
   aparecer na tela.
6. Confira no site do GitHub se os arquivos apareceram no repositório.

### Criar o token de acesso (pro botão "Forçar atualização" funcionar)

1. No GitHub, clique na sua foto (canto superior direito) → **Settings**.
2. Vá em **Developer settings** (no fim do menu da esquerda) → **Personal
   access tokens** → **Fine-grained tokens** → **Generate new token**.
3. Dê um nome (ex.: "monitoramento-vercel"), escolha o repositório que
   você criou em "Repository access", e em "Permissions" marque
   **Actions: Read and write**.
4. Clique em **Generate token** e **copie o token na hora** (ele só
   aparece uma vez). Guarde num lugar seguro — vamos usar no Vercel daqui
   a pouco.

### Configurar o segredo do banco no GitHub

1. No repositório, vá em **Settings → Secrets and variables → Actions**.
2. Clique em **New repository secret**.
3. Nome: `DATABASE_URL`. Valor: a mesma connection string da Parte 1
   (com `postgresql+psycopg://` e sua senha).
4. Salve.

A partir daqui, o robô já está configurado pra rodar a cada 5 minutos —
mas só vai começar a rodar de fato depois que o workflow for ativado (ele
ativa sozinho assim que detecta atividade no repositório; se quiser
testar na hora, vá na aba **Actions** do repositório, clique no workflow
"Varredura de noticias" e depois em **Run workflow**).

---

## Parte 4 — Vercel (o site)

1. Acesse **https://vercel.com** e crie uma conta — **entre usando sua
   conta do GitHub** (mais simples, já conecta os dois automaticamente).
2. Clique em **"Add New" → "Project"**.
3. Selecione o repositório `monitoramento-noticias` que você criou.
4. Antes de clicar em "Deploy", abra **"Environment Variables"** e
   adicione, uma de cada vez (nome à esquerda, valor à direita):

   | Nome | Valor |
   |---|---|
   | `DATABASE_URL` | a connection string do Supabase (Parte 1) |
   | `GITHUB_TOKEN` | o token que você gerou na Parte 3 |
   | `GITHUB_REPO` | `SEU-USUARIO/monitoramento-noticias` |
   | `SESSION_TTL_MINUTES` | `480` (opcional, é o padrão) |

5. Clique em **Deploy**. Espere alguns minutos.
6. Quando terminar, clique em **"Visit"** — deve abrir a tela de login do
   Monitoramento, agora com um endereço tipo
   `https://monitoramento-noticias.vercel.app`, acessível de qualquer
   lugar.
7. Entre com `allancruz078@gmail.com` e a senha que você definiu na
   Parte 2.

### Cadastrando outras pessoas

Depois de entrar, vá em **Administração** e cadastre os outros usuários
por lá (o cadastro público por e-mail também funciona, mas precisa de um
servidor de e-mail configurado — se quiser isso, me avisa que a gente
configura o SMTP também).

---

## Se algo não funcionar de primeira

Deploy na nuvem quase nunca funciona 100% no primeiro clique — é normal.
Me manda:
- O erro exato que aparecer na tela do Vercel (aba **"Deployments" →
  clique no deploy → "Logs"**), ou
- O que aparecer na aba **Actions** do GitHub se o robô não estiver
  rodando.

Com o log exato eu consigo corrigir rápido — sem ele, fico advinhando.

---

## Parte 5 — Deixar a atualização pontual de verdade (cron-job.org)

O agendamento nativo do GitHub Actions (`schedule:` no workflow) não é
pontual — o próprio GitHub não garante horário exato, e pode atrasar
bastante em horário de pico. Por isso a varredura de 5 em 5 minutos de
verdade é acionada por um serviço externo gratuito, o **cron-job.org**
(permite até 1x por minuto, de graça, sem cartão de crédito).

1. Gere uma senha/segredo aleatório qualquer (pode ser uma frase longa
   sem espaços) — vai ser o `CRON_SECRET`.
2. No Vercel, vá em **Settings → Environment Variables** e adicione:
   `CRON_SECRET` = o segredo que você gerou. Redeploy depois de salvar.
3. Acesse **https://cron-job.org**, crie uma conta gratuita.
4. Clique em **"Create cronjob"**.
5. Em **URL**, coloque:
   ```
   https://monitoramento-noticias.vercel.app/api/cron-trigger
   ```
6. Em **Execution schedule**, escolha **"Every 5 minutes"**.
7. Procure a aba/seção **"Advanced"** (ou "Headers"/"Request method") e
   configure:
   - **Request method**: `POST`
   - Adicione um **header customizado**: nome `X-Cron-Secret`, valor = o
     mesmo segredo do passo 1/2.
8. Salve. O primeiro disparo já deve acionar uma varredura — confira a
   aba **Actions** do GitHub pra ver se apareceu uma execução nova.

O `schedule:` do GitHub Actions continua ativo como reserva (roda 1x por
hora, caso o cron-job.org fique fora do ar por algum motivo) — não
precisa mexer nele.

---

## Parte 6 — Módulo Spreads de Debêntures (site + B3 a cada 15 min)

O módulo de Spreads (aba "Spreads" do dashboard: Visão Geral + Emissores)
foi construído inteiramente rodando só no seu computador — nunca subiu
pro site hospedado. Esta parte sobe ele: passa a atualizar sozinho, todo
dia, sem você precisar rodar nada manualmente.

### 1. Subir o código pro GitHub

O módulo inteiro (e mais uma leva de correções recentes) ainda está só no
seu computador, sem nunca ter sido commitado. Abra o PowerShell na pasta
`credit_monitor` e rode, um de cada vez:

```
git add .env.example "Abrir Monitoramento.bat" CLAUDE.md app/app.py app/auth.py app/db.py app/models.py app/scheduler.py requirements.txt static/app.js static/style.css templates/admin.html templates/base.html templates/dashboard.html templates/signup.html
git add .github/workflows/b3_trades.yml .github/workflows/spreads_daily.yml .github/workflows/spreads_verify.yml app/spreads app/spreads_routes.py scripts/backfill_b3_trade_spreads.py scripts/fetch_b3_trades.py scripts/fetch_debenture_spreads.py scripts/verify_spreads_updated.py static/spreads.js templates/spreads.html
git status
```

Confira no `git status` que só apareceram esses arquivos (nada dentro de
`data/`, nem `Mapear Ratings 2026.bat`, nem os scripts soltos de
diagnóstico/ratings — esses são de outra finalidade e não devem subir).
Se estiver tudo certo:

```
git commit -m "Modulo Spreads de Debentures + negocio a negocio B3 (deploy inicial)"
git push
```

O Vercel redeploya sozinho assim que detecta o push (não precisa fazer
nada lá).

### 2. Segredos novos no GitHub (Settings → Secrets and variables → Actions)

O módulo Spreads bate na API oficial da Anbima, que precisa de
credenciais próprias (diferentes do `DATABASE_URL`, que já existe desde a
Parte 3):

| Nome | Valor |
|---|---|
| `ANBIMA_CLIENT_ID` | do seu `.env` local |
| `ANBIMA_CLIENT_SECRET` | do seu `.env` local |

Não precisa adicionar nada no Vercel — quem bate na Anbima é sempre o
GitHub Actions, nunca o site em si.

### 3. Os três workflows novos (já vêm prontos no código)

- **`spreads_daily.yml`** — captura os spreads de debêntures 1x por dia,
  às 21h (horário de Brasília). Não precisa de cron-job.org: uma vez por
  dia não exige a mesma pontualidade do `schedule:` nativo do GitHub.
- **`spreads_verify.yml`** — roda 1h depois (22h BRT) só pra CONFERIR que
  a captura de fato trouxe o dado mais recente (pergunta pra própria
  Anbima qual foi o último dia publicado e compara com o banco). Se a
  captura ficou pra trás por algum motivo, esse workflow **falha de
  propósito** — o GitHub manda e-mail de notificação de falha sozinho,
  sem precisar configurar nada a mais.
- **`b3_trades.yml`** — negócio a negócio da B3, precisa de verdade dos
  15 em 15 minutos (só durante o pregão). Igual à varredura de notícias,
  o `schedule:` nativo do GitHub não é pontual o bastante — precisa do
  relay externo (próximo passo).

### 4. Segundo cronjob no cron-job.org (pro negócio a negócio de 15 em 15 min)

Você já tem um cronjob lá pra notícias (Parte 5) — duplique ele:

1. Entre em **cron-job.org** → **"Create cronjob"**.
2. **URL**:
   ```
   https://SEU-SITE.vercel.app/api/cron-trigger?job=b3_trades
   ```
3. **Execution schedule**: "Every 15 minutes".
4. Em **Advanced**: **Request method** `POST`, header customizado
   `X-Cron-Secret` = o MESMO segredo já configurado no Vercel (`CRON_SECRET`,
   da Parte 5 — não precisa criar outro).
5. Salve. Pode deixar rodando 24 horas por dia sem se preocupar com
   horário de mercado — o próprio servidor já checa se o pregão está
   aberto (9h-18h BRT, dia útil) e só aciona a captura de verdade dentro
   desse horário; fora disso, o disparo do cron-job.org não faz nada.

`spreads_daily` e `spreads_verify` NÃO precisam de nada no cron-job.org —
rodam sozinhos pelo `schedule:` nativo do GitHub, já que 1x/dia não exige
pontualidade.

### 5. Primeira carga de dado (o Supabase começa vazio só de spread)

**Importante — isto NÃO é criar nada do zero.** É o MESMO projeto
Supabase e a MESMA connection string que você já usa desde que o
dashboard de notícias foi pro ar (Parte 1) — as tabelas de notícias,
usuários, fontes etc. continuam exatamente como estão, ninguém mexe
nelas. A única coisa que falta é que esse banco ainda não tem NENHUMA
linha de spread/debênture (as tabelas novas desse módulo: `Debenture`,
`DebentureSpread`, `NegocioB3`, `NtnbReferencia`). Elas são criadas
sozinhas, automaticamente, na primeira vez que qualquer script do módulo
Spreads roda contra esse banco — não precisa rodar `scripts.seed` de
novo, não precisa abrir "Abrir Monitoramento.bat", não precisa mexer no
Supabase pela interface web nenhuma.

Passo a passo:

1. Ache a connection string do Supabase que você já usa — está salva
   nas variáveis de ambiente do Vercel (**Settings → Environment
   Variables → `DATABASE_URL`**), copie de lá.
2. No seu `.env` local, descomente/cole essa MESMA linha
   `DATABASE_URL=postgresql+psycopg://...` temporariamente (é o mesmo
   truque da Parte 2, passo 6, só que ao contrário — lá você comentou
   essa linha pra voltar a usar o banco local; agora só descomenta de
   novo).
3. Rode só este comando (nada mais):
   ```
   python -m scripts.fetch_debenture_spreads --start 2026-04-27
   ```
   (3 meses antes de hoje — ajuste a data se rodar em outro dia.) Ele
   cria as tabelas que faltam sozinho e carrega os últimos 3 meses de
   spread — os últimos 2 anos que você tem localmente ficam de fora de
   propósito (decisão já tomada, pra começar mais leve).
4. Comente a linha `DATABASE_URL=` de novo no seu `.env` (mesmo passo
   de sempre) pra voltar a usar o banco local no seu computador.

Não precisa fazer nada disso pro negócio a negócio da B3 — ele já começa a
acumular sozinho a partir do primeiro disparo do cron-job.org. Se quiser
também um pouco de histórico recente ali, é opcional (faça antes do passo
4 acima, com o `DATABASE_URL` ainda apontando pro Supabase):

```
python -m scripts.fetch_b3_trades --start 2026-07-20
```

### 6. Conferir que funcionou

- Aba **Actions** do GitHub: os três workflows (`spreads_daily`,
  `spreads_verify`, `b3_trades`) devem aparecer na lista — clique em cada
  um e "Run workflow" pra testar na hora, sem esperar o horário.
- No site, abra a aba **Spreads** → **Emissores**, escolha um emissor
  qualquer e confira se os cards de taxa e a tabela de negociações
  aparecem.

---

## Coisas pra lembrar depois

- Se adicionar/mudar fonte, empresa ou setor no seu computador local, isso
  **não** aparece sozinho no site hospedado — são bancos diferentes agora.
  Combine comigo qual banco vira a fonte de verdade daqui pra frente
  (recomendo: o Supabase, já que é o que várias pessoas vão usar).
- O `Setores.xlsx` e o `.env` nunca sobem pro GitHub (ficam de fora de
  propósito) — se precisar atualizar a planilha de cobertura no Supabase
  depois, repete a Parte 2 (rodar o seed local apontando pro Supabase).
