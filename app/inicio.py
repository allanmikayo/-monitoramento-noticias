"""Página inicial do Hub (26/10/2026, pedido do Allan): um resumo de tudo
que o dashboard tem, numa tela só, sem rolar.

Cada bloco tem a SUA rota (ver inicio_routes.py) e carrega sozinho. Uma
rota só para a tela inteira juntaria ~15 consultas a cinco módulos numa
mesma função da Vercel (limite de 10 s) -- e um bloco lento ou sem dado
derrubaria a página toda. Separados, cada cartão aparece quando fica
pronto e um erro fica restrito ao próprio cartão.

Nada aqui recalcula métrica: cada bloco chama a função que a aba de origem
já usa (spreads, secundário, primário, notícias, repositório), para o
número da página inicial ser sempre o mesmo da aba que o link abre.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import store
from .models import Article, OfertaCVM, Report
from .primario import queries as primario
from .primario.coleta import STATUS_MORTO
from .spreads import curva_juros, mercado_secundario, queries

SMART_URL = "https://www.itau.com.br/itaubba-pt/portal/credit/report/"


# ---------------------------------------------------------------------------
# Spreads: as duas classes, WoW, série de 3 meses e quem mexeu
# ---------------------------------------------------------------------------
def spreads(db: Session) -> dict:
    saida = []
    for classe in queries.CLASSES:
        k = queries.kpi_summary(db, classe, queries.COMPARACAO_BASES["WoW"])
        ref = k.get("data_referencia")
        serie = []
        if ref:
            fim = date.fromisoformat(ref)
            serie = [{"data": p["data"], "spread": p["spread_medio"]}
                     for p in queries.time_series(db, classe, inicio=fim - timedelta(days=92), fim=fim)]
        mv = queries.movers(db, classe, queries.COMPARACAO_BASES["WoW"], top_n=3)
        enxuto = lambda lista: [  # noqa: E731
            {"codigo": m["codigo"], "nome": m.get("nome"), "spread": m.get("spread"),
             "variacao_bps": m["variacao_bps"]} for m in lista]
        saida.append({
            "classe": classe,
            "data_referencia": ref,
            "data_comparacao": k.get("data_comparacao"),
            "spread": k.get("spread_medio"),
            "variacao_bps": k.get("variacao_bps"),
            "n_ativos": k.get("n_ativos"),
            "serie": serie,
            "aberturas": enxuto(mv.get("aberturas", [])),
            "fechamentos": enxuto(mv.get("fechamentos", [])),
        })
    return {"classes": saida}


# ---------------------------------------------------------------------------
# Mercado secundário: média de 5 dias úteis contra o último mês
# ---------------------------------------------------------------------------
def secundario(db: Session) -> dict:
    try:
        r = mercado_secundario.resumo(db)
    except Exception:  # tabela ainda não criada (nenhum export subido)
        return {"referencia": None}
    if not r.get("referencia"):
        return {"referencia": None}
    classes, soma5, somames = [], 0.0, 0.0
    for linha in r["linhas"]:
        j = linha["janelas"]
        m5 = (j.get("5d") or {}).get("media") or 0.0
        mm = (j.get("mes") or {}).get("media") or 0.0
        soma5, somames = soma5 + m5, somames + mm
        classes.append({"classe": linha["classe"], "media_5d": m5, "media_mes": mm})
    jan = {j["chave"]: j for j in r["janelas"]}
    return {
        "referencia": r["referencia"],
        "media_5d": soma5, "media_mes": somames,
        "variacao": (soma5 / somames - 1) if somames else None,
        "janela_5d": jan.get("5d"), "janela_mes": jan.get("mes"),
        "classes": classes,
    }


# ---------------------------------------------------------------------------
# Mercado primário: último mês fechado e acumulado do ano, por ENCERRAMENTO
# ---------------------------------------------------------------------------
def _volume_encerrado(db: Session, inicio: date, fim: date) -> tuple[float, int]:
    vol, n = db.execute(
        select(func.coalesce(func.sum(OfertaCVM.valor_total), 0), func.count(OfertaCVM.numero_requerimento))
        .where(OfertaCVM.data_encerramento >= inicio, OfertaCVM.data_encerramento <= fim,
               OfertaCVM.status.not_in(STATUS_MORTO))
    ).one()
    return float(vol or 0), int(n or 0)


def _mes_anterior(d: date) -> tuple[date, date]:
    primeiro_atual = d.replace(day=1)
    fim = primeiro_atual - timedelta(days=1)
    return fim.replace(day=1), fim


def _um_ano_antes(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29/02
        return d.replace(year=d.year - 1, day=28)


def primario_bloco(db: Session, hoje: date | None = None) -> dict:
    """Volume ENCERRADO (data de encerramento da CVM) de DEB, CRI e CRA.

    * Último mês fechado contra o mesmo mês do ano anterior (YoY).
    * Acumulado do ano até a data mais recente da base contra o mesmo
      intervalo do ano anterior (YTD vs YTD) -- comparar com o ano anterior
      inteiro faria todo ano parecer em queda até dezembro.
    """
    ate = hoje or primario._hoje(db)
    m_ini, m_fim = _mes_anterior(ate)
    mes, n_mes = _volume_encerrado(db, m_ini, m_fim)
    mes_aa, _ = _volume_encerrado(db, _um_ano_antes(m_ini), _um_ano_antes(m_fim))
    ytd, n_ytd = _volume_encerrado(db, date(ate.year, 1, 1), ate)
    ytd_aa, _ = _volume_encerrado(db, date(ate.year - 1, 1, 1), _um_ano_antes(ate))

    # Fila: só o que está de pé (parada há mais de 90 dias fica fora, como
    # no resumo da aba Primário), separada por status.
    fila = [o for o in primario.pipeline(db, (), ate) if not o["parada"]]
    por_status: dict[str, dict] = {}
    for o in fila:
        s = por_status.setdefault(o["status"], {"status": o["status"], "ofertas": 0, "volume": 0.0})
        s["ofertas"] += 1
        s["volume"] += o["valor"] / 1000  # R$ bi
    return {
        "disponivel_ate": ate.isoformat(),
        "mes": {"inicio": m_ini.isoformat(), "fim": m_fim.isoformat(), "volume": mes, "ofertas": n_mes,
                "volume_ano_anterior": mes_aa, "variacao": (mes / mes_aa - 1) if mes_aa else None},
        "ytd": {"inicio": date(ate.year, 1, 1).isoformat(), "fim": ate.isoformat(), "volume": ytd,
                "ofertas": n_ytd, "volume_ano_anterior": ytd_aa,
                "variacao": (ytd / ytd_aa - 1) if ytd_aa else None},
        "pipeline": {
            "ofertas": len(fila), "volume": sum(o["valor"] for o in fila) / 1000,
            "por_status": sorted(por_status.values(), key=lambda s: -s["volume"]),
        },
    }


# ---------------------------------------------------------------------------
# Curvas
# ---------------------------------------------------------------------------
def curvas(db: Session) -> dict:
    try:
        return curva_juros.curvas_comparadas(db)
    except Exception:  # tabela ainda não criada (backfill não rodou)
        db.rollback()
        return {"curvas": []}


# ---------------------------------------------------------------------------
# Notícias, ratings e relatórios
# ---------------------------------------------------------------------------
def _artigo(a: Article) -> dict:
    quando = a.published_at or a.found_at
    return {
        "titulo": a.title, "url": a.url, "fonte": a.source_name, "tipo": a.article_type,
        "quando": quando.isoformat() if quando else None,
        "empresas": [c.name for c in a.companies][:2],
        "setores": [s.name for s in a.sector_tags][:1],
    }


def noticias(db: Session) -> dict:
    ultimas = store.list_articles(db, window_hours=24 * 7, article_type=["news", "research"], limit=6)
    ratings = store.list_articles(db, window_hours=24 * 5, article_type="rating_action", limit=200)
    return {"noticias": [_artigo(a) for a in ultimas], "ratings": [_artigo(a) for a in ratings]}


def relatorios(db: Session, limite: int = 6) -> dict:
    linhas = db.scalars(
        select(Report).options(selectinload(Report.companies))
        .order_by(Report.published_at.desc().nullslast(), Report.ingested_at.desc()).limit(limite)
    ).all()
    return {"relatorios": [{
        "titulo": r.title, "url": SMART_URL + r.id,
        "data": r.published_at.isoformat() if r.published_at else None,
        "analista": r.analyst, "categoria": r.category,
        "empresas": [c.name for c in r.companies][:2],
    } for r in linhas]}
