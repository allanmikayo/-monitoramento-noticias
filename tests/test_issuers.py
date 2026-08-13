"""Testes de app/spreads/issuers.py — emissores, taxonomia e rating vigente.

Usa SQLite em memória; nenhum teste toca o banco real.

    python -m pytest tests/test_issuers.py -v
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Debenture, Issuer, IssuerAlias, IssuerRatingAtual, IssuerRatingPeriodo
from app.spreads import issuers as isv


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _issuer(db, nome, **kw):
    from app.spreads.issuer_key import issuer_key
    i = Issuer(key=issuer_key(nome), nome=nome, **kw)
    db.add(i)
    db.flush()
    return i


# ---------------------------------------------------------------------------
# Resolução de emissor
# ---------------------------------------------------------------------------

def test_resolve_por_grafia_diferente(db):
    """O motivo do módulo existir: a mesma Anbima escreve o nome de dois
    jeitos e as duas grafias têm que chegar no mesmo emissor."""
    i = _issuer(db, "AEGEA SANEAMENTO E PARTICIPACOES S.A.")
    assert isv.resolver_issuer(db, "AEGEA SANEAMENTO E PARTICIPAÇÕES S/A") is i


def test_resolve_por_alias(db):
    """Casamento manual: "AES CAJUINA AB1" e "CAJUINA AB1" nunca colapsam
    por normalização (e não devem — remover prefixo de grupo juntaria
    empresas distintas), então a ligação vem do alias."""
    i = _issuer(db, "CAJUINA AB1 HOLDINGS S.A")
    db.add(IssuerAlias(issuer_id=i.id, alias_key="AES CAJUINA AB1", alias_nome="AES CAJUINA AB1 HOLDINGS S.A."))
    db.flush()
    assert isv.resolver_issuer(db, "AES CAJUINA AB1 HOLDINGS S.A.") is i


def test_nome_invalido_nao_resolve(db):
    _issuer(db, "VALE S.A.")
    for ruim in (None, "", "-", "S.A."):
        assert isv.resolver_issuer(db, ruim) is None


def test_resolver_nao_cria(db):
    """`resolver_issuer` é read-only de propósito: a carga de ratings usa
    ela justamente pra NÃO criar emissor de papel que não temos."""
    assert isv.resolver_issuer(db, "EMPRESA NOVA S.A.") is None
    assert db.query(Issuer).count() == 0


def test_obter_ou_criar_nasce_sem_taxonomia(db):
    i = isv.obter_ou_criar_issuer(db, "EMPRESA NOVA S.A.")
    assert i is not None and i.setor is None
    assert i.taxonomia_origem == isv.ORIGEM_ANBIMA


# ---------------------------------------------------------------------------
# Precedência da taxonomia
# ---------------------------------------------------------------------------

def test_snapshot_preenche_vazio(db):
    i = _issuer(db, "VALE S.A.")
    assert isv.aplicar_taxonomia(i, setor="Mineração", sub_setor="Mineração",
                                 grupo_economico="VALE", origem=isv.ORIGEM_SNAPSHOT)
    assert i.setor == "Mineração"


def test_recarga_nao_desfaz_edicao_manual(db):
    """O bug que essa precedência evita: o Allan corrige um setor na
    Administração, alguém roda o seed de novo e a correção evapora sem
    ninguém notar."""
    i = _issuer(db, "VALE S.A.")
    isv.aplicar_taxonomia(i, setor="Setor Correto", sub_setor=None,
                          grupo_economico=None, origem=isv.ORIGEM_MANUAL)
    mudou = isv.aplicar_taxonomia(i, setor="Setor Da Planilha", sub_setor=None,
                                  grupo_economico=None, origem=isv.ORIGEM_SNAPSHOT)
    assert mudou is False
    assert i.setor == "Setor Correto"


def test_manual_sobrescreve_snapshot(db):
    i = _issuer(db, "VALE S.A.")
    isv.aplicar_taxonomia(i, setor="A", sub_setor=None, grupo_economico=None,
                          origem=isv.ORIGEM_SNAPSHOT)
    assert isv.aplicar_taxonomia(i, setor="B", sub_setor=None, grupo_economico=None,
                                 origem=isv.ORIGEM_MANUAL)
    assert i.setor == "B"


# ---------------------------------------------------------------------------
# Rating vigente
# ---------------------------------------------------------------------------

def test_rating_vigente_por_agencia_independente(db):
    """CASO REAL (TUPI ENERGIAS, validação de 04/08/2026): Fitch avaliou em
    abril, Moody's em maio. O rating vigente combina a ÚLTIMA de CADA
    agência — não só a última linha capturada.

    Fitch AA(bra)=3 + Moody's BBB.br=9 -> média 6 -> "A".
    """
    i = _issuer(db, "TUPI ENERGIAS RENOVAVEIS S.A.")
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AA(bra)", data_acao=date(2026, 4, 29))
    isv.registrar_acao_rating(db, i, agencia="MOODYS", rating="BBB.br", data_acao=date(2026, 5, 29))
    atual = isv.recalcular_rating_atual(db, i.id)
    assert (atual.fitch, atual.moodys) == ("AA(bra)", "BBB.br")
    assert atual.rating_medio == "A"
    assert atual.n_agencias == 2


def test_rating_mais_recente_vence(db):
    i = _issuer(db, "EMPRESA S.A.")
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AA(bra)", data_acao=date(2026, 1, 1))
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AAA(bra)", data_acao=date(2026, 6, 1))
    atual = isv.recalcular_rating_atual(db, i.id)
    assert atual.fitch == "AAA(bra)"
    assert atual.fitch_data == date(2026, 6, 1)
    assert atual.rating_medio == "AAA"


def test_sem_rating_vira_na(db):
    i = _issuer(db, "EMPRESA S.A.")
    atual = isv.recalcular_rating_atual(db, i.id)
    assert atual.rating_medio == "N.A."
    assert atual.notch_medio is None and atual.n_agencias == 0


def test_notch_permite_ordenar_por_risco(db):
    """Ordenar pela string daria A < AA < AAA (alfabético), que é o
    inverso do risco. `notch_medio` existe pra isso."""
    for nome, ag, rt in [("A S.A.", "FITCH", "AAA(bra)"),
                         ("B S.A.", "FITCH", "A(bra)"),
                         ("C S.A.", "FITCH", "AA(bra)")]:
        i = _issuer(db, nome)
        isv.registrar_acao_rating(db, i, agencia=ag, rating=rt, data_acao=date(2026, 1, 1))
        isv.recalcular_rating_atual(db, i.id)
    db.flush()
    ordem = [a.rating_medio for a in db.query(IssuerRatingAtual).order_by(IssuerRatingAtual.notch_medio).all()]
    assert ordem == ["AAA", "AA", "A"]


def test_acao_duplicada_nao_grava(db):
    """O scraper reprocessa janelas sobrepostas — reencontrar a mesma ação
    é o caso NORMAL, não erro."""
    i = _issuer(db, "EMPRESA S.A.")
    assert isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AA(bra)", data_acao=date(2026, 1, 1))
    assert isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AA(bra)", data_acao=date(2026, 1, 1)) is None


def test_duplicata_detectada_dentro_do_mesmo_lote(db):
    """BUG REAL (04/08/2026): a carga do snapshot estourou UNIQUE constraint
    na AEGEA — a fonte traz a mesma ação repetida uma vez por ticker (33
    linhas idênticas). Sem o flush em `registrar_acao_rating`, o SELECT de
    verificação não enxergava os INSERTs ainda pendentes na sessão.
    """
    i = _issuer(db, "AEGEA SANEAMENTO S/A")
    gravadas = sum(
        1 for _ in range(33)
        if isv.registrar_acao_rating(db, i, agencia="FITCH", rating="A+(bra)",
                                     data_acao=date(2026, 4, 29)) is not None
    )
    assert gravadas == 1


def test_agencia_invalida_levanta(db):
    i = _issuer(db, "EMPRESA S.A.")
    with pytest.raises(ValueError):
        isv.registrar_acao_rating(db, i, agencia="STANDARD_POORS", rating="brAAA",
                                  data_acao=date(2026, 1, 1))


def test_rating_desconhecido_e_registrado(db):
    """`brCCC` e `RD(bra)` não estão nas tabelas de peso (decisão do Allan:
    manter como está). Não podem sumir em silêncio."""
    i = _issuer(db, "EMPRESA S.A.")
    isv.registrar_acao_rating(db, i, agencia="SP", rating="brCCC", data_acao=date(2026, 1, 1))
    atual = isv.recalcular_rating_atual(db, i.id)
    assert atual.rating_medio == "N.A."
    assert "brCCC" in (atual.desconhecidos_json or "")


# ---------------------------------------------------------------------------
# Vínculo debênture -> emissor
# ---------------------------------------------------------------------------

def test_ticker_tem_precedencia_sobre_nome(db):
    """CASO REAL (SABESP): o banco escreve "DE SP", a planilha "DE SÃO
    PAULO" — nunca colapsam por normalização, mas o ticker prova que é a
    mesma empresa."""
    i = _issuer(db, "CIA SANEAMENTO BASICO EST. SAO PAULO - SABESP")
    db.add(Debenture(codigo="SBSP15", nome="CIA. DE SANEAMENTO BÁSICO DO ESTADO DE SP - SABESP"))
    db.flush()
    r = isv.vincular_debentures(db, {"SBSP15": "CIA SANEAMENTO BASICO EST. SAO PAULO - SABESP"})
    assert r["por_ticker"] == 1
    assert db.get(Debenture, "SBSP15").issuer_id == i.id


def test_casamento_por_ticker_vira_alias_quando_ha_evidencia(db):
    """Tokens não-genéricos em comum (CACHOEIRA, PAULISTA) => generalizar
    é seguro, vira alias e a próxima emissão casa sozinha."""
    i = _issuer(db, "CACHOEIRA PAULISTA TRANSMISSORA DE ENERGIA S.A.")
    db.add(Debenture(codigo="CPTE11", nome="CACHOEIRA PAULISTA TRANS. DE ENERGIA S/A"))
    db.flush()
    r = isv.vincular_debentures(db, {"CPTE11": "CACHOEIRA PAULISTA TRANSMISSORA DE ENERGIA S.A."})
    assert len(r["aliases_criados"]) == 1
    assert db.query(IssuerAlias).count() == 1


def test_nomes_sem_token_em_comum_vao_para_revisao(db):
    """CASO REAL (EDP TRANSMISSÃO -> HORIZON TRANSMISSÃO ES): renomeação
    societária provável, mas os nomes só compartilham "TRANSMISSAO", que é
    genérico. O papel é ligado (o ticker prova), mas NÃO vira alias — senão
    toda emissão futura chamada "EDP TRANSMISSÃO" herdaria o emissor
    errado a partir de um caso só."""
    _issuer(db, "HORIZON TRANSMISSAO ES S.A.")
    db.add(Debenture(codigo="EDPT11", nome="EDP TRANSMISSÃO S.A."))
    db.flush()
    r = isv.vincular_debentures(db, {"EDPT11": "HORIZON TRANSMISSAO ES S.A."})
    assert r["por_ticker"] == 1              # papel ligado
    assert r["aliases_criados"] == []        # mas não generalizado
    assert len(r["revisar"]) == 1
    assert db.query(IssuerAlias).count() == 0


def test_fallback_por_nome_quando_ticker_desconhecido(db):
    """Emissão nova, posterior à carga da planilha: não está no mapa de
    tickers, mas o nome do emissor casa."""
    i = _issuer(db, "VALE S.A.")
    db.add(Debenture(codigo="VALE99", nome="VALE S/A"))
    db.flush()
    r = isv.vincular_debentures(db, {})
    assert r["por_nome"] == 1
    assert db.get(Debenture, "VALE99").issuer_id == i.id


def test_sem_match_e_reportado_nao_inventado(db):
    db.add(Debenture(codigo="XXXX11", nome="EMPRESA DESCONHECIDA S.A."))
    db.flush()
    r = isv.vincular_debentures(db, {})
    assert r["ligadas"] == 0
    assert "EMPRESA DESCONHECIDA S.A." in r["sem_match"]
    assert db.get(Debenture, "XXXX11").issuer_id is None


# ---------------------------------------------------------------------------
# Rating histórico (as-of) — evita viés retrospectivo
# ---------------------------------------------------------------------------

def test_periodo_por_mudanca_nao_por_dia(db):
    """Uma linha por MUDANÇA de rating, não por dia do calendário."""
    i = _issuer(db, "EMPRESA S.A.")
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AAA(bra)", data_acao=date(2025, 1, 10))
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AA(bra)", data_acao=date(2026, 3, 5))
    isv.reconstruir_periodos_rating(db, i.id)
    ps = db.query(IssuerRatingPeriodo).order_by(IssuerRatingPeriodo.data_inicio).all()
    assert len(ps) == 2
    assert (ps[0].data_inicio, ps[0].data_fim, ps[0].rating_medio) == (date(2025, 1, 10), date(2026, 3, 5), "AAA")
    assert (ps[1].data_inicio, ps[1].data_fim, ps[1].rating_medio) == (date(2026, 3, 5), None, "AA")


def test_rating_em_data_passada_nao_usa_o_de_hoje(db):
    """O ponto central: um emissor rebaixado hoje NÃO pode aparecer no
    balde de hoje no histórico inteiro. Sem isso, o gráfico de "spread
    médio AAA no tempo" fica com nível artificialmente alto no passado."""
    i = _issuer(db, "EMPRESA S.A.")
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AAA(bra)", data_acao=date(2025, 1, 10))
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="BBB(bra)", data_acao=date(2026, 6, 1))
    isv.reconstruir_periodos_rating(db, i.id)
    assert isv.rating_em(db, i.id, date(2025, 6, 30)).rating_medio == "AAA"
    assert isv.rating_em(db, i.id, date(2026, 7, 1)).rating_medio == "BBB"


