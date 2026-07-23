"""Pentágono S.A. DTVM — Assembleias/editais de convocação de debêntures.

Diferente de Vórtx (SPA em Next.js -- precisa de Playwright, ver
vortx.py), o site da Pentágono é um ASP.NET "clássico" server-rendered --
confirmado ao vivo (23/07/2026) que um GET comum já devolve o HTML final
com os documentos, sem precisar de navegador/JavaScript:

1. `GET /Site/Investidores?emissor={nome}` -- busca por emissor, devolve
   uma tabela (Razão Social | Série/Emissão | Ativo) com um link por
   ativo pra `/Site/DetalhesEmissor?ativo={CODIGO}`.
2. `GET /Site/DetalhesEmissor?ativo={CODIGO}&aba=tab-3&tipo=undefined` --
   `aba=tab-3` é a aba "Publicações" (a mesma URL com `aba=tab-2` é
   "Documentos", que traz só a escritura/aditamentos, sem interesse aqui).
   Cada publicação é um `<article>` com um link cujo texto já é o nome do
   arquivo, ex.: "2026.04.24 - EDT de CNV 9ª Emissão - Contrata Padis e
   HOULIHAN - vf Public DC impresso.pdf" (prefixo AAAA.MM.DD parseável).
   O link em si não tem `href` real -- é `onclick="DownloadBinario('ID')"`,
   que só abre `https://www.pentagonotrustee.com.br/Site/DownloadBinario
   ?id=ID` (função JS inspecionada ao vivo) -- então construímos essa URL
   direto, sem precisar executar o onclick.

"Publicações" mistura editais de convocação de AGD/AGT com Fatos
Relevantes -- por pedido do Allan (23/07/2026, "Debêntures primeiro")
filtramos aqui pelo NOME DO ARQUIVO pra pegar só o que parece assembleia
(AGD/AGT/edital de convocação); Fatos Relevantes da Pentágono ficam de
fora por enquanto (o CVM RAD já cobre fato relevante via a CVM diretamente
pra todas as empresas da cobertura, então não é uma lacuna nova).

Custo de rede: como a Pentágono não tem uma página central "todas as
assembleias do mercado" (diferente da Oliveira Trust), a única forma de
achar os documentos por empresa é buscar UMA A UMA as ~96 empresas da
cobertura e abrir cada ativo encontrado -- ~100-250 requisições HTTP por
rodada, dependendo de quantas séries cada empresa tem. Isso é MUITO mais
pesado que as outras fontes (que trazem tudo num único GET/tabela) e pode
ser visto como abuso pelo servidor da Pentágono se rodar a cada 5 minutos
pra sempre -- Allan, se isso virar problema (rate limit, IP bloqueado),
me avisa que a gente reduz a frequência só desta fonte ou troca por uma
lista fixa de ativos já conhecidos em vez de buscar tudo de novo a cada
rodada.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

from bs4 import BeautifulSoup

from .base import RawArticle, brt_to_utc, get, load_coverage_names

logger = logging.getLogger(__name__)

BASE_SITE = "https://www.pentagonotrustee.com.br"
SEARCH_URL = f"{BASE_SITE}/Site/Investidores"
DETALHES_URL = f"{BASE_SITE}/Site/DetalhesEmissor"
DOWNLOAD_URL = f"{BASE_SITE}/Site/DownloadBinario"

# aba=tab-3 -- "Publicacoes" (confirmado ao vivo, 23/07/2026)
ABA_PUBLICACOES = "tab-3"

_NOME_ARQUIVO_DATA_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})\s*-\s*(.+)$")
_ASSEMBLEIA_RE = re.compile(r"\b(AGD|AGT|assemble|convoca[cç][aã]o|edital)\b", re.IGNORECASE)


def _listar_ativos(nome_empresa: str) -> list[tuple[str, str, str]]:
    """Busca por emissor e devolve [(razao_social, serie_emissao, codigo_ativo), ...]."""
    try:
        resp = get(f"{SEARCH_URL}?emissor={quote(nome_empresa)}")
    except Exception as e:  # noqa: BLE001
        logger.warning("pentagono: falha buscando emissor %r: %s", nome_empresa, e)
        return []

    soup = BeautifulSoup(resp.content, "lxml")
    out: list[tuple[str, str, str]] = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        link = cells[2].find("a")
        codigo = (link.get_text(strip=True) if link else cells[2].get_text(strip=True)).strip()
        if not codigo:
            continue
        razao_social = cells[0].get_text(" ", strip=True)
        serie_emissao = cells[1].get_text(" ", strip=True)
        out.append((razao_social, serie_emissao, codigo))
    return out


def _listar_publicacoes(codigo_ativo: str) -> list[dict]:
    try:
        resp = get(f"{DETALHES_URL}?ativo={quote(codigo_ativo)}&aba={ABA_PUBLICACOES}&tipo=undefined")
    except Exception as e:  # noqa: BLE001
        logger.warning("pentagono: falha abrindo publicacoes de %r: %s", codigo_ativo, e)
        return []

    soup = BeautifulSoup(resp.content, "lxml")
    out: list[dict] = []
    for art in soup.find_all("article"):
        link = art.find("a", onclick=True)
        if not link:
            continue
        onclick = link.get("onclick") or ""
        m = re.search(r"DownloadBinario\('(\d+)'\)", onclick)
        if not m:
            continue
        doc_id = m.group(1)
        nome_arquivo = link.get_text(strip=True)
        out.append({"id": doc_id, "nome_arquivo": nome_arquivo})
    return out


def _parse_data_do_nome(nome_arquivo: str) -> datetime | None:
    m = _NOME_ARQUIVO_DATA_RE.match(nome_arquivo)
    if not m:
        return None
    ano, mes, dia, _resto = m.groups()
    try:
        return brt_to_utc(int(ano), int(mes), int(dia))
    except ValueError:
        return None


def fetch(url: str) -> list[RawArticle]:
    nomes_cobertura = load_coverage_names()
    if not nomes_cobertura:
        logger.warning("pentagono: lista de empresas da cobertura vazia -- nada sera' coletado")
        return []

    out: list[RawArticle] = []
    ativos_vistos: set[str] = set()

    for nome_empresa in nomes_cobertura:
        for razao_social, serie_emissao, codigo_ativo in _listar_ativos(nome_empresa):
            if codigo_ativo in ativos_vistos:
                continue
            ativos_vistos.add(codigo_ativo)

            for doc in _listar_publicacoes(codigo_ativo):
                nome_arquivo = doc["nome_arquivo"]
                if not _ASSEMBLEIA_RE.search(nome_arquivo):
                    continue

                published = _parse_data_do_nome(nome_arquivo)
                titulo_bits = [b for b in [razao_social, serie_emissao, codigo_ativo, nome_arquivo] if b]
                out.append(
                    RawArticle(
                        url=f"{DOWNLOAD_URL}?id={doc['id']}",
                        title=" — ".join(titulo_bits),
                        snippet="Pentágono — Publicações (Assembleia/edital de convocação)",
                        published_at=published,
                        article_type="assembleia",
                    )
                )

    return out
