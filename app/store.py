"""Funções de acesso a dados para artigos: normalização de URL, upsert com
dedupe, listagem filtrada, limpeza automática. Mesma estratégia do
clipinator (mantém o corpo mais longo em updates, dedupe por URL normalizada)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session, load_only, selectinload

from .models import Article, article_company, article_sector

# DATA DE ORDENAÇÃO DO ARTIGO (08/09/2026).
#
# Um artigo é datado por `published_at`; quando a fonte não informa data de
# publicação (acontece em RSS mal formado e em algumas páginas raspadas),
# vale `found_at`. Antes isso era escrito como um OR de duas condições:
#
#     (published_at IS NOT NULL AND published_at >= corte)
#     OR (published_at IS NULL AND found_at >= corte)
#
# Logicamente idêntico ao COALESCE, mas com uma diferença cara: nenhum
# índice serve pra um OR entre duas colunas, então TODA carga do dashboard
# varria `articles` inteira. O diagnóstico de 08/09/2026 mediu 1.840
# varreduras completas e 8.982.042 linhas lidas sequencialmente.
#
# Com a expressão única existe um índice pra ela (`ix_articles_data`, ver
# app/models.py), e ele resolve o filtro E a ordenação de uma vez.
#
# CUIDADO: a expressão aqui tem que continuar batendo LITERALMENTE com a do
# índice -- é assim que o Postgres reconhece que pode usá-lo.
DATA_ARTIGO = func.coalesce(Article.published_at, Article.found_at)

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


def prefetch_articles(db: Session, urls: list[str]) -> dict[str, Article]:
    """Carrega DE UMA VEZ os artigos que já existem entre `urls` (já
    normalizadas), com empresas e setores juntos.

    POR QUE EXISTE (08/09/2026). `upsert_article` fazia uma consulta por
    URL, e logo depois `_set_companies`/`_set_sectors` tocavam
    `art.companies` e `art.sector_tags`, cada um disparando outra consulta
    (lazy load). Três idas ao banco por item de feed -- e como um RSS
    devolve os MESMOS itens a cada rodada, quase tudo era trabalho
    repetido pra redescobrir artigo que já estava lá.

    O diagnóstico do Supabase de 08/09/2026 mediu o estrago em ~55 dias:

        articles_url_key      2.109.272 usos
        article_company_pkey  2.098.103 usos
        article_sector_pkey   1.783.325 usos

    Os três praticamente iguais -- a assinatura exata de um N+1. Isso, e
    não o tamanho do banco (111 MB, 100% de cache), é o que esgotava a
    instância: o Postgres não estava lendo disco, estava atendendo milhões
    de consultas minúsculas.

    Com o prefetch, uma fonte de 30 itens sai de ~90 consultas para 2 (esta
    e as duas do selectinload).
    """
    if not urls:
        return {}
    stmt = (
        select(Article)
        .options(selectinload(Article.companies), selectinload(Article.sector_tags))
        .where(Article.url.in_(set(urls)))
    )
    return {a.url: a for a in db.scalars(stmt).unique()}


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
    existing_map: dict[str, Article] | None = None,
) -> bool:
    """Insere se novo; se já existir, atualiza campos (mantendo o corpo mais
    longo) e garante que as empresas/setores casados estejam associados.
    Retorna True se o artigo era novo.

    `existing_map` é o resultado de `prefetch_articles` para as URLs desta
    rodada (ver o porquê lá). Quando informado, a busca por URL sai do
    banco e vira uma consulta de dicionário, e as coleções já vêm
    carregadas -- nenhuma das três consultas por artigo acontece. Sem ele,
    o comportamento é o antigo, uma consulta por artigo: os chamadores
    antigos e os testes continuam funcionando sem mudança."""
    sector_ids = sector_ids or []
    norm_url = normalize_url(url)
    if existing_map is not None:
        existing = existing_map.get(norm_url)
    else:
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
        # O mesmo link pode vir DUAS VEZES no mesmo feed (paginação que
        # repete item, home que lista a mesma matéria em duas seções).
        # Registrar o recém-criado no mapa faz a segunda ocorrência cair no
        # caminho de atualização em vez de tentar inserir a mesma URL de
        # novo e estourar a unicidade.
        if existing_map is not None:
            existing_map[norm_url] = art
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
    sector_ids: list[int] | None = None,
    company_ids: list[int] | None = None,
    source_domain: str | None = None,
    # Filtro de FONTE por nome, multi-seleção (pedido do Allan,
    # 12/08/2026: "um filtro de caixa suspensa com as fontes, igual o
    # filtro de setor"). Usa `Article.source_name`, não `domain`: é o que
    # aparece na tela e o que o usuário reconhece ("Valor Econômico", não
    # "valor.globo.com"). `source_domain` continua existindo para quem
    # chama por domínio.
    source_names: list[str] | None = None,
    article_type: str | None = None,
    coverage: list[str] | None = None,  # ["minha"] (default) | ["todos"] | ["minha","todos"]
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
    # NÃO CARREGA `body` (08/09/2026). A listagem nunca mostra o corpo do
    # artigo -- `/api/articles` devolve título, resumo e vínculos, nada
    # mais. Mesmo assim o `select(Article)` trazia a coluna inteira: são 27
    # MB de texto em `articles`, arrastados do banco a cada carga da tela
    # pra serem descartados no serializador. `load_only` corta isso.
    #
    # `body` e `matched_keywords` continuam acessíveis (o SQLAlchemy busca
    # sob demanda se alguém ler), então nada quebra -- só deixa de vir de
    # graça em quem não usa.
    stmt = select(Article).options(
        load_only(
            Article.id, Article.url, Article.domain, Article.source_name,
            Article.article_type, Article.title, Article.snippet,
            Article.published_at, Article.found_at, Article.is_covered,
        ),
        selectinload(Article.companies).selectinload(Company.sector),
        selectinload(Article.sector_tags),
    ).where(DATA_ARTIGO >= cutoff)
    if source_domain:
        stmt = stmt.where(Article.domain == source_domain)
    if source_names:
        stmt = stmt.where(Article.source_name.in_(source_names))
    if article_type:
        stmt = stmt.where(Article.article_type == article_type)
    coverage = coverage or ["minha"]
    if "todos" not in coverage:
        # "Minha cobertura": bateu com alguma empresa/setor OU é ação de
        # rating (essas o usuário sempre quer ver, mesmo fora da cobertura
        # nomeada -- uma agência rebaixando qualquer emissor do mercado de
        # crédito privado é relevante pra um analista de credit research).
        stmt = stmt.where((Article.is_covered.is_(True)) | (Article.article_type == "rating_action"))
    # Setor e empresa agora aceitam MAIS DE UM valor (pedido do Allan,
    # 03/08/2026: poder marcar mais de uma opção nos filtros). "Bate com
    # QUALQUER um dos selecionados" (OR), não "todos ao mesmo tempo" --
    # continua usando EXISTS em vez de JOIN pra não multiplicar linha
    # quando um artigo casa com mais de uma empresa/setor selecionado ao
    # mesmo tempo (mesma razão que já valia pro filtro de setor sozinho).
    if company_ids:
        empresa_bate = exists().where(
            article_company.c.article_id == Article.id,
            article_company.c.company_id.in_(company_ids),
        )
        stmt = stmt.where(empresa_bate)
    elif sector_ids:
        # Um artigo pode estar ligado a um setor de duas formas: via empresa
        # especifica daquele setor (article_company -> companies.sector_id),
        # ou via tag direta de setor (article_sector, quando so' bateu termo
        # setorial -- ver taxonomy.resolve_coverage, 17/07/2026).
        empresa_do_setor = exists().where(
            article_company.c.article_id == Article.id,
            article_company.c.company_id.in_(
                select(Company.id).where(Company.sector_id.in_(sector_ids))
            ),
        )
        tag_de_setor = exists().where(
            article_sector.c.article_id == Article.id,
            article_sector.c.sector_id.in_(sector_ids),
        )
        stmt = stmt.where(empresa_do_setor | tag_de_setor)
    # ORDENAÇÃO PELA MESMA EXPRESSÃO DO FILTRO -- é o que deixa o índice
    # `ix_articles_data` resolver filtro e ordem numa passada só.
    #
    # MUDANÇA VISÍVEL (08/09/2026): antes era `published_at DESC NULLS LAST,
    # found_at DESC`, o que empurrava todo artigo SEM data de publicação
    # pro fim da lista, por mais recente que fosse. Agora ele entra na
    # posição da data em que foi capturado. Na prática a lista fica mais
    # correta (uma matéria de hoje sem `published_at` aparece hoje, não no
    # rodapé); se algum dia isso incomodar, é esta linha.
    stmt = stmt.order_by(DATA_ARTIGO.desc()).limit(limit)
    return list(db.scalars(stmt).unique())


def cleanup_old_articles(db: Session, max_age_hours: int) -> int:
    """Apaga artigos além da janela de retenção.

    NÃO CHAMAR NA VARREDURA (08/09/2026). Isto rodava ao fim de CADA
    rodada do pipeline -- 96 vezes por dia -- e cada execução varria
    `articles` inteira só pra descobrir que, na imensa maioria das vezes,
    não havia nada pra apagar. Passou pra `scripts/faxina_diaria.py`, que
    roda uma vez por dia.

    Também não traz mais os ids pro Python. A versão anterior carregava
    todos os ids antigos numa lista e montava três `IN (...)` com eles --
    numa limpeza acumulada isso vira uma consulta com milhares de literais.
    Agora o filtro é uma subconsulta e o banco resolve tudo por lá.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    antigos = select(Article.id).where(DATA_ARTIGO < cutoff).scalar_subquery()
    # ORDEM IMPORTA (bug corrigido em 17/07/2026): apagar o artigo antes de
    # limpar as linhas dele em article_company/article_sector quebra no
    # Postgres (ForeignKeyViolation -- "still referenced from table
    # article_sector"). No SQLite o problema ficava escondido, porque as
    # chaves estrangeiras não são aplicadas por padrão neste projeto.
    db.execute(article_company.delete().where(article_company.c.article_id.in_(antigos)))
    db.execute(article_sector.delete().where(article_sector.c.article_id.in_(antigos)))
    result = db.execute(delete(Article).where(DATA_ARTIGO < cutoff))
    return result.rowcount or 0
