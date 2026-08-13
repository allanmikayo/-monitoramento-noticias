"""Análises de valor relativo da aba Spreads — o miolo analítico da tela.

MOTIVO (12/08/2026): o Allan pediu pra reorganizar a aba Spreads "com a
cabeça de um analista sell side muito experiente e de um investidor buy
side muito bom", incorporando as análises que estavam na aba Mercado do
dashboard antigo, e sugerindo o que falta.

O que a tela antiga respondia era só a PRIMEIRA pergunta do funil de quem
analisa crédito:

    1. NÍVEL          — o mercado está caro ou barato?     (tinha: spread médio)
    2. MOVIMENTO      — como chegamos aqui?                (tinha: evolução, movers)
    3. VALOR RELATIVO — onde dentro do mercado está o valor? (NÃO TINHA NADA)
    4. EXECUÇÃO       — dá pra comprar?                     (está no Balcão B3)

Este módulo é o bloco 3, que era o buraco. Ele existe pra responder a
única pergunta que gera decisão: *dado o rating, a duration e o setor,
qual papel está pagando mais do que deveria?*

O QUE A MEDIÇÃO NO BANCO REAL MOSTROU (04/08/2026, IPCA+, 592 papéis)
---------------------------------------------------------------------
    desvio do spread bruto                   120,0 bps
    tirando rating e duration                 99,2 bps   -> rating explica 32%
    tirando também o setor                    93,2 bps   -> setor explica 8%
    sobra                                                -> 60% idiossincrático

Ou seja: **rating explica menos de um terço do spread.** Uma tabela de
"spread médio por rating" — que era o principal recorte da tela antiga —
esconde 68% do que move preço. É isso que justifica o bloco de valor
relativo existir.

DUAS ARMADILHAS QUE JÁ MORDERAM AQUI
------------------------------------
1. **`incentivada` é `'S'`/`'N'`, não booleano.** `bool('N')` é `True` em
   Python, então agrupar por `bool(row.incentivada)` joga incentivada e
   não-incentivada no MESMO grupo e o corte simplesmente não acontece --
   sem erro nenhum, só um resultado errado. Use `== "S"`. Em IPCA+ a base
   é 575 incentivadas contra 17 não (Lei 12.431), então isso vira filtro,
   não corte principal; em CDI+ é o contrário.

2. **Mínimos quadrados não serve pra ajustar a curva.** Papéis como
   VAMO33 (AA carregando 570 bps há 400 pregões) não são dado ruim -- são
   preço de verdade, do setor de locação de frota sob estresse. Mas com
   OLS eles puxam a reta e o R² deu 0,00 em TODOS os ratings. Usamos
   **Theil-Sen** (mediana das inclinações par a par): ponto extremo entra
   na conta como um voto entre milhares, não arrasta a reta. E a dispersão
   é medida por **MAD**, não desvio-padrão, pelo mesmo motivo.
"""
from __future__ import annotations

import statistics
from datetime import date

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from .queries import (
    _resolve_hoje,
    distinct_dates,
    tickers_excluidos_spread,
)
from .ratings import SEM_RATING

# Grupo pequeno demais não tem curva: com 5 papéis a "mediana das
# inclinações" é ruído com cara de estimativa. 12 é o menor grupo em que
# o ajuste ficou estável ao reamostrar a base real.
MINIMO_PARA_CURVA = 12

# Idem pro prêmio setorial -- setor com 3 papéis não tem prêmio, tem um
# emissor.
MINIMO_POR_SETOR = 6

# Piso pra dispersão de referência (MAD) de um grupo de rating, em bps.
#
# DOIS MOTIVOS, e o primeiro é um bug de verdade:
#
# 1. **MAD zero apaga o outlier.** Se todos os papéis de uma faixa caem
#    exatamente na curva, o MAD é 0 -- e aí `residuo / MAD` era forçado a
#    0 pra todo mundo, INCLUSIVE pro papel deslocado. O único papel que
#    interessava sumia da lista, sem erro nenhum. Peguei isso num teste
#    com base sintética, mas o caso degenerado aparece de verdade em
#    faixa com poucos papéis e taxa espelhada.
#
# 2. **MAD minúsculo infla o z.** Medido na base real: AA+ com MAD de
#    6,9 bps produzia z acima de 50. O spread vem de uma taxa indicativa
#    publicada com 2 casas; abaixo de uns poucos bps a "dispersão" que se
#    está medindo é resolução do dado, não comportamento de mercado.
#    Normalizar por um número desses é afirmar uma precisão que a fonte
#    não tem.
MAD_MINIMO_BPS = 5.0

