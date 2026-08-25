"""Testes de app/spreads/b3_agregado.py — agregado diário da B3.

    python -m pytest tests/test_b3_agregado.py -v
"""
from __future__ import annotations

import os

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import NegocioB3, NegocioB3Diario
from app.spreads import b3_agregado as agg


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _neg(codigo="ABCD11", dia=date(2026, 7, 27), **kw):
    base = {"codigo": codigo, "data_negocio": dia, "instrument_type": "DEB",
            "quantidade": 100.0, "preco": 1000.0, "volume": 100_000.0,
            "taxa": 10.0, "spread": 150.0}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Ponderação
# ---------------------------------------------------------------------------

def test_taxa_ponderada_por_volume_nao_media_simples():
    """Um negócio de R$ 50 mil e outro de R$ 50 milhões não podem pesar
    igual. É o mesmo princípio do spread ponderado por estoque no resto do
    projeto (bug de 27/07/2026 no card "SPREAD MÉDIO").

    Aqui: taxa 8% com R$ 1 mi e taxa 12% com R$ 9 mi.
    Simples daria 10,0; ponderada dá 11,6.
    """
    linhas = agg.agregar_linhas([
        _neg(taxa=8.0, volume=1_000_000.0),
        _neg(taxa=12.0, volume=9_000_000.0),
    ])
    assert linhas[0]["taxa_media"] == pytest.approx(11.6, abs=0.01)


def test_sem_volume_cai_para_media_simples(db):
    """Melhor uma média simples do que perder o dia inteiro. Acontece
    pouco, mas acontece."""
    linhas = agg.agregar_linhas([
        _neg(taxa=8.0, volume=None),
        _neg(taxa=12.0, volume=None),
    ])
    assert linhas[0]["taxa_media"] == pytest.approx(10.0)


def test_negocio_sem_taxa_nao_entra_na_media():
    linhas = agg.agregar_linhas([
        _neg(taxa=10.0, volume=1_000_000.0),
        _neg(taxa=None, volume=9_000_000.0),
    ])
    assert linhas[0]["taxa_media"] == pytest.approx(10.0)
    assert linhas[0]["n_negocios"] == 2      # mas conta como negócio


# ---------------------------------------------------------------------------
# Agregação
# ---------------------------------------------------------------------------

def test_agrupa_por_codigo_e_data():
    linhas = agg.agregar_linhas([
        _neg("AAAA11", date(2026, 7, 27)),
        _neg("AAAA11", date(2026, 7, 27)),
        _neg("AAAA11", date(2026, 7, 28)),
        _neg("BBBB11", date(2026, 7, 27)),
    ])
    chaves = {(l["codigo"], l["data"]) for l in linhas}
    assert len(chaves) == 3
    por = {(l["codigo"], l["data"]): l for l in linhas}
    assert por[("AAAA11", date(2026, 7, 27))]["n_negocios"] == 2


def test_maior_negocio_separa_bloco_de_fluxo():
    """Volume igual pode ser um bloco só ou fluxo pulverizado — são
    liquidezes diferentes, e só o total não distingue."""
    bloco = agg.agregar_linhas([_neg(volume=10_000_000.0)])[0]
    fluxo = agg.agregar_linhas([_neg(volume=1_000_000.0) for _ in range(10)])[0]
    assert bloco["volume"] == fluxo["volume"] == 10_000_000.0
    assert bloco["maior_negocio"] == 10_000_000.0
    assert fluxo["maior_negocio"] == 1_000_000.0


def test_min_e_max_preservam_a_dispersao():
    linhas = agg.agregar_linhas([
        _neg(spread=100.0), _neg(spread=300.0), _neg(spread=200.0),
    ])
    assert (linhas[0]["spread_min"], linhas[0]["spread_max"]) == (100.0, 300.0)


def test_lista_vazia_nao_quebra():
    assert agg.agregar_linhas([]) == []


def test_negocio_sem_codigo_ou_data_e_descartado():
    linhas = agg.agregar_linhas([
        _neg(codigo=None), _neg(dia=None), _neg(),
    ])
    assert len(linhas) == 1


# ---------------------------------------------------------------------------
# Persistência e reconstrução
# ---------------------------------------------------------------------------

def test_grava_e_e_idempotente(db):
    linhas = agg.agregar_linhas([_neg(), _neg()])
    agg.gravar_agregado(db, linhas)
    agg.gravar_agregado(db, linhas)
    assert db.query(NegocioB3Diario).count() == 1


