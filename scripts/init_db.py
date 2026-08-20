"""Cria as tabelas que faltam e roda as migrações — de FORA do servidor.

Por que existe (13/08/2026): o `@app.on_event("startup")` do app.py faz
`Base.metadata.create_all` + `run_migrations` a cada cold start. São 29
tabelas conferidas uma a uma mais ~17 ALTERs, cada um uma ida e volta até o
Supabase. Quando o commit do Hub (issuers/ratings/securitizados) e o do
Repositório de Relatórios adicionaram tabelas novas, a criação delas passou
dos 10s de `maxDuration` do vercel.json: a função morria no meio, as tabelas
ficavam pela metade e a requisição seguinte recomeçava do zero — o site
ficou preso em 504 GATEWAY_TIMEOUT em todas as rotas.

Rodar isto uma vez, da sua máquina (conexão direta, sem limite de tempo),
resolve: com as tabelas já existentes, o `create_all` do boot vira só
conferência e cabe folgado no tempo.

Uso, no PowerShell dentro de credit_monitor:

    python -m scripts.init_db

Ele lê o DATABASE_URL do .env, o mesmo que a Vercel usa. Rode de novo toda
vez que adicionar tabela ou coluna nova em models.py.
"""
from __future__ import annotations

import sys

from app.db import Base, engine, run_migrations
from app import models  # noqa: F401 — precisa importar pro metadata registrar tudo


def main() -> int:
    destino = str(engine.url).split("@")[-1]  # sem usuário/senha no log
    print(f"Banco: ...@{destino}")
    print(f"Tabelas registradas no modelo: {len(Base.metadata.tables)}")

    from sqlalchemy import inspect
    antes = set(inspect(engine).get_table_names())

    print("Criando o que falta...")
    Base.metadata.create_all(engine)

    depois = set(inspect(engine).get_table_names())
    novas = sorted(depois - antes)
    print(f"  criadas agora: {', '.join(novas) if novas else '(nenhuma, já estava tudo lá)'}")

    print("Rodando migrações de coluna...")
    run_migrations()

    # A view `v_spread_rating` era criada dentro da etapa `periodos` da
    # rodada noturna. Essa etapa saiu em 20/08/2026 junto com o pipeline de
    # ratings, então a criação da view mudou de lugar para cá -- é aqui que
    # mora o resto do DDL. A view continua sendo lida por
    # app/spreads/analitico.py e pela aba Banco de Dados; ela funciona com a
    # tabela de ratings vazia (as colunas de rating só ficam nulas).
    print("Criando/atualizando views...")
    from app.spreads.views import criar_views
    criar_views(engine)

    faltando = sorted(set(Base.metadata.tables) - depois)
    if faltando:
        print(f"AVISO: ainda faltam {faltando}")
        return 1
    print(f"OK — {len(depois)} tabelas no banco.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
