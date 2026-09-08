"""Guardas do conserto de desempenho de 08/09/2026.

CONTEXTO. O diagnóstico do Supabase mediu, em ~55 dias de operação, um
banco de 111 MB com 100% de cache -- e mesmo assim a instância esgotada.
Não era leitura de disco: era VOLUME de operação. Os contadores de índice
mostraram a assinatura exata de um N+1 na varredura de notícias:

    articles_url_key      2.109.272 usos
    article_company_pkey  2.098.103 usos
    article_sector_pkey   1.783.325 usos

e `articles`, sem nenhum índice de tempo, com 1.840 varreduras completas e
8.982.042 linhas lidas sequencialmente.

Estes testes existem para que os quatro consertos não sejam desfeitos sem
querer. Cada um mede o COMPORTAMENTO (quantas consultas, qual SQL), não a
implementação -- é o que o próximo refator precisa preservar.

    python -m pytest tests/test_desempenho_consultas.py -v
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app import pipeline, store
from app.models import Article


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        agora = datetime.now(timezone.utc)
        for i in range(10):
            s.add(Article(
                url=f"http://exemplo.com/{i}", domain="exemplo.com",
                source_name="Fonte X", title=f"materia {i}", snippet="resumo",
                body="corpo longo " * 50,
                published_at=agora - timedelta(hours=1), found_at=agora,
                is_covered=True,
            ))
        s.commit()
        yield s


class _Espia:
    """Conta e guarda o SQL emitido no engine da sessão."""

    def __init__(self, db):
        self.engine = db.get_bind()
        self.sqls: list[str] = []

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._ouvir)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._ouvir)

    def _ouvir(self, conn, cursor, sql, params, context, executemany):
        self.sqls.append(" ".join(sql.split()))

    @property
    def n(self) -> int:
        return len(self.sqls)

    def contendo(self, termo: str) -> list[str]:
        return [s for s in self.sqls if termo.lower() in s.lower()]


# ---------------------------------------------------------------------------
# 1. N+1 da varredura
# ---------------------------------------------------------------------------

def test_prefetch_elimina_a_consulta_por_artigo(db):
    """Com o mapa em mãos, reprocessar um feed inteiro não consulta por item.

    É o caso do dia a dia: um RSS devolve os MESMOS itens a cada rodada, e
    antes cada item custava três idas ao banco (busca por URL + dois lazy
    loads de vínculo) só para concluir que já estava lá.
    """
    urls = [f"http://exemplo.com/{i}" for i in range(10)]
    mapa = store.prefetch_articles(db, urls)
    assert len(mapa) == 10

    with _Espia(db) as espia:
        for i, url in enumerate(urls):
            novo = store.upsert_article(
                db, url=url, domain="exemplo.com", source_name="Fonte X",
                article_type="news", title=f"materia {i}", snippet="resumo",
                body="corpo longo " * 50, published_at=None,
                matched_keywords=[], company_ids=[], sector_ids=[],
                existing_map=mapa,
            )
            assert novo is False
    assert espia.n == 0, f"ainda consulta por artigo: {espia.sqls[:3]}"


def test_sem_o_mapa_o_comportamento_antigo_continua(db):
    """Chamador que não passa `existing_map` não quebra -- só volta a ser N+1.

    Vale para os testes existentes e para qualquer script que use
    `upsert_article` avulso.
    """
    with _Espia(db) as espia:
        novo = store.upsert_article(
            db, url="http://exemplo.com/0", domain="exemplo.com",
            source_name="Fonte X", article_type="news", title="t",
            snippet="s", body="b", published_at=None, matched_keywords=[],
            company_ids=[], sector_ids=[],
        )
    assert novo is False
    assert espia.contendo("FROM articles"), "deveria ter buscado pela URL"


def test_prefetch_faz_uma_consulta_para_a_fonte_inteira(db):
    urls = [f"http://exemplo.com/{i}" for i in range(10)]
    with _Espia(db) as espia:
        store.prefetch_articles(db, urls)
    # 1 pelos artigos + 1 por coleção do selectinload (empresas e setores)
    assert espia.n <= 3, f"{espia.n} consultas: {espia.sqls}"


def test_url_repetida_no_mesmo_lote_nao_duplica(db):
    """Feed que lista a mesma matéria duas vezes não pode estourar a unicidade."""
    mapa = store.prefetch_articles(db, [])
    for _ in range(2):
        store.upsert_article(
            db, url="http://exemplo.com/nova", domain="exemplo.com",
            source_name="Fonte X", article_type="news", title="nova",
            snippet="s", body="b", published_at=None, matched_keywords=[],
            company_ids=[], sector_ids=[], existing_map=mapa,
        )
    db.commit()
    assert db.query(Article).filter(Article.url == "http://exemplo.com/nova").count() == 1


def test_pipeline_usa_o_prefetch():
    corpo = inspect.getsource(pipeline._run_source)
    assert "prefetch_articles" in corpo
    assert "existing_map" in corpo


# ---------------------------------------------------------------------------
# 2. Índice de tempo e peso da listagem
# ---------------------------------------------------------------------------

def test_listagem_filtra_e_ordena_pela_mesma_expressao(db):
    """Filtro e ordem têm que sair da expressão indexada, não de um OR."""
    with _Espia(db) as espia:
        store.list_articles(db, window_hours=24, coverage=["todos"])
    principal = espia.contendo("FROM articles")[0]
    assert principal.lower().count("coalesce") >= 2, principal
    assert "published_at IS NULL" not in principal, "voltou o OR que impedia o índice"


def test_listagem_nao_carrega_o_corpo_do_artigo(db):
    """`body` são 27 MB em produção e a tela nunca mostra."""
    with _Espia(db) as espia:
        artigos = store.list_articles(db, window_hours=24, coverage=["todos"])
    assert len(artigos) == 10
    principal = espia.contendo("FROM articles")[0]
    assert "articles.body" not in principal, principal
    assert "articles.title" in principal, principal


def test_o_indice_de_tempo_existe_no_modelo():
    from app.models import Article as A

    nomes = {i.name for i in A.__table__.indexes}
    assert "ix_articles_data" in nomes, (
        "sem esse índice, toda carga do dashboard varre `articles` inteira"
    )


# ---------------------------------------------------------------------------
# 3. Limpeza: fora da varredura, e sem lista de ids
# ---------------------------------------------------------------------------

def test_a_varredura_nao_limpa_mais(db):
    corpo = inspect.getsource(pipeline.run_pipeline)
    fn = ast.parse(textwrap.dedent(corpo)).body[0]
    codigo = ast.unparse(fn)
    assert "cleanup_old_articles" not in codigo, (
        "a limpeza voltou pra varredura -- ela varre `articles` inteira, "
        "96 vezes por dia; o lugar dela é scripts/faxina_diaria.py"
    )


def test_limpeza_apaga_por_subconsulta(db):
    """Sem trazer id nenhum pro Python -- era um IN com milhares de literais."""
    antigo = datetime.now(timezone.utc) - timedelta(days=90)
    db.add(Article(url="http://velho", domain="x.com", source_name="F",
                   title="velho", published_at=antigo, found_at=antigo))
    db.commit()

    with _Espia(db) as espia:
        n = store.cleanup_old_articles(db, max_age_hours=24 * 45)
    db.commit()

    assert n == 1
    assert db.query(Article).count() == 10

    deletes = espia.contendo("DELETE")
    assert len(deletes) == 3, espia.sqls
    # Os dois vínculos saem por subconsulta...
    for sql in deletes[:2]:
        assert "SELECT" in sql.upper(), f"deixou de usar subconsulta: {sql}"
    # ...e o artigo, pelo mesmo predicado de data. O que NENHUM deles pode
    # ter é a lista de ids carregada no Python, que era o problema: numa
    # limpeza acumulada virava um IN com milhares de literais.
    for sql in deletes:
        assert "IN (?, ?" not in sql, f"voltou a lista de ids literais: {sql}"
        assert "coalesce" in sql.lower() or "SELECT" in sql.upper(), sql


def test_faxina_diaria_existe_e_nao_faz_ddl():
    from scripts import faxina_diaria

    corpo = inspect.getsource(faxina_diaria)
    assert "cleanup_old_articles" in corpo
    # Nenhuma CHAMADA de DDL -- a menção em comentário é bem-vinda, é onde
    # o próximo leitor descobre por que o DDL não está aqui.
    assert "create_all(" not in corpo
    assert "ensure_schema(" not in corpo
