"""Conexão com o banco de dados.

Hoje aponta para SQLite local (data/credit_monitor.db). No futuro, basta
definir a variável de ambiente DATABASE_URL (ex.: a connection string do
Supabase/Postgres) que todo o resto do código continua funcionando sem
alterações — os models usam SQLAlchemy ORM, portátil entre os dois bancos.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# os.getenv(..., default) so usa o default quando a variavel NAO existe --
# mas o .env.example trazia "DATABASE_URL=" (vazio), que faz a variavel
# existir com string vazia e quebra o create_engine(). Por isso "or" aqui,
# em vez de confiar so no default do getenv.
DATABASE_URL = os.getenv("DATABASE_URL") or ""


def raizes_confiaveis(destino: str) -> str | None:
    """Caminho de um pacote de raízes de CA que exista em TODO ambiente.

    POR QUE ISTO EXISTE (10/09/2026). Com `sslmode=verify-full` o libpq precisa
    de uma âncora de confiança, e cada ambiente esconde a dele num lugar
    diferente:

      - Linux comum: `/etc/ssl/certs`, e `sslrootcert=system` acha sozinho.
      - Windows: `system` significa "o repositório do OpenSSL", NÃO o do
        Windows -- e o OpenSSL embutido no psycopg procura num caminho
        compilado que não existe na máquina.
      - Runtime da Vercel: mesma história. O erro nos dois é o mesmo e não
        ajuda em nada:
            SSL error: certificate verify failed

    Perseguir o caminho certo em cada ambiente é caçar um problema que volta
    sempre. `certifi` é um pacote de raízes mantido pela comunidade Python,
    já instalado aqui como dependência do `requests`, presente em todos os
    três ambientes e com as raízes do Let's Encrypt (ISRG Root X1 e X2)
    dentro. Um caminho só, que funciona em todo lugar.

    Devolve `None` se o certifi não estiver disponível (aí o libpq volta ao
    comportamento padrão) ou se a URL já pedir um `sslrootcert` explícito --
    quem escreveu a URL manda.
    """
    if destino.startswith("sqlite") or "sslrootcert" in destino:
        return None
    try:
        import certifi
    except ImportError:  # pragma: no cover -- requests o traz junto
        return None
    return certifi.where()

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
    # `connect_timeout` (segundos) -- ADICIONADO 20/08/2026. Sem ele, quando
    # o Supabase fica sobrecarregado a conexão fica pendurada até a função da
    # Vercel morrer aos 60s, e o usuário encara uma aba girando por um minuto
    # antes de um 504. Com 8s, a falha acontece rápido e o app consegue
    # mostrar uma página dizendo o que houve (ver o handler de
    # OperationalError em app.py).
    #
    # O erro real observado em produção era justamente na conexão, não numa
    # consulta:
    #   psycopg.errors.ConnectionFailure: Failed to connect to database:
    #   authentication did not complete within 15000ms
    connect_args = {"prepare_threshold": None, "connect_timeout": 8}
    # Sem isto, `sslmode=verify-full` falha na Vercel com
    # "SSL error: certificate verify failed" -- ver raizes_confiaveis().
    _ca = raizes_confiaveis(DATABASE_URL)
    if _ca:
        connect_args["sslrootcert"] = _ca
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


# ---------------------------------------------------------------------------
# ENGINE DE MANUTENÇÃO -- a porta certa para DDL e carga
#
# BUG REAL (09/09/2026). `scripts/init_db.py` importava o `engine` daqui de
# cima. Só que esse engine é afinado para a VERCEL: pooler em modo TRANSAÇÃO
# (porta 6543), `NullPool`, `connect_timeout=8`. Para um script de DDL
# rodando da máquina do Allan, os três estão errados:
#
#   - modo transação não garante que dois comandos seguidos caiam na mesma
#     conexão do servidor -- e DDL com `SET statement_timeout` antes depende
#     exatamente disso;
#   - o pool do Supavisor em modo transação é o primeiro recurso a acabar
#     quando o projeto está saturado, e aí nem a conexão sai:
#         (ECHECKOUTTIMEOUT) unable to check out connection from the pool
#         after 15000ms in Transaction mode
#   - 8 segundos é o certo para uma função serverless falhar rápido e mostrar
#     página de erro; é curto demais para um script que pode esperar.
#
# O pooler de SESSÃO (mesma máquina, porta 5432) dá uma conexão dedicada pela
# vida da sessão -- que é o que DDL, carga e diagnóstico querem. Foi por isso
# que forçar `:5432` na mão fez o `init_db` andar, e sem forçar ele nem
# conectava. Este helper faz essa troca sozinho, para ninguém mais depender
# de lembrar de uma variável de ambiente.
# ---------------------------------------------------------------------------

PORTA_POOLER_TRANSACAO = 6543
PORTA_POOLER_SESSAO = 5432


def url_de_manutencao(url: str) -> str:
    """Troca o pooler de transação pelo de sessão, se for esse o caso.

    Mexe na URL pelo objeto do SQLAlchemy, não por `str.replace` -- senha com
    `:` ou `@` dentro sobrevive.
    """
    if not url or url.startswith("sqlite"):
        return url
    alvo = make_url(url)
    hospedeiro = (alvo.host or "").lower()
    if hospedeiro.endswith("pooler.supabase.com") and alvo.port == PORTA_POOLER_TRANSACAO:
        alvo = alvo.set(port=PORTA_POOLER_SESSAO)
    return alvo.render_as_string(hide_password=False)


def _e_supabase(destino: str) -> bool:
    """A URL aponta para a Supabase (pooler ou conexão direta)?"""
    try:
        hospedeiro = (make_url(destino).host or "").lower()
    except Exception:  # noqa: BLE001 -- URL exótica não é problema deste helper
        return False
    return hospedeiro.endswith("pooler.supabase.com") or hospedeiro.endswith(".supabase.co")


def connect_args_manutencao(destino: str, connect_timeout: int = 30) -> dict:
    """Os `connect_args` da conexão de manutenção.

    Função própria para poder ser conferida em teste sem abrir conexão.
    """
    if destino.startswith("sqlite"):
        return {"check_same_thread": False}

    args: dict = {"prepare_threshold": None, "connect_timeout": connect_timeout}

    ca = raizes_confiaveis(destino)
    if ca:
        args["sslrootcert"] = ca

    # LIMITES NO `options` -- SÓ CONTRA A SUPABASE (09/09 e 10/09/2026).
    #
    # Por que existe: a Supabase põe `statement_timeout` curto no ROLE, e um
    # `SET` depois protege apenas o comando seguinte. Todo o resto da sessão --
    # inclusive o `ROLLBACK` que o SQLAlchemy dispara ao fechar uma conexão --
    # continua no limite do role. Foi isso que derrubou o `init_db`: um
    # ROLLBACK cancelado por `statement timeout`, o que não faz sentido nenhum
    # até você descobrir de onde vem o limite. `options` vai no pacote de
    # abertura e vale para a sessão inteira. Medido: com o role em 900ms, uma
    # conexão sem `options` cancela um `pg_sleep(2)`; com `options`, passa.
    #
    # Por que é CONDICIONAL: `options` não atravessa um pooler em modo
    # transação, que recusa parâmetro de inicialização desconhecido --
    #     FATAL: unsupported startup parameter in options: statement_timeout
    # e não pode repassar, porque a conexão de servidor é reaproveitada entre
    # clientes e um `SET` de um vazaria para o próximo. No Postgres próprio da
    # OCI o PgBouncer está justamente nesse modo, e os mesmos limites já vêm do
    # `postgresql.conf`, valendo para todos. Mandar `options` ali seria pedir
    # de novo o que já está posto, ao custo de não conseguir conectar.
    #
    # O remendo nasceu por causa da origem e vai embora junto com ela.
    if _e_supabase(destino):
        args["options"] = (
            "-c statement_timeout=300000"
            " -c lock_timeout=10000"
            " -c idle_in_transaction_session_timeout=60000"
        )
    return args


def criar_engine_manutencao(url: str | None = None, connect_timeout: int = 30) -> Engine:
    """Engine para rodar DDL, carga e diagnóstico da máquina do Allan.

    Pooler de SESSÃO, tempo de conexão folgado e `pool_pre_ping` (uma conexão
    que ficou parada enquanto o banco se recuperava não vira erro no meio do
    trabalho). NÃO usar isto dentro do app: lá o NullPool + pooler de
    transação é o certo.
    """
    destino = url_de_manutencao(url or DATABASE_URL)
    return create_engine(
        destino,
        connect_args=connect_args_manutencao(destino, connect_timeout),
        future=True,
        pool_pre_ping=True,
    )



def criar_sessao_manutencao(eng: Engine | None = None) -> sessionmaker:
    """`SessionLocal` equivalente, mas pela porta de manutenção."""
    return sessionmaker(
        bind=eng or criar_engine_manutencao(),
        autoflush=False,
        autocommit=False,
        future=True,
    )


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema(force: bool = False, eng: Engine | None = None) -> None:
    """`create_all` + `run_migrations` -- mas SÓ quando explicitamente pedido.

    POR QUE (08/09/2026). Todo script de coleta abria com essas duas
    chamadas. Juntas são 29 tabelas conferidas uma a uma e 15 ALTERs: ~44
    idas e voltas até o Supabase ANTES de o script fazer qualquer trabalho
    útil -- a cada rodada, e a varredura de notícias roda 96 vezes por dia.

    O custo não é só latência. Quatro dos ALTERs são `ALTER COLUMN ... TYPE`
    em `debentures`. Mesmo quando o tipo já está certo e o Postgres não
    precisa reescrever a tabela, ele **reconstrói os índices da coluna** e
    pega ACCESS EXCLUSIVE nela. Como `codigo` é a chave primária, o coletor
    de NOTÍCIAS estava reconstruindo a PK da tabela de DEBÊNTURES de 15 em
    15 minutos, travando quem estivesse lendo a aba Spreads naquele
    instante.

    O DDL já tinha saído do boot da Vercel em 20/08/2026 pelo mesmo motivo
    (45 segundos no primeiro acesso depois de a função dormir). Faltava
    tirar dos scripts.

    ONDE O DDL MORA AGORA: `python -m scripts.init_db`, rodado da máquina do
    Allan, por conexão direta e sem limite de tempo. Rode depois de QUALQUER
    mudança em models.py -- inclusive índice novo.

    Para forçar numa execução isolada (banco novo, ambiente de teste), sem
    mexer em código: variável de ambiente `CREDIT_MONITOR_DDL=1`.
    """
    if not force and os.getenv("CREDIT_MONITOR_DDL", "").strip().lower() not in ("1", "true", "sim"):
        return
    from . import models  # noqa: F401 -- popula Base.metadata antes do create_all

    alvo = eng if eng is not None else engine
    Base.metadata.create_all(alvo)
    falhas = run_migrations(eng=alvo)
    if falhas:
        # Levantar aqui, e não devolver em silêncio: um esquema pela metade
        # quebra longe da causa (ver o histórico em run_migrations).
        detalhe = "\n  ".join(f"{cmd}\n    -> {err}" for cmd, err in falhas)
        raise RuntimeError(
            f"{len(falhas)} migração(ões) falharam de verdade (não é 'coluna já existe'):\n  {detalhe}"
        )


# Erros de migração que são ESPERADOS e devem passar em silêncio -- por
# SQLSTATE, não por texto de mensagem (que muda com idioma e versão):
#   42701 duplicate_column  -> a coluna já existe, que é o caso normal aqui
#   42710 duplicate_object  -> idem para constraint/índice
#   42P07 duplicate_table
#   42P01 undefined_table   -> ALTER numa tabela que ainda não existe; o
#                              bloco de `securitizados` conta com isso desde
#                              a Fase 2 (ver o comentário lá embaixo)
SQLSTATE_BENIGNOS = {"42701", "42710", "42P07", "42P01"}
# SQLite não tem SQLSTATE; a mensagem é a única pista.
MENSAGENS_BENIGNAS = ("duplicate column name", "already exists", "no such table")

# CONTENÇÃO -- a falha não é do comando, é de quem estava na frente dele:
#   55P03 lock_not_available -> estourou o `lock_timeout` esperando a vez
#   57014 query_canceled     -> estourou o `statement_timeout`
# Vale tentar de novo; um ALTER que não conseguiu a trava agora costuma
# conseguir daqui a alguns segundos, quando a rodada de coleta terminar.
SQLSTATE_CONTENCAO = {"55P03", "57014"}


def _sqlstate(exc: Exception) -> str | None:
    return getattr(exc, "sqlstate", None) or getattr(getattr(exc, "orig", None), "sqlstate", None)


def _e_benigno(exc: Exception) -> bool:
    estado = _sqlstate(exc)
    if estado:
        return estado in SQLSTATE_BENIGNOS
    texto = str(exc).lower()
    return any(m in texto for m in MENSAGENS_BENIGNAS)


# Os dois formatos de comando que dá para conferir no catálogo ANTES de
# mandar. Deliberadamente estritos: o que não casar com eles é executado
# do jeito antigo -- errar para o lado de rodar um comando à toa é barato,
# errar para o lado de PULAR um comando necessário deixa o esquema pela
# metade.
_RE_ADD = re.compile(r"^ALTER TABLE (\w+) ADD COLUMN (\w+)\b", re.IGNORECASE)
_RE_TIPO = re.compile(
    r"^ALTER TABLE (\w+) ALTER COLUMN (\w+) TYPE VARCHAR\((\d+)\)\s*$", re.IGNORECASE
)


# Consulta ao catálogo do Postgres, não à `information_schema` (09/09/2026).
#
# `information_schema.columns` é uma view que junta meia dúzia de catálogos e
# ainda roda checagem de permissão coluna a coluna. Medido num banco com 9.175
# colunas: 6,2ms contra 2,3ms desta -- 3x. Não foi ela que causou o timeout do
# Allan (a causa era o limite no role, ver `criar_engine_manutencao`), mas num
# banco que já está no limite, a consulta mais barata é a que tem chance.
#
# `atttypmod - 4` é como o Postgres guarda o N de VARCHAR(N); só vale para
# varchar/bpchar (em `numeric` o mesmo campo guarda precisão e escala), daí o
# filtro por `typname`.
_SQL_CATALOGO = """
SELECT c.relname, a.attname,
       CASE WHEN t.typname IN ('varchar', 'bpchar') AND a.atttypmod > 4
            THEN a.atttypmod - 4 END
  FROM pg_attribute a
  JOIN pg_class     c ON c.oid = a.attrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_type      t ON t.oid = a.atttypid
 WHERE n.nspname = current_schema()
   AND c.relkind IN ('r', 'p')
   AND a.attnum > 0
   AND NOT a.attisdropped
   AND c.relname = ANY(%s)
