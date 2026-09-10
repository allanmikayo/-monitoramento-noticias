#!/usr/bin/env bash
# Prepara uma VM Ubuntu 24.04 recém-criada na OCI para hospedar o Postgres do
# Hub Credit Research. Serve tanto no Ampere A1 (ARM64) quanto no
# VM.Standard.E2.1.Micro (x86_64) -- tudo aqui é pacote de repositório.
#
# Instala Docker, PgBouncer e certbot; configura firewall; cria as pastas.
# É idempotente: rodar de novo não estraga nada.
#
#   bash 00-preparar-vm.sh
set -euo pipefail

echo "==> Atualizando o sistema"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq

# CLIENTE 17, NÃO O 16 DO UBUNTU (10/09/2026).
#
# O Ubuntu 24.04 empacota o cliente PostgreSQL 16. O Supabase de origem roda
# 17.6. E a regra do pg_dump é dura -- da documentação oficial:
#
#   "pg_dump cannot dump from PostgreSQL servers newer than its own major
#    version; it will refuse to even try, rather than risk making an invalid
#    dump."
#
# Ou seja: com o cliente 16 a migração não falharia no meio, falharia ANTES
# de começar. O caminho contrário é permitido (dump de servidor mais velho, e
# restauração em servidor mais novo), então o 17 atende origem e destino.
echo "==> Repositório oficial do PostgreSQL (o Ubuntu só traz o cliente 16)"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt-get update -qq

echo "==> Instalando Docker, PgBouncer, certbot e utilitários"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  docker.io docker-compose-v2 pgbouncer certbot \
  postgresql-client-17 ufw unattended-upgrades curl gzip

# Confere em vez de confiar: se o apt tiver resolvido para outra versão, é
# melhor descobrir agora do que na hora da migração.
VERSAO_DUMP=$(pg_dump --version | grep -oE '[0-9]+' | head -1)
if [[ "${VERSAO_DUMP:-0}" -lt 17 ]]; then
  echo "ERRO: pg_dump instalado é o $VERSAO_DUMP, e a origem é Postgres 17." >&2
  echo "      Ele vai se recusar a ler o banco. Confira o repositório PGDG." >&2
  exit 1
fi
echo "    pg_dump $(pg_dump --version | awk '{print $3}') -- ok para ler o Supabase 17"

sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" || true

echo "==> Firewall (ufw)"
# ATENÇÃO: a porta 22 vem PRIMEIRO, e de propósito. Habilitar o ufw sem
# liberar SSH antes te tranca para fora da própria VM -- é o erro clássico
# e a recuperação exige console serial na OCI.
sudo ufw allow 22/tcp    comment 'SSH'
sudo ufw allow 6432/tcp  comment 'PgBouncer (Postgres via TLS)'
sudo ufw allow 80/tcp    comment 'Let\''s Encrypt (renovação do certificado)'
sudo ufw --force enable
sudo ufw status verbose

# A imagem Ubuntu da OCI vem com regras iptables próprias que IGNORAM o ufw
# e derrubam tudo que não seja SSH. Sem estas duas linhas, o firewall
# "está aberto" no ufw e a porta continua fechada de fora -- é a pegadinha
# que faz todo mundo perder uma tarde.
echo "==> Abrindo as portas também no iptables da imagem Oracle"
# Inserção no TOPO da cadeia (posição 1), não numa posição fixa: a receita
# que circula usa `-I INPUT 6`, que assume a cadeia padrão da imagem Oracle e
# falha com "Index of insertion too big" se ela tiver menos regras. No topo
# sempre funciona, e é antes do REJECT final, que é o que importa.
sudo iptables -I INPUT 1 -m state --state NEW -p tcp --dport 6432 -j ACCEPT
sudo iptables -I INPUT 1 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo netfilter-persistent save 2>/dev/null || sudo sh -c 'iptables-save > /etc/iptables/rules.v4'

echo "==> Atualizações de segurança automáticas"
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Pastas"
# /opt, NÃO o home (10/09/2026). O Postgres do contêiner roda como uid 999 e
# precisa atravessar o diretório até os arquivos montados. O Ubuntu 24.04
# passou a criar `/home/<usuario>` com permissão 750 -- uid 999 não entra, e o
# contêiner morre no boot com
#     ls: cannot open directory '/docker-entrypoint-initdb.d/': Permission denied
# A alternativa seria `chmod 755 /home/ubuntu`, mas isso desfaz um
# endurecimento de propósito do Ubuntu para resolver um problema que só existe
# porque o arquivo estava no lugar errado. Configuração de serviço mora em
# /opt.
sudo mkdir -p /opt/credit-infra
sudo chown "$USER:$USER" /opt/credit-infra
mkdir -p ~/backups
sudo mkdir -p /etc/pgbouncer/tls /var/log/pgbouncer /var/run/pgbouncer
sudo chown -R postgres:postgres /var/log/pgbouncer /var/run/pgbouncer

echo
echo "PRONTO. Faltam, nesta ordem:"
echo "  1. Liberar a porta 6432 na Security List da OCI (no navegador) --"
echo "     o firewall da VM não basta, a OCI tem o dela por fora."
echo "  2. Copiar docker-compose.yml, .env e postgres/ para /opt/credit-infra"
echo "  3. bash 10-tls-letsencrypt.sh SEU.DOMINIO"
echo "  4. cd /opt/credit-infra && docker compose up -d"
echo
echo "Saia e entre de novo no SSH para o grupo docker valer sem sudo."