def test_antes_do_primeiro_rating_devolve_none(db):
    """Não estende o rating mais antigo pra trás.

    Validado contra o snapshot (04/08/2026): estender derruba a aderência
    de 100% pra 84,6% — o histórico de spread começa muito antes da base
    de ratings e as notas daquele período eram outras. Buraco visível é
    melhor que cobertura inventada."""
    i = _issuer(db, "EMPRESA S.A.")
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AAA(bra)", data_acao=date(2026, 4, 29))
    isv.reconstruir_periodos_rating(db, i.id)
    assert isv.rating_em(db, i.id, date(2025, 1, 3)) is None
    assert isv.rating_em(db, i.id, date(2026, 4, 29)).rating_medio == "AAA"


def test_agencias_no_mesmo_dia_geram_um_periodo(db):
    """Duas agências publicando no mesmo dia é um estado só, não dois."""
    i = _issuer(db, "EMPRESA S.A.")
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AAA(bra)", data_acao=date(2026, 5, 1))
    isv.registrar_acao_rating(db, i, agencia="SP", rating="brAA+", data_acao=date(2026, 5, 1))
    isv.reconstruir_periodos_rating(db, i.id)
    ps = db.query(IssuerRatingPeriodo).all()
    assert len(ps) == 1
    assert (ps[0].fitch, ps[0].sp) == ("AAA(bra)", "brAA+")
    assert ps[0].rating_medio == "AA+"      # (1+2)/2 = 1,5 -> 2


