"""Casamento de keywords (empresas + termos de setor) contra título/snippet.

Normaliza acentuação e caixa (mesma estratégia do clipinator) para evitar
que "Vale" e "VALE" sejam tratadas como termos diferentes, e usa \b (limite
de palavra) para reduzir falso positivo.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize(text: str) -> str:
    return _strip_accents(text).lower()


@lru_cache(maxsize=512)
def _compiled_pattern(keywords: tuple[str, ...]) -> re.Pattern | None:
    terms = [re.escape(normalize(k)) for k in keywords if k and k != "*"]
    if not terms:
        return None
    # ordena por tamanho decrescente para termos compostos terem prioridade
    terms.sort(key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(terms) + r")\b", re.IGNORECASE)


def match_keywords(
    text: str, keywords: list[str], keywords_sem_checagem_maiuscula: set[str] | None = None
) -> list[str]:
    """Retorna a sublista de `keywords` que aparece em `text` (normalizado).

    Por padrão, exige que a ocorrência comece com maiúscula no texto
    ORIGINAL (não normalizado) para contar como casamento -- nomes de
    empresa são substantivos próprios, então isso evita que uma palavra
    comum do português que por coincidência é igual ao nome de uma empresa
    conte como menção. Caso real que motivou isso (17/07/2026): a notícia
    "Flávio incentivou tarifaço... 'a medida foi... movida por razões
    políticas'..." não fala da empresa Movida em lugar nenhum, mas "movida"
    (particípio do verbo mover, minúsculo) casava com o nome da empresa
    "Movida" e ficava marcada errado. Texto em CAIXA ALTA (manchetes, nomes
    de empresa no CVM RAD) continua batendo normalmente -- só minúscula
    pura é rejeitada.

    `keywords_sem_checagem_maiuscula` (normalizados via `normalize()`) é
    para termos de SETOR (ex.: "varejo", "crédito privado") -- esses não
    são nomes próprios, é normal aparecerem em minúscula no meio de uma
    frase, então ficam isentos dessa checagem."""
    if not text or not keywords:
        return []
    pattern = _compiled_pattern(tuple(keywords))
    if pattern is None:
        return []
    isentos = keywords_sem_checagem_maiuscula or set()
    norm_text = normalize(text)
    # normalize() faz strip de acento + lower, o que preserva o comprimento
    # do texto pra quase todo caractere em português real (á/ã/ç etc viram
    # 1 char). Só ativa a checagem de capitalização se os tamanhos baterem
    # -- caso contrário (unicode incomum) cai no comportamento antigo em
    # vez de arriscar checar a posição errada.
    pode_checar_capitalizacao = len(norm_text) == len(text)

    found = set()
    for m in pattern.finditer(norm_text):
        termo = m.group(0)
        if pode_checar_capitalizacao and termo not in isentos:
            ch = text[m.start()]
            if ch.isalpha() and not ch.isupper():
                continue
        found.add(termo)
    if not found:
        return []
    # mapeia de volta para o keyword original (mantendo grafia cadastrada)
    norm_to_original = {normalize(k): k for k in keywords if k and k != "*"}
    return sorted({norm_to_original.get(f, f) for f in found})


def matches_any(text: str, keywords: list[str]) -> bool:
    return bool(match_keywords(text, keywords))
