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

import faulthandler
import io
import sys
import traceback
from datetime import datetime
from pathlib import Path

from app.db import Base, criar_engine_manutencao, ensure_schema
from app import models  # noqa: F401 — precisa importar pro metadata registrar tudo

# NÃO usar o `engine` de `app.db`: aquele é o da Vercel (pooler em modo
# TRANSAÇÃO, porta 6543, NullPool, 8s de connect_timeout). Em 09/09/2026 foi
# ele que fez este script morrer em
#     (ECHECKOUTTIMEOUT) unable to check out connection from the pool
#     after 15000ms in Transaction mode
# antes de chegar em qualquer migração -- enquanto a MESMA execução, com
# `:5432` forçado na variável de ambiente, conectava e rodava o DDL inteiro.
# `criar_engine_manutencao` faz essa troca para o pooler de SESSÃO sozinha.
engine = criar_engine_manutencao()

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
    (
        "ix_debenture_taxonomia",
        "CREATE INDEX IF NOT EXISTS ix_debenture_taxonomia "
        "ON debentures (classe, setor, subsetor)",
    ),
]


def _indice_existe(nome: str) -> bool:
    with engine.connect() as conn:
        if engine.dialect.name == "postgresql":
            sql = "select count(*) from pg_indexes where indexname = '%s'" % nome
        else:
            sql = "select count(*) from sqlite_master where type='index' and name = '%s'" % nome
        return bool(conn.exec_driver_sql(sql).scalar())


def conferir_colunas() -> list[str]:
    """Compara as colunas do MODELO com as que existem no banco de verdade.

    POR QUE (09/09/2026). O `init_db` de hoje disse "OK" com três colunas
    faltando -- `run_migrations` tinha engolido a falha, e o problema só
    apareceu depois, na criação de um índice que dependia delas. A mensagem
    apontava para o índice, não para a causa.

    UMA CONSULTA, NÃO 29 (mesma data, mais tarde). A primeira versão usava
    `inspect(engine).get_columns(nome)` numa volta por tabela -- 29 idas ao
    banco, cada uma reflexão completa do SQLAlchemy. Num banco saturado isso
    é 29 chances de estourar o tempo. `_catalogo_colunas` responde tudo de
    uma vez, e é o mesmo mapa que as migrações já usam.
    """
    from app.db import _catalogo_colunas

    nomes = list(Base.metadata.tables)
    catalogo = _catalogo_colunas(engine, nomes)
    if catalogo is None:  # SQLite -- cai na reflexão, que lá é barata
        from sqlalchemy import inspect

        inspetor = inspect(engine)
        catalogo = {n: {c["name"]: None for c in inspetor.get_columns(n)}
                    for n in inspetor.get_table_names()}

    faltando: list[str] = []
    for nome, tabela in Base.metadata.tables.items():
        no_banco = catalogo.get(nome)
        if no_banco is None:
            continue  # tabela inexistente já é reportada em outro lugar
        faltando.extend(
            f"{nome}.{coluna.name}" for coluna in tabela.columns
            if coluna.name not in no_banco
        )
    return faltando


def _colunas_sem_migracao(faltando: list[str]) -> set[str]:
    """Quais das colunas ausentes nem sequer tem um ALTER escrito.

    Distingue "o comando falhou" de "o comando nao existe" -- que pedem
    reacoes opostas: tentar de novo, ou escrever a migracao.
    """
    import inspect as _inspect
    import re

    from app import db as _db

    fonte = _inspect.getsource(_db.run_migrations)
    previstas = set()
    for comando in re.findall(r'^\s+"(ALTER TABLE [^"]+)"', fonte, re.M):
        m = _db._RE_ADD.match(comando)
        if m:
            previstas.add(f"{m.group(1)}.{m.group(2)}")
    return {c for c in faltando if c not in previstas}


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
                # `SET statement_timeout` é do Postgres; no SQLite local o
                # comando nem existe (e não há limite de tempo pra estourar).
                if engine.dialect.name == "postgresql":
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


