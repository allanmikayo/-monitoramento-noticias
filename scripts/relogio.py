"""Relógio das coletas frequentes (10/11/2026).

POR QUE EXISTE. O `schedule:` nativo do GitHub não cumpre cadência curta:
na semana de 03 a 10/11/2026 a varredura de notícias (pedida a cada 15 min)
rodou 27 vezes, ~4 por dia, e o negócio a negócio da B3 (a cada 30 min no
pregão) rodou 8 vezes. O relay externo (cron-job.org -> /api/cron-trigger)
resolveria, mas não está configurado desde 04/11.

COMO FUNCIONA. O `relogio.yml` roda este script, que fica de pé por quase 6
horas (o teto de um job do Actions) e, a cada minuto, aciona por
`workflow_dispatch`:
  * `scrape.yml` a cada 15 min;
  * `b3_trades.yml` a cada 30 min, só em dia útil das 9h às 18h (BRT).
Basta o GitHub entregar UMA execução agendada do relógio a cada ~6 h para a
cadência ficar contínua -- e isso ele entrega. O `concurrency` do workflow
deixa uma execução na fila enquanto a outra roda, então o próximo relógio
assume assim que o anterior termina.

Assembleias, spreads, curvas e o resto continuam no `schedule:` próprio:
são diários ou 3x ao dia, e lá o atraso do GitHub não faz diferença.

O relay do cron-job.org, se for religado, convive: os workflows acionados
têm `concurrency` e não rodam dois ao mesmo tempo.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

BRT = ZoneInfo("America/Sao_Paulo")
NOTICIAS = ("scrape.yml", timedelta(minutes=15))
B3 = ("b3_trades.yml", timedelta(minutes=30))
B3_ABRE, B3_FECHA = 9, 18           # mesmo horário de app.py (_B3_MARKET_*)
DURACAO = timedelta(hours=5, minutes=45)   # o job tem teto de 6 h
PASSO = 60                           # segundos entre verificações


def pregao_aberto(agora: datetime) -> bool:
    return agora.weekday() < 5 and B3_ABRE <= agora.hour < B3_FECHA


def devidos(agora: datetime, ultimos: dict[str, datetime | None]) -> list[str]:
    """Workflows que já passaram do intervalo desde o último disparo."""
    saida = []
    for arquivo, intervalo in (NOTICIAS, B3):
        if arquivo == B3[0] and not pregao_aberto(agora):
            continue
        ultimo = ultimos.get(arquivo)
        if ultimo is None or agora - ultimo >= intervalo:
            saida.append(arquivo)
    return saida


def disparar(arquivo: str, repo: str, token: str, ref: str) -> tuple[bool, str]:
    try:
        r = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/{arquivo}/dispatches",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"},
            json={"ref": ref}, timeout=20)
        return r.status_code == 204, f"{r.status_code} {r.text[:200]}"
    except requests.RequestException as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    fim = datetime.now(BRT) + DURACAO
    ultimos: dict[str, datetime | None] = {}
    disparos, falhas = 0, 0
    while datetime.now(BRT) < fim:
        agora = datetime.now(BRT)
        for arquivo in devidos(agora, ultimos):
            ok, msg = disparar(arquivo, repo, token, ref)
            print(f"{agora:%d/%m %H:%M} {arquivo}: {'ok' if ok else 'FALHOU ' + msg}", flush=True)
            if ok:
                ultimos[arquivo] = agora
                disparos += 1
            else:
                falhas += 1
                print(f"::warning title=relogio::{arquivo} não foi acionado: {msg}", flush=True)
        time.sleep(PASSO)
    print(f"::notice title=relogio::{disparos} disparos, {falhas} falhas", flush=True)
    return 0 if disparos or not falhas else 1


if __name__ == "__main__":
    sys.exit(main())
