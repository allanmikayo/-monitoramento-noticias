"""Models SQLAlchemy — esquema pensado para funcionar igual em SQLite (local)
e Postgres/Supabase (nuvem, futuro). Nomes de tabela em inglês para casar
com o padrão do Supabase (auth.users é separado; aqui usamos 'users' próprio
por enquanto e migramos para supabase auth mais adiante — ver CLAUDE.md)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
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
