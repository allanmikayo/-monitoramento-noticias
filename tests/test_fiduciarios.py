"""Agentes fiduciários reescritos para HTTP direto (03/11/2026).

Sem rede: as respostas abaixo reproduzem o formato visto ao vivo nas APIs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.sources import oliveiratrust, pentagono, vortx
from app.sources.fiduciarios import Cobertura, titulo


def _cob():
    return Cobertura(
        nomes=["Light", "Sabesp"], nome_por_chave={"Light": "Light", "Sabesp": "Sabesp"},
        nome_por_cnpj8={"60444437": "Light"}, nome_por_ticker={"SBSPF3": "Sabesp", "22K1689687": "Light"},
    )


def test_vortx_empresa_por_ticker_cnpj_e_nome():
    cob = _cob()
    deb = {"operation_type_id": "3", "cnpj_emissora": "60.444.437/0001-46", "codIf": "XYZ1", "apelido": "X", "emissor": "Y"}
    cri = {"operation_type_id": "1", "cnpj_emissora": "08.769.451/0001-08", "codIf": "22K1689687", "apelido": "Z", "emissor": "W"}
    cri_cnpj_securitizadora = dict(cri, codIf="OUTRO", cnpj_emissora="60.444.437/0001-46")
    nome = {"operation_type_id": "5", "cnpj_emissora": "", "codIf": "", "apelido": "LIGHT", "emissor": "VIRGO"}
    assert vortx._empresa_da_operacao(deb, cob) == "Light"
    assert vortx._empresa_da_operacao(cri, cob) == "Light"
    # CNPJ só vale para debênture: em CRI/CRA é o da securitizadora.
    assert vortx._empresa_da_operacao(cri_cnpj_securitizadora, cob) is None
    assert vortx._empresa_da_operacao(nome, cob) == "Light"


def test_vortx_datas():
    assert vortx._data("2024-05-23T00:00:00.000Z", so_dia=True).isoformat() == "2024-05-23T15:00:00+00:00"
    assert vortx._data("2024-04-23T10:30:13.673").isoformat() == "2024-04-23T13:30:00+00:00"  # BRT -> UTC


def test_vortx_documentos_filtra_janela(monkeypatch):
    agora = datetime.now(timezone.utc)
    recente = (agora - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000")
    antigo = (agora - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%S.000")
    respostas = {
        "fatos-relevantes": {"relevantFacts": [{"id": 1, "title": "Aviso", "createdDate": recente, "files": [{"url": "https://x/f.pdf"}]}]},
        "documentos-por-tipo/1": [{"type": "Relatório Garantias", "documents": [
            {"url": "https://x/a.pdf", "description": "Garantias.pdf", "uploadTimestamp": recente},
            {"url": "https://x/b.pdf", "description": "Velho.pdf", "uploadTimestamp": antigo}]}],
        "documentos-por-tipo/2": [{"type": "Assembleia Investidores", "documents": [
            {"url": "https://x/c.pdf", "description": "AGD.pdf", "uploadTimestamp": recente}]}],
    }
    monkeypatch.setattr(vortx, "get_json", lambda u, **k: next(v for c, v in respostas.items() if u.endswith(c)))
    arts = vortx._documentos_da_operacao({"codigo": 91018, "codIf": "LIGHD2", "emissao": "22"}, "Light",
                                         agora - timedelta(days=40))
    assert [a.url for a in arts] == ["https://x/f.pdf", "https://x/a.pdf"]
    assert arts[1].title == "Light — Relatório Garantias — Garantias.pdf (LIGHD2 22ª emissão)"


def test_oliveiratrust_link_direto():
    assert oliveiratrust._link_direto("https://www.oliveiratrust.com.br/portal/leitor/#https://a/b.pdf") == "https://a/b.pdf"
    assert oliveiratrust._link_direto("https://www.oliveiratrust.com.br/portal/leitor/https://a/b.pdf") == "https://a/b.pdf"
    assert oliveiratrust._link_direto("https://a/c.pdf") == "https://a/c.pdf"


def test_pentagono_pu_categoria_data():
    html = b"""<table><tr><th>Data</th><th>Codigo</th></tr>
    <tr><td>03/11/2026</td><td>SBSPF3</td><td>1</td></tr><tr><td>03/11/2026</td><td>12E0031990</td></tr></table>"""
    assert pentagono._codigos_da_pagina(html) == ["SBSPF3", "12E0031990"]
    assert pentagono._categoria("2026.09.21 - Edital de convocação AGD de 13.10.2026.pdf", "x") == "Edital de convocação"
    assert pentagono._categoria("2026.10.09 - Análise de Quóruns (AGD 09.10.2026).pdf", "x") == "Assembleia"
    assert pentagono._categoria("2026.10.13 - Termo de Não Instalação.pdf", "x") == "Assembleia"
    assert pentagono._categoria("2025.02.03 - 1º Aditamento a Escritura.pdf", "x") == "Aditamento"
    assert pentagono._parse_data_do_nome("2026.10.09 - X.pdf").date().isoformat() == "2026-10-09"
    assert pentagono._parse_data_do_nome("sem data.pdf") is None


def test_titulo():
    assert titulo("Sabesp", "Ata", "AGD.pdf (SBSPF3)") == "Sabesp — Ata — AGD.pdf (SBSPF3)"
