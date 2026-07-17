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


def resolve_coverage(matched_keywords: list[str], taxonomy: "TaxonomyIndex") -> tuple[set[int], set[int]]:
    """Decide como marcar um artigo a partir das keywords que bateram no
    título/resumo. Retorna (company_ids, sector_ids).

    Regra (pedido explícito do Allan, 17/07/2026, revisada no mesmo dia):
    se alguma keyword casada é o nome/alias de uma empresa ESPECÍFICA, o
    artigo fica ligado só a essas empresas (preciso -- é isso que ele
    realmente cita), sem tag de setor. Se NENHUMA empresa específica bateu
    mas um termo de SETOR bateu (ex.: "saneamento", "Copom"), o artigo NÃO
    fica mais "grudado" em toda empresa do setor (isso inflava a lista de
    chips com empresas que a notícia nem cita) -- em vez disso ganha uma
    tag do PRÓPRIO SETOR (Article.sector_tags), sinalizando que é
    relevante pra quem cobre o setor inteiro sem apontar pra uma empresa
    específica. Continua contando como "minha cobertura" (is_covered
    depende só de ter batido alguma keyword, ver pipeline.py)."""
    company_ids: set[int] = set()
    sector_ids: set[int] = set()
    for kw in matched_keywords:
        norm = normalize(kw)
        comp_ids = taxonomy.keyword_to_companies.get(norm, set())
        if comp_ids:
            company_ids |= comp_ids
        else:
            sector_ids |= taxonomy.keyword_to_sectors.get(norm, set())
    if company_ids:
        return company_ids, set()
    return set(), sector_ids
