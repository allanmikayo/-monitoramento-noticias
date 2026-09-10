#!/usr/bin/env bash
# Restaura um dump -- e, sem argumento, TESTA a restauração do backup mais
# recente num banco descartável.
#
#   bash 30-restaurar.sh                      # testa o último backup
#   bash 30-restaurar.sh caminho/do.dump      # testa aquele arquivo
#   bash 30-restaurar.sh caminho/do.dump --pra-valer   # restaura POR CIMA do banco real
#
# POR QUE O MODO DE TESTE É O PADRÃO. Backup que nunca foi restaurado é
# uma suposição, não um backup -- e o dia de descobrir isso nunca é um dia
# bom. Este script roda por cron toda semana justamente para transformar a
# suposição em fato verificado.
set -euo pipefail

cd "$(dirname "$0")/.."
source .env

ARQUIVO="${1:-}"
MODO="${2:-teste}"
if [[ -z "$ARQUIVO" ]]; then
  ARQUIVO=$(ls -t "${BACKUP_DIR:-$HOME/backups}"/diario/*.dump 2>/dev/null | head -1 || true)
fi
[[ -n "$ARQUIVO" && -f "$ARQUIVO" ]] || { echo "nenhum dump encontrado" >&2; exit 1; }

echo "[$(date '+%F %T')] arquivo: $ARQUIVO"

if [[ "$MODO" == "--pra-valer" ]]; then
  echo
  echo "!!! Isto vai SUBSTITUIR o banco '$POSTGRES_DB'. Digite RESTAURAR para confirmar:"
  read -r confirmacao
  [[ "$confirmacao" == "RESTAURAR" ]] || { echo "cancelado"; exit 1; }
  docker exec -i credit_pg pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      --clean --if-exists --no-owner < "$ARQUIVO"
  echo "[$(date '+%F %T')] restaurado sobre o banco real"
  exit 0
fi

ALVO="teste_restauracao_$(date +%s)"
echo "[$(date '+%F %T')] restaurando em '$ALVO' (banco descartável)"
docker exec credit_pg createdb -U "$POSTGRES_USER" "$ALVO"
trap 'docker exec credit_pg dropdb -U "$POSTGRES_USER" --if-exists "$ALVO" >/dev/null 2>&1 || true' EXIT

docker exec -i credit_pg pg_restore -U "$POSTGRES_USER" -d "$ALVO" --no-owner < "$ARQUIVO"

# Conferência de conteúdo, não só de "o comando não deu erro": um dump
# vazio restaura sem reclamar nenhuma.
LINHAS=$(docker exec credit_pg psql -U "$POSTGRES_USER" -d "$ALVO" -tAc "
  SELECT COALESCE(sum(n_live_tup),0) FROM pg_stat_user_tables;")
TABELAS=$(docker exec credit_pg psql -U "$POSTGRES_USER" -d "$ALVO" -tAc "
  SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")

echo "[$(date '+%F %T')] restaurado: $TABELAS tabela(s), ~$LINHAS linha(s)"
if [[ "$TABELAS" -lt 20 ]]; then
  echo "[$(date '+%F %T')] ERRO: esperava ~29 tabelas, achei $TABELAS -- backup SUSPEITO" >&2
  exit 1
fi
echo "[$(date '+%F %T')] TESTE OK -- o backup restaura e tem conteúdo"
