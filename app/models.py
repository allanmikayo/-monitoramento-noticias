"""Models SQLAlchemy — esquema pensado para funcionar igual em SQLite (local)
e Postgres/Supabase (nuvem, futuro). Nomes de tabela em inglês para casar
com o padrão do Supabase (auth.users é separado; aqui usamos 'users' próprio
por enquanto e migramos para supabase auth mais adiante — ver CLAUDE.md)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

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
