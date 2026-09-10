#!/usr/bin/env bash
# Migra o banco do Supabase para esta VM -- e CONFERE linha a linha.
#
#   bash 50-migrar-do-supabase.sh "postgresql://usuario:senha@HOST:5432/postgres"
#
# USE A PORTA 5432, NÃO A 6543. O `pg_dump` precisa de uma sessão estável:
# ele abre uma transação e mantém o mesmo backend do início ao fim. O
# pooler em modo transação (6543) troca de backend entre comandos e o dump
# sai corrompido ou trava no meio. A 5432 do mesmo host é o pooler em modo
# sessão, que serve.
#
# Rode ISTO NA VM, não no seu Windows: a rede do datacenter é muito mais
# estável para uma transferência longa, e o Supabase está justamente com
# dificuldade de manter conexão.
set -euo pipefail

cd "$(dirname "$0")/.."
source .env

ORIGEM="${1:-}"
[[ -n "$ORIGEM" ]] || { echo 'uso: bash 50-migrar-do-supabase.sh "postgresql://...@host:5432/postgres"' >&2; exit 1; }
if [[ "$ORIGEM" == *":6543/"* ]]; then
  echo "ERRO: essa é a porta do pooler de TRANSAÇÃO (6543). Troque para 5432." >&2
  echo "      pg_dump não funciona de forma confiável através dela." >&2
  exit 1
fi

DUMP="$HOME/migracao-supabase-$(date +%F-%H%M).dump"

# LIMITES NA CONEXÃO DE ORIGEM (10/09/2026). A Supabase põe um
# `statement_timeout` curto no ROLE, e ele vale para QUALQUER comando da
# sessão -- inclusive o `COPY` de cada tabela que o pg_dump emite. Num banco
# saturado o dump morre no meio com
#     canceling statement due to statement timeout
# e um dump interrompido não é retomável: é recomeçar do zero.
#
# Foi exatamente esse defeito que derrubou o `scripts/init_db.py` do projeto,
# até os limites passarem a ir no pacote de abertura da conexão. Aqui o
# mecanismo é o `PGOPTIONS`, que o libpq envia do mesmo jeito.
#
#   statement_timeout=0                    sem limite -- um dump demora o que
#                                          precisar, e é uma vez só
#   idle_in_transaction_session_timeout=0  o pg_dump segura uma transação
#                                          aberta o tempo todo, de propósito
#                                          (é assim que ele garante um retrato
#                                          consistente); um limite aqui mata
#                                          justamente o que faz o dump valer
#   lock_timeout=0                         idem: ele precisa dos ACCESS SHARE
export PGOPTIONS="-c statement_timeout=0 -c idle_in_transaction_session_timeout=0 -c lock_timeout=0"

echo "==> 0/4  Conferindo que a origem responde"
if ! psql "$ORIGEM" -tAc "select 'ok'" >/dev/null 2>&1; then
  echo "ERRO: a origem não respondeu. Se for saturação, espere alguns minutos" >&2
  echo "      e/ou pause os jobs do cron-job.org antes de tentar de novo." >&2
  exit 1
fi
echo "    origem respondendo ($(psql "$ORIGEM" -tAc "select pg_size_pretty(pg_database_size(current_database()))" | tr -d ' ') de dados)"

# VERSÃO DO CLIENTE >= VERSÃO DO SERVIDOR (10/09/2026).
#
# Da documentação do pg_dump: "cannot dump from PostgreSQL servers newer than
# its own major version; it will refuse to even try". O Ubuntu 24.04 traz o
# cliente 16 e o Supabase roda 17 -- combinação que não funciona. O
# `00-preparar-vm.sh` instala o 17 do repositório oficial; esta conferência
# existe para o caso de alguém rodar este script noutra máquina e receber uma
# mensagem que não explica nada.
SERVIDOR=$(psql "$ORIGEM" -tAc "SHOW server_version_num" | cut -c1-2)
CLIENTE=$(pg_dump --version | grep -oE '[0-9]+' | head -1)
if [[ "$CLIENTE" -lt "$SERVIDOR" ]]; then
  echo "ERRO: pg_dump é versão $CLIENTE e o servidor de origem é $SERVIDOR." >&2
  echo "      pg_dump se recusa a ler servidor mais novo que ele." >&2
  echo "      Instale o cliente $SERVIDOR:" >&2
  echo "        sudo apt-get install -y postgresql-common" >&2
  echo "        sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y" >&2
  echo "        sudo apt-get install -y postgresql-client-$SERVIDOR" >&2
  exit 1
