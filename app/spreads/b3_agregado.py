"""Agregação diária do negócio a negócio da B3.

Transforma as operações individuais em uma linha por Código+Data — ver o
cabeçalho de `models.NegocioB3Diario` para o porquê (resumo: 6,3 milhões
de linhas em dois anos viram ~650 mil, e nenhuma análise da tela consome
a operação individual).

Duas entradas:

- `agregar_do_banco()` — refaz o agregado a partir de `negocios_b3` que já
  está gravado. É o caminho para o dado que já existe.
- `agregar_linhas()` — agrega direto da captura, sem passar pelo bruto.
  É o caminho do backfill longo, em que guardar o bruto de 500 dias é
  justamente o que se quer evitar.
"""
from __future__ import annotations

import csv
import gzip
import logging
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ..models import NegocioB3, NegocioB3Diario

logger = logging.getLogger(__name__)

# Só estes três instrumentos são guardados. A fonte da B3 traz MUITO mais
# (CFF, CDCA, COE, CPR, LF, LFSN...) e o filtro já existe na captura
# (`b3_trades.INSTRUMENT_TYPES`); repetido aqui de propósito, como segunda
# barreira: se um dia a captura mudar ou alguém carregar dado por outro
# caminho, o agregado continua limpo. Pedido explícito do Allan
# (12/08/2026): "garanta que você está armazenando apenas CRA, CRI e
# debêntures".
INSTRUMENTOS = {"DEB", "CRI", "CRA"}

# DUAS RETENÇÕES DIFERENTES, de propósito — desenho do Allan (12/08/2026):
#
#   CONSOLIDADO (`negocios_b3_diario`)  -> para SEMPRE
#   NEGÓCIO A NEGÓCIO (`negocios_b3`)   -> últimos 5 dias
#
# São duas visões separadas na aba Balcão B3: o consolidado responde
# "como está a negociação deste papel/emissor no tempo" (taxa e volume por
# ticker), e o negócio a negócio responde "o que aconteceu esta semana",
# que é uma pergunta de curtíssimo prazo e não precisa de histórico.
#
# É o desenho que resolve o problema de espaço sem perder análise:
#
#   guardando o bruto para sempre  ->  ~740 MB/ano, estoura em ~6 meses
#   bruto com 5 dias (regime)      ->  ~15 MB FIXOS, não cresce
#   consolidado para sempre        ->  ~34 MB/ano
#
# Com isso o banco inteiro cresce ~76 MB/ano e o plano gratuito do
# Supabase dura ~5 anos. Ver ESTRUTURA_DADOS.md.
#
# HISTÓRICO: uma versão anterior tinha 90 dias e a poda rodava escondida
# na rodada noturna; outra tinha retenção infinita. Hoje o número é
# deliberado e a poda é explícita no log, com o agregado como pré-condição.
RETENCAO_BRUTO_DIAS = 5


def _ponderada(pares: list[tuple[float, float]]) -> float | None:
    """Média ponderada de (valor, peso), ignorando peso nulo/zero.

    Cai para média SIMPLES quando nenhum negócio tem volume informado --
    é melhor que devolver `None` e perder o dia inteiro. Acontece pouco,
    mas acontece.
    """
    validos = [(v, p) for v, p in pares if v is not None]
    if not validos:
        return None
    com_peso = [(v, p) for v, p in validos if p]
    if not com_peso:
        return sum(v for v, _ in validos) / len(validos)
    total = sum(p for _, p in com_peso)
    return sum(v * p for v, p in com_peso) / total


def agregar_linhas(negocios: list[dict]) -> list[dict]:
    """Agrega uma lista de negócios (dicts da captura) por Código+Data."""
    grupos: dict[tuple[str, date], list[dict]] = {}
    for n in negocios:
        codigo = n.get("codigo")
        dt = n.get("data_negocio")
        if not codigo or dt is None:
            continue
        if n.get("instrument_type") not in INSTRUMENTOS:
            continue
        grupos.setdefault((codigo, dt), []).append(n)

    saida = []
    for (codigo, dt), itens in grupos.items():
        volumes = [i.get("volume") or 0 for i in itens]
        saida.append({
            "codigo": codigo,
            "data": dt,
            "instrument_type": itens[0].get("instrument_type"),
            "n_negocios": len(itens),
            "volume": sum(volumes) or None,
            "quantidade": sum(i.get("quantidade") or 0 for i in itens) or None,
            "taxa_media": _ponderada([(i.get("taxa"), i.get("volume") or 0) for i in itens]),
            "taxa_min": min([i["taxa"] for i in itens if i.get("taxa") is not None], default=None),
            "taxa_max": max([i["taxa"] for i in itens if i.get("taxa") is not None], default=None),
            "spread_medio": _ponderada([(i.get("spread"), i.get("volume") or 0) for i in itens]),
            "spread_min": min([i["spread"] for i in itens if i.get("spread") is not None], default=None),
            "spread_max": max([i["spread"] for i in itens if i.get("spread") is not None], default=None),
            "preco_medio": _ponderada([(i.get("preco"), i.get("volume") or 0) for i in itens]),
            "maior_negocio": max(volumes, default=None) or None,
        })
    return saida


