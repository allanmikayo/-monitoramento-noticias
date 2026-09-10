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
from app.models import Article, NegocioB3


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


# ---------------------------------------------------------------------------
# 4. Gravação do negócio a negócio da B3
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_b3():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _trades_falsos(n: int) -> list[dict]:
    from datetime import date

    dia = date(2026, 9, 8)
    return [{
        "trade_code": f"#{1000000 + i}",
        "data_negocio": dia,
        "instrument_type": "DEB",
        "emissor": "EMISSOR TESTE S.A.",
        "codigo": f"TEST{i % 30:02d}",
        "isin": f"BRTESTDBS{i:03d}",
        "quantidade": 10,
        "preco": 1000.0 + i,
        "volume": 10000.0 + i,
        "taxa": 8.5,
        "origem": "Pre-registro - Voice",
        "horario": "11:05:53",
        "data_liquidacao": dia,
        "situacao": "Confirmado",
    } for i in range(n)]


def test_gravacao_da_b3_vai_em_lotes(db_b3):
    """Um dia inteiro não pode virar UM comando gigante.

    FALHA REAL EM PRODUÇÃO (08/09/2026): a captura trouxe 5.606 negócios em
    9 páginas, tudo certo, e o job morreu no `commit` -- um único
    `insertmanyvalues` de 423 KB e ~16 mil parâmetros, com `RETURNING id`
    por cima, cancelado por `statement timeout`. Nada do dia foi gravado.

    O comportamento a preservar: vários comandos pequenos, sem RETURNING.
    """
    from app.spreads.persist import LOTE_NEGOCIOS_B3, save_negocios_b3

    trades = _trades_falsos(600)
    with _Espia(db_b3) as espia:
        n = save_negocios_b3(db_b3, trades)

    assert n == 600
    inserts = espia.contendo("INSERT INTO negocios_b3")
    esperado = -(-600 // LOTE_NEGOCIOS_B3)  # divisão para cima
    assert len(inserts) == esperado, f"{len(inserts)} comando(s) de INSERT: {esperado} esperado(s)"
    for sql in inserts:
        assert "RETURNING" not in sql.upper(), (
            "voltou o INSERT do ORM com RETURNING id -- ninguém usa o id gerado "
            f"e ele encarece cada gravação: {sql[:120]}"
        )


def test_gravacao_da_b3_continua_idempotente(db_b3):
    """O dedupe por `trade_code` tem que sobreviver à mudança de lote.

    A B3 devolve o dia INTEIRO a cada consulta, então gravar duas vezes o
    mesmo período é o caso normal, não a exceção.
    """
    from app.spreads.persist import save_negocios_b3

    trades = _trades_falsos(300)
    assert save_negocios_b3(db_b3, trades) == 300
    assert save_negocios_b3(db_b3, trades) == 0
    assert db_b3.query(NegocioB3).count() == 300


def test_lote_parcial_sobrevive_a_falha_no_meio(db_b3, monkeypatch):
    """Commit por lote: o que entrou antes do erro não se perde.

    É o que faz a execução seguinte continuar de onde parou, em vez de
    recomeçar o dia inteiro -- e foi por não ter isso que uma falha no
    último passo apagava horas de captura.
    """
    from app.spreads import persist

    trades = _trades_falsos(600)
    original = persist.NegocioB3.__table__.insert
    chamadas = {"n": 0}

    real_execute = db_b3.execute

    def execute_com_falha(stmt, *a, **kw):
        texto = str(stmt).upper()
        if texto.startswith("INSERT INTO NEGOCIOS_B3"):
            chamadas["n"] += 1
            if chamadas["n"] == 3:
                raise RuntimeError("timeout simulado no terceiro lote")
        return real_execute(stmt, *a, **kw)

    monkeypatch.setattr(db_b3, "execute", execute_com_falha)
    with pytest.raises(RuntimeError):
        persist.save_negocios_b3(db_b3, trades)
    monkeypatch.undo()

    gravados = db_b3.query(NegocioB3).count()
    assert gravados == 500, f"esperava os 2 primeiros lotes gravados, achei {gravados}"


# ---------------------------------------------------------------------------
# 5. Spread por setor com drill-down (09/09/2026)
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_setor():
    from datetime import date

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base
    from app.models import Debenture, DebentureSpread

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    classe = "IPCA + Incentivadas"
    hoje, antes = date(2026, 9, 8), date(2026, 9, 1)
    dados = [
        ("SANE11", "Saneamento", "Saneamento", "AEGEA", 500.0, 210.0, 180.0),
        ("SANE12", "Saneamento", "Saneamento", "IGUA", 100.0, 250.0, 245.0),
        ("ENER11", "Energia Elétrica", "Transmissão", "ISA", 800.0, 150.0, 155.0),
        ("ENER12", "Energia Elétrica", "Geração", "AES", 200.0, 190.0, 170.0),
        ("SEMTX9", None, None, None, 50.0, 300.0, 300.0),
    ]
    with Session(eng) as s:
        for cod, setor, sub, grupo, est, sp_h, sp_a in dados:
            s.add(Debenture(codigo=cod, nome=f"Papel {cod}", classe=classe,
                            setor=setor, subsetor=sub, grupo_economico=grupo))
            s.add(DebentureSpread(codigo=cod, data=hoje, spread=sp_h, estoque=est))
            s.add(DebentureSpread(codigo=cod, data=antes, spread=sp_a, estoque=est))
        s.commit()
        yield s


def test_setor_pondera_por_estoque_nao_media_simples(db_setor):
    """Metodologia do relatório semanal: papel com estoque maior pesa mais.

    Saneamento tem 210 bps com 500 de estoque e 250 com 100. A média simples
    daria 230; a ponderada dá 216,7. Se este teste começar a ver 230, alguém
    trocou a ponderação e os números da tela deixaram de bater com os do
    Allan.
    """
    from app.spreads import queries

    r = queries.spread_por_setor(db_setor, "IPCA + Incentivadas",
                                 dias_comparacao=1, nivel="setor")
    saneamento = next(l for l in r["linhas"] if l["rotulo"] == "Saneamento")
    assert saneamento["spread_medio"] == 216.7
    assert saneamento["estoque"] == 600.0
    assert saneamento["n_ativos"] == 2


def test_setor_ordena_por_maior_abertura(db_setor):
    from app.spreads import queries

    r = queries.spread_por_setor(db_setor, "IPCA + Incentivadas",
                                 dias_comparacao=1, nivel="setor")
    assert r["linhas"][0]["rotulo"] == "Saneamento", "a maior abertura tem que vir primeiro"
    assert r["linhas"][0]["variacao_bps"] > 0


def test_drill_down_revela_movimento_que_o_setor_esconde(db_setor):
    """É o caso de uso que justifica o drill-down.

    Energia Elétrica fecha em +0,0 bps no agregado -- parece parado. Um nível
    abaixo, Geração abriu 20 e Transmissão fechou 5: o setor estava imóvel na
    média e em movimento por dentro.
    """
    from app.spreads import queries

    setor = queries.spread_por_setor(db_setor, "IPCA + Incentivadas",
                                     dias_comparacao=1, nivel="setor")
    energia = next(l for l in setor["linhas"] if l["rotulo"] == "Energia Elétrica")
    assert energia["variacao_bps"] == 0.0

    sub = queries.spread_por_setor(db_setor, "IPCA + Incentivadas", dias_comparacao=1,
                                   nivel="subsetor", setor="Energia Elétrica")
    por_nome = {l["rotulo"]: l["variacao_bps"] for l in sub["linhas"]}
    assert por_nome == {"Geração": 20.0, "Transmissão": -5.0}


def test_drill_down_ate_o_ticker_traz_nome_e_grupo(db_setor):
    from app.spreads import queries

    r = queries.spread_por_setor(db_setor, "IPCA + Incentivadas", dias_comparacao=1,
                                 nivel="ticker", setor="Energia Elétrica", subsetor="Geração")
    assert len(r["linhas"]) == 1
    linha = r["linhas"][0]
    assert linha["rotulo"] == "ENER12"
    assert linha["grupo_economico"] == "AES"


def test_papel_sem_taxonomia_aparece_em_vez_de_sumir(db_setor):
    """Um papel omitido da soma é um erro silencioso; uma linha visível não."""
    from app.spreads import queries
    from app.spreads.queries import SEM_CLASSIFICACAO

    r = queries.spread_por_setor(db_setor, "IPCA + Incentivadas",
                                 dias_comparacao=1, nivel="setor")
    rotulos = [l["rotulo"] for l in r["linhas"]]
    assert SEM_CLASSIFICACAO in rotulos

    # e o drill-down até ele também funciona
    r = queries.spread_por_setor(db_setor, "IPCA + Incentivadas", dias_comparacao=1,
                                 nivel="ticker", setor=SEM_CLASSIFICACAO,
                                 subsetor=SEM_CLASSIFICACAO)
    assert [l["rotulo"] for l in r["linhas"]] == ["SEMTX9"]


def test_kpi_traz_estoque_total(db_setor):
    from app.spreads import queries

    k = queries.kpi_summary(db_setor, "IPCA + Incentivadas", dias_comparacao=1)
    assert k["estoque_total"] == 1650.0
    assert k["estoque_cobertura"] == 5
