#!/usr/bin/env bash
# Verificação de saúde -- o que substitui o painel do Supabase.
#
# Roda por cron de hora em hora e escreve uma linha por execução. Se você
# configurar HEALTHCHECK_URL (healthchecks.io é gratuito), ele avisa por
# e-mail quando o ping PARA de chegar -- que é o alarme que importa: um
# script que morreu não manda mensagem nenhuma dizendo que morreu.
set -euo pipefail

cd "$(dirname "$0")/.."
source .env
PING="${HEALTHCHECK_URL:-}"

problemas=()

docker exec credit_pg pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q \
  || problemas+=("postgres não responde a pg_isready")

systemctl is-active --quiet pgbouncer || problemas+=("pgbouncer parado")

DISCO=$(df --output=pcent / | tail -1 | tr -dc '0-9')
[[ "$DISCO" -lt 85 ]] || problemas+=("disco em ${DISCO}%")

ULTIMO=$(ls -t "${BACKUP_DIR:-$HOME/backups}"/diario/*.dump 2>/dev/null | head -1 || true)
if [[ -z "$ULTIMO" ]]; then
  problemas+=("nenhum backup encontrado")
elif [[ $(( ($(date +%s) - $(stat -c %Y "$ULTIMO")) / 3600 )) -gt 36 ]]; then
  problemas+=("backup mais recente tem mais de 36h")
fi

TAMANHO=$(docker exec credit_pg psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT pg_size_pretty(pg_database_size(current_database()));" 2>/dev/null || echo "?")
CONEXOES=$(docker exec credit_pg psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database();" 2>/dev/null || echo "?")

if [[ ${#problemas[@]} -eq 0 ]]; then
  echo "[$(date '+%F %T')] OK -- banco $TAMANHO, $CONEXOES conexão(ões), disco ${DISCO}%"
  [[ -n "$PING" ]] && curl -fsS -m 10 "$PING" >/dev/null || true
else
  echo "[$(date '+%F %T')] PROBLEMA: ${problemas[*]}" >&2
  [[ -n "$PING" ]] && curl -fsS -m 10 "$PING/fail" >/dev/null || true
  exit 1
fi
