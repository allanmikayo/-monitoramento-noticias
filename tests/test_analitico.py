"""Testes das análises de valor relativo da aba Spreads.

Duas famílias:

1. **Ajuste robusto** (`theil_sen`) — testado contra casos onde a resposta
   certa é conhecida de antemão, incluindo o caso que motivou trocar
   mínimos quadrados por Theil-Sen.

2. **Rotas e coerência** — as análises rodando contra um banco montado
   à mão, conferindo que os números batem com a conta feita na unha.

    python -m pytest tests/test_analitico.py -v
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.spreads.analitico import Z_REVISAO, theil_sen


# ---------------------------------------------------------------------------
# Ajuste robusto
# ---------------------------------------------------------------------------

def test_theil_sen_acha_a_reta_exata():
    """Com pontos exatamente numa reta, tem que devolver essa reta."""
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [10 + 3 * x for x in xs]
    a, b = theil_sen(xs, ys)
    assert b == pytest.approx(3.0)
    assert a == pytest.approx(10.0)


def test_theil_sen_ignora_outlier_que_o_ols_seguiria():
    """O TESTE QUE JUSTIFICA O MÓDULO USAR THEIL-SEN.

    Nove papéis numa reta de 5 bps/ano e um décimo a 900 bps (o caso
    VAMO33: AA carregando 570 bps há 400 pregões — preço real, não dado
    ruim, mas que não descreve a curva do rating).

    Mínimos quadrados persegue o outlier e devolve uma inclinação sem
    relação com os outros nove. Theil-Sen trata o ponto como um voto
    entre milhares de pares.

    O outlier fica na PONTA do eixo x (duration 9), não no meio: ponto
    exatamente sobre a média de x tem alavancagem zero e o OLS acertaria
    por acidente — o que faria este teste passar sem testar nada. Papel
    estressado de verdade também costuma estar na ponta (dívida longa é
    onde o mercado cobra o prêmio).
    """
    xs = [float(i) for i in range(1, 10)] + [9.0]
    ys = [10 + 5 * x for x in range(1, 10)] + [900.0]

    a, b = theil_sen(xs, ys)
    assert b == pytest.approx(5.0, abs=0.5), "a inclinação não pode seguir o outlier"

    # E a comparação explícita com OLS, pra o teste documentar a diferença.
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    b_ols = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    assert abs(b_ols - 5.0) > abs(b - 5.0), "OLS deveria errar mais que Theil-Sen aqui"


def test_theil_sen_com_durations_iguais_nao_divide_por_zero():
    """Emissor que emite duas séries no mesmo vencimento aparece com
    duration idêntica até a 6ª casa — não é hipotético."""
    xs = [3.0, 3.0, 3.0, 6.0]
    ys = [50.0, 55.0, 52.0, 80.0]
    a, b = theil_sen(xs, ys)
    assert b == b  # não é NaN
    assert b > 0


def test_theil_sen_com_um_ponto_so_nao_explode():
    a, b = theil_sen([2.0], [40.0])
    assert b == 0.0 and a == 40.0


def test_theil_sen_sem_pontos_nao_explode():
    a, b = theil_sen([], [])
    assert (a, b) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# Contra um banco montado à mão
# ---------------------------------------------------------------------------

@pytest.fixture()
def banco():
    """Base sintética com curva conhecida: dois ratings, inclinação de
    10 bps/ano em AAA e 20 em AA, mais um papel deslocado de propósito."""
    from app.db import Base, SessionLocal, engine
    from app.models import Debenture, DebentureSpread, Issuer, IssuerRatingPeriodo
    from app.spreads.views import criar_views

    Base.metadata.create_all(engine)
    criar_views(engine)
    hoje = date(2026, 8, 4)

    with SessionLocal() as db:
        # 2 emissores, 2 ratings, 15 papéis cada (acima de MINIMO_PARA_CURVA)
        for idx, (rating, notch, base, incl, setor) in enumerate(
            [("AAA", 1, 20.0, 10.0, "Energia"), ("AA", 4, 100.0, 20.0, "Saneamento")]
        ):
            # `key` é NOT NULL — é a chave canônica de emissor
            # (app/spreads/issuer_key.py), não um detalhe opcional.
            iss = Issuer(key=f"EMISSOR {rating}", nome=f"EMISSOR {rating}", setor=setor)
            db.add(iss)
            db.flush()
            db.add(IssuerRatingPeriodo(
                issuer_id=iss.id, codigo=None, origem="HISTORICO",
                data_inicio=date(2020, 1, 1), data_fim=None,
                rating_medio=rating, notch_medio=notch,
            ))
            for i in range(15):
                cod = f"{'X' if idx == 0 else 'Y'}{i:04d}"
                dur = 1.0 + i * 0.5
                db.add(Debenture(
                    codigo=cod, nome=f"Papel {cod}", indexador="IPCA +",
                    incentivada="S", classe="IPCA + Incentivadas", issuer_id=iss.id,
                ))
                # Último papel de cada rating sai 200 bps acima da curva.
                extra = 200.0 if i == 14 else 0.0
                db.add(DebentureSpread(
                    codigo=cod, data=hoje, spread=base + incl * dur + extra,
                    duration=dur, estoque=100.0, taxa_indicativa=7.0,
                ))
        db.commit()
    yield SessionLocal
    Base.metadata.drop_all(engine)


def test_curva_recupera_a_inclinacao_plantada(banco):
    from app.spreads.analitico import curva_por_rating

    with banco() as db:
        d = curva_por_rating(db, "IPCA + Incentivadas")
    curvas = {c["rating"]: c for c in d["curvas"]}
    assert set(curvas) == {"AAA", "AA"}
    # O papel deslocado não pode mover a inclinação — é exatamente o ponto.
    assert curvas["AAA"]["roll_down_bps_ano"] == pytest.approx(10.0, abs=0.5)
    assert curvas["AA"]["roll_down_bps_ano"] == pytest.approx(20.0, abs=0.5)


def test_curva_ordena_do_melhor_rating_para_o_pior(banco):
    """A tela desenha uma cor por posição na lista, numa escala que vai do
    verde ao vermelho — se a ordem furar, o gráfico mente sobre a
    hierarquia de risco."""
    from app.spreads.analitico import curva_por_rating

    with banco() as db:
        d = curva_por_rating(db, "IPCA + Incentivadas")
    assert [c["rating"] for c in d["curvas"]] == ["AAA", "AA"]


def test_decomposicao_acha_o_papel_deslocado(banco):
    from app.spreads.analitico import decomposicao

    with banco() as db:
        d = decomposicao(db, "IPCA + Incentivadas")
    achados = {c["codigo"] for c in d["baratos"] + d["revisar"]}
    assert "X0014" in achados and "Y0014" in achados


def test_faixa_perfeitamente_ajustada_nao_apaga_o_outlier(banco):
    """BUG REAL, pego por este teste (12/08/2026).

    Na base sintética 14 dos 15 papéis caem EXATAMENTE na curva, então o
    MAD do grupo é 0. A versão anterior fazia `z = residuo / MAD` e caía
    num `else 0.0` quando MAD era 0 — dando z=0 pra todo mundo, inclusive
    pro único papel deslocado. O papel que interessava sumia da lista sem
    erro nenhum.

    O piso `MAD_MINIMO_BPS` conserta, e também tira o exagero do outro
    lado (AA+ com MAD de 6,9 bps produzia z acima de 50 na base real).
    """
    from app.spreads.analitico import decomposicao

    with banco() as db:
        d = decomposicao(db, "IPCA + Incentivadas")
    todos = d["baratos"] + d["caros"] + d["revisar"]
    z_deslocados = [c["z"] for c in todos if c["codigo"] in ("X0014", "Y0014")]
    assert z_deslocados, "os papéis deslocados não apareceram em lista nenhuma"
    assert all(abs(z) > Z_REVISAO for z in z_deslocados), (
        f"z dos deslocados = {z_deslocados}; deveriam estar muito fora da faixa"
    )


def test_decomposicao_soma_100_por_cento(banco):
    """As três fatias são uma decomposição de variância — têm que fechar
    (com 1 p.p. de folga pro arredondamento de cada uma)."""
    from app.spreads.analitico import decomposicao

    with banco() as db:
        e = decomposicao(db, "IPCA + Incentivadas")["explicacao"]
    total = e["pct_rating"] + e["pct_setor"] + e["pct_idiossincratico"]
    assert 99 <= total <= 101, f"as fatias somaram {total}%"


def test_decomposicao_nunca_devolve_chave_faltando(banco):
    """A tela lê todas essas chaves sem checar — chave ausente vira
    `undefined` no JS e some da tela em silêncio."""
    from app.spreads.analitico import decomposicao

    with banco() as db:
        d = decomposicao(db, "IPCA + Incentivadas")
    for k in ("data", "explicacao", "setores", "baratos", "caros", "revisar", "z_revisao"):
        assert k in d, f"faltou '{k}' no retorno"


def test_resumo_por_rating_fecha_100_por_cento_do_estoque(banco):
    from app.spreads.analitico import resumo_por_rating

    with banco() as db:
        d = resumo_por_rating(db, "IPCA + Incentivadas")
    assert sum(l["pct_estoque"] for l in d["linhas"]) == pytest.approx(100.0, abs=0.2)
    assert d["total_estoque"] == pytest.approx(30 * 100.0)


def test_dispersao_respeita_a_ordem_dos_percentis(banco):
    from app.spreads.analitico import dispersao_intra_rating

    with banco() as db:
        d = dispersao_intra_rating(db, "IPCA + Incentivadas")
    assert d["faixas"], "deveria ter faixas"
    for f in d["faixas"]:
        assert f["p10"] <= f["p25"] <= f["mediana"] <= f["p75"] <= f["p90"], f


def test_data_sem_dado_nao_explode(banco):
    """Data anterior a qualquer boletim: tem que devolver estrutura vazia
    coerente, não estourar."""
    from app.spreads.analitico import (
        curva_por_rating, decomposicao, dispersao_intra_rating,
        posicao_historica, resumo_por_rating,
    )
    antiga = date(2000, 1, 1)
    with banco() as db:
        assert posicao_historica(db, "IPCA + Incentivadas", antiga)["spread"] is None
        assert curva_por_rating(db, "IPCA + Incentivadas", antiga)["curvas"] == []
        assert dispersao_intra_rating(db, "IPCA + Incentivadas", antiga)["faixas"] == []
        assert resumo_por_rating(db, "IPCA + Incentivadas", antiga)["linhas"] == []
        assert decomposicao(db, "IPCA + Incentivadas", antiga)["baratos"] == []


def test_classe_inexistente_devolve_vazio_e_nao_mistura(banco):
    """As duas classes NUNCA se misturam (regra do Allan, 23/07/2026) —
    pedir CDI+ numa base só de IPCA+ tem que vir vazio, não cair na outra."""
    from app.spreads.analitico import curva_por_rating

    with banco() as db:
        assert curva_por_rating(db, "CDI + Tradicionais")["curvas"] == []


# ---------------------------------------------------------------------------
# Rotas ligadas ao app — o teste que pega "módulo escrito mas não ligado"
# ---------------------------------------------------------------------------

ROTAS = [
    "/api/spreads/posicao-historica",
    "/api/spreads/curva",
    "/api/spreads/dispersao",
    "/api/spreads/compressao",
    "/api/spreads/valor-relativo",
    "/api/spreads/por-rating",
]


def _login(SessionLocal):
    """Cria um usuário ativo e devolve o token de sessão.

    Necessário desde 13/08/2026: a aba Spreads voltou a exigir login
    (`app.py`: `register_spreads_routes(require_user)`) -- hoje a única
    aba pública é o Repositório de Relatórios. Mesmo padrão de
    `tests/test_banco.py::_login_admin`.
    """
    from app import auth
    from app.models import User
    with SessionLocal() as db:
        u = db.query(User).first()
        if u is None:
            u = User(email="a@a.com", name="Teste", role="admin", active=True,
                     password_hash=auth.hash_password("x" * 10), email_confirmed=True)
            db.add(u)
            db.commit()
        s = auth.create_session(db, u, ip="1", user_agent="teste")
        db.commit()
        return s.token


@pytest.fixture()
def cliente(banco):
    """Cliente JÁ AUTENTICADO.

    ARMADILHA QUE ESTE FIXTURE FECHA (corrigido em 20/08/2026): sem o
    cookie, `require_user` redireciona para /login e o TestClient SEGUE o
    redirect, devolvendo a página de login com status **200**. Ou seja,
    `assert r.status_code == 200` passava mesmo com a rota completamente
    quebrada -- justamente o contrário do que `test_rota_responde` foi
    escrito para pegar. O par de testes ficava um verde-falso e um
    vermelho legítimo (o de 400, que via 200 da tela de login).
    """
    from fastapi.testclient import TestClient
    import app.app as A
    c = TestClient(A.app, raise_server_exceptions=False)
    c.cookies.set("session_token", _login(banco))
    return c


# `+` em query string significa ESPAÇO — "classe=IPCA + Incentivadas" cru
# chega no servidor como "IPCA   Incentivadas" e toma 400. Tem que ser
# percent-encoded (`%2B`), que é o que o `URLSearchParams` do
# static/spreads_analitico.js faz sozinho.
CLASSE_Q = "IPCA%20%2B%20Incentivadas"


@pytest.mark.parametrize("rota", ROTAS)
def test_rota_responde(cliente, rota):
    """BUG JÁ PAGO (11/08/2026): módulos escritos, rotas não registradas no
    app.py, e o Allan reiniciou o servidor sem ver mudança nenhuma. Teste
    de unidade não pega isso."""
    r = cliente.get(f"{rota}?classe={CLASSE_Q}", follow_redirects=False)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("rota", ROTAS)
def test_rota_recusa_classe_invalida(cliente, rota):
    r = cliente.get(f"{rota}?classe=Outros", follow_redirects=False)
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("rota", ROTAS)
def test_rota_exige_login(banco, rota):
    """Contrapartida do fixture autenticado: garante que a proteção existe
    de verdade, em vez de o teste acima passar por já estar logado."""
    from fastapi.testclient import TestClient
    import app.app as A
    anon = TestClient(A.app, raise_server_exceptions=False)
    r = anon.get(f"{rota}?classe={CLASSE_Q}", follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403), r.status_code


def test_pagina_spreads_traz_os_quatro_blocos(cliente):
    """A ordem dos blocos é a tese da tela — se um sumir do template, a
    tela continua funcionando e a leitura quebra em silêncio."""
    r = cliente.get("/spreads")
    assert r.status_code == 200
    for termo in (
        "Onde o mercado está", "Como chegamos aqui", "Onde está o valor", "Detalhe",
        "Curva de crédito por rating", "Compressão entre ratings",
        "Quanto cada camada explica", "spreads_analitico.js",
    ):
        assert termo in r.text, f"faltou '{termo}' na página"


def test_pagina_mantem_os_ids_que_o_spreads_js_usa(cliente):
    """O spreads.js antigo (887 linhas, validado e em uso) referencia
    estes IDs por `getElementById`. Se a reorganização derrubar um, a
    parte antiga da tela para sem erro visível."""
    r = cliente.get("/spreads")
    for elemento_id in (
        "dados-ate", "classe-tabs", "base-tabs", "visao-data", "busca-ativo",
        "busca-resultados", "kpi-spread", "kpi-variacao", "kpi-n-ativos",
        "kpi-duration", "chart-series", "chart-scatter", "chart-distribution",
        "tabela-aberturas", "tabela-fechamentos", "detalhes-wrap", "drilldown-wrap",
        "painel-visao-geral", "painel-emissores", "secao-tabs",
    ):
        assert f'id="{elemento_id}"' in r.text, f"sumiu o id '{elemento_id}'"
