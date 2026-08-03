"""Diagnóstico ÚNICO (não faz parte do pipeline) pra testar a API oficial
da Anbima (developers.anbima.com.br) antes de reescrever
app/spreads/fetch.py pra usá-la — ver CLAUDE.md, seção "Spreads de
debêntures". Roda o fluxo OAuth2 (client_credentials), depois faz algumas
chamadas de teste e imprime a resposta CRUA (JSON) — é só isso, não grava
nada no banco.

Por que existe: eu (Claude) não tenho como testar esta API do sandbox onde
escrevo código (rede bloqueada pro domínio da Anbima) e a documentação
pública não mostra um exemplo real de resposta (nomes de campo podem vir
com casing diferente do documentado, paginação pode existir, etc.). Rodar
isso primeiro evita eu escrever um parser "no escuro" contra um formato
que eu só estou adivinhando pela doc em texto.

Uso:
    python -m scripts.anbima_api_probe

Pré-requisito: ANBIMA_CLIENT_ID e ANBIMA_CLIENT_SECRET no seu .env
(gerados em https://admin-developers.anbima.com.br/api-portal/user).
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("ANBIMA_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("ANBIMA_CLIENT_SECRET", "")

# Produção -- ver DEPLOY.md/CLAUDE.md se precisarmos trocar pro Sandbox
# (https://api-sandbox.anbima.com.br) em algum momento de teste.
BASE_URL = "https://api.anbima.com.br"


def get_access_token() -> str:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise SystemExit(
            "ANBIMA_CLIENT_ID / ANBIMA_CLIENT_SECRET não configurados no .env -- "
            "veja .env.example."
        )
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        f"{BASE_URL}/oauth/access-token",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {basic}"},
        json={"grant_type": "client_credentials"},
        timeout=30,
    )
    print(f"[oauth] status={resp.status_code}")
    print(f"[oauth] corpo bruto: {resp.text[:1000]}")
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise SystemExit(f"Resposta do OAuth não trouxe access_token: {data}")
    print(f"[oauth] access_token obtido (expira em {data.get('expires_in')}s)")
    return token


def call(path: str, access_token: str, params: dict | None = None) -> None:
    url = f"{BASE_URL}{path}"
    headers = {"client_id": CLIENT_ID, "access_token": access_token}
    resp = requests.get(url, headers=headers, params=params or {}, timeout=30)
    print(f"\n=== GET {path} params={params} ===")
    print(f"status={resp.status_code}")
    try:
        parsed = resp.json()
        print(json.dumps(parsed, indent=2, ensure_ascii=False)[:4000])
        if isinstance(parsed, dict) and "content" in parsed:
            print(f"(tem {len(parsed.get('content', []))} item(ns) em 'content' -- pode ter mais em paginação)")
        elif isinstance(parsed, list):
            print(f"(lista com {len(parsed)} item(ns))")
    except ValueError:
        print(f"corpo não é JSON: {resp.text[:1000]}")


def main() -> None:
    token = get_access_token()

    # 1) Debêntures, mercado secundário, SEM data (deve trazer a data mais recente)
    call("/feed/precos-indices/v1/debentures/mercado-secundario", token)

    # 2) Debêntures, mercado secundário, pedindo uma data de ~2 anos atrás --
    #    é o teste que importa de verdade: se vier 200 com dado, a API tem
    #    profundidade histórica boa o suficiente pro backfill de 2 anos que
    #    o Allan pediu. Se vier 404/erro/vazio, a API também é limitada e
    #    precisamos rever a estratégia de novo.
    call("/feed/precos-indices/v1/debentures/mercado-secundario", token, params={"data": "2024-07-23"})

    # 3) Curva de juros de títulos públicos (NTN-B), sem data
    call("/feed/precos-indices/v1/titulos-publicos/curvas-juros", token)

    # 4) Mesma curva, ~2 anos atrás
    call("/feed/precos-indices/v1/titulos-publicos/curvas-juros", token, params={"data": "2024-07-23"})

    # 5) NOVO: mercado secundário de títulos públicos (LTN/NTN-B/NTN-F etc.)
    #    -- taxa indicativa DISCRETA por vencimento, o equivalente exato da
    #    aba "NTN-B" do .xls antigo (rates[data_vencimento] = taxa). Preciso
    #    ver o valor real de "tipo_titulo" pra filtrar só NTN-B (a doc não
    #    lista os códigos possíveis).
    call("/feed/precos-indices/v1/titulos-publicos/mercado-secundario-TPF", token)

    # 6) Mesmo endpoint, ~2 anos atrás -- confirma profundidade histórica
    #    também aqui (é um endpoint separado do de debêntures, pode ter
    #    retenção diferente).
    call(
        "/feed/precos-indices/v1/titulos-publicos/mercado-secundario-TPF",
        token,
        params={"data": "2024-07-23"},
    )


if __name__ == "__main__":
    main()
