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
        # BUG CORRIGIDO (23/07/2026): a primeira versao do modulo de spreads
        # criou `debentures` com VARCHAR(20)/(10) -- estourou
        # (StringDataRightTruncation) na primeira captura real do Allan
        # contra o Supabase (SQLite nao teria acusado, nao impoe VARCHAR(N)
        # de verdade). `Base.metadata.create_all` so cria tabela nova, nao
        # altera coluna existente -- por isso o ALTER explicito aqui pra
        # quem ja rodou a versao antiga contra Postgres. ALTER COLUMN TYPE
        # nao existe no SQLite (cai no except e e ignorado, sem problema --
        # SQLite so tem "type affinity", nunca aplicou o limite mesmo).
        "ALTER TABLE debentures ALTER COLUMN codigo TYPE VARCHAR(40)",
        "ALTER TABLE debentures ALTER COLUMN indexador TYPE VARCHAR(30)",
        "ALTER TABLE debentures ALTER COLUMN incentivada TYPE VARCHAR(20)",
        "ALTER TABLE debentures ALTER COLUMN cnpj TYPE VARCHAR(30)",
        # NOVO (24/07/2026): liga a debênture ao cadastro de empresas do
        # monitoramento de notícias -- aba "Marcação Emissores" (ver
        # scripts/match_debenture_issuers.py).
        "ALTER TABLE debentures ADD COLUMN company_id INTEGER",
        # NOVO (27/07/2026): spread em bps calculado por negócio da B3 --
        # ver app/models.py NegocioB3.spread e app/spreads/b3_trades.py.
        "ALTER TABLE negocios_b3 ADD COLUMN spread FLOAT",
        # NOVO (27/07/2026, mesmo dia): referência de NTN-B específica de
        # cada papel (vinda da Anbima) -- ver app/models.py
        # Debenture.referencia_ntnb. Corrige compute_trade_spreads, que
        # antes usava sempre o vértice mais curto pra todo negócio IPCA+.
        "ALTER TABLE debentures ADD COLUMN referencia_ntnb VARCHAR(20)",
        # NOVO (27/07/2026, mesmo dia): curva de NTN-B inteira cacheada por
        # dia (não só o vértice mais curto) -- ver app/models.py
        # NtnbReferencia.curva_json.
        "ALTER TABLE ntnb_referencia ADD COLUMN curva_json TEXT",
        # NOVO (04/08/2026, Fase 1 do Hub): liga a debênture ao emissor
        # canônico (`issuers`), que carrega setor/subsetor/grupo e os
        # ratings. As tabelas novas em si (issuers, issuer_aliases,
        # issuer_ratings, issuer_rating_atual) são criadas por
        # `Base.metadata.create_all` -- só esta coluna precisa de ALTER,
        # porque `debentures` já existe.
        #
        # NÃO substitui `company_id`: aquele aponta pro cadastro editorial
        # de notícias (~96 empresas cobertas), este pro emissor de mercado
        # (~470). Uma debênture pode ter issuer_id sem company_id (emissor
        # fora da cobertura), e o caminho pra notícia é
        # debentures -> issuers -> companies.
        "ALTER TABLE debentures ADD COLUMN issuer_id INTEGER",
        # Mesmo racional pro CRA/CRI quando a tabela existir (Fase 2) --
        # o ALTER falha silencioso enquanto ela não existir, que é o
        # comportamento desejado deste bloco.
        "ALTER TABLE securitizados ADD COLUMN issuer_id INTEGER",
        # NOVO (04/08/2026): % REUNE -- quanto da taxa indicativa veio de
        # negócio real em vez de modelo. Métrica de confiança no preço,
        # usada pela aba Securitizados pra separar "spread abriu" de "a
        # marcação mudou" (ver app/spreads/queries_securitizados.py).
        "ALTER TABLE securitizado_spreads ADD COLUMN pct_reune FLOAT",
    ]
    with engine.connect() as conn:
        for stmt in statements:
            try:
                conn.exec_driver_sql(stmt)
                conn.commit()
            except Exception:  # noqa: BLE001
                conn.rollback()
