"""Normalização de rating e cálculo do RATING MÉDIO por emissor.

Porte fiel da calculadora em Excel que o Allan usa hoje (fórmula enviada
por ele em 04/08/2026) — era a única peça de lógica de negócio do
dashboard que não existia em código nenhum, só na planilha. Sem isto, as
agregações "spread médio por rating" ficam impossíveis (lacuna registrada
no CLAUDE.md, seção "O que o relatório semanal do Allan tem que este
dashboard NÃO cobre").

A fórmula original do Excel é uma cadeia MATCH → AVERAGE → ROUND →
HLOOKUP:

1. MATCH   — cada rating vira um PESO inteiro de 1 (AAA) a 22 (D), numa
             tabela por agência (as escalas nacionais têm sufixo/prefixo
             diferente: `AA+(bra)` na Fitch, `brAA+` na S&P, `AA+.br` na
             Moody's).
2. AVERAGE — média aritmética simples dos pesos das agências que TÊM
             rating. Agência sem rating (`-`, vazio, `N.A.`) não entra na
             conta e não puxa a média — não é tratada como zero.
3. ROUND   — arredondamento "half up" do Excel (2,5 vira 3, não 2 como
             faria o `round()` nativo do Python, que usa banker's
             rounding — ver `_round_half_up`).
4. HLOOKUP — o peso arredondado volta pra uma escala PADRÃO sem sufixo de
             agência (`AAA`, `AA+`, ...), que é a que o dashboard mostra.

Emissor sem nenhum rating reconhecido devolve `"N.A."` com peso `None` —
NÃO devolve o pior rating nem zero (é ausência de informação, não é grau
especulativo; misturar as duas coisas distorceria qualquer média de
spread por rating).

REGRAS A MANTER
---------------
- **Não "conserte" os buracos das tabelas.** Elas são cópia literal da
  planilha e têm lacunas de propósito: a Fitch não tem `CCC(bra)` (pula
  de `CCC+(bra)`=17 pra `CCC-(bra)`=18), a S&P não tem nada no peso 21
  (`C`) nem `brC`, e a Moody's também não tem o 21. Preencher esses
  buracos mudaria a média de qualquer emissor na faixa CCC/CC e faria o
  resultado divergir da planilha do Allan — que é a referência.

  **DECISÃO EXPLÍCITA DO ALLAN (04/08/2026):** a validação contra as
  93.764 linhas do snapshot achou dois valores reais que não casam em
  lacuna nenhuma — `brCCC` na S&P (7 ocorrências) e `D(bra)` na Fitch
  (1 ocorrência). Perguntei se deviam virar peso 17/18 e 22. Resposta:
  **manter os pesos exatamente como estão, não alterar.** Então esses
  dois continuam caindo em `ratings_desconhecidos()` — ignorados na
  média e sinalizados na tela de Administração, nunca convertidos por
  chute. Não adicione essas chaves sem falar com ele de novo.
- **`"D"` é igual pras três agências** (peso 22), sem prefixo/sufixo. É a
  única chave compartilhada entre as três tabelas.
- **Case sensitive**, igual ao MATCH do Excel: `AA+(bra)` casa,
  `aa+(bra)` não. `normalizar_rating` faz uma tolerância mínima
  (espaços em volta) e nada mais — ver docstring de lá.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Tabelas de peso por agência (escala NACIONAL brasileira)
#
# Cópia literal da planilha do Allan. Ver "REGRAS A MANTER" no topo antes
# de mexer em qualquer linha destas três tabelas.
# ---------------------------------------------------------------------------

FITCH_PESO: dict[str, int] = {
    "AAA(bra)": 1,  "AA+(bra)": 2,  "AA(bra)": 3,  "AA-(bra)": 4,
    "A+(bra)": 5,   "A(bra)": 6,    "A-(bra)": 7,
    "BBB+(bra)": 8, "BBB(bra)": 9,  "BBB-(bra)": 10,
    "BB+(bra)": 11, "BB(bra)": 12,  "BB-(bra)": 13,
    "B+(bra)": 14,  "B(bra)": 15,   "B-(bra)": 16,
    "CCC+(bra)": 17, "CCC-(bra)": 18, "CC(bra)": 19, "CC-(bra)": 20,
    "C(bra)": 21,
    "D": 22,
}

SP_PESO: dict[str, int] = {
    "brAAA": 1,  "brAA+": 2,  "brAA": 3,  "brAA-": 4,
    "brA+": 5,   "brA": 6,    "brA-": 7,
    "brBBB+": 8, "brBBB": 9,  "brBBB-": 10,
    "brBB+": 11, "brBB": 12,  "brBB-": 13,
    "brB+": 14,  "brB": 15,   "brB-": 16,
    "brCCC+": 17, "brCCC-": 18, "brCC": 19, "brCC-": 20,
    # sem peso 21 (C) na tabela da S&P -- de propósito, ver topo
    "D": 22,
}

MOODYS_PESO: dict[str, int] = {
    "AAA.br": 1,  "AA+.br": 2,  "AA.br": 3,  "AA-.br": 4,
    "A+.br": 5,   "A.br": 6,    "A-.br": 7,
    "BBB+.br": 8, "BBB.br": 9,  "BBB-.br": 10,
    "BB+.br": 11, "BB.br": 12,  "BB-.br": 13,
    "B+.br": 14,  "B.br": 15,   "B-.br": 16,
    "CCC+.br": 17, "CCC-.br": 18, "CC.br": 19, "CC-.br": 20,
    # sem peso 21 (C) na tabela da Moody's -- de propósito, ver topo
    "D": 22,
}

# Escala padrão (sem sufixo de agência) -- o que o dashboard exibe.
PESO_TO_PADRAO: dict[int, str] = {
    1: "AAA",  2: "AA+",  3: "AA",  4: "AA-",
    5: "A+",   6: "A",    7: "A-",
    8: "BBB+", 9: "BBB",  10: "BBB-",
    11: "BB+", 12: "BB",  13: "BB-",
    14: "B+",  15: "B",   16: "B-",
    17: "CCC+", 18: "CCC-", 19: "CC", 20: "CC-",
    21: "C",   22: "D",
}

PADRAO_TO_PESO: dict[str, int] = {v: k for k, v in PESO_TO_PADRAO.items()}

AGENCIAS = ("fitch", "sp", "moodys")

_TABELAS: dict[str, dict[str, int]] = {
    "fitch": FITCH_PESO,
    "sp": SP_PESO,
    "moodys": MOODYS_PESO,
}

# Valores que a Anbima/os scrapers/o Excel usam pra "não tem rating".
# Tratados como ausência de informação (não entram na média) -- ver
# docstring do módulo.
#
# A lista saiu da validação contra as 93.764 linhas do snapshot
# (04/08/2026), não de suposição:
# - `"-"`     : o marcador padrão da base do Allan.
# - `"N.R."`  : "Not Rated" -- 33.427 ocorrências, quase todas em Moody's.
#               É ausência de rating de verdade, NÃO erro de dado, então
#               não deve aparecer em `ratings_desconhecidos()`.
# - `"0"`     : célula vazia lida como zero pelo Excel (99 ocorrências em
#               Fitch e S&P, 13 em Moody's) -- artefato de planilha.
# - `"(blank)"`: idem, marcador de tabela dinâmica do Excel (30 linhas).
_VAZIOS = {
    "", "-", "--", "n.a.", "na", "n/a", "n.r.", "nr", "nao rated",
    "nan", "none", "null", "sem rating", "0", "(blank)",
}

# Valor devolvido quando nenhuma agência tem rating reconhecido.
SEM_RATING = "N.A."


def normalizar_rating(valor: object) -> str:
    """Limpeza mínima antes do lookup: `None` vira `""`, tira espaços das
    pontas e colapsa espaço interno.

    De propósito NÃO faz case-folding nem remove pontuação: o MATCH do
    Excel é case sensitive e a diferença entre `AA-(bra)` e `AA(bra)` é
    um caractere só. Normalizar demais aqui transformaria erro de dado
    (que a gente quer VER, via `ratings_desconhecidos`) em rating
    silenciosamente errado.
    """
    if valor is None:
        return ""
    return " ".join(str(valor).split())


def peso_de(agencia: str, valor: object) -> int | None:
    """Peso 1-22 de um rating na tabela da agência, ou `None` se for
    vazio/não reconhecido.

    `None` é deliberadamente ambíguo entre "não tem rating" e "rating não
    reconhecido" pro cálculo da média (os dois casos simplesmente não
    entram), mas quem quiser distinguir tem `eh_vazio()` e
    `ratings_desconhecidos()`.
    """
    tabela = _TABELAS.get(agencia)
    if tabela is None:
        raise ValueError(f"agência desconhecida: {agencia!r} -- use uma de {AGENCIAS}")
    return tabela.get(normalizar_rating(valor))


def eh_vazio(valor: object) -> bool:
    """True se o valor é uma marca de "sem rating" (`-`, vazio, `N.A.`...),
    em vez de um rating de verdade escrito errado."""
    return normalizar_rating(valor).lower() in _VAZIOS


def ratings_desconhecidos(fitch=None, sp=None, moodys=None) -> dict[str, str]:
    """Ratings que NÃO são vazio e mesmo assim não casaram na tabela —
    ou seja, dado provavelmente errado/novo, não ausência de rating.

    Existe pra alimentar a tela de Administração: um rating que a Fitch
    passe a publicar num formato novo sumiria da média silenciosamente
    (viraria `None` e a média seria calculada só com as outras agências,
    sem nenhum sinal). Aqui ele aparece.
    """
    fora: dict[str, str] = {}
    for agencia, valor in (("fitch", fitch), ("sp", sp), ("moodys", moodys)):
        texto = normalizar_rating(valor)
        if texto and not eh_vazio(texto) and peso_de(agencia, texto) is None:
            fora[agencia] = texto
    return fora


def _round_half_up(soma: int, n: int) -> int:
    """Arredondamento "half up" do Excel, em aritmética inteira.

    O `round()` do Python usa banker's rounding (`round(2.5) == 2`), o
    Excel não (`ROUND(2,5;0) == 3`) -- usar o nativo faria todo emissor
    com duas agências em notches adjacentes cair pro rating MELHOR em vez
    do pior em metade dos casos.

    Feito com inteiros (`(2*soma + n) // (2*n)`) em vez do
    `int(media + 0.5)` do script original de propósito: `int(x + 0.5)` em
    float pode errar na borda quando a média não é exatamente
    representável em binário (com 3 agências a média é soma/3, dízima em
    base 2). Aqui é exato por construção.
    """
    return (2 * soma + n) // (2 * n)


def calcular_rating_medio(fitch=None, sp=None, moodys=None) -> dict:
    """Rating médio de um emissor a partir dos ratings das três agências.

    Devolve um dict (não uma tupla como o script original) porque quem
    chama quase sempre quer só um dos campos, e posicional de 4 elementos
    fica ilegível no call site:

        {
          "rating": "AA-",      # escala padrão, ou "N.A."
          "peso": 4,            # 1-22 (útil pra ORDENAR/plotar), ou None
          "media": 3.5,         # média crua antes do arredondamento
          "n_agencias": 2,      # quantas entraram na conta
          "pesos": {"fitch": 3, "sp": 4},   # só as que entraram
          "desconhecidos": {},  # ver ratings_desconhecidos()
        }

    `peso` é o que permite "spread médio por rating" ordenar AAA→D
    corretamente; ordenar pela string daria ordem alfabética (A antes de
    AA antes de AAA), que é errada.
    """
    pesos: dict[str, int] = {}
    for agencia, valor in (("fitch", fitch), ("sp", sp), ("moodys", moodys)):
        p = peso_de(agencia, valor)
        if p is not None:
            pesos[agencia] = p

    desconhecidos = ratings_desconhecidos(fitch, sp, moodys)

    if not pesos:
        return {
            "rating": SEM_RATING,
            "peso": None,
            "media": None,
            "n_agencias": 0,
            "pesos": {},
            "desconhecidos": desconhecidos,
        }

    soma = sum(pesos.values())
    n = len(pesos)
    peso_final = _round_half_up(soma, n)

    return {
        "rating": PESO_TO_PADRAO.get(peso_final, SEM_RATING),
        "peso": peso_final,
        "media": soma / n,
        "n_agencias": n,
        "pesos": pesos,
        "desconhecidos": desconhecidos,
    }


def rating_medio(fitch=None, sp=None, moodys=None) -> str:
    """Atalho pro caso mais comum: só a string do rating médio."""
    return calcular_rating_medio(fitch, sp, moodys)["rating"]


def ordenar_ratings(valores) -> list[str]:
    """Ratings da escala padrão em ordem de risco (AAA primeiro, N.A. por
    último). Usado pra ordenar eixo de gráfico e coluna de tabela —
    ordenar string cru daria ordem alfabética, que é errada."""
    unicos = {normalizar_rating(v) or SEM_RATING for v in valores}
    return sorted(unicos, key=lambda r: PADRAO_TO_PESO.get(r, 99))
