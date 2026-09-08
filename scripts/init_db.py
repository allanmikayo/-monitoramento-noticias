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

from app.db import Base, engine, ensure_schema
from app import models  # noqa: F401 — precisa importar pro metadata registrar tudo

# ÍNDICES QUE PRECISAM DE DDL EXPLÍCITO (08/09/2026).
#
# `Base.metadata.create_all` só cria TABELA que não existe -- ele nem olha
# os índices de uma tabela que já está lá. Então índice novo em tabela
# antiga não nasce sozinho em produção: tem que vir por aqui.
#
# Sem CONCURRENTLY de propósito: a conexão do projeto é o pooler da Supabase
# em MODO TRANSAÇÃO (porta 6543), e `CREATE INDEX CONCURRENTLY` usa várias
# transações internas na mesma sessão -- justamente o que um pooler de
# transação não garante. Em `articles` (37 MB, 33 mil linhas) o índice sai
# em poucos segundos de lock, o que é aceitável; num banco grande, valeria
# rodar pela conexão direta com CONCURRENTLY.
INDICES = [
    (
        "ix_articles_data",
        "CREATE INDEX IF NOT EXISTS ix_articles_data "
        "ON articles (coalesce(published_at, found_at) DESC)",
    ),
]


def _indice_existe(nome: str) -> bool:
    with engine.connect() as conn:
        if engine.dialect.name == "postgresql":
            sql = "select count(*) from pg_indexes where indexname = '%s'" % nome
        else:
            sql = "select count(*) from sqlite_master where type='index' and name = '%s'" % nome
        return bool(conn.exec_driver_sql(sql).scalar())


def criar_indices() -> None:
    """Cria os índices que faltam, com folga de tempo -- e CONFERE depois.

    BUG REAL (08/09/2026, no mesmo dia em que o índice foi criado): a
    primeira versão rodava o CREATE INDEX no limite de tempo padrão da
    sessão e engolia a falha num "AVISO" no meio do log. Com o banco
    saturado -- que é EXATAMENTE a situação em que o índice mais importa --
    o comando não conseguia vez e morria por `statement timeout`. O
    `init_db` terminava dizendo "OK", e o índice não estava no banco. Só
    apareceu porque o `estado_dos_robos` foi conferir.

    Três correções: o timeout sobe para 15 minutos DENTRO da mesma
    transação do DDL (o pooler da Supabase é modo transação -- um `SET`
    solto pode cair noutra conexão), o script confere no catálogo em vez de
    confiar no "não deu erro", e a falha aparece em alto e bom som no fim.
    """
    houve_falha = False
    for nome, ddl in INDICES:
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("SET statement_timeout = '15min'")
                conn.exec_driver_sql(ddl)
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            houve_falha = True
            print(f"  {nome}: FALHOU -- {type(exc).__name__}: {str(exc)[:200]}")
            continue
        if _indice_existe(nome):
            print(f"  {nome}: ok")
        else:
            houve_falha = True
            print(f"  {nome}: FALHOU -- o comando passou mas o índice não existe")
    if houve_falha:
        print()
        print("  !! ALGUM INDICE NAO FOI CRIADO -- e isso nao e detalhe: sem o")
        print("     ix_articles_data, toda carga do dashboard varre a tabela")
        print("     `articles` inteira. Tente de novo com o banco menos ocupado")
        print("     (fora do horario dos jobs noturnos).")


def main() -> int:
    destino = str(engine.url).split("@")[-1]  # sem usuário/senha no log
    print(f"Banco: ...@{destino}")
    print(f"Tabelas registradas no modelo: {len(Base.metadata.tables)}")

    from sqlalchemy import inspect
    antes = set(inspect(engine).get_table_names())

    print("Criando o que falta...")
    ensure_schema(force=True)

    depois = set(inspect(engine).get_table_names())
    novas = sorted(depois - antes)
    print(f"  criadas agora: {', '.join(novas) if novas else '(nenhuma, já estava tudo lá)'}")

    # (as migrações de coluna já rodaram dentro do ensure_schema acima)
    print("Criando índices que faltam...")
    criar_indices()

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