def gravar_agregado(db: Session, linhas: list[dict]) -> int:
    """Upsert do agregado. Idempotente por Código+Data.

    Grava em lote via SQL, não pelo ORM: um backfill de 500 dias escreve
    centenas de milhares de linhas, e o ORM aqui só custaria tempo (mesma
    lição da importação do banco do Allan, 05/08/2026).

    PORTABILIDADE (corrigido em 20/08/2026, junto com `agregar_do_banco`):
    era `INSERT OR REPLACE` com `?` num cursor da DBAPI -- SQLite only.
    Agora é `ON CONFLICT ... DO UPDATE` com parâmetro nomeado via
    `text()`, que funciona nos dois bancos.
    """
    if not linhas:
        return 0

    campos = ("codigo", "data", "instrument_type", "n_negocios", "volume",
              "quantidade", "taxa_media", "taxa_min", "taxa_max",
              "spread_medio", "spread_min", "spread_max",
              "preco_medio", "maior_negocio")
    atualiza = ", ".join(f"{c} = EXCLUDED.{c}" for c in campos
                         if c not in ("codigo", "data"))

    db.execute(
        text(
            f"INSERT INTO negocios_b3_diario ({', '.join(campos)})"
            f" VALUES ({', '.join(':' + c for c in campos)})"
            f" ON CONFLICT (codigo, data) DO UPDATE SET {atualiza}"
        ),
        [{c: l[c] for c in campos} for l in linhas],
    )
    db.commit()
    return len(linhas)


def agregar_do_banco(db: Session, inicio: date | None = None, fim: date | None = None) -> dict:
    """Reconstrói o agregado a partir de `negocios_b3` já gravado.

    Faz a agregação em SQL, não em Python: são dezenas de milhares de
    linhas por dia, e trazê-las para a aplicação só para somar seria
    desperdício.

    PORTABILIDADE (corrigido em 20/08/2026 -- quebrava em produção):
    esta função era SQL de SQLite cru, com `INSERT OR REPLACE`, placeholder
    `?` e cursor da DBAPI direto (`db.connection().connection`). Rodava
    local (SQLite) e falhava no Supabase (Postgres) com

        the query has 0 placeholders but 3 parameters were passed

    -- o psycopg usa `%s`, não `?`, então não via placeholder nenhum. Os
    testes não pegaram porque também rodam em SQLite. Agora usa `text()`
    com parâmetro NOMEADO (`:tipo0`), que o SQLAlchemy traduz para o
    dialeto certo, e `ON CONFLICT ... DO UPDATE`, que existe nos dois
    (SQLite >= 3.24 e Postgres) e substitui o `INSERT OR REPLACE`.
    """
    tipos = sorted(INSTRUMENTOS)
    params: dict = {f"tipo{i}": t for i, t in enumerate(tipos)}
    where = ["instrument_type IN (" + ",".join(f":tipo{i}" for i in range(len(tipos))) + ")"]
    if inicio:
        where.append("data_negocio >= :inicio")
        params["inicio"] = inicio
    if fim:
        where.append("data_negocio <= :fim")
        params["fim"] = fim
    filtro = "WHERE " + " AND ".join(where)

    # Colunas atualizadas quando a linha (codigo, data) já existe. A chave
    # do conflito é `uq_negocio_b3_diario`.
    atualiza = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in (
            "instrument_type", "n_negocios", "volume", "quantidade",
            "taxa_media", "taxa_min", "taxa_max",
            "spread_medio", "spread_min", "spread_max",
            "preco_medio", "maior_negocio",
        )
    )

    db.execute(text(f"""
        INSERT INTO negocios_b3_diario
            (codigo, data, instrument_type, n_negocios, volume, quantidade,
             taxa_media, taxa_min, taxa_max, spread_medio, spread_min, spread_max,
             preco_medio, maior_negocio)
        SELECT
            codigo,
            data_negocio,
            MIN(instrument_type),
            COUNT(*),
            SUM(volume),
            SUM(quantidade),
            -- Ponderada por volume. O COALESCE no denominador evita
            -- divisão por zero num dia em que nenhum negócio tem volume;
            -- nesse caso o NULLIF devolve NULL e a coluna fica vazia, que
            -- é honesto -- melhor que uma média simples disfarçada de
            -- ponderada.
            SUM(taxa * COALESCE(volume, 0)) / NULLIF(SUM(CASE WHEN taxa IS NOT NULL THEN COALESCE(volume, 0) END), 0),
            MIN(taxa), MAX(taxa),
            SUM(spread * COALESCE(volume, 0)) / NULLIF(SUM(CASE WHEN spread IS NOT NULL THEN COALESCE(volume, 0) END), 0),
            MIN(spread), MAX(spread),
            SUM(preco * COALESCE(volume, 0)) / NULLIF(SUM(COALESCE(volume, 0)), 0),
            MAX(volume)
        FROM negocios_b3
        {filtro}
        GROUP BY codigo, data_negocio
        ON CONFLICT (codigo, data) DO UPDATE SET {atualiza}
    """), params)
    db.commit()
    total = db.scalar(select(func.count()).select_from(NegocioB3Diario))
    dias = db.scalar(select(func.count(func.distinct(NegocioB3Diario.data))))
    return {"linhas_agregado": total, "dias": dias}


