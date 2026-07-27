"""Consultas agregadas sobre o histórico de spreads — usado pelas rotas da
API do dashboard (app/spreads_routes.py). Tudo filtrado por `classe`
("IPCA + Incentivadas" | "CDI + Tradicionais") — ver `compute_classe` em
app/spreads/fetch.py pra entender por que essas duas bases nunca se
misturam numa mesma consulta/gráfico (pedido explícito do Allan,
23/07/2026: não são comparáveis entre si).

"N dias de comparação" em todas as funções aqui significa N POSIÇÕES na
lista de datas com dado disponível (não N dias corridos) — assim feriados
e dias sem publicação não distorcem a janela "última semana"."""
from __future__ import annotations

import itertools
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import AppSetting, Article, Company, Debenture, DebentureSpread, NegocioB3
from .fetch import _normalize_codigo

CLASSES = ["IPCA + Incentivadas", "CDI + Tradicionais"]

# Chave em AppSetting (config chave-valor genérica, ver app/auth.py) pra
# lista de tickers excluídos manualmente da conta de spread -- pedido do
# Allan, 27/07/2026: aba Administração, campo de texto separado por ";".
# Ver `tickers_excluidos_spread` pra leitura e app/app.py pro formulário
# que grava.
TICKERS_EXCLUIDOS_SETTING_KEY = "spread_tickers_excluidos"


def tickers_excluidos_spread(db: Session) -> set[str]:
    """Tickers excluídos manualmente da conta de spread (pedido do Allan,
    27/07/2026) -- ex.: papel com dado errático/ruim conhecido que ele
    prefere tirar da base à mão em vez de esperar a Anbima corrigir.
    Normalizado com a MESMA função que cruza código de ativo em
    `fetch.py` (`_normalize_codigo`) pra não depender de o Allan digitar
    o código com exatamente a mesma pontuação/maiúscula do cadastro."""
    row = db.get(AppSetting, TICKERS_EXCLUIDOS_SETTING_KEY)
    if not row or not row.value:
        return set()
    return {_normalize_codigo(t) for t in row.value.split(";") if t.strip()}

# Fuso de Brasília, fixo (Brasil não tem mais horário de verão desde
# 2019) -- usado só pra achar a janela "últimas 24h" do spread da B3
# (`emissor_taxas`); `data_negocio`/`horario` são gravados nesse fuso
# (é o fuso da própria B3), não em UTC.
_BRT = ZoneInfo("America/Sao_Paulo")

# Bases de comparação oferecidas no dashboard (pedido do Allan, 24/07/2026)
# -- valores em POSIÇÕES no histórico de dias com dado (não dias corridos),
# aproximando convenção de mercado de 252 dias úteis/ano (mesma lógica já
# usada em Debenture.duration, dividido por 252). d-1 = pregão anterior;
# WoW/MoM/QoQ/SoS/YoY = 1 semana/mês/trimestre/semestre/ano úteis atrás.
COMPARACAO_BASES = {
    "d-1": 1,
    "WoW": 5,
    "MoM": 21,
    "QoQ": 63,
    "SoS": 126,
    "YoY": 252,
}


def distinct_dates(db: Session, classe: str) -> list[date]:
    """Datas com dado pra essa classe, mais recente primeiro."""
    rows = (
        db.query(DebentureSpread.data)
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .filter(Debenture.classe == classe)
        .distinct()
        .order_by(DebentureSpread.data.desc())
        .all()
    )
    return [r[0] for r in rows]


def latest_date(db: Session, classe: str) -> date | None:
    dates = distinct_dates(db, classe)
    return dates[0] if dates else None


def _date_n_back(dates_desc: list[date], n: int) -> date | None:
    return dates_desc[n] if len(dates_desc) > n else None


def _resolve_hoje(dates_desc: list[date], data_referencia: date | None) -> date | None:
    """Resolve qual data usar como "hoje" nas contas da Visão Geral.

    Sem `data_referencia` (padrão): a mais recente disponível -- mesmo
    comportamento de sempre. Com `data_referencia` (pedido do Allan,
    27/07/2026: campo de data na Visão Geral pra escolher a data
    analisada, em vez de sempre olhar a última publicada): pega a data
    disponível mais próxima, pra trás, da selecionada -- ex. se o Allan
    escolher um sábado ou feriado (sem boletim), cai no último dia útil
    anterior com dado, em vez de devolver vazio. `dates_desc` já vem do
    mais recente pro mais antigo, então o primeiro item <= a data
    selecionada já é exatamente esse "mais próximo pra trás"."""
    if data_referencia is None:
        return dates_desc[0] if dates_desc else None
    candidatos = [d for d in dates_desc if d <= data_referencia]
    return candidatos[0] if candidatos else None


def _index_from(dates_desc: list[date], hoje: date, n: int) -> date | None:
    """Acha a data `n` posições atrás de `hoje` em `dates_desc`. Antes
    disso ganhar suporte a `data_referencia`, "hoje" era sempre
    `dates_desc[0]` e `_date_n_back(dates_desc, n)` bastava; agora "hoje"
    pode ser qualquer data da lista, então primeiro achamos o índice dele
    pra andar `n` posições a partir dali (mesma convenção de "N posições
    no histórico com dado", não N dias corridos -- ver topo do arquivo)."""
    try:
        idx = dates_desc.index(hoje)
    except ValueError:
        return None
    return _date_n_back(dates_desc, idx + n)


def kpi_summary(
    db: Session, classe: str, dias_comparacao: int = 5, data_referencia: date | None = None,
) -> dict:
    dates_desc = distinct_dates(db, classe)
    hoje = _resolve_hoje(dates_desc, data_referencia)
    if hoje is None:
        return {
            "data_referencia": None, "data_comparacao": None, "spread_medio": None,
            "spread_medio_fallback": False,
            "variacao_bps": None, "n_ativos": 0, "duration_media_ponderada": None,
            "duration_ponderada_fallback": False,
        }
    anterior = _index_from(dates_desc, hoje, dias_comparacao)
    excluidos = tickers_excluidos_spread(db)

    q_n = (
        db.query(func.count(DebentureSpread.id))
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .filter(Debenture.classe == classe, DebentureSpread.data == hoje, DebentureSpread.spread.isnot(None))
    )
    if excluidos:
        q_n = q_n.filter(DebentureSpread.codigo.notin_(excluidos))
    n_hoje = q_n.scalar()

    # CORRIGIDO (27/07/2026): Allan reparou que o card "SPREAD MÉDIO" da
    # Visão Geral dava um número diferente do que ele calcula manualmente
    # -- confirmou que o card estava usando `AVG(spread)` (média simples,
    # cada ticker com o mesmo peso), enquanto o resto do dashboard inteiro
    # (`emissor_taxas`, `emissor_series`, `_weighted_avg_duration`) sempre
    # usou média PONDERADA por Estoque (spread de papel com estoque maior
    # pesa mais, igual a metodologia do relatório semanal). Esse card era
    # o único lugar que ainda fazia média simples -- agora usa
    # `_weighted_avg_spread`, a mesma lógica/fallback de
    # `_weighted_avg_duration` (cai pra média simples só se NENHUM ticker
    # da data tiver Estoque cruzado).
    media_hoje, spread_fallback = _weighted_avg_spread(db, classe, hoje, excluidos)

    variacao = None
    if anterior is not None and media_hoje is not None:
        media_anterior, _ = _weighted_avg_spread(db, classe, anterior, excluidos)
        if media_anterior is not None:
            variacao = media_hoje - media_anterior

    duration_pond, fallback = _weighted_avg_duration(db, classe, hoje, excluidos)

    return {
        "data_referencia": hoje.isoformat(),
        "data_comparacao": anterior.isoformat() if anterior else None,
        "spread_medio": round(media_hoje, 1) if media_hoje is not None else None,
        # True quando nenhuma linha da data tinha Estoque cruzado (cobertura
        # de debentures.com.br pode ser esparsa em datas mais antigas -- ver
        # nota em app/spreads/fetch.py) -- cai pra média simples sem peso, e
        # o dashboard mostra isso pro Allan não achar que é ponderada de
        # verdade quando não é (mesmo padrão de `duration_ponderada_fallback`).
        "spread_medio_fallback": spread_fallback,
        "variacao_bps": round(variacao, 1) if variacao is not None else None,
        "n_ativos": n_hoje or 0,
        "duration_media_ponderada": round(duration_pond, 2) if duration_pond is not None else None,
        "duration_ponderada_fallback": fallback,
    }