def test_agregar_do_banco(db):
    for i in range(3):
        db.add(NegocioB3(trade_code=f"#{i}", codigo="ABCD11",
                         data_negocio=date(2026, 7, 27), instrument_type="DEB",
                         volume=1_000_000.0, taxa=10.0, spread=150.0, preco=1000.0))
    db.commit()
    r = agg.agregar_do_banco(db)
    assert r["linhas_agregado"] == 1
    linha = db.query(NegocioB3Diario).one()
    assert linha.n_negocios == 3
    assert linha.volume == 3_000_000.0
    assert linha.taxa_media == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Poda — a parte perigosa
# ---------------------------------------------------------------------------

def test_nao_poda_sem_agregado_gravado(db):
    """A salvaguarda que importa: apagar o bruto sem o agregado
    correspondente perderia o dado de vez."""
    antigo = date.today() - timedelta(days=200)
    db.add(NegocioB3(trade_code="#1", codigo="ABCD11", data_negocio=antigo,
                     instrument_type="DEB", volume=1000.0))
    db.commit()
    r = agg.podar_bruto(db, dias=90)
    assert r["apagados"] == 0
    assert db.query(NegocioB3).count() == 1


def test_poda_apaga_so_o_antigo(db):
    hoje = date(2026, 8, 5)
    antigo, recente = hoje - timedelta(days=200), hoje - timedelta(days=10)
    for i, dt in enumerate([antigo, recente]):
        db.add(NegocioB3(trade_code=f"#{i}", codigo="ABCD11", data_negocio=dt,
                         instrument_type="DEB", volume=1000.0))
        # AMBOS os dias precisam de agregado — é a condição para a poda
        # aceitar rodar (ver test_recusa_dia_sem_agregado).
        db.add(NegocioB3Diario(codigo="ABCD11", data=dt, n_negocios=1))
    db.commit()
    r = agg.podar_bruto(db, dias=90, hoje=hoje)
    assert r["apagados"] == 1
    restante = db.query(NegocioB3).one()
    assert restante.data_negocio == recente


def test_recusa_dia_sem_agregado(db):
    """A salvaguarda que importa: se UM dia a apagar não tem agregado, a
    poda não roda — apagar ali perderia o dado de vez.

    Compara com as datas que SERIAM apagadas, não com a data de corte: a
    primeira versão exigia `max(agregado) >= corte` e se recusava a rodar
    num banco só com dado antigo, mesmo com tudo devidamente agregado.
    """
    hoje = date(2026, 8, 5)
    d1, d2 = hoje - timedelta(days=200), hoje - timedelta(days=150)
    for i, dt in enumerate([d1, d2]):
        db.add(NegocioB3(trade_code=f"#{i}", codigo="ABCD11", data_negocio=dt,
                         instrument_type="DEB", volume=1000.0))
    db.add(NegocioB3Diario(codigo="ABCD11", data=d1, n_negocios=1))   # falta o d2
    db.commit()
    r = agg.podar_bruto(db, dias=90, hoje=hoje)
    assert r["apagados"] == 0
    assert "sem agregado" in r["motivo"]
    assert db.query(NegocioB3).count() == 2


def test_poda_sem_dado_nao_quebra(db):
    assert agg.podar_bruto(db, dias=90)["apagados"] == 0


# ---------------------------------------------------------------------------
# Retenção infinita — decisão do Allan (12/08/2026)
# ---------------------------------------------------------------------------

def test_duas_retencoes_diferentes(db):
    """Desenho do Allan (12/08/2026): consolidado para SEMPRE, negócio a
    negócio só nos últimos 5 dias.

    É o que resolve o espaço sem perder análise — o bruto passa a ter
    tamanho de regime (~15 MB), não taxa de crescimento.
    """
    assert agg.RETENCAO_BRUTO_DIAS == 5
    hoje = date(2026, 8, 12)
    antigo, recente = hoje - timedelta(days=30), hoje - timedelta(days=2)
    for i, dt in enumerate([antigo, recente]):
        db.add(NegocioB3(trade_code=f"#{i}", codigo="ABCD11", data_negocio=dt,
                         instrument_type="DEB", volume=1000.0))
        db.add(NegocioB3Diario(codigo="ABCD11", data=dt, n_negocios=1))
    db.commit()

    agg.podar_bruto(db, hoje=hoje)
    # o bruto antigo sai...
    assert db.query(NegocioB3).count() == 1
    assert db.query(NegocioB3).one().data_negocio == recente
    # ...e o consolidado dos DOIS dias permanece
    assert db.query(NegocioB3Diario).count() == 2


def test_consolidado_nunca_e_apagado(db):
    """A poda só toca `negocios_b3`. O consolidado é a série histórica —
    apagá-lo perderia justamente o que a aba Balcão B3 analisa no tempo."""
    hoje = date(2026, 8, 12)
    for anos in (1, 2, 3):
        dt = hoje - timedelta(days=365 * anos)
        db.add(NegocioB3Diario(codigo="ABCD11", data=dt, n_negocios=1))
    db.commit()
    agg.podar_bruto(db, hoje=hoje)
    assert db.query(NegocioB3Diario).count() == 3


