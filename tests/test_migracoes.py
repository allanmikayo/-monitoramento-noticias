"""O DDL só sai quando precisa sair — e a falha diz quem é o culpado.

CONTEXTO (09/09/2026). O `init_db` do Allan voltou com quatro erros:

    ALTER TABLE debentures ALTER COLUMN codigo TYPE VARCHAR(40)
      -> cannot alter type of a column used by a view or rule
    ALTER TABLE debentures ADD COLUMN grupo_economico VARCHAR(120)
      -> canceling statement due to statement timeout

Duas causas diferentes, e nenhuma delas era o comando em si:

1. As três primeiras estavam falhando desde 04/08/2026, escondidas pelo
   `except Exception: pass` que saiu de cena um dia antes. As colunas já
   tinham a largura certa — o comando era um não-evento tentando pegar
   ACCESS EXCLUSIVE na chave primária de `debentures`.

2. A quarta não demorou: ficou na FILA. O `ADD COLUMN` de uma coluna
   anulável sem default é troca de catálogo, instantânea. O tempo todo foi
   espera pela trava, provavelmente atrás de uma das coletas que rodam de
   15 em 15 minutos. A mensagem apontava para o comando; o culpado estava
   ao lado.

Estes testes guardam as duas correções.
"""
from __future__ import annotations

import pytest

from app import db


# --------------------------------------------------------------------------
# 1. O catálogo decide o que nem precisa ser enviado
# --------------------------------------------------------------------------

# `debentures` como está no banco do Allan: varchars já largos, taxonomia
# metade criada (setor/subsetor entraram em 09/09, grupo_economico não).
CATALOGO = {
    "debentures": {
        "codigo": 40, "indexador": 30, "incentivada": 20, "cnpj": 30,
        "company_id": None, "referencia_ntnb": 20, "issuer_id": None,
        "setor": 80, "subsetor": 80,
    },
    "run_logs": {"sources_json": None},
}


def test_nao_manda_add_column_de_coluna_que_ja_existe():
    motivo = db._motivo_para_pular(
        "ALTER TABLE debentures ADD COLUMN setor VARCHAR(80)", CATALOGO
    )
    assert motivo is not None


def test_manda_add_column_de_coluna_que_falta():
    # Esta é a que faltava de verdade em 09/09 — tem que sair.
    assert db._motivo_para_pular(
        "ALTER TABLE debentures ADD COLUMN grupo_economico VARCHAR(120)", CATALOGO
    ) is None


def test_nao_manda_alter_type_quando_a_coluna_ja_esta_larga():
    """O caso que quebrou. Sem esta checagem o comando sai, esbarra na view
    `v_spread_rating` e volta como erro — para não fazer nada."""
    assert db._motivo_para_pular(
        "ALTER TABLE debentures ALTER COLUMN codigo TYPE VARCHAR(40)", CATALOGO
    ) is not None


def test_manda_alter_type_quando_o_alargamento_e_de_verdade():
    """A checagem não pode virar um silenciador: coluna estreita de verdade
    ainda precisa do ALTER (e aí a view tem que sair da frente — é o que o
    `init_db` faz)."""
    estreito = {"debentures": {"codigo": 20}}
    assert db._motivo_para_pular(
        "ALTER TABLE debentures ALTER COLUMN codigo TYPE VARCHAR(40)", estreito
    ) is None


def test_tabela_que_nao_existe_deixa_o_comando_sair():
    """`securitizados` só nasce na Fase 2. O ALTER dela conta com o 42P01
    benigno — pular por engano esconderia a coluna no dia em que a tabela
    aparecer."""
    assert db._motivo_para_pular(
        "ALTER TABLE securitizados ADD COLUMN issuer_id INTEGER", CATALOGO
    ) is None


def test_sem_catalogo_nada_e_filtrado():
    """SQLite não tem `information_schema`. Sem catálogo o comportamento
    volta a ser o antigo: manda tudo."""
    for stmt in ("ALTER TABLE debentures ADD COLUMN setor VARCHAR(80)",
                 "ALTER TABLE debentures ALTER COLUMN codigo TYPE VARCHAR(40)"):
        assert db._motivo_para_pular(stmt, None) is None


