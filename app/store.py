"""Funções de acesso a dados para artigos: normalização de URL, upsert com
dedupe, listagem filtrada, limpeza automática. Mesma estratégia do
clipinator (mantém o corpo mais longo em updates, dedupe por URL normalizada)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session, selectinload

from .models import Article, article_company, article_sector

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src",
}


def normalize_url(url: str) -> str:
    try:
        p = urlparse(url)
    except ValueError:
        return url
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = p.path.rstrip("/") or p.path
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    return urlunparse((p.scheme, netloc, path, p.params, urlencode(query), p.fragment))


def upsert_article(
    db: Session,
    *,
    url: str,
    domain: str,
    source_name: str,
    article_type: str,
    title: str,
    snippet: str,
    body: str,
    published_at: datetime | None,
    matched_keywords: list[str],
    company_ids: list[int],
    sector_ids: list[int] | None = None,
    is_covered: bool = True,
) -> bool:
    """Insere se novo; se já existir, atualiza campos (mantendo o corpo mais
    longo) e garante que as empresas/setores casados estejam associados.
    Retorna True se o artigo era novo."""
    sector_ids = sector_ids or []
    norm_url = normalize_url(url)
    existing = db.scalar(select(Article).where(Article.url == norm_url))

    if existing is None:
        art = Article(
            url=norm_url,
            domain=domain,
            source_name=source_name,
            article_type=article_type,
            title=title,
            snippet=snippet,
            body=body,
            published_at=published_at,
            found_at=datetime.now(timezone.utc),
            matched_keywords=json.dumps(matched_keywords, ensure_ascii=False),
            is_covered=is_covered,
        )
        db.add(art)
        db.flush()
        _set_companies(db, art, company_ids)
        _set_sectors(db, art, sector_ids)
        return True

    existing.title = title or existing.title
    if snippet and len(snippet) > len(existing.snippet or ""):
        existing.snippet = snippet
    if body and len(body) > len(existing.body or ""):
        existing.body = body
    if published_at and not existing.published_at:
        existing.published_at = published_at
    existing.matched_keywords = json.dumps(
        sorted(set(matched_keywords) | set(json.loads(existing.matched_keywords or "[]"))),
        ensure_ascii=False,
    )
    if is_covered and not existing.is_covered:
        existing.is_covered = True
    _set_companies(db, existing, company_ids)
    _set_sectors(db, existing, sector_ids)
    return False


def _set_companies(db: Session, art: Article, company_ids: list[int]) -> None:
    """Sincroniza as empresas vinculadas ao artigo com o resultado do
    casamento de keywords DESTA rodada -- adiciona o que é novo e remove
    o que não bate mais.

    Antes isso só adicionava (nunca removia), então qualquer vínculo errado
    gravado uma única vez (ex.: bug de scraper antigo reaproveitando URL,
    ou colisão de dedupe) ficava PRA SEMPRE, mesmo depois do bug corrigido
    -- foi o que causou empresas erradas (ex.: "Boa Safra") aparecendo em
    notícias que não citam elas. Agora a lista de empresas do artigo é
    sempre a foto exata do casamento mais recente."""
    existing_ids = {c.id for c in art.companies}
    new_ids = set(company_ids)

    to_remove = existing_ids - new_ids
    if to_remove:
        db.execute(
            article_company.delete().where(
                article_company.c.article_id == art.id,
                article_company.c.company_id.in_(to_remove),
            )
        )

    to_add = new_ids - existing_ids
    for cid in to_add:
        db.execute(article_company.insert().values(article_id=art.id, company_id=cid))


def _set_sectors(db: Session, art: Article, sector_ids: list[int]) -> None:
    """Mesma lógica de `_set_companies`, mas pra tag de SETOR (17/07/2026) --
    a foto exata do casamento mais recente, sem acumular tag antiga."""
    existing_ids = {s.id for s in art.sector_tags}
    new_ids = set(sector_ids)

    to_remove = existing_ids - new_ids
    if to_remove:
        db.execute(
            article_sector.delete().where(
                article_sector.c.article_id == art.id,
                article_sector.c.sector_id.in_(to_remove),
            )
        )

    to_add = new_ids - existing_ids
    for sid in to_add:
        db.execute(article_sector.insert().values(article_id=art.id, sector_id=sid))


def list_articles(
    db: Session,
    *,
    window_hours: int,
    sector_id: int | None = None,
    company_id: int | None = None,
    source_domain: str | None = None,
    article_type: str | None = None,
    coverage: str = "minha",  # "minha" (default) | "todos"
    limit: int = 500,
):
    from .models import Company

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    # BUG CORRIGIDO (17/07/2026): sem isso, cada artigo dispara uma consulta
    # separada pra carregar `.companies` e cada empresa outra pra carregar
    # `.sector` (N+1) -- no SQLite local isso e' rapido o bastante pra nao
    # incomodar, mas no Postgres/Supabase hospedado (Vercel, com NullPool =
    # conexao nova a cada consulta) isso somava centenas de idas-e-voltas de
    # rede e estourava o timeout de 10s da funcao serverless. selectinload
    # busca tudo em poucas consultas agrupadas, independente de quantos
    # artigos/empresas existam.
    stmt = select(Article).options(
        selectinload(Article.companies).selectinload(Company.sector),
        selectinload(Article.sector_tags),
    ).where(
        ((Article.published_at.is_not(None)) & (Article.published_at >= cutoff))
        | ((Article.published_at.is_(None)) & (Article.found_at >= cutoff))
    )
    if source_domain:
        stmt = stmt.where(Article.domain == source_domain)
    if article_type:
        stmt = stmt.where(Article.article_type == article_type)
    if coverage != "todos":
        # "Minha cobertura": bateu com alguma empresa/setor OU é ação de
        # rating (essas o usuário sempre quer ver, mesmo fora da cobertura
        # nomeada -- uma agência rebaixando qualquer emissor do mercado de
        # crédito privado é relevante pra um analista de credit research).
        stmt = stmt.where((Article.is_covered.is_(True)) | (Article.article_type == "rating_action"))
    if company_id:
        stmt = stmt.join(Article.companies).where(Company.id == company_id)
    elif sector_id:
        # Um artigo pode estar ligado a um setor de duas formas: via empresa
        # especifica daquele setor (article_company -> companies.sector_id),
        # ou via tag direta de setor (article_sector, quando so' bateu termo
        # setorial -- ver taxonomy.resolve_coverage, 17/07/2026). Usa EXISTS
        # em vez de JOIN pra não multiplicar linha nem exigir DISTINCT.
        empresa_do_setor = exists().where(
            article_company.c.article_id == Article.id,
            article_company.c.company_id.in_(
                select(Company.id).where(Company.sector_id == sector_id)
            ),
        )
        tag_de_setor = exists().where(
            article_sector.c.article_id == Article.id,
            article_sector.c.sector_id == sector_id,
        )
        stmt = stmt.where(empresa_do_setor | tag_de_setor)
    stmt = stmt.order_by(Article.published_at.desc().nullslast(), Article.found_at.desc()).limit(limit)
    return list(db.scalars(stmt).unique())


def cleanup_old_articles(db: Session, max_age_hours: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    result = db.execute(
        delete(Article).where(
            (Article.published_at.is_not(None) & (Article.published_at < cutoff))
            | (Article.published_at.is_(None) & (Article.found_at < cutoff))
        )
    )
    return result.rowcount or 0
