"""Uma fonte lenta não pode derrubar a varredura inteira.

BUG REAL (11/09/2026). O job do GitHub Actions morreu em
"The operation was canceled" -- bateu no `timeout-minutes: 20`. O preparo
levou 57 segundos; a fonte 23 de 24 começou e nunca terminou. Não era
travamento: `vortx.fetch` faz uma busca HTTP para CADA nome da cobertura
(empresas mais apelidos, ~250 buscas) antes de abrir o navegador, e isso
roda a cada 15 minutos.

O estrago não ficou na fonte lenta. O job morreu, a fonte 24 nunca rodou, o
`RunLog` nunca fechou e o resumo nunca foi impresso -- tudo que veio antes
já estava salvo, mas a execução aparece como falha e o painel não registra
nada.

O teto por fonte é a defesa GERAL: qualquer fonte que passe do limite é
interrompida, registra erro como qualquer outra falha, e a varredura segue.
"""
from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from app import pipeline

UNIX = hasattr(__import__("signal"), "SIGALRM")


def _fonte_falsa(nome: str, fetch) -> dict:
    """Registra um módulo de fonte no lugar de um arquivo em app/sources."""
    mod = types.ModuleType(f"app.sources.{nome}")
    mod.fetch = fetch
    sys.modules[f"app.sources.{nome}"] = mod
    return {"name": nome, "category": "news", "scraper_module": nome,
            "url": "http://exemplo", "domain": "exemplo"}


@pytest.mark.skipif(not UNIX, reason="o teto usa SIGALRM, que só existe em Unix")
def test_fonte_lenta_e_interrompida_e_vira_erro(monkeypatch):
    monkeypatch.setattr(pipeline, "TEMPO_MAXIMO_POR_FONTE", 1)

    def devagar(url):
        time.sleep(30)
        return []

    info = _fonte_falsa("fonte_devagar", devagar)
    comeco = time.monotonic()
    r = pipeline._run_source(info, taxonomy=None)
    gasto = time.monotonic() - comeco

    assert gasto < 10, f"não interrompeu: gastou {gasto:.1f}s"
    assert r["error"], "a interrupção tem que virar erro registrado, não silêncio"
    assert "TimeoutError" in r["error"]
    assert r["found"] == 0


@pytest.mark.skipif(not UNIX, reason="o teto usa SIGALRM, que só existe em Unix")
def test_o_relogio_nao_sobra_para_a_fonte_seguinte():
    """O alarme tem que ser desarmado no fim de cada fonte. Se vazasse, uma
    fonte rápida depois de uma lenta seria morta pelo relógio da anterior --
    um erro fantasma, na fonte errada."""
    from app.pipeline import _teto_de_tempo

    with _teto_de_tempo(1, "primeira"):
        pass
    time.sleep(1.3)  # o alarme da primeira já teria estourado aqui
    with _teto_de_tempo(30, "segunda"):
        time.sleep(0.1)


def test_fonte_rapida_passa_intacta():
    from app.sources.base import RawArticle

    info = _fonte_falsa(
        "fonte_rapida",
        lambda url: [RawArticle(url="http://exemplo/1", title="oi")],
    )
    r = pipeline._run_source(info, taxonomy=None)
    # Sem taxonomia real a gravação falha adiante, mas o que importa aqui é
    # que o teto não interferiu na captura.
    assert r["found"] == 1
    assert r["error"] is None or "TimeoutError" not in str(r["error"])


def test_sem_sigalrm_o_teto_nao_quebra(monkeypatch):
    """No Windows do Allan não há SIGALRM. O contexto tem que virar um nada,
    nunca uma exceção -- rodar local não pode depender de um recurso de
    Unix."""
    import signal as _signal

    monkeypatch.delattr(_signal, "SIGALRM", raising=False)
    with pipeline._teto_de_tempo(1, "qualquer"):
        time.sleep(0.05)


