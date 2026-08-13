"""Models SQLAlchemy — esquema pensado para funcionar igual em SQLite (local)
e Postgres/Supabase (nuvem, futuro). Nomes de tabela em inglês para casar
com o padrão do Supabase (auth.users é separado; aqui usamos 'users' próprio
por enquanto e migramos para supabase auth mais adiante — ver CLAUDE.md)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Column,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .spreads.ratings import SEM_RATING


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Cobertura: setores, empresas, keywords extras por setor
# ---------------------------------------------------------------------------

class Sector(Base):
    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)

    companies: Mapped[list["Company"]] = relationship(back_populates="sector", cascade="all, delete-orphan")
    extra_keywords: Mapped[list["SectorKeyword"]] = relationship(back_populates="sector", cascade="all, delete-orphan")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    analyst: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    sector: Mapped["Sector"] = relationship(back_populates="companies")
    aliases: Mapped[list["CompanyAlias"]] = relationship(back_populates="company", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("sector_id", "name", name="uq_company_sector_name"),)


class CompanyAlias(Base):
    """Variações de nome/ticker usadas para casar keywords (ex.: 'Vale' -> 'VALE3', 'Vale S.A.')."""
    __tablename__ = "company_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(200), nullable=False)

    company: Mapped["Company"] = relationship(back_populates="aliases")

    __table_args__ = (UniqueConstraint("company_id", "alias", name="uq_alias_company"),)


class SectorKeyword(Base):
    """Termos genéricos do setor (ex.: 'ANEEL', 'tarifa de energia'), além dos nomes de empresa."""
    __tablename__ = "sector_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id"), nullable=False)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)

    sector: Mapped["Sector"] = relationship(back_populates="extra_keywords")

    __table_args__ = (UniqueConstraint("sector_id", "keyword", name="uq_sector_keyword"),)


# ---------------------------------------------------------------------------
# Fontes
# ---------------------------------------------------------------------------

class Source(Base):
    """Um site/portal monitorado. `kind` indica o tipo de coletor usado no pipeline."""
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="news")  # news | rating_agency | regulatory
    kind: Mapped[str] = mapped_column(String(50), default="html")      # rss | html | api
    scraper_module: Mapped[str] = mapped_column(String(120))            # ex.: 'infomoney'
    url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_login: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(20), default="global")     # all | global | specific
    notes: Mapped[str | None] = mapped_column(Text)


class SourceKeyword(Base):
    """Keywords específicas de uma fonte, usadas quando source.mode == 'specific'."""
    __tablename__ = "source_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)

    __table_args__ = (UniqueConstraint("source_id", "keyword", name="uq_source_keyword"),)


# ---------------------------------------------------------------------------
# Artigos
# ---------------------------------------------------------------------------

article_company = Table(
    "article_company",
    Base.metadata,
    Column("article_id", Integer, ForeignKey("articles.id"), primary_key=True),
    Column("company_id", Integer, ForeignKey("companies.id"), primary_key=True),
)

# Tag de SETOR (17/07/2026, pedido do Allan): quando uma noticia bate so'
# com um termo de setor (ex.: "saneamento", "Copom") e nao cita nenhuma
# empresa especifica da cobertura, antes isso grudava TODAS as empresas
# daquele setor no artigo (poluia a lista de chips com empresas que a
# noticia nem cita). Agora esse caso vira uma tag de SETOR separada (sem
# empresa nenhuma anexada) -- ver taxonomy.resolve_coverage.
article_sector = Table(
    "article_sector",
    Base.metadata,
    Column("article_id", Integer, ForeignKey("articles.id"), primary_key=True),
    Column("sector_id", Integer, ForeignKey("sectors.id"), primary_key=True),
)


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    domain: Mapped[str] = mapped_column(String(200), nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    article_type: Mapped[str] = mapped_column(String(30), default="news")  # news | rating_action | fato_relevante
    title: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    matched_keywords: Mapped[str] = mapped_column(Text, default="[]")  # json list
    # True se bateu com alguma empresa/palavra-chave da cobertura (setor ou
    # empresa específica). Artigos que não bateram também são guardados
    # (para o usuário poder auditar o que foi capturado mas filtrado) --
    # esse campo é o que diferencia "Minha cobertura" de "Todos" no filtro
    # do dashboard. Ações de rating aparecem em "Minha cobertura" mesmo com
    # is_covered=False (ver store.list_articles).
    is_covered: Mapped[bool] = mapped_column(Boolean, default=True)

    companies: Mapped[list["Company"]] = relationship(secondary=article_company)
    sector_tags: Mapped[list["Sector"]] = relationship(secondary=article_sector)


# ---------------------------------------------------------------------------
# Repositório de Relatórios (Smart) — 13/08/2026, pedido do Allan
#
# Catálogo dos relatórios do time de Fixed Income Credit Research publicados
# no Smart (itau.com.br/itaubba-pt/portal/credit). O Smart tagueia relatório
# por ticker de bolsa, o que não existe em renda fixa: um relatório chamado
# "Resultados 2T26 - Parte 1" analisa 20 empresas e o especialista que busca
# pelo nome da empresa não acha. Aqui a tag é resolvida por nome de empresa,
# reaproveitando Sector/Company/CompanyAlias que já existem para as notícias
# (um cadastro só serve os dois módulos — mexer em Fontes & Empresas melhora
# o casamento aqui e lá).
#
# Mesmo desenho de Article: N:N com Company, mais uma tag de SETOR separada
# para o caso "relatório setorial que não cita empresa específica" (ex.:
# "Update Setorial - Locadoras") — ver comentário de article_sector acima,
# o problema é idêntico.
# ---------------------------------------------------------------------------

report_company = Table(
    "report_company",
    Base.metadata,
    Column("report_id", String(36), ForeignKey("reports.id"), primary_key=True),
    Column("company_id", Integer, ForeignKey("companies.id"), primary_key=True),
    # True quando a empresa aparece no TÍTULO (o relatório é sobre ela);
    # False quando só é citada no resumo. A UI pinta as duas diferente —
    # sem isso, "PRIO: Quick Take" e uma menção de passagem à Usiminas no
    # mesmo relatório ficariam indistinguíveis.
    Column("principal", Boolean, default=False),
)

report_sector = Table(
    "report_sector",
    Base.metadata,
    Column("report_id", String(36), ForeignKey("reports.id"), primary_key=True),
    Column("sector_id", Integer, ForeignKey("sectors.id"), primary_key=True),
)


class Report(Base):
    __tablename__ = "reports"

    # O id é o UUID do próprio Smart — a URL de consulta é
    # https://www.itau.com.br/itaubba-pt/portal/credit/report/{id}. Usar o id
    # da origem como PK torna a ingestão idempotente: o bookmarklet pode
    # reenviar os mesmos relatórios sem duplicar.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[date | None] = mapped_column(Date)
    category: Mapped[str | None] = mapped_column(String(80))     # Quarterly Results Update | Market Dynamics | Credit Fundamentals
    investment_type: Mapped[str | None] = mapped_column(String(40))  # Offshore | Local | Retail
    analyst: Mapped[str | None] = mapped_column(String(120))
    # Relatório de mercado (Semanal, Top Picks, Market Highlights): nunca é
    # sobre empresa. Fica fora da fila de revisão para ela não encher de
    # falso positivo — são ~40% da base.
    is_market: Mapped[bool] = mapped_column(Boolean, default=False)
    # Alguém ajustou as tags à mão pela interface; a reingestão não
    # sobrescreve o que foi revisado por humano.
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    companies: Mapped[list["Company"]] = relationship(secondary=report_company)
    sector_tags: Mapped[list["Sector"]] = relationship(secondary=report_sector)


Index("ix_reports_published_at", Report.published_at)


# ---------------------------------------------------------------------------
# Usuários, sessões, confirmação de e-mail
#
# Schema pensado para migrar depois para o Supabase Auth: os campos
# equivalem ao que o Supabase já guarda (id uuid, email, role via tabela
# 'profiles', confirmação de e-mail). Por ora local com hash bcrypt próprio.
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user")  # admin | user
    email_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tokens: Mapped[list["EmailToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class EmailToken(Base):
    """Token de confirmação de cadastro (ou reset de senha, futuramente)."""
    __tablename__ = "email_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default=lambda: uuid.uuid4().hex)
    purpose: Mapped[str] = mapped_column(String(30), default="confirm_email")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="tokens")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default=lambda: uuid.uuid4().hex)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="sessions")


# ---------------------------------------------------------------------------
# Config e log de execuções
# ---------------------------------------------------------------------------

class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class RunLog(Base):
    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_found: Mapped[int] = mapped_column(Integer, default=0)
    triggered_by: Mapped[str] = mapped_column(String(30), default="scheduler")  # scheduler | manual
    errors: Mapped[str] = mapped_column(Text, default="[]")
    # Detalhe por fonte: [{"name","category","found","new","error"}, ...] — usado
    # no painel de diagnóstico para saber exatamente o que cada fonte retornou.
    sources_json: Mapped[str] = mapped_column(Text, default="[]")


# ---------------------------------------------------------------------------
# Spreads de debêntures (mercado secundário Anbima/debentures.com.br) —
# primeiro módulo do "Hub Credit Research" além do monitoramento de notícias
# (pedido do Allan, 23/07/2026). Ver CLAUDE.md para o desenho completo.
#
# Duas tabelas: `Debenture` guarda o cadastro (relativamente estático — nome,
# indexador, CNPJ, se é incentivada) com UMA linha por Código, sempre
# sobrescrita com a versão mais recente vista. `DebentureSpread` é a série
# histórica de verdade: uma linha por Código+Data, nunca sobrescrita (cada
# nova captura faz upsert só do dia que está sendo processado, então rodar o
# script de novo pro mesmo dia atualiza aquele dia sem duplicar nem apagar
# dias anteriores).
# ---------------------------------------------------------------------------

class Debenture(Base):
    """Cadastro por Código — dado 'de hoje', sem histórico próprio (o
    histórico de taxa/spread/PU etc. mora em DebentureSpread)."""
    __tablename__ = "debentures"

    codigo: Mapped[str] = mapped_column(String(40), primary_key=True)
    nome: Mapped[str | None] = mapped_column(String(200))
    # BUG CORRIGIDO (23/07/2026): String(20)/(10) estouraram contra dado real
    # da Anbima já na primeira captura do Allan (StringDataRightTruncation no
    # Postgres/Supabase -- SQLite não teria acusado, ele não impõe VARCHAR(N)
    # de verdade, por isso só apareceu contra o banco de produção). Larguras
    # generosas de propósito -- não vale a pena arriscar de novo por causa de
    # um valor "código"/"CNPJ" fora do padrão típico de 6/14 caracteres.
    indexador: Mapped[str | None] = mapped_column(String(30))  # "CDI +" | "IPCA +"
    incentivada: Mapped[str | None] = mapped_column(String(20))  # valor cru da Anbima (Sim/Não/vazio)
    cnpj: Mapped[str | None] = mapped_column(String(30))
    # Bucket analítico (pedido do Allan, 23/07/2026): "IPCA + Incentivadas" |
    # "CDI + Tradicionais" | "Outros" -- calculado por app/spreads/fetch.py
    # compute_classe(indexador, incentivada) sempre que um dos dois campos é
    # atualizado (ver app/spreads/persist.py). NÃO comparável entre si (bases
    # de referência diferentes), por isso é o filtro principal do dashboard,
    # não o indexador sozinho.
    classe: Mapped[str | None] = mapped_column(String(30))
    # Vencimento da NTN-B que a Anbima usa como referência pra ESTE papel
    # (campo `referencia_ntnb` do boletim de debêntures -- só vem
    # preenchido pra papel IPCA+, nunca pra CDI+). CORRIGIDO (27/07/2026):
    # Allan apontou que `b3_trades.compute_trade_spreads` estava usando
    # sempre a NTN-B de vértice MAIS CURTO pra todo negócio IPCA+ da B3,
    # errado -- deveria usar a referência ESPECÍFICA do papel (a mesma que
    # `fetch.fetch_spreads` já usa pro card Anbima) e só cair no vértice
    # mais curto quando o papel não tiver essa referência própria. Esse
    # campo guarda a referência (atualizada a cada captura diária, igual
    # `indexador`/`classe`) pra `compute_trade_spreads` conseguir consultar
    # sem precisar rebuscar o boletim inteiro da Anbima.
    referencia_ntnb: Mapped[str | None] = mapped_column(String(20))
    # Ligação com o cadastro de empresas do monitoramento de notícias (pedido
    # do Allan, 24/07/2026 -- aba "Marcação Emissores"): permite mostrar
    # notícias da empresa ao lado do gráfico de spread dela e agregar todos
    # os tickers de dívida de um mesmo emissor. Preenchido por
    # scripts/match_debenture_issuers.py (heurística de nome, não 100%
    # confiável -- Allan revisa/corrige aliases em /fontes se precisar).
    # NÃO é FK de verdade no schema (sem ondelete/constraint) só pra manter
    # o padrão simples de ALTER TABLE ADD COLUMN já usado nas migrações
    # daqui (ver app/db.py run_migrations).
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    # NOVO (04/08/2026): emissor canônico -- é por aqui que a debênture
    # herda setor/subsetor/grupo e o rating médio (ver models.Issuer e
    # app/spreads/issuers.py::vincular_debentures).
    #
    # Coexiste com `company_id`, não substitui: aquele aponta pro cadastro
    # editorial de notícias (~96 empresas cobertas), este pro emissor de
    # mercado (~470). Papel de emissor fora da cobertura tem issuer_id sem
    # company_id -- o caminho pra notícia é debentures -> issuers ->
    # companies, e `Issuer.company_id` é quem faz a ponte.
    issuer_id: Mapped[int | None] = mapped_column(ForeignKey("issuers.id"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    history: Mapped[list["DebentureSpread"]] = relationship(
        back_populates="debenture", cascade="all, delete-orphan"
    )


class DebentureSpread(Base):
    """Snapshot diário de uma debênture (uma linha por Código+Data)."""
    __tablename__ = "debenture_spreads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(ForeignKey("debentures.codigo"), nullable=False)
    data: Mapped[datetime] = mapped_column(Date, nullable=False)  # data de referência (dia útil, sem hora)
    taxa_indicativa: Mapped[float | None] = mapped_column(Float)
    pu: Mapped[float | None] = mapped_column(Float)
    pct_pu_par: Mapped[float | None] = mapped_column(Float)
    spread: Mapped[float | None] = mapped_column(Float)  # em bps, já calculado (ver app/spreads/fetch.py)
    estoque: Mapped[float | None] = mapped_column(Float)  # R$ milhões
    duration: Mapped[float | None] = mapped_column(Float)  # em anos

    debenture: Mapped["Debenture"] = relationship(back_populates="history")

    __table_args__ = (
        UniqueConstraint("codigo", "data", name="uq_debenture_spread_codigo_data"),
        Index("ix_debenture_spread_data", "data"),
        Index("ix_debenture_spread_codigo", "codigo"),
    )


class NegocioB3(Base):
    """Negócio a negócio da B3 (Boletim Diário do Mercado -- pedido do
    Allan, 24/07/2026): cada linha é UMA operação individual (não
    agregada) em debênture/CRI/CRA, capturada a cada 15 min durante o
    pregão (é a cadência que a própria B3 atualiza essa tabela). Fonte tem
    MUITOS outros tipos de instrumento (CFF, CDCA, COE, CPR, LF...) --
    filtramos só DEB/CRI/CRA (`instrument_type`) porque foi o que o Allan
    pediu, ver app/spreads/b3_trades.py.

    Mostrado na aba "Emissores" filtrado pelos tickers do(s) emissor(es)
    selecionados (mesma lista que já alimenta a tabela de tickers) -- por
    isso hoje só aparece coisa pra DEB de verdade (CRI/CRA não têm
    `company_id`/emissor ligado no cadastro ainda)."""
    __tablename__ = "negocios_b3"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Identificador do negócio na B3 (ex.: "#1009622879") -- único por
    # operação, usado pra não duplicar quando a mesma consulta de 15 em 15
    # min reenvia o dia inteiro de novo (não só os negócios novos).
    trade_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    data_negocio: Mapped[datetime] = mapped_column(Date, nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(10), nullable=False)  # DEB | CRI | CRA
    emissor: Mapped[str | None] = mapped_column(String(200))
    codigo: Mapped[str] = mapped_column(String(40), nullable=False)  # ticker normalizado (ver _normalize_codigo)
    isin: Mapped[str | None] = mapped_column(String(20))
    quantidade: Mapped[float | None] = mapped_column(Float)
    preco: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)  # R$
    taxa: Mapped[float | None] = mapped_column(Float)
    # Spread em bps calculado a partir de `taxa` (pedido do Allan,
    # 27/07/2026 -- os cards da aba Emissores mostram spread, não taxa
    # crua, igual ao resto do dashboard). Preenchido em
    # app/spreads/b3_trades.py::compute_trade_spreads no momento da
    # gravação (`persist.save_negocios_b3`), NÃO no fetch -- mesma fórmula
    # de app/spreads/fetch.py::fetch_spreads (CDI+ = taxa*100; IPCA+ usa a
    # NTN-B de vértice mais curto do dia do negócio, já que o negócio a
    # negócio da B3 não traz `referencia_ntnb` por papel como a Anbima
    # traz). None pra negócio de indexador fora de IPCA+Incentivadas/
    # CDI+Tradicionais (não dá pra calcular, mesma regra do resto do
    # dashboard) ou pra ticker que a gente ainda não tem cadastrado.
    spread: Mapped[float | None] = mapped_column(Float)
    origem: Mapped[str | None] = mapped_column(String(40))  # "Pre-registro - Voice" | "Registro"
    horario: Mapped[str | None] = mapped_column(String(10))  # "HH:MM"
    data_liquidacao: Mapped[datetime | None] = mapped_column(Date)
    situacao: Mapped[str | None] = mapped_column(String(20))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index("ix_negocio_b3_codigo", "codigo"),
        Index("ix_negocio_b3_data", "data_negocio"),
    )


# ---------------------------------------------------------------------------
# Securitizados (CRA/CRI) — Fase 2 do Hub (04/08/2026)
#
# Espelha o par `debentures` / `debenture_spreads`: cadastro relativamente
# estático de um lado, série diária do outro. Fonte: API oficial da Anbima,
# endpoint /feed/precos-indices/v1/cri-cra/mercado-secundario (mesma
# credencial OAuth2 já usada pras debêntures).
#
# TRÊS DIFERENÇAS RELEVANTES EM RELAÇÃO A DEBÊNTURE
# -------------------------------------------------
# 1. **Não há ESTOQUE.** A Anbima não publica saldo em aberto de CRI/CRA
#    (a de debêntures vem de outra fonte, ver fetch.fetch_estoque). Sem
#    estoque não dá pra fazer média ponderada — todo agregado de
#    securitizado é média SIMPLES, e isso precisa ficar explícito na tela,
#    porque o resto do dashboard é ponderado (ver CLAUDE.md, "card SPREAD
#    MÉDIO não era ponderado por Estoque", bug de 27/07/2026).
#
# 2. **Existe `%CDI` (DI multiplicativo)**, que debênture não tem. A taxa
#    é um percentual do CDI (ex.: 97,2 = 97,2% do CDI), então o spread é
#    `(taxa - 100) * 100` e pode ser NEGATIVO — papel que paga menos que
#    100% do CDI. Somar isso com CDI+ aditivo não faz sentido nenhum.
#
# 3. **O risco é do ORIGINADOR, não do emissor.** `emissor` é a
#    securitizadora (Opea, True, Virgo — só 12 delas pra 218
#    originadores); ela é veículo, não devedora. Toda classificação
#    (setor, grupo, rating) tem que pendurar em `originador_credito`.
#    Confirmado pelo Allan em 04/08/2026.
# ---------------------------------------------------------------------------

class Securitizado(Base):
    """Cadastro de um CRI/CRA — uma linha por código, sobrescrita."""
    __tablename__ = "securitizados"

    codigo: Mapped[str] = mapped_column(String(40), primary_key=True)
    tipo_ativo: Mapped[str | None] = mapped_column(String(10))  # CRI | CRA
    # Securitizadora (veículo). NÃO é quem carrega o risco -- ver nota 3.
    emissor: Mapped[str | None] = mapped_column(String(300))
    # Quem tomou o crédito. É por ele que se classifica setor/grupo/rating.
    originador_credito: Mapped[str | None] = mapped_column(String(600))
    serie: Mapped[str | None] = mapped_column(String(50))
    emissao: Mapped[str | None] = mapped_column(String(50))
    data_vencimento: Mapped[datetime | None] = mapped_column(Date)
    tipo_remuneracao: Mapped[str | None] = mapped_column(String(50))
    # Derivado de `tipo_remuneracao` (ver spreads/securitizados.py):
    # "IPCA+" | "CDI+" | "%CDI" | "OUTRO". É o filtro principal da aba,
    # porque as três primeiras classes NÃO são comparáveis entre si.
    indexador: Mapped[str | None] = mapped_column(String(20))
    referencia_ntnb: Mapped[str | None] = mapped_column(String(20))
    # Emissor canônico resolvido a partir do ORIGINADOR (não do emissor).
    issuer_id: Mapped[int | None] = mapped_column(ForeignKey("issuers.id"))

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    history: Mapped[list["SecuritizadoSpread"]] = relationship(
        back_populates="securitizado", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_securitizado_tipo", "tipo_ativo"),
        Index("ix_securitizado_indexador", "indexador"),
        Index("ix_securitizado_issuer", "issuer_id"),
    )


class SecuritizadoSpread(Base):
    """Snapshot diário de um CRI/CRA (uma linha por Código+Data)."""
    __tablename__ = "securitizado_spreads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(ForeignKey("securitizados.codigo"), nullable=False)
    data: Mapped[datetime] = mapped_column(Date, nullable=False)
    taxa_indicativa: Mapped[float | None] = mapped_column(Float)
    taxa_compra: Mapped[float | None] = mapped_column(Float)
    taxa_venda: Mapped[float | None] = mapped_column(Float)
    desvio_padrao: Mapped[float | None] = mapped_column(Float)
    pu: Mapped[float | None] = mapped_column(Float)
    pct_pu_par: Mapped[float | None] = mapped_column(Float)
    pct_vne: Mapped[float | None] = mapped_column(Float)
    # % REUNE -- quanto da taxa indicativa veio de NEGÓCIO REAL registrado
    # no sistema REUNE da Anbima, em vez de modelo/consulta a dealers.
    #
    # É uma métrica de CONFIANÇA NO PREÇO, e o dashboard do Allan não
    # usava. Spread de papel com REUNE alto é preço observado; com REUNE
    # baixo é estimativa. Tratar os dois igual num ranking de "maiores
    # aberturas" põe ruído de marcação no topo da lista.
    pct_reune: Mapped[float | None] = mapped_column(Float)
    # Em ANOS, igual `DebentureSpread.duration`. A API devolve em dias
    # úteis (o snapshot do Allan guardava assim, ex. 58 e 143) -- aqui é
    # convertido dividindo por 252, senão securitizado e debênture não
    # poderiam ir pro mesmo gráfico de spread × duration.
    duration: Mapped[float | None] = mapped_column(Float)
    taxa_ntnb_ref: Mapped[float | None] = mapped_column(Float)
    # Em bps. Fórmula por indexador (ver spreads/securitizados.py) --
    # a mesma de debênture pra IPCA+/CDI+, mais a de %CDI.
    spread: Mapped[float | None] = mapped_column(Float)

    securitizado: Mapped["Securitizado"] = relationship(back_populates="history")

    __table_args__ = (
        UniqueConstraint("codigo", "data", name="uq_securitizado_spread_codigo_data"),
        Index("ix_securitizado_spread_data", "data"),
        Index("ix_securitizado_spread_codigo", "codigo"),
    )


class NegocioB3Diario(Base):
    """Agregado diário do negócio a negócio da B3 — uma linha por
    Código+Data.

    POR QUE ESTA TABELA EXISTE
    --------------------------
    O Allan pediu histórico longo da B3 para as análises (05/08/2026), e a
    conta não fecha guardando negócio a negócio: são ~12.600 operações de
    DEB/CRI/CRA por dia útil, o que dá **~6,3 milhões de linhas em dois
    anos**. Isso não cabe no Supabase gratuito e deixa lenta qualquer
    consulta que varra o período.

    E não precisa: nenhuma análise da tela consome a operação individual.
    "Anbima × B3", liquidez, volume por setor, giro sobre estoque — todas
    querem, por papel e por dia, o volume, a taxa média e quantos negócios
    houve. Isso são ~1.300 papéis × 500 dias = **~650 mil linhas**, dez
    vezes menos.

    A tabela `negocios_b3` (bruta) continua existindo para a janela
    recente, que alimenta a tabela "negócio a negócio" da aba Emissores —
    lá o dado individual é o produto, não um meio.

    TAXA PONDERADA POR VOLUME, não média simples: um negócio de R$ 50 mil
    e outro de R$ 50 milhões não podem pesar igual na taxa do dia. É o
    mesmo princípio do spread ponderado por estoque no resto do projeto
    (ver CLAUDE.md, bug de 27/07/2026).
    """
    __tablename__ = "negocios_b3_diario"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(40), nullable=False)
    data: Mapped[datetime] = mapped_column(Date, nullable=False)
    instrument_type: Mapped[str | None] = mapped_column(String(10))  # DEB | CRI | CRA

    n_negocios: Mapped[int] = mapped_column(Integer, default=0)
    volume: Mapped[float | None] = mapped_column(Float)      # R$ somados
    quantidade: Mapped[float | None] = mapped_column(Float)

    taxa_media: Mapped[float | None] = mapped_column(Float)    # ponderada por volume
    taxa_min: Mapped[float | None] = mapped_column(Float)
    taxa_max: Mapped[float | None] = mapped_column(Float)
    spread_medio: Mapped[float | None] = mapped_column(Float)  # bps, ponderado
    spread_min: Mapped[float | None] = mapped_column(Float)
    spread_max: Mapped[float | None] = mapped_column(Float)

    preco_medio: Mapped[float | None] = mapped_column(Float)
    # Maior negócio do dia (R$) -- separa "um bloco só" de "fluxo
    # pulverizado", que é o que distingue liquidez de verdade.
    maior_negocio: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("codigo", "data", name="uq_negocio_b3_diario"),
        Index("ix_negocio_diario_data", "data"),
        Index("ix_negocio_diario_codigo_data", "codigo", "data"),
    )


class NtnbReferencia(Base):
    """Cache da curva de NTN-B (taxa por vencimento) do dia -- pedido do
    Allan (27/07/2026): "a taxa de referência não vai mudar ao longo do
    dia", então não faz sentido bater na API da Anbima de novo a cada
    captura de negócio a negócio da B3 (a cada 15 min). Preenchida uma
    vez por dia pelo job diário de spreads (`fetch.fetch_ntnb_curve`,
    chamado de `scripts/fetch_debenture_spreads.py`) e lida por
    `b3_trades.compute_trade_spreads` o resto do dia -- só busca ao vivo
    na Anbima se ainda não tiver cache pra aquele dia (ex.: antes do job
    diário rodar, ver `b3_trades._get_ntnb_curve`).

    AMPLIADO (27/07/2026, mesmo dia): guardava só a taxa de vértice MAIS
    CURTO (`min_ntnb`/`min_venc`) -- Allan apontou que isso é usado errado
    como referência única pra TODO negócio IPCA+ da B3, quando na verdade
    cada papel tem sua PRÓPRIA referência de NTN-B vinda da Anbima
    (`Debenture.referencia_ntnb`). `curva_json` guarda a curva inteira
    (`{vencimento: taxa}`) pra `compute_trade_spreads` poder achar a taxa
    do vencimento específico do papel; `min_ntnb`/`min_venc` continuam
    existindo só como FALLBACK pra papel sem referência própria (mesma
    regra que `fetch.fetch_spreads` já usa pro card Anbima)."""
    __tablename__ = "ntnb_referencia"

    data: Mapped[datetime] = mapped_column(Date, primary_key=True)
    min_ntnb: Mapped[float | None] = mapped_column(Float)
    min_venc: Mapped[str | None] = mapped_column(String(20))
    # JSON de {vencimento: taxa} -- ver docstring acima. Nullable pra não
    # quebrar linhas gravadas antes dessa coluna existir (cache antigo só
    # tinha min_ntnb/min_venc); `b3_trades._get_ntnb_curve` trata
    # curva_json vazio como cache "parcial" e rebusca ao vivo.
    curva_json: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------------------
# Emissor: taxonomia (setor/subsetor/grupo) e ratings
#
# Fase 1 do Hub Credit Research (04/08/2026) -- fecha as duas lacunas que o
# CLAUDE.md registrava como impeditivas ("spread médio por RATING" e
# "abertura de spread por SETOR", ver seção "O que o relatório semanal do
# Allan tem que este dashboard NÃO cobre").
#
# POR QUE UMA TABELA DE EMISSOR, E NÃO COLUNAS EM `debentures`
# -----------------------------------------------------------
# Validado contra as 93.764 linhas do Dashboard_Snapshot: a taxonomia é
# **100% consistente por emissor** -- nenhum dos 1.517 tickers muda de
# setor/subsetor/grupo ao longo das 84 datas, e nenhum dos 467 emissores
# tem dois setores diferentes. Rating idem: as agências avaliam o emissor,
# não a emissão (as 33 linhas de AEGEA no snapshot são a MESMA ação de
# rating repetida por ticker).
#
# Então o dado natural é por emissor e o ticker herda. Guardar em
# `debentures` significaria repetir a mesma informação ~3,3x (1.517
# tickers / 467 emissores) e abrir espaço pra duas debêntures do mesmo
# emissor divergirem -- estado que a realidade não tem.
#
# `Company` (do monitoramento de notícias) NÃO serve como esta tabela: são
# só as ~96 empresas da cobertura editorial, contra ~470 emissores com
# papel no mercado. `Issuer.company_id` liga as duas quando existe
# cobertura, e é o que permite mostrar notícia ao lado do spread.
# ---------------------------------------------------------------------------

class Issuer(Base):
    """Emissor de dívida — chave da taxonomia e dos ratings.

    A identidade vem de `key` (ver app/spreads/issuer_key.py), não do nome
    cru: a mesma Anbima devolve "AEGEA ... PARTICIPAÇÕES S/A" pela API
    oficial e "AEGEA ... PARTICIPACOES S.A." pela planilha, e casar por
    igualdade literal perdia 45% dos emissores.
    """
    __tablename__ = "issuers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Chave canônica (maiúsculas, sem acento/pontuação/forma societária).
    key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    # Nome "bonito" pra exibir -- o mais recente visto na API da Anbima.
    nome: Mapped[str] = mapped_column(String(300), nullable=False)
    nome_anbima: Mapped[str | None] = mapped_column(String(300))
    cnpj: Mapped[str | None] = mapped_column(String(30))

    setor: Mapped[str | None] = mapped_column(String(120))
    sub_setor: Mapped[str | None] = mapped_column(String(120))
    grupo_economico: Mapped[str | None] = mapped_column(String(200))
    # 'SNAPSHOT' (carga inicial) | 'MANUAL' (editado na Administração) |
    # 'ANBIMA' (emissor novo criado pelo job diário, ainda sem taxonomia).
    taxonomia_origem: Mapped[str | None] = mapped_column(String(20))

    # Liga ao cadastro de empresas do monitoramento de notícias quando a
    # empresa está na cobertura editorial. Nullable: a maioria dos
    # emissores não é coberta. NÃO é FK com constraint, pra manter o
    # padrão de migração simples do projeto (ver db.py run_migrations).
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    ratings: Mapped[list["IssuerRating"]] = relationship(
        back_populates="issuer", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_issuer_setor", "setor"),
        Index("ix_issuer_grupo", "grupo_economico"),
    )


class IssuerAlias(Base):
    """Casamento manual de um nome que a normalização automática não pega.

    Existe porque `issuer_key` é de propósito conservador: normaliza
    grafia, nunca faz fuzzy matching. "AES CAJUINA AB1 HOLDINGS" (API) e
    "CAJUINA AB1 HOLDINGS" (planilha) são o mesmo emissor, mas juntar isso
    por heurística de prefixo juntaria também "ÁGUAS DO RIO 1" com "ÁGUAS
    DO RIO 4" -- e atribuir o rating errado a uma emissão inteira é pior
    do que não atribuir nada.

    Então o que não casa sozinho aparece na Administração e o Allan liga
    na mão, UMA vez por emissor; a ligação fica aqui.
    """
    __tablename__ = "issuer_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), nullable=False)
    # Chave normalizada do nome alternativo (não o nome cru -- assim o
    # alias também absorve variação de acento/pontuação do lado dele).
    alias_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    alias_nome: Mapped[str | None] = mapped_column(String(300))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IssuerRating(Base):
    """Uma ação de rating: agência × emissor × data.

    Série histórica, nunca sobrescrita -- é o que permite "quando a
    Fitch rebaixou este emissor?" e o gráfico de spread antes/depois da
    ação. O rating VIGENTE é derivado daqui (ver `IssuerRatingAtual`).
    """
    __tablename__ = "issuer_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), nullable=False)
    # NULL = rating do EMISSOR (vale pra todos os papéis dele).
    # Preenchido = rating daquela EMISSÃO específica, que sobrepõe o do
    # emissor só pra ela.
    #
    # DESCOBERTO NA VALIDAÇÃO (04/08/2026): rating não é sempre por
    # emissor. Na base do Allan, a COSAN tem TRÊS níveis simultâneos e
    # estáveis ao longo das 84 datas -- CSAN13/14/16 sempre AAA(bra),
    # CSAN15/18/23/24/33 sempre A+(bra), CSANB2 sempre A(bra). Não é
    # ruído: é tranche com garantia diferente, coisa normal em crédito.
    # A COGNA idem (COGNA3 AAA, o resto AA+).
    #
    # Modelar só por emissor forçaria um rating único e MUDARIA as curvas
    # que ele já analisa -- exatamente o que ele pediu pra não acontecer.
    # 21 emissores precisam disso; os outros ~450 usam `codigo IS NULL`.
    codigo: Mapped[str | None] = mapped_column(String(40))
    agencia: Mapped[str] = mapped_column(String(10), nullable=False)  # FITCH | SP | MOODYS
    # Rating na grafia ORIGINAL da agência ("AA+(bra)", "brAA+", "AA+.br").
    # Guardado cru de propósito: é o que `app/spreads/ratings.py` espera
    # como entrada, e normalizar na gravação perderia a rastreabilidade
    # contra o site da agência.
    rating: Mapped[str | None] = mapped_column(String(30))
    rating_anterior: Mapped[str | None] = mapped_column(String(30))
    perspectiva: Mapped[str | None] = mapped_column(String(40))
    perspectiva_anterior: Mapped[str | None] = mapped_column(String(40))
    acao: Mapped[str | None] = mapped_column(String(40))  # Upgrade | Downgrade | Afirmação | ...
    data_acao: Mapped[datetime] = mapped_column(Date, nullable=False)
    link: Mapped[str | None] = mapped_column(Text)
    origem: Mapped[str | None] = mapped_column(String(30))  # SNAPSHOT | SCRAPING_FITCH | MANUAL | ...
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    issuer: Mapped["Issuer"] = relationship(back_populates="ratings")

    __table_args__ = (
        # Mesma ação capturada de novo (o scraper reprocessa o histórico)
        # não pode duplicar. Inclui `rating` porque duas agências podem
        # publicar no mesmo dia e o mesmo emissor pode ter revisão de
        # escala global e nacional na mesma data.
        UniqueConstraint("issuer_id", "codigo", "agencia", "data_acao", "rating",
                         name="uq_issuer_rating_acao"),
        Index("ix_issuer_rating_issuer", "issuer_id"),
        Index("ix_issuer_rating_codigo", "codigo"),
        Index("ix_issuer_rating_data", "data_acao"),
    )


class IssuerRatingAtual(Base):
    """Rating vigente por emissor + o RATING MÉDIO calculado.

    Tabela derivada, recalculada por inteiro a cada carga de ratings
    (`app/spreads/ratings_sync.py`). Existe pra não obrigar toda query de
    dashboard a fazer um `MAX(data_acao)` correlacionado por agência --
    seriam três subconsultas por emissor em cima de uma tabela que só
    cresce, no caminho quente de todas as telas.

    `notch_medio` (1=AAA .. 22=D) é o que permite ORDENAR por risco.
    Ordenar pela string daria ordem alfabética (A < AA < AAA), que é
    errada -- e é um bug silencioso num eixo de gráfico.
    """
    __tablename__ = "issuer_rating_atual"

    issuer_id: Mapped[int] = mapped_column(
        ForeignKey("issuers.id", ondelete="CASCADE"), primary_key=True
    )
    fitch: Mapped[str | None] = mapped_column(String(30))
    sp: Mapped[str | None] = mapped_column(String(30))
    moodys: Mapped[str | None] = mapped_column(String(30))
    fitch_data: Mapped[datetime | None] = mapped_column(Date)
    sp_data: Mapped[datetime | None] = mapped_column(Date)
    moodys_data: Mapped[datetime | None] = mapped_column(Date)
    # Escala padrão sem sufixo de agência ("AA-"), ou "N.A.".
    #
    # NUNCA NULO. Regra do Allan (04/08/2026): "para o rating médio
    # necessariamente deve ter ou o rating médio ou N.A., nunca em
    # branco". Nulo e "N.A." significam a mesma coisa pro analista, mas
    # se comportam de forma completamente diferente em SQL -- NULL some
    # de `GROUP BY`, quebra comparação (`= 'N.A.'` é falso) e vira buraco
    # silencioso em gráfico. "N.A." é um balde de verdade, com estoque e
    # spread próprios, e precisa aparecer como tal.
    rating_medio: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SEM_RATING, server_default=SEM_RATING
    )
    # `notch_medio` PODE ser nulo -- é o ordenador numérico, e "N.A." não
    # tem posição na escala de risco. Ordenar com NULLS LAST joga o balde
    # sem rating pro fim, que é onde ele deve ficar.
    notch_medio: Mapped[int | None] = mapped_column(Integer)
    n_agencias: Mapped[int] = mapped_column(Integer, default=0)
    # JSON com os valores que não casaram em nenhuma tabela de peso (ex.:
    # {"sp": "brCCC"}). Alimenta a tela de Administração -- ver
    # ratings.ratings_desconhecidos(). Allan decidiu (04/08/2026) NÃO
    # adicionar essas grafias às tabelas de peso, então elas ficam
    # visíveis aqui em vez de sumirem no cálculo.
    desconhecidos_json: Mapped[str | None] = mapped_column(Text)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index("ix_rating_atual_notch", "notch_medio"),
    )


class IssuerRatingPeriodo(Base):
    """Rating médio VIGENTE em cada intervalo de datas (slowly changing
    dimension).

    POR QUE ISTO EXISTE — viés retrospectivo
    ----------------------------------------
    `IssuerRatingAtual` responde "qual o rating hoje?". Não serve pro
    gráfico histórico: juntar o spread de 2025 com o rating de hoje
    atribui a nota atual ao passado inteiro. Um emissor rebaixado no mês
    passado apareceria no balde AAA por dois anos de histórico e depois
    "pularia" de balde — o gráfico de "spread médio AAA no tempo"
    mostraria um nível artificialmente alto no passado, porque papel que
    hoje é AA ainda estava contado como AAA lá atrás.

    É exatamente o que o processo manual do Allan (descrito por ele em
    04/08/2026) já evita: ele EMPILHA os ratings por data em vez de
    substituir, e cruza spread × rating por (ticker, data). Validado
    contra o snapshot: a junção as-of (último rating com data <= data do
    spread) reproduz o `ratingMedio` da base dele com **zero
    divergências** onde há cobertura de rating.

    POR QUE INTERVALO, E NÃO UMA LINHA POR DIA
    ------------------------------------------
    Uma tabela dia a dia (470 emissores × ~500 dias úteis) daria ~235 mil
    linhas pra representar algumas centenas de mudanças de rating. Aqui é
    uma linha por MUDANÇA, e a junção vira:

        JOIN issuer_rating_periodo p ON p.issuer_id = d.issuer_id
         AND s.data >= p.data_inicio
         AND (p.data_fim IS NULL OR s.data < p.data_fim)

    `data_fim` NULL = período vigente (aberto).

    Tabela derivada: reconstruída inteira a partir de `issuer_ratings`
    por `issuers.reconstruir_periodos_rating()`. Nunca editada à mão.
    """
    __tablename__ = "issuer_rating_periodo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issuer_id: Mapped[int] = mapped_column(
        ForeignKey("issuers.id", ondelete="CASCADE"), nullable=False
    )
    # Mesma semântica de IssuerRating.codigo: NULL = período do EMISSOR,
    # preenchido = período daquela EMISSÃO (sobrepõe o do emissor).
    # A resolução é "o mais específico vence" -- ver
    # issuers.rating_em(codigo=...).
    codigo: Mapped[str | None] = mapped_column(String(40))
    data_inicio: Mapped[datetime] = mapped_column(Date, nullable=False)
    # NULL = vigente. Exclusivo: o período vale para data < data_fim.
    data_fim: Mapped[datetime | None] = mapped_column(Date)

    fitch: Mapped[str | None] = mapped_column(String(30))
    sp: Mapped[str | None] = mapped_column(String(30))
    moodys: Mapped[str | None] = mapped_column(String(30))
    # NUNCA NULO -- mesma regra de IssuerRatingAtual.rating_medio.
    rating_medio: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SEM_RATING, server_default=SEM_RATING
    )
    notch_medio: Mapped[int | None] = mapped_column(Integer)
    n_agencias: Mapped[int] = mapped_column(Integer, default=0)

    # 'HISTORICO' | 'DERIVADO' -- e a diferença NÃO é cosmética.
    #
    # HISTORICO: copiado verbatim da view final do Allan (ticker × data ×
    #   ratingMedio). **CONGELADO**: nunca é recalculado nem apagado.
    #   Regra dele, 04/08/2026: "o histórico não deve ser alterado, se
    #   para o ticker x o rating era y na data z, manter, pois qualquer
    #   alteração no histórico geraria uma diferença".
    #
    #   Isso importa porque a base histórica tem ~1,7% de linhas em que o
    #   `ratingMedio` discorda das próprias colunas de agência ao lado
    #   (planilha que ficou defasada). Recalcular "consertaria" essas
    #   linhas e mudaria as curvas de 2025 que ele já analisou e
    #   distribuiu -- o conserto seria pior que o defeito.
    #
    # DERIVADO: calculado a partir de `issuer_ratings` pela regra corrente
    #   (agência sem rating não entra na média; nenhuma agência com rating
    #   = N.A.). É o que vale daqui pra frente, e o único que
    #   `reconstruir_periodos_rating` apaga e reconstrói.
    #
    # Dentro da janela coberta pelo HISTORICO, ele vence -- ver
    # issuers.rating_em().
    origem: Mapped[str] = mapped_column(String(20), default="DERIVADO")

    __table_args__ = (
        UniqueConstraint("issuer_id", "codigo", "data_inicio", "origem",
                         name="uq_rating_periodo_inicio"),
        Index("ix_rating_periodo_issuer_data", "issuer_id", "data_inicio"),
        Index("ix_rating_periodo_codigo_data", "codigo", "data_inicio"),
        Index("ix_rating_periodo_notch", "notch_medio"),
    )