"""


def _catalogo_colunas(
    eng: Engine, tabelas: list[str] | None = None
) -> dict[str, dict[str, int | None]] | None:
    """`{tabela: {coluna: tamanho_do_varchar}}` numa consulta só.

    Devolve `None` quando não dá para consultar (SQLite) -- e aí nada é
    filtrado, o comportamento volta a ser o de antes. Levanta se a consulta
    falhar; quem chama decide (`run_migrations` avisa e segue sem filtro).
    """
    if eng.dialect.name != "postgresql":
        return None
    mapa: dict[str, dict[str, int | None]] = {}
    with eng.connect() as conn:
        linhas = conn.exec_driver_sql(_SQL_CATALOGO, (list(tabelas or []),))
        for tabela, coluna, tamanho in linhas:
            mapa.setdefault(tabela, {})[coluna] = tamanho
    return mapa


def _tabelas_citadas(statements: list[str]) -> list[str]:
    """As tabelas que os comandos tocam -- para perguntar ao catálogo só sobre
    elas, em vez de sobre o schema inteiro."""
    nomes = []
    for stmt in statements:
        m = _RE_ADD.match(stmt) or _RE_TIPO.match(stmt)
        if m and m.group(1) not in nomes:
            nomes.append(m.group(1))
    return nomes


def _motivo_para_pular(stmt: str, catalogo: dict[str, dict[str, int | None]] | None) -> str | None:
    """Por que este comando não precisa rodar -- ou `None` se precisa.

    POR QUE ISTO EXISTE (09/09/2026). Dois problemas de uma vez.

    O primeiro é custo. `ADD COLUMN` numa coluna que já existe não é de
    graça: é uma ida e volta até o banco e um ACCESS EXCLUSIVE em
    `debentures` para descobrir que não havia nada a fazer. Uma consulta
    de catálogo responde por todas as quinze.

    O segundo é o que quebrou de verdade. Os três `ALTER COLUMN ... TYPE`
    em `debentures` passaram a falhar com

        cannot alter type of a column used by a view or rule
        DETAIL: rule _RETURN on view v_spread_rating depends on column "codigo"

    desde que a view `v_spread_rating` foi criada (04/08/2026). Ficaram
    invisíveis enquanto `run_migrations` engolia exceção; quando parou de
    engolir, viraram quatro falhas na cara do Allan. Só que as colunas já
    estavam largas o bastante -- o ALTER era um não-evento tentando pegar
    a trava mais pesada do Postgres na chave primária da tabela. Conferir
    o tamanho atual no catálogo faz o comando simplesmente não acontecer.

    (Quando o alargamento for de verdade necessário, o comando roda e a
    view precisa sair da frente -- é o que `scripts/init_db.py` faz,
    dropando as views antes e recriando depois.)
    """
    if catalogo is None:
        return None

    m = _RE_ADD.match(stmt)
    if m:
        tabela, coluna = m.group(1), m.group(2)
        colunas = catalogo.get(tabela)
        if colunas is None:
            return None  # tabela não existe -> deixa falhar benigno (42P01)
        return "coluna já existe" if coluna in colunas else None

    m = _RE_TIPO.match(stmt)
    if m:
        tabela, coluna, alvo = m.group(1), m.group(2), int(m.group(3))
        colunas = catalogo.get(tabela)
        if colunas is None or coluna not in colunas:
            return None
        atual = colunas[coluna]
        if atual is not None and atual >= alvo:
            return f"já é VARCHAR({atual}), o alvo era {alvo}"
        return None

    return None


def _executar_ddl(stmt: str, tentativas: int, eng: Engine | None = None) -> str | None:
    """Roda um comando de DDL. Devolve o erro, ou `None` se deu certo.

    `lock_timeout` curto DE PROPÓSITO. Sem ele, um ALTER que não consegue
    a trava fica pendurado até o `statement_timeout` estourar -- foi o que
    aconteceu com `ADD COLUMN grupo_economico` em 09/09/2026, que passou
    o tempo todo na fila e voltou como `canceling statement due to
    statement timeout`, uma mensagem que aponta para o comando quando o
    culpado era o coletor rodando ao lado. Com 10s, a espera termina cedo,
    o script tenta de novo, e a mensagem final diz o que de fato houve.
    """
    alvo = eng if eng is not None else engine
    ultimo = ""
    for tentativa in range(1, tentativas + 1):
        try:
            with alvo.connect() as conn:
                if alvo.dialect.name == "postgresql":
                    # Na MESMA transação do DDL: o pooler da Supabase é modo
                    # transação, um `SET` solto pode cair noutra conexão.
                    conn.exec_driver_sql("SET lock_timeout = '10s'")
                    conn.exec_driver_sql("SET statement_timeout = '5min'")
                conn.exec_driver_sql(stmt)
                conn.commit()
            return None
        except Exception as exc:  # noqa: BLE001
            if _e_benigno(exc):
                return None
            ultimo = f"{type(exc).__name__}: {str(exc)[:250]}"
            if _sqlstate(exc) not in SQLSTATE_CONTENCAO:
                return ultimo
            if tentativa < tentativas:
                time.sleep(5 * tentativa)
                continue
            return (
                ultimo
                + f" -- a trava não abriu em {tentativas} tentativas. Tem coleta"
                " segurando a tabela (elas rodam de 15 em 15 min); espere ela"
                " terminar, ou pause os jobs, e rode de novo."
            )
    return ultimo


def run_migrations(tentativas: int = 3, eng: Engine | None = None) -> list[tuple[str, str]]:
    """Adiciona colunas novas em bancos já existentes (SQLite e Postgres
    aceitam a mesma sintaxe ALTER TABLE ADD COLUMN).

    DEVOLVE A LISTA DE FALHAS REAIS -- `[(comando, erro), ...]`, vazia quando
    tudo certo.

    BUG CORRIGIDO (09/09/2026). Esta função tinha um `except Exception: pass`
    por ALTER, pensado para engolir "a coluna já existe" -- que é o caso
    normal e não é erro. Só que ele engolia TUDO: timeout, conexão recusada,
    permissão negada. Aconteceu de verdade no mesmo dia: as três colunas da
    taxonomia (`setor`, `subsetor`, `grupo_economico`) não foram criadas
    porque o Supabase estava sem responder, o `init_db` terminou dizendo
    "OK", e o erro só apareceu depois, na criação do índice que dependia
    delas -- longe da causa.

    Agora a distinção é por SQLSTATE: "já existe" passa calado, qualquer
    outra coisa volta na lista e quem chamou decide o que fazer. É a mesma
    lição do `criar_indices`: não deu erro e deu certo são coisas
    diferentes.

    SEGUNDA RODADA (09/09/2026, mesmo dia). Parar de engolir revelou que
    quatro comandos vinham falhando havia mais de um mês -- ver
    `_motivo_para_pular`. A resposta não foi voltar a silenciar: foi
    perguntar ao catálogo o que já está feito e não mandar o comando.
    """
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
        # NOVO (09/09/2026): taxonomia por ticker (setor/subsetor/grupo),
        # carregada de Taxonomia_Emissores.xlsx por
        # scripts/importar_taxonomia.py -- ver app/models.py Debenture.setor.
        "ALTER TABLE debentures ADD COLUMN setor VARCHAR(80)",
        "ALTER TABLE debentures ADD COLUMN subsetor VARCHAR(80)",
        "ALTER TABLE debentures ADD COLUMN grupo_economico VARCHAR(120)",
        # `securitizados` carrega as MESMAS três colunas (ver models.py
        # Securitizado.setor) -- e elas ficaram de fora quando eu escrevi o
        # bloco acima. Só apareceu porque o `conferir_colunas` do init_db foi
        # perguntar ao catálogo em vez de confiar em "não deu erro": as
        # migrações de `debentures` passaram todas, o script disse que estava
        # tudo certo, e três colunas continuavam faltando em outra tabela.
        "ALTER TABLE securitizados ADD COLUMN setor VARCHAR(80)",
        "ALTER TABLE securitizados ADD COLUMN subsetor VARCHAR(80)",
        "ALTER TABLE securitizados ADD COLUMN grupo_economico VARCHAR(120)",
    ]
    # `ALTER COLUMN ... TYPE` é sintaxe que o SQLite não tem. Antes esses
    # quatro comandos caíam no except e passavam despercebidos; agora que
    # falha de verdade é reportada, eles precisam ser EXCLUÍDOS de propósito
    # em vez de silenciados -- não são erro, são comando que não se aplica
    # àquele banco.
    alvo = eng if eng is not None else engine
    if alvo.dialect.name == "sqlite":
        statements = [c for c in statements if " ALTER COLUMN " not in c]

    # Uma falha ao LER o catálogo não pode derrubar a migração: no pior caso
    # mandamos todo o DDL como antes, que é correto, só mais caro.
    try:
        catalogo = _catalogo_colunas(alvo, _tabelas_citadas(statements))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "nao consegui ler o catalogo (%s: %s) -- vou mandar todo o DDL,"
            " inclusive o que talvez ja esteja feito",
            type(exc).__name__, str(exc)[:120],
        )
        catalogo = None
    pendentes = [s for s in statements if _motivo_para_pular(s, catalogo) is None]

    falhas: list[tuple[str, str]] = []
    for stmt in pendentes:
        erro = _executar_ddl(stmt, tentativas, alvo)
        if erro:
            falhas.append((stmt, erro))
    return falhas
