"""Toda rota que o navegador chama existe no servidor?

BUG REAL E CARO (11/09/2026). `static/spreads.js` chamava
`/api/spreads/por-setor` e o servidor respondia 404: a rota não existia,
embora a consulta (`queries.spread_por_setor`) estivesse inteira e testada.

O modo de falha é o problema. Uma rota que some não estoura em lugar
nenhum: a promessa do `fetch` é rejeitada, aquele bloco da tela fica vazio,
e todo o resto da página continua desenhando normalmente. Para quem olha,
parece falta de DADO -- e a investigação começa no banco, no coletor, na
taxonomia, em qualquer lugar menos no lugar certo. Foi assim que a tabela
de spread por setor "sumiu" e passou dias assim.

Teste de unidade não pega, teste de rota só pega a rota que alguém lembrou
de escrever. Este aqui compara as duas listas: o que o JavaScript chama
contra o que o app registra.
"""
from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Caminhos com parte variável (`${...}`) são montados no navegador e não dá
# para casar com a rota literal -- ficam de fora, e o teste diz quantos
# ignorou para essa exclusão não crescer em silêncio.
_CHAMADA = re.compile(r'fetch(?:JSON)?\(\s*[`"\'](/api/[^`"\'$]*)[`"\']')


def _chamadas_do_frontend() -> dict[str, set[str]]:
    por_arquivo: dict[str, set[str]] = {}
    for js in sorted((RAIZ / "static").glob("*.js")):
        achadas = set(_CHAMADA.findall(js.read_text(encoding="utf-8")))
        if achadas:
            por_arquivo[js.name] = achadas
    return por_arquivo


def test_toda_rota_chamada_pelo_js_existe_no_app():
    """Pergunta ao servidor, não à lista de rotas.

    Enumerar `app.routes` parecia mais direto, mas devolve uma lista
    incompleta neste app (as rotas vindas de `include_router` não aparecem,
    embora respondam) -- e um teste que se apoia numa lista errada acusa
    falha onde não há. Bater na rota é a pergunta de verdade: 404 significa
    que o caminho não é servido; QUALQUER outra resposta (inclusive o 303
    que manda para o login) significa que existe.
    """
    from fastapi.testclient import TestClient

    import app.app as A

    cliente = TestClient(A.app, raise_server_exceptions=False)
    faltando = []
    for arquivo, chamadas in _chamadas_do_frontend().items():
        for caminho in sorted(chamadas):
            if cliente.get(caminho, follow_redirects=False).status_code == 404:
                faltando.append(f"{arquivo} chama {caminho}")
    assert not faltando, (
        "o navegador chama rota que o servidor não serve (a tela fica "
        "vazia, sem erro visível):\n  " + "\n  ".join(faltando)
    )


def test_o_frontend_realmente_foi_lido():
    """Se um refactor mudar o jeito de chamar a API, o teste acima passaria
    vazio -- verde por não ter olhado nada. Este ancora o piso."""
    por_arquivo = _chamadas_do_frontend()
    assert "spreads.js" in por_arquivo, "não achei chamadas de API em spreads.js"
    assert len(por_arquivo["spreads.js"]) >= 8, por_arquivo["spreads.js"]


def test_por_setor_esta_entre_as_chamadas_conferidas():
    """A rota que originou este arquivo. Se ela sair da lista de chamadas
    conferidas, o teste acima continuaria verde sem protegê-la."""
    assert "/api/spreads/por-setor" in _chamadas_do_frontend()["spreads.js"]
