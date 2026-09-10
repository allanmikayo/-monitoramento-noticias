# Como gerar o `userlist.txt`

O PgBouncer precisa do hash SCRAM do usuário, não da senha em texto.
O jeito certo é pedir o hash ao **próprio Postgres**, que já o tem — assim a
senha nunca é digitada duas vezes nem fica em histórico de shell.

Na VM, com o contêiner rodando:

```bash
source /opt/credit-infra/.env

# `chr(34)` em vez de aspas escapadas: o formato do userlist.txt exige aspas
# DUPLAS em volta do nome e do hash, e montá-las dentro de uma string SQL que
# já está dentro de aspas do shell vira um emaranhado que quebra ao copiar.
# Pedir o caractere pelo código elimina o problema.
docker exec -e LANG=C -i credit_pg psql -U "$POSTGRES_USER" -d postgres -tAc \
  "SELECT chr(34)||rolname||chr(34)||' '||chr(34)||rolpassword||chr(34)
     FROM pg_authid WHERE rolname = '$POSTGRES_USER'" \
  | sudo tee /etc/pgbouncer/userlist.txt

sudo chown postgres:postgres /etc/pgbouncer/userlist.txt
sudo chmod 600 /etc/pgbouncer/userlist.txt
sudo systemctl restart pgbouncer
```

O arquivo fica com uma linha só, assim:

```
"credit_admin" "SCRAM-SHA-256$4096:...$...:..."
```

**Refaça isto toda vez que trocar a senha do banco.** Trocar no Postgres e
esquecer aqui é a causa mais comum de "de repente parou de autenticar" —
e o erro que aparece do lado do cliente não diz isso.