# Acima disso o papel entra na lista de revisão em vez da de oportunidade.
# 4 MADs é o ponto onde, na base real, todos os casos que olhei eram ou
# estresse conhecido (Vamos, Movida, Simpar) ou papel ilíquido com taxa
# encalhada -- nos dois casos o analista precisa OLHAR, não comprar pelo
# número.
Z_REVISAO = 4.0


def _linhas(
    db: Session, classe: str, data: date, excluidos: set[str] | None = None,
) -> list[dict]:
    """Uma linha por papel na data, já com rating/setor da `v_spread_rating`.

    Consulta crua (não ORM) porque `v_spread_rating` é uma VIEW -- ela
    resolve a junção as-of do rating vigente e a precedência
    emissão > emissor, que é justamente o que não queremos reimplementar
    em cada análise (ver app/spreads/views.py).
    """
    sql = """
        SELECT codigo, emissor, setor, rating_medio, notch_medio,
               duration, spread, estoque, incentivada
          FROM v_spread_rating
         WHERE data = :data AND classe = :classe
           AND spread IS NOT NULL AND duration IS NOT NULL
    """
    rows = db.execute(text(sql), {"data": data, "classe": classe}).mappings().all()
    ex = excluidos or set()
    return [dict(r) for r in rows if r["codigo"] not in ex]


# ---------------------------------------------------------------------------
# Ajuste robusto
# ---------------------------------------------------------------------------

