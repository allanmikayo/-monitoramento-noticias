# Postgres próprio na OCI — runbook

Substitui o Supabase por uma VM Always Free da Oracle Cloud. Custo: **US$ 0/mês**,
sem prazo de validade.

## Por que este desenho

O banco tem ~111 MB e cresce ~76 MB/ano. A VM gratuita da OCI dá **2 OCPU ARM
e 12 GB de RAM** — o banco inteiro cabe em cache dezenas de vezes. O gargalo
que derrubou o Supabase (Disk IO esgotado numa instância compartilhada) deixa
de ter como acontecer, porque quase nenhuma consulta volta a tocar o disco.

```
Internet ─── 6432/TLS ──▶ PgBouncer (na VM)  ──▶ Postgres 17 (Docker, 127.0.0.1)
   ▲                       modo transação          nunca exposto
   │                       Let's Encrypt
Vercel + GitHub Actions
```

**Só o PgBouncer fica exposto.** O Postgres escuta apenas em 127.0.0.1: um
scanner de portas na internet não encontra Postgres nenhum nesta máquina.

O PgBouncer não é enfeite — é obrigatório. O site roda serverless na Vercel
com `NullPool` (uma conexão por requisição, ver `app/db.py`); sem pooler,
qualquer pico esgota o `max_connections`. Era esse o papel do Supavisor.

E o código já está pronto: `app/db.py` define `prepare_threshold=None` desde
julho, exatamente porque prepared statements quebram com pooler de transação.
**Nada muda na aplicação além da string de conexão.**

---

## Parte 1 — Só você pode fazer (navegador)

### 1.1 Criar a conta na OCI

