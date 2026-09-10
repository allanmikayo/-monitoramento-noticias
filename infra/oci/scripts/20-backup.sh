#!/usr/bin/env bash
# Backup diário do banco. Roda na VM, por cron.
#
# O que o Supabase fazia por você e agora é seu. Três decisões aqui:
#
#   1. `pg_dump -Fc` (formato custom) em vez de SQL puro: comprime sozinho
#      e permite restaurar tabela avulsa sem editar arquivo de 100 MB.
#   2. Retenção em duas faixas -- 14 diários e 8 semanais. Erro percebido
#      no mesmo dia se resolve com o diário; erro que só aparece um mês
#      depois (um script que corrompeu dado devagar) precisa do semanal.
#   3. Cópia FORA da VM. Backup no mesmo disco do banco protege contra
#      "apaguei a tabela", não contra "perdi a máquina". O Object Storage
#      da OCI dá 20 GB no Always Free -- de graça, e é outro domínio de
#      falha.
set -euo pipefail

cd "$(dirname "$0")/.."
source .env

DESTINO="${BACKUP_DIR:-$HOME/backups}"
BUCKET="${OCI_BUCKET:-}"          # vazio = só backup local
HOJE=$(date +%F)
DIA_SEMANA=$(date +%u)            # 7 = domingo
mkdir -p "$DESTINO/diario" "$DESTINO/semanal"

ARQUIVO="$DESTINO/diario/credit_monitor-$HOJE.dump"

echo "[$(date '+%F %T')] iniciando backup"
docker exec credit_pg pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$ARQUIVO"

# Um dump que não abre não é backup. `pg_restore --list` lê o índice do
# arquivo e falha se ele estiver truncado -- é barato e pega o modo de
# falha mais comum (disco cheio no meio da escrita).
if ! pg_restore --list "$ARQUIVO" >/dev/null 2>&1; then
  echo "[$(date '+%F %T')] ERRO: o dump saiu ilegível -- backup ABORTADO" >&2
  rm -f "$ARQUIVO"
  exit 1
fi

TAMANHO=$(du -h "$ARQUIVO" | cut -f1)
echo "[$(date '+%F %T')] dump ok: $ARQUIVO ($TAMANHO)"

if [[ "$DIA_SEMANA" == "7" ]]; then
  cp "$ARQUIVO" "$DESTINO/semanal/credit_monitor-$HOJE.dump"
  echo "[$(date '+%F %T')] cópia semanal guardada"
fi

find "$DESTINO/diario"  -name '*.dump' -mtime +14 -delete
find "$DESTINO/semanal" -name '*.dump' -mtime +56 -delete

if [[ -n "$BUCKET" ]] && command -v oci >/dev/null 2>&1; then
  echo "[$(date '+%F %T')] enviando para o Object Storage ($BUCKET)"
  oci os object put --bucket-name "$BUCKET" --file "$ARQUIVO" \
      --name "diario/credit_monitor-$HOJE.dump" --force >/dev/null
  echo "[$(date '+%F %T')] enviado"
else
  echo "[$(date '+%F %T')] AVISO: sem cópia fora da VM (OCI_BUCKET não configurado)."
  echo "                    Backup só local protege contra erro humano, não contra perder a máquina."
fi

echo "[$(date '+%F %T')] concluído"