def theil_sen(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Reta robusta: inclinação = mediana das inclinações par a par.

    Devolve `(intercepto, inclinação)`. Preferido a mínimos quadrados
    porque o universo de spread tem cauda pesada legítima -- ver a
    armadilha nº 2 na docstring do módulo.

    É O(n²) nos pares. Com n até ~400 por rating (o maior grupo real é
    AAA, 359 papéis) são ~64 mil pares, uns 30 ms -- não vale complicar.
    """
    n = len(xs)
    if n < 2:
        return (ys[0] if ys else 0.0), 0.0
    inclinacoes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(n)
        for j in range(i + 1, n)
        # Dois papéis com a MESMA duration dariam divisão por zero. Não é
        # hipotético: emissor que emite duas séries no mesmo vencimento
        # aparece com duration idêntica até a 6ª casa.
        if abs(xs[j] - xs[i]) > 1e-9
    ]
    if not inclinacoes:
        return statistics.median(ys), 0.0
    b = statistics.median(inclinacoes)
    a = statistics.median([y - b * x for x, y in zip(xs, ys)])
    return a, b


def _mad(valores: list[float]) -> float:
    """Desvio absoluto mediano — a medida de dispersão que os outliers não
    sequestram. Desvio-padrão de uma amostra com Vamos dentro mede a
    Vamos, não o mercado."""
    if not valores:
        return 0.0
    m = statistics.median(valores)
    return statistics.median([abs(v - m) for v in valores])


# ---------------------------------------------------------------------------
# 1. NÍVEL — o mercado está caro ou barato?
# ---------------------------------------------------------------------------

def posicao_historica(
    db: Session, classe: str, data_referencia: date | None = None, janela: int = 504,
) -> dict:
    """Onde o spread médio de hoje cai na distribuição dos últimos ~2 anos.

    POR QUE ISSO É O PRIMEIRO NÚMERO DA TELA: "43,1 bps" não significa
    nada sozinho. "43,1 bps, percentil 50 de 2 anos" já é uma posição.
    O card de spread médio da tela antiga dava o nível sem o contexto, e
    contexto é o que separa observar de decidir.

    A série usada é a do spread médio PONDERADO POR ESTOQUE, a mesma
    metodologia de `queries._weighted_avg_spread` -- refeita aqui em SQL
    agregado (uma consulta pro histórico inteiro) porque chamar a função
    do ORM 504 vezes levava ~8 s.
    """
    excluidos = tickers_excluidos_spread(db)
    datas = distinct_dates(db, classe)
    hoje = _resolve_hoje(datas, data_referencia)
    if hoje is None:
        return {"data": None, "spread": None, "percentil": None, "serie": []}

    # `IN` com lista em `text()` exige bindparam EXPANDING -- passar uma
    # tupla direto num `:nome` comum vira um único parâmetro escalar e o
    # SQL quebra. `expanding=True` faz o SQLAlchemy explodir em
    # `(:x_1, :x_2, ...)` na hora de executar.
    filtro_ex = " AND codigo NOT IN :excluidos" if excluidos else ""
    params: dict = {"classe": classe, "hoje": hoje}

    sql = text(f"""
        SELECT data,
               SUM(spread * estoque) / NULLIF(SUM(estoque), 0) AS media
          FROM v_spread_rating
         WHERE classe = :classe AND spread IS NOT NULL
           AND estoque IS NOT NULL AND estoque > 0
           AND data <= :hoje{filtro_ex}
         GROUP BY data
         ORDER BY data DESC
         LIMIT {int(janela)}
    """)
    if excluidos:
        sql = sql.bindparams(bindparam("excluidos", expanding=True))
        params["excluidos"] = list(excluidos)
    serie = [
        {"data": r["data"], "spread": float(r["media"])}
        for r in db.execute(sql, params).mappings().all()
        if r["media"] is not None
    ]
    if not serie:
        return {"data": hoje.isoformat(), "spread": None, "percentil": None, "serie": []}

    serie.reverse()  # cronológica, pro gráfico
    atual = serie[-1]["spread"]
    valores = sorted(s["spread"] for s in serie)
    # Percentil por posição na amostra, não interpolado: com ~500 pontos a
    # diferença é irrelevante e a definição fica óbvia pra quem confere na
    # mão ("quantos dias tiveram spread menor que hoje").
    abaixo = sum(1 for v in valores if v < atual)
    return {
        "data": serie[-1]["data"] if isinstance(serie[-1]["data"], str)
                else serie[-1]["data"].isoformat(),
        "spread": round(atual, 1),
        "percentil": round(100 * abaixo / len(valores)),
        "minimo": round(valores[0], 1),
        "mediana": round(statistics.median(valores), 1),
        "maximo": round(valores[-1], 1),
        "n_observacoes": len(valores),
        "serie": [
            {"data": s["data"] if isinstance(s["data"], str) else s["data"].isoformat(),
             "spread": round(s["spread"], 1)}
            for s in serie
        ],
    }


def curva_por_rating(
    db: Session, classe: str, data_referencia: date | None = None,
) -> dict:
    """Estrutura a termo do crédito: spread × duration, uma reta por rating.

    É o gráfico que um analista de crédito abre primeiro e que a tela
    antiga não tinha. Ele responde de uma vez: quanto o mercado paga por
    risco (distância entre as retas) e quanto paga por prazo (inclinação
    de cada uma).

    A inclinação é o número mais acionável da tela: é o **roll-down**.
    Uma reta de 6 bps/ano quer dizer que só de o papel envelhecer um ano,
    andando pra baixo na própria curva, ele ganha 6 bps de spread —
    carrego que não depende de o mercado se mover.
    """
    excluidos = tickers_excluidos_spread(db)
    datas = distinct_dates(db, classe)
    hoje = _resolve_hoje(datas, data_referencia)
    if hoje is None:
        return {"data": None, "curvas": []}

    linhas = [
        r for r in _linhas(db, classe, hoje, excluidos)
        if r["rating_medio"] != SEM_RATING
    ]
    por_rating: dict[str, list[dict]] = {}
    for r in linhas:
        por_rating.setdefault(r["rating_medio"], []).append(r)

    curvas = []
    for rating, grupo in por_rating.items():
        if len(grupo) < MINIMO_PARA_CURVA:
            continue
        xs = [g["duration"] for g in grupo]
        ys = [g["spread"] for g in grupo]
        a, b = theil_sen(xs, ys)
        residuos = [y - (a + b * x) for x, y in zip(xs, ys)]
        curvas.append({
            "rating": rating,
            "notch": grupo[0]["notch_medio"],
            "n": len(grupo),
            "intercepto": round(a, 1),
            "roll_down_bps_ano": round(b, 1),
            "mad_residuo": round(_mad(residuos), 1),
            "duration_min": round(min(xs), 2),
            "duration_max": round(max(xs), 2),
            "pontos": [
                {"codigo": g["codigo"], "emissor": g["emissor"],
                 "duration": round(g["duration"], 2), "spread": round(g["spread"], 1),
                 "estoque": g["estoque"]}
                for g in grupo
            ],
        })
    curvas.sort(key=lambda c: c["notch"] if c["notch"] is not None else 99)
    return {"data": hoje.isoformat(), "curvas": curvas}


def dispersao_intra_rating(
    db: Session, classe: str, data_referencia: date | None = None,
) -> dict:
    """Quanto os papéis de um MESMO rating diferem entre si.

    Lê-se como termômetro de discriminação: dispersão baixa é mercado
    tratando o rating como suficiente (compra-se o bucket); dispersão alta
    é mercado precificando risco caso a caso — e aí seleção paga.

    Na base real a dispersão cresce monotonicamente com o risco (AAA
    ~51 bps de desvio, A+ ~296 bps), o que é o esperado e serve de
    conferência: se algum dia AAA aparecer mais disperso que A, é sinal de
    problema nos dados, não de mercado.
    """
    excluidos = tickers_excluidos_spread(db)
    datas = distinct_dates(db, classe)
    hoje = _resolve_hoje(datas, data_referencia)
    if hoje is None:
        return {"data": None, "faixas": []}

    linhas = [
        r for r in _linhas(db, classe, hoje, excluidos)
        if r["rating_medio"] != SEM_RATING
    ]
    por_rating: dict[str, list[dict]] = {}
    for r in linhas:
        por_rating.setdefault(r["rating_medio"], []).append(r)

    faixas = []
    for rating, grupo in por_rating.items():
        if len(grupo) < MINIMO_PARA_CURVA:
            continue
        v = sorted(g["spread"] for g in grupo)
        faixas.append({
            "rating": rating,
            "notch": grupo[0]["notch_medio"],
            "n": len(v),
            "p10": round(v[len(v) // 10], 1),
            "p25": round(v[len(v) // 4], 1),
            "mediana": round(statistics.median(v), 1),
            "p75": round(v[(3 * len(v)) // 4], 1),
            "p90": round(v[-max(1, len(v) // 10)], 1),
            "amplitude": round(v[-max(1, len(v) // 10)] - v[len(v) // 10], 1),
        })
    faixas.sort(key=lambda f: f["notch"] if f["notch"] is not None else 99)
    return {"data": hoje.isoformat(), "faixas": faixas}


# ---------------------------------------------------------------------------
# 2. MOVIMENTO — o ciclo de crédito
# ---------------------------------------------------------------------------

def compressao_entre_ratings(
    db: Session, classe: str, passo: int = 21, janela: int = 504,
) -> dict:
    """Diferencial AA−AAA e A−AA ao longo do tempo — o ciclo de crédito.

    É a análise de movimento que faltava. A evolução do spread médio diz
    se o mercado abriu ou fechou; ESTA diz se abriu/fechou *igual pra
    todo mundo*. São coisas diferentes e a segunda é a que antecipa
    virada de ciclo:

        compressão  -> apetite por risco subindo, prêmio de crédito
                       sumindo (fim de ciclo benigno)
        abertura    -> mercado voltando a cobrar por risco

    Medido na base real: o diferencial AA−AAA saiu de ~44 bps em meados de
    2025 pra ~76 bps agora. Descompressão — o mercado voltou a discriminar.

    Amostra a cada `passo` pregões (padrão 21 ≈ 1 mês) em vez de todo dia:
    o diferencial diário é ruidoso e a leitura que interessa é de
    tendência, não de pregão.
    """
    excluidos = tickers_excluidos_spread(db)
    datas = distinct_dates(db, classe)[:janela]
    if not datas:
        return {"pontos": [], "pares": []}
    amostra = list(reversed(datas[::passo]))

    pontos = []
    for d in amostra:
        linhas = [
            r for r in _linhas(db, classe, d, excluidos)
            if r["rating_medio"] != SEM_RATING
        ]
        por: dict[str, list[float]] = {}
        for r in linhas:
            por.setdefault(r["rating_medio"], []).append(r["spread"])
        # Mediana, não média ponderada: o diferencial tem que refletir o
        # papel TÍPICO de cada faixa. Ponderado por estoque, AAA vira
        # Petrobras e Vale e o "AAA" do gráfico passa a ser o spread de
        # dois emissores.
        medianas = {
            rt: round(statistics.median(v), 1)
            for rt, v in por.items() if len(v) >= MINIMO_POR_SETOR
        }
        pontos.append({"data": d.isoformat(), "medianas": medianas})

    pares = []
    for maior, menor, rotulo in (("AA", "AAA", "AA − AAA"), ("A", "AA", "A − AA")):
        serie = [
            {"data": p["data"],
             "valor": round(p["medianas"][maior] - p["medianas"][menor], 1)}
            for p in pontos
            if maior in p["medianas"] and menor in p["medianas"]
        ]
        if serie:
            pares.append({"rotulo": rotulo, "serie": serie})
    return {"pontos": pontos, "pares": pares}


# ---------------------------------------------------------------------------
# 3. VALOR RELATIVO — onde está o valor (o bloco que não existia)
# ---------------------------------------------------------------------------

def decomposicao(
    db: Session, classe: str, data_referencia: date | None = None, top_n: int = 12,
) -> dict:
    """Separa o spread em rating + duration, prêmio de setor e idiossincrático.

    A cascata é:

        spread                                  (o que se observa)
          − curva Theil-Sen do próprio rating   -> resíduo
          − mediana do resíduo do setor         -> idiossincrático

    O idiossincrático é o único termo que representa uma OPINIÃO: é o que
    sobra depois de tirar tudo que é explicável por classificação. Papel
    com idiossincrático positivo paga acima do que seus pares de mesmo
    rating, mesma duration e mesmo setor pagam — é aí que se procura.

    E o painel mostra *quanto* cada camada explica, porque isso calibra a
    confiança em cima do resto da tela: na base real rating explica 32% e
    setor mais 8%, então 60% do spread não é capturado por nenhuma
    classificação. Uma tela que só mostrasse médias por rating estaria
    escondendo essa maior parte.

    `top_n` é por lista (baratos e caros), não no total.
    """
    excluidos = tickers_excluidos_spread(db)
    datas = distinct_dates(db, classe)
    hoje = _resolve_hoje(datas, data_referencia)
    if hoje is None:
        return {"data": None, "explicacao": {}, "setores": [], "baratos": [], "caros": [], "revisar": []}

    linhas = [
        r for r in _linhas(db, classe, hoje, excluidos)
        if r["rating_medio"] != SEM_RATING
    ]
    por_rating: dict[str, list[dict]] = {}
    for r in linhas:
        por_rating.setdefault(r["rating_medio"], []).append(r)

    ajustados: list[dict] = []
    for _rating, grupo in por_rating.items():
        if len(grupo) < MINIMO_PARA_CURVA:
            continue
        a, b = theil_sen([g["duration"] for g in grupo], [g["spread"] for g in grupo])
        for g in grupo:
            g["esperado_rating"] = a + b * g["duration"]
            g["residuo"] = g["spread"] - g["esperado_rating"]
        ajustados += grupo

    if not ajustados:
        return {"data": hoje.isoformat(), "explicacao": {}, "setores": [],
                "baratos": [], "caros": [], "revisar": []}

    por_setor: dict[str, list[float]] = {}
    for g in ajustados:
        por_setor.setdefault(g["setor"] or "—", []).append(g["residuo"])
    premio_setor = {
        s: statistics.median(v) for s, v in por_setor.items() if len(v) >= MINIMO_POR_SETOR
    }
    for g in ajustados:
        g["premio_setor"] = premio_setor.get(g["setor"] or "—", 0.0)
        g["idiossincratico"] = g["residuo"] - g["premio_setor"]

    # Quanto cada camada explica: redução de VARIÂNCIA (desvio², não
    # desvio), que é a decomposição que soma 100%.
    def var(vals: list[float]) -> float:
        return statistics.pvariance(vals) if len(vals) > 1 else 0.0

    v_bruto = var([g["spread"] for g in ajustados])
    v_resid = var([g["residuo"] for g in ajustados])
    v_idio = var([g["idiossincratico"] for g in ajustados])
    explicacao = {
        "n": len(ajustados),
        "desvio_bruto": round(v_bruto ** 0.5, 1),
        "desvio_sem_rating": round(v_resid ** 0.5, 1),
        "desvio_idiossincratico": round(v_idio ** 0.5, 1),
        "pct_rating": round(100 * (v_bruto - v_resid) / v_bruto) if v_bruto else 0,
        "pct_setor": round(100 * (v_resid - v_idio) / v_bruto) if v_bruto else 0,
        "pct_idiossincratico": round(100 * v_idio / v_bruto) if v_bruto else 0,
    }

    setores = sorted(
        (
            {"setor": s, "n": len(por_setor[s]), "premio_bps": round(p, 1)}
            for s, p in premio_setor.items()
        ),
        key=lambda x: -x["premio_bps"],
    )

    # Normaliza o idiossincrático pelo MAD do próprio rating: 80 bps acima
    # da curva significa coisas MUITO diferentes em AAA (onde o MAD é
    # ~20 bps) e em A+ (onde é ~209 bps). Sem isso a lista de "baratos"
    # vira só a lista dos ratings mais baixos.
    mad_por_rating = {
        # `max(..., MAD_MINIMO_BPS)` -- ver a constante pros dois motivos.
        # Sem o piso, faixa perfeitamente ajustada tem MAD 0 e o papel
        # deslocado recebe z=0, ou seja, some justamente da lista pra qual
        # ele deveria ir.
        rt: max(_mad([g["idiossincratico"] for g in grupo]), MAD_MINIMO_BPS)
        for rt, grupo in por_rating.items() if len(grupo) >= MINIMO_PARA_CURVA
    }
    for g in ajustados:
        m = mad_por_rating.get(g["rating_medio"], MAD_MINIMO_BPS)
        g["z"] = g["idiossincratico"] / m

    def cartao(g: dict) -> dict:
        return {
            "codigo": g["codigo"], "emissor": g["emissor"], "setor": g["setor"],
            "rating": g["rating_medio"], "duration": round(g["duration"], 2),
            "spread": round(g["spread"], 1),
            "esperado": round(g["esperado_rating"] + g["premio_setor"], 1),
            "idiossincratico": round(g["idiossincratico"], 1),
            "z": round(g["z"], 1),
            "estoque": g["estoque"],
        }

    # Extremos saem das listas de oportunidade e vão pra lista de revisão
    # — ver Z_REVISAO. Papel a 12 MADs da curva quase nunca é oportunidade;
    # é estresse conhecido ou taxa encalhada por falta de negócio.
    normais = [g for g in ajustados if abs(g["z"]) <= Z_REVISAO]
    extremos = [g for g in ajustados if abs(g["z"]) > Z_REVISAO]

    return {
        "data": hoje.isoformat(),
        "explicacao": explicacao,
        "setores": setores,
        "baratos": [cartao(g) for g in sorted(normais, key=lambda g: -g["z"])[:top_n]],
        "caros": [cartao(g) for g in sorted(normais, key=lambda g: g["z"])[:top_n]],
        "revisar": [cartao(g) for g in sorted(extremos, key=lambda g: -abs(g["z"]))[:top_n]],
        "z_revisao": Z_REVISAO,
    }


def resumo_por_rating(
    db: Session, classe: str, data_referencia: date | None = None,
) -> dict:
    """Tabela spread/estoque/duration por rating — o recorte que o Allan já
    usa, mantido, mas agora com o número de papéis e a fatia de estoque ao
    lado pra deixar claro o peso de cada faixa.

    Ponderado por estoque (mesma metodologia do resto do dashboard, ver
    `queries._weighted_avg_spread`), com mediana ao lado: quando as duas
    divergem muito, a faixa está sendo dominada por um emissor grande —
    e isso é informação, não defeito.
    """
    excluidos = tickers_excluidos_spread(db)
    datas = distinct_dates(db, classe)
    hoje = _resolve_hoje(datas, data_referencia)
    if hoje is None:
        return {"data": None, "linhas": [], "total_estoque": 0.0}

    linhas = _linhas(db, classe, hoje, excluidos)
    por: dict[str, list[dict]] = {}
    for r in linhas:
        por.setdefault(r["rating_medio"], []).append(r)

    total_estoque = sum((r["estoque"] or 0) for r in linhas)
    saida = []
    for rating, grupo in por.items():
        com_estoque = [g for g in grupo if g["estoque"]]
        peso = sum(g["estoque"] for g in com_estoque)
        pond = (
            sum(g["spread"] * g["estoque"] for g in com_estoque) / peso
            if peso else None
        )
        dur = (
            sum(g["duration"] * g["estoque"] for g in com_estoque) / peso
            if peso else None
        )
        saida.append({
            "rating": rating,
            "notch": grupo[0]["notch_medio"],
            "n": len(grupo),
            "spread_ponderado": round(pond, 1) if pond is not None else None,
            "spread_mediano": round(statistics.median([g["spread"] for g in grupo]), 1),
            "duration": round(dur, 2) if dur is not None else None,
            "estoque": round(peso, 1),
            "pct_estoque": round(100 * peso / total_estoque, 1) if total_estoque else 0.0,
        })
    # "N.A." sempre por último, independentemente do notch (que é nulo).
    saida.sort(key=lambda x: (x["rating"] == SEM_RATING,
                              x["notch"] if x["notch"] is not None else 99))
    return {"data": hoje.isoformat(), "linhas": saida,
            "total_estoque": round(total_estoque, 1)}