def arquivar_bruto(db: Session, destino: Path, ate: date) -> dict:
    """Exporta o negócio a negócio bruto até `ate` para CSV comprimido.

    Existe para que a poda nunca signifique perda: o `.csv.gz` guarda a
    operação individual (~30 MB por ano de dados, contra ~740 MB na
    tabela), e pode ser reimportado se algum dia a análise exigir o dado
    granular antigo.

    Devolve sem escrever nada se não houver o que arquivar.
    """
    linhas = db.execute(
        select(NegocioB3).where(NegocioB3.data_negocio <= ate).order_by(NegocioB3.data_negocio)
    ).scalars().all()
    if not linhas:
        return {"arquivadas": 0, "arquivo": None}

    destino.parent.mkdir(parents=True, exist_ok=True)
    campos = ["trade_code", "data_negocio", "instrument_type", "emissor", "codigo", "isin",
              "quantidade", "preco", "volume", "taxa", "spread", "origem", "horario",
              "data_liquidacao", "situacao"]
    with gzip.open(destino, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(campos)
        for n in linhas:
            w.writerow([getattr(n, c, None) for c in campos])
    return {"arquivadas": len(linhas), "arquivo": str(destino),
            "tamanho_mb": round(destino.stat().st_size / 1e6, 2)}


def podar_bruto(db: Session, dias: int | None = RETENCAO_BRUTO_DIAS,
                hoje: date | None = None) -> dict:
    """Apaga negócio a negócio BRUTO mais antigo que `dias`.

    O padrão é `RETENCAO_BRUTO_DIAS` (5), então **a chamada sem argumento
    APAGA**. Passe `dias=None` explicitamente para desligar a poda.

    (Esta docstring dizia o contrário até 20/08/2026 — "não roda sozinha",
    "retenção infinita" — resquício de uma versão anterior em que o padrão
    era `None`. A contradição com o código logo abaixo atrapalhou o
    diagnóstico do estouro de Disk IO do Supabase, porque dava a entender
    que a poda estava desligada de propósito, quando na verdade o problema
    era outro: `rodada_noturna.py`, que é quem chama esta função, não
    tinha workflow e nunca rodava na nuvem.)

    A salvaguarda continua: só apaga depois de conferir que cada dia a
    apagar tem agregado gravado — apagar o bruto sem o agregado perderia
    o dado de vez.

    Antes de usar, considere `arquivar_bruto()` — o CSV comprimido guarda
    a operação individual em ~4% do espaço da tabela.
    """
    if dias is None:
        return {"apagados": 0, "corte": None,
                "motivo": "retenção infinita (padrão) — nada foi apagado"}

    hoje = hoje or date.today()
    corte = hoje - timedelta(days=dias)

    mais_antigo_bruto = db.scalar(select(func.min(NegocioB3.data_negocio)))
    if mais_antigo_bruto is None:
        return {"apagados": 0, "corte": corte.isoformat(), "motivo": "sem dado bruto"}

    # A salvaguarda compara o agregado com as DATAS QUE SERÃO APAGADAS,
    # não com a data de corte.
    #
    # A primeira versão exigia `max(agregado) >= corte`, o que é restritivo
    # demais: num banco cujo bruto e agregado terminam antes do corte (só
    # dado antigo), a poda se recusava a rodar mesmo com todo o período
    # devidamente agregado. O que importa é que cada dia a apagar tenha
    # agregado -- é isso que garante que nada se perde.
    dias_a_apagar = {
        d for (d,) in db.execute(
            select(NegocioB3.data_negocio).where(NegocioB3.data_negocio < corte).distinct()
        )
    }
    if not dias_a_apagar:
        return {"apagados": 0, "corte": corte.isoformat(),
                "motivo": "nada mais antigo que o corte"}

    dias_agregados = {
        d for (d,) in db.execute(
            select(NegocioB3Diario.data).where(NegocioB3Diario.data < corte).distinct()
        )
    }
    sem_agregado = dias_a_apagar - dias_agregados
    if sem_agregado:
        return {"apagados": 0, "corte": corte.isoformat(),
                "motivo": f"{len(sem_agregado)} dia(s) sem agregado — nada apagado",
                "dias_sem_agregado": sorted(str(d) for d in sem_agregado)[:10]}

    n = db.scalar(
        select(func.count()).select_from(NegocioB3).where(NegocioB3.data_negocio < corte)
    )
    db.execute(delete(NegocioB3).where(NegocioB3.data_negocio < corte))
    db.commit()
    return {"apagados": n, "corte": corte.isoformat(), "motivo": "ok"}