def conferir_conexao() -> bool:
    """Uma conexão de teste antes de qualquer trabalho.

    POR QUE. Sem isto, um banco fora do ar vira um traceback de noventa
    linhas cujo topo diz `select pg_catalog.version()` -- o que faz procurar
    defeito no script. A pergunta que interessa ("o banco atendeu?") tem que
    ser respondida na primeira linha da saída.
    """
    # `version()` e' funcao do Postgres; no SQLite o nome e' outro. Sem
    # este desvio, rodar o init_db contra o banco local (DATABASE_URL vazio)
    # morre em "no such function: version" -- um erro sobre a CONFERENCIA,
    # que faz procurar defeito no banco quando o banco esta' bem.
    sql_versao = (
        "select sqlite_version()"
        if engine.dialect.name == "sqlite"
        else "select version()"
    )
    try:
        with engine.connect() as conn:
            versao = conn.exec_driver_sql(sql_versao).scalar()
    except Exception as exc:  # noqa: BLE001
        texto = str(exc)
        print("  NAO CONECTOU.")
        print(f"  {type(exc).__name__}: {texto.splitlines()[0][:180]}")
        if "ECHECKOUTTIMEOUT" in texto:
            print("     O pooler nao tinha conexao livre para dar. E saturacao do")
            print("     projeto, nao problema do script. Espere alguns minutos,")
            print("     ou pause os jobs do cron-job.org, e rode de novo.")
        elif "authentication did not complete" in texto:
            print("     A conexao abriu mas o handshake nao terminou -- a instancia")
            print("     esta sem folga de CPU. Mesma receita: esperar ou aliviar a carga.")
        elif "password" in texto.lower() or "role" in texto.lower():
            print("     Parece credencial. Confira o DATABASE_URL do .env.")
        return False
    rotulo = str(versao).split(" on ")[0]
    if engine.dialect.name == "sqlite":
        # Aviso, nao erro: rodar contra o SQLite local e' legitimo (e' o
        # modo offline do app), mas quase sempre e' engano -- significa que
        # o DATABASE_URL do .env nao foi lido, e as migracoes vao acontecer
        # num arquivo que ninguem usa em vez de no banco da nuvem.
        print(f"  ok -- SQLite {rotulo} (banco LOCAL)")
        print("     ATENCAO: sem DATABASE_URL no .env, isto altera o arquivo")
        print("     local em data/, nao o Postgres da OCI. Se a intencao era")
        print("     o banco da nuvem, confira se a linha do .env comeca com")
        print("     DATABASE_URL= antes de postgresql+psycopg://")
    else:
        print(f"  ok -- {rotulo}")
    return True


def dropar_views() -> None:
    """Tira as views da frente antes do DDL nas tabelas de base.

    POR QUE (09/09/2026). Uma view guarda dependência nas COLUNAS que ela
    seleciona, e o Postgres recusa mexer no tipo de qualquer uma delas:

        cannot alter type of a column used by a view or rule
        DETAIL: rule _RETURN on view v_spread_rating depends on column "codigo"

    Os `ALTER COLUMN ... TYPE` de `debentures` batiam nisso desde que a
    view nasceu (04/08/2026) -- em silêncio, porque `run_migrations`
    engolia exceção. Hoje o catálogo faz esses comandos nem saírem quando
    a coluna já está larga, que é o caso normal; este drop é para o dia em
    que o alargamento for de verdade necessário.

    É seguro porque `criar_views` é DROP + CREATE (idempotente) e roda no
    `finally` do `main`, então a view volta mesmo se o DDL do meio falhar.
    """
    from app.spreads.views import VIEWS

    with engine.connect() as conn:
        for nome in VIEWS:
            conn.exec_driver_sql(f"DROP VIEW IF EXISTS {nome}")
            conn.commit()
    print(f"  removidas: {', '.join(VIEWS)}")


def recriar_views() -> None:
    """Recria as views. Nunca levanta -- ela roda no `finally`, e uma
    exceção aqui esconderia a falha de verdade que trouxe o script até
    aqui. Se falhar, grita, porque a aba Banco de Dados e o
    `app/spreads/analitico.py` leem `v_spread_rating`."""
    try:
        from app.spreads.views import criar_views

        criadas = criar_views(engine)
        print(f"  recriadas: {', '.join(criadas)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  !! FALHOU ao recriar as views -- {type(exc).__name__}: {str(exc)[:200]}")
        print("     `v_spread_rating` NAO esta no banco. Rode de novo; a aba")
        print("     Banco de Dados e o analitico dependem dela.")


