"""Exporta a aba Spreads para o Research Dashboard Hub do Itaú BBA (repositório
`rTaiShan/research-dashboards`, publicação via Dashboard Data Feeder).

POR QUE EXISTE (24/11/2026, migração para o sistema do banco). No hub do banco
o site é só tela: React no navegador, sem servidor e sem banco. Ele lê arquivos
JSON publicados no Mindassets. Toda conta que hoje é uma rota em
`app/spreads_routes.py` passa a ser feita no navegador a partir do dado que este
script publica.

POR QUE DADO GRANULAR E NÃO NÚMEROS PRONTOS. A Visão Geral combina classe x
base (d-1 ... YoY, Personalizado) x data analisada x filtros de setor, subsetor
e grupo, todos de múltipla escolha. Pré-calcular cada combinação é impossível.
Publicar a matriz papel x dia (compacta) deixa o navegador refazer as mesmas
contas de `queries.py` para qualquer combinação, e o resultado bate com o site
atual.

ARQUIVOS (os nomes são o contrato com o dashboard; não renomear sem antes
publicar código que leia os dois nomes):

  spreads-cadastro.json          um registro por papel: emissor, setor,
                                 subsetor, grupo, indexador, classe
  spreads-<c>-recente.json       matriz dos últimos JANELA pregões da classe
  spreads-<c>-anterior.json      os JANELA pregões anteriores (só é baixado
                                 para SoS, YoY ou data antiga)
  spreads-<c>-detalhe-<p>.json   taxa indicativa e PU de cada papel precificado,
                                 nos mesmos pregões da matriz (<p> = recente |
                                 anterior). Só o botão Detalhes usa; por isso
                                 fica fora da matriz, que abre com a aba
  spreads-serie.json             evolução do spread médio no histórico INTEIRO,
                                 sem recorte (o gráfico de evolução abre nela)
  spreads-emissores.json         aba Emissor: a tabela "Abertura da Dívida" de
                                 cada emissor (a própria `queries.emissor_tickers`
                                 do site, com características, vencimento, taxa
                                 da emissão e última taxa Anbima), os grupos
                                 econômicos e o emissor que a aba abre

  <c> = "ipca" (IPCA + Incentivadas) ou "cdi" (CDI + Tradicionais).

MATRIZ. `datas` é a lista de pregões do arquivo, do mais antigo ao mais
recente. Cada papel é `[inicio, spread[], duration[], estoque[], pct_pu_par[]]`:
os quatro vetores começam na posição `inicio` de `datas` e têm o mesmo tamanho.

  - spread, duration e estoque só vêm preenchidos quando o papel está
    PRECIFICADO na data (mesma regra de `queries.PRECIFICADO`); fora disso,
    null. Assim nenhuma conta do navegador usa uma base diferente do card.
  - pct_pu_par vem sempre que a ANBIMA publicar (o modo "Todos" dos deságios
    mostra papel sem spread).
  - Papéis da lista de exclusão manual (Administração) ficam de fora de todos
    os arquivos.

Arredondamento: ver CASAS. É mais do que a tela mostra, para as contas do
navegador baterem com as do `queries.py`.

PÚBLICO. Os arquivos do Mindassets podem ser baixados por qualquer cliente
(docs/dashboard-data-feeder.md do repositório do hub). Aqui só vai dado de
mercado da ANBIMA/Debentures.com e a taxonomia; nada de usuário, senha ou URL
interna.

Uso:
    python -m scripts.export_spreads_dashboard --saida saida_dashboard/
    python -m scripts.export_spreads_dashboard --saida X --janela 130
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.models import Debenture, DebentureSpread  # noqa: E402
from app.spreads import queries  # noqa: E402

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
JANELA_PADRAO = 130  # ~6 meses de pregões por arquivo
# Casas decimais publicadas. Mais que as da tela de propósito: com 1 casa no
# spread, um papel que variou -0,04 bps caía na faixa "0 até 10" em vez de
# "-10 até 0" e a composição saía até 1 p.p. diferente do site; com 3 casas, o
# CSV do Detalhes ainda trocava a última casa em ~5% das linhas (5,315 -> 5,32
# onde o Python dá 5,31). Com 5 casas sobra só empate de ponto flutuante do
# próprio Python (CDI+ 5,4495 x 100 = 544,94999 -> 544,9): ±0,1 em poucas
# linhas. Conferido contra `queries.py` em 24/11/2026.
CASAS = {"spread": 5, "duration": 5, "estoque": 5, "pct_pu_par": 4, "taxa": 4, "pu": 6}
SLUG_CLASSE = {"IPCA + Incentivadas": "ipca", "CDI + Tradicionais": "cdi"}


def _r(v: float | None, casas: int) -> float | None:
    return None if v is None else round(float(v), casas)


def _escrever(pasta: Path, nome: str, conteudo: dict) -> int:
    caminho = pasta / nome
    texto = json.dumps(conteudo, ensure_ascii=False, separators=(",", ":"))
    caminho.write_text(texto, encoding="utf-8")
    logger.info("%s: %.2f MB", nome, len(texto.encode("utf-8")) / 1e6)
    return len(texto)


def montar_cadastro(db, excluidos: set[str]) -> dict:
    """Um registro por papel das duas classes. Chaves curtas porque o arquivo
    é baixado em toda abertura da aba."""
    papeis = {}
    q = db.query(Debenture).filter(Debenture.classe.in_(queries.CLASSES))
    for d in q.all():
        if d.codigo in excluidos:
            continue
        papeis[d.codigo] = {
            "e": d.nome,
            "c": SLUG_CLASSE[d.classe],
            "i": d.indexador,
            "n": d.incentivada,
            "s": d.setor,
            "ss": d.subsetor,
            "g": d.grupo_economico,
        }
    return {"schemaVersion": SCHEMA_VERSION, "papeis": papeis}


def montar_matriz(db, classe: str, datas: list[date], excluidos: set[str]) -> dict:
    """Matriz papel x dia para `datas` (ordem crescente)."""
    if not datas:
        return {"schemaVersion": SCHEMA_VERSION, "classe": classe, "datas": [], "papeis": {}}
    pos = {d: i for i, d in enumerate(datas)}
    n = len(datas)
    linhas = (
        db.query(DebentureSpread.codigo, DebentureSpread.data, DebentureSpread.spread,
                 DebentureSpread.pu, DebentureSpread.pct_pu_par, DebentureSpread.duration,
                 DebentureSpread.estoque)
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .filter(Debenture.classe == classe,
                DebentureSpread.data >= datas[0], DebentureSpread.data <= datas[-1])
        .all()
    )
    bruto: dict[str, list[list]] = {}
    for codigo, dia, spread, pu, pct, duration, estoque in linhas:
        if codigo in excluidos or dia not in pos:
            continue
        v = bruto.setdefault(codigo, [[None] * n for _ in range(4)])
        i = pos[dia]
        precificado = (spread is not None and pu is not None
                       and pct is not None and duration is not None)
        if precificado:
            v[0][i] = _r(spread, CASAS["spread"])
            v[1][i] = _r(duration, CASAS["duration"])
            v[2][i] = _r(estoque, CASAS["estoque"])
        v[3][i] = _r(pct, CASAS["pct_pu_par"])

    papeis = {}
    for codigo, vetores in bruto.items():
        usados = [i for i in range(n) if any(vet[i] is not None for vet in vetores)]
        if not usados:
            continue
        ini, fim = usados[0], usados[-1] + 1
        papeis[codigo] = [ini] + [vet[ini:fim] for vet in vetores]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "classe": classe,
        "datas": [d.isoformat() for d in datas],
        "papeis": papeis,
    }


def montar_detalhe(db, classe: str, datas: list[date], excluidos: set[str]) -> dict:
    """Taxa indicativa e PU dos papéis PRECIFICADOS (mesma regra da matriz e de
    `queries.detalhes_rows`), no formato `[inicio, taxa[], pu[]]`."""
    if not datas:
        return {"schemaVersion": SCHEMA_VERSION, "classe": classe, "datas": [], "papeis": {}}
    pos = {d: i for i, d in enumerate(datas)}
    n = len(datas)
    bruto: dict[str, list[list]] = {}
    linhas = (
        db.query(DebentureSpread.codigo, DebentureSpread.data, DebentureSpread.taxa_indicativa,
                 DebentureSpread.pu)
        .join(Debenture, Debenture.codigo == DebentureSpread.codigo)
        .filter(Debenture.classe == classe, queries.PRECIFICADO,
                DebentureSpread.data >= datas[0], DebentureSpread.data <= datas[-1])
        .all()
    )
    for codigo, dia, taxa, pu in linhas:
        if codigo in excluidos or dia not in pos:
            continue
        v = bruto.setdefault(codigo, [[None] * n, [None] * n])
        v[0][pos[dia]] = _r(taxa, CASAS["taxa"])
        v[1][pos[dia]] = _r(pu, CASAS["pu"])
    papeis = {}
    for codigo, (taxas, pus) in bruto.items():
        usados = [i for i in range(n) if taxas[i] is not None or pus[i] is not None]
        ini, fim = usados[0], usados[-1] + 1
        papeis[codigo] = [ini, taxas[ini:fim], pus[ini:fim]]
    return {"schemaVersion": SCHEMA_VERSION, "classe": classe,
            "datas": [d.isoformat() for d in datas], "papeis": papeis}


def montar_serie(db) -> dict:
    """Evolução sem recorte no histórico inteiro -- a mesma função da rota
    `/api/spreads/series` (média ponderada, mediana e base comparável)."""
    classes = {}
    for classe, slug in SLUG_CLASSE.items():
        pontos = queries.time_series(db, classe)
        classes[slug] = [
            [p["data"], p["spread_medio"], p["spread_mediana"], p["n_ativos"],
             p.get("spread_comparavel")]
            for p in pontos
        ]
    return {"schemaVersion": SCHEMA_VERSION,
            "colunas": ["data", "media", "mediana", "n", "comparavel"],
            "classes": classes}


# Colunas de cada linha de `spreads-emissores.json` (mesmos nomes de campo de
# `queries.emissor_tickers`, em lista para o arquivo ficar menor).
COLUNAS_TICKER = [
    "codigo", "emissor", "classe", "indexador", "incentivada", "estoque",
    "data_emissao", "data_vencimento", "taxa_emissao", "duration", "data_estoque",
    "spread", "taxa_anbima", "data_anbima", "setor", "subsetor", "grupo_economico", "cnpj",
]


def montar_emissores(db) -> dict:
    """Tudo o que a aba Emissor precisa e não está na matriz: a tabela de cada
    emissor (`emissor_tickers`, exatamente a função da rota /api/spreads/emissor),
    os grupos (`grupos_economicos`) e o emissor padrão (`emissor_padrao`).
    Perfil de GRUPO no navegador = união das tabelas dos emissores do grupo."""
    emissores, fora = {}, {}
    for nome in queries.list_emissores(db):
        t = queries.emissor_tickers(db, [nome])
        if t["tickers"]:
            emissores[nome] = [[linha.get(c) for c in COLUNAS_TICKER] for linha in t["tickers"]]
        if t.get("fora_de_circulacao"):
            fora[nome] = [[f["codigo"], f["data_estoque"]] for f in t["fora_de_circulacao"]]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "padrao": queries.emissor_padrao(db),
        "colunas": COLUNAS_TICKER,
        "emissores": emissores,
        "foraDeCirculacao": fora,
        "grupos": [[g["grupo"], g["emissores"]] for g in queries.grupos_economicos(db)],
    }


def exportar(saida: Path, janela: int = JANELA_PADRAO) -> dict:
    saida.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        excluidos = queries.tickers_excluidos_spread(db)
        gerado_em = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tamanhos = {}

        cadastro = montar_cadastro(db, excluidos)
        cadastro["geradoEm"] = gerado_em
        tamanhos["spreads-cadastro.json"] = _escrever(saida, "spreads-cadastro.json", cadastro)

        for classe, slug in SLUG_CLASSE.items():
            datas_desc = queries.distinct_dates(db, classe)
            recentes = sorted(datas_desc[:janela])
            anteriores = sorted(datas_desc[janela:2 * janela])
            for periodo, datas in (("recente", recentes), ("anterior", anteriores)):
                nome = f"spreads-{slug}-{periodo}.json"
                m = montar_matriz(db, classe, datas, excluidos)
                m["geradoEm"] = gerado_em
                # Existe mais histórico antes deste arquivo? O navegador usa
                # isso para saber se ainda vale buscar o "anterior".
                m["temAnterior"] = periodo == "recente" and bool(anteriores)
                tamanhos[nome] = _escrever(saida, nome, m)
                nome_det = f"spreads-{slug}-detalhe-{periodo}.json"
                det = montar_detalhe(db, classe, datas, excluidos)
                det["geradoEm"] = gerado_em
                tamanhos[nome_det] = _escrever(saida, nome_det, det)

        emissores = montar_emissores(db)
        emissores["geradoEm"] = gerado_em
        tamanhos["spreads-emissores.json"] = _escrever(saida, "spreads-emissores.json", emissores)

        serie = montar_serie(db)
        serie["geradoEm"] = gerado_em
        tamanhos["spreads-serie.json"] = _escrever(saida, "spreads-serie.json", serie)
        return tamanhos
    finally:
        db.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--saida", required=True, type=Path, help="pasta onde gravar os JSONs")
    ap.add_argument("--janela", type=int, default=JANELA_PADRAO,
                    help=f"pregões por arquivo de matriz (padrão {JANELA_PADRAO})")
    args = ap.parse_args()
    tamanhos = exportar(args.saida, args.janela)
    total = sum(tamanhos.values()) / 1e6
    logger.info("OK: %d arquivos, %.2f MB no total", len(tamanhos), total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