def _weighted_avg_spread(
    db: Session, classe: str, data: date, excluidos: set[str] | None = None,
) -> tuple[float | None, bool]:
    """Spread médio ponderado por Estoque na data -- MESMA metodologia
    usada em `emissor_taxas`/`emissor_series`/`_weighted_avg_duration`
    (nunca média simples: papel com Estoque maior pesa mais). Sem nenhum
    Estoque disponível na classe/data, cai pra média simples (sem peso)
    -- devolve (valor, fallback_usado)."""
    q = (
        db.query(DebentureSpread.spread, DebentureSpread.estoque)
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .filter(Debenture.classe == classe, DebentureSpread.data == data, DebentureSpread.spread.isnot(None))
    )
    if excluidos:
        q = q.filter(DebentureSpread.codigo.notin_(excluidos))
    rows = q.all()
    if not rows:
        return None, False
    com_estoque = [(s, e) for s, e in rows if e is not None and e > 0]
    if com_estoque:
        soma_peso = sum(e for _, e in com_estoque)
        soma_pond = sum(s * e for s, e in com_estoque)
        return (soma_pond / soma_peso if soma_peso else None), False
    valores = [s for s, _ in rows]
    return (sum(valores) / len(valores) if valores else None), True


def _weighted_avg_duration(
    db: Session, classe: str, data: date, excluidos: set[str] | None = None,
) -> tuple[float | None, bool]:
    """Duration média ponderada por Estoque na data. Sem nenhum Estoque
    disponível na classe/data, cai pra média simples (sem peso) — devolve
    (valor, fallback_usado). Exige `spread` calculado (não só `duration`)
    -- papel sem taxa publicada pela Anbima nesse dia não deve entrar em
    nenhuma conta da base (pedido do Allan, 27/07/2026), mesmo se por
    algum motivo tivesse `duration` sem `spread`."""
    q = (
        db.query(DebentureSpread.duration, DebentureSpread.estoque)
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .filter(
            Debenture.classe == classe, DebentureSpread.data == data,
            DebentureSpread.duration.isnot(None), DebentureSpread.spread.isnot(None),
        )
    )
    if excluidos:
        q = q.filter(DebentureSpread.codigo.notin_(excluidos))
    rows = q.all()
    if not rows:
        return None, False
    com_estoque = [(d, e) for d, e in rows if e is not None and e > 0]
    if com_estoque:
        soma_peso = sum(e for _, e in com_estoque)
        soma_pond = sum(d * e for d, e in com_estoque)
        return (soma_pond / soma_peso if soma_peso else None), False
    durations = [d for d, _ in rows]
    return (sum(durations) / len(durations) if durations else None), True


def time_series(db: Session, classe: str, codigo: str | None = None) -> list[dict]:
    """Série diária. Sem `codigo`: spread médio da classe inteira por dia
    (linha "Total" do relatório do Allan). Com `codigo`: a série do próprio
    papel (spread + taxa indicativa), pro drill-down de um ativo específico."""
    if codigo:
        rows = (
            db.query(DebentureSpread.data, DebentureSpread.spread, DebentureSpread.taxa_indicativa)
            .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
            .filter(Debenture.classe == classe, DebentureSpread.codigo == codigo)
            .order_by(DebentureSpread.data)
            .all()
        )
        return [{"data": r[0].isoformat(), "spread": r[1], "taxa_indicativa": r[2]} for r in rows]

    # CORRIGIDO (27/07/2026): igual ao bug do card "SPREAD MÉDIO" (ver
    # `_weighted_avg_spread`), esta série usava `AVG(spread)` -- média
    # simples, cada ticker com peso igual -- só que aqui pra CADA DIA do
    # histórico, não só o mais recente. Ou seja, o gráfico "Evolução do
    # Spread Médio" nunca batia com o card, mesmo depois da correção do
    # card (27/07/2026, ver seção "Bug real: card SPREAD MÉDIO..."):
    # Allan pegou isso comparando os dois. Trocado pra ponderar por
    # Estoque em CADA data (mesmo fallback pra média simples quando
    # nenhum ticker daquele dia tem Estoque cruzado), agrupado em Python
    # (`itertools.groupby` sobre as linhas cruas ordenadas por data) já
    # que o SQL não dá pra fazer "SUM(spread*estoque)/SUM(estoque) com
    # fallback pra AVG(spread) quando soma de estoque é zero" numa única
    # expressão limpa por GROUP BY. (Chegou a ganhar uma linha de
    # mediana no mesmo dia, pedido do próprio Allan -- revertida no dia
    # seguinte, "não gostei", ver CLAUDE.md.)
    excluidos = tickers_excluidos_spread(db)
    q = (
        db.query(DebentureSpread.data, DebentureSpread.spread, DebentureSpread.estoque)
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .filter(Debenture.classe == classe, DebentureSpread.spread.isnot(None))
    )
    if excluidos:
        q = q.filter(DebentureSpread.codigo.notin_(excluidos))
    rows = q.order_by(DebentureSpread.data).all()

    out = []
    for dia, grupo in itertools.groupby(rows, key=lambda r: r[0]):
        spreads = []
        com_estoque = []
        for _, spread, estoque in grupo:
            spreads.append(spread)
            if estoque is not None and estoque > 0:
                com_estoque.append((spread, estoque))
        if com_estoque:
            soma_peso = sum(e for _, e in com_estoque)
            media = (sum(s * e for s, e in com_estoque) / soma_peso) if soma_peso else None
        else:
            media = sum(spreads) / len(spreads) if spreads else None
        out.append({
            "data": dia.isoformat(),
            "spread_medio": round(media, 1) if media is not None else None,
            "n_ativos": len(spreads),
        })
    return out