def test_comando_de_formato_desconhecido_sempre_sai():
    """A regra é errar para o lado de rodar à toa, nunca para o lado de
    pular o que era necessário."""
    assert db._motivo_para_pular("CREATE INDEX seja_o_que_for ON x (y)", CATALOGO) is None
    assert db._motivo_para_pular(
        "ALTER TABLE debentures ALTER COLUMN codigo TYPE TEXT", CATALOGO
    ) is None


def test_todo_comando_da_lista_e_reconhecido_pelas_regex():
    """Se alguém acrescentar um ALTER num formato que as regex não leem, ele
    passa a rodar sempre — funciona, mas volta a custar uma trava por rodada.
    Este teste avisa."""
    import inspect as _inspect
    import re

    fonte = _inspect.getsource(db.run_migrations)
    comandos = re.findall(r'^\s+"(ALTER TABLE [^"]+)"', fonte, re.M)
    assert len(comandos) >= 15, "a lista de migrações encolheu inesperadamente"
    nao_lidos = [
        c for c in comandos
        if not db._RE_ADD.match(c) and not db._RE_TIPO.match(c)
    ]
    assert nao_lidos == [], f"formato novo que o catálogo não sabe conferir: {nao_lidos}"


# --------------------------------------------------------------------------
# 2. Contenção é reportada como contenção
# --------------------------------------------------------------------------

class _ErroFalso(Exception):
    def __init__(self, sqlstate):
        super().__init__(f"erro simulado {sqlstate}")
        self.sqlstate = sqlstate


def test_lock_timeout_e_tratado_como_contencao_e_nao_como_erro_do_comando():
    assert db._sqlstate(_ErroFalso("55P03")) in db.SQLSTATE_CONTENCAO
    assert db._sqlstate(_ErroFalso("57014")) in db.SQLSTATE_CONTENCAO
    # e continua não sendo confundido com "já existe"
    assert not db._e_benigno(_ErroFalso("55P03"))


def test_coluna_ja_existe_continua_passando_calada():
    assert db._e_benigno(_ErroFalso("42701"))
    assert db._e_benigno(_ErroFalso("42P01"))


def test_contencao_tenta_de_novo_e_a_mensagem_final_culpa_a_trava(monkeypatch):
    """O ponto do teste: `_executar_ddl` não pode desistir na primeira
    negativa de trava, e quando desistir tem que dizer POR QUE — a mensagem
    crua do Postgres ('canceling statement due to...') faz o leitor procurar
    defeito no comando."""
    tentativas_feitas = []

    class _ConexaoFalsa:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def exec_driver_sql(self, stmt):
            if stmt.startswith("SET"):
                return None
            tentativas_feitas.append(stmt)
            raise _ErroFalso("55P03")
        def commit(self): pass

    monkeypatch.setattr(db.engine, "connect", lambda: _ConexaoFalsa())
    monkeypatch.setattr(db.time, "sleep", lambda s: None)

    erro = db._executar_ddl("ALTER TABLE debentures ADD COLUMN x INTEGER", tentativas=3)

    assert len(tentativas_feitas) == 3, "desistiu cedo demais numa falha transitória"
    assert erro is not None
    assert "trava não abriu" in erro


def test_erro_que_nao_e_contencao_nao_fica_repetindo(monkeypatch):
    """Permissão negada não melhora na terceira vez — insistir só atrasa a
    notícia ruim."""
    tentativas_feitas = []

    class _ConexaoFalsa:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def exec_driver_sql(self, stmt):
            if stmt.startswith("SET"):
                return None
            tentativas_feitas.append(stmt)
            raise _ErroFalso("42501")  # insufficient_privilege
        def commit(self): pass

    monkeypatch.setattr(db.engine, "connect", lambda: _ConexaoFalsa())
    erro = db._executar_ddl("ALTER TABLE debentures ADD COLUMN x INTEGER", tentativas=3)
    assert len(tentativas_feitas) == 1
    assert erro is not None and "trava não abriu" not in erro


# --------------------------------------------------------------------------
# 3. A view sai da frente e VOLTA
# --------------------------------------------------------------------------

def test_init_db_recria_as_views_mesmo_quando_o_ddl_falha():
    """A parte perigosa da correção. Dropar a view antes do DDL só é
    aceitável se ela voltar em QUALQUER saída — inclusive quando o
    `ensure_schema` levanta. Um `finally` é o que garante isso; sem ele, uma
    migração falha deixaria o banco sem `v_spread_rating`, e a aba Banco de
    Dados e o `app/spreads/analitico.py` leem essa view."""
    import inspect as _inspect

    from scripts import init_db

    fonte = _inspect.getsource(init_db.main)
    assert "dropar_views()" in fonte
    assert "finally:" in fonte
    depois_do_finally = fonte.split("finally:", 1)[1]
    assert "recriar_views()" in depois_do_finally


