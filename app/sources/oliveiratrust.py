"""Oliveira Trust — documentos de agente fiduciário (DEB, CRI, CRA).
Reescrito em 03/11/2026 (mapa da API no CLAUDE.md).

Lista central: `services-ft.oliveiratrust.com.br/app/v1/titulos/documentos`,
sem autenticação.
  * Assembleias: filtro `data_inicial`/`data_final` sobre a data da
    assembleia. Pega os últimos `LOOKBACK_DAYS` e também as convocações já
    publicadas para os próximos meses.
  * Relatórios: não têm data, só o ano (`ano`). Entram os do ano corrente;
    a data exibida é a da primeira vez que o robô viu o documento.

Quem é a empresa:
  * DEB: o nome do emissor (`nomec`/`nomeg`) casa com a cobertura.
  * CRI/CRA: o nome é o da securitizadora. A ficha do título
    (`www.oliveiratrust.com.br/api/titulos/{tit}`) traz `codigo_do_ativo`,
    que casa com o cadastro de CRI/CRA (`securitizados`) e daí com o devedor.
    A resposta fica guardada em `AppSetting` (o código de um título não muda).

Link do documento: a ficha traz `codigo_operacao`, e
`www.oliveiratrust.com.br/api/titulos/fundos/downloads/{codigo_operacao}`
lista os arquivos com o link final. O endereço antigo
(`services-ft .../titulos/{tit}`) passou a devolver 403 em 11/2026.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from .base import RawArticle, brt_to_utc
from .fiduciarios import carregar_cobertura, corte, get_json, titulo

logger = logging.getLogger(__name__)

API_BASE = "https://services-ft.oliveiratrust.com.br/app/v1"
SITE_API = "https://www.oliveiratrust.com.br/api"
ATIVO_PAGE = "https://www.oliveiratrust.com.br/investidor/ativo"
PAGE_LIMIT = 100
MAX_PAGES = 15
DIAS_A_FRENTE = 365
TIPOS_ATIVO = ("debentures", "cri", "cra")
CACHE_KEY = "oliveiratrust_titulos"  # tit -> {"ativo": codigo_do_ativo, "op": codigo_operacao}
TRABALHADORES = 8

# LOOKBACK_DAYS mantido por compatibilidade com quem importava daqui.
LOOKBACK_DAYS = 40


def _parse_data(data_iso: str | None) -> datetime | None:
    if not data_iso:
        return None
    try:
        d = date.fromisoformat(str(data_iso)[:10])
        return brt_to_utc(d.year, d.month, d.day)
    except ValueError:
        return None


def _listar(params: dict) -> list[dict]:
    itens: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        try:
            d = get_json(f"{API_BASE}/titulos/documentos", params={**params, "page": page, "limit": PAGE_LIMIT})["data"]
        except Exception as e:  # noqa: BLE001
            logger.warning("oliveiratrust: lista %s pagina %d falhou: %s", params, page, e)
            break
        lote = d.get("data") or []
        itens.extend(lote)
        if not lote or page * PAGE_LIMIT >= int(d.get("total") or 0):
            break
    return itens


def _ler_cache() -> dict:
    try:
        from ..db import SessionLocal
        from ..models import AppSetting

        with SessionLocal() as db:
            s = db.get(AppSetting, CACHE_KEY)
            return json.loads(s.value) if s and s.value else {}
    except Exception:  # noqa: BLE001
        return {}


def _gravar_cache(cache: dict) -> None:
    try:
        from ..db import SessionLocal
        from ..models import AppSetting

        with SessionLocal() as db:
            s = db.get(AppSetting, CACHE_KEY)
            valor = json.dumps(cache, separators=(",", ":"))
            if s:
                s.value = valor
            else:
                db.add(AppSetting(key=CACHE_KEY, value=valor))
            db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("oliveiratrust: não gravei o cache de títulos: %s", e)


def _ficha(tit) -> dict | None:
    try:
        d = get_json(f"{SITE_API}/titulos/{tit}").get("data") or []
        if d:
            return {"ativo": (d[0].get("codigo_do_ativo") or "").strip().upper(), "op": d[0].get("codigo_operacao")}
    except Exception as e:  # noqa: BLE001
        logger.warning("oliveiratrust: ficha do título %s falhou: %s", tit, e)
    return None


def _downloads(codigo_operacao) -> list[dict]:
    try:
        return get_json(f"{SITE_API}/titulos/fundos/downloads/{codigo_operacao}").get("data") or []
    except Exception as e:  # noqa: BLE001
        logger.warning("oliveiratrust: downloads da operação %s falharam: %s", codigo_operacao, e)
        return []


def _link_direto(link: str) -> str:
    """O site devolve o leitor (`/portal/leitor/#https://...pdf`); fica o
    arquivo em si, que abre e baixa sem o leitor."""
    for marca in ("leitor/#", "leitor/"):
        if marca in link:
            resto = link.split(marca, 1)[1]
            if resto.startswith("http"):
                return resto
    return link


def fetch(url: str) -> list[RawArticle]:
    cob = carregar_cobertura()
    if not cob.nomes:
        logger.warning("oliveiratrust: cobertura vazia -- nada será coletado")
        return []

    hoje = date.today()
    ini = (datetime.combine(hoje, datetime.min.time()) - timedelta(days=LOOKBACK_DAYS)).date()
    itens: list[dict] = []
    for tipo in TIPOS_ATIVO:
        itens += _listar({"tipo_ativo": tipo, "tipo_documento": "Assembleias",
                          "data_inicial": ini.isoformat(),
                          "data_final": (hoje + timedelta(days=DIAS_A_FRENTE)).isoformat()})
        itens += [dict(i, _relatorio=True) for i in
                  _listar({"tipo_ativo": tipo, "tipo_documento": "Relatórios", "ano": hoje.year})]

    cache = _ler_cache()
    faltam = sorted({str(i["tit"]) for i in itens
                     if i.get("tit") and (i.get("tipo") or "").upper() != "DEBENTURES" and str(i["tit"]) not in cache})
    if faltam:
        with ThreadPoolExecutor(TRABALHADORES) as ex:
            for tit, ficha in zip(faltam, ex.map(_ficha, faltam)):
                if ficha:
                    cache[tit] = ficha
        _gravar_cache(cache)

    escolhidos: list[tuple[dict, str]] = []
    for i in itens:
        tipo = (i.get("tipo") or "").upper()
        if tipo == "DEBENTURES":
            empresa = cob.empresa_no_texto(i.get("nomec") or "", i.get("nomeg") or "")
        else:
            empresa = cob.empresa_do_ticker((cache.get(str(i.get("tit"))) or {}).get("ativo"))
        if empresa:
            escolhidos.append((i, empresa))

    # Link final só para os escolhidos (poucos), com a ficha de cada título.
    for tit in {str(i["tit"]) for i, _ in escolhidos if i.get("tit")} - set(cache):
        ficha = _ficha(tit)
        if ficha:
            cache[tit] = ficha
    ops = {str((cache.get(str(i.get("tit"))) or {}).get("op")) for i, _ in escolhidos} - {"None"}
    with ThreadPoolExecutor(TRABALHADORES) as ex:
        arquivos = dict(zip(ops, ex.map(_downloads, ops)))
    _gravar_cache(cache)

    limite = corte()
    out: list[RawArticle] = []
    for i, empresa in escolhidos:
        relatorio = i.get("_relatorio", False)
        quando = None if relatorio else _parse_data(i.get("data"))
        if not relatorio and quando and quando < limite:
            continue
        ficha = cache.get(str(i.get("tit"))) or {}
        link = None
        for a in arquivos.get(str(ficha.get("op")), []):
            if a.get("cod") == i.get("codigo") and a.get("link"):
                link = _link_direto(a["link"])
                break
        if not link:
            link = f"{ATIVO_PAGE}?id={i.get('tit')}&typo={(i.get('tipo') or 'DEBENTURES').upper()}#{i.get('codigo')}"
        categoria = "Relatório" if relatorio else "Assembleia"
        serie = " ".join(b for b in [ficha.get("ativo") or "", i.get("titulo") or ""] if b)
        out.append(RawArticle(
            url=link,
            title=titulo(empresa, categoria, f"{(i.get('arquivo_nome') or '').strip()} ({serie})"),
            snippet=f"Oliveira Trust — {(i.get('tipo') or '').title()}",
            published_at=quando,
            article_type="assembleia",
        ))
    return out
