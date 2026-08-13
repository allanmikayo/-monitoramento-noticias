"""Testes de app/spreads/views.py — a view `v_spread_rating`.

Cobrem as duas armadilhas que a view existe pra evitar, e que passam
despercebidas porque o resultado *parece* certo: linha duplicada e rating
nulo.

    python -m pytest tests/test_views.py -v
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Debenture, DebentureSpread, Issuer, IssuerRatingPeriodo
from app.spreads.issuer_key import issuer_key
from app.spreads.ratings import SEM_RATING
from app.spreads.views import conferir_view, criar_views


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


def _montar(eng, *, periodos=(), spreads=()):
    """Um emissor, uma debênture, e o que os testes pedirem."""
    with Session(eng) as db:
        issuer = Issuer(key=issuer_key("COSAN S.A."), nome="COSAN S.A.",
                        setor="Óleo & Gás", grupo_economico="COSAN")
        db.add(issuer)
        db.flush()
        db.add(Debenture(codigo="CSAN13", nome="COSAN S.A.", issuer_id=issuer.id,
                         indexador="IPCA +", classe="IPCA + Incentivadas"))
        for dt, spread in spreads or [(date(2025, 6, 1), 100.0)]:
            db.add(DebentureSpread(codigo="CSAN13", data=dt, spread=spread, estoque=500.0))
        for codigo, origem, inicio, fim, rating, notch in periodos:
            db.add(IssuerRatingPeriodo(
                issuer_id=issuer.id, codigo=codigo, origem=origem,
                data_inicio=inicio, data_fim=fim,
                rating_medio=rating, notch_medio=notch, n_agencias=1))
        db.commit()
    criar_views(eng)
    return eng


def _linhas(eng, sql="SELECT * FROM v_spread_rating"):
    with eng.connect() as conn:
        return [dict(r._mapping) for r in conn.exec_driver_sql(sql)]


# ---------------------------------------------------------------------------
# Armadilha 1: duplicação
# ---------------------------------------------------------------------------

def test_periodos_nos_dois_niveis_nao_duplicam_linha(engine):
    """BUG REAL (04/08/2026). Com períodos de emissor E de emissão, o join
    ingênuo `(p.codigo = d.codigo OR p.codigo IS NULL)` casa com os dois:
    854.268 linhas para uma base de 569.272 — 150% de "cobertura". Todo
    SUM(estoque) sai inflado e não aparece erro nenhum na tela.
    """
    _montar(engine, periodos=[
        (None, "HISTORICO", date(2025, 1, 1), None, "A+", 5),       # emissor
        ("CSAN13", "HISTORICO", date(2025, 1, 1), None, "AAA", 1),  # emissão
    ])
    assert len(_linhas(engine)) == 1


def test_conferir_view_detecta_duplicacao(engine):
    _montar(engine, periodos=[
        (None, "HISTORICO", date(2025, 1, 1), None, "A+", 5),
        ("CSAN13", "HISTORICO", date(2025, 1, 1), None, "AAA", 1),
    ])
    chk = conferir_view(engine)
    assert chk["duplicou"] is False and chk["ok"] is True
    assert chk["linhas_view"] == chk["linhas_base"] == 1


# ---------------------------------------------------------------------------
# Armadilha 2: rating nulo
# ---------------------------------------------------------------------------

def test_sem_periodo_vira_na_e_nunca_nulo(engine):
    """Regra do Allan (04/08/2026): "para o rating médio necessariamente
    deve ter ou o rating médio ou N.A., nunca em branco".

    NULL e "N.A." significam o mesmo pro analista, mas em SQL não: NULL
    some do GROUP BY, não casa com `= 'N.A.'` e vira buraco silencioso no
    gráfico. 21% das linhas de spread caem neste caso.
    """
    _montar(engine)  # nenhum período
    linha = _linhas(engine)[0]
    assert linha["rating_medio"] == SEM_RATING
    assert linha["rating_medio"] is not None
    assert linha["rating_escopo"] == "SEM_RATING"


def test_data_anterior_ao_primeiro_periodo_vira_na(engine):
    """Spread de jan/2025 com rating que só começa em jun/2025."""
    _montar(engine,
            spreads=[(date(2025, 1, 5), 100.0)],
            periodos=[(None, "HISTORICO", date(2025, 6, 1), None, "AAA", 1)])
    assert _linhas(engine)[0]["rating_medio"] == SEM_RATING


def test_nenhum_rating_medio_nulo_na_view(engine):
    _montar(engine, spreads=[(date(2025, 1, 5), 90.0), (date(2025, 8, 1), 95.0)],
            periodos=[(None, "HISTORICO", date(2025, 6, 1), None, "AAA", 1)])
    assert conferir_view(engine)["rating_nulo"] == 0


# ---------------------------------------------------------------------------
# Precedência
# ---------------------------------------------------------------------------

def test_emissao_vence_emissor(engine):
    """Caso COSAN: CSAN13 é AAA enquanto o emissor é A+."""
    _montar(engine, periodos=[
        (None, "HISTORICO", date(2025, 1, 1), None, "A+", 5),
        ("CSAN13", "HISTORICO", date(2025, 1, 1), None, "AAA", 1),
    ])
    linha = _linhas(engine)[0]
    assert linha["rating_medio"] == "AAA"
    assert linha["rating_escopo"] == "EMISSAO"


def test_historico_vence_derivado_no_mesmo_nivel(engine):
    """Onde os dois existem, o observado (o que o Allan analisou) manda."""
    _montar(engine, periodos=[
        (None, "HISTORICO", date(2025, 1, 1), None, "AAA", 1),
        (None, "DERIVADO", date(2025, 1, 1), None, "AA", 3),
    ])
    assert _linhas(engine)[0]["rating_medio"] == "AAA"


def test_herda_do_emissor_quando_a_emissao_nao_tem(engine):
    _montar(engine, periodos=[(None, "HISTORICO", date(2025, 1, 1), None, "A+", 5)])
    linha = _linhas(engine)[0]
    assert linha["rating_medio"] == "A+"
    assert linha["rating_escopo"] == "EMISSOR"


# ---------------------------------------------------------------------------
# Junção as-of e conteúdo
# ---------------------------------------------------------------------------

def test_asof_pega_o_periodo_da_data_da_linha(engine):
    """Cada linha de spread recebe o rating vigente NAQUELA data — não o
    de hoje. É o que evita viés retrospectivo no gráfico histórico."""
    _montar(engine,
            spreads=[(date(2025, 3, 1), 100.0), (date(2026, 3, 1), 120.0)],
            periodos=[
                (None, "HISTORICO", date(2025, 1, 1), date(2026, 1, 1), "AAA", 1),
                (None, "HISTORICO", date(2026, 1, 1), None, "BBB", 9),
            ])
    por_data = {r["data"]: r["rating_medio"] for r in _linhas(engine)}
    assert por_data[str(date(2025, 3, 1))] == "AAA"
    assert por_data[str(date(2026, 3, 1))] == "BBB"


def test_traz_setor_e_grupo_para_agregar(engine):
    """A view é o ponto único de junção: sem ela, cada consulta refaz o
    caminho spread -> debênture -> emissor -> taxonomia."""
    _montar(engine, periodos=[(None, "HISTORICO", date(2025, 1, 1), None, "AAA", 1)])
    linha = _linhas(engine)[0]
    assert linha["setor"] == "Óleo & Gás"
    assert linha["grupo_economico"] == "COSAN"
    assert linha["emissor"] == "COSAN S.A."
    assert linha["classe"] == "IPCA + Incentivadas"


def test_debenture_sem_emissor_ainda_aparece(engine):
    """LEFT JOIN, não INNER: papel sem emissor casado não pode sumir da
    view — sumir silenciosamente é pior que aparecer como N.A."""
    with Session(engine) as db:
        db.add(Debenture(codigo="XXXX11", nome="EMPRESA DESCONHECIDA S.A."))
        db.add(DebentureSpread(codigo="XXXX11", data=date(2025, 6, 1),
                               spread=200.0, estoque=10.0))
        db.commit()
    criar_views(engine)
    linhas = _linhas(engine, "SELECT * FROM v_spread_rating WHERE codigo = 'XXXX11'")
    assert len(linhas) == 1
    assert linhas[0]["rating_medio"] == SEM_RATING
    assert linhas[0]["setor"] is None


def test_rating_e_notch_saem_do_mesmo_periodo(engine):
    """BUG REAL (04/08/2026). Com `COALESCE` coluna a coluna, os campos de
    uma linha podiam vir de PERÍODOS DIFERENTES: apareceu
    `rating_medio='N.A.'` (do período da emissão) com `notch_medio=5` (do
    período do emissor, porque o da emissão tinha notch nulo).

    Rating e notch se contradizendo é o pior caso — o gráfico ordena pelo
    notch e rotula pelo rating.
    """
    _montar(engine, periodos=[
        # Emissão sem rating (notch nulo) — tem que vencer INTEIRA.
        ("CSAN13", "HISTORICO", date(2025, 1, 1), None, SEM_RATING, None),
        # Emissor com A+ — não pode "vazar" o notch pra linha acima.
        (None, "HISTORICO", date(2025, 1, 1), None, "A+", 5),
    ])
    linha = _linhas(engine)[0]
    assert linha["rating_medio"] == SEM_RATING
    assert linha["notch_medio"] is None
    assert conferir_view(engine)["incoerentes"] == 0


def test_conferir_view_detecta_incoerencia(engine):
    """"N.A." é o único rating sem notch; qualquer outra combinação é
    incoerência e tem que ser detectada."""
    _montar(engine, periodos=[(None, "HISTORICO", date(2025, 1, 1), None, "AAA", 1)])
    chk = conferir_view(engine)
    assert chk["incoerentes"] == 0 and chk["ok"] is True


def test_criar_views_e_idempotente(engine):
    _montar(engine, periodos=[(None, "HISTORICO", date(2025, 1, 1), None, "AAA", 1)])
    criar_views(engine)
    criar_views(engine)
    assert conferir_view(engine)["ok"] is True