def test_recriar_views_nunca_levanta(monkeypatch):
    """Ela roda dentro do `finally`. Se levantar, some com a exceção original
    — que é a falha que o Allan precisa ler."""
    from scripts import init_db

    def _explodir(_engine):
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr("app.spreads.views.criar_views", _explodir)
    init_db.recriar_views()  # não deve levantar


# --------------------------------------------------------------------------
# 4. Script de manutenção fala pela porta de manutenção
# --------------------------------------------------------------------------

# CONTEXTO (09/09/2026, terceira rodada). As duas correções acima estavam
# certas e mesmo assim o `init_db` não andou: ele importava o `engine` de
# `app.db`, que é o da Vercel -- pooler em modo TRANSAÇÃO (6543), NullPool,
# 8s de connect_timeout. A execução morria em
#     (ECHECKOUTTIMEOUT) unable to check out connection from the pool
#     after 15000ms in Transaction mode
# ANTES de qualquer migração. A mesma execução com `:5432` forçado na
# variável de ambiente conectava e rodava o DDL inteiro -- prova de que o
# problema era a porta, não o banco nem o comando.


def test_troca_o_pooler_de_transacao_pelo_de_sessao():
    saida = db.url_de_manutencao(
        "postgresql+psycopg://u:s@aws-1-sa-east-1.pooler.supabase.com:6543/postgres"
    )
    assert ":5432/" in saida and ":6543/" not in saida


def test_senha_com_caractere_especial_sobrevive_a_troca():
    """A troca é feita pelo objeto URL do SQLAlchemy, não por `str.replace`.
    A senha vai percent-encoded na URL; a volta tem que devolver a original
    intacta -- inclusive o `:6543/` que um `str.replace(":6543/", ":5432/")`
    ingênuo pegaria DENTRO dela."""
    from sqlalchemy.engine import make_url

    senha = "s3nh@:6543/estranha"
    original = (
        make_url("postgresql+psycopg://usuario@aws-1.pooler.supabase.com:6543/postgres")
        .set(password=senha)
        .render_as_string(hide_password=False)
    )

    alvo = make_url(db.url_de_manutencao(original))
    assert alvo.password == senha
    assert alvo.port == 5432
    assert alvo.database == "postgres"


def test_nao_mexe_em_url_que_nao_e_o_pooler_de_transacao():
    for url in (
        "postgresql+psycopg://u:s@db.projeto.supabase.co:5432/postgres",   # conexão direta
        "postgresql+psycopg://u:s@aws-1.pooler.supabase.com:5432/postgres",  # já é sessão
        "postgresql+psycopg://u:s@meu-postgres-na-oci:6543/postgres",      # não é Supabase
        "sqlite:///data/credit_monitor.db",
    ):
        assert db.url_de_manutencao(url) == url


def test_init_db_nao_usa_o_engine_da_vercel():
    """O teste que teria pego o defeito. `from app.db import engine` num
    script de manutenção é sempre erro: aquele engine existe para uma função
    serverless de 10 segundos."""
    import inspect as _inspect

    from scripts import init_db

    fonte = _inspect.getsource(init_db)
    cabecalho = fonte.split("def ", 1)[0]
    assert "criar_engine_manutencao()" in cabecalho
    assert "import Base, engine," not in cabecalho


def test_importar_taxonomia_tambem():
    import inspect as _inspect

    from scripts import importar_taxonomia

    fonte = _inspect.getsource(importar_taxonomia)
    assert "criar_sessao_manutencao" in fonte


def test_init_db_confere_a_conexao_antes_de_trabalhar():
    """Banco fora do ar tem que virar uma linha, não noventa."""
    import inspect as _inspect

    from scripts import init_db

    fonte = _inspect.getsource(init_db.main)
    antes_do_ddl = fonte.split("ensure_schema", 1)[0]
    assert "conferir_conexao()" in antes_do_ddl


# --------------------------------------------------------------------------
# 5. A lista de migrações precisa cobrir TODAS as tabelas que ganharam a coluna
# --------------------------------------------------------------------------

