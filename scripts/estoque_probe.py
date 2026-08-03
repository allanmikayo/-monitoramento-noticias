"""Diagnóstico ÚNICO (não faz parte do pipeline) pra descobrir por que o
Estoque está vindo vazio com tanta frequência no backfill (24/07/2026,
Allan reportou que não é o esperado). fetch_estoque() engolia a exceção
sem mostrar o motivo real -- corrigido em fetch.py, mas esse script isola
o problema em algumas datas específicas sem precisar esperar o backfill
inteiro terminar (pode rodar num terminal separado, em paralelo).

Confirmado por fora (via ferramenta de busca, no Claude): a página de
estoque do debentures.com.br TEM dado real pra 23/07/2024 (mais de um ano
atrás) -- ou seja, não parece ser um problema de retenção histórica como
foi com o boletim antigo da Anbima. Então ou é falha de rede intermitente
(muitas requisições em sequência no backfill), ou um bug no parsing
(posição fixa de coluna -- ver fetch_estoque) que não se sustenta pra
todas as datas.

Uso:
    python -m scripts.estoque_probe
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.spreads import anbima_api
from app.spreads.fetch import build_session, fetch_estoque

DATAS_TESTE = [
    date.today() - timedelta(days=3),   # recente (dia útil mais próximo)
    date(2025, 9, 1),                    # onde o backfill do Allan está agora
    date(2025, 1, 15),
    date(2024, 7, 23),                   # início do backfill (~2 anos atrás)
]


def main() -> None:
    session = build_session()
    for d in DATAS_TESTE:
        # Pula fim de semana pra não gastar teste com data sem pregão
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        date_str = d.strftime("%d/%m/%Y")
        print(f"\n=== Estoque para {date_str} ===")
        try:
            df = fetch_estoque(session, date_str)
            print(f"[OK] {len(df)} linha(s) — amostra:")
            print(df.head(5).to_string(index=False))
            n_sem_estoque = df["Estoque"].isna().sum()
            print(f"  ({n_sem_estoque} de {len(df)} linhas com Estoque vazio dentro do próprio arquivo)")
        except Exception as e:
            print(f"[FALHOU] {type(e).__name__}: {e}")
            continue

        # NOVO: fetch_estoque() sozinho está OK (visto acima) -- então o
        # problema mais provável é o CRUZAMENTO com a Anbima, se o código
        # de uma fonte não bate com o da outra (ex.: caixa alta/baixa,
        # espaço, hífen). Testa isso direto aqui.
        try:
            deb_rows = anbima_api.fetch_debentures_mercado_secundario(d)
            codigos_anbima = {r.get("codigo_ativo") for r in deb_rows if r.get("codigo_ativo")}
            codigos_estoque = set(df["Código"])
            intersecao = codigos_anbima & codigos_estoque
            so_anbima = list(codigos_anbima - codigos_estoque)[:10]
            print(f"  Anbima: {len(codigos_anbima)} código(s) | Estoque: {len(codigos_estoque)} código(s)")
            print(f"  Cruzam (match direto): {len(intersecao)} ({len(intersecao)/len(codigos_anbima)*100:.0f}% da Anbima)")
            if so_anbima:
                print(f"  Exemplos SÓ na Anbima (sem match no Estoque): {so_anbima}")
        except Exception as e:
            print(f"  [cruzamento com Anbima falhou: {type(e).__name__}: {e} -- confira ANBIMA_CLIENT_ID/SECRET no .env]")


if __name__ == "__main__":
    main()