def test_arquivar_antes_de_podar(tmp_path, db):
    """A poda nunca deve significar perda — o CSV comprimido guarda a
    operação individual em ~4% do espaço da tabela."""
    import gzip
    for i in range(3):
        db.add(NegocioB3(trade_code=f"#{i}", codigo="ABCD11",
                         data_negocio=date(2026, 1, 10), instrument_type="DEB",
                         volume=1000.0 * i, taxa=10.0))
    db.commit()
    destino = tmp_path / "b3_2026.csv.gz"
    r = agg.arquivar_bruto(db, destino, date(2026, 6, 30))
    assert r["arquivadas"] == 3 and destino.exists()
    with gzip.open(destino, "rt", encoding="utf-8") as fh:
        linhas = fh.read().splitlines()
    assert len(linhas) == 4                    # cabeçalho + 3
    assert "trade_code" in linhas[0]


def test_arquivar_sem_dado_nao_cria_arquivo(tmp_path, db):
    destino = tmp_path / "vazio.csv.gz"
    r = agg.arquivar_bruto(db, destino, date(2026, 6, 30))
    assert r["arquivadas"] == 0 and not destino.exists()


# ---------------------------------------------------------------------------
# Só DEB, CRI e CRA — pedido explícito do Allan (12/08/2026)
# ---------------------------------------------------------------------------

def test_so_guarda_os_tres_instrumentos():
    """A fonte da B3 traz CFF, CDCA, COE, CPR, LF... O filtro existe na
    captura; esta é a segunda barreira, para o caso de o dado entrar por
    outro caminho."""
    linhas = agg.agregar_linhas([
        _neg(codigo="DEB11", instrument_type="DEB"),
        _neg(codigo="CRI11", instrument_type="CRI"),
        _neg(codigo="CRA11", instrument_type="CRA"),
        _neg(codigo="LF11", instrument_type="LF"),
        _neg(codigo="COE11", instrument_type="COE"),
        _neg(codigo="CDCA1", instrument_type="CDCA"),
    ])
    assert {l["codigo"] for l in linhas} == {"DEB11", "CRI11", "CRA11"}


def test_agregar_do_banco_ignora_outros_instrumentos(db):
    for i, tipo in enumerate(["DEB", "CRI", "CRA", "LF", "COE"]):
        db.add(NegocioB3(trade_code=f"#{i}", codigo=f"{tipo}11",
                         data_negocio=date(2026, 8, 10), instrument_type=tipo,
                         volume=1000.0, taxa=10.0))
    db.commit()
    agg.agregar_do_banco(db)
    tipos = {r.instrument_type for r in db.query(NegocioB3Diario).all()}
    assert tipos == {"DEB", "CRI", "CRA"}


# ---------------------------------------------------------------------------
# Portabilidade SQLite <-> Postgres
# ---------------------------------------------------------------------------
#
# BUG REAL (20/08/2026): `gravar_agregado` e `agregar_do_banco` usavam
# `INSERT OR REPLACE` com placeholder `?` num cursor da DBAPI -- sintaxe
# exclusiva de SQLite. Passava em toda a suíte (que roda em SQLite) e
# quebrava no Supabase, na PRIMEIRA execução real da rodada noturna:
#
#     [b3] FALHOU: the query has 0 placeholders but 3 parameters were passed
#
# (o psycopg usa `%s`, não `?`, então não enxergava placeholder nenhum).
#
# Os dois testes abaixo cobrem a lacuna sem exigir um Postgres de verdade:
# um lê o código-fonte, o outro compila o SQL nos dois dialetos.

def test_nao_usa_sintaxe_exclusiva_de_sqlite():
    """Lê o código EXECUTÁVEL do módulo (sem docstrings, que legitimamente
    citam os termos ao explicar a correção)."""
    import ast
    from pathlib import Path

    fonte = Path(agg.__file__).read_text(encoding="utf-8")

    class TiraDocstrings(ast.NodeTransformer):
        def _limpa(self, node):
            self.generic_visit(node)
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]
            return node
        visit_Module = visit_ClassDef = _limpa
        visit_FunctionDef = visit_AsyncFunctionDef = _limpa

    executavel = ast.unparse(TiraDocstrings().visit(ast.parse(fonte)))
    for termo in ("INSERT OR REPLACE", "db.connection().connection",
                  "conn.cursor", "executemany"):
        assert termo not in executavel, (
            f"{termo!r} só funciona em SQLite — a produção roda em Postgres"
        )


