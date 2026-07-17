"""Constrói o índice de keywords (empresas + termos de setor) usado pelo
pipeline para decidir relevância e marcar setor/empresa de cada artigo."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .filter import normalize
from .models import Company, CompanyAlias, Sector, SectorKeyword


@dataclass
class TaxonomyIndex:
    all_keywords: list[str]
    keyword_to_companies: dict[str, set[int]]   # keyword normalizado -> company_id
    keyword_to_sectors: dict[str, set[int]]     # keyword normalizado -> sector_id (inclui via empresa)
    company_sector: dict[int, int]              # company_id -> sector_id
    company_names: dict[int, str]
    sector_names: dict[int, str]
    companies_by_sector: dict[int, set[int]]    # sector_id -> company_ids (p/ fallback setorial)
    sector_only_keywords: set[str]              # keywords normalizados que NAO sao nome/alias de empresa


def build_index(db: Session) -> TaxonomyIndex:
    keyword_to_companies: dict[str, set[int]] = {}
    keyword_to_sectors: dict[str, set[int]] = {}
    company_sector: dict[int, int] = {}
    company_names: dict[int, str] = {}
    companies_by_sector: dict[int, set[int]] = {}
    sector_only_keywords: set[str] = set()
    sector_names: dict[int, str] = {s.id: s.name for s in db.query(Sector).all()}

    all_keywords: set[str] = set()

    companies = db.query(Company).filter(Company.active.is_(True)).all()
    for c in companies:
        company_sector[c.id] = c.sector_id
        company_names[c.id] = c.name
        companies_by_sector.setdefault(c.sector_id, set()).add(c.id)
        terms = [c.name] + [a.alias for a in c.aliases]
        for t in terms:
            if not t or not t.strip():
                continue
            all_keywords.add(t)
            norm = normalize(t)
            keyword_to_companies.setdefault(norm, set()).add(c.id)
            keyword_to_sectors.setdefault(norm, set()).add(c.sector_id)

    for sk in db.query(SectorKeyword).all():
        if not sk.keyword.strip():
            continue
        all_keywords.add(sk.keyword)
        norm = normalize(sk.keyword)
        keyword_to_sectors.setdefault(norm, set()).add(sk.sector_id)
        # Termo de setor puro (nao e' tambem nome/alias de alguma empresa) --
        # isento da checagem de maiuscula em match_keywords, porque nao e'
        # substantivo proprio (ex.: "varejo", "credito privado").
        if norm not in keyword_to_companies:
            sector_only_keywords.add(norm)

    return TaxonomyIndex(
        all_keywords=sorted(all_keywords),
        keyword_to_companies=keyword_to_companies,
        keyword_to_sectors=keyword_to_sectors,
        company_sector=company_sector,
        company_names=company_names,
        sector_names=sector_names,
        companies_by_sector=companies_by_sector,
        sector_only_keywords=sector_only_keywords,
    )


def resolve_company_ids(matched_keywords: list[str], taxonomy: "TaxonomyIndex") -> set[int]:
    """Decide quais empresas um artigo deve ficar associado, a partir das
    keywords que bateram no título/resumo.

    Regra (pedido explícito do Allan, 17/07/2026): se alguma keyword
    casada é o nome/alias de uma empresa ESPECÍFICA, usa só essas (preciso
    -- é isso que o artigo realmente cita). Só quando NENHUMA empresa
    específica bate, mas um termo de SETOR bate (ex.: "varejo", "crédito
    privado"), associa a TODAS as empresas cadastradas naquele(s) setor(es)
    -- notícia setorial/macro é relevante pra quem cobre o setor inteiro,
    mesmo sem citar nenhum emissor pelo nome."""
    company_ids: set[int] = set()
    sector_fallback_ids: set[int] = set()
    for kw in matched_keywords:
        norm = normalize(kw)
        comp_ids = taxonomy.keyword_to_companies.get(norm, set())
        if comp_ids:
            company_ids |= comp_ids
        else:
            for sector_id in taxonomy.keyword_to_sectors.get(norm, set()):
                sector_fallback_ids |= taxonomy.companies_by_sector.get(sector_id, set())
    return company_ids or sector_fallback_ids