def test_reafirmacao_nao_cria_periodo(db):
    """Agência reafirmar a mesma nota não muda balde -- não vira período
    novo (só encareceria a junção)."""
    i = _issuer(db, "EMPRESA S.A.")
    for dt in (date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1)):
        isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AAA(bra)", data_acao=dt)
    isv.reconstruir_periodos_rating(db, i.id)
    assert db.query(IssuerRatingPeriodo).count() == 1


def test_acao_retroativa_reconstroi_certo(db):
    """A agência publica com atraso e o scraper grava a data do FATO, então
    chegar ação com data anterior à última já gravada é normal. A
    reconstrução é completa justamente pra isso."""
    i = _issuer(db, "EMPRESA S.A.")
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AA(bra)", data_acao=date(2026, 6, 1))
    isv.reconstruir_periodos_rating(db, i.id)
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AAA(bra)", data_acao=date(2026, 2, 1))
    isv.reconstruir_periodos_rating(db, i.id)
    ps = db.query(IssuerRatingPeriodo).order_by(IssuerRatingPeriodo.data_inicio).all()
    assert [p.rating_medio for p in ps] == ["AAA", "AA"]
    assert ps[0].data_fim == date(2026, 6, 1)


def test_reconstrucao_e_idempotente(db):
    i = _issuer(db, "EMPRESA S.A.")
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AAA(bra)", data_acao=date(2026, 1, 1))
    isv.reconstruir_periodos_rating(db, i.id)
    n1 = db.query(IssuerRatingPeriodo).count()
    isv.reconstruir_periodos_rating(db, i.id)
    assert db.query(IssuerRatingPeriodo).count() == n1 == 1


