"""Importa o banco de trabalho do Allan (`ws.credit_research.db`).

Ele mandou o banco real em 04/08/2026 — com dado público até HOJE, e com
as três coisas que faltavam:

- `cadastro_tickers` (4.794 tickers, setor/subsetor/grupo 100% preenchidos)
  — a "tabela pronta" que ele tinha mencionado;
- `ratings` (78.197 observações, 110 datas desde 03/01/2025) — o histórico
  anterior a abr/2026 que não existia no snapshot;
- `base_cri_cra` e `spreads` atualizados até 04/08/2026.

A view dele (`vw_base_final`) confirma a regra que eu havia inferido:

    LEFT JOIN ratings r ON s.codigo = r.ticker
     AND r.data_rating = (SELECT MAX(r2.data_rating) FROM ratings r2
                          WHERE r2.ticker = s.codigo
                            AND r2.data_rating <= s.data_ref)

Junção as-of **por ticker**, `data_rating <= data_ref`. É exatamente o que
`issuer_rating_periodo` implementa.

    python -m scripts.importar_ws_credit_research --ws /caminho/ws.credit_research.db --dry-run
    python -m scripts.importar_ws_credit_research --ws /caminho/ws.credit_research.db

O QUE É IMPORTADO E O QUE NÃO É
-------------------------------
- `cadastro_tickers` -> `issuers` (taxonomia) + `debentures.issuer_id`.
- `ratings`          -> `issuer_ratings` (eventos de MUDANÇA) + períodos
                        `HISTORICO`.
- `ativos`           -> lista de exclusão em `app_settings`.
- `base_cri_cra`     -> `securitizados` / `securitizado_spreads`.
- `spreads`          -> só as datas que o `credit_monitor` ainda NÃO tem.

Sobre a última: o `credit_monitor` já tem 504 datas DIÁRIAS da API oficial
(jul/2024 a jul/2026); o banco do Allan tem 91 datas semanais (jan/2025 a
ago/2026). A base daqui é mais densa no passado, a dele é mais recente —
então importa-se só o complemento, sem sobrescrever o que já existe.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import Base, SessionLocal, engine, run_migrations  # noqa: E402
from app.models import (  # noqa: E402
    AppSetting,
    Debenture,
    DebentureSpread,
    Issuer,
)
from app.spreads import issuers as isv  # noqa: E402
from app.spreads import persist_securitizados as psec  # noqa: E402
from app.spreads import securitizados as sec  # noqa: E402
from app.spreads.issuer_key import issuer_key  # noqa: E402
from app.spreads.queries import TICKERS_EXCLUIDOS_SETTING_KEY  # noqa: E402
from app.spreads.ratings import PADRAO_TO_PESO, SEM_RATING, eh_vazio, peso_de  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("importar_ws")

CAMPO_AGENCIA = {"fitch": "FITCH", "sp": "SP", "moodys": "MOODYS"}
ORIGEM = "WS_CREDIT_RESEARCH"


def _txt(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "-", "N.A.", "nan", "None") else s


def _dt(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _abrir(caminho: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# 1. Taxonomia
# ---------------------------------------------------------------------------

# Valores que significam "ainda não classificado". Perdem para qualquer
# classificação específica quando duas grafias do mesmo emissor divergem.
_GENERICOS = {"outros", "outro", "n/a", "na", "-", ""}


def _melhor_taxonomia(a: tuple, b: tuple) -> tuple:
    """Entre duas taxonomias do mesmo emissor, fica a mais específica.

    MOTIVO (05/08/2026): o `cadastro_tickers` do Allan tem o mesmo emissor
    cadastrado em duas grafias — uma com acento, outra sem — e às vezes só
    UMA delas está classificada:

        MRV ENGENHARIA E PARTICIPAÇÕES S.A.  -> "Outros"
        MRV ENGENHARIA E PARTICIPACOES S.A.  -> "Incorporadoras, Shoppings..."
        JSL S.A.                             -> "Outros"
        JSL S.A                              -> "Transportes"

    São 624 linhas da view dele saindo como "Outros" por causa disso — os
    tickers que apontam para a cópia não classificada. Não é ambiguidade
    real: é a mesma empresa, e uma das fichas ficou por preencher.

    Campo a campo, porque a grafia boa de setor pode estar numa ficha e a
    de grupo econômico na outra.
    """
    escolhida = []
    for va, vb in zip(a, b):
        ga = (va or "").strip().lower() in _GENERICOS
        gb = (vb or "").strip().lower() in _GENERICOS
        if ga and not gb:
            escolhida.append(vb)
        elif gb and not ga:
            escolhida.append(va)
        else:
            escolhida.append(va if va else vb)
    return tuple(escolhida)


def importar_taxonomia(db, ws, *, dry_run: bool) -> dict:
    """`cadastro_tickers` -> `issuers` + mapa ticker -> emissor.

    A taxonomia é por EMISSOR (validado: nenhum emissor tem dois setores),
    mas o cadastro é por ticker. Agrupa por `nome_anbima`; conflito dentro
    do mesmo emissor é reportado, não silenciado.
    """
    linhas = ws.execute("SELECT * FROM cadastro_tickers").fetchall()
    por_emissor: dict[str, dict] = {}
    ticker_para_nome: dict[str, str] = {}
    conflitos: list[tuple] = []

    for r in linhas:
        nome = _txt(r["nome_anbima"]) or _txt(r["nome_empresa"])
        ticker = _txt(r["ticker"])
        if not nome or not ticker:
            continue
        ticker_para_nome[ticker.upper()] = nome
        chave = issuer_key(nome)
        if not chave:
            continue
        tax = (_txt(r["setor"]), _txt(r["sub_setor"]), _txt(r["grupo_economico"]))
        atual = por_emissor.get(chave)
        if atual is None:
            por_emissor[chave] = {"nome": nome, "tax": tax, "n": 1, "grafias": {nome}}
        else:
            atual["n"] += 1
            atual["grafias"].add(nome)
            if atual["tax"] != tax:
                escolhida = _melhor_taxonomia(atual["tax"], tax)
                if escolhida != atual["tax"]:
                    atual["tax"] = escolhida
                conflitos.append((nome, atual["tax"], tax))

    criados = atualizados = 0
    if not dry_run:
        for chave, info in por_emissor.items():
            issuer = db.scalar(select(Issuer).where(Issuer.key == chave))
            if issuer is None:
                issuer = Issuer(key=chave, nome=info["nome"], nome_anbima=info["nome"])
                db.add(issuer)
                db.flush()
                criados += 1
            else:
                atualizados += 1
            setor, sub, grupo = info["tax"]
            isv.aplicar_taxonomia(issuer, setor=setor, sub_setor=sub,
                                  grupo_economico=grupo, origem=isv.ORIGEM_SNAPSHOT)
        db.commit()

    return {"tickers": len(ticker_para_nome), "emissores": len(por_emissor),
            "criados": criados, "atualizados": atualizados,
            "conflitos": conflitos, "ticker_para_nome": ticker_para_nome}


# ---------------------------------------------------------------------------
# 2. Ratings — observações viram eventos de mudança
# ---------------------------------------------------------------------------

def importar_ratings(db, ws, *, dry_run: bool) -> dict:
    """`ratings` (78.197 observações) -> eventos + períodos congelados.

    A tabela do Allan é uma FOTO por (ticker, data): o mesmo rating se
    repete toda semana enquanto não muda. Guardar tudo seria 78 mil linhas
    para algumas centenas de mudanças reais, então só as MUDANÇAS viram
    ação, e a série de `rating` (o médio dele, copiado) vira período
    `HISTORICO`.

    Copiado, não recalculado: é a regra que ele deu — *"o histórico não
    deve ser alterado, se para o ticker x o rating era y na data z,
    manter"*.
    """
    linhas = ws.execute(
        "SELECT ticker, nome_anbima, nome_empresa, data_rating, fitch, sp, moodys,"
        "       rating, perspectiva_atual, rating_anterior, acao_de_rating, link, origem"
        "  FROM ratings ORDER BY ticker, data_rating"
    ).fetchall()

    # ticker -> {data: {agencia: rating, "_medio": rating}}
    serie: dict[str, dict[date, dict]] = defaultdict(dict)
    nome_do_ticker: dict[str, str] = {}
    invalidos: dict[tuple, int] = defaultdict(int)

    for r in linhas:
        ticker = _txt(r["ticker"])
        dt = _dt(r["data_rating"])
        if not ticker or dt is None:
            continue
        nome = _txt(r["nome_anbima"]) or _txt(r["nome_empresa"])
        if nome:
            nome_do_ticker.setdefault(ticker, nome)
        estado: dict = {}
        for campo, agencia in CAMPO_AGENCIA.items():
            valor = _txt(r[campo])
            if valor is None or eh_vazio(valor):
                continue
            if peso_de(campo, valor) is None:
                invalidos[(campo, valor)] += 1
                continue
            estado[agencia] = valor
        estado["_medio"] = _txt(r["rating"]) or SEM_RATING
        serie[ticker][dt] = estado

    acoes = periodos = 0
    sem_issuer: dict[str, int] = {}
    # Série do EMISSOR (codigo=None): a união das observações de todos os
    # tickers dele. Existe porque `IssuerRatingAtual` e o fallback de
    # `rating_em()` operam nesse escopo — sem ela, uma debênture NOVA
    # (que não estava no cadastro do Allan) ficaria sem rating nenhum, e
    # a tela "rating vigente por emissor" sairia vazia.
    serie_emissor: dict[int, dict[date, dict]] = defaultdict(dict)

    for ticker, obs in serie.items():
        nome = nome_do_ticker.get(ticker)
        issuer = isv.resolver_issuer(db, nome) if nome else None
        if issuer is None:
            sem_issuer[nome or ticker] = sem_issuer.get(nome or ticker, 0) + 1
            continue
        # Alimenta a série do emissor com o que este ticker observou.
        for dt, estado in obs.items():
            alvo = serie_emissor[issuer.id].setdefault(dt, {})
            for ag, rt in estado.items():
                alvo.setdefault(ag, rt)

        if dry_run:
            acoes += 1
            continue

        # Eventos: só quando o valor de uma agência MUDA.
        anterior: dict[str, str] = {}
        for dt in sorted(obs):
            for ag, rt in obs[dt].items():
                if ag == "_medio":
                    continue
                if anterior.get(ag) != rt:
                    if isv.registrar_acao_rating(
                        db, issuer, agencia=ag, rating=rt, data_acao=dt,
                        codigo=ticker, origem=ORIGEM,
                        acao="Observado na base do Allan",
                    ) is not None:
                        acoes += 1
                    anterior[ag] = rt

        # Períodos congelados a partir do rating médio DELE.
        congelada: list[tuple] = []
        for dt in sorted(obs):
            estado = dict(obs[dt])
            medio = estado.pop("_medio", SEM_RATING) or SEM_RATING
            if congelada and congelada[-1][1] == medio and congelada[-1][3] == estado:
                continue
            congelada.append((dt, medio, PADRAO_TO_PESO.get(medio), estado))
        periodos += isv.gravar_periodos_historicos(db, issuer.id, ticker, congelada)

    # Agora o escopo do EMISSOR, a partir da união montada acima.
    periodos_emissor = 0
    if not dry_run:
        for issuer_id, obs in serie_emissor.items():
            anterior: dict[str, str] = {}
            issuer = db.get(Issuer, issuer_id)
            for dt in sorted(obs):
                for ag, rt in obs[dt].items():
                    if ag == "_medio":
                        continue
                    if anterior.get(ag) != rt:
                        if isv.registrar_acao_rating(
                            db, issuer, agencia=ag, rating=rt, data_acao=dt,
                            codigo=None, origem=ORIGEM,
                            acao="Observado na base do Allan (nível emissor)",
                        ) is not None:
                            acoes += 1
                        anterior[ag] = rt
            congelada: list[tuple] = []
            for dt in sorted(obs):
                estado = dict(obs[dt])
                medio = estado.pop("_medio", SEM_RATING) or SEM_RATING
                if congelada and congelada[-1][1] == medio and congelada[-1][3] == estado:
                    continue
                congelada.append((dt, medio, PADRAO_TO_PESO.get(medio), estado))
            periodos_emissor += isv.gravar_periodos_historicos(db, issuer_id, None, congelada)
        db.commit()
    return {"observacoes": len(linhas), "tickers": len(serie), "acoes": acoes,
            "periodos": periodos, "periodos_emissor": periodos_emissor,
            "emissores": len(serie_emissor),
            "sem_issuer": sem_issuer, "invalidos": dict(invalidos)}


# ---------------------------------------------------------------------------
# 3. Lista de exclusão
# ---------------------------------------------------------------------------

def importar_exclusoes(db, ws, *, dry_run: bool) -> dict:
    """`ativos.considerar = 'Não'` -> `app_settings`.

    Na view dele isso vira a coluna `incluir`, usada para tirar papel da
    análise sem apagá-lo da base. O `credit_monitor` já tem o mesmo
    conceito (`spread_tickers_excluidos`), então só transfere.
    """
    excluidos = sorted({
        _txt(r["ticker"]) for r in ws.execute(
            "SELECT ticker FROM ativos WHERE considerar = 'Não'"
        ).fetchall() if _txt(r["ticker"])
    })
    if not dry_run:
        cfg = db.get(AppSetting, TICKERS_EXCLUIDOS_SETTING_KEY)
        if cfg is None:
            cfg = AppSetting(key=TICKERS_EXCLUIDOS_SETTING_KEY, value="")
            db.add(cfg)
        cfg.value = json.dumps(excluidos, ensure_ascii=False)
        db.commit()
    return {"excluidos": len(excluidos), "amostra": excluidos[:8]}


# ---------------------------------------------------------------------------
# 4. Securitizados
# ---------------------------------------------------------------------------

def importar_cri_cra(db, ws, *, dry_run: bool) -> dict:
    """Carga em LOTE de `base_cri_cra` (101 mil linhas, 302 dias).

    Usa `executemany` no driver, não o ORM.

    BUG REAL DE DESEMPENHO (05/08/2026): a primeira versão chamava
    `persist_securitizados.persistir_dia` uma vez por dia — o caminho
    correto pro job diário, que grava ~490 linhas. Para 302 dias de uma
    vez, cada chamada recarrega os 494 registros de cadastro e faz um
    commit próprio; a carga andou até ~87 mil linhas e travou.

    `persistir_dia` continua sendo o caminho da captura diária. Aqui, que
    é carga histórica de uma vez só, o ORM é a ferramenta errada.
    """
    linhas = ws.execute("SELECT * FROM base_cri_cra").fetchall()

    cadastro: dict[str, dict] = {}
    series: list[tuple] = []
    for r in linhas:
        norm = sec.normalizar(dict(r))
        if norm is None or norm["data"] is None:
            continue
        # A base dele já traz a taxa de NTN-B resolvida por papel.
        taxa_ntnb = r["taxa_ntnb_ref"] or None
        norm["taxa_ntnb_ref"] = taxa_ntnb
        norm["spread"] = sec.calcular_spread(norm["indexador"], norm["taxa_indicativa"], taxa_ntnb)
        # Cadastro: a última leitura de cada código vence (é o mais recente).
        cadastro[norm["codigo"]] = norm
        series.append((
            norm["codigo"], norm["data"].isoformat(), norm["taxa_indicativa"],
            norm["taxa_compra"], norm["taxa_venda"], norm["desvio_padrao"],
            norm["pu"], norm["pct_pu_par"], norm["pct_vne"], norm.get("pct_reune"),
            norm["duration"], norm["taxa_ntnb_ref"], norm["spread"],
        ))

    if dry_run:
        return {"linhas": len(linhas), "dias": len({s[1] for s in series}),
                "gravadas": 0, "ativos": len(cadastro)}

    agora = datetime.now(timezone.utc).isoformat()
    conn = db.connection().connection   # conexão DBAPI crua
    cur = conn.cursor()
    cur.executemany(
        "INSERT OR REPLACE INTO securitizados"
        " (codigo, tipo_ativo, emissor, originador_credito, serie, emissao,"
        "  data_vencimento, tipo_remuneracao, indexador, referencia_ntnb,"
        "  first_seen_at, last_seen_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(n["codigo"], n["tipo_ativo"], n["emissor"], n["originador_credito"],
          n["serie"], n["emissao"],
          n["data_vencimento"].isoformat() if n["data_vencimento"] else None,
          n["tipo_remuneracao"], n["indexador"], n["referencia_ntnb"], agora, agora)
         for n in cadastro.values()],
    )
    cur.executemany(
        "INSERT OR REPLACE INTO securitizado_spreads"
        " (codigo, data, taxa_indicativa, taxa_compra, taxa_venda, desvio_padrao,"
        "  pu, pct_pu_par, pct_vne, pct_reune, duration, taxa_ntnb_ref, spread)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        series,
    )
    db.commit()
    v = psec.vincular_originadores(db)
    return {"linhas": len(linhas), "dias": len({s[1] for s in series}),
            "gravadas": len(series), "ativos": len(cadastro),
            "com_issuer": v["com_issuer"], "sem_match": len(v["sem_match"])}


# ---------------------------------------------------------------------------
# 5. Spreads — só o complemento
# ---------------------------------------------------------------------------

def _spread_em_bps(spread, indexador) -> float | None:
    """Converte o spread de CDI+ da base do Allan para bps.

    BUG REAL (05/08/2026): na tabela `spreads` dele o CDI+ está em PONTOS
    PERCENTUAIS (média 1,36, máximo 17), enquanto o IPCA+ já está em bps
    (média 51,6, máximo 1.903). O `credit_monitor` guarda tudo em bps.

    Importar cru misturava as duas unidades na MESMA coluna: as datas
    vindas da API ficavam com CDI+ ~136 bps e as importadas com ~1,36 —
    uma queda de 99% no gráfico exatamente na emenda entre as duas
    origens, que pareceria um fechamento histórico de spread.

    É o mesmo erro que o CLAUDE.md registra ter sido corrigido em
    24/07/2026 no caminho da API (`fetch.py`: "o script original do Allan
    deixava esse valor em pontos percentuais"). Reintroduzi pelo caminho
    da importação; a correção é a mesma multiplicação por 100.

    IPCA+ NÃO é convertido — já vem em bps, e multiplicar de novo daria
    5.000 bps de spread médio.
    """
    if spread is None:
        return None
    return float(spread) * 100 if (indexador or "").upper().startswith("CDI") else float(spread)


def importar_spreads_faltantes(db, ws, *, dry_run: bool) -> dict:
    """Importa só as datas de `spreads` que o `credit_monitor` não tem.

    Não sobrescreve nada: a base daqui vem da API oficial e é diária; a
    dele é semanal. O que interessa é o rabo recente (25/07 em diante).
    """
    ja_tem = {d for (d,) in db.execute(select(DebentureSpread.data).distinct()).all()}
    ja_tem = {d.isoformat() if hasattr(d, "isoformat") else str(d) for d in ja_tem}
    datas_ws = [r[0] for r in ws.execute("SELECT DISTINCT data_ref FROM spreads ORDER BY data_ref")]
    faltantes = [d for d in datas_ws if d not in ja_tem]

    inseridas = novos_papeis = 0
    if not dry_run and faltantes:
        existentes = {c for (c,) in db.execute(select(Debenture.codigo)).all()}
        marcadores = ",".join("?" * len(faltantes))
        for r in ws.execute(
            f"SELECT * FROM spreads WHERE data_ref IN ({marcadores})", faltantes
        ):
            codigo = _txt(r["codigo"])
            dt = _dt(r["data_ref"])
            if not codigo or dt is None:
                continue
            if codigo not in existentes:
                db.add(Debenture(codigo=codigo, indexador=_txt(r["indexador"]),
                                 incentivada=_txt(r["deb_incent"])))
                existentes.add(codigo)
                novos_papeis += 1
                db.flush()
            db.add(DebentureSpread(
                codigo=codigo, data=dt,
                taxa_indicativa=r["taxa_indicativa"], pu=r["pu"],
                pct_pu_par=r["pct_pu_par"],
                spread=_spread_em_bps(r["spread"], r["indexador"]),
                estoque=r["estoque"], duration=r["duration"],
            ))
            inseridas += 1
        db.commit()
    return {"datas_ws": len(datas_ws), "faltantes": len(faltantes),
            "periodo_faltante": (faltantes[0], faltantes[-1]) if faltantes else None,
            "inseridas": inseridas, "novos_papeis": novos_papeis}


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ws", type=Path, required=True, help="caminho do ws.credit_research.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    Base.metadata.create_all(engine)
    run_migrations()
    db = SessionLocal()
    ws = _abrir(args.ws)
    try:
        logger.info("=== TAXONOMIA ===")
        t = importar_taxonomia(db, ws, dry_run=args.dry_run)
        logger.info("  %d tickers -> %d emissores (criados %d, atualizados %d)",
                    t["tickers"], t["emissores"], t["criados"], t["atualizados"])
        for nome, a, b in t["conflitos"][:5]:
            logger.warning("  conflito de taxonomia em %s: %s vs %s", nome[:40], a, b)

        logger.info("=== VÍNCULO DEBÊNTURE -> EMISSOR ===")
        if not args.dry_run:
            v = isv.vincular_debentures(db, t["ticker_para_nome"])
            logger.info("  %d ligadas (ticker %d, nome %d) | aliases %d | revisar %d | sem match %d",
                        v["ligadas"], v["por_ticker"], v["por_nome"],
                        len(v["aliases_criados"]), len(v["revisar"]), len(v["sem_match"]))

        logger.info("=== RATINGS ===")
        r = importar_ratings(db, ws, dry_run=args.dry_run)
        logger.info("  %d observações / %d tickers -> %d ações", r["observacoes"], r["tickers"], r["acoes"])
        logger.info("  períodos: %d por ticker + %d por emissor (%d emissores)",
                    r["periodos"], r.get("periodos_emissor", 0), r.get("emissores", 0))
        if r["invalidos"]:
            logger.info("  valores em formato inválido: %s", r["invalidos"])
        if r["sem_issuer"]:
            logger.info("  tickers sem emissor: %d", len(r["sem_issuer"]))

        logger.info("=== EXCLUSÕES ===")
        e = importar_exclusoes(db, ws, dry_run=args.dry_run)
        logger.info("  %d tickers excluídos %s", e["excluidos"], e["amostra"])

        logger.info("=== CRI/CRA ===")
        c = importar_cri_cra(db, ws, dry_run=args.dry_run)
        logger.info("  %d linhas em %d dias -> %d gravadas | %d ativos | %d com emissor (%d originadores sem match)", c["linhas"], c["dias"], c["gravadas"], c.get("ativos",0), c.get("com_issuer",0), c.get("sem_match",0))

        logger.info("=== SPREADS (complemento) ===")
        s = importar_spreads_faltantes(db, ws, dry_run=args.dry_run)
        logger.info("  %d datas na base do Allan, %d faltando aqui %s -> %d linhas, %d papéis novos",
                    s["datas_ws"], s["faltantes"], s["periodo_faltante"] or "",
                    s["inseridas"], s["novos_papeis"])

        if not args.dry_run:
            logger.info("=== PERÍODOS DERIVADOS E VIEW ===")
            rr = isv.recalcular_todos_ratings(db)
            from app.spreads.views import conferir_view, criar_views
            criar_views(engine)
            chk = conferir_view(engine)
            logger.info("  emissores com rating: %d | períodos derivados: %d",
                        rr["com_rating"], rr["periodos"])
            logger.info("  view: %s linhas | nulos %d | incoerentes %d | %s",
                        f"{chk['linhas_view']:,}", chk["rating_nulo"],
                        chk["incoerentes"], "OK" if chk["ok"] else "FALHOU")
        else:
            logger.info("[DRY-RUN] nada foi gravado.")
    finally:
        ws.close()
        db.close()


if __name__ == "__main__":
    main()
