"""Configuração comum dos testes.

`app/db.py` cria o engine no momento do IMPORT, lendo `DATABASE_URL` do
`.env` — que em produção aponta pro Supabase. Sem isto, só importar
`app.db` num teste tenta carregar o driver do Postgres (`psycopg`) e,
se ele estiver instalado, abre conexão com o banco REAL.

Então o `DATABASE_URL` é forçado ANTES de qualquer import de `app.*`.
Vale pra suíte inteira: nenhum teste deste projeto deve tocar banco de
verdade.

POR QUE ARQUIVO TEMPORÁRIO E NÃO `sqlite://` (memória)
------------------------------------------------------
O banco em memória do SQLite é POR CONEXÃO: cada nova conexão do pool
enxerga um banco vazio. Testes de unidade que criam o próprio engine não
sentem isso, mas teste de ROTA sente — o `TestClient` abre uma conexão
nova e some com as tabelas:

    sqlalchemy.exc.OperationalError: no such table: sessions

Foi exatamente o que aconteceu em 12/08/2026 ao escrever os testes de
ponta a ponta da aba Banco de Dados. Um arquivo temporário elimina a
classe inteira de problema e ainda fica mais perto de produção. É
apagado no fim da sessão de testes.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="credit_monitor_testes_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'testes.db'}"

atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

# Permite `from app...` rodando pytest da raiz do projeto.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# PAPEL COMPLETO POR PADRÃO (22/10/2026). As contas de spread passaram a
# exigir PU, % do PU par e duration publicados (`queries.PRECIFICADO`). Os
# testes antigos criam `DebentureSpread` só com spread/estoque; em vez de
# reescrever cada um, um papel criado SEM esses campos ganha valores
# neutros. Passar `pu=None` (etc.) EXPLICITAMENTE continua valendo -- é como
# se testa o papel que a regra deixa de fora.
from sqlalchemy import event as _event  # noqa: E402

from app.models import DebentureSpread as _DebentureSpread  # noqa: E402

_PADRAO_PAPEL = {"pu": 1000.0, "pct_pu_par": 100.0, "duration": 5.0}


@_event.listens_for(_DebentureSpread, "init")
def _papel_completo(target, args, kwargs):
    for campo, valor in _PADRAO_PAPEL.items():
        kwargs.setdefault(campo, valor)
