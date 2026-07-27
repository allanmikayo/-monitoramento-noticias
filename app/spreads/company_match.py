"""Heurística de nome pra ligar `Debenture.nome` (emissor, vindo da Anbima)
a `Company` (cadastro do monitoramento de notícias, `/fontes`) — usado por
`scripts/match_debenture_issuers.py`. NÃO roda automaticamente no pipeline
diário: é uma revisão manual (roda uma vez, Allan confere o relatório e
ajusta nomes/aliases em `/fontes` se precisar, depois roda de novo com
`--apply`). Pedido do Allan (24/07/2026) pra aba "Marcação Emissores" —
liga cada emissor a uma empresa da cobertura pra puxar notícias dela.

NÃO é fuzzy matching de verdade (sem biblioteca externa) — normaliza os
dois lados (remove acento, maiúscula, pontuação, sufixos societários tipo
"S/A"/"LTDA"/"PARTICIPAÇÕES") e casa por igualdade ou por CONTENÇÃO de
token (todo token do nome mais curto aparece no nome mais longo).
Deliberadamente conservador: prefere deixar SEM match (Allan resolve
manual) a criar um match errado que grudaria notícias da empresa errada
numa outra."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Company, Debenture

# Sufixos/conectores societários comuns em nomes de emissor da Anbima --
# removidos antes de comparar, senão "X PARTICIPACOES S/A" nunca bate com
# o nome curto "X" cadastrado em Company.
_SUFFIXES = {
    "sa", "ltda", "holding", "holdings", "participacoes", "participacao",
    "companhia", "cia", "grupo", "brasil", "brasileira", "brasileiro",
    "do", "da", "de", "dos", "das", "e",
}


def _strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def normalize_name(raw: str) -> str:
    """'AEGEA SANEAMENTO E PARTICIPAÇÕES S/A (*)' -> 'aegea saneamento'."""
    s = _strip_accents(raw or "").lower()
    s = re.sub(r"\([^)]*\)", " ", s)  # defesa extra -- SpreadRow.nome já remove (*)/(**)/(#) normalmente
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    # "S/A" vira "s a" depois do regex acima -- token de 1 letra sozinho não
    # carrega sinal nenhum pra comparação (e não bate com o _SUFFIXES
    # "sa" porque já foi partido em dois); descarta em vez de deixar
    # ruído solto no conjunto de tokens.
    tokens = [t for t in s.split() if len(t) > 1 and t not in _SUFFIXES]
    return " ".join(tokens)


@dataclass
class MatchResult:
    emissor: str
    company_id: int | None
    company_name: str | None
    motivo: str  # "exato" | "contencao" | "sem_match"


def _token_containment(a: str, b: str) -> bool:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    curto, longo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(curto) == 1:
        (unico,) = curto
        if len(unico) < 6:
            return False  # token único curto demais -- risco alto de falso positivo
    return curto.issubset(longo)


def match_all(db: Session) -> list[MatchResult]:
    """Pra cada nome de emissor distinto em `Debenture.nome`, tenta achar a
    `Company` correspondente. NÃO grava nada — devolve o relatório pra
    `scripts/match_debenture_issuers.py` decidir o que persistir."""
    companies = db.query(Company).filter(Company.active.is_(True)).all()
    candidatos: list[tuple[int, str, str]] = []  # (company_id, nome_norm, nome_original)
    for c in companies:
        candidatos.append((c.id, normalize_name(c.name), c.name))
        for a in c.aliases:
            norm = normalize_name(a.alias)
            if norm:
                candidatos.append((c.id, norm, c.name))

    emissores = sorted(
        r[0] for r in db.query(Debenture.nome).filter(Debenture.nome.isnot(None)).distinct().all()
    )

    results: list[MatchResult] = []
    for emissor in emissores:
        norm = normalize_name(emissor)
        if not norm:
            results.append(MatchResult(emissor, None, None, "sem_match"))
            continue
        exatos = [c for c in candidatos if c[1] == norm]
        if exatos:
            results.append(MatchResult(emissor, exatos[0][0], exatos[0][2], "exato"))
            continue
        contidos = [c for c in candidatos if _token_containment(norm, c[1])]
        if contidos:
            results.append(MatchResult(emissor, contidos[0][0], contidos[0][2], "contencao"))
            continue
        results.append(MatchResult(emissor, None, None, "sem_match"))
    return results