# ---------------------------------------------------------------------------
# Histórico congelado (origem='HISTORICO')
# ---------------------------------------------------------------------------

def _congelar(db, issuer, codigo, serie):
    return isv.gravar_periodos_historicos(db, issuer.id, codigo, serie)


def test_historico_vence_o_derivado_na_janela_dele(db):
    """A regra do Allan (04/08/2026): "o histórico não deve ser alterado,
    se para o ticker x o rating era y na data z, manter".

    Aqui o histórico diz AAA em 2025; uma ação de rating com data
    retroativa diria AA. O histórico tem que ganhar.
    """
    i = _issuer(db, "EMPRESA S.A.")
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AA(bra)", data_acao=date(2025, 1, 1))
    isv.reconstruir_periodos_rating(db, i.id)
    _congelar(db, i, None, [(date(2025, 1, 3), "AAA", 1, {"FITCH": "AAA(bra)"})])
    db.flush()
    assert isv.rating_em(db, i.id, date(2025, 6, 1)).rating_medio == "AAA"


def test_reconstruir_derivado_nao_apaga_historico(db):
    """O bug que isso previne: rodar o job diário apagaria o histórico
    congelado e mudaria todas as curvas de 2025."""
    i = _issuer(db, "EMPRESA S.A.")
    _congelar(db, i, None, [(date(2025, 1, 3), "AAA", 1, {})])
    db.flush()
    isv.reconstruir_periodos_rating(db, i.id)
    isv.reconstruir_periodos_rating(db, i.id)
    congelados = db.query(IssuerRatingPeriodo).filter_by(origem="HISTORICO").all()
    assert len(congelados) == 1 and congelados[0].rating_medio == "AAA"