[cloud.oracle.com](https://cloud.oracle.com) → conta gratuita. Pede cartão para
verificação, **não cobra**.

> ### A região é a decisão irreversível deste passo
>
> O Always Free vive na **região "home" da conta** — escolhida no cadastro e
> que **não muda depois**. Volume gratuito criado fora dela é cobrado normal.
>
> Escolha **Brazil East (São Paulo)** `sa-saopaulo-1` ou **Brazil Southeast
> (Vinhedo)** `sa-vinhedo-1`.
>
> Não é preferência: o `vercel.json` do projeto fixa `"regions": ["gru1"]`, ou
> seja, as funções que consultam o banco rodam em São Paulo. Banco fora do
> Brasil faz cada consulta atravessar o continente, e uma tela do dashboard
> faz várias.
>
> Entre as duas, **Vinhedo costuma ter mais capacidade ARM livre** (ver 1.2) e
> fica no mesmo estado, a ~100 km — perto o bastante para a diferença ser
> irrelevante perto de um salto para os Estados Unidos. Se São Paulo estiver
> apertada, Vinhedo é a escolha melhor, não o consolo.

### 1.2 Criar a VM

Compute → Instances → Create instance:

- **Image:** Ubuntu 24.04
- **Shape:** `VM.Standard.A1.Flex` — **2 OCPUs, 12 GB RAM**
- **Boot volume:** 100 GB
- **SSH:** salve a chave privada, é o único jeito de entrar

O teto do Always Free caiu para 2 OCPU / 12 GB em **15/06/2026** (era 4 / 24).
O `postgresql.conf` deste repositório já está dimensionado para 12 GB. O limite
de disco é 200 GB no total, mínimo 47 GB por boot volume — 100 GB deixa folga
para os backups.

#### Se aparecer `Out of host capacity`

Não é erro seu: a capacidade ARM gratuita acaba, e **em `sa-saopaulo-1` há
relato de dias seguidos sem vaga**, com mais de mil tentativas automáticas
falhando. Na ordem:

1. Tente outro *availability domain* e repita algumas vezes ao longo do dia.
2. Tente `sa-vinhedo-1` — **mas só se ainda não criou a conta**, porque a
   região home não muda.
3. **Plano B, sempre disponível:** `VM.Standard.E2.1.Micro` (AMD, 1 GB RAM,
   1/8 de OCPU com burst). Dá para ter duas dessas, e elas não têm fila de
   capacidade.

   1 GB parece pouco e é — mas o banco tem ~111 MB, então cabe inteiro em
   cache. Vem com configuração própria, já testada:

   ```bash
   # no .env da VM
   PG_CONF=postgresql-micro.conf
   # e ligue swap ANTES de subir o Postgres:
   sudo bash scripts/05-swap-para-micro.sh
   ```

   Não é para ficar: quando o A1 abrir, o mesmo backup restaura na máquina
   maior. Mas serve para a migração não ficar refém de capacidade.

### 1.3 Abrir a porta na Security List
Networking → VCN → Subnet → Security List → **Add Ingress Rule**:

| | |
|---|---|
| Source CIDR | `0.0.0.0/0` |
| Destination Port | `6432` |

Repita para a `80` (Let's Encrypt precisa dela para renovar).

> O firewall da VM **não basta**: a OCI tem o dela por fora. Esquecer este
> passo é a causa nº 1 de "abri tudo e não conecta".

### 1.4 Um domínio
Precisa de um para o certificado TLS. [DuckDNS](https://duckdns.org) dá um
subdomínio grátis em dois minutos — aponte para o IP público da VM.

---

## Parte 2 — Na VM (com ajuda de IA, se quiser)

```bash
ssh -i sua-chave.key ubuntu@SEU_IP

# copie a pasta infra/oci para /opt/credit-infra (scp ou git clone)
#
# /opt, NAO o home. O Ubuntu 24.04 cria /home/<usuario> com permissao 750, e o
# Postgres do conteiner roda como uid 999 -- ele nao consegue atravessar ate os
# arquivos montados e morre no boot com:
#     ls: cannot open directory '/docker-entrypoint-initdb.d/': Permission denied
cd /opt/credit-infra

# PERMISSOES DEPOIS DO SCP (10/09/2026). O OpenSSH do Windows traduz as ACLs e
# cria os diretorios como 700. O Postgres do conteiner roda como uid 999 e nao
# consegue entrar -- morre no boot com "cannot open directory
# '/docker-entrypoint-initdb.d/'". `chown` NAO resolve: o problema e o modo,
# nao o dono. Confira com `namei -l postgres/initdb` (procure drwx------).
chmod 755 /opt/credit-infra /opt/credit-infra/postgres /opt/credit-infra/postgres/initdb
chmod 644 /opt/credit-infra/postgres/postgresql*.conf /opt/credit-infra/postgres/initdb/*.sql

cp .env.example .env
chmod 600 .env               # tem a senha do banco, e nao e montado em conteiner
nano .env                    # gere a senha com o comando que está lá dentro

sudo bash scripts/05-swap-para-micro.sh   # SO no E2.1.Micro, e ANTES do Postgres
bash scripts/00-preparar-vm.sh
# saia e entre de novo no SSH (grupo docker)

bash scripts/10-tls-letsencrypt.sh db.seudominio.duckdns.org
docker compose up -d
docker compose logs -f postgres     # espere "database system is ready"

# Confira que a extensão de métricas subiu -- é dela que os scripts de
# diagnóstico dependem. Tem que responder 't'.
docker exec credit_pg psql -U credit_admin -d credit_monitor \
  -tAc "SELECT count(*) > 0 FROM pg_stat_statements;"
```

Agora o PgBouncer — siga `pgbouncer/README-userlist.md` para gerar o
`userlist.txt`, depois:

```bash
sudo cp pgbouncer/pgbouncer.ini /etc/pgbouncer/pgbouncer.ini
sudo systemctl enable --now pgbouncer
sudo systemctl status pgbouncer
```

Teste de fora da VM (do seu Windows):

```powershell
psql "postgresql://credit_admin:SENHA@db.seudominio.duckdns.org:6432/credit_monitor?sslmode=verify-full&sslrootcert=system" -c "select version();"
```

---

## Parte 3 — Migrar os dados

```bash
bash scripts/50-migrar-do-supabase.sh "postgresql://postgres.XXXX:SENHA@aws-1-sa-east-1.pooler.supabase.com:5432/postgres"
```

Repare na **porta 5432**, não 6543 — `pg_dump` não funciona de forma confiável
através do pooler de transação, e o script recusa se você passar a errada.

**Pause os jobs do cron-job.org antes.** A origem já está no limite; tirar as
coletas de 15 em 15 minutos do caminho é a diferença entre um dump que sai e um
que morre no meio — e um dump interrompido não é retomável, é recomeçar.

O script exporta `PGOPTIONS` com `statement_timeout=0` na conexão de origem. Sem
isso o dump nem começa: a Supabase põe um limite curto no *role*, e ele vale
para qualquer comando da sessão. Medido contra um Postgres com o role em 1ms —
sem `PGOPTIONS`, `pg_dump` morre na primeira consulta (`canceling statement due
to statement timeout`); com ele, dump íntegro e saída 0.

Ele baixa, confere a integridade do arquivo, restaura e **compara a contagem
de linhas tabela a tabela** entre origem e destino. Se alguma divergir, ele
falha e manda não trocar a `DATABASE_URL` ainda.

Depois, com todas batendo, troque em dois lugares:

- **Vercel** → Settings → Environment Variables → `DATABASE_URL`
- **GitHub** → Settings → Secrets and variables → Actions → `DATABASE_URL`

```
postgresql+psycopg://credit_admin:SENHA@db.seudominio.duckdns.org:6432/credit_monitor?sslmode=verify-full&sslrootcert=system
```

> **`&sslrootcert=system` não é enfeite.** Com `verify-full` o libpq exige uma
> âncora de confiança e, sem esse parâmetro, procura em
> `~/.postgresql/root.crt` -- que não existe em lugar nenhum. A conexão falha
> com "root certificate file does not exist", uma mensagem que aponta para um
> arquivo em vez de apontar para a causa. `system` manda usar o repositório de
> certificados do sistema operacional, onde a raiz do Let's Encrypt já está.
> Vale para a Vercel e para o GitHub Actions do mesmo jeito.
>
> **No Windows é diferente.** `system` quer dizer "o repositório do OpenSSL",
> não "o repositório do Windows" -- e o OpenSSL embutido no psycopg procura um
> bundle num caminho compilado que não existe na máquina. O erro é
> `SSL error: certificate verify failed`. Instale a raiz no lugar padrão do
> libpq, copiando da própria VM (é a raiz que assinou o certificado):
>
> ```powershell
> New-Item -ItemType Directory -Force -Path "$env:APPDATA\postgresql" | Out-Null
> scp -i "$HOME\.ssh\oci-credit.key" ubuntu@SEU_IP:/etc/ssl/certs/ISRG_Root_X1.pem "$env:APPDATA\postgresql\root.crt"
> ```
>
> Com o arquivo lá, a URL local usa só `?sslmode=verify-full`, sem
> `sslrootcert`. São duas URLs de propósito: a de produção (Linux) leva
> `&sslrootcert=system`; a sua, não.

```
```

E rode, do seu Windows, com a `DATABASE_URL` nova:

```powershell
python -m scripts.init_db
python -m scripts.importar_taxonomia
```

---

## Parte 4 — Automação

```bash
bash scripts/60-instalar-cron.sh
```

| Quando | O quê |
|---|---|
| 03h BRT, diário | backup (`pg_dump -Fc`, 14 diários + 8 semanais) |
| 04h BRT, domingos | **teste de restauração** num banco descartável |
| de hora em hora | verificação de saúde |

O teste de restauração semanal é o item que quase ninguém tem e que faz o
backup ser real: ele restaura o dump mais recente, conta tabelas e linhas, e
falha se vier vazio.

Para receber aviso quando algo parar, crie um check gratuito em
[healthchecks.io](https://healthchecks.io) e ponha a URL em `HEALTHCHECK_URL`
no `.env`. O alarme certo é o do ping que **deixou de chegar** — script morto
não manda mensagem avisando que morreu.

Para a cópia fora da VM, crie um bucket no Object Storage (20 GB grátis),
configure a CLI `oci` e ponha o nome em `OCI_BUCKET`. Backup no mesmo disco do
banco protege contra "apaguei a tabela", não contra "perdi a máquina".

---

## O dia a dia

| O que era no Supabase | Onde fica agora |
|---|---|
| SQL Editor / ver tabelas | Adminer: `ssh -L 8080:localhost:8080 ubuntu@IP` → http://localhost:8080 |
| Logs | `docker compose logs postgres` e `/var/lib/postgresql/data/log/` na VM |
| Consultas lentas | já ligado: qualquer consulta acima de 1s vai para o log |
| Métricas / consultas caras | `pg_stat_statements` ativo — `scripts/diagnostico_banco.py` funciona igual |
| Backups | `scripts/20-backup.sh`, e `30-restaurar.sh` para voltar |
| Status do serviço | `scripts/40-saude.sh` |

```bash
docker compose ps                     # está no ar?
docker compose restart postgres       # reiniciar
sudo systemctl restart pgbouncer      # reiniciar o pooler
tail -f /opt/credit-infra/logs/saude.log # última verificação
docker compose pull && docker compose up -d   # atualizar o Postgres
```

---

## O que passa a ser seu

Vale dizer sem rodeio, porque é a única coisa que este arranjo cobra:
**uptime, backup e segurança agora são responsabilidade sua.** Não há plantão
nem SLA. Os scripts automatizam a parte repetitiva e o teste de restauração
semanal cobre o risco mais caro, mas se a VM cair num sábado, quem percebe é
você.

Para este dashboard — uso interno, sem cliente externo dependendo dele — a
troca compensa: US$ 300/ano economizados por uma máquina que, para esta carga,
é ordens de grandeza mais folgada que a instância paga equivalente.

Se um dia ele virar ferramenta de time, os US$ 25/mês do Supabase Pro deixam
de ser caros e passam a ser barato seguro.
