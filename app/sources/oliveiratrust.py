"""Oliveira Trust — Assembleias (AGD/AGT) de debêntures.

Achado importante (23/07/2026, via inspeção ao vivo do site com o Chrome
do Allan): a página "Central de Documentos" (`/investidor/documentos`) é
só a casca -- os dados de verdade vêm de uma API REST pública, sem
autenticação, hospedada em `services-ft.oliveiratrust.com.br`:

    GET /app/v1/titulos/documentos?page=N&limit=100
        &tipo_ativo=debentures&tipo_documento=Assembleias

Devolve JSON paginado (`data.current_page`, `data.total`, `data.per_page`,
`data.data` = lista de documentos), cada item com `tit` (id do "título" --
a série/emissão), `codigo` (id do documento em si), `data`/`data_formatada`
(a AGD é sempre datada -- diferente de "Relatórios", que só tem `ano`, sem
dia/mês, por isso relatório anual ficou de fora desta primeira versão),
`titulo` (série/emissão), `nomec`/`nomeg` (razão social / nome curto do
emissor).

Como NÃO é possível baixar Playwright pra isso (é uma API JSON de
verdade), a coleta é só `base.get()` (curl_cffi) -- mais rápida e mais
robusta que renderizar a página. Confirmado ao vivo: 1.612 registros de
AGD/AGT de debênture no total, ~40 publicados nos últimos 30 dias (23/06 a
23/07/2026), incluindo pelo menos 1 empresa da cobertura (Hidrovias do
Brasil, AGD de 26/06/2026).

Link do documento: a listagem NÃO tem o link do PDF direto (só um "tit" e
um "codigo"). O link real de download é resolvido em 2 chamadas extras
(só para os itens que batem com a cobertura, pra não gastar requisição à
toa com o resto do mercado):

    GET /app/v1/titulos/{tit}                      -> pega `codigo_operacao`
    GET /app/v1/titulos/fundos/downloads/{codigo_operacao}
        -> lista de {cod, descricao, link} -- `link` é a URL final do PDF
           (via o leitor do próprio site, ex.:
           https://www.oliveiratrust.com.br/portal/leitor/#https://...pdf)

Casa `cod` da lista de downloads com o `codigo` do item da listagem para
achar o link exato. Se não achar (ex.: API mudou de formato), cai no
fallback da página do "título" (`/investidor/ativo?id={tit}&typo=...`).

Relatórios anuais e CRI/CRA ficaram de fora desta primeira versão (pedido
explícito do Allan, 23/07/2026: "Debêntures primeiro, nas 3" fontes) --
CRI/CRA têm o mesmo formato de API (só trocar tipo_ativo=cri/cra), mas o
campo `nomec`/`nomeg` desses documentos é o nome do VEÍCULO da
securitizadora (ex.: "CIA PROVINCIA SEC 42E"), não da empresa devedora --
o casamento por keyword de empresa não vai bater direto, precisa de uma
segunda etapa (abrir o documento/escritura pra achar o devedor real) antes
de valer a pena ligar isso.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .base import RawArticle, brt_to_utc, get, load_coverage_names

logger = logging.getLogger(__name__)

API_BASE = "https://services-ft.oliveiratrust.com.br/app/v1"
ATIVO_PAGE = "https://www.oliveiratrust.com.br/investidor/ativo"

LOOKBACK_DAYS = 40  # folga sobre a janela de 30 dias do dashboard (mesma ideia do cvm_rad.py)
PAGE_LIMIT = 100
MAX_PAGES = 10  # trava de seguranca -- 40 dias de AGD de debenture nunca deveria passar de 3-4 paginas de 100


def _parse_data(data_iso: str | None) -> datetime | None:
    if not data_iso:
        return None
    try:
        ano, mes, dia = (int(x) for x in data_iso.split("-"))
        return brt_to_utc(ano, mes, dia)
    except (ValueError, AttributeError):
        return None


def _resolver_link_real(tit: int, codigo: int, cache_operacao: dict, cache_downloads: dict) -> str | None:
    """Resolve o link direto do PDF via as 2 chamadas extra descritas no
    docstring do módulo. Usa cache (por `tit`) porque vários documentos do
    mesmo título/série reaproveitam a mesma chamada de downloads."""
    try:
        if tit not in cache_operacao:
            resp = get(f"{API_BASE}/titulos/{tit}")
            payload = resp.json()
            registros = payload.get("data") or []
            cache_operacao[tit] = registros[0].get("codigo_operacao") if registros else None
        codigo_operacao = cache_operacao.get(tit)
        if not codigo_operacao:
            return None

        if codigo_operacao not in cache_downloads:
            resp = get(f"{API_BASE}/titulos/fundos/downloads/{codigo_operacao}")
            payload = resp.json()
            cache_downloads[codigo_operacao] = payload.get("data") or []
        for item in cache_downloads[codigo_operacao]:
            if item.get("cod") == codigo:
                return item.get("link")
    except Exception as e:  # noqa: BLE001
        logger.warning("oliveiratrust: falha resolvendo link real (tit=%s, codigo=%s): %s", tit, codigo, e)
    return None


def fetch(url: str) -> list[RawArticle]:
    from ..filter import match_keywords

    nomes_cobertura = load_coverage_names()
    if not nomes_cobertura:
        logger.warning("oliveiratrust: lista de empresas da cobertura vazia -- nada sera' coletado")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    cache_operacao: dict[int, int | None] = {}
    cache_downloads: dict[int, list] = {}

    out: list[RawArticle] = []
    for page_num in range(1, MAX_PAGES + 1):
        try:
            resp = get(
                f"{API_BASE}/titulos/documentos",
                params={
                    "page": page_num,
                    "limit": PAGE_LIMIT,
                    "tipo_ativo": "debentures",
                    "tipo_documento": "Assembleias",
                },
            )
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("oliveiratrust: falha na pagina %d: %s", page_num, e)
            break

        registros = (payload.get("data") or {}).get("data") or []
        if not registros:
            break

        oldest_on_page: datetime | None = None
        for item in registros:
            published = _parse_data(item.get("data"))
            if published and (oldest_on_page is None or published < oldest_on_page):
                oldest_on_page = published
            if published and published < cutoff:
                continue

            nomec = item.get("nomec") or ""
            nomeg = item.get("nomeg") or ""
            empresa_raw = f"{nomec} {nomeg}".strip()
            if not match_keywords(empresa_raw, nomes_cobertura):
                continue

            tit = item.get("tit")
            codigo = item.get("codigo")
            link = None
            if tit and codigo:
                link = _resolver_link_real(tit, codigo, cache_operacao, cache_downloads)
            if not link:
                link = f"{ATIVO_PAGE}?id={tit}&typo=DEBENTURES#{codigo}"

            titulo_serie = item.get("titulo") or ""
            arquivo_nome = (item.get("arquivo_nome") or "").strip()
            empresa_exibicao = nomec or nomeg
            titulo_bits = [b for b in [empresa_exibicao, titulo_serie, arquivo_nome] if b]

            out.append(
                RawArticle(
                    url=link,
                    title=" — ".join(titulo_bits),
                    snippet="Oliveira Trust — Assembleia de debenturistas",
                    published_at=published,
                    article_type="assembleia",
                )
            )

        total = (payload.get("data") or {}).get("total") or 0
        if page_num * PAGE_LIMIT >= total:
            break
        if oldest_on_page and oldest_on_page < cutoff:
            break

    return out
