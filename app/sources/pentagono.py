"""Pentágono — documentos de agente fiduciário (DEB, CRI, CRA). Reescrito em
03/11/2026.

A busca por emissor do site (`/Site/Investidores?emissor=X`) está quebrada
até no navegador ("Houve problemas no seu acesso"), e era dela que a versão
anterior dependia: voltava zero sempre. Dois caminhos que funcionam por GET
simples (conferidos ao vivo, ver CLAUDE.md):

  1. Universo de papéis da Pentágono: a consulta de preços unitários lista
     todos os códigos acompanhados numa data, 50 por página
     (`/PrecosUnitarios/Consulta?DataInicial=AAAA-MM-DD&pagina=N`, ~35
     páginas). Cruza com os tickers da cobertura (debêntures e CRI/CRA).
  2. Página de cada papel: `/Site/DetalhesEmissor?ativo=TICKER&aba=tab-2`
     (Documentos: atas, análise de quórum, termos, escrituras, aditamentos)
     e `aba=tab-3` (Publicações: editais). Cada arquivo tem a data no
     começo do nome ("2026.10.09 - ...") e o id em `DownloadBinario('N')`.

Só entra documento com data no nome dentro de `LOOKBACK_DAYS`.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from . import base
from .base import RawArticle, brt_to_utc, get
from .fiduciarios import carregar_cobertura, corte, titulo

logger = logging.getLogger(__name__)

BASE_SITE = "https://www.pentagonotrustee.com.br"
PU_URL = f"{BASE_SITE}/PrecosUnitarios/Consulta"
DETALHES_URL = f"{BASE_SITE}/Site/DetalhesEmissor"
DOWNLOAD_URL = f"{BASE_SITE}/Site/DownloadBinario"
ABAS = {"tab-2": "Documento", "tab-3": "Publicação"}
MAX_PAGINAS_PU = 80
TRABALHADORES = 6

_NOME_ARQUIVO_DATA_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})\s*-\s*(.+)$")
_CATEGORIAS = [
    (re.compile(r"edital|convoca", re.I), "Edital de convocação"),
    (re.compile(r"\bata\b", re.I), "Ata"),
    (re.compile(r"qu[oó]rum|n[aã]o instala|\bAGD\b|\bAGT\b|assembl", re.I), "Assembleia"),
    (re.compile(r"fato relevante", re.I), "Fato relevante"),
    (re.compile(r"aviso", re.I), "Aviso"),
    (re.compile(r"aditamento", re.I), "Aditamento"),
    (re.compile(r"relat[oó]rio", re.I), "Relatório"),
    (re.compile(r"escritura", re.I), "Escritura"),
]


def _parse_data_do_nome(nome_arquivo: str) -> datetime | None:
    m = _NOME_ARQUIVO_DATA_RE.match(nome_arquivo or "")
    if not m:
        return None
    ano, mes, dia, _resto = m.groups()
    try:
        return brt_to_utc(int(ano), int(mes), int(dia))
    except ValueError:
        return None


def _categoria(nome: str, padrao: str) -> str:
    for rx, cat in _CATEGORIAS:
        if rx.search(nome):
            return cat
    return padrao


def _codigos_da_pagina(html: bytes) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) >= 2:
            c = tds[1].get_text(strip=True).upper()
            if c:
                out.append(c)
    return out


def _sessao():
    """Sem cookie a consulta de PU devolve HTTP 500 (NullReferenceException no
    servidor; conferido no navegador, 03/11/2026). Abre a sessão como o site:
    idioma (`/Language/Change/pt-BR`) e a página de PU."""
    kw = {"impersonate": base.IMPERSONATE} if base._USE_CURL_CFFI else {}
    s = base._http.Session(**kw)
    s.headers.update(base.DEFAULT_HEADERS)
    for caminho in ("/Language/Change/pt-BR", "/PrecosUnitarios"):
        s.get(f"{BASE_SITE}{caminho}", timeout=base.REQUEST_TIMEOUT).raise_for_status()
    s.headers["Referer"] = f"{BASE_SITE}/PrecosUnitarios"
    logger.info("pentagono: cookies da sessão: %s", sorted(s.cookies.keys()))
    return s


def universo() -> set[str]:
    """Códigos acompanhados pela Pentágono (consulta de PU)."""
    try:
        sessao = _sessao()
    except Exception as e:  # noqa: BLE001
        logger.warning("pentagono: não abri a página de PU: %s", e)
        return set()
    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    for atras in range(0, 6):
        dia = (hoje - timedelta(days=atras)).isoformat()
        codigos: set[str] = set()
        for pagina in range(1, MAX_PAGINAS_PU + 1):
            try:
                r = sessao.get(PU_URL, params={"DataInicial": dia, "pagina": pagina}, timeout=base.REQUEST_TIMEOUT)
                r.raise_for_status()
                lote = _codigos_da_pagina(r.content)
            except Exception as e:  # noqa: BLE001
                logger.warning("pentagono: PU %s pagina %d falhou: %s", dia, pagina, e)
                break
            novos = set(lote) - codigos
            if not novos:
                break
            codigos |= novos
        if codigos:
            logger.info("pentagono: %d códigos na consulta de PU de %s", len(codigos), dia)
            return codigos
    return set()


def _listar_documentos(codigo_ativo: str, aba: str) -> list[dict]:
    resp = get(f"{DETALHES_URL}?ativo={quote(codigo_ativo)}&aba={aba}&tipo=undefined")
    soup = BeautifulSoup(resp.content, "lxml")
    out = []
    for art in soup.find_all("article"):
        link = art.find("a", onclick=True)
        if not link:
            continue
        m = re.search(r"DownloadBinario\('(\d+)'\)", link.get("onclick") or "")
        if m:
            out.append({"id": m.group(1), "nome_arquivo": link.get_text(" ", strip=True)})
    return out


def fetch(url: str) -> list[RawArticle]:
    cob = carregar_cobertura()
    if not cob.nome_por_ticker:
        logger.warning("pentagono: nenhum ticker da cobertura -- nada será coletado")
        return []
    nossos = sorted(universo() & set(cob.nome_por_ticker))
    logger.info("pentagono: %d tickers da cobertura acompanhados pela Pentágono", len(nossos))
    limite = corte()

    def _do_ticker(codigo: str) -> list[RawArticle]:
        empresa = cob.empresa_do_ticker(codigo) or codigo
        out = []
        for aba, padrao in ABAS.items():
            try:
                docs = _listar_documentos(codigo, aba)
            except Exception as e:  # noqa: BLE001
                logger.warning("pentagono: %s %s falhou: %s", codigo, aba, e)
                continue
            for d in docs:
                quando = _parse_data_do_nome(d["nome_arquivo"])
                if not quando or quando < limite:
                    continue
                out.append(RawArticle(
                    url=f"{DOWNLOAD_URL}?id={d['id']}",
                    title=titulo(empresa, _categoria(d["nome_arquivo"], padrao), f"{d['nome_arquivo']} ({codigo})"),
                    snippet="Pentágono — documento do papel",
                    published_at=quando,
                    article_type="assembleia",
                ))
        return out

    with ThreadPoolExecutor(TRABALHADORES) as ex:
        artigos = [a for lote in ex.map(_do_ticker, nossos) for a in lote]

    # O mesmo arquivo aparece em cada série da emissão, às vezes com id
    # diferente: fica um por (empresa, nome do arquivo).
    vistos: set = set()
    out = []
    for a in artigos:
        chave = (a.title.rsplit(" (", 1)[0], a.published_at)
        if a.url not in vistos and chave not in vistos:
            vistos.update({a.url, chave})
            out.append(a)
    return out
