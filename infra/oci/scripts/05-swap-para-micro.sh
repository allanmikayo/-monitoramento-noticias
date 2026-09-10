#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Swap -- só para o plano B (VM.Standard.E2.1.Micro, 1 GB de RAM).
#
# POR QUE. 1 GB é o total da máquina: Postgres, PgBouncer, Docker e o sistema.
# Não é o uso médio que derruba, é o pico -- um VACUUM pesado, um restore, um
# CREATE INDEX. Sem swap o kernel escolhe uma vítima e mata; a vítima costuma
# ser o Postgres, e o contêiner reinicia no meio de uma escrita.
#
# 2 GB de swap não fazem o banco ficar rápido (disco não é RAM). Fazem o pico
# virar lentidão em vez de morte, que é a troca certa numa máquina destas.
#
# NÃO rode isto no Ampere A1 de 12 GB -- lá não faz falta.
#
#   sudo bash 05-swap-para-micro.sh
# ---------------------------------------------------------------------------
set -euo pipefail

TAMANHO="${1:-2G}"
ARQUIVO=/swapfile

[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }

if swapon --show | grep -q "$ARQUIVO"; then
  echo "swap já está ligada:"
  swapon --show
  exit 0
fi

echo "==> 1/4  Criando $ARQUIVO de $TAMANHO"
# fallocate falha em alguns sistemas de arquivos; dd sempre funciona.
fallocate -l "$TAMANHO" "$ARQUIVO" 2>/dev/null || \
  dd if=/dev/zero of="$ARQUIVO" bs=1M count=$(( ${TAMANHO%G} * 1024 )) status=none
chmod 600 "$ARQUIVO"

echo "==> 2/4  Formatando e ligando"
mkswap "$ARQUIVO" >/dev/null
swapon "$ARQUIVO"

echo "==> 3/4  Deixando permanente (sobrevive a reboot)"
if ! grep -q "^$ARQUIVO" /etc/fstab; then
  echo "$ARQUIVO none swap sw 0 0" >> /etc/fstab
fi

echo "==> 4/4  Ajustando o comportamento do kernel"
# swappiness baixa: use swap como rede de segurança, não como rotina. O
# Postgres gerencia o próprio cache e não gosta de ter páginas trocadas.
# vfs_cache_pressure baixa: preserva o cache de metadados, que numa máquina
# pequena vale mais do que parece.
sysctl -w vm.swappiness=10 >/dev/null
sysctl -w vm.vfs_cache_pressure=50 >/dev/null
cat > /etc/sysctl.d/99-postgres-micro.conf <<'SYS'
vm.swappiness = 10
vm.vfs_cache_pressure = 50
SYS

echo
echo "pronto:"
swapon --show
free -h