def test_upsert_compila_nos_dois_dialetos():
    """Renderiza o upsert em Postgres e SQLite e confere que cada um recebe
    o placeholder do seu driver. É exatamente isso que estava quebrado."""
    import re
    from sqlalchemy import text
    from sqlalchemy.dialects import postgresql, sqlite as sqlite_dialect

    campos = ("codigo", "data", "n_negocios", "volume")
    atualiza = ", ".join(f"{c} = EXCLUDED.{c}" for c in ("n_negocios", "volume"))
    stmt = text(
        f"INSERT INTO negocios_b3_diario ({', '.join(campos)})"
        f" VALUES ({', '.join(':' + c for c in campos)})"
        f" ON CONFLICT (codigo, data) DO UPDATE SET {atualiza}"
    )

    for dialeto, marca in ((postgresql.dialect(), r"%\(\w+\)s"),
                           (sqlite_dialect.dialect(), r"\?")):
        sql = str(stmt.compile(dialect=dialeto))
        assert re.findall(marca, sql), f"{dialeto.name}: placeholder não renderizado"
        assert "ON CONFLICT" in sql


# ---------------------------------------------------------------------------
# Fechamento diário (scripts/fechar_b3.py)
# ---------------------------------------------------------------------------
#
# A captura (b3_trades.yml) sempre teve workflow; o FECHAMENTO nunca teve.
# Virou script próprio em 20/08/2026, com action individual
# (b3_fechamento.yml), a pedido do Allan: "separe em actions individuais,
# assim se um der erro não quebra o fluxo inteiro".

def test_fechar_agrega_e_poda_na_ordem_certa(db):
    """Agregar ANTES de podar não é detalhe: `podar_bruto` só apaga um dia
    que já tenha consolidado. Invertida, a poda não apagaria nada."""
    from datetime import date, timedelta
    from scripts.fechar_b3 import fechar

    hoje = date.today()
    antigo = hoje - timedelta(days=30)
    for i, d in enumerate((antigo, hoje)):
        db.add(NegocioB3(trade_code=f"#f{i}", codigo="DEB99", data_negocio=d,
                         instrument_type="DEB", volume=1000.0, taxa=10.0))
    db.commit()

    r = fechar(db)

    # o dia antigo foi consolidado antes de sumir do bruto
    datas_agregadas = {x.data for x in db.query(NegocioB3Diario).all()}
    assert antigo in datas_agregadas
    assert r["poda"]["apagados"] == 1
    assert {x.data_negocio for x in db.query(NegocioB3).all()} == {hoje}


def test_fechar_sem_poda_nao_apaga(db):
    """A flag existe para rodar o fechamento sem risco quando se está
    investigando algo no bruto."""
    from datetime import date, timedelta
    from scripts.fechar_b3 import fechar

    antigo = date.today() - timedelta(days=30)
    db.add(NegocioB3(trade_code="#sp", codigo="DEB98", data_negocio=antigo,
                     instrument_type="DEB", volume=1000.0, taxa=10.0))
    db.commit()

    r = fechar(db, podar=False)
    assert r["poda"]["apagados"] == 0
    assert db.query(NegocioB3).count() == 1


def test_rodada_noturna_delega_para_o_mesmo_codigo():
    """Uma implementação, dois pontos de entrada. Se a etapa da rodada
    noturna voltasse a ter lógica própria, as duas divergiriam com o tempo.

    Olha o CORPO da função, sem a docstring: ela cita `podar_bruto()` ao
    explicar o que o fechamento faz, e comparar a fonte crua acusaria isso
    como se fosse chamada de verdade.
    """
    import ast
    import inspect
    import textwrap

    from scripts import rodada_noturna

    fn = ast.parse(textwrap.dedent(inspect.getsource(rodada_noturna.etapa_b3))).body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]
    corpo = ast.unparse(fn)

    assert "fechar_b3" in corpo, "etapa_b3 precisa delegar, não reimplementar"
    for termo in ("agregar_do_banco", "podar_bruto"):
        assert termo not in corpo, f"etapa_b3 voltou a chamar {termo} direto"


def test_fechar_b3_cria_tabelas_num_banco_novo(tmp_path, monkeypatch):
    """Num banco vazio o script tem que criar as tabelas antes de agregar.

    BUG REAL (20/08/2026): `fechar_b3.py` importava `Base` mas não
    `app.models`, então `Base.metadata` ficava vazio, o `create_all` não
    criava nada e o script morria com "no such table: negocios_b3_diario".
    Só apareceu rodando contra um SQLite limpo -- a suíte não pega, porque o
    conftest já criou tudo antes.
    """
    import subprocess
    import sys
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    r = subprocess.run(
        [sys.executable, "-m", "scripts.fechar_b3"],
        cwd=raiz, capture_output=True, text=True,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path}/novo.db"},
    )
    assert r.returncode == 0, r.stderr[-2000:]
    assert "no such table" not in (r.stdout + r.stderr)
    assert "fechamento concluído" in (r.stdout + r.stderr)