def main() -> int:
    destino = str(engine.url).split("@")[-1]  # sem usuário/senha no log
    print(f"Banco: ...@{destino}")
    from app.db import DATABASE_URL, PORTA_POOLER_SESSAO, PORTA_POOLER_TRANSACAO

    if f":{PORTA_POOLER_TRANSACAO}/" in DATABASE_URL and engine.url.port == PORTA_POOLER_SESSAO:
        print(f"  (o .env aponta para {PORTA_POOLER_TRANSACAO}, o pooler de transacao;")
        print(f"   DDL vai pelo de sessao, {PORTA_POOLER_SESSAO} -- ver criar_engine_manutencao)")
    print(f"Tabelas registradas no modelo: {len(Base.metadata.tables)}")

    print("Conferindo a conexao...")
    if not conferir_conexao():
        return 1

    from sqlalchemy import inspect
    antes = set(inspect(engine).get_table_names())

    # A view `v_spread_rating` mora aqui desde 20/08/2026 (a etapa `periodos`
    # da rodada noturna, onde ela era criada, saiu junto com o pipeline de
    # ratings). Como ela trava `ALTER COLUMN` nas tabelas de base, sai antes
    # do DDL e volta no `finally` -- ver dropar_views().
    print("Removendo views (elas travam ALTER COLUMN nas tabelas de base)...")
    dropar_views()

    faltando: list[str] = []
    depois = antes  # se o DDL abaixo explodir, o `finally` ainda precisa rodar
    try:
        print("Criando o que falta...")
        ensure_schema(force=True, eng=engine)

        depois = set(inspect(engine).get_table_names())
        novas = sorted(depois - antes)
        print(f"  criadas agora: {', '.join(novas) if novas else '(nenhuma, já estava tudo lá)'}")

        # (as migrações de coluna já rodaram dentro do ensure_schema acima)
        print("Conferindo colunas do modelo contra o banco...")
        faltando = conferir_colunas()
        if faltando:
            print(f"  !! {len(faltando)} coluna(s) do models.py NÃO existem no banco:")
            # DUAS CAUSAS DIFERENTES, e a diferença é tudo (09/09/2026).
            #
            # A mensagem antiga dizia sempre "rode de novo com o banco
            # respondendo". Numa execução em que o banco respondia
            # perfeitamente e faltavam `securitizados.setor/subsetor/
            # grupo_economico`, isso mandou o Allan para o lado errado: não
            # havia ALTER nenhum para aquelas três colunas. Nenhuma
            # quantidade de tentar de novo cria o que ninguém pediu.
            sem_migracao = _colunas_sem_migracao(faltando)
            for item in faltando:
                marca = "   <-- NAO HA MIGRACAO PARA ESTA" if item in sem_migracao else ""
                print(f"     - {item}{marca}")
            print("     Índices e consultas que dependem delas vão falhar.")
            if sem_migracao:
                print()
                print("     As marcadas acima estao no models.py e NAO tem ALTER em")
                print("     app/db.py::run_migrations. Rodar de novo nao resolve --")
                print("     o comando que as criaria nao existe. Falta acrescentar la:")
                for item in sorted(sem_migracao):
                    tabela, coluna = item.split(".", 1)
                    print(f'       "ALTER TABLE {tabela} ADD COLUMN {coluna} <TIPO>",')
            if set(faltando) - sem_migracao:
                print("     As demais tem migracao: o comando saiu e nao pegou.")
                print("     Rode de novo com o banco menos ocupado.")
        else:
            print("  todas as colunas do modelo existem no banco")

        print("Criando índices que faltam...")
        criar_indices()
    finally:
        print("Recriando views...")
        recriar_views()

    tabelas_faltando = sorted(set(Base.metadata.tables) - depois)
    if tabelas_faltando:
        print(f"AVISO: ainda faltam as tabelas {tabelas_faltando}")
        return 1
    if faltando:
        return 1
    print(f"OK — {len(depois)} tabelas no banco.")
    return 0


# ---------------------------------------------------------------------------
# Saída em arquivo, além da tela (09/09/2026)
#
# POR QUE. A execução de 08/09 parou logo depois de "Tabelas registradas no
# modelo: 29" e não deu para saber se o script morreu ali ou se só a
# colagem da janela do PowerShell tinha sido cortada. Duas coisas muito
# diferentes: a primeira seria o mesmo `Segmentation fault` que derrubou os
# jobs no GitHub, agora também na máquina local; a segunda, nada.
#
# Agora tudo que aparece na tela vai junto para `data/init_db.txt`, com
# flush a cada linha -- então mesmo um processo abatido no meio deixa
# escrito até onde chegou. E o `faulthandler` aponta o mesmo arquivo: se for
# segfault, a pilha cai lá dentro em vez de sumir com a janela.
# ---------------------------------------------------------------------------

LOG = Path(__file__).resolve().parent.parent / "data" / "init_db.txt"


class _Tee:
    """Escreve nos dois lugares e dá flush em cada linha."""

    def __init__(self, *saidas):
        self.saidas = saidas

    def write(self, texto):
        for saida in self.saidas:
            try:
                saida.write(texto)
                saida.flush()
            except Exception:  # noqa: BLE001 -- console do Windows com acento
                pass
        return len(texto)

    def flush(self):
        for saida in self.saidas:
            try:
                saida.flush()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with io.open(LOG, "w", encoding="utf-8") as arquivo:
        # O faulthandler precisa de um arquivo próprio aberto o tempo todo:
        # ele escreve a pilha SEM passar pelo Python, direto no descritor.
        faulthandler.enable(file=arquivo)
        original = sys.stdout
        sys.stdout = _Tee(original, arquivo)
        try:
            arquivo.write(f"init_db -- {datetime.now().astimezone():%d/%m/%Y %H:%M:%S}\n")
            codigo = main()
        except Exception:
            traceback.print_exc(file=arquivo)
            traceback.print_exc(file=original)
            codigo = 1
        finally:
            sys.stdout = original
            print(f"\n(relatorio completo em {LOG})")
    sys.exit(codigo)
