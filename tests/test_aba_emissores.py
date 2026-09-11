"""A aba Emissores redesenhada (11/09/2026, pedido do Allan).

O QUE MUDOU E POR QUÊ. A aba tinha um botão escolhendo UMA classe
(IPCA+Incentivadas ou CDI+Tradicionais) e a tela inteira obedecia a ele.
Isso produziu três queixas no mesmo dia, todas com a mesma raiz: com três
CPFLs selecionadas o gráfico desenhou uma linha só (as outras duas não têm
papel na classe escolhida), e não havia como comparar o IPCA+ com o CDI+ do
mesmo emissor sem trocar o filtro.

O botão saiu. As duas classes passam a aparecer lado a lado -- seis cards
(três por classe) e dois gráficos. O que NÃO mudou, e que estes testes
protegem: as duas classes nunca se somam. As referências são diferentes
(NTN-B e DI) e uma média entre elas não significaria nada.

Também cobre o filtro por grupo econômico e o bloco de notícias do setor.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Article, Company, Debenture, DebentureSpread, Sector
from app.spreads import queries


def _base() -> Session:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    # CPFL TRANSMISSAO: papéis nas DUAS classes.
    s.add(Debenture(codigo="TRAN1", nome="CPFL TRANSMISSAO", indexador="IPCA +",
                    classe="IPCA + Incentivadas", incentivada="S",
                    setor="Energia Elétrica", grupo_economico="Grupo CPFL"))
    s.add(Debenture(codigo="TRAN2", nome="CPFL TRANSMISSAO", indexador="CDI +",
                    classe="CDI + Tradicionais", incentivada="N",
                    setor="Energia Elétrica", grupo_economico="Grupo CPFL"))
    # CPFL RENOVÁVEIS: só CDI+. É o caso que deixava o gráfico com uma linha só.
    s.add(Debenture(codigo="RENO1", nome="CPFL RENOVAVEIS", indexador="CDI +",
                    classe="CDI + Tradicionais", incentivada="N",
                    setor="Energia Elétrica", grupo_economico="Grupo CPFL"))
    # Emissor de outro grupo, para o filtro ter o que NÃO selecionar.
    s.add(Debenture(codigo="AGUA1", nome="AEGEA", indexador="IPCA +",
                    classe="IPCA + Incentivadas", incentivada="S",
                    setor="Saneamento", grupo_economico="Grupo Aegea"))
    d = date(2026, 9, 10)
    s.add(DebentureSpread(codigo="TRAN1", data=d, spread=40.0, duration=5.0, estoque=100.0))
    s.add(DebentureSpread(codigo="TRAN2", data=d, spread=200.0, duration=2.0, estoque=300.0))
    s.add(DebentureSpread(codigo="RENO1", data=d, spread=240.0, duration=4.0, estoque=100.0))
    s.add(DebentureSpread(codigo="AGUA1", data=d, spread=60.0, duration=6.0, estoque=50.0))
    s.commit()
    return s


# ---------------------------------------------------------------------------
# Os seis cards
# ---------------------------------------------------------------------------

def test_devolve_as_duas_classes_sempre_na_mesma_ordem():
    """A ordem é a da tela: IPCA+ à esquerda, CDI+ à direita. Se ela
    oscilasse, os cards trocariam de lugar entre um carregamento e outro."""
    db = _base()
    r = queries.emissor_cards(db, ["CPFL TRANSMISSAO", "CPFL RENOVAVEIS"])
    assert [c["classe"] for c in r["classes"]] == [
        "IPCA + Incentivadas", "CDI + Tradicionais"]


def test_nunca_soma_as_duas_classes():
    """A regra mais antiga do dashboard: IPCA+ mede contra a NTN-B e CDI+
    contra o DI. Um total juntando os dois não significaria nada -- e agora
    que estão lado a lado na tela, a tentação de somar é maior, não menor."""
    db = _base()
    r = queries.emissor_cards(db, ["CPFL TRANSMISSAO", "CPFL RENOVAVEIS"])
    por_classe = {c["classe"]: c for c in r["classes"]}
    assert por_classe["IPCA + Incentivadas"]["estoque"] == pytest.approx(100.0)
    assert por_classe["CDI + Tradicionais"]["estoque"] == pytest.approx(400.0)
    assert "total" not in r and len(r["classes"]) == 2


def test_spread_e_duration_sao_ponderados_pelo_estoque():
    """Metodologia do relatório do Allan: média ponderada, nunca simples.
    CDI+: spread 200 com estoque 300 e 240 com estoque 100 -> 210, não 220.
    Duration: 2 com 300 e 4 com 100 -> 2,5, não 3."""
    db = _base()
    cdi = {c["classe"]: c for c in
           queries.emissor_cards(db, ["CPFL TRANSMISSAO", "CPFL RENOVAVEIS"])["classes"]
           }["CDI + Tradicionais"]
    assert cdi["spread"] == pytest.approx(210.0)
    assert cdi["duration"] == pytest.approx(2.5)
    assert cdi["duration_fallback"] is False


def test_classe_sem_papel_volta_vazia_em_vez_de_sumir():
    """O lado vazio tem que continuar na tela dizendo "—". Sumir faria
    parecer que a classe não existe, quando o que não existe é papel do
    emissor nela."""
    db = _base()
    r = queries.emissor_cards(db, ["CPFL RENOVAVEIS"])
    ipca = {c["classe"]: c for c in r["classes"]}["IPCA + Incentivadas"]
    assert ipca["n_ativos"] == 0
    assert ipca["spread"] is None and ipca["duration"] is None and ipca["estoque"] is None


def test_estoque_soma_e_nao_pondera():
    """Estoque é saldo, não taxa: soma. Os outros dois ponderam."""
    db = _base()
    ipca = {c["classe"]: c for c in queries.emissor_cards(db, ["CPFL TRANSMISSAO", "AEGEA"])["classes"]
            }["IPCA + Incentivadas"]
    assert ipca["estoque"] == pytest.approx(150.0)


def test_o_custo_nao_cresce_com_o_numero_de_tickers():
    """A versão anterior desta conta (`emissor_taxas`) fazia uma consulta
    POR TICKER para achar o último spread. Com um emissor grande isso eram
    dezenas de idas e voltas para desenhar três números."""
    from tests.test_emissores import _Espia

    db = _base()
    with _Espia(db) as e1:
        queries.emissor_cards(db, ["CPFL TRANSMISSAO"])
    with _Espia(db) as e2:
        queries.emissor_cards(db, ["CPFL TRANSMISSAO", "CPFL RENOVAVEIS", "AEGEA"])
    assert e2.n == e1.n, f"{e1.n} -> {e2.n} consultas ao triplicar os emissores"


# ---------------------------------------------------------------------------
# Grupo econômico
# ---------------------------------------------------------------------------

def test_grupo_traz_os_emissores_para_selecionar():
    """A decisão do Allan: escolher um grupo ADICIONA os emissores dele aos
    chips. Por isso a lista vem junto -- sem ela o navegador precisaria de
    uma segunda consulta só para saber o que marcar."""
    db = _base()
    por_grupo = {g["grupo"]: g for g in queries.grupos_economicos(db)}
    assert por_grupo["Grupo CPFL"]["emissores"] == ["CPFL RENOVAVEIS", "CPFL TRANSMISSAO"]
    assert por_grupo["Grupo CPFL"]["n_emissores"] == 2
    assert por_grupo["Grupo Aegea"]["n_emissores"] == 1


def test_grupo_conta_emissores_e_nao_tickers():
    """CPFL TRANSMISSAO tem dois papéis e um nome só. Contar tickers faria
    a tela prometer 3 emissores e entregar 2."""
    db = _base()
    cpfl = {g["grupo"]: g for g in queries.grupos_economicos(db)}["Grupo CPFL"]
    assert cpfl["n_emissores"] == 2


def test_ticker_sem_grupo_nao_inventa_uma_opcao_vazia():
    db = _base()
    db.add(Debenture(codigo="SEMG1", nome="SEM GRUPO", classe="IPCA + Incentivadas"))
    db.commit()
    grupos = [g["grupo"] for g in queries.grupos_economicos(db)]
    assert None not in grupos and "" not in grupos


# ---------------------------------------------------------------------------
# Notícias do setor
# ---------------------------------------------------------------------------

def _com_noticias(db: Session) -> Session:
    energia = Sector(name="Energia Elétrica")
    db.add(energia)
    db.flush()
    emp = Company(sector_id=energia.id, name="CPFL Energia")
    db.add(emp)
    db.flush()
    a1 = Article(url="http://x/1", domain="x", source_name="Valor", title="Da empresa",
                 published_at=datetime(2026, 9, 10, tzinfo=timezone.utc))
    a1.companies.append(emp)
    a1.sector_tags.append(energia)
    a2 = Article(url="http://x/2", domain="x", source_name="Valor", title="Do setor",
                 published_at=datetime(2026, 9, 9, tzinfo=timezone.utc))
    a2.sector_tags.append(energia)
    db.add_all([a1, a2])
    db.commit()
    return db


def test_setor_do_emissor_vem_da_taxonomia():
    db = _base()
    assert queries.setores_dos_emissores(db, ["CPFL TRANSMISSAO"]) == ["Energia Elétrica"]
    assert queries.setores_dos_emissores(db, []) == []


def test_noticia_do_setor_aparece_mesmo_sem_empresa_ligada():
    """A queixa concreta do Allan: painel vazio para as CPFLs. Nenhuma delas
    tem `company_id`, então o bloco do emissor fica vazio -- mas o do setor
    não precisa ficar."""
    db = _com_noticias(_base())
    noticias = queries.sector_news(db, ["Energia Elétrica"])
    assert [n["title"] for n in noticias] == ["Da empresa", "Do setor"]


def test_nao_repete_no_bloco_do_setor_o_que_ja_esta_no_do_emissor():
    db = _com_noticias(_base())
    empresas = queries.companies_for_emissores(db, ["CPFL TRANSMISSAO"])
    do_emissor = queries.company_news(db, [e["company_id"] for e in empresas.values()])
    do_setor = queries.sector_news(db, ["Energia Elétrica"],
                                   excluir_ids=[n["id"] for n in do_emissor])
    assert not ({n["id"] for n in do_emissor} & {n["id"] for n in do_setor})


def test_setor_que_so_existe_na_taxonomia_nao_quebra():
    """Os dois vocabulários de "setor" (taxonomia e cobertura editorial) não
    têm garantia de usar as mesmas palavras. Quando não batem, o resultado
    tem que ser lista vazia -- nunca exceção."""
    db = _com_noticias(_base())
    assert queries.sector_news(db, ["Saneamento"]) == []
    assert queries.sector_news(db, []) == []


# ---------------------------------------------------------------------------
# As rotas
# ---------------------------------------------------------------------------

# LIXO DE FIXTURE É BUG DE OUTRO ARQUIVO (11/09/2026). Os testes de ROTA
# precisam usar o engine do app (`app.db`), que em teste é um arquivo único
# compartilhado por toda a suíte (ver tests/conftest.py). Sem limpar no fim,
# as linhas plantadas aqui sobrevivem e entram na conta de quem rodar
# depois: foi assim que `test_curva_recupera_a_inclinacao_plantada` passou a
# falhar ao ganharmos um arquivo de teste que ordena antes dele. Limpa nos
# dois lados -- antes, porque o arquivo pode vir sujo; depois, porque o
# próximo não tem culpa.
@pytest.fixture()
def cliente_emissores():
    from fastapi.testclient import TestClient

    import app.app as A
    from app import auth
    from app.db import Base as B, SessionLocal, engine
    from app.models import User

    B.metadata.create_all(engine)
    with SessionLocal() as db:
        db.query(DebentureSpread).delete()
        db.query(Debenture).delete()
        db.add(Debenture(codigo="TRAN1", nome="CPFL TRANSMISSAO", indexador="IPCA +",
                         classe="IPCA + Incentivadas", setor="Energia Elétrica",
                         grupo_economico="Grupo CPFL"))
        db.add(DebentureSpread(codigo="TRAN1", data=date(2026, 9, 10), spread=40.0,
                               duration=5.0, estoque=100.0))
        u = db.query(User).first()
        if u is None:
            u = User(email="a@a.com", name="Teste", role="admin", active=True,
                     password_hash=auth.hash_password("x" * 10), email_confirmed=True)
            db.add(u)
        db.commit()
        token = auth.create_session(db, u, ip="1", user_agent="teste").token
        db.commit()
    c = TestClient(A.app, raise_server_exceptions=False)
    c.cookies.set("session_token", token)
    yield c
    with SessionLocal() as db:
        db.query(DebentureSpread).delete()
        db.query(Debenture).delete()
        db.commit()


@pytest.mark.parametrize("rota", [
    "/api/spreads/grupos",
    "/api/spreads/emissor/cards?nome=CPFL+TRANSMISSAO",
    "/api/spreads/emissor/noticias?nome=CPFL+TRANSMISSAO",
    "/api/spreads/emissor/negociacoes?nome=CPFL+TRANSMISSAO",
])
def test_rotas_novas_respondem(cliente_emissores, rota):
    """Rota escrita mas não registrada, ou registrada e quebrada, aparece na
    tela como bloco vazio -- sem erro visível. Foi assim que a tabela de
    tickers subiu sem nenhuma linha em 11/09/2026."""
    r = cliente_emissores.get(rota, follow_redirects=False)
    assert r.status_code == 200, r.text


def test_negociacoes_sem_classe_nao_dao_400(cliente_emissores):
    """O botão de classe saiu da tela, então esta rota passou a ser chamada
    sem `classe`. Antes isso era 400 (classe inválida) e a tabela ficava
    vazia sem dizer por quê."""
    r = cliente_emissores.get("/api/spreads/emissor/negociacoes?nome=CPFL+TRANSMISSAO")
    assert r.status_code == 200, r.text


def test_painel_de_noticias_traz_os_dois_blocos(cliente_emissores):
    r = cliente_emissores.get("/api/spreads/emissor/noticias?nome=CPFL+TRANSMISSAO")
    corpo = r.json()
    for chave in ("empresas", "noticias", "setores", "noticias_setor"):
        assert chave in corpo, f"falta '{chave}' na resposta"
    assert corpo["setores"] == ["Energia Elétrica"]


def test_setor_casa_mesmo_com_acento_ou_caixa_diferente():
    """Os dois cadastros de setor cresceram separados e ninguém garantiu que
    escrevem igual. "ENERGIA ELETRICA" na cobertura e "Energia Elétrica" na
    taxonomia são o mesmo setor para qualquer leitor -- e virariam dois num
    `IN` cru de SQL, deixando o bloco de notícias vazio sem erro na tela."""
    db = _com_noticias(_base())
    assert queries.sector_news(db, ["ENERGIA ELETRICA"])
    assert queries.sector_news(db, ["  energia  elétrica "])


def test_vocabulario_de_fato_diferente_continua_vazio():
    """A normalização conserta acento e caixa, não tradução. Se um lado diz
    "Utilities" e o outro "Energia Elétrica", o bloco vem vazio -- e é isso
    mesmo, até existir uma dimensão única de setor."""
    db = _com_noticias(_base())
    assert queries.sector_news(db, ["Utilities"]) == []


# ---------------------------------------------------------------------------
# Cache dos arquivos estáticos
# ---------------------------------------------------------------------------

def test_todo_static_do_template_tem_versao():
    """TRÊS VEZES NUM DIA (11/09/2026) nós olhamos a tela depois do deploy,
    vimos o comportamento antigo e fomos procurar defeito no código -- que
    estava certo. O navegador é que guardava o `.js`/`.css` anterior; o HTML
    vem do servidor a cada visita, os estáticos não, e o Ctrl+F5 nem sempre
    passa pela borda da Vercel.

    `?v=` na URL resolve na raiz: mudou o deploy, mudou a URL, o navegador é
    obrigado a buscar de novo. Este teste existe para a próxima referência
    a /static nascer já versionada -- esquecer é silencioso, e o sintoma
    aparece dias depois, parecendo bug de código.
    """
    import re
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent / "templates"
    sem_versao = []
    for arq in raiz.glob("*.html"):
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if not re.search(r'(src|href)="/static/', linha):
                continue
            if "?v=" not in linha:
                sem_versao.append(f"{arq.name}: {linha.strip()}")
    assert not sem_versao, "referência a /static sem ?v=:\n  " + "\n  ".join(sem_versao)


def test_versao_estatica_muda_com_o_commit(monkeypatch):
    from app import app as A

    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "abcdef1234567890")
    assert A._versao_estatica() == "abcdef123456"


def test_versao_estatica_funciona_sem_vercel(monkeypatch):
    """Rodando local não existe VERCEL_GIT_COMMIT_SHA -- cai no mtime dos
    arquivos, que é o que muda a cada salvamento durante o desenvolvimento."""
    from app import app as A

    monkeypatch.delenv("VERCEL_GIT_COMMIT_SHA", raising=False)
    v = A._versao_estatica()
    assert v and v != "0" and v.isdigit()
