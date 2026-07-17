"""Conexão com o banco de dados.

Hoje aponta para SQLite local (data/credit_monitor.db). No futuro, basta
definir a variável de ambiente DATABASE_URL (ex.: a connection string do
Supabase/Postgres) que todo o resto do código continua funcionando sem
alterações — os models usam SQLAlchemy ORM, portátil entre os dois bancos.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# os.getenv(..., default) so usa o default quando a variavel NAO existe --
# mas o .env.example trazia "DATABASE_URL=" (vazio), que faz a variavel
# existir com string vazia e quebra o create_engine(). Por isso "or" aqui,
# em vez de confiar so no default do getenv.
DATABASE_URL = os.getenv("DATABASE_URL") or ""
_IS_SQLITE = not DATABASE_URL or DATABASE_URL.startswith("sqlite")

if _IS_SQLITE:
    # Só cria a pasta data/ (e só usa arquivo local) quando de fato estamos
    # em SQLite -- em hospedagem serverless (Vercel) o sistema de arquivos
    # do deploy é READ-ONLY fora de /tmp, então criar pasta aqui sem essa
    # checagem derrubava o app inteiro já na importação do módulo, antes
    # de qualquer rota rodar. Com DATABASE_URL apontando pro Supabase
    # (Postgres), essa pasta nunca é necessária.
    DATA_DIR = BASE_DIR / "data"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _DB_PATH = (DATA_DIR / "credit_monitor.db").as_posix()  # forward slashes -- precisa disso no Windows
    DATABASE_URL = DATABASE_URL or f"sqlite:///{_DB_PATH}"

if _IS_SQLITE:
    connect_args: dict = {"check_same_thread": False}
else:
    # BUG CORRIGIDO (17/07/2026): o driver psycopg (v3) por padrão prepara
    # os comandos SQL repetidos no lado do servidor pra ir mais rápido
    # (prepared statements, nomeados _pg3_0, _pg3_1...) -- isso quebra o
    # "Transaction pooler" (PgBouncer) da Supabase, porque cada transação
    # pode cair numa conexão de banco diferente por trás do pooler, e o
    # psycopg tenta reusar um nome de prepared statement que já existe
    # numa conexão diferente ("DuplicatePreparedStatement"). Desativando
    # com prepare_threshold=None, o psycopg nunca tenta preparar do lado
    # do servidor -- funciona certinho com pooler em modo transação (é
    # a recomendação oficial pra esse cenário).
    connect_args = {"prepare_threshold": None}
engine_kwargs: dict = {"connect_args": connect_args, "future": True}
if not _IS_SQLITE:
    # Serverless (Vercel) roda vários containers curtos em paralelo -- ter
    # um pool de conexões próprio do SQLAlchemy por cima do connection
    # pooler do Supabase (PgBouncer) pode conflitar com o gerenciamento de
    # sessão dele. NullPool = cada operação abre/fecha sua própria conexão
    # e deixa o PgBouncer cuidar do pooling de verdade (é pra isso que ele
    # existe -- usar a connection string "pooler"/"transaction mode" do
    # Supabase, não a "direct connection", em produção na nuvem).
    engine_kwargs["poolclass"] = NullPool
engine = create_engine(DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Adiciona colunas novas em bancos já existentes (SQLite e Postgres
    aceitam a mesma sintaxe ALTER TABLE ADD COLUMN). Cada ALTER fica em um
    try/except próprio -- se a coluna já existe, ignora o erro e segue."""
    statements = [
        "ALTER TABLE run_logs ADD COLUMN sources_json TEXT DEFAULT '[]'",
        # TRUE (nao 1) -- Postgres nao aceita inteiro cru como default de
        # BOOLEAN (SQLite aceita os dois; TRUE funciona nos dois bancos).
        "ALTER TABLE articles ADD COLUMN is_covered BOOLEAN DEFAULT TRUE",
    ]
    with engine.connect() as conn:
        for stmt in statements:
            try:
                conn.exec_driver_sql(stmt)
                conn.commit()
            except Exception:  # noqa: BLE001
                conn.rollback()