def test_historico_nao_e_recalculado_das_agencias(db):
    """~1,7% da base do Allan tem `ratingMedio` discordando das próprias
    colunas de agência (planilha defasada). O valor DELE é o que fica —
    "consertar" mudaria curva já analisada e distribuída."""
    i = _issuer(db, "EMPRESA S.A.")
    # As agências dariam AA+ ((1+2)/2 = 1,5 -> 2); a view dizia AAA.
    _congelar(db, i, None,
              [(date(2025, 1, 3), "AAA", 1, {"FITCH": "AAA(bra)", "SP": "brAA+"})])
    db.flush()
    assert isv.rating_em(db, i.id, date(2025, 6, 1)).rating_medio == "AAA"


def test_congelar_e_idempotente(db):
    i = _issuer(db, "EMPRESA S.A.")
    serie = [(date(2025, 1, 3), "AAA", 1, {})]
    _congelar(db, i, None, serie)
    _congelar(db, i, None, serie)
    db.flush()
    assert db.query(IssuerRatingPeriodo).filter_by(origem="HISTORICO").count() == 1


def test_ultimo_periodo_fica_aberto(db):
    """BUG REAL (04/08/2026): a 1ª versão fechava a janela observada na
    última data e deixava o cálculo derivado assumir. Medido: 23% dos
    escopos davam SALTO na fronteira (185 casos "N.A. -> AAA").

    Pela regra do Allan, sem ação nova nada muda -- o último rating
    continua valendo indefinidamente."""
    i = _issuer(db, "EMPRESA S.A.")
    _congelar(db, i, None, [(date(2025, 1, 3), "AAA", 1, {})])
    db.flush()
    p = db.query(IssuerRatingPeriodo).filter_by(origem="HISTORICO").one()
    assert p.data_fim is None
    assert isv.rating_em(db, i.id, date(2027, 1, 1)).rating_medio == "AAA"