def movers(
    db: Session, classe: str, dias_comparacao: int = 5, top_n: int = 10, data_referencia: date | None = None,
) -> dict:
    """Maiores aberturas/fechamentos de spread no período + o dataset
    completo (spread x duration) pro scatter de variação — réplica das
    tabelas/gráfico 'Maiores Variações no Período' do relatório semanal."""
    dates_desc = distinct_dates(db, classe)
    if len(dates_desc) < 2:
        return {"data_referencia": None, "data_comparacao": None, "aberturas": [], "fechamentos": [], "scatter": []}
    hoje = _resolve_hoje(dates_desc, data_referencia)
    if hoje is None:
        return {"data_referencia": None, "data_comparacao": None, "aberturas": [], "fechamentos": [], "scatter": []}
    anterior = _index_from(dates_desc, hoje, dias_comparacao)
    if anterior is None and dates_desc[-1] != hoje:
        # Sem histórico suficiente pra completar a base de comparação
        # inteira (ex. só WoW pedido mas só temos 3 dias) -- cai pra
        # comparar contra o dado mais antigo disponível, mesma lógica de
        # antes de `data_referencia` existir. BUG CORRIGIDO (27/07/2026):
        # com `data_referencia`, "hoje" deixou de ser sempre
        # `dates_desc[0]` e passou a poder SER `dates_desc[-1]` (o Allan
        # escolhe justamente a data mais antiga do histórico) -- sem esse
        # `!= hoje`, o fallback comparava a data contra ela mesma
        # (variação sempre 0 bps pra todo mundo, silenciosamente errado
        # em vez de "sem dado suficiente").
        anterior = dates_desc[-1]
    if anterior is None:
        return {"data_referencia": hoje.isoformat(), "data_comparacao": None, "aberturas": [], "fechamentos": [], "scatter": []}

    excluidos = tickers_excluidos_spread(db)

    q_hoje = (
        db.query(DebentureSpread)
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .filter(Debenture.classe == classe, DebentureSpread.data == hoje, DebentureSpread.spread.isnot(None))
    )
    q_ant = (
        db.query(DebentureSpread.codigo, DebentureSpread.spread)
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .filter(Debenture.classe == classe, DebentureSpread.data == anterior, DebentureSpread.spread.isnot(None))
    )
    if excluidos:
        q_hoje = q_hoje.filter(DebentureSpread.codigo.notin_(excluidos))
        q_ant = q_ant.filter(DebentureSpread.codigo.notin_(excluidos))
    rows_hoje = {r.codigo: r for r in q_hoje.all()}
    rows_ant = dict(q_ant.all())
    nomes = {
        d.codigo: d.nome for d in db.query(Debenture).filter(Debenture.codigo.in_(rows_hoje.keys())).all()
    }

    variacoes = []
    for codigo, row in rows_hoje.items():
        spread_ant = rows_ant.get(codigo)
        if row.spread is None or spread_ant is None:
            continue
        delta = row.spread - spread_ant
        variacoes.append({
            "codigo": codigo,
            "nome": nomes.get(codigo),
            "spread": round(row.spread, 1),
            "variacao_bps": round(delta, 1),
            "duration": round(row.duration, 2) if row.duration is not None else None,
        })

    variacoes_com_duration = [v for v in variacoes if v["duration"] is not None]
    aberturas = sorted(variacoes, key=lambda v: v["variacao_bps"], reverse=True)[:top_n]
    fechamentos = sorted(variacoes, key=lambda v: v["variacao_bps"])[:top_n]

    return {
        "data_referencia": hoje.isoformat(),
        "data_comparacao": anterior.isoformat(),
        "aberturas": aberturas,
        "fechamentos": fechamentos,
        "scatter": variacoes_com_duration,
    }


