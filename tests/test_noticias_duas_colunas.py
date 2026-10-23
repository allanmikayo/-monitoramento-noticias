"""Página de Notícias em duas colunas (22/10/2026): links de documento CVM
(abrir/baixar direto no RAD) e consulta por vários tipos de uma vez."""
from types import SimpleNamespace

from app import app as app_mod, config


def _art(tipo, url, id_=7):
    return SimpleNamespace(article_type=tipo, url=url, id=id_)


def test_cvm_com_protocolo_ganha_abrir_e_baixar_no_enet():
    abrir, baixar = app_mod._links_documento(_art(
        "fato_relevante",
        "https://www.rad.cvm.gov.br/ENETWeb/frmExibirArquivoIPEExterno.aspx?NumeroProtocoloEntrega=1545488"))
    assert abrir == ("https://www.rad.cvm.gov.br/ENET/frmExibirArquivoIPEExterno.aspx"
                     "?NumeroProtocoloEntrega=1545488")
    assert "frmDownloadDocumento.aspx" in baixar and "numProtocolo=1545488" in baixar


def test_cvm_sem_protocolo_continua_na_busca_sem_download():
    assert app_mod._links_documento(_art("fato_relevante", "https://www.rad.cvm.gov.br/x#123")) == (
        config.CVM_SEARCH_URL, None)


def test_assembleia_abre_o_arquivo_e_baixa_pelo_proxy():
    abrir, baixar = app_mod._links_documento(_art("assembleia", "https://x.s3.amazonaws.com/a.pdf", 42))
    assert abrir == "https://x.s3.amazonaws.com/a.pdf" and baixar == "/api/documentos/42/baixar"


def test_noticia_nao_tem_download():
    assert app_mod._links_documento(_art("news", "https://valor.globo.com/x")) == ("https://valor.globo.com/x", None)


def test_formulario_fre_abre_no_enet_sem_download():
    abrir, baixar = app_mod._links_documento(_art(
        "fato_relevante",
        "https://rad.cvm.gov.br/ENETWeb/frmGerenciaPaginaFRE.aspx?NumeroSequencialDocumento=159533"
        "&CodigoTipoInstituicao=1#02471-6-202607161051-frmGerenciaPaginaFRE.aspx"))
    assert abrir == ("https://www.rad.cvm.gov.br/ENET/frmGerenciaPaginaFRE.aspx"
                     "?NumeroSequencialDocumento=159533&CodigoTipoInstituicao=1")
    assert baixar is None


def test_pagina_tem_documentos_e_destaque_de_ratings():
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "templates" / "dashboard.html").read_text(encoding="utf-8")
    assert ">Documentos</h2>" in html and 'id="ratings-destaque"' in html
    assert 'id="imprensa-tabs"' not in html          # filtro Notícias/Ratings saiu