# CONTEXTO (09/09/2026). Depois de tudo acima funcionar, o `init_db` do Allan
# rodou limpo e mesmo assim reportou três colunas faltando -- em
# `securitizados`, não em `debentures`. A taxonomia foi acrescentada às duas
# tabelas no `models.py` e só a uma delas na lista de migrações. Nenhuma
# execução daria certo, porque o comando não existia.
#
# Quem pegou foi o `conferir_colunas`, perguntando ao catálogo em vez de
# confiar em "nenhuma migração falhou". Estes testes trazem a checagem para
# antes do banco.


def _adds_da_lista() -> set[tuple[str, str]]:
    import inspect as _inspect
    import re

    fonte = _inspect.getsource(db.run_migrations)
    fora = set()
    for comando in re.findall(r'^\s+"(ALTER TABLE [^"]+)"', fonte, re.M):
        m = db._RE_ADD.match(comando)
        if m:
            fora.add((m.group(1), m.group(2)))
    return fora


def test_taxonomia_cobre_as_duas_tabelas_que_a_declaram():
    """`setor`, `subsetor` e `grupo_economico` estão em `debentures` E em
    `securitizados`. Quem declara no modelo precisa de ALTER na lista.

    Só vale para tabela que JÁ APARECE na lista de migrações. Essa é a marca
    de que ela existe em bancos antigos e recebe coluna por ALTER. `issuers`
    nasceu em 04/08/2026 com `setor` e `grupo_economico` dentro, criada
    inteira pelo `create_all` -- exigir ALTER dela seria exigir um comando
    que nunca teve razão de existir.
    """
    from app import models  # noqa: F401
    from app.db import Base

    adds = _adds_da_lista()
    tabelas_migradas = {tab for tab, _ in adds}
    faltando = []
    for coluna in ("setor", "subsetor", "grupo_economico"):
        for nome, tabela in Base.metadata.tables.items():
            if nome not in tabelas_migradas:
                continue
            if coluna in tabela.columns and (nome, coluna) not in adds:
                faltando.append(f"{nome}.{coluna}")
    assert faltando == [], (
        "coluna de taxonomia no models.py sem ALTER em run_migrations: "
        + ", ".join(faltando)
    )


def test_toda_migracao_aponta_para_coluna_que_o_modelo_tem():
    """O reverso: um ALTER para coluna que o modelo não declara é lixo que
    pega trava por nada, toda vez."""
    from app import models  # noqa: F401
    from app.db import Base

    sobrando = [
        f"{tab}.{col}"
        for tab, col in _adds_da_lista()
        if tab in Base.metadata.tables and col not in Base.metadata.tables[tab].columns
    ]
    assert sobrando == [], f"ALTER sem coluna correspondente no models.py: {sobrando}"


def test_init_db_separa_falta_de_migracao_de_falha_de_execucao():
    """A mensagem que mandou o Allan para o lado errado. Coluna sem ALTER
    tem que ser dita como tal -- 'rode de novo' nunca a criaria."""
    from scripts import init_db

    # `securitizados.setor` agora TEM migração; invento uma que não tem.
    sem = init_db._colunas_sem_migracao(
        ["securitizados.setor", "debentures.coluna_que_ninguem_pediu"]
    )
    assert sem == {"debentures.coluna_que_ninguem_pediu"}


# --------------------------------------------------------------------------
# 6. `options` na conexão: remédio da Supabase, veneno no PgBouncer
# --------------------------------------------------------------------------

# CONTEXTO (10/09/2026). Com o Postgres próprio já de pé, a primeira conexão
# pelo PgBouncer respondeu:
#
#     FATAL: unsupported startup parameter in options: statement_timeout
#
# Um pooler em modo transação recusa parâmetro de inicialização que não
# conhece, e com razão: a conexão de servidor é reaproveitada entre clientes,
# e um `SET` de um vazaria para o próximo. Ou seja, o `options` que consertou
# a Supabase impediria o `init_db` de falar com o banco novo.


def test_options_vai_para_a_supabase():
    args = db.connect_args_manutencao(
        "postgresql+psycopg://u:s@aws-1-sa-east-1.pooler.supabase.com:5432/postgres"
    )
    assert "statement_timeout" in args.get("options", "")
    assert "lock_timeout" in args["options"]


