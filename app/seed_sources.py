"""Sincroniza app.config.KNOWN_SOURCES com a tabela `sources` do banco.

Usado tanto pelo seed manual (scripts/seed.py, roda do computador local)
quanto pela rodada automática do GitHub Actions (scripts/run_once.py) --
assim, uma fonte nova adicionada no config.py aparece sozinha na próxima
execução agendada, sem precisar rodar o seed manualmente contra o Supabase
toda vez que uma fonte é adicionada (17/07/2026, motivado pelo pedido do
Allan de cadastrar a fonte do Banco Central)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import config
from .models import Source

SYNC_FIELDS = ("url", "scraper_module", "category", "kind", "notes")


def sync_known_sources(db: Session) -> tuple[int, int]:
    """Cria fontes novas e atualiza os campos "donos do código" das que já
    existem (url, scraper, categoria, tipo, notas). NUNCA mexe em
    'enabled' -- isso é controlado pelo usuário na aba Fontes & Empresas, e
    sobrescrever aqui reverteria a escolha dele a cada execução. Retorna
    (quantidade nova, quantidade atualizada)."""
    existing = {s.name: s for s in db.query(Source).all()}
    n_new = n_synced = 0
    for src in config.KNOWN_SOURCES:
        current = existing.get(src["name"])
        if current is None:
            db.add(Source(**src))
            n_new += 1
            continue
        changed = False
        for field in SYNC_FIELDS:
            novo_valor = src.get(field)
            if novo_valor is not None and getattr(current, field) != novo_valor:
                setattr(current, field, novo_valor)
                changed = True
        if changed:
            n_synced += 1
    db.commit()
    return n_new, n_synced
