"""O que existe no menu, e o que deixou de existir.

A ABA "BANCO DE DADOS" SAIU (11/09/2026, pedido do Allan). Ela expunha SQL
livre na web, atrás de `require_admin` e de uma lista de barreiras. Desde a
migração para o Postgres próprio da OCI o Allan tem o DBeaver ligado direto
no banco, que faz o mesmo melhor -- e o que não existe não precisa ser
defendido.

Estes testes existem porque remoção é o tipo de mudança que volta sozinha:
basta alguém restaurar um arquivo antigo ou copiar um bloco de navegação de
uma versão anterior. Um link morto no menu é barulho; uma ROTA de SQL livre
que volta sem ninguém perceber é outra coisa.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture()
def anonimo():
    import app.app as A

    return TestClient(A.app, raise_server_exceptions=False)


def test_a_rota_do_banco_nao_existe_mais(anonimo):
    """404, não 302 para o login: a rota tem que ter sumido, não ficado
    escondida atrás da autenticação."""
    r = anonimo.get("/banco", follow_redirects=False)
    assert r.status_code == 404, r.status_code


@pytest.mark.parametrize("rota", ["/api/banco/tabela", "/api/banco/export"])
def test_as_rotas_de_sql_livre_nao_existem_mais(anonimo, rota):
    r = anonimo.get(rota, follow_redirects=False)
    assert r.status_code == 404, f"{rota} respondeu {r.status_code}"


def test_o_menu_nao_tem_mais_o_link():
    base = (RAIZ / "templates" / "base.html").read_text(encoding="utf-8")
    assert "/banco" not in base
    assert "Banco de Dados" not in base


def test_os_arquivos_da_aba_foram_removidos():
    """Arquivo órfão não quebra nada e por isso fica anos no repositório,
    confundindo quem procura de onde vem uma tela."""
    for caminho in (
        "app/spreads/banco_routes.py",
        "templates/banco.html",
        "static/banco.js",
        "tests/test_banco.py",
    ):
        assert not (RAIZ / caminho).exists(), f"{caminho} ainda existe"


def test_o_menu_mantem_as_abas_que_ficaram():
    base = (RAIZ / "templates" / "base.html").read_text(encoding="utf-8")
    for rotulo in ("Repositório", "Notícias", "Spreads", "Balcão B3",
                   "Fontes &amp; Empresas", "Administração"):
        assert rotulo in base, f"sumiu do menu: {rotulo}"