def test_options_nao_vai_para_o_pgbouncer_proprio():
    """O teste que existe por causa do erro. Sem esta condição, o `init_db`
    apontado para o banco novo não conecta."""
    args = db.connect_args_manutencao(
        "postgresql+psycopg://credit_admin:s@credit-research-dashboard.duckdns.org:6432/credit_monitor"
    )
    assert "options" not in args, (
        "o PgBouncer em modo transação recusa a conexão inteira por causa disto"
    )
    # o resto da configuração continua valendo
    assert args["prepare_threshold"] is None
    assert args["connect_timeout"] == 30


def test_sqlite_nao_recebe_parametro_de_postgres():
    assert db.connect_args_manutencao("sqlite:///data/x.db") == {"check_same_thread": False}


def test_reconhece_as_duas_formas_de_supabase():
    assert db._e_supabase("postgresql://u@aws-1-sa-east-1.pooler.supabase.com:5432/postgres")
    assert db._e_supabase("postgresql://u@db.ggomvbrjsefsaloxvksp.supabase.co:5432/postgres")
    assert not db._e_supabase("postgresql://u@credit-research-dashboard.duckdns.org:6432/x")
    assert not db._e_supabase("sqlite:///x.db")


def test_o_engine_de_manutencao_usa_esses_args(monkeypatch):
    """Liga a função testada ao engine de verdade -- sem isto, os testes acima
    poderiam passar com a função sendo ignorada por quem a chama."""
    capturado = {}

    def _falso(destino, connect_args=None, **kw):
        capturado["destino"] = destino
        capturado["connect_args"] = connect_args
        return "engine-de-mentira"

    monkeypatch.setattr(db, "create_engine", _falso)
    db.criar_engine_manutencao(
        "postgresql+psycopg://credit_admin:s@credit-research-dashboard.duckdns.org:6432/credit_monitor"
    )
    assert "options" not in capturado["connect_args"]

    db.criar_engine_manutencao(
        "postgresql+psycopg://u:s@aws-1-sa-east-1.pooler.supabase.com:6543/postgres"
    )
    assert "options" in capturado["connect_args"]
    assert ":5432/" in capturado["destino"], "e a troca de porta continua valendo"


# --------------------------------------------------------------------------
# 7. Âncora de confiança para o TLS: um caminho que vale em todo ambiente
# --------------------------------------------------------------------------

# CONTEXTO (10/09/2026). Depois de trocar a DATABASE_URL, o dashboard voltou
# 500 e o log da Vercel dizia:
#
#     psycopg.OperationalError: connection failed: connection to server at
#     "193.123.105.175", port 6432 failed: SSL error: certificate verify failed
#
# O mesmo erro tinha aparecido no Windows minutos antes. Causa comum:
# `sslmode=verify-full` exige uma âncora, e `sslrootcert=system` significa "o
# repositório do OpenSSL", que existe no Linux comum mas não no OpenSSL
# embutido do psycopg (Windows) nem no runtime da Vercel.
#
# A saída não foi descobrir o caminho certo de cada ambiente -- é caçar um
# problema que volta sempre --, e sim apontar para o `certifi`, que já vem
# junto do `requests` e está em todos eles.


def test_indica_o_certifi_para_postgres():
    caminho = db.raizes_confiaveis(
        "postgresql+psycopg://u:s@credit-research-dashboard.duckdns.org:6432/x?sslmode=verify-full"
    )
    assert caminho is not None
    import os

    assert os.path.exists(caminho), "o pacote de raízes tem que existir de verdade"


def test_o_pacote_traz_as_raizes_do_lets_encrypt():
    """Se o certifi um dia deixar de trazer a ISRG Root, o TLS do banco para
    de validar -- e o erro apareceria em produção, não aqui."""
    caminho = db.raizes_confiaveis("postgresql://u@host/x?sslmode=verify-full")
    conteudo = open(caminho, encoding="utf-8", errors="ignore").read()
    assert "ISRG Root X1" in conteudo or "ISRG Root X2" in conteudo


def test_respeita_sslrootcert_explicito_na_url():
    """Quem escreveu a URL manda."""
    assert db.raizes_confiaveis("postgresql://u@h/x?sslmode=verify-full&sslrootcert=system") is None
    assert db.raizes_confiaveis("postgresql://u@h/x?sslrootcert=/tmp/meu.pem") is None


def test_sqlite_nao_recebe_certificado():
    assert db.raizes_confiaveis("sqlite:///data/x.db") is None


