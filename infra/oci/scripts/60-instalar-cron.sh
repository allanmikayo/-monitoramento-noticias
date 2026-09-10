#!/usr/bin/env bash
# Instala as tarefas agendadas da VM.
#
# Horários pensados para não colidir com os jobs do GitHub Actions, que
# rodam entre 00h e 02h UTC (21h-23h BRT):
#   03h00 BRT  backup diário          -- depois de tudo que escreve à noite
#   04h00 BRT  teste de restauração   -- domingos
#   de hora em hora, saúde
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$HOME/credit-infra/logs"
mkdir -p "$LOG"

NOVO=$(mktemp)
crontab -l 2>/dev/null | grep -v "credit-infra" > "$NOVO" || true

cat >> "$NOVO" <<CRON
# --- credit-infra (instalado por 60-instalar-cron.sh) ---
0 6 * * *   bash $DIR/20-backup.sh    >> $LOG/backup.log 2>&1
0 7 * * 0   bash $DIR/30-restaurar.sh >> $LOG/restauracao.log 2>&1
0 * * * *   bash $DIR/40-saude.sh     >> $LOG/saude.log 2>&1
CRON

crontab "$NOVO"
rm -f "$NOVO"

echo "Cron instalado (horários em UTC -- 06h UTC = 03h BRT):"
crontab -l | grep -A4 "credit-infra"
echo
echo "Logs em $LOG"
