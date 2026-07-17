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

## Coisas pra lembrar depois

- Se adicionar/mudar fonte, empresa ou setor no seu computador local, isso
  **não** aparece sozinho no site hospedado — são bancos diferentes agora.
  Combine comigo qual banco vira a fonte de verdade daqui pra frente
  (recomendo: o Supabase, já que é o que várias pessoas vão usar).
- O `Setores.xlsx` e o `.env` nunca sobem pro GitHub (ficam de fora de
  propósito) — se precisar atualizar a planilha de cobertura no Supabase
  depois, repete a Parte 2 (rodar o seed local apontando pro Supabase).
