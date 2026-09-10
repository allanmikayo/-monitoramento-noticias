#!/usr/bin/env bash
# Emite o certificado TLS do PgBouncer com Let's Encrypt e instala a
# renovação automática.
#
#   bash 10-tls-letsencrypt.sh db.seudominio.com seu@email.com
#
# PRECISA DE UM DOMÍNIO apontando para o IP público desta VM. Se você não
# tem um, o DuckDNS (duckdns.org) dá um subdomínio de graça em dois
# minutos, e serve perfeitamente.
#
# POR QUE NÃO CERTIFICADO PRÓPRIO: com autoassinado você fica preso em
# `sslmode=require`, que criptografa mas NÃO verifica com quem está
# falando -- ou seja, não protege contra alguém no meio do caminho. Com
# Let's Encrypt dá para usar `verify-full`, que é o que fecha a porta de
# verdade. São dois minutos a mais por uma diferença que importa.
set -euo pipefail

DOMINIO="${1:-}"
EMAIL="${2:-}"
if [[ -z "$DOMINIO" ]]; then
  echo "uso: bash 10-tls-letsencrypt.sh db.seudominio.com [seu@email.com]" >&2
  exit 1
fi

# E-MAIL VALE A PENA (10/09/2026). A primeira versão usava
# `--register-unsafely-without-email`, que evita um campo a preencher. Só que
# a renovação automática é a única coisa entre você e um banco que para de
# aceitar conexões -- e ela pode quebrar em silêncio (porta 80 fechada por
# uma regra nova, disco cheio, certbot desatualizado). Com e-mail registrado,
# o Let's Encrypt avisa 20 dias antes do vencimento. É a diferença entre
# "renovar numa terça de manhã" e "descobrir com o dashboard fora do ar".
if [[ -n "$EMAIL" ]]; then
  REGISTRO=(--email "$EMAIL")
else
  echo "AVISO: sem e-mail, você não recebe aviso se a renovação falhar." >&2
  REGISTRO=(--register-unsafely-without-email)
fi

echo "==> Emitindo certificado para $DOMINIO"
sudo certbot certonly --standalone -d "$DOMINIO" --agree-tos "${REGISTRO[@]}" -n

# O PgBouncer roda como usuário `postgres` e não consegue ler
# /etc/letsencrypt. Em vez de afrouxar as permissões de lá (que o certbot
# reescreve a cada renovação), copiamos para uma pasta própria.
instalar_certs() {
  sudo cp "/etc/letsencrypt/live/$1/fullchain.pem" /etc/pgbouncer/tls/fullchain.pem
  sudo cp "/etc/letsencrypt/live/$1/privkey.pem"  /etc/pgbouncer/tls/privkey.pem
  sudo chown postgres:postgres /etc/pgbouncer/tls/*.pem
  sudo chmod 600 /etc/pgbouncer/tls/privkey.pem
}
instalar_certs "$DOMINIO"

# Renovação: o certbot renova sozinho a cada 90 dias, mas o PgBouncer só
# lê o certificado ao iniciar. Sem este gancho, o certificado renova e a
# conexão passa a falhar 90 dias depois -- longe da causa, no pior estilo.
echo "==> Instalando o gancho de renovação"
sudo tee /etc/letsencrypt/renewal-hooks/deploy/pgbouncer.sh >/dev/null <<HOOK
#!/usr/bin/env bash
set -e
cp /etc/letsencrypt/live/$DOMINIO/fullchain.pem /etc/pgbouncer/tls/fullchain.pem
cp /etc/letsencrypt/live/$DOMINIO/privkey.pem  /etc/pgbouncer/tls/privkey.pem
chown postgres:postgres /etc/pgbouncer/tls/*.pem
chmod 600 /etc/pgbouncer/tls/privkey.pem
systemctl reload pgbouncer || systemctl restart pgbouncer
HOOK
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/pgbouncer.sh

echo "==> Testando a renovação (simulação)"
sudo certbot renew --dry-run

echo
echo "Certificado instalado. Sua DATABASE_URL usa este domínio:"
echo "  postgresql+psycopg://USUARIO:SENHA@$DOMINIO:6432/credit_monitor?sslmode=verify-full&sslrootcert=system"