fi
echo "    pg_dump $CLIENTE lendo servidor $SERVIDOR -- compatível"

echo "==> 1/4  Baixando o banco do Supabase"
# Até 3 tentativas: a instância de origem está instável, e um dump
# interrompido no meio não é retomável -- é recomeçar.
tentativa=1
until pg_dump "$ORIGEM" -Fc --no-owner --no-acl --no-privileges -f "$DUMP"; do
  tentativa=$((tentativa + 1))
  [[ $tentativa -le 3 ]] || { echo "falhou 3 vezes -- o Supabase não está aguentando o dump" >&2; exit 1; }
  echo "    tentativa $tentativa em 30s..."
  rm -f "$DUMP"; sleep 30
done
echo "    baixado: $(du -h "$DUMP" | cut -f1)"

echo "==> 2/4  Conferindo a integridade do arquivo"
pg_restore --list "$DUMP" >/dev/null
echo "    arquivo íntegro"

echo "==> 3/4  Restaurando no Postgres local"
docker exec -i credit_pg pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --clean --if-exists --no-owner < "$DUMP" 2>&1 | grep -v "^pg_restore: warning" || true

echo "==> 4/4  Comparando contagem de linhas, tabela a tabela"
# Esta é a etapa que transforma "acho que migrou" em "migrou". Compara a
# ORIGEM com o DESTINO, não o destino consigo mesmo.
TMP_ORIG=$(mktemp); TMP_DEST=$(mktemp)
trap 'rm -f "$TMP_ORIG" "$TMP_DEST"' EXIT

CONTAGEM_SQL="
SELECT table_name, (xpath('/row/c/text()',
       query_to_xml(format('SELECT count(*) AS c FROM public.%I', table_name),
                    false, true, '')))[1]::text::bigint AS n
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;"

psql "$ORIGEM" -tAF'|' -c "$CONTAGEM_SQL" > "$TMP_ORIG"
docker exec -i credit_pg psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAF'|' -c "$CONTAGEM_SQL" > "$TMP_DEST"

echo
printf "%-28s %12s %12s   %s\n" "TABELA" "SUPABASE" "AQUI" ""
divergencias=0
while IFS='|' read -r tabela n_orig; do
  [[ -n "$tabela" ]] || continue
  n_dest=$(grep "^${tabela}|" "$TMP_DEST" | cut -d'|' -f2 || echo "AUSENTE")
  if [[ "$n_orig" == "$n_dest" ]]; then
    printf "%-28s %12s %12s   ok\n" "$tabela" "$n_orig" "$n_dest"
  else
    printf "%-28s %12s %12s   <<< DIVERGE\n" "$tabela" "$n_orig" "$n_dest"
    divergencias=$((divergencias + 1))
  fi
done < "$TMP_ORIG"

echo
if [[ $divergencias -gt 0 ]]; then
  echo "ATENÇÃO: $divergencias tabela(s) com contagem diferente. NÃO troque a"
  echo "DATABASE_URL ainda -- investigue antes." >&2
  exit 1
fi

echo "Todas as tabelas bateram. Migração conferida."
echo
echo "Agora troque a DATABASE_URL em dois lugares:"
echo "  - Vercel:  Settings -> Environment Variables"
echo "  - GitHub:  Settings -> Secrets and variables -> Actions"
echo
echo "Formato:"
echo "  postgresql+psycopg://$POSTGRES_USER:SENHA@SEU.DOMINIO:6432/$POSTGRES_DB?sslmode=verify-full"
echo
echo "Guarde o dump por algumas semanas: $DUMP"
