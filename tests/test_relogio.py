"""Relógio das coletas frequentes (scripts/relogio.py, 10/11/2026)."""
from datetime import datetime, timedelta

from scripts import relogio as R

TER_11H = datetime(2026, 11, 10, 11, 0, tzinfo=R.BRT)      # terça, pregão
SAB_11H = datetime(2026, 11, 14, 11, 0, tzinfo=R.BRT)      # sábado
TER_20H = datetime(2026, 11, 10, 20, 0, tzinfo=R.BRT)      # terça, depois do pregão


def test_primeira_volta_dispara_os_dois_no_pregao():
    assert R.devidos(TER_11H, {}) == ["scrape.yml", "b3_trades.yml"]


def test_b3_so_no_pregao():
    assert R.devidos(SAB_11H, {}) == ["scrape.yml"]
    assert R.devidos(TER_20H, {}) == ["scrape.yml"]


def test_respeita_o_intervalo_de_cada_um():
    ultimos = {"scrape.yml": TER_11H, "b3_trades.yml": TER_11H}
    assert R.devidos(TER_11H + timedelta(minutes=14), ultimos) == []
    assert R.devidos(TER_11H + timedelta(minutes=15), ultimos) == ["scrape.yml"]
    assert R.devidos(TER_11H + timedelta(minutes=30), ultimos) == ["scrape.yml", "b3_trades.yml"]
