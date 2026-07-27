"""Cliente da API oficial da Anbima (developers.anbima.com.br) — substitui
o scraping do boletim `.xls` diário (retenção de só ~5 dias úteis,
confirmado pelo Allan em 23/07/2026: "No site da Anbima só fica disponível
dos últimos 5 dias úteis") por uma API REST com profundidade histórica
confirmada manualmente (ver scripts/anbima_api_probe.py — rodado por Allan
em 24/07/2026, `data=2024-07-23` devolveu 940 debêntures e 49 títulos
públicos, ambos no mesmo formato da data mais recente — ~2 anos de
profundidade confirmados nos dois endpoints usados aqui).

Dois endpoints usados (ver CLAUDE.md, seção "Spreads de debêntures"):
- /feed/precos-indices/v1/debentures/mercado-secundario — taxa indicativa,
  PU, duration etc. por debênture, já separado por `grupo` ("DI SPREAD" /
  "IPCA SPREAD") e já trazendo `referencia_ntnb` quando aplicável.
- /feed/precos-indices/v1/titulos-publicos/mercado-secundario-TPF — taxas
  discretas por título público (LTN/LFT/NTN-B/NTN-F...), filtramos
  `tipo_titulo == "NTN-B"` (confirmado via probe) — é o equivalente exato
  da aba "NTN-B" do `.xls` antigo (tabela de taxa por vencimento), NÃO a
  curva paramétrica de /titulos-publicos/curvas-juros (essa outra devolve
  parâmetros de Nelson-Siegel-Svensson, exigiria reimplementar a fórmula —
  desnecessário já que este outro endpoint dá a taxa pronta).

Autenticação: OAuth2 client_credentials. Cadastro em
https://admin-developers.anbima.com.br/api-portal/user — client_id/secret
SÓ no .env (ANBIMA_CLIENT_ID / ANBIMA_CLIENT_SECRET), nunca no chat.
"""
from __future__ import annotations

import base64
import os
import time
from datetime import date

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.anbima.com.br"

# Cache de token em memória de processo -- evita um round-trip de OAuth por
# dia no backfill (são centenas de dias úteis num backfill de 2 anos).
_token_cache: dict = {"token": None, "expires_at": 0.0}


def _client_credentials() -> tuple[str, str]:
    client_id = os.getenv("ANBIMA_CLIENT_ID", "")
    client_secret = os.getenv("ANBIMA_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "ANBIMA_CLIENT_ID / ANBIMA_CLIENT_SECRET não configurados no .env "
            "-- veja .env.example."
        )
    return client_id, client_secret


def get_access_token(force_refresh: bool = False) -> str:
    """Token válido por 3600s (confirmado via probe) -- renova com 60s de
    folga antes de expirar, ou sob demanda se o servidor devolver 401."""
    now = time.time()
    if not force_refresh and _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    client_id, client_secret = _client_credentials()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        f"{BASE_URL}/oauth/access-token",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {basic}"},
        json={"grant_type": "client_credentials"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + float(data.get("expires_in", 3600))
    return token


def _get(path: str, params: dict | None = None) -> list[dict]:
    client_id, _ = _client_credentials()
    token = get_access_token()
    headers = {"client_id": client_id, "access_token": token}
    resp = requests.get(f"{BASE_URL}{path}", headers=headers, params=params or {}, timeout=60)
    if resp.status_code == 401:
        # Token pode ter expirado antes da hora esperada -- tenta renovar
        # uma vez só (evita loop infinito se a credencial em si é inválida).
        token = get_access_token(force_refresh=True)
        headers["access_token"] = token
        resp = requests.get(f"{BASE_URL}{path}", headers=headers, params=params or {}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    # Os dois endpoints usados aqui devolvem lista JSON crua (confirmado via
    # probe) -- o `isinstance` é só defesa contra a API um dia passar a
    # envelopar em {"content": [...]} (outros endpoints da Anbima fazem
    # isso, mas não os dois que testamos).
    return data if isinstance(data, list) else data.get("content", [])


def fetch_debentures_mercado_secundario(dt: date | None = None) -> list[dict]:
    """Debêntures no mercado secundário na data `dt`. [] se não houver
    publicação (feriado/fim de semana/data sem pregão) -- comportamento
    observado no probe: a API respondeu 200 mesmo pra datas testadas, então
    tratamos lista vazia como "sem dado" em vez de erro.

    `dt=None` (parâmetro omitido) -- a própria API devolve a data mais
    recente disponível (documentado e confirmado via probe); usado por
    `detect_latest_published_date` pra achar o último dia publicado sem
    precisar tentar dia a dia."""
    params = {"data": dt.isoformat()} if dt is not None else None
    return _get("/feed/precos-indices/v1/debentures/mercado-secundario", params=params)


def fetch_titulos_publicos_mercado_secundario(dt: date) -> list[dict]:
    """LTN/LFT/NTN-B/NTN-F etc. na data `dt` (taxa discreta por
    vencimento) -- usar `tipo_titulo == "NTN-B"` pra filtrar."""
    return _get(
        "/feed/precos-indices/v1/titulos-publicos/mercado-secundario-TPF",
        params={"data": dt.isoformat()},
    )