def movement_distribution(
    db: Session, classe: str, dias_comparacao: int = 5, n_snapshots: int = 5, data_referencia: date | None = None,
) -> list[dict]:
    """Composição da base de ativos por faixa de abertura/fechamento de
    spread — pedido do Allan (24/07/2026): o STEP entre snapshots é a
    própria base de comparação selecionada no dashboard (d-1/WoW/MoM/QoQ/
    SoS/YoY, ver COMPARACAO_BASES), não uma janela fixa. Ou seja: se a base
    é d-1, mostra os últimos `n_snapshots` DIAS (cada um comparado com o
    dia útil anterior); se é MoM, mostra os últimos `n_snapshots` MESES
    (cada um comparado com ~21 du atrás); etc. Cada snapshot i usa
    `dates_desc[i*dias_comparacao]` como referência, comparada contra
    `dates_desc[(i+1)*dias_comparacao]` — é sempre "essa data vs. exatamente
    uma base de comparação atrás", nunca comparação com hoje.

    Limiar de bucket: 10 bps pra IPCA+ Incentivadas, 5 bps pra CDI+
    Tradicionais (mesma diferença usada no relatório semanal -- bases com
    volatilidade histórica diferente).

    Devolve do snapshot mais ANTIGO pro mais recente (ordem de eixo X de
    gráfico) — com menos de `n_snapshots` períodos de histórico disponível,
    devolve só os que dá, sem erro."""
    dates_desc = distinct_dates(db, classe)
    limiar = 10 if classe == "IPCA + Incentivadas" else 5
    labels = [f"< -{limiar} bps", f"-{limiar} a 0 bps", f"0 a {limiar} bps", f"> {limiar} bps"]
    excluidos = tickers_excluidos_spread(db)

    # Âncora dos snapshots: por padrão a data mais recente (idx 0); com
    # `data_referencia` (campo de data da Visão Geral, 27/07/2026) os
    # snapshots recuam a partir da data escolhida em vez de sempre da
    # última publicada -- mesma resolução "mais próxima pra trás" de
    # `_resolve_hoje`. BUG CORRIGIDO (27/07/2026): se `data_referencia`
    # for anterior a TODO o histórico, `_resolve_hoje` devolve `None` --
    # antes disso caía silenciosamente pro índice 0 (data mais recente),
    # ou seja, o gráfico de distribuição mostrava "hoje" enquanto o
    # KPI/Movers (que já tratavam esse caso corretamente) mostravam vazio
    # -- inconsistente. Agora devolve lista vazia também, igual aos
    # outros dois.
    hoje = _resolve_hoje(dates_desc, data_referencia)
    if data_referencia is not None and hoje is None:
        return []
    idx_hoje = dates_desc.index(hoje) if hoje is not None else 0

    out = []
    for i in range(n_snapshots):
        idx = idx_hoje + i * dias_comparacao
        if idx >= len(dates_desc):
            break
        d = dates_desc[idx]
        anterior = _date_n_back(dates_desc, idx + dias_comparacao)
        if anterior is None:
            break  # sem histórico suficiente pra mais snapshots nessa base
        # CORRIGIDO (27/07/2026): "composição da base" agora pondera por
        # Estoque (% do ESTOQUE em cada faixa), não mais % da CONTAGEM de
        # tickers -- mesma convenção usada em todo o resto do dashboard
        # (spread médio, duration, agora também a série histórica, ver
        # `_weighted_avg_spread`/`time_series`). Um papel gigante que
        # abriu 50bps pesa mais na composição da base do que um papel
        # pequeno que fechou 5bps -- contar "1 ticker" pra cada teria o
        # mesmo problema de média simples que o Allan já tinha pegado no
        # card. `estoque` vem sempre do dia "hoje" do snapshot (mesma
        # convenção de `_weighted_avg_duration`/`_weighted_avg_spread`).
        q_hoje = (
            db.query(DebentureSpread.codigo, DebentureSpread.spread, DebentureSpread.estoque)
            .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
            .filter(Debenture.classe == classe, DebentureSpread.data == d, DebentureSpread.spread.isnot(None))
        )
        q_ant = (
            db.query(DebentureSpread.codigo, DebentureSpread.spread)
            .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
            .filter(Debenture.classe == classe, DebentureSpread.data == anterior, DebentureSpread.spread.isnot(None))
        )
        if excluidos:
            q_hoje = q_hoje.filter(DebentureSpread.codigo.notin_(excluidos))
            q_ant = q_ant.filter(DebentureSpread.codigo.notin_(excluidos))
        spreads_hoje = {codigo: (spread, estoque) for codigo, spread, estoque in q_hoje.all()}
        spreads_ant = dict(q_ant.all())

        deltas_com_peso = []  # (delta, estoque) só pra quem tem Estoque > 0
        deltas_todos = []  # (delta,) pra todo mundo com comparação válida -- fallback e n_ativos
        for codigo, (spread, estoque) in spreads_hoje.items():
            ant = spreads_ant.get(codigo)
            if ant is None:
                continue
            delta = spread - ant
            deltas_todos.append(delta)
            if estoque is not None and estoque > 0:
                deltas_com_peso.append((delta, estoque))

        buckets = {label: 0.0 for label in labels}
        if deltas_com_peso:
            usar = deltas_com_peso  # ponderado -- exclui quem não tem Estoque, igual ao resto do dashboard
        else:
            usar = [(delta, 1.0) for delta in deltas_todos]  # fallback: contagem simples (peso 1 cada)
        total_peso = sum(peso for _, peso in usar)
        for delta, peso in usar:
            if delta < -limiar:
                buckets[labels[0]] += peso
            elif delta < 0:
                buckets[labels[1]] += peso
            elif delta <= limiar:
                buckets[labels[2]] += peso
            else:
                buckets[labels[3]] += peso
        pct = {k: (round(v / total_peso * 100, 1) if total_peso else 0) for k, v in buckets.items()}
        out.append({
            "data": d.isoformat(), "data_comparacao": anterior.isoformat(),
            "n_ativos": len(deltas_todos), **pct,
        })
    return list(reversed(out))


def search_debentures(db: Session, q: str, classe: str | None = None, limit: int = 20) -> list[dict]:
    query = db.query(Debenture)
    if classe:
        query = query.filter(Debenture.classe == classe)
    if q:
        like = f"%{q}%"
        query = query.filter((Debenture.codigo.ilike(like)) | (Debenture.nome.ilike(like)))
    rows = query.order_by(Debenture.codigo).limit(limit).all()
    return [{"codigo": d.codigo, "nome": d.nome, "classe": d.classe} for d in rows]


def list_emissores(db: Session) -> list[str]:
    """Nomes de emissor distintos (`Debenture.nome`) -- opções do filtro de
    emissor na aba 'Marcação Emissores' (pedido do Allan, 24/07/2026)."""
    rows = (
        db.query(Debenture.nome)
        .filter(Debenture.nome.isnot(None), Debenture.nome != "")
        .distinct()
        .order_by(Debenture.nome)
        .all()
    )
    return [r[0] for r in rows]


def emissor_tickers(db: Session, nomes_emissor: list[str]) -> list[dict]:
    """Tabela dos tickers de um ou mais emissores -- código, indexador,
    classe, incentivada, Estoque mais recente de cada um (pode ser de
    datas diferentes entre tickers se algum ficou sem publicação num
    pregão específico). Ampliado (24/07/2026) pra aceitar VÁRIOS emissores
    de uma vez -- pedido do Allan pra seleção múltipla na aba de
    emissores; devolve o campo `emissor` em cada linha pra distinguir de
    qual empresa é cada ticker quando mais de uma está selecionada."""
    debs = (
        db.query(Debenture)
        .filter(Debenture.nome.in_(nomes_emissor))
        .order_by(Debenture.nome, Debenture.codigo)
        .all()
    )
    out = []
    for d in debs:
        ultimo = (
            db.query(DebentureSpread)
            .filter(DebentureSpread.codigo == d.codigo)
            .order_by(DebentureSpread.data.desc())
            .first()
        )
        out.append({
            "codigo": d.codigo,
            "emissor": d.nome,
            "indexador": d.indexador,
            "classe": d.classe,
            "incentivada": d.incentivada,
            "estoque": round(ultimo.estoque, 1) if ultimo and ultimo.estoque is not None else None,
            "data_estoque": ultimo.data.isoformat() if ultimo else None,
        })
    return out


