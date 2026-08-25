"""Aba "Banco de Dados" — consulta e extração, só para administradores.

Pedido do Allan (12/08/2026): *"quero que você crie uma aba 'Banco de
Dados' disponível apenas para administradores que eu consiga consultar e
extrair os dados armazenados"*.

O que oferece:
- inventário de tabelas com contagem, período coberto e espaço ocupado;
- pré-visualização de qualquer tabela, com filtro de data e paginação;
- export CSV de tabela inteira ou de um recorte;
- SQL livre, **somente leitura**.

SEGURANÇA — por que o SQL livre não é um buraco
-----------------------------------------------
Executar SQL digitado numa tela é perigoso por natureza. As barreiras
aqui, em camadas, porque nenhuma sozinha basta:

1. **Só admin.** `require_admin` barra antes de qualquer coisa.
2. **Só SELECT/WITH.** A consulta é normalizada e precisa começar com um
   dos dois; qualquer outro verbo é recusado.
3. **Uma instrução só.** `;` no meio é recusado — é assim que se anexa um
   `DROP` a um `SELECT` legítimo.
4. **Lista negra de palavras-chave** de escrita e DDL, checada por
   fronteira de palavra (`\\bDELETE\\b` não casa com `DELETED_AT`).
5. **LIMIT obrigatório**, injetado se ausente.
6. **Conexão em modo leitura** quando o banco suporta (`SET TRANSACTION
   READ ONLY` no Postgres), que é a única barreira que o próprio banco
   garante — as anteriores são do nosso lado.

Mesmo assim, isto é uma ferramenta de administrador, não algo para expor
publicamente. A rota inteira está sob `require_admin` de propósito.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from .capacidade import diagnostico

logger = logging.getLogger(__name__)

router = APIRouter()

# Verbos aceitos no início da consulta.
_INICIO_OK = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)

# Palavras que nunca podem aparecer numa consulta de leitura. Checadas por
# fronteira de palavra pra não barrar coluna chamada "created_at" por
# conter "CREATE".
_PROIBIDAS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "REPLACE", "GRANT", "REVOKE", "ATTACH", "DETACH", "PRAGMA", "VACUUM",
    "COPY", "EXECUTE", "CALL", "MERGE",
)
_RE_PROIBIDAS = re.compile(r"\b(" + "|".join(_PROIBIDAS) + r")\b", re.IGNORECASE)

LIMITE_PADRAO = 500
LIMITE_MAXIMO = 5_000
LIMITE_EXPORT = 200_000

# Tabelas que a tela lista, com a coluna de data usada pro filtro de
# período. `None` = tabela de cadastro, sem série temporal.
TABELAS = {
    "debenture_spreads": "data",
    "securitizado_spreads": "data",
    "negocios_b3": "data_negocio",
    "negocios_b3_diario": "data",
    "issuer_ratings": "data_acao",
    "issuer_rating_periodo": "data_inicio",
    "issuer_rating_atual": None,
    "debentures": None,
    "securitizados": None,
    "issuers": None,
    "issuer_aliases": None,
    "articles": "published_at",
    "companies": None,
    "sources": None,
    "ntnb_referencia": "data",
    "run_logs": "started_at",
    "v_spread_rating": "data",
}


def validar_consulta(sql: str, limite: int = LIMITE_PADRAO) -> str:
    """Devolve o SQL pronto pra executar, ou levanta `ValueError`.

    Ver o cabeçalho do módulo para o raciocínio das barreiras.
    """
    if not sql or not sql.strip():
        raise ValueError("consulta vazia")
    limpo = sql.strip().rstrip(";").strip()

    if not _INICIO_OK.match(limpo):
        raise ValueError("só SELECT ou WITH são aceitos nesta tela")
    if ";" in limpo:
        raise ValueError(
            "uma instrução por vez — o ';' no meio da consulta é recusado"
        )
    achado = _RE_PROIBIDAS.search(limpo)
    if achado:
        raise ValueError(f"'{achado.group(1).upper()}' não é permitido: esta tela é somente leitura")

    limite = max(1, min(limite, LIMITE_MAXIMO))
    if not re.search(r"\bLIMIT\s+\d+", limpo, re.IGNORECASE):
        limpo = f"{limpo}\nLIMIT {limite}"
    return limpo


def _somente_leitura(db: Session) -> None:
    """Marca a transação como leitura no banco, quando ele suporta.

    É a única barreira que não depende de a nossa validação estar certa.
    O SQLite não tem equivalente por transação (só o modo `?mode=ro` na
    conexão), então lá as barreiras de aplicação são o que temos.
    """
    if not (db.bind and db.bind.dialect.name == "postgresql"):
        return
    try:
        # Precisa vir ANTES de qualquer consulta na transação corrente --
        # o Postgres recusa com "SET TRANSACTION must be called before any
        # query". Como a sessão pode já ter lido algo (o `get_db` é
        # compartilhado), garante-se um ponto limpo com `rollback()`.
        db.rollback()
        db.execute(text("SET TRANSACTION READ ONLY"))
    except Exception as exc:  # noqa: BLE001
        # É a barreira EXTRA, não a única: a validação da consulta já
        # recusou escrita antes de chegar aqui. Falhar em marcar a
        # transação não pode derrubar a tela.
        logger.warning("não consegui marcar a transação como somente leitura: %s", exc)


def _linhas_e_colunas(db: Session, sql: str, params: dict | None = None):
    rs = db.execute(text(sql), params or {})
    colunas = list(rs.keys())
    return colunas, [list(r) for r in rs]


# Views não entram na contagem estimada nem no MIN/MAX: no Postgres não há
# entrada de estatística para elas, e qualquer agregado força materializar o
# join inteiro. `v_spread_rating` sozinha tem ~98 mil linhas.
VIEWS = {"v_spread_rating", "issuer_rating_atual"}


def _limpar(db: Session) -> None:
    """Desfaz a transação depois de um erro esperado.

    No Postgres, um erro aborta a transação inteira: qualquer consulta
    seguinte falha com "current transaction is aborted, commands ignored
    until end of transaction block". Como este módulo usa try/except para
    tolerar tabela inexistente, sem o rollback a PRIMEIRA ausência derrubava
    todas as consultas seguintes -- e um `except` que segue em frente vira
    uma página inteira em branco ou um 500.
    """
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass


def _contagens_estimadas(db: Session) -> dict[str, int]:
    """Número aproximado de linhas por tabela, do catálogo do Postgres.

    POR QUE NÃO `COUNT(*)` (corrigido em 20/08/2026 -- a página dava 504):
    no Postgres, `COUNT(*)` sem WHERE varre a tabela inteira. Esta tela
    listava 17 tabelas, entre elas `debenture_spreads` (centenas de milhares
    de linhas) e uma VIEW, que precisa materializar o join só para contar.
    Somando os MIN/MAX, eram ~35 consultas pesadas numa página só, e o
    conjunto passava dos 60s de limite da função.

    `reltuples` é atualizado por ANALYZE/autovacuum: pode estar alguns
    porcento defasado, o que é irrelevante num inventário de "quanto tem
    aqui dentro" -- e é instantâneo, porque lê catálogo, não dado.

    Fora do Postgres devolve vazio e o chamador cai no COUNT(*) exato: em
    SQLite local as tabelas são pequenas e a contagem é barata.
    """
    if not (db.bind and db.bind.dialect.name == "postgresql"):
        return {}
    try:
        linhas = db.execute(text("""
            SELECT c.relname, GREATEST(c.reltuples, 0)::bigint
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'r'
        """)).all()
        return {r[0]: int(r[1]) for r in linhas}
    except Exception as exc:  # noqa: BLE001
        logger.warning("não consegui estimar contagens pelo catálogo: %s", exc)
        # ROLLBACK OBRIGATÓRIO: no Postgres, um erro aborta a transação
        # inteira e TODA consulta seguinte falha com "current transaction is
        # aborted". Sem isto, uma falha aqui contaminaria os COUNT(*) de
        # fallback e o MIN/MAX logo abaixo, transformando um aviso local
        # numa página quebrada.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {}


def inventario(db: Session, diag: dict | None = None) -> list[dict]:
    """Contagem, período coberto e tamanho de cada tabela.

    `diag` é recebido de fora para não chamar `diagnostico()` duas vezes na
    mesma página -- era o que acontecia até 20/08/2026.
    """
    if diag is None:
        diag = diagnostico(db.bind)
    tamanhos = {t["tabela"]: t for t in diag["tabelas"]}
    estimadas = _contagens_estimadas(db)

    saida = []
    for tabela, col_data in TABELAS.items():
        aproximado = False
        if tabela in estimadas:
            n, aproximado = estimadas[tabela], True
        elif tabela in VIEWS:
            n = None                      # contar view custa o join inteiro
        else:
            try:
                n = db.execute(text(f'SELECT COUNT(*) FROM "{tabela}"')).scalar() or 0
            except Exception:  # noqa: BLE001
                _limpar(db)               # tabela ainda não existe neste banco
                continue

        periodo = None
        # MIN/MAX só onde a coluna de data é indexada e a relação é tabela
        # real. Numa view, cada agregado é uma varredura completa.
        if col_data and tabela not in VIEWS:
            try:
                r = db.execute(
                    text(f'SELECT MIN("{col_data}"), MAX("{col_data}") FROM "{tabela}"')
                ).first()
                if r and r[0]:
                    periodo = {"de": str(r[0])[:10], "ate": str(r[1])[:10]}
            except Exception:  # noqa: BLE001
                _limpar(db)

        info = tamanhos.get(tabela, {})
        saida.append({
            "tabela": tabela,
            "linhas": n,
            "linhas_aproximadas": aproximado,
            "coluna_data": col_data,
            "periodo": periodo,
            "mb": round(info.get("mb", 0), 2),
        })
    return sorted(saida, key=lambda x: -(x["linhas"] or 0))


def _filtro_periodo(tabela: str, de: str | None, ate: str | None):
    col = TABELAS.get(tabela)
    if not col or not (de or ate):
        return "", {}
    cond, params = [], {}
    if de:
        cond.append(f'"{col}" >= :de')
        params["de"] = de
    if ate:
        cond.append(f'"{col}" <= :ate')
        params["ate"] = ate
    return " WHERE " + " AND ".join(cond), params


def registrar_rotas(app, require_admin, templates):
    """Registra as rotas. `require_admin` vem de app.py (evita import circular)."""

    @router.get("/banco", response_class=HTMLResponse)
    def pagina(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
        # Assinatura NOVA do Starlette: `request` primeiro, depois o nome
        # do template. A antiga (`nome, {"request": ...}`) falha com
        # "unhashable type: 'dict'" -- o resto do projeto (app.py,
        # spreads_routes.py) já usa esta forma.
        # `diagnostico` calculado UMA vez e repassado -- antes era chamado
        # aqui e de novo dentro de `inventario`, duplicando a consulta de
        # catálogo mais cara da página.
        diag = diagnostico(db.bind)
        # O INVENTÁRIO É ACESSÓRIO. A função principal desta tela é consultar
        # e extrair dado; se a contagem de linhas falhar, ela não pode levar
        # a página junto. Mesmo princípio que `capacidade.diagnostico` já
        # segue ("NUNCA levanta"), agora estendido para cá -- em 20/08/2026
        # esta página devolveu Internal Server Error e a tela não dava
        # nenhuma pista do motivo.
        erro_inventario = None
        try:
            tabelas = inventario(db, diag)
        except Exception as exc:  # noqa: BLE001
            logger.exception("inventário falhou")
            _limpar(db)
            tabelas = []
            erro_inventario = f"{type(exc).__name__}: {exc}"

        return templates.TemplateResponse(request, "banco.html", {
            "user": user,
            "tabelas": tabelas,
            "erro_inventario": erro_inventario,
            "capacidade": diag,
            "limite_padrao": LIMITE_PADRAO,
            "limite_maximo": LIMITE_MAXIMO,
        })

    @router.get("/api/banco/tabela")
    def ver_tabela(
        tabela: str,
        de: str | None = None,
        ate: str | None = None,
        pagina: int = Query(1, ge=1),
        por_pagina: int = Query(50, ge=1, le=500),
        user=Depends(require_admin),
        db: Session = Depends(get_db),
    ):
        if tabela not in TABELAS:
            raise HTTPException(400, f"tabela desconhecida: {tabela}")
        where, params = _filtro_periodo(tabela, de, ate)
        total = db.execute(text(f'SELECT COUNT(*) FROM "{tabela}"{where}'), params).scalar() or 0
        ordem = f' ORDER BY "{TABELAS[tabela]}" DESC' if TABELAS[tabela] else ""
        params = {**params, "lim": por_pagina, "off": (pagina - 1) * por_pagina}
        colunas, linhas = _linhas_e_colunas(
            db, f'SELECT * FROM "{tabela}"{where}{ordem} LIMIT :lim OFFSET :off', params
        )
        return {"tabela": tabela, "total": total, "pagina": pagina,
                "por_pagina": por_pagina, "colunas": colunas,
                "linhas": [[_json_seguro(v) for v in l] for l in linhas]}

    @router.post("/api/banco/consulta")
    def consulta(
        payload: dict,
        user=Depends(require_admin),
        db: Session = Depends(get_db),
    ):
        try:
            sql = validar_consulta(payload.get("sql", ""), int(payload.get("limite", LIMITE_PADRAO)))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _somente_leitura(db)
        try:
            colunas, linhas = _linhas_e_colunas(db, sql)
        except Exception as exc:  # noqa: BLE001
            # A mensagem do banco é o que ajuda a corrigir a consulta --
            # e quem está aqui já é admin.
            raise HTTPException(400, f"erro na consulta: {exc}") from exc
        return {"colunas": colunas, "n": len(linhas),
                "linhas": [[_json_seguro(v) for v in l] for l in linhas],
                "sql_executado": sql}

    @router.get("/api/banco/export")
    def exportar(
        tabela: str | None = None,
        sql: str | None = None,
        de: str | None = None,
        ate: str | None = None,
        user=Depends(require_admin),
        db: Session = Depends(get_db),
    ):
        """CSV de uma tabela (com recorte de período) ou de uma consulta."""
        if sql:
            consulta_sql = validar_consulta(sql, LIMITE_EXPORT)
            nome = "consulta"
        elif tabela:
            if tabela not in TABELAS:
                raise HTTPException(400, f"tabela desconhecida: {tabela}")
            where, _ = _filtro_periodo(tabela, de, ate)
            # Interpola as datas direto porque `text()` com params não
            # sobrevive ao streaming abaixo; já validadas como data.
            for chave, valor in (("de", de), ("ate", ate)):
                if valor:
                    _validar_data(valor)
                    where = where.replace(f":{chave}", f"'{valor}'")
            consulta_sql = f'SELECT * FROM "{tabela}"{where} LIMIT {LIMITE_EXPORT}'
            nome = tabela
        else:
            raise HTTPException(400, "informe 'tabela' ou 'sql'")

        _somente_leitura(db)
        rs = db.execute(text(consulta_sql))
        colunas = list(rs.keys())

        def gerar():
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")   # ';' abre direto no Excel BR
            w.writerow(colunas)
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)
            for linha in rs:
                w.writerow([_csv_br(v) for v in linha])
                yield buf.getvalue(); buf.seek(0); buf.truncate(0)

        carimbo = datetime.now().strftime("%Y%m%d_%H%M")
        return StreamingResponse(
            gerar(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{nome}_{carimbo}.csv"'},
        )

    app.include_router(router)
    return router


def _validar_data(valor: str) -> None:
    try:
        datetime.strptime(valor[:10], "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(400, f"data inválida: {valor}") from exc


def _json_seguro(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _csv_br(v):
    """Número com vírgula decimal — senão o Excel em pt-BR remonta o
    número e produz coisas como "8.567.994.822.9".

    É o mesmo bug já corrigido no export do módulo de spreads
    (CLAUDE.md, 27/07/2026); repetido aqui porque é outro caminho de
    saída e o erro reapareceria igual.
    """
    if isinstance(v, float):
        return f"{v:.6f}".rstrip("0").rstrip(".").replace(".", ",")
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v
