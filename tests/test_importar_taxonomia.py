"""A carga da taxonomia grava em lotes — e o que já passou fica gravado.

CONTEXTO (10/09/2026). A primeira versão fazia o caminho natural do ORM:
carregar todas as debêntures como objetos, mexer nos atributos, e um
`commit()` no fim. Uma transação cobrindo ~1.500 linhas. Num banco folgado
sai em meio segundo (medido: 0,43s). No banco do Allan, saturado, passou dos
5 minutos do `statement_timeout` e foi cancelada INTEIRA:

    psycopg.errors.QueryCanceled: canceling statement due to statement timeout
    [SQL: UPDATE debentures SET setor=...  WHERE debentures.codigo = ...]

"Cancelada inteira" é o problema, não a lentidão: nenhuma linha ficou
gravada, e rodar de novo recomeçava do zero — sempre esbarrando no mesmo
limite. Não era questão de idas e voltas; medi, e o ORM manda um
`executemany` só, não 1.500 comandos. O que não cabia era o TAMANHO da
transação.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Debenture
from scripts import importar_taxonomia as it


@pytest.fixture()
def sessao(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tx.db'}")
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine, future=True)
    with Sess() as db:
        db.add_all([Debenture(codigo=f"TICK{i:04d}", nome=f"Deb {i}") for i in range(50)])
        db.commit()
        yield db


def _planilha(n: int = 50) -> dict[str, dict]:
    return {
        f"TICK{i:04d}": {
            "setor": "Energia Elétrica",
            "subsetor": "Distribuição",
            "grupo_economico": "EQUATORIAL",
        }
        for i in range(n)
    }


def _com_setor(db) -> int:
    return len(db.execute(select(Debenture.codigo).where(Debenture.setor.is_not(None))).all())


def test_grava_a_taxonomia(sessao):
    r = it.importar(sessao, _planilha(), lote=10)
    assert r["atualizadas"] == 50
    assert _com_setor(sessao) == 50


def test_o_que_ja_passou_fica_gravado_quando_um_lote_morre(sessao, monkeypatch):
    """O teste que existe por causa do bug. Com uma transação só, uma falha
    no meio zerava tudo; em lotes, o trabalho feito fica feito."""
    original = it._gravar_lote
    chamadas = {"n": 0}

    def morre_no_quarto(db, pedaco):
        chamadas["n"] += 1
        if chamadas["n"] == 4:
            raise RuntimeError("canceling statement due to statement timeout")
        return original(db, pedaco)

    monkeypatch.setattr(it, "_gravar_lote", morre_no_quarto)

    with pytest.raises(RuntimeError):
        it.importar(sessao, _planilha(), lote=10)

    assert _com_setor(sessao) == 30, "os três primeiros lotes tinham que ter ficado"


def test_rodar_de_novo_continua_de_onde_parou(sessao, monkeypatch):
    original = it._gravar_lote
    chamadas = {"n": 0}

    def morre_no_quarto(db, pedaco):
        chamadas["n"] += 1
        if chamadas["n"] == 4:
            raise RuntimeError("timeout")
        return original(db, pedaco)

    monkeypatch.setattr(it, "_gravar_lote", morre_no_quarto)
    with pytest.raises(RuntimeError):
        it.importar(sessao, _planilha(), lote=10)

    monkeypatch.setattr(it, "_gravar_lote", original)
    r = it.importar(sessao, _planilha(), lote=10)
    assert r["atualizadas"] == 20, "só as que faltavam"
    assert r["inalteradas"] == 30, "as já gravadas são puladas"
    assert _com_setor(sessao) == 50


def test_rodar_com_tudo_em_dia_nao_escreve_nada(sessao):
    it.importar(sessao, _planilha(), lote=10)
    r = it.importar(sessao, _planilha(), lote=10)
    assert r["atualizadas"] == 0
    assert r["inalteradas"] == 50


def test_ticker_fora_da_planilha_conta_como_sem_classificacao(sessao):
    r = it.importar(sessao, _planilha(20), lote=10)
    assert r["atualizadas"] == 20
    assert r["sem_classificacao"] == 30
    assert r["debentures"] == 50


def test_simular_nao_grava(sessao):
    r = it.importar(sessao, _planilha(), simular=True, lote=10)
    assert r["atualizadas"] == 50, "relata o que faria"
    assert _com_setor(sessao) == 0, "e não faz"


def test_lote_tenta_de_novo_antes_de_desistir(sessao, monkeypatch):
    """Cancelamento por saturação costuma passar na segunda. Desistir na
    primeira negativa desperdiça o trabalho já feito no lote."""
    monkeypatch.setattr(it.time, "sleep", lambda _s: None)
    tentativas = {"n": 0}
    original_execute = sessao.execute

    def falha_duas_vezes(*a, **kw):
        tentativas["n"] += 1
        if tentativas["n"] <= 2:
            raise RuntimeError("canceling statement due to statement timeout")
        return original_execute(*a, **kw)

    monkeypatch.setattr(sessao, "execute", falha_duas_vezes)
    it._gravar_lote(sessao, [{"codigo": "TICK0000", "setor": "S",
                              "subsetor": "SS", "grupo_economico": "G"}])
    assert tentativas["n"] == 3, "tinha que ter insistido"
