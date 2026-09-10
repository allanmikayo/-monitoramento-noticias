"""Limpeza pontual do banco -- rodar UMA vez, depois do conserto de 08/09/2026.

O QUE FAZ, E POR QUÊ

1. REINDEX em `negocios_b3`. A tabela está com 0 linhas vivas e 0 byte de
   dados, mas os índices ocupam 25 MB -- 13 MB só o de `trade_code`. É
   bloat: o Postgres não devolve espaço de índice depois de um DELETE em
   massa, e essa tabela levou 419.587 inserções e 419.587 exclusões. Só um
   REINDEX (ou um TRUNCATE) devolve.

   REINDEX e não TRUNCATE de propósito: TRUNCATE apagaria dado se a captura
   tiver voltado a funcionar entre o diagnóstico e agora. REINDEX reconstrói
   os índices sem tocar em uma linha sequer -- mesmo ganho de espaço, risco
   zero.

2. VACUUM ANALYZE nas tabelas quentes. `articles` estava com o autovacuum de
   29/08 e o autoanalyze de 02/09; estatística velha faz o planejador
   escolher plano ruim -- e agora que existe o índice `ix_articles_data`,
   ele precisa de estatística fresca pra saber que vale usá-lo.

3. Confere se o `ix_articles_data` chegou mesmo no banco.

O QUE NÃO FAZ, DE PROPÓSITO -- ver `relatorio_indices_ociosos()` no fim.

    python -m scripts.limpeza_pontual
    python -m scripts.limpeza_pontual --simular    # só mede e relata

SOMENTE MANUTENÇÃO: não apaga linha nenhuma, em nenhuma tabela.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402

# Tabelas que valem VACUUM ANALYZE: as que recebem escrita de verdade.
TABELAS_QUENTES = [
    "articles", "article_company", "article_sector",
    "negocios_b3", "negocios_b3_diario",
    "debentures", "debenture_spreads",
    "securitizados", "securitizado_spreads",
    "run_logs", "sessions",
]


def _autocommit():
    """VACUUM e REINDEX não podem rodar dentro de transação."""
    return engine.connect().execution_options(isolation_level="AUTOCOMMIT")


def medir() -> dict[str, tuple[str, str, int]]:
    sql = """
        select c.relname,
               pg_size_pretty(pg_total_relation_size(c.oid)),
               pg_size_pretty(pg_indexes_size(c.oid)),
               pg_total_relation_size(c.oid)
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'r'
    """
    with engine.connect() as conn:
        conn.execute(text("SET statement_timeout = 30000"))
        return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(text(sql))}


def conferir_indice() -> bool:
    with engine.connect() as conn:
        conn.execute(text("SET statement_timeout = 15000"))
        return bool(conn.execute(text(
            "select count(*) from pg_indexes "
            "where schemaname='public' and indexname='ix_articles_data'"
        )).scalar())


def reindexar_negocios_b3(simular: bool) -> None:
    with engine.connect() as conn:
        conn.execute(text("SET statement_timeout = 120000"))
        linhas = conn.execute(text("select count(*) from negocios_b3")).scalar()
    print(f"  negocios_b3 tem {linhas} linha(s) agora.")
    if simular:
        print("  [simulado] REINDEX TABLE negocios_b3")
        return
    if linhas and linhas > 0:
        print("  ATENÇÃO: a tabela NÃO está vazia -- o REINDEX vai travá-la por alguns")
        print("  segundos. Se estiver em horário de pregão, prefira rodar mais tarde.")
    with _autocommit() as conn:
        conn.execute(text("SET statement_timeout = 300000"))
        conn.execute(text("REINDEX TABLE negocios_b3"))
    print("  REINDEX concluído.")


def vacuum_analyze(simular: bool) -> None:
    for tabela in TABELAS_QUENTES:
        if simular:
            print(f"  [simulado] VACUUM (ANALYZE) {tabela}")
            continue
        try:
            with _autocommit() as conn:
                conn.execute(text("SET statement_timeout = 300000"))
                conn.execute(text(f"VACUUM (ANALYZE) {tabela}"))
            print(f"  {tabela}: ok")
        except Exception as exc:  # noqa: BLE001
            print(f"  {tabela}: AVISO -- {type(exc).__name__}: {str(exc)[:140]}")


def relatorio_indices_ociosos() -> None:
    """Lista índices sem uso -- e explica por que este script não os apaga.

    A tentação é apagar tudo que aparece com `idx_scan = 0`. Quase nada aqui
    justifica o risco:

    - Índices de `issuers` / `issuer_ratings` / `issuer_rating_*`: as tabelas
      estão VAZIAS desde a remoção do pipeline de rating em 20/08. Índice de
      tabela vazia ocupa 8 kB e não custa escrita nenhuma. Apagar não ganha
      nada e atrapalha o dia em que o assunto voltar.
    - Índices de `securitizados` / `securitizado_spreads`: tabelas de 300 kB
      que recebem ~700 inserções POR ANO. Irrelevante.
    - `negocios_b3_pkey` e `negocios_b3_diario_pkey`: estes SIM custam --
      são chave primária sintética (`id`) numa tabela que recebe ~17 mil
      inserções por pregão, e nenhuma consulta do projeto usa. Só que apagar
      chave primária é mudança de MODELO, não faxina: implicaria promover
      `trade_code` a chave primária em `app/models.py`, migrar a tabela e
      reconferir tudo que faz join. É uma decisão separada, com ganho real
      mas não urgente -- fica registrada aqui em vez de ser feita de
      surpresa numa rotina chamada "limpeza".
    """
    sql = """
        select relname, indexrelname, idx_scan,
               pg_size_pretty(pg_relation_size(indexrelid))
        from pg_stat_user_indexes
        where idx_scan = 0 and pg_relation_size(indexrelid) > 65536
        order by pg_relation_size(indexrelid) desc
    """
    print()
    print("Índices sem nenhum uso e maiores que 64 kB (relatório, nada é apagado):")
    try:
        with engine.connect() as conn:
            conn.execute(text("SET statement_timeout = 30000"))
            linhas = list(conn.execute(text(sql)))
    except Exception as exc:  # noqa: BLE001
        print(f"  [falhou] {type(exc).__name__}")
        return
    if not linhas:
        print("  (nenhum)")
        return
    for tabela, indice, _usos, tamanho in linhas:
        print(f"  {tabela:<24} {indice:<36} {tamanho}")
    print("  -> por que nenhum é apagado aqui: ver a docstring de")
    print("     relatorio_indices_ociosos() em scripts/limpeza_pontual.py")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--simular", action="store_true", help="mede e relata, sem executar")
    args = ap.parse_args()

    print(f"limpeza pontual -- {datetime.now().astimezone():%d/%m/%Y %H:%M:%S}")
    print(f"destino: {str(engine.url).split('@')[-1]}\n")

    print("0. Índice do conserto de 08/09")
    print("   ix_articles_data:",
          "PRESENTE" if conferir_indice() else "AUSENTE -- rode `python -m scripts.init_db` antes")

    antes = medir()
    total_antes = sum(v[2] for v in antes.values())
    print(f"\n   tamanho total das tabelas do projeto: {total_antes/1024/1024:.1f} MB")
    if "negocios_b3" in antes:
        print(f"   negocios_b3: {antes['negocios_b3'][0]} (sendo {antes['negocios_b3'][1]} de índice)")

    print("\n1. REINDEX de negocios_b3")
    reindexar_negocios_b3(args.simular)

    print("\n2. VACUUM ANALYZE das tabelas quentes")
    vacuum_analyze(args.simular)

    if not args.simular:
        depois = medir()
        total_depois = sum(v[2] for v in depois.values())
        print(f"\n3. Resultado")
        if "negocios_b3" in depois:
            print(f"   negocios_b3: {antes.get('negocios_b3', ('?',))[0]} -> {depois['negocios_b3'][0]}")
        print(f"   total: {total_antes/1024/1024:.1f} MB -> {total_depois/1024/1024:.1f} MB "
              f"({(total_antes-total_depois)/1024/1024:+.1f} MB)")

    relatorio_indices_ociosos()
    print("\nconcluído.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