def emissor_series(db: Session, nomes_emissor: list[str], classe: str, nivel: str = "emissor") -> dict:
    """Série de spread ao longo do tempo dos emissores filtrados, só
    tickers da `classe` selecionada (nunca mistura IPCA+Incentivadas com
    CDI+Tradicionais, mesma regra do resto do dashboard). Pedido do Allan
    (24/07/2026, ampliado pra seleção múltipla no mesmo dia):
    - `nivel="ticker"`: uma linha por ticker (de qualquer um dos emissores
      selecionados).
    - `nivel="emissor"`: uma linha POR EMISSOR selecionado, cada uma sendo
      o spread médio ponderado por Estoque entre os tickers daquele
      emissor naquela classe, dia a dia (fallback pra média simples se
      nenhum ticker daquele dia tiver Estoque cruzado)."""
    pares_nome_codigo = (
        db.query(Debenture.nome, Debenture.codigo)
        .filter(Debenture.nome.in_(nomes_emissor), Debenture.classe == classe)
        .all()
    )
    if not pares_nome_codigo:
        return {"nivel": nivel, "series": []}
    excluidos = tickers_excluidos_spread(db)
    codigo_to_nome = {codigo: nome for nome, codigo in pares_nome_codigo if codigo not in excluidos}
    codigos = list(codigo_to_nome.keys())
    if not codigos:
        return {"nivel": nivel, "series": []}

    rows = (
        db.query(DebentureSpread.codigo, DebentureSpread.data, DebentureSpread.spread, DebentureSpread.estoque)
        .filter(DebentureSpread.codigo.in_(codigos), DebentureSpread.spread.isnot(None))
        .order_by(DebentureSpread.data)
        .all()
    )

    if nivel == "ticker":
        por_codigo: dict[str, list[dict]] = {}
        for codigo, d, spread, _estoque in rows:
            por_codigo.setdefault(codigo, []).append({"data": d.isoformat(), "spread": round(spread, 1)})
        series = [{"codigo": c, "pontos": pontos} for c, pontos in por_codigo.items() if pontos]
        return {"nivel": "ticker", "series": series}

    # nivel == "emissor": agrega por (emissor, data), ponderado por Estoque
    por_emissor_data: dict[tuple[str, date], list[tuple[float, float | None]]] = {}
    for codigo, d, spread, estoque in rows:
        nome = codigo_to_nome.get(codigo)
        if nome is None:
            continue
        por_emissor_data.setdefault((nome, d), []).append((spread, estoque))

    pontos_por_emissor: dict[str, list[dict]] = {}
    for (nome, d), pares in por_emissor_data.items():
        com_peso = [(s, e) for s, e in pares if e is not None and e > 0]
        if com_peso:
            soma_peso = sum(e for _, e in com_peso)
            valor = sum(s * e for s, e in com_peso) / soma_peso if soma_peso else None
        else:
            valor = sum(s for s, _ in pares) / len(pares) if pares else None
        if valor is not None:
            pontos_por_emissor.setdefault(nome, []).append({"data": d.isoformat(), "spread": round(valor, 1)})

    series = [
        {"codigo": nome, "pontos": sorted(pontos, key=lambda p: p["data"])}
        for nome, pontos in pontos_por_emissor.items()
    ]
    return {"nivel": "emissor", "series": series}


def companies_for_emissores(db: Session, nomes_emissor: list[str]) -> dict[str, dict]:
    """Empresa da cobertura ligada a cada emissor (ver
    scripts/match_debenture_issuers.py) -- emissores sem match ficam de
    fora do dict (não têm o que retornar)."""
    rows = (
        db.query(Debenture.nome, Company.id, Company.name)
        .join(Company, Debenture.company_id == Company.id)
        .filter(Debenture.nome.in_(nomes_emissor))
        .distinct()
        .all()
    )
    return {nome: {"company_id": cid, "company_name": cname} for nome, cid, cname in rows}