def test_o_engine_do_app_leva_o_certificado():
    """É o engine que a Vercel usa. Se ele não levar, o dashboard cai com
    'certificate verify failed' e ninguém liga isso a este arquivo."""
    if db.engine.dialect.name == "postgresql":
        assert "sslrootcert" in db.connect_args
    else:
        assert "sslrootcert" not in db.connect_args, "SQLite não usa TLS"


def test_o_engine_de_manutencao_tambem(monkeypatch):
    capturado = {}
    monkeypatch.setattr(
        db, "create_engine",
        lambda destino, connect_args=None, **kw: capturado.update(
            destino=destino, connect_args=connect_args) or "engine",
    )
    db.criar_engine_manutencao(
        "postgresql+psycopg://u:s@credit-research-dashboard.duckdns.org:6432/x?sslmode=verify-full"
    )
    assert "sslrootcert" in capturado["connect_args"]


# --------------------------------------------------------------------------
# 8. O mesmo conceito tem a mesma largura em toda tabela
# --------------------------------------------------------------------------

# CONTEXTO (11/09/2026). A revisão do modelo achou `setor` com três larguras:
# VARCHAR(120) em `issuers`, VARCHAR(80) em `debentures` e `securitizados`.
# Idem `grupo_economico` (200 contra 120) e `indexador` (30 contra 20).
#
# Não é só redundância. É um valor que cabe numa coluna e não cabe na outra:
# copiar de `issuers` para `debentures` faria o Postgres recusar a linha com
#     value too long for type character varying(80)
# e o erro apareceria no coletor, longe de qualquer decisão de modelagem.
#
# Este teste não guarda as oito correções — guarda a REGRA, para a próxima
# coluna nascer certa.

# Colunas que nomeiam o mesmo conceito onde quer que apareçam. `sub_setor`
# (com underscore, em `issuers`) e `subsetor` são o mesmo conceito escrito de
# dois jeitos -- a diferença de nome é outra dívida, anotada mas não corrigida
# aqui, porque renomear coluna mexe na view e no código que a lê.
CONCEITOS = {
    "setor": "setor",
    "subsetor": "subsetor",
    "sub_setor": "subsetor",
    "grupo_economico": "grupo_economico",
    "indexador": "indexador",
    "emissor": "emissor",
}


def _larguras_por_conceito():
    from app import models  # noqa: F401
    from app.db import Base
    from sqlalchemy import String

    achados: dict[str, dict[str, int]] = {}
    for nome, tabela in Base.metadata.tables.items():
        for coluna in tabela.columns:
            conceito = CONCEITOS.get(coluna.name)
            if conceito is None or not isinstance(coluna.type, String):
                continue
            if coluna.type.length is None:
                continue
            achados.setdefault(conceito, {})[f"{nome}.{coluna.name}"] = coluna.type.length
    return achados


def test_conceito_igual_tem_largura_igual():
    divergentes = {}
    for conceito, onde in _larguras_por_conceito().items():
        if len(set(onde.values())) > 1:
            divergentes[conceito] = onde
    assert divergentes == {}, (
        "mesmo conceito com larguras diferentes — um valor que cabe numa tabela "
        f"não caberia na outra: {divergentes}"
    )


def test_o_alargamento_tem_migracao_correspondente():
    """models.py alargado sem ALTER na lista deixa o banco de produção para
    trás — e o `create_all` não alarga coluna de tabela que já existe."""
    import inspect as _inspect
    import re

    fonte = _inspect.getsource(db.run_migrations)
    alters = {
        (m.group(1), m.group(2)): int(m.group(3))
        for m in (db._RE_TIPO.match(c) for c in
                  re.findall(r'^\s+"(ALTER TABLE [^"]+)"', fonte, re.M))
        if m
    }
    from app import models  # noqa: F401
    from app.db import Base
    from sqlalchemy import String

    faltando = []
    for conceito, onde in _larguras_por_conceito().items():
        for caminho, largura in onde.items():
            tabela, coluna = caminho.split(".")
            declarada = Base.metadata.tables[tabela].columns[coluna]
            if not isinstance(declarada.type, String):
                continue
            # só exigimos ALTER onde a lista já cuida daquela tabela
            tabelas_migradas = {t for t, _ in alters}
            if tabela in tabelas_migradas and alters.get((tabela, coluna), largura) != largura:
                faltando.append(f"{caminho}: modelo={largura}, migração={alters[(tabela, coluna)]}")
    assert faltando == [], f"models.py e run_migrations discordam: {faltando}"
