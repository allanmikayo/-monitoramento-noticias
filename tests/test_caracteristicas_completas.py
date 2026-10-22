"""Arquivo de características inteiro (22/10/2026) -- base da tabela da aba
Emissor: situação (Registrado/Excluído), vencimento e estoque por ativo."""
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import DebentureCaracteristica
from app.spreads.fetch import parse_caracs
from app.spreads.persist import persist_caracteristicas_completas, persist_estoque_ativos

AMOSTRA = Path(__file__).parent / "fixtures" / "caracteristicas_amostra.tsv"


def _texto():
    linhas = AMOSTRA.read_text(encoding="latin1").splitlines()
    i = next(i for i, l in enumerate(linhas) if l.startswith("Codigo do Ativo"))
    return "\n".join(linhas[i:])


def test_le_situacao_vencimento_e_empresa_da_amostra_real():
    por = {c.codigo: c for c in parse_caracs(_texto())}
    assert por["AAJR11"].situacao == "Registrado"
    assert por["AALM11"].situacao == "Excluído"
    assert por["AAJR11"].data_vencimento == date(2029, 6, 27)
    assert por["AALM12"].empresa == "AURA ALMAS MINERACAO S.A."


def test_grava_tudo_e_estoque_por_ativo():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = Session(eng)
    caracs = parse_caracs(_texto())
    assert persist_caracteristicas_completas(db, caracs) == len({c.codigo for c in caracs})
    df = pd.DataFrame({"Código": ["AAJR11", "NAOEXISTE"], "Estoque": [123456.0, 10.0]})
    assert persist_estoque_ativos(db, df, date(2026, 10, 21)) == 1
    linha = db.get(DebentureCaracteristica, "AAJR11")
    assert linha.estoque == 123.456 and linha.data_estoque == date(2026, 10, 21)
    # Regravar as características não apaga o estoque já gravado.
    persist_caracteristicas_completas(db, caracs)
    assert db.get(DebentureCaracteristica, "AAJR11").estoque == 123.456