def emissor_taxas(db: Session, nomes_emissor: list[str], classe: str) -> dict:
    """Dois "spreads" pro card no topo da aba Emissores, SEMPRE em bps
    (pedido do Allan, 24/07 e ajustado pra spread em 27/07/2026) -- NÃO
    são a mesma coisa, propositalmente mantidas separadas e nunca
    misturadas num único número:
    - `anbima_spread`: spread mais recente de cada ticker do(s)
      emissor(es) (já em bps, `DebentureSpread.spread` -- mesmo cálculo
      de `fetch.fetch_spreads`), ponderado por Estoque entre os tickers
      (mesmo padrão de `_weighted_avg_duration`/`emissor_series` nível
      emissor -- fallback pra média simples se nenhum ticker tiver
      Estoque cruzado naquela data). É o boletim consolidado 1x/dia da
      Anbima.
    - `anbima_spread_3m`: mesma conta, mas agregando os últimos 63
      pregões com dado (mesma janela de "QoQ" em COMPARACAO_BASES) em vez
      de só o dia mais recente -- referência histórica discreta ao lado
      do número principal.
    - `b3_spread`: spread ponderado por VOLUME (não Estoque) dos negócios
      individuais na B3 (negócio a negócio) no dia mais recente que teve
      negócio pra esses tickers -- calculado em
      `b3_trades.compute_trade_spreads` na hora de gravar cada negócio
      (CDI+ = taxa*100; IPCA+ = NTN-B de referência específica do papel,
      `Debenture.referencia_ntnb`, igual ao card Anbima -- só cai na
      NTN-B de vértice mais curto do dia quando o papel não tem essa
      referência própria; CORRIGIDO 27/07/2026, ver `b3_trades.py`). Só
      usa negócio com spread E volume calculados.
    - `b3_spread_7d`: mesma conta, mas numa janela móvel dos últimos 7
      DIAS corridos (não posições de pregão) -- referência discreta ao
      lado do `b3_spread`, mesmo papel que `anbima_spread_3m` faz pro
      card da Anbima. MUDOU (27/07/2026, pedido do Allan): era uma
      janela de 24h -- pouco negócio entra numa janela tão curta pra
      maioria dos emissores (a maior parte dos dias sem qualquer negócio
      nas últimas 24h), a média saía vazia quase sempre; 7 dias dá uma
      amostra bem maior sem perder o sentido de "recente" (o card
      principal `b3_spread` já cobre "o dia mais recente com negócio").

    BUG REAL evitado aqui (24/07/2026, achado testando com a Coelba antes
    de mandar pro Allan): `taxa_indicativa`/`taxa` NÃO são a mesma unidade
    entre indexadores diferentes -- "DI PERCENTUAL" guarda algo como
    104.7 (% do CDI) e "PREFIXADO"/"IPCA +"/"CDI +" guardam uma taxa
    normal tipo 8-15. Papel de indexador fora de IPCA+/CDI+ cai em
    `classe == "Outros"` -- por isso esta função exige `classe` e filtra
    os tickers por ela, igual ao resto do dashboard (e por isso
    `compute_trade_spreads` também só calcula spread pra essas duas
    classes, nunca pra "Outros")."""
    excluidos = tickers_excluidos_spread(db)
    codigos = [
        row[0]
        for row in db.query(Debenture.codigo)
        .filter(Debenture.nome.in_(nomes_emissor), Debenture.classe == classe)
        .all()
        if row[0] not in excluidos
    ]
    vazio = {
        "anbima_spread": None, "anbima_spread_fallback": False, "anbima_data": None,
        "anbima_spread_3m": None,
        "b3_spread": None, "b3_volume": None, "b3_n_negocios": 0, "b3_data": None,
        "b3_spread_7d": None,
    }
    if not codigos:
        return vazio

    # -- Anbima: spread mais recente de CADA ticker, ponderado por Estoque --
    ultimos: list[DebentureSpread] = []
    for codigo in codigos:
        row = (
            db.query(DebentureSpread)
            .filter(DebentureSpread.codigo == codigo, DebentureSpread.spread.isnot(None))
            .order_by(DebentureSpread.data.desc())
            .first()
        )
        if row is not None:
            ultimos.append(row)

    anbima_spread = None
    anbima_spread_fallback = False
    anbima_data = None
    if ultimos:
        com_peso = [(r.spread, r.estoque) for r in ultimos if r.estoque is not None and r.estoque > 0]
        if com_peso:
            soma_peso = sum(e for _, e in com_peso)
            anbima_spread = sum(s * e for s, e in com_peso) / soma_peso if soma_peso else None
        else:
            valores = [r.spread for r in ultimos]
            anbima_spread = sum(valores) / len(valores) if valores else None
            anbima_spread_fallback = True
        anbima_data = max(r.data for r in ultimos).isoformat()

    # -- Anbima: média ponderada dos últimos ~3 meses (63 posições) --
    datas_desc = [
        row[0]
        for row in db.query(DebentureSpread.data)
        .filter(DebentureSpread.codigo.in_(codigos), DebentureSpread.spread.isnot(None))
        .distinct()
        .order_by(DebentureSpread.data.desc())
        .limit(63)
        .all()
    ]
    anbima_spread_3m = None
    anbima_spread_3m_inicio = None
    anbima_spread_3m_fim = None
    if datas_desc:
        cutoff = min(datas_desc)
        anbima_spread_3m_inicio = cutoff.isoformat()
        anbima_spread_3m_fim = max(datas_desc).isoformat()
        rows_3m = (
            db.query(DebentureSpread.spread, DebentureSpread.estoque)
            .filter(
                DebentureSpread.codigo.in_(codigos),
                DebentureSpread.spread.isnot(None),
                DebentureSpread.data >= cutoff,
            )
            .all()
        )
        com_peso = [(s, e) for s, e in rows_3m if e is not None and e > 0]
        if com_peso:
            soma_peso = sum(e for _, e in com_peso)
            anbima_spread_3m = sum(s * e for s, e in com_peso) / soma_peso if soma_peso else None
        else:
            valores = [s for s, _e in rows_3m]
            anbima_spread_3m = sum(valores) / len(valores) if valores else None

    # -- B3: spread ponderado por volume, dia mais recente com negócio --
    ultima_data_negocio = (
        db.query(func.max(NegocioB3.data_negocio)).filter(NegocioB3.codigo.in_(codigos)).scalar()
    )
    b3_spread = None
    b3_volume = None
    b3_n_negocios = 0
    b3_data = None
    if ultima_data_negocio is not None:
        negocios = (
            db.query(NegocioB3.spread, NegocioB3.volume)
            .filter(NegocioB3.codigo.in_(codigos), NegocioB3.data_negocio == ultima_data_negocio)
            .all()
        )
        b3_n_negocios = len(negocios)
        b3_data = ultima_data_negocio.isoformat()
        com_spread = [(s, v) for s, v in negocios if s is not None and v is not None and v > 0]
        if com_spread:
            soma_vol = sum(v for _, v in com_spread)
            b3_spread = sum(s * v for s, v in com_spread) / soma_vol if soma_vol else None
            b3_volume = soma_vol

    # -- B3: spread ponderado por volume, janela móvel dos últimos 7 dias
    # corridos (MUDOU 27/07/2026, era 24h -- ver docstring) --
    agora_brt = datetime.now(_BRT)
    cutoff_7d = agora_brt - timedelta(days=7)
    candidatos_7d = (
        db.query(NegocioB3.data_negocio, NegocioB3.horario, NegocioB3.spread, NegocioB3.volume)
        .filter(
            NegocioB3.codigo.in_(codigos),
            NegocioB3.data_negocio >= cutoff_7d.date(),
            NegocioB3.spread.isnot(None),
            NegocioB3.volume.isnot(None),
        )
        .all()
    )
    pares_7d: list[tuple[float, float]] = []
    for data_negocio, horario, spread, volume in candidatos_7d:
        if volume is None or volume <= 0:
            continue
        try:
            hh, mm, ss = (horario or "00:00:00").split(":")
            momento = datetime(
                data_negocio.year, data_negocio.month, data_negocio.day,
                int(hh), int(mm), int(ss), tzinfo=_BRT,
            )
        except (ValueError, AttributeError):
            continue
        if momento >= cutoff_7d:
            pares_7d.append((spread, volume))
    b3_spread_7d = None
    if pares_7d:
        soma_vol_7d = sum(v for _, v in pares_7d)
        b3_spread_7d = sum(s * v for s, v in pares_7d) / soma_vol_7d if soma_vol_7d else None

    return {
        "anbima_spread": round(anbima_spread, 1) if anbima_spread is not None else None,
        "anbima_spread_fallback": anbima_spread_fallback,
        "anbima_data": anbima_data,
        "anbima_spread_3m": round(anbima_spread_3m, 1) if anbima_spread_3m is not None else None,
        # Intervalo explícito do "Média 3M" (pedido do Allan, 27/07/2026:
        # "sempre que colocar algum indicativo como 'Média 7d:' coloque
        # (data-data) explícito") -- posições reais com dado, não uma
        # janela nominal de calendário (63 posições pode cobrir mais de 3
        # meses corridos se houver feriados/gaps).
        "anbima_spread_3m_inicio": anbima_spread_3m_inicio,
        "anbima_spread_3m_fim": anbima_spread_3m_fim,
        "b3_spread": round(b3_spread, 1) if b3_spread is not None else None,
        "b3_volume": round(b3_volume, 2) if b3_volume is not None else None,
        "b3_n_negocios": b3_n_negocios,
        "b3_data": b3_data,
        "b3_spread_7d": round(b3_spread_7d, 1) if b3_spread_7d is not None else None,
        # Intervalo explícito do "Média 7d" -- aqui SIM é janela nominal
        # de calendário (cutoff_7d/agora), não posições com negócio (B3
        # não tem negócio todo dia pra todo papel, ao contrário do
        # boletim Anbima que publica 1x por dia útil).
        "b3_spread_7d_inicio": cutoff_7d.date().isoformat(),
        "b3_spread_7d_fim": agora_brt.date().isoformat(),
    }


