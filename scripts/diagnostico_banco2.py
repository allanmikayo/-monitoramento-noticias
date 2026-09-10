"""Parte 2 do diagnostico SOMENTE LEITURA (08/09/2026).

A parte 1 travou na consulta de pg_stat_statements. Aqui cada bloco roda
em conexao propria, com timeout de 20s, e o arquivo e' gravado depois de
CADA bloco -- se um travar, o resto do relatorio sobrevive.

    python -m scripts.diagnostico_banco2

Escreve data/diagnostico_banco2.txt.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402

SAIDA = Path(__file__).resolve().parent.parent / "data" / "diagnostico_banco2.txt"
linhas: list[str] = []


def out(s: str = "") -> None:
    linhas.append(s)
    try:
        print(s, flush=True)
    except Exception:
        print(s.encode("ascii", "replace").decode("ascii"), flush=True)


def salvar() -> None:
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text("\n".join(linhas), encoding="utf-8")


def bloco(titulo: str, sql: str, timeout_ms: int = 20000, limite_col: int = 190) -> None:
    out()
    out("=" * 78)
    out(titulo)
    out("=" * 78)
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET statement_timeout = {timeout_ms}"))
            res = conn.execute(text(sql))
            cols = list(res.keys())
            rows = res.fetchall()
    except Exception as exc:  # noqa: BLE001
        out(f"[falhou] {type(exc).__name__}: {str(exc)[:400]}")
        salvar()
        return
    if not rows:
        out("(sem linhas)")
        salvar()
        return
    larg = [len(c) for c in cols]
    corpo = []
    for r in rows:
        vals = []
        for v in r:
            s = "" if v is None else str(v)
            if len(s) > limite_col:
                s = s[: limite_col - 3] + "..."
            vals.append(s)
        corpo.append(vals)
        for i, v in enumerate(vals):
            larg[i] = max(larg[i], len(v))
    out("  ".join(c.ljust(larg[i]) for i, c in enumerate(cols)))
    out("  ".join("-" * larg[i] for i in range(len(cols))))
    for vals in corpo:
        out("  ".join(v.ljust(larg[i]) for i, v in enumerate(vals)))
    salvar()


def main() -> None:
    out("DIAGNOSTICO PARTE 2 -- somente leitura")
    out(f"gerado em {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}")
    salvar()

    bloco("A. NEGOCIOS_B3 -- a captura do balcao esta viva?", """
        select count(*) as negocios,
               min(data_negocio) as mais_antigo,
               max(data_negocio) as mais_novo,
               max(captured_at) as ultima_captura
        from negocios_b3
    """)

    bloco("B. NEGOCIOS_B3_DIARIO -- ate quando o agregado chegou", """
        select count(*) as linhas, min(data) as mais_antigo, max(data) as mais_novo,
               count(*) filter (where data >= current_date - 10) as linhas_ultimos_10_dias
        from negocios_b3_diario
    """)

    bloco("C. SPREADS -- ate quando os jobs diarios chegaram", """
        select 'debenture_spreads' as tabela, count(*) as linhas,
               min(data)::text as mais_antigo, max(data)::text as mais_novo
        from debenture_spreads
        union all
        select 'securitizado_spreads', count(*), min(data)::text, max(data)::text
        from securitizado_spreads
        union all
        select 'ntnb_referencia', count(*), min(data)::text, max(data)::text
        from ntnb_referencia
    """)

    bloco("D. ULTIMAS 25 VARREDURAS DE NOTICIAS", """
        select started_at, (finished_at - started_at) as duracao,
               n_found as novos, triggered_by as origem,
               left(coalesce(errors,'[]'), 150) as erros
        from run_logs order by started_at desc limit 25
    """)

    bloco("E. VARREDURAS POR DIA (ultimos 15 dias)", """
        select started_at::date as dia, count(*) as rodadas,
               count(*) filter (where finished_at is null) as sem_terminar,
               round(avg(extract(epoch from (finished_at - started_at)))) as seg_media,
               sum(n_found) as novos
        from run_logs
        where started_at >= current_date - 15
        group by 1 order by 1 desc
    """)

    bloco("F. ARTICLES -- volume e peso do texto", """
        select count(*) as artigos,
               min(found_at)::date as capturado_desde,
               max(found_at) as ultima_captura,
               pg_size_pretty(sum(length(coalesce(body,'')))::bigint)    as peso_corpo,
               pg_size_pretty(sum(length(coalesce(snippet,'')))::bigint) as peso_resumo,
               round(avg(length(coalesce(body,'')))) as corpo_medio,
               max(length(coalesce(body,''))) as maior_corpo
        from articles
    """)

    bloco("G. ARTICLES POR FONTE", """
        select source_name as fonte, count(*) as artigos,
               max(found_at)::date as ultima_captura
        from articles group by 1 order by 2 desc
    """)

    # pg_stat_statements estourou o timeout na parte 1. Aqui o corte (LIMIT)
    # vem ANTES do regexp_replace, entao a formatacao roda em 15 linhas em vez
    # de em todas as milhares de entradas do cache de consultas.
    bloco("G2. QUANTAS ENTRADAS TEM O pg_stat_statements", """
        select count(*) as entradas from pg_stat_statements
    """, timeout_ms=15000)

    bloco("H. CONSULTAS MAIS CARAS POR TEMPO TOTAL", """
        with topo as (
          select query, total_exec_time, calls, mean_exec_time, shared_blks_read
          from pg_stat_statements
          where dbid = (select oid from pg_database where datname = current_database())
          order by total_exec_time desc limit 15
        )
        select round(total_exec_time)::bigint as ms_total, calls as chamadas,
               round(mean_exec_time::numeric,2) as ms_media,
               shared_blks_read as blocos_disco,
               left(regexp_replace(query, '\\s+', ' ', 'g'), 150) as consulta
        from topo order by total_exec_time desc
    """, timeout_ms=25000)

    bloco("I. CONSULTAS MAIS EXECUTADAS (volume)", """
        with topo as (
          select query, total_exec_time, calls, mean_exec_time
          from pg_stat_statements
          where dbid = (select oid from pg_database where datname = current_database())
          order by calls desc limit 15
        )
        select calls as chamadas, round(total_exec_time)::bigint as ms_total,
               round(mean_exec_time::numeric,3) as ms_media,
               left(regexp_replace(query, '\\s+', ' ', 'g'), 150) as consulta
        from topo order by calls desc
    """, timeout_ms=25000)

    bloco("J. ESCRITA POR CONSULTA (o que gera WAL)", """
        with topo as (
          select query, total_exec_time, calls, shared_blks_dirtied, wal_bytes
          from pg_stat_statements
          where dbid = (select oid from pg_database where datname = current_database())
          order by wal_bytes desc limit 15
        )
        select round(total_exec_time)::bigint as ms_total, calls as chamadas,
               shared_blks_dirtied as blocos_sujos, wal_bytes,
               left(regexp_replace(query, '\\s+', ' ', 'g'), 150) as consulta
        from topo order by wal_bytes desc
    """, timeout_ms=25000)

    bloco("K. CONEXOES AGORA", """
        select coalesce(nullif(application_name,''),'(sem nome)') as aplicacao,
               state as estado, count(*) as conexoes,
               max(now() - state_change) as mais_antiga
        from pg_stat_activity where datname = current_database()
        group by 1,2 order by 3 desc
    """)

    bloco("L. INDICES DA TABELA ARTICLES", """
        select indexname as indice, indexdef as definicao
        from pg_indexes where schemaname='public' and tablename='articles'
    """)

    out()
    out(f"Relatorio salvo em: {SAIDA}")
    salvar()


if __name__ == "__main__":
    main()
