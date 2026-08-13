"""Chave canônica de emissor — casa o mesmo emissor escrito de formas
diferentes entre as fontes.

MOTIVO (04/08/2026): ao ligar os ratings do Dashboard_Snapshot às 1.675
debêntures do banco, só **255 de 467** nomes casaram por igualdade
literal. As duas fontes são a MESMA Anbima, mas chegam por caminhos
diferentes (API oficial vs. planilha/boletim), e cada caminho trata
acento e pontuação do seu jeito:

    banco     "AEGEA SANEAMENTO E PARTICIPAÇÕES S/A"
    snapshot  "AEGEA SANEAMENTO E PARTICIPACOES S/A"

    banco     "AES CAJUINA AB1 HOLDINGS S.A."
    snapshot  "CAJUINA AB1 HOLDINGS S.A"

Sem esta normalização, ~45% dos emissores ficariam sem rating e sem setor
no dashboard — e o pior é que ficariam em silêncio, parecendo "emissor
sem rating" em vez de "falha de casamento".

ESCOPO DELIBERADAMENTE LIMITADO
-------------------------------
Isto normaliza **grafia**, não identidade. Faz: maiúsculas, remoção de
acento, remoção de pontuação, descarte de sufixo societário (S.A., LTDA,
SPE...) e colapso de espaço. NÃO faz: fuzzy matching, distância de
Levenshtein, nem inferência de grupo econômico.

Isso é de propósito. Casamento aproximado entre nomes de empresa erra
para os dois lados e erra silenciosamente — juntar "ÁGUAS DO RIO 1 SPE"
com "ÁGUAS DO RIO 4 SPE" atribuiria o rating errado a uma emissão
inteira, que é pior do que não atribuir nada. O que não casa aqui vai
para a tela de Administração para o Allan resolver na mão (uma vez por
emissor, persistido em `IssuerAlias`).
"""
from __future__ import annotations

import re
import unicodedata

# Forma societária removida ANTES da limpeza de pontuação, casando a
# expressão INTEIRA ("S.A.", "S/A", "SA", "LTDA"...).
#
# BUG PEGO NA VALIDAÇÃO (04/08/2026): a primeira versão fazia isso pelo
# caminho errado -- limpava a pontuação primeiro e depois descartava os
# tokens "S" e "A" soltos. Funciona pra "S.A.", mas come letra
# identificadora de verdade:
#
#     "AGUAS DO PARA A SPE S.A."  -> "AGUAS PARA"      (o "A" sumiu!)
#     "LOCALIZA RENT A CAR S/A"   -> "LOCALIZA RENT CAR"
#
# Duas SPE irmãs ("PARÁ A" e um eventual "PARÁ B") colidiriam na mesma
# chave e herdariam o rating uma da outra -- o erro mais caro que esse
# módulo pode cometer. Casando a forma societária como unidade, "A" e "B"
# soltos sobrevivem. Validado: 440 chaves e 26 grupos nas duas versões
# (nenhuma regressão), com os casos acima corrigidos.
_FORMA_SOCIETARIA = re.compile(
    r"(?<![A-Z0-9])(?:S\s*[./]\s*A|S\s*/\s*A|S\.A|SA|LTDA?|LIMITADA|EIRELI|EPP|ME)"
    r"(?![A-Z0-9])",
    re.IGNORECASE,
)

# Palavras descartadas por token, DEPOIS da limpeza de pontuação. Só entra
# aqui o que nunca distingue dois emissores.
#
# CUIDADO: "SPE" está aqui, mas o número/letra que costuma vir junto
# ("SPE 1", "SPE 4", "PARÁ A") NÃO é removido -- é justamente o que
# separa uma concessionária da outra. Ver
# test_issuer_key.py::test_spe_numerada_nao_colide.
_SUFIXOS = {
    "SPE",                           # sociedade de propósito específico
    "CIA", "COMPANHIA",
    "PARTICIPACOES", "PARTICIPACAO", "PART", "PARTS",
    "HOLDING", "HOLDINGS",
    "DO", "DA", "DOS", "DAS", "DE", "E",   # conectivos
}

# Prefixos de grupo que aparecem em UMA fonte e não na outra (ex.: o
# banco traz "AES CAJUINA AB1", o snapshot traz só "CAJUINA AB1"). NÃO
# são removidos por padrão -- remover prefixo de grupo é exatamente o
# tipo de heurística que junta emissores distintos do mesmo grupo. Ficam
# registrados aqui só como documentação do caso conhecido; o casamento
# desses vai por alias manual.
_PREFIXOS_DE_GRUPO_CONHECIDOS = ("AES", "CPFL", "ENEL", "NEOENERGIA", "EDP")

_NAO_ALFANUM = re.compile(r"[^A-Z0-9 ]+")
_ESPACOS = re.compile(r"\s+")


def remover_acentos(texto: str) -> str:
    """"PARTICIPAÇÕES" -> "PARTICIPACOES" (decomposição NFKD, descarta as
    marcas de combinação)."""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def issuer_key(nome: object) -> str:
    """Chave canônica de um nome de emissor.

    >>> issuer_key("AEGEA SANEAMENTO E PARTICIPAÇÕES S/A")
    'AEGEA SANEAMENTO'
    >>> issuer_key("AEGEA SANEAMENTO E PARTICIPACOES S.A.")
    'AEGEA SANEAMENTO'

    Devolve `""` para entrada vazia ou que vire só sufixo — quem chama
    trata string vazia como "não casável" e manda pra revisão manual, em
    vez de casar todos os vazios entre si (que é o bug clássico dessa
    normalização).
    """
    if nome is None:
        return ""
    texto = remover_acentos(str(nome)).upper()
    # ORDEM IMPORTA: forma societária primeiro (enquanto a pontuação ainda
    # existe pra delimitar "S.A."), limpeza de pontuação depois. Inverter
    # reintroduz o bug do "AGUAS PARA A" -- ver comentário de
    # _FORMA_SOCIETARIA.
    texto = _FORMA_SOCIETARIA.sub(" ", texto)
    texto = _NAO_ALFANUM.sub(" ", texto)
    texto = _ESPACOS.sub(" ", texto).strip()
    if not texto:
        return ""

    tokens = [t for t in texto.split(" ") if t and t not in _SUFIXOS]
    if not tokens:
        # Nome era só sufixo ("S.A.", "-", "LTDA") -- não é emissor.
        return ""
    return " ".join(tokens)


def issuer_key_com_fallback(nome: object) -> str:
    """Igual a `issuer_key`, mas se a limpeza zerar tudo devolve o nome
    normalizado sem descartar sufixo.

    Usado só na tela de Administração, pra conseguir EXIBIR algo em vez
    de uma linha em branco. Nunca usar pra casar — dois emissores cujo
    nome inteiro é sufixo casariam entre si.
    """
    chave = issuer_key(nome)
    if chave:
        return chave
    texto = remover_acentos(str(nome or "")).upper()
    return _ESPACOS.sub(" ", _NAO_ALFANUM.sub(" ", texto)).strip()
