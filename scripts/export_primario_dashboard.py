"""Exporta a aba Mercado Primário para o Research Dashboard Hub do Itaú.

Mesma ideia de `export_spreads_dashboard.py`: no hub o site é só tela, e as
contas de `app/primario/queries.py` são refeitas no navegador. A aba tem
janela x data (registro/encerramento) x incentivadas x instrumentos (múltipla
escolha), então vai a base de ofertas, não números prontos. As ofertas são
dado público da CVM.

  primario-ofertas.json   uma linha por oferta que não morreu (status fora de
      STATUS_MORTO), inclusive a fila e as secundárias (a tela tira as
      secundárias do volume, como o site). Já vai com o que depende do banco:
      quem deve (nome e chave, pela classificação do casamento), setor e
      grupo. Colunas em `CAMPOS`.
  primario-ofertas-extra.json  as colunas que só o CSV usa (CNPJ, nome do
      líder na CVM, regime, emissão, agente fiduciário...), na mesma ordem de
      linhas. Separado para a aba não baixar isso ao abrir.
  primario-emissoes.json  séries casadas das debêntures (taxa e spread na
      emissão, spread hoje), por oferta, de `emissoes.series_por_oferta`.
      Vão as DEB dos últimos 3 anos: a tabela mostra as 80 mais recentes da
      janela.

Uso:
    python -m scripts.export_primario_dashboard --saida saida_dashboard/
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import OfertaCVM  # noqa: E402
from app.primario import queries  # noqa: E402
from app.primario.coleta import STATUS_ABERTO, STATUS_MORTO  # noqa: E402
from scripts.export_spreads_dashboard import SCHEMA_VERSION, _escrever  # noqa: E402

logger = logging.getLogger(__name__)

GRUPOS = [c for c, _ in queries.GRUPOS]
CAMPOS = ["numero", "registro", "encerramento", "instrumento", "valor", "emissor", "lider", "status",
          "incentivado", "publico", "investidores", "devedor_curto", "tipo_oferta", *GRUPOS,
          "quem", "chave", "setor", "grupo", "devedor_cls", "aberta"]
EXTRA = ["cnpj_emissor", "nome_lider", "devedor", "regime_distribuicao", "bookbuilding", "sustentavel",
         "emissao", "agente_fiduciario"]
ANOS_EMISSOES = 3
# Colunas de texto que se repetem muito (emissor, líder, status, setor...)
# vão como índice numa lista única de textos: o arquivo cai ~40% (diretriz do
# hub: payload de no máximo poucos MB). O dashboard decodifica ao ler.
CODIFICADAS = ["instrumento", "emissor", "lider", "status", "incentivado", "publico", "devedor_curto",
               "tipo_oferta", "quem", "chave", "setor", "grupo", "devedor_cls"]


def _codificar(campos: list[str], linhas: list[list]) -> list[str]:
    textos: list[str] = []
    indice: dict[str, int] = {}
    cols = [campos.index(c) for c in CODIFICADAS]
    for linha in linhas:
        for i in cols:
            v = linha[i]
            if v is None:
                continue
            if v not in indice:
                indice[v] = len(textos)
                textos.append(v)
            linha[i] = indice[v]
    return textos


def _iso(d):
    return d.isoformat() if d else None


def exportar(saida: Path) -> dict:
    saida.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        cls = queries._classificacao(db)
        quem = queries._Quem(cls)
        hoje = queries._hoje(db)
        ofertas = db.scalars(select(OfertaCVM).where(
            (OfertaCVM.status.not_in(STATUS_MORTO)) | (OfertaCVM.status.in_(STATUS_ABERTO)))
            .order_by(OfertaCVM.numero_requerimento)).all()
        linhas, extras, debs = [], [], []
        corte = hoje - timedelta(days=365 * ANOS_EMISSOES)
        for o in ofertas:
            c = cls.get(o.numero_requerimento)
            linhas.append([
                o.numero_requerimento, _iso(o.data_registro), _iso(o.data_encerramento), o.instrumento,
                o.valor_total, o.nome_emissor, o.lider, o.status, o.incentivado, o.publico_alvo,
                o.n_investidores, o.devedor_curto, o.tipo_oferta, *[getattr(o, g) for g in GRUPOS],
                quem.nome(o), quem.chave(o),
                c.setor if c and c.setor else None,
                quem.grupo(o),
                c.devedor if c and c.devedor else None,
                1 if o.status in STATUS_ABERTO else 0,
            ])
            extras.append([getattr(o, k) for k in EXTRA])
            if o.instrumento == "DEB" and max(o.data_registro or corte.min, o.data_encerramento or corte.min) >= corte:
                debs.append(o.numero_requerimento)
        try:
            from app.primario.emissoes import series_por_oferta
            series = series_por_oferta(db, debs)
        except Exception:  # tabelas do casamento ainda não existem
            logger.exception("séries das debêntures indisponíveis")
            db.rollback()
            series = {}
        textos = _codificar(CAMPOS, linhas)
        ultima = db.scalar(select(func.max(OfertaCVM.atualizado_em)))
        gerado = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tamanhos = {}
        for nome, conteudo in (
            ("primario-ofertas.json", {"schemaVersion": SCHEMA_VERSION, "hoje": hoje.isoformat(),
                                       "ultimaColeta": ultima.isoformat() if ultima else None,
                                       "classificacaoDisponivel": bool(cls), "campos": CAMPOS,
                                       "codificadas": CODIFICADAS, "textos": textos, "linhas": linhas}),
            ("primario-ofertas-extra.json", {"schemaVersion": SCHEMA_VERSION, "campos": EXTRA, "linhas": extras}),
            ("primario-emissoes.json", {"schemaVersion": SCHEMA_VERSION, "ofertas": series}),
        ):
            conteudo["geradoEm"] = gerado
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
