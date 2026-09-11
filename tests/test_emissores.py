"""A tabela de dívidas da aba Emissores: agrupada, totalizada e barata.

PEDIDO DO ALLAN (09 e 11/09/2026). Na tabela que mostra todas as dívidas a
mercado dos emissores selecionados: separar por grupo de indexador "para
ficar as semelhantes juntas", com totalizador de estoque por grupo, e trazer
a duration do papel hoje.

E um N+1 que estava lá desde 24/07/2026: a função fazia uma consulta por
ticker para achar o último spread. Num emissor com dezenas de papéis, eram
dezenas de idas e voltas para desenhar uma tabela.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Debenture, DebentureSpread
from app.spreads import queries


class _Espia:
    """Conta o SQL que sai pelo engine da sessão."""

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


def _base(n_ipca: int = 3, n_cdi: int = 2) -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    for i in range(n_ipca):
        s.add(Debenture(codigo=f"IPCA{i}", nome="ENERGISA S/A", indexador="IPCA",
                        classe="IPCA + Incentivadas", incentivada="S",
                        data_emissao=date(2024, 6, 15), indice_emissao="IPCA",
                        percentual_emissao=None, taxa_emissao=6.8123))
        # dois dias de historico: o mais recente e o que deve aparecer
        s.add(DebentureSpread(codigo=f"IPCA{i}", data=date(2026, 9, 8),
                              estoque=100.0, taxa_indicativa=7.0, duration=3.0, spread=40.0))
        s.add(DebentureSpread(codigo=f"IPCA{i}", data=date(2026, 9, 9),
                              estoque=200.0, taxa_indicativa=7.5, duration=4.5, spread=45.0))
    for i in range(n_cdi):
        s.add(Debenture(codigo=f"CDI{i}", nome="ENERGISA S/A", indexador="DI +",
                        classe="CDI + Tradicionais", incentivada="N",
                        data_emissao=date(2023, 2, 1), indice_emissao="DI",
                        percentual_emissao=100.0, taxa_emissao=1.75))
        s.add(DebentureSpread(codigo=f"CDI{i}", data=date(2026, 9, 9),
                              estoque=50.0, taxa_indicativa=1.9, duration=2.0, spread=190.0))
    s.commit()
    return s


def test_agrupa_por_classe_e_totaliza_por_grupo():
    db = _base()
    r = queries.emissor_tickers(db, ["ENERGISA S/A"])
    por_classe = {t["classe"]: t for t in r["totais"]}
    assert por_classe["IPCA + Incentivadas"]["estoque"] == pytest.approx(600.0)
    assert por_classe["IPCA + Incentivadas"]["n_ativos"] == 3
    assert por_classe["CDI + Tradicionais"]["estoque"] == pytest.approx(100.0)
    assert por_classe["CDI + Tradicionais"]["n_ativos"] == 2


def test_nao_soma_estoque_entre_classes():
    """IPCA+ e CDI+ usam referências diferentes (NTN-B e DI). Um total único
    misturaria as duas e não significaria nada."""
    db = _base()
    r = queries.emissor_tickers(db, ["ENERGISA S/A"])
    assert len(r["totais"]) == 2, "tem que haver um total por classe, não um geral"
    assert all("classe" in t for t in r["totais"])


def test_linhas_saem_agrupadas_e_nao_intercaladas():
    """Sem ordenação por classe, o navegador desenharia IPCA e CDI misturados
    e o agrupamento visual não teria onde se apoiar."""
    db = _base()
    classes = [t["classe"] for t in queries.emissor_tickers(db, ["ENERGISA S/A"])["tickers"]]
    assert classes == sorted(classes), f"linhas intercaladas: {classes}"


def test_traz_duration_e_estoque_do_ultimo_dia_publicado():
    db = _base()
    # por codigo, nao pela posicao: a ordenacao e por CLASSE primeiro, e
    # "CDI + Tradicionais" vem antes de "IPCA + Incentivadas"
    linha = {t["codigo"]: t for t in queries.emissor_tickers(db, ["ENERGISA S/A"])["tickers"]}["IPCA0"]
    assert linha["duration"] == pytest.approx(4.5), "pegou o dia 08, não o 09"
    assert linha["estoque"] == pytest.approx(200.0)
    assert linha["data_estoque"] == "2026-09-09"


def test_a_coluna_de_taxa_e_a_da_emissao_nao_a_indicativa():
    """Pedido do Allan (11/09/2026). Numa tabela de DÍVIDA do emissor, a
    taxa que descreve o papel é a contratada na emissão -- fixa pela vida
    toda -- e não o preço de hoje no secundário, que muda todo dia e já
    está nos cards e no gráfico da mesma aba."""
    db = _base()
    por_codigo = {t["codigo"]: t for t in queries.emissor_tickers(db, ["ENERGISA S/A"])["tickers"]}
    assert por_codigo["IPCA0"]["taxa_emissao"] == "IPCA + 6,8123%"
    assert por_codigo["CDI0"]["taxa_emissao"] == "CDI + 1,75%"
    assert "taxa_indicativa" not in por_codigo["IPCA0"], (
        "a taxa indicativa saiu desta tabela -- deixá-la no JSON convida a "
        "voltar para a tela sem querer"
    )


def test_traz_a_data_de_emissao_do_cadastro():
    db = _base()
    por_codigo = {t["codigo"]: t for t in queries.emissor_tickers(db, ["ENERGISA S/A"])["tickers"]}
    assert por_codigo["IPCA0"]["data_emissao"] == "2024-06-15"
    assert por_codigo["CDI0"]["data_emissao"] == "2023-02-01"


def test_papel_sem_caracteristicas_capturadas_nao_quebra_a_tabela():
    """Papel que entrou no cadastro antes de 11/09/2026 fica sem as colunas
    de emissão até a próxima rodada de captura -- tem que sair com `None`,
    não com exceção."""
    db = _base(n_ipca=0, n_cdi=0)
    db.add(Debenture(codigo="ANTIGO", nome="ENERGISA S/A", indexador="IPCA",
                     classe="IPCA + Incentivadas", incentivada="S"))
    db.commit()
    linha = queries.emissor_tickers(db, ["ENERGISA S/A"])["tickers"][0]
    assert linha["taxa_emissao"] is None
    assert linha["data_emissao"] is None


def test_cada_ticker_usa_a_propria_ultima_data():
    """Um papel que ficou sem publicação num pregão traz o dia anterior --
    não pode sumir da tabela nem herdar a data de outro."""
    db = _base(n_ipca=1, n_cdi=0)
    db.add(Debenture(codigo="ATRASADO", nome="ENERGISA S/A", indexador="IPCA",
                     classe="IPCA + Incentivadas", incentivada="S"))
    db.add(DebentureSpread(codigo="ATRASADO", data=date(2026, 9, 2),
                           estoque=10.0, taxa_indicativa=6.0, duration=1.0, spread=30.0))
    db.commit()
    por_codigo = {t["codigo"]: t for t in queries.emissor_tickers(db, ["ENERGISA S/A"])["tickers"]}
    assert por_codigo["ATRASADO"]["data_estoque"] == "2026-09-02"
    assert por_codigo["IPCA0"]["data_estoque"] == "2026-09-09"


def test_lei_12431_vem_do_cadastro():
    db = _base()
    por_codigo = {t["codigo"]: t for t in queries.emissor_tickers(db, ["ENERGISA S/A"])["tickers"]}
    assert por_codigo["IPCA0"]["incentivada"] == "S"
    assert por_codigo["CDI0"]["incentivada"] == "N"


def test_papel_sem_nenhum_spread_aparece_com_campos_vazios():
    """Com `debentures` virando o universo do mercado, papel sem
    precificação da ANBIMA passa a ser comum -- não pode sumir da tabela."""
    db = _base(n_ipca=1, n_cdi=0)
    db.add(Debenture(codigo="SEMPRECO", nome="ENERGISA S/A", indexador="IPCA",
                     classe="IPCA + Incentivadas", incentivada="S"))
    db.commit()
    por_codigo = {t["codigo"]: t for t in queries.emissor_tickers(db, ["ENERGISA S/A"])["tickers"]}
    assert "SEMPRECO" in por_codigo
    assert por_codigo["SEMPRECO"]["estoque"] is None
    assert por_codigo["SEMPRECO"]["duration"] is None


def test_o_custo_nao_cresce_com_o_numero_de_tickers():
    """O teste que existe por causa do N+1. Se alguém voltar a buscar o
    último spread dentro de um laço, o número de consultas passa a seguir o
    número de papéis e este teste acusa."""
    poucos = _base(n_ipca=2, n_cdi=1)
    with _Espia(poucos) as e1:
        queries.emissor_tickers(poucos, ["ENERGISA S/A"])

    muitos = _base(n_ipca=30, n_cdi=20)
    with _Espia(muitos) as e2:
        queries.emissor_tickers(muitos, ["ENERGISA S/A"])

    assert e2.n == e1.n, (
        f"3 papéis custaram {e1.n} consultas e 50 custaram {e2.n} -- "
        "o custo voltou a seguir o tamanho do emissor"
    )
    assert e2.n <= 3, f"{e2.n} consultas para montar uma tabela é demais"