def emissor_ranking_diferencas(
    db: Session, classe: str, top_n: int = 15, data_referencia: date | None = None,
) -> dict:
    """Ranking de TODOS os emissores da classe por diferença entre o
    spread negociado na B3 e o spread do boletim da Anbima -- pedido do
    Allan, 27/07/2026: tela inicial da aba Emissores (antes de selecionar
    algum emissor) mostrando "Emissor, Taxa Anbima na data selecionada,
    Taxa B3 (média ponderada da última semana, com base na data Anbima),
    variação", com top 15 diferenças positivas de um lado e top 15
    negativas do outro.

    Mesma metodologia de `emissor_taxas` (ponderado por Estoque do lado
    Anbima, por volume do lado B3), só que em massa pra classe inteira em
    vez de emissor por emissor, e com uma diferença de desenho
    importante: a janela de 7 dias do lado B3 é ancorada na PRÓPRIA data
    do boletim Anbima de cada emissor ("com base na data Anbima", pedido
    explícito do Allan) -- não em "agora" como o card `b3_spread_7d` de
    `emissor_taxas` (que faz sentido pra um emissor específico sendo
    olhado ao vivo, mas não pra um ranking onde cada emissor pode ter a
    Anbima publicada num dia ligeiramente diferente).

    `data_referencia` (opcional, pedido do Allan no dia seguinte -- "faltou
    a opção de eu poder alterar a data de referência, essa data precisa
    ficar explícita em algum lugar"): quando informada, cada ticker usa a
    última linha com spread PUBLICADA ATÉ essa data (não a mais recente
    de verdade) -- mesma ideia de "hoje" da Visão Geral (`_resolve_hoje`),
    só que aplicada por ticker em vez de um único "hoje" global pra
    classe inteira (esse ranking já tolerava emissores com datas Anbima
    ligeiramente diferentes entre si mesmo sem esse parâmetro). O campo
    `data_referencia` devolvido no resultado é a MAIOR data Anbima entre
    os emissores do ranking -- serve pra mostrar "Dados até: X" de forma
    explícita no front-end.

    "Variação" = B3 − Anbima (positivo: B3 negociando com spread MAIOR
    que o boletim -- mercado secundário mais largo que a marcação;
    negativo: B3 mais apertado que a Anbima) -- mesma convenção de sinal
    de "aberturas"/"fechamentos" usada no resto do dashboard. Só entram
    no ranking emissores com AMBOS os lados calculáveis (Anbima e pelo
    menos 1 negócio B3 na janela) -- maioria dos emissores não tem
    negócio recente, então o ranking cobre só uma fração da base (isso é
    esperado, não um bug)."""
    excluidos = tickers_excluidos_spread(db)
    debs = db.query(Debenture.codigo, Debenture.nome).filter(Debenture.classe == classe).all()
    codigo_para_nome = {c: n for c, n in debs if c not in excluidos and n}
    codigos = list(codigo_para_nome)
    vazio = {"aberturas": [], "fechamentos": [], "data_referencia": None}
    if not codigos:
        return vazio

    nome_para_codigos: dict[str, list[str]] = {}
    for codigo, nome in codigo_para_nome.items():
        nome_para_codigos.setdefault(nome, []).append(codigo)

    # -- lado Anbima: última linha com spread de cada ticker (até
    # `data_referencia`, quando informada) --
    sub_q = db.query(DebentureSpread.codigo, func.max(DebentureSpread.data).label("max_data")).filter(
        DebentureSpread.codigo.in_(codigos), DebentureSpread.spread.isnot(None)
    )
    if data_referencia is not None:
        sub_q = sub_q.filter(DebentureSpread.data <= data_referencia)
    sub = sub_q.group_by(DebentureSpread.codigo).subquery()
    ultimos = (
        db.query(DebentureSpread.codigo, DebentureSpread.spread, DebentureSpread.estoque, DebentureSpread.data)
        .join(sub, (DebentureSpread.codigo == sub.c.codigo) & (DebentureSpread.data == sub.c.max_data))
        .all()
    )
    por_emissor: dict[str, dict] = {}
    for codigo, spread, estoque, data in ultimos:
        nome = codigo_para_nome.get(codigo)
        if not nome:
            continue
        info = por_emissor.setdefault(nome, {"anbima": [], "anbima_data": None})
        info["anbima"].append((spread, estoque))
        if info["anbima_data"] is None or data > info["anbima_data"]:
            info["anbima_data"] = data
    if not por_emissor:
        return vazio

    # -- lado B3: busca em massa (uma query só) os negócios de TODOS os
    # tickers da classe numa janela larga o bastante pra cobrir a janela
    # de 7 dias de qualquer emissor (mesmo o com Anbima mais atrasada) --
    # filtragem fina de 7 dias por emissor acontece depois, em Python.
    data_mais_recente = max(info["anbima_data"] for info in por_emissor.values() if info["anbima_data"])
    inicio_busca = data_mais_recente - timedelta(days=60)  # folga generosa
    negocios_rows = (
        db.query(NegocioB3.codigo, NegocioB3.data_negocio, NegocioB3.spread, NegocioB3.volume)
        .filter(
            NegocioB3.codigo.in_(codigos),
            NegocioB3.data_negocio >= inicio_busca,
            NegocioB3.data_negocio <= data_mais_recente,
            NegocioB3.spread.isnot(None),
            NegocioB3.volume.isnot(None),
        )
        .all()
    )
    negocios_por_codigo: dict[str, list[tuple]] = {}
    for codigo, data_negocio, spread, volume in negocios_rows:
        if volume is None or volume <= 0:
            continue
        negocios_por_codigo.setdefault(codigo, []).append((data_negocio, spread, volume))

    resultados = []
    for nome, info in por_emissor.items():
        com_peso = [(s, e) for s, e in info["anbima"] if e is not None and e > 0]
        if com_peso:
            soma_peso = sum(e for _, e in com_peso)
            anbima_spread = (sum(s * e for s, e in com_peso) / soma_peso) if soma_peso else None
        else:
            valores = [s for s, _ in info["anbima"]]
            anbima_spread = sum(valores) / len(valores) if valores else None
        anbima_data = info["anbima_data"]
        if anbima_spread is None or anbima_data is None:
            continue

        janela_inicio = anbima_data - timedelta(days=7)
        pares_b3 = [
            (spread, volume)
            for codigo in nome_para_codigos.get(nome, [])
            for data_negocio, spread, volume in negocios_por_codigo.get(codigo, [])
            if janela_inicio <= data_negocio <= anbima_data
        ]
        if not pares_b3:
            continue
        soma_vol = sum(v for _, v in pares_b3)
        b3_spread = (sum(s * v for s, v in pares_b3) / soma_vol) if soma_vol else None
        if b3_spread is None:
            continue

        resultados.append({
            "emissor": nome,
            "anbima_spread": round(anbima_spread, 1),
            "anbima_data": anbima_data.isoformat(),
            "b3_spread_7d": round(b3_spread, 1),
            "variacao_bps": round(b3_spread - anbima_spread, 1),
        })

    aberturas = sorted(resultados, key=lambda r: r["variacao_bps"], reverse=True)[:top_n]
    fechamentos = sorted(resultados, key=lambda r: r["variacao_bps"])[:top_n]
    return {"aberturas": aberturas, "fechamentos": fechamentos, "data_referencia": data_mais_recente.isoformat()}


