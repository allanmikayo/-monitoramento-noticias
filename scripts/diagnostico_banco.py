"""Diagnostico SOMENTE LEITURA do banco de producao (Supabase).

Criado em 08/09/2026 para investigar o aviso "Your project is currently
exhausting multiple resources" e a instabilidade do site.

NAO ESCREVE NADA. Le apenas catalogo e estatisticas do Postgres
(pg_stat_user_tables, pg_stat_user_indexes, pg_statio_user_tables,
pg_stat_statements, pg_stat_activity) e algumas contagens.

Usa a DATABASE_URL do .env -- a senha nunca aparece no relatorio.

    python -m scripts.diagnostico_banco

Escreve data/diagnostico_banco.txt.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import DATABASE_URL, engine  # noqa: E402

SAIDA = Path(__file__).resolve().parent.parent / "data" / "diagnostico_banco.txt"

linhas: list[str] = []


def out(s: str = "") -> None:
    linhas.append(s)
    try:
        print(s)
    except Exception:
        print(s.encode("ascii", "replace").decode("ascii"))


def secao(titulo: str) -> None:
    out()
    out("=" * 78)
    out(titulo)
    out("=" * 78)


def tabela(conn, sql: str, titulo: str, limite_col: int = 200) -> None:
    secao(titulo)
    try:
        res = conn.execute(text(sql))
        cols = list(res.keys())
        rows = res.fetchall()
    except Exception as exc:  # noqa: BLE001
        out(f"[falhou] {type(exc).__name__}: {str(exc)[:300]}")
        return
    if not rows:
        out("(sem linhas)")
        return
    larguras = [len(c) for c in cols]
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
            larguras[i] = max(larguras[i], len(v))
    out("  ".join(c.ljust(larguras[i]) for i, c in enumerate(cols)))
    out("  ".join("-" * larguras[i] for i in range(len(cols))))
    for vals in corpo:
        out("  ".join(v.ljust(larguras[i]) for i, v in enumerate(vals)))


def main() -> None:
    host = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else "(local)"
    out("DIAGNOSTICO DO BANCO -- somente leitura")
    out(f"gerado em {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}")
    out(f"destino: {host}")

    with engine.connect() as conn:
        tabela(conn, """
            select version() as versao,
                   current_setting('max_connections') as max_conn,
                   pg_size_pretty(pg_database_size(current_database())) as tamanho_banco
        """, "1. SERVIDOR")

        tabela(conn, """
            select extname, extversion from pg_extension order by extname
        """, "2. EXTENSOES INSTALADAS")

        tabela(conn, """
            select c.relname as tabela,
                   pg_size_pretty(pg_total_relation_size(c.oid)) as total,
                   pg_size_pretty(pg_relation_size(c.oid)) as dados,
                   pg_size_pretty(pg_indexes_size(c.oid)) as indices,
                   s.n_live_tup as vivas, s.n_dead_tup as mortas,
                   s.seq_scan as seq_scans, s.seq_tup_read as seq_linhas_lidas,
                   s.idx_scan as idx_scans,
                   s.n_tup_ins as ins, s.n_tup_upd as upd, s.n_tup_del as del,
                   s.last_autovacuum, s.last_autoanalyze
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            left join pg_stat_user_tables s on s.relid = c.oid
            where n.nspname = 'public' and c.relkind = 'r'
            order by pg_total_relation_size(c.oid) desc
        """, "3. TABELAS -- tamanho, linhas mortas e padrao de leitura")

        tabela(conn, """
            select relname as tabela, indexrelname as indice, idx_scan as usos,
                   pg_size_pretty(pg_relation_size(indexrelid)) as tamanho
            from pg_stat_user_indexes
            order by idx_scan asc, pg_relation_size(indexrelid) desc
        """, "4. INDICES -- quantas vezes cada um foi usado (usos=0 e' indice inutil)")

        tabela(conn, """
            select relname as tabela, heap_blks_read as blocos_do_disco,
                   heap_blks_hit as blocos_da_memoria,
                   round(100.0*heap_blks_hit/nullif(heap_blks_hit+heap_blks_read,0),1) as pct_memoria
            from pg_statio_user_tables
            order by heap_blks_read desc
            limit 20
        """, "5. LEITURA DE DISCO POR TABELA (blocos lidos do disco = custo de IO)")

        tabela(conn, """
            select round(total_exec_time)::bigint as ms_total,
                   calls as chamadas,
                   round(mean_exec_time::numeric,2) as ms_media,
                   shared_blks_read as blocos_disco,
                   shared_blks_hit as blocos_memoria,
                   rows as linhas,
                   left(regexp_replace(query, '\\s+', ' ', 'g'), 190) as consulta
            from pg_stat_statements
            order by total_exec_time desc
            limit 20
        """, "6. CONSULTAS MAIS CARAS POR TEMPO TOTAL")

        tabela(conn, """
            select shared_blks_read as blocos_disco,
                   calls as chamadas,
                   round(total_exec_time)::bigint as ms_total,
                   left(regexp_replace(query, '\\s+', ' ', 'g'), 190) as consulta
            from pg_stat_statements
            where shared_blks_read > 0
            order by shared_blks_read desc
            limit 20
        """, "6b. CONSULTAS QUE MAIS LERAM DO DISCO (o que queima o Disk IO)")

        tabela(conn, """
            select coalesce(nullif(application_name,''),'(sem nome)') as aplicacao,
                   state as estado, count(*) as conexoes,
                   max(now() - state_change) as mais_antiga
            from pg_stat_activity
            where datname = current_database()
            group by 1,2 order by 3 desc
        """, "7. CONEXOES ABERTAS AGORA")

        tabela(conn, """
            select indexname as indice, indexdef as definicao
            from pg_indexes where schemaname='public' and tablename='articles'
        """, "8. INDICES DA TABELA ARTICLES")

        tabela(conn, """
            select count(*) as artigos,
                   min(published_at) as publicado_mais_antigo,
                   max(published_at) as publicado_mais_novo,
                   min(found_at) as capturado_mais_antigo,
                   pg_size_pretty(sum(length(coalesce(body,''))))   as tamanho_corpo,
                   pg_size_pretty(sum(length(coalesce(snippet,'')))) as tamanho_resumo,
                   round(avg(length(coalesce(body,'')))) as corpo_medio_bytes,
                   max(length(coalesce(body,''))) as maior_corpo_bytes
            from articles
        """, "9. ARTICLES -- volume e peso do texto")

        tabela(conn, """
            select source_name as fonte, count(*) as artigos
            from articles group by 1 order by 2 desc limit 30
        """, "9b. ARTICLES POR FONTE")

        tabela(conn, """
            select count(*) as negocios,
                   min(data_negocio) as mais_antigo,
                   max(data_negocio) as mais_novo,
                   count(distinct data_negocio) as dias_guardados
            from negocios_b3
        """, "10. NEGOCIOS_B3 -- a poda de 5 dias esta funcionando?")

        tabela(conn, """
            select count(*) as linhas,
                   min(data) as mais_antigo, max(data) as mais_novo
            from negocios_b3_diario
        """, "10b. NEGOCIOS_B3_DIARIO -- o agregado esta sendo escrito?")

        tabela(conn, """
            select count(*) as linhas, min(data) as mais_antigo, max(data) as mais_novo
            from debenture_spreads
        """, "11. DEBENTURE_SPREADS -- ate quando o job diario chegou")

        tabela(conn, """
            select count(*) as linhas, min(data) as mais_antigo, max(data) as mais_novo
            from securitizado_spreads
        """, "11b. SECURITIZADO_SPREADS")

        tabela(conn, """
            select id, started_at, finished_at,
                   (finished_at - started_at) as duracao,
                   n_found as novos, triggered_by as disparado_por,
                   left(coalesce(errors,''), 160) as erros
            from run_logs order by started_at desc limit 20
        """, "12. ULTIMAS 20 VARREDURAS DE NOTICIAS (duracao e erros)")

        tabela(conn, """
            select count(*) as total,
                   count(*) filter (where finished_at is null) as sem_conclusao,
                   min(started_at) as primeira, max(started_at) as ultima
            from run_logs
        """, "12b. RUN_LOGS -- volume total")

        tabela(conn, """
            select count(*) as sessoes,
                   count(*) filter (where revoked) as revogadas,
                   count(*) filter (where expires_at < now()) as expiradas
            from sessions
        """, "13. SESSOES DE LOGIN acumuladas")

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text("\n".join(linhas), encoding="utf-8")
    out()
    out(f"Relatorio salvo em: {SAIDA}")


if __name__ == "__main__":
    main()