def test_acao_nova_fecha_o_periodo_observado_sem_salto(db):
    """"Quando eu atualizo os spreads na data x, se tiver alguma rating
    action na data x aí vai receber esse rating, e os de datas anteriores
    permanecerão inalteráveis"."""
    i = _issuer(db, "EMPRESA S.A.")
    _congelar(db, i, None, [(date(2025, 1, 3), "AAA", 1, {"FITCH": "AAA(bra)"})])
    db.flush()
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="AA(bra)", data_acao=date(2026, 9, 1))
    isv.reconstruir_periodos_rating(db, i.id)
    assert isv.rating_em(db, i.id, date(2025, 6, 1)).rating_medio == "AAA"   # passado intacto
    assert isv.rating_em(db, i.id, date(2026, 8, 31)).rating_medio == "AAA"  # véspera, sem salto
    assert isv.rating_em(db, i.id, date(2026, 9, 2)).rating_medio == "AA"    # ação nova vale


def test_acao_dentro_da_janela_observada_e_ignorada(db):
    """Ação já representada na view não pode gerar período concorrente --
    era a origem do salto de fronteira."""
    i = _issuer(db, "EMPRESA S.A.")
    _congelar(db, i, None, [(date(2025, 1, 3), "AAA", 1, {}),
                            (date(2026, 5, 1), "AA", 3, {})])
    db.flush()
    isv.registrar_acao_rating(db, i, agencia="FITCH", rating="BBB(bra)", data_acao=date(2025, 6, 1))
    isv.reconstruir_periodos_rating(db, i.id)
    assert isv.rating_em(db, i.id, date(2025, 7, 1)).rating_medio == "AAA"
    assert db.query(IssuerRatingPeriodo).filter_by(origem="DERIVADO").count() == 0


def test_historico_por_ticker_vence_o_do_emissor(db):
    """Caso COSAN: a tranche tem rating próprio, diferente do emissor."""
    i = _issuer(db, "COSAN S.A.")
    _congelar(db, i, None, [(date(2025, 1, 3), "A+", 5, {})])
    _congelar(db, i, "CSAN13", [(date(2025, 1, 3), "AAA", 1, {})])
    db.flush()
    assert isv.rating_em(db, i.id, date(2025, 6, 1), "CSAN13").rating_medio == "AAA"
    assert isv.rating_em(db, i.id, date(2025, 6, 1), "CSAN15").rating_medio == "A+"


def test_vinculo_e_idempotente(db):
    _issuer(db, "VALE S.A.")
    db.add(Debenture(codigo="VALE99", nome="VALE S/A"))
    db.flush()
    mapa = {"VALE99": "VALE S.A."}
    isv.vincular_debentures(db, mapa)
    r2 = isv.vincular_debentures(db, mapa)
    assert r2["ligadas"] == 1
    assert db.query(IssuerAlias).count() == 0   # não duplica alias