def emissor_trades(db: Session, nomes_emissor: list[str], classe: str, limit: int = 30) -> list[dict]:
    """Últimas negociações (negócio a negócio, B3 -- pedido do Allan,
    24/07/2026) dos tickers do(s) emissor(es) selecionados. Filtra pelos
    mesmos códigos que já alimentam `emissor_tickers` -- por isso hoje só
    aparece coisa pra DEB de verdade: CRI/CRA não têm `Debenture`/emissor
    ligado no cadastro (não são debêntures), então nunca vão ter ticker
    aqui até algum dia ganharem seu próprio cadastro. Ordenado do negócio
    mais recente pro mais antigo (data + horário).

    BUG REAL CORRIGIDO (27/07/2026): não filtrava por `classe` -- um
    emissor com séries em mais de uma classe (ex. uma debênture IPCA+
    Incentivada e outra CDI+ Tradicional, ou uma terceira que cai em
    "Outros") aparecia aqui com TODOS os tickers, mesmo com o filtro
    IPCA+/CDI+ selecionado na página. Allan reparou pela contagem: o
    card "SPREAD NEGOCIADO (B3)" (`emissor_taxas`, que já filtrava por
    classe) mostrava "9 negócio(s)" mas esta tabela mostrava bem mais.
    Agora exige `classe` igual ao resto da aba Emissores."""
    codigo_indexador = {
        row[0]: row[1]
        for row in db.query(Debenture.codigo, Debenture.indexador)
        .filter(Debenture.nome.in_(nomes_emissor), Debenture.classe == classe)
        .all()
    }
    codigos = list(codigo_indexador)
    if not codigos:
        return []
    rows = (
        db.query(NegocioB3)
        .filter(NegocioB3.codigo.in_(codigos))
        .order_by(NegocioB3.data_negocio.desc(), NegocioB3.horario.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "data_negocio": r.data_negocio.isoformat() if r.data_negocio else None,
            "horario": r.horario,
            "instrument_type": r.instrument_type,
            "emissor": r.emissor,
            "codigo": r.codigo,
            # Indexador no lugar de Quantidade na tabela (pedido do
            # Allan, 27/07/2026) -- vem do cadastro (`Debenture`), não do
            # negócio em si (a B3 não manda indexador negócio a negócio).
            "indexador": codigo_indexador.get(r.codigo),
            "quantidade": r.quantidade,
            "preco": round(r.preco, 4) if r.preco is not None else None,
            "volume": round(r.volume, 2) if r.volume is not None else None,
            "taxa": round(r.taxa, 2) if r.taxa is not None else None,
            "situacao": r.situacao,
        }
        for r in rows
    ]


def company_news(db: Session, company_ids: list[int], limit: int = 8) -> list[dict]:
    """Últimas notícias que mencionam qualquer uma das empresas (mesmo
    cadastro/matching do monitoramento de notícias, ver app/taxonomy.py) --
    lista de IDs pra suportar seleção múltipla de emissores na mesma
    consulta (notícias de todas as empresas selecionadas, misturadas por
    data)."""
    if not company_ids:
        return []
    articles = (
        db.query(Article)
        .join(Article.companies)
        .filter(Company.id.in_(company_ids))
        .order_by(Article.published_at.desc().nullslast(), Article.found_at.desc())
        .limit(limit)
        .distinct()
        .all()
    )
    return [
        {
            "id": a.id,
            "title": a.title,
            "url": a.url,
            "source_name": a.source_name,
            "article_type": a.article_type,
            "published_at": a.published_at.isoformat() if a.published_at else None,
        }
        for a in articles
    ]


def detalhes_latest_date(db: Session, classe: str) -> date | None:
    """Dia mais recente com dado, pro filtro do botão "Detalhes" default
    pra algo com dado assim que abre (sem precisar o Allan escolher uma
    data toda vez). `classe=""` (Todos) == IPCA+Incentivadas OU
    CDI+Tradicionais (NUNCA "Outros" -- pedido do Allan, 27/07/2026: "na
    base quero apenas IPCA + Incentivadas, CDI+ Tradicionais e todos (os
    dois)"). Só considera dia com pelo menos um spread calculado (papel
    sem taxa publicada não conta)."""
    q = (
        db.query(func.max(DebentureSpread.data))
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .filter(DebentureSpread.spread.isnot(None))
    )
    q = q.filter(Debenture.classe == classe) if classe else q.filter(Debenture.classe.in_(CLASSES))
    return q.scalar()


def _detalhes_row_dict(spread_row: "DebentureSpread", deb: "Debenture") -> dict:
    return {
        "codigo": spread_row.codigo,
        "taxa": spread_row.taxa_indicativa,
        "pct_pu_par": spread_row.pct_pu_par,
        "pu": spread_row.pu,
        "data": spread_row.data.isoformat() if spread_row.data else None,
        "indexador": deb.indexador,
        "incentivada": deb.incentivada,
        "spread": spread_row.spread,
        "estoque": spread_row.estoque,
        "duration": spread_row.duration,
    }


def detalhes_rows(db: Session, classe: str, data: date | None) -> dict:
    """Linhas granulares (Código+Data) de UM DIA -- botão "Detalhes" da
    aba Visão Geral (pedido do Allan, 27/07/2026): o nível mais granular
    de dado que os gráficos da página mostram, igual ao registro cru de
    `DebentureSpread`. `classe=""` (sentinela pro filtro "Todos", NÃO faz
    parte de `CLASSES` porque as duas classes normais nunca devem se
    misturar em GRÁFICO nenhum -- aqui é só listagem lado a lado, então
    misturar as DUAS classes é seguro e foi pedido explicitamente)
    devolve `IPCA + Incentivadas` OU `CDI + Tradicionais` -- NUNCA
    "Outros" (CORRIGIDO 27/07/2026, mesmo dia: a primeira versão incluía
    "Outros" em "Todos"; Allan pediu explicitamente "na base quero apenas
    IPCA + Incentivadas, CDI+ Tradicionais e todos (os dois)"). `data=None`
    usa o dia mais recente disponível pra essa classe
    (`detalhes_latest_date`).

    CORRIGIDO (27/07/2026, mesmo dia): papel sem `spread` calculado (a
    Anbima não publicou taxa pra ele naquele dia -- não foi precificado)
    NUNCA deve aparecer aqui nem em nenhuma conta de spread, pedido
    explícito do Allan. Mesma regra aplicada a `tickers_excluidos_spread`
    (lista manual da aba Administração).

    SIMPLIFICADO (27/07/2026, mesmo dia): a primeira versão filtrava
    "até uma data" (histórico inteiro, podendo passar de meio milhão de
    linhas) -- Allan pediu pra simplificar pra um dia só. Um dia tem no
    máximo ~1700 debêntures (o total cadastrado), então não precisa mais
    de paginação nem de streaming especial pro export -- a mesma função
    serve a tela E o CSV."""
    if data is None:
        data = detalhes_latest_date(db, classe)
    if data is None:
        return {"rows": [], "data": None}
    excluidos = tickers_excluidos_spread(db)
    q = (
        db.query(DebentureSpread, Debenture)
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .filter(DebentureSpread.data == data, DebentureSpread.spread.isnot(None))
    )
    q = q.filter(Debenture.classe == classe) if classe else q.filter(Debenture.classe.in_(CLASSES))
    if excluidos:
        q = q.filter(DebentureSpread.codigo.notin_(excluidos))
    rows = q.order_by(DebentureSpread.codigo.asc()).all()
    return {"rows": [_detalhes_row_dict(sr, deb) for sr, deb in rows], "data": data.isoformat()}


def bond_detail(db: Session, codigo: str) -> dict | None:
    deb = db.get(Debenture, codigo)
    if deb is None:
        return None
    return {
        "codigo": deb.codigo,
        "nome": deb.nome,
        "indexador": deb.indexador,
        "classe": deb.classe,
        "incentivada": deb.incentivada,
        "cnpj": deb.cnpj,
    }