def test_em_thread_de_fundo_o_teto_se_desliga():
    """O botão "Forçar atualização" do dashboard roda a varredura numa
    thread de fundo (app/scheduler.py), e `signal.signal` só funciona na
    thread principal -- armar o alarme ali levantaria ValueError e quebraria
    a varredura manual."""
    erro = []

    def alvo():
        try:
            with pipeline._teto_de_tempo(1, "em thread"):
                time.sleep(0.05)
        except Exception as e:  # noqa: BLE001
            erro.append(e)

    t = threading.Thread(target=alvo)
    t.start()
    t.join()
    assert not erro, f"o teto quebrou numa thread de fundo: {erro}"


# ---------------------------------------------------------------------------
# Duas cadências: notícias de 15 em 15 min, assembleias 1x/dia
# ---------------------------------------------------------------------------
#
# As três fontes de assembleia buscam empresa por empresa (~250 buscas HTTP
# em sequência) e não cabem numa varredura de 15 minutos. Saíram para
# `scripts/rodada_assembleias.py`. O filtro é por `scraper_module` porque a
# cadência depende de COMO o scraper foi escrito -- não é configuração do
# usuário, que continua mandando só no `enabled` de cada fonte.

def _fontes_no_banco(monkeypatch, modulos):
    """Substitui a consulta de fontes por uma lista fixa, sem tocar banco."""
    from app import pipeline as P

    chamadas = {}

    class _FonteFalsa:
        def __init__(self, mod):
            self.name = mod
            self.domain = "exemplo"
            self.category = "news"
            self.scraper_module = mod
            self.url = "http://exemplo"

    def _run_pipeline_espiao(**kw):
        apenas = kw.get("apenas_modulos")
        exceto = kw.get("exceto_modulos")
        chamadas["rodou"] = [
            m for m in modulos
            if (apenas is None or m in apenas) and (exceto is None or m not in exceto)
        ]
        return chamadas["rodou"]

    return _run_pipeline_espiao, chamadas


def test_varredura_de_noticias_pula_as_fontes_de_assembleia(monkeypatch):
    from app import config

    todas = ["infomoney", "vortx", "oliveiratrust", "pentagono", "cvm_rad"]
    roda, _ = _fontes_no_banco(monkeypatch, todas)
    assert roda(exceto_modulos=config.FONTES_LENTAS) == ["infomoney", "cvm_rad"]


def test_rodada_de_assembleias_roda_so_elas(monkeypatch):
    from app import config

    todas = ["infomoney", "vortx", "oliveiratrust", "pentagono", "cvm_rad"]
    roda, _ = _fontes_no_banco(monkeypatch, todas)
    assert roda(apenas_modulos=config.FONTES_LENTAS) == [
        "vortx", "oliveiratrust", "pentagono"]


def test_as_duas_cadencias_cobrem_tudo_sem_sobrepor():
    """Nenhuma fonte pode ficar de fora das duas rotinas (sumiria da coleta
    em silêncio) nem entrar nas duas (coletaria em dobro)."""
    from app import config

    todas = {s["scraper_module"] for s in config.KNOWN_SOURCES}
    noticias = todas - config.FONTES_LENTAS
    assembleias = todas & config.FONTES_LENTAS
    assert noticias | assembleias == todas, "fonte órfã entre as duas rotinas"
    assert not (noticias & assembleias), "fonte rodando nas duas rotinas"
    assert config.FONTES_LENTAS <= todas, (
        f"FONTES_LENTAS cita módulo que não existe em KNOWN_SOURCES: "
        f"{config.FONTES_LENTAS - todas}"
    )


def test_canalenergia_busca_pelo_navegador():
    """O site devolve 403 ao GET desde 11/09/2026. Se alguém voltar o
    coletor para o caminho HTTP puro, ele para de coletar em silêncio -- o
    erro vira só mais uma linha de ERRO no log da rodada."""
    import inspect

    from app.sources import canalenergia

    assert "renderizado=True" in inspect.getsource(canalenergia.fetch)
