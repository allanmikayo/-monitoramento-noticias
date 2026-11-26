"""Exporta Notícias, Início e Repositório para o Research Dashboard Hub do Itaú.

Mesma ideia de `export_spreads_dashboard.py` (ler a docstring de lá): no hub o
site é só tela e lê JSONs publicados no Mindassets. Estes três mudam ao longo
do dia (varredura de notícias, RAD, relatórios do Smart), então rodam no fim
de cada varredura (`scrape.yml`), não no job diário de spreads.

  noticias.json     artigos dos últimos 30 dias para a aba Notícias e o feed do
      Início, com título e resumo (nunca o corpo). Vai tudo que é "Minha cobertura" (coberto ou ação de
      rating) e os N mais recentes fora dela -- a imprensa "Todos" capta
      milhares por dia e o site mostra no máximo 500 por consulta, então
      basta o topo. Formato compacto: ver `CAMPOS_ARTIGO`. Leva o resumo
      (snippet) cortado em RESUMO_MAX caracteres: a tela mostra 2 linhas e a
      busca procura nele (o Raul confirmou em 25/11/2026 que pode ir).
  inicio.json       os blocos da página inicial, calculados pelas MESMAS
      funções de `app/inicio.py` (spreads, primário, secundário, curvas,
      temporada de resultados) e a captação líquida mensal. O feed de
      ratings e notícias do Início sai do noticias.json.
  captacao-diaria.json / captacao-mensal.json  a base de captação líquida
      dos fundos de crédito, linha a linha, com as COLUNAS DO EXPORT
      (data_anls/mes_ref, segmento, nom_clie_gesr, qtd_fundos, patr_liqo,
      cpit_liqa/cpit_liqa_mtd). Motivo: essa base vai passar a vir de dentro
      do banco "com a mesma estrutura"; o dashboard acha cada coluna pelo nome.
      Em `nom_clie_gesr` vai o HASH da gestora (o banco só guarda o hash; a
      aba é de sell side e não cita gestora). Diário: só os últimos 60 dias
      com dado (o maior recorte da tela); mensal: tudo.
  repositorio.json  o catálogo de relatórios do Smart, igual a
      `GET /api/cobertura/dados` (público no site), sem o campo de
      pendência de revisão (é curadoria do admin, e o hub é só leitura).

Uso:
    python -m scripts.export_hub_frequente --saida saida_dashboard/
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import load_only, selectinload  # noqa: E402

from app import config, inicio  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (Article, CaptacaoDiaria, CaptacaoMensal, Company, Report, Sector, Source,  # noqa: E402
                        report_company)
from app.sources import vortx  # noqa: E402
from app.cobertura_routes import _norm  # noqa: E402
from app.store import DATA_ARTIGO  # noqa: E402
from scripts.export_spreads_dashboard import SCHEMA_VERSION, _escrever  # noqa: E402

logger = logging.getLogger(__name__)

JANELA_DIAS = 30
FORA_DA_COBERTURA = 1500  # mais recentes fora da cobertura (ver docstring)
CAMPOS_ARTIGO = ["id", "tipo", "titulo", "url", "baixar", "fonte", "quando", "coberto", "empresas", "setores", "resumo"]
RESUMO_MAX = 500
SMART_URL = "https://www.itau.com.br/itaubba-pt/portal/credit/report/"

# Mesmos links de `app.app._links_documento` (cópia para não importar o app
# FastAPI aqui). Diferença: o hub não tem as rotas /api/documentos/... do
# site, então o documento da Vórtx abre na página da operação (o link
# assinado do arquivo vence em ~1 h) e o "Baixar" dos agentes fiduciários
# vai direto ao arquivo.
_RAD = "https://www.rad.cvm.gov.br/ENET/"
_PROTOCOLO_RE = re.compile(r"NumeroProtocoloEntrega=(\d+)", re.I)
_FORMULARIO_RE = re.compile(
    r"(frmGerenciaPaginaFRE\.aspx|frmExibirCodigoGovernancaExt\.aspx)\?([^#]*NumeroSequencialDocumento=\d+[^#]*)", re.I)


def links(a: Article) -> tuple[str, str | None]:
    if a.article_type == "fato_relevante":
        m = _PROTOCOLO_RE.search(a.url or "")
        if not m:
            f = _FORMULARIO_RE.search(a.url or "")
            if f:
                return f"{_RAD}{f.group(1)}?{f.group(2)}", None
            return config.CVM_SEARCH_URL, None
        p = m.group(1)
        return (f"{_RAD}frmExibirArquivoIPEExterno.aspx?NumeroProtocoloEntrega={p}",
                f"{_RAD}frmDownloadDocumento.aspx?Tela=ext&numProtocolo={p}&descTipo=IPE&CodigoInstituicao=1")
    if a.article_type == "assembleia":
        if vortx.eh_link_estavel(a.url):
            return a.url.split("#", 1)[0], None
        return a.url, a.url
    return a.url, None


def _iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def montar_noticias(db) -> dict:
    corte = datetime.now(timezone.utc) - timedelta(days=JANELA_DIAS)
    base = (select(Article)
            .options(load_only(Article.id, Article.url, Article.source_name, Article.article_type, Article.title,
                               Article.published_at, Article.found_at, Article.is_covered, Article.snippet),
                     selectinload(Article.companies), selectinload(Article.sector_tags))
            .where(DATA_ARTIGO >= corte))
    cobertos = db.scalars(base.where((Article.is_covered.is_(True)) | (Article.article_type == "rating_action"))).unique().all()
    fora = db.scalars(base.where(Article.is_covered.is_not(True), Article.article_type != "rating_action")
                      .order_by(DATA_ARTIGO.desc()).limit(FORA_DA_COBERTURA)).unique().all()
    artigos = []
    for a in sorted({x.id: x for x in [*cobertos, *fora]}.values(), key=lambda x: x.published_at or x.found_at, reverse=True):
        url, baixar = links(a)
        artigos.append([a.id, a.article_type, a.title, url, baixar, a.source_name, _iso(a.published_at or a.found_at),
                        1 if a.is_covered else 0, sorted(c.id for c in a.companies), sorted(s.id for s in a.sector_tags),
                        (a.snippet or "").strip()[:RESUMO_MAX] or None])
    empresas = [[c.id, c.name, c.sector_id] for c in db.scalars(select(Company).where(Company.active.is_(True)).order_by(Company.name))]
    setores = [[s.id, s.name] for s in db.scalars(select(Sector).order_by(Sector.name))]
    fontes = [s.name for s in db.scalars(select(Source).order_by(Source.name))]
    return {"schemaVersion": SCHEMA_VERSION, "janelaDias": JANELA_DIAS, "campos": CAMPOS_ARTIGO,
            "empresas": empresas, "setores": setores, "fontes": fontes, "artigos": artigos}


def _enxugar_curvas(c: dict) -> dict:
    """Só o que o Início desenha: vértices de 6 meses a 10 anos."""
    for curva in c.get("curvas", []):
        curva["vertices"] = [{k: v.get(k) for k in ("du", "anos", "pre", "ipca")}
                             for v in curva["vertices"] if v.get("anos") is not None and 0.5 <= v["anos"] <= 10]
    return c


def montar_inicio(db) -> dict:
    from app import captacao

    blocos = {}
    for nome, fn in (("spreads", inicio.spreads), ("primario", inicio.primario_bloco),
                     ("secundario", inicio.secundario), ("curvas", inicio.curvas), ("resultados", inicio.resultados)):
        try:
            blocos[nome] = fn(db)
        except Exception:  # um bloco com problema não derruba os outros (igual ao site)
            logger.exception("bloco %s do Início falhou", nome)
            db.rollback()
            blocos[nome] = None
    if blocos["curvas"]:
        blocos["curvas"] = _enxugar_curvas(blocos["curvas"])
    r = blocos["resultados"]
    if r:
        blocos["resultados"] = {"hoje": r.get("hoje"), "temporada": r.get("temporada"), "resumo": r.get("resumo"),
                                "empresas": [{"nome": e["nome"], "data": e.get("data"), "status": e["status"]}
                                             for e in r.get("empresas", [])]}
    try:
        m = captacao.mensal(db)
        n = len(m["meses"])
        ini = max(0, n - 13)
        blocos["captacao"] = {"meses": m["meses"][ini:], "referencia": m.get("referencia"),
                              "series": {seg: {"captacao": m["series"][seg]["captacao"][ini:]}
                                         for seg in ("Infra", "CP ex Infra") if seg in m["series"]}}
    except Exception:
        logger.exception("captação falhou")
        db.rollback()
        blocos["captacao"] = None
    return {"schemaVersion": SCHEMA_VERSION, **blocos}


def montar_repositorio(db) -> dict:
    principais: dict[str, set[int]] = {}
    for rid, cid in db.execute(select(report_company.c.report_id, report_company.c.company_id)
                               .where(report_company.c.principal.is_(True))):
        principais.setdefault(rid, set()).add(cid)
    relatorios = []
    for r in db.scalars(select(Report).options(selectinload(Report.companies), selectinload(Report.sector_tags))
                        .order_by(Report.published_at.desc().nullslast())).unique():
        pri = principais.get(r.id, set())
        relatorios.append({
            "id": r.id, "titulo": r.title, "data": r.published_at.isoformat() if r.published_at else None,
            "categoria": r.category, "tipo_investimento": r.investment_type, "analista": r.analyst,
            "url": SMART_URL + r.id, "mercado": bool(r.is_market),
            "empresas": sorted((c.name for c in r.companies), key=_norm),
            "empresa_principal": sorted((c.name for c in r.companies if c.id in pri), key=_norm),
            "setores_extra": sorted((s.name for s in r.sector_tags), key=_norm),
        })
    empresas = sorted(({"empresa": c.name, "setor": c.sector.name, "analista": c.analyst or "—"}
                       for c in db.scalars(select(Company).options(selectinload(Company.sector))
                                           .where(Company.active.is_(True)))), key=lambda e: _norm(e["empresa"]))
    ultima = db.scalar(select(Report.ingested_at).order_by(Report.ingested_at.desc()))
    return {"schemaVersion": SCHEMA_VERSION,
            "gerado_em": (ultima or datetime.now(timezone.utc)).date().isoformat(),
            "empresas": empresas, "relatorios": relatorios}


DIAS_CAPTACAO = 60


def montar_captacao(db) -> tuple[dict, dict]:
    datas = [d for (d,) in db.execute(select(CaptacaoDiaria.data).distinct()
                                      .order_by(CaptacaoDiaria.data.desc()).limit(DIAS_CAPTACAO))]
    diaria = {"schemaVersion": SCHEMA_VERSION,
              "colunas": ["data_anls", "segmento", "nom_clie_gesr", "qtd_fundos", "patr_liqo", "cpit_liqa"],
              "linhas": []}
    if datas:
        diaria["linhas"] = [[r.data.isoformat(), r.segmento, r.gestora, r.qtd_fundos, r.pl, r.captacao]
                            for r in db.scalars(select(CaptacaoDiaria).where(CaptacaoDiaria.data >= min(datas))
                                                .order_by(CaptacaoDiaria.data))]
    mensal = {"schemaVersion": SCHEMA_VERSION,
              "colunas": ["mes_ref", "segmento", "nom_clie_gesr", "qtd_fundos", "patr_liqo", "cpit_liqa_mtd"],
              "linhas": [[r.mes.isoformat(), r.segmento, r.gestora, r.qtd_fundos, r.pl, r.captacao]
                         for r in db.scalars(select(CaptacaoMensal).order_by(CaptacaoMensal.mes))]}
    return diaria, mensal


def exportar(saida: Path) -> dict:
    saida.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        gerado_em = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tamanhos = {}
        for nome, montar in (("noticias.json", montar_noticias), ("inicio.json", montar_inicio),
                             ("repositorio.json", montar_repositorio)):
            conteudo = montar(db)
            conteudo["geradoEm"] = gerado_em
            tamanhos[nome] = _escrever(saida, nome, conteudo)
        try:
            diaria, mensal = montar_captacao(db)
        except Exception:  # tabelas ainda não criadas (nenhum export importado)
            logger.exception("captação indisponível")
            db.rollback()
        else:
            for nome, conteudo in (("captacao-diaria.json", diaria), ("captacao-mensal.json", mensal)):
                conteudo["geradoEm"] = gerado_em
                tamanhos[nome] = _escrever(saida, nome, conteudo)
        return tamanhos
    finally:
        db.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--saida", required=True, type=Path, help="pasta onde gravar os JSONs")
    args = ap.parse_args()
    tamanhos = exportar(args.saida)
    logger.info("OK: %d arquivos, %.2f MB no total", len(tamanhos), sum(tamanhos.values()) / 1e6)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
