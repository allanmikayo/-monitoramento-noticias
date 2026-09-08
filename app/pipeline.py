"""Orquestra a coleta: roda cada fonte habilitada, casa keywords/empresas,
grava só o que é novo/relevante, registra estatísticas de execução."""
from __future__ import annotations

import importlib
import json
import logging
from datetime import datetime, timezone
from typing import Callable

from . import store  # `config` saiu junto com a limpeza (08/09/2026)
from .db import SessionLocal
from .filter import match_keywords
from .models import RunLog, Source
from .taxonomy import build_index, resolve_coverage

logger = logging.getLogger(__name__)

_TYPE_BY_CATEGORY = {
    "rating_agency": "rating_action",
    "regulatory": "fato_relevante",
    "news": "news",
}

ProgressCallback = Callable[[int, int, str], None]


def _run_source(source_info: dict, taxonomy) -> dict:
    """Roda uma fonte e retorna um dict de diagnóstico:
    {name, category, found, matched, new, error}.
    `found`   = quantos itens o scraper trouxe da fonte (antes de filtrar)
    `matched` = quantos desses citam alguma empresa/termo que cobrimos
    `new`     = quantos eram artigos novos (não estavam no banco ainda)
    `error`   = mensagem de erro, se algo quebrou (None se deu tudo certo)
    """
    result = {
        "name": source_info["name"], "category": source_info["category"],
        "found": 0, "matched": 0, "new": 0, "error": None,
    }

    try:
        module = importlib.import_module(f"app.sources.{source_info['scraper_module']}")
    except ImportError as e:
        result["error"] = f"módulo '{source_info['scraper_module']}' não encontrado: {e}"
        return result

    try:
        raw_articles = module.fetch(source_info["url"])
    except Exception as e:  # noqa: BLE001
        logger.exception("Erro coletando %s", source_info["name"])
        result["error"] = f"{type(e).__name__}: {e}"
        return result

    result["found"] = len(raw_articles)

    try:
        n_new = 0
        n_matched = 0
        with SessionLocal() as db:
            # UMA CONSULTA PRA FONTE INTEIRA, não uma por artigo
            # (08/09/2026) -- ver `store.prefetch_articles` pro diagnóstico
            # completo. Resumo: um feed devolve os mesmos ~30 itens a cada
            # rodada, e cada item custava três idas ao banco (busca por URL
            # + dois lazy loads de vínculo) só pra concluir que já estava
            # lá. Numa fonte de 30 itens são ~90 consultas que viram 2.
            urls = [store.normalize_url(raw.url) for raw in raw_articles]
            existentes = store.prefetch_articles(db, urls)
            for raw in raw_articles:
                haystack = f"{raw.title}\n{raw.snippet}"
                matched = match_keywords(haystack, taxonomy.all_keywords, taxonomy.sector_only_keywords)
                is_covered = bool(matched)
                if is_covered:
                    n_matched += 1

                # Empresa especifica citada -> so' ela (preciso). Se nenhuma
                # empresa especifica bateu mas um termo de SETOR bateu ->
                # o artigo ganha uma tag do SETOR (nao fica grudado em toda
                # empresa do setor -- pedido do Allan, 17/07/2026).
                company_ids, sector_ids = resolve_coverage(matched, taxonomy)

                article_type = raw.article_type or _TYPE_BY_CATEGORY.get(source_info["category"], "news")

                # Guarda TODO artigo encontrado, não só os que bateram com a
                # cobertura -- o usuário pode auditar o resto pelo filtro
                # "Cobertura: Todos" no dashboard (e ações de rating sempre
                # aparecem em "Minha cobertura" mesmo sem is_covered).
                is_new = store.upsert_article(
                    db,
                    url=raw.url,
                    domain=source_info["domain"],
                    source_name=source_info["name"],
                    article_type=article_type,
                    title=raw.title,
                    snippet=raw.snippet,
                    body=raw.body,
                    published_at=raw.published_at,
                    matched_keywords=matched,
                    company_ids=sorted(company_ids),
                    sector_ids=sorted(sector_ids),
                    is_covered=is_covered,
                    existing_map=existentes,
                )
                if is_new:
                    n_new += 1
            db.commit()
        result["new"] = n_new
        result["matched"] = n_matched
    except Exception as e:  # noqa: BLE001
        logger.exception("Erro processando artigos de %s", source_info["name"])
        result["error"] = f"{type(e).__name__}: {e}"

    return result


def run_pipeline(triggered_by: str = "scheduler", progress_cb: ProgressCallback | None = None) -> dict:
    """Roda todas as fontes habilitadas. Retorna um resumo (para log/API).

    `progress_cb(indice_atual, total, nome_da_fonte)` é chamado ANTES de
    processar cada fonte -- usado pelo endpoint de status para mostrar uma
    barra de progresso em tempo real no dashboard.
    """
    started_at = datetime.now(timezone.utc)
    summary = {"started_at": started_at.isoformat(), "sources": [], "n_new": 0, "errors": []}

    with SessionLocal() as db:
        taxonomy = build_index(db)
        sources = [
            {
                "name": s.name, "domain": s.domain, "category": s.category,
                "scraper_module": s.scraper_module, "url": s.url,
            }
            for s in db.query(Source).filter(Source.enabled.is_(True)).all()
        ]

        run_log = RunLog(started_at=started_at, triggered_by=triggered_by)
        db.add(run_log)
        db.commit()
        run_log_id = run_log.id

    total = len(sources)
    for i, source_info in enumerate(sources, start=1):
        if progress_cb:
            try:
                progress_cb(i, total, source_info["name"])
            except Exception:  # noqa: BLE001
                pass
        result = _run_source(source_info, taxonomy)
        summary["sources"].append(result)
        summary["n_new"] += result["new"]
        if result["error"]:
            summary["errors"].append(f"{result['name']}: {result['error']}")

    # A LIMPEZA DE ARTIGOS ANTIGOS SAIU DAQUI (08/09/2026).
    #
    # Ela rodava ao fim de toda varredura -- 96 vezes por dia -- e cada
    # execução varria `articles` inteira pra, quase sempre, não apagar
    # nada: a janela de retenção é de 45 dias, então só há o que apagar
    # algumas vezes por dia, no máximo.
    #
    # Agora é `scripts/faxina_diaria.py`, com workflow próprio
    # (`faxina_diaria.yml`, 23h BRT). `summary["cleaned_up"]` continua
    # existindo, sempre 0, pra não quebrar quem lê o resumo (o painel de
    # diagnóstico do dashboard e os testes).
    summary["cleaned_up"] = 0

    with SessionLocal() as db:
        log = db.get(RunLog, run_log_id)
        if log:
            log.finished_at = datetime.now(timezone.utc)
            log.n_found = summary["n_new"]
            log.errors = json.dumps(summary["errors"], ensure_ascii=False)
            log.sources_json = json.dumps(summary["sources"], ensure_ascii=False)
            db.commit()

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_pipeline(triggered_by="manual")
    print(json.dumps(result, indent=2, ensure_ascii=False))
