"""Exporta a aba Mercado Secundário para o Research Dashboard Hub do Itaú BBA.

Mesma ideia de `export_spreads_dashboard.py` (ler a docstring de lá): no hub o
site é só tela, as contas de `app/spreads/mercado_secundario.py` são refeitas
no navegador a partir de dois arquivos.

  mercado-secundario-negocios-<ano>.json   a BASE DE VOLUME, linha a linha, com as
      colunas nomeadas como no export (Ticker, Data de Ref. do Negócio, Volume,
      Spread (bps)). O dashboard acha cada coluna pelo NOME do cabeçalho, como
      `mercado_secundario.mapear_cabecalho`. Motivo (24/11/2026): essa base vai
      passar a vir de dentro do banco "com a mesma estrutura"; quando vier, o
      arquivo do banco substitui este sem mexer no dashboard (coluna a mais é
      ignorada). UM ARQUIVO POR ANO (25/11/2026): um ano de negócios passa de
      8 MB e a diretriz do hub é payload de poucos MB. A aba abre com o ano
      corrente e o anterior (tudo que o resumo, o ranking e o gráfico de 1-6
      meses usam) e só busca os anos mais antigos no "Mensal"/"Anual" do
      volume e no "Tudo" do gráfico de performance. A lista de anos vai em
      `anosNegocios` no cadastro; sem ela, o dashboard lê um arquivo único
      `mercado-secundario-negocios.json` (ex.: se o banco publicar a base
      inteira num arquivo só).

  mercado-secundario-cadastro.json   O TICKER É A CHAVE: classe, emissor,
      setor, grupo e estoque de cada papel das duas classes, resolvidos pela
      mesma ordem de `mercado_secundario.mapa_tickers` (debentures, depois
      debenture_caracteristicas). Vai o universo INTEIRO das duas classes, não
      só o que negociou, para um ticker novo da base do banco já ter cadastro.
      Papel fora das duas classes não vai: a aba só mostra as duas.
      Estoque = o mais recente até a última data da base de volume
      (`mercado_secundario._estoques`), que é o "fim da janela" do giro.

Uso:
    python -m scripts.export_mercado_secundario_dashboard --saida saida_dashboard/
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Debenture, DebentureCaracteristica, MercadoSecundario  # noqa: E402
from app.spreads import mercado_secundario as ms  # noqa: E402
from app.spreads.queries import _classe_por_caracteristica  # noqa: E402
from scripts.export_spreads_dashboard import SCHEMA_VERSION, _escrever, _r  # noqa: E402

logger = logging.getLogger(__name__)

COLUNAS = ["Ticker", "Data de Ref. do Negócio", "Volume", "Spread (bps)"]
CLASSE_IDX = {"IPCA + Incentivadas": 0, "CDI + Tradicionais": 1}


def montar_negocios(db) -> dict[int, dict]:
    """{ano: conteúdo do arquivo daquele ano}."""
    t = MercadoSecundario
    por_ano: dict[int, list] = {}
    for codigo, d, volume, spread in db.execute(
            select(t.codigo, t.data, t.volume, t.spread).order_by(t.data, t.codigo)):
        por_ano.setdefault(d.year, []).append([codigo, d.isoformat(), _r(volume, 2), _r(spread, 4)])
    return {ano: {"schemaVersion": SCHEMA_VERSION, "colunas": COLUNAS, "linhas": linhas}
            for ano, linhas in por_ano.items()}


def montar_cadastro(db) -> dict:
    """Mesma regra de `ms.mapa_tickers`, só que para o universo das classes
    (e, para quem já negociou, com o nome/setor do export como reserva)."""
    t = MercadoSecundario
    fonte = {r.codigo: r for r in db.execute(
        select(t.codigo, func.max(t.devedor_fonte).label("devedor"),
               func.max(t.setor_fonte).label("setor"))
        .group_by(t.codigo))}
    papeis: dict[str, dict] = {}
    for d in db.scalars(select(Debenture)):
        if d.classe in CLASSE_IDX:
            papeis[d.codigo] = {"c": d.classe, "e": d.nome, "s": d.setor, "g": d.grupo_economico}
    vistos = {d for (d,) in db.execute(select(Debenture.codigo))}
    for c in db.scalars(select(DebentureCaracteristica)):
        if c.codigo in vistos:
            continue
        classe = _classe_por_caracteristica(c)
        if classe in CLASSE_IDX:
            papeis[c.codigo] = {"c": classe, "e": c.empresa, "s": None, "g": None}
    for codigo, p in papeis.items():
        f = fonte.get(codigo)
        p["e"] = p["e"] or (f.devedor.title() if f is not None and f.devedor else codigo)
        if not p["s"] and f is not None and f.setor:
            p["s"] = f.setor.title()
        p["g"] = p["g"] or p["e"]

    ultima = db.scalar(select(func.max(t.data))) or date.today()
    estoques = ms._estoques(db, list(papeis), ultima)
    saida = {}
    for codigo in sorted(papeis):
        p, est = papeis[codigo], estoques.get(codigo)
        saida[codigo] = [CLASSE_IDX[p["c"]], p["e"], p["s"], p["g"],
                         _r(est[0], 5) if est else None,
                         est[1].isoformat() if est and est[1] else None]
    return {"schemaVersion": SCHEMA_VERSION, "estoqueAte": ultima.isoformat(),
            "campos": ["classe", "emissor", "setor", "grupo", "estoque", "dataEstoque"],
            "classes": list(CLASSE_IDX), "papeis": saida}


def exportar(saida: Path) -> dict:
    saida.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        gerado_em = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tamanhos = {}
        negocios = montar_negocios(db)
        for ano, conteudo in negocios.items():
            conteudo["geradoEm"] = gerado_em
            nome = f"mercado-secundario-negocios-{ano}.json"
            tamanhos[nome] = _escrever(saida, nome, conteudo)
        cadastro = montar_cadastro(db)
        cadastro["anosNegocios"] = sorted(negocios)
        cadastro["geradoEm"] = gerado_em
        tamanhos["mercado-secundario-cadastro.json"] = _escrever(saida, "mercado-secundario-cadastro.json", cadastro)
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
