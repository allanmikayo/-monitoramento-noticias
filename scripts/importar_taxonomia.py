"""Carrega setor, subsetor e grupo econômico por TICKER no cadastro de debêntures.

Fonte: aba "Tickers" do `Taxonomia_Emissores.xlsx` -- a planilha que o Allan
mantém, com 1.581 tickers classificados. A base de debêntures tem ~1.365
códigos, então a cobertura esperada é praticamente total.

POR QUE ISTO EXISTE (09/09/2026). A Visão Geral da aba Spreads passou a ter
uma tabela de spread por setor com drill-down (setor -> subsetor -> ticker).
Sem a taxonomia no banco, essa agregação não existe: `debentures` só tinha
indexador e classe, e a ligação com o cadastro editorial de notícias
(`company_id`) cobre 96 empresas, não a base de mercado inteira.

SÓ ESCREVE NO QUE JÁ EXISTE. Ticker da planilha que não está em `debentures`
é ignorado (não cria papel do nada); debênture sem ticker na planilha fica
com setor nulo e aparece como "Sem classificação" na tela -- explícito, em
vez de sumir da soma.

IDEMPOTENTE. Rodar de novo só atualiza o que mudou de valor.

    python -m scripts.importar_taxonomia
    python -m scripts.importar_taxonomia --arquivo "..\\Taxonomia_Emissores.xlsx"
    python -m scripts.importar_taxonomia --simular    # relata, não grava
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

# Mesma razão do `scripts/init_db.py` (09/09/2026): o `SessionLocal` de
# `app.db` fala pelo pooler em modo TRANSAÇÃO (6543) com 8s de timeout, que é
# o certo para a Vercel e errado para carga rodando daqui. Foi o que fez este
# script não conectar no mesmo dia em que o init_db também não conectava.
from app.db import criar_sessao_manutencao  # noqa: E402

SessionLocal = criar_sessao_manutencao()
from app.models import Debenture  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("taxonomia")

RAIZ = Path(__file__).resolve().parent.parent
# A planilha vive UM nível acima de credit_monitor, junto dos outros
# arquivos de trabalho do Allan. Segundo caminho é o fallback para quem
# preferir mantê-la dentro do projeto.
CAMINHOS_PADRAO = [
    RAIZ.parent / "Taxonomia_Emissores.xlsx",
    RAIZ / "data" / "Taxonomia_Emissores.xlsx",
]
ABA = "Tickers"
COLUNAS = {"Ticker": "ticker", "Setor": "setor",
           "Subsetor": "subsetor", "Grupo Econômico": "grupo_economico"}


def _achar_planilha(informado: str | None) -> Path:
    if informado:
        caminho = Path(informado)
        if not caminho.exists():
            raise SystemExit(f"planilha não encontrada: {caminho}")
        return caminho
    for caminho in CAMINHOS_PADRAO:
        if caminho.exists():
            return caminho
    raise SystemExit(
        "Taxonomia_Emissores.xlsx não encontrada. Procurei em:\n  "
        + "\n  ".join(str(c) for c in CAMINHOS_PADRAO)
        + "\nUse --arquivo para apontar o caminho."
    )


def ler_taxonomia(caminho: Path) -> dict[str, dict]:
    df = pd.read_excel(caminho, sheet_name=ABA)
    faltando = [c for c in COLUNAS if c not in df.columns]
    if faltando:
        raise SystemExit(
            f"a aba '{ABA}' não tem as colunas {faltando}. "
            f"Colunas encontradas: {list(df.columns)}"
        )
    df = df[list(COLUNAS)].rename(columns=COLUNAS)
    for coluna in df.columns:
        df[coluna] = df[coluna].astype("string").str.strip()
    df = df[df["ticker"].notna() & (df["ticker"] != "")]

    por_ticker: dict[str, dict] = {}
    for linha in df.to_dict("records"):
        ticker = linha.pop("ticker").upper()
        # Última ocorrência ganha, caso a planilha repita um ticker.
        por_ticker[ticker] = {k: (v if v and v != "<NA>" else None) for k, v in linha.items()}
    return por_ticker


LOTE = 200
TENTATIVAS = 3


def _gravar_lote(db, pedaco: list[dict]) -> None:
    """Um lote de UPDATEs, com nova tentativa quando o banco cancela.

    Forma "bulk update by primary key" do SQLAlchemy: `update(Debenture)` com
    uma lista de dicionários que trazem a CHAVE PRIMÁRIA (`codigo`) junto dos
    campos. Vira um `executemany` só.

    A primeira versão usava `where(codigo == bindparam("b_codigo"))` com a
    chave rebatizada -- e o SQLAlchemy recusou:
        No primary key value supplied for column(s) debentures.codigo
    porque uma lista de dicionários já seleciona este caminho, e nele a chave
    tem que vir com o nome real da coluna.

    `synchronize_session=False`: não há objetos ORM carregados para manter em
    dia, este script lê por colunas.
    """
    from sqlalchemy import update

    comando = update(Debenture).execution_options(synchronize_session=False)
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            db.execute(comando, pedaco)
            db.commit()
            return
        except Exception:  # noqa: BLE001
            db.rollback()
            if tentativa == TENTATIVAS:
                raise
            logger.warning("lote cancelado, tentativa %d de %d", tentativa, TENTATIVAS)
            time.sleep(5 * tentativa)


def importar(db, por_ticker: dict[str, dict], simular: bool = False, lote: int = LOTE) -> dict:
    """Grava a taxonomia nas debêntures, em lotes com commit por lote.

    POR QUE EM LOTES (10/09/2026). A primeira versão fazia o caminho natural
    do ORM: carregar todas as debêntures como objetos, mexer nos atributos, e
    um `commit()` no fim. Isso vira UMA transação cobrindo ~1.500 linhas. Num
    banco folgado sai em meio segundo (medido: 0,43s). No banco do Allan,
    saturado, passou dos 5 minutos do `statement_timeout` e foi cancelada
    INTEIRA -- e "cancelada inteira" é o problema, não a lentidão: nenhuma
    linha ficou gravada, e rodar de novo recomeçava do zero.

    Não é questão de idas e voltas. Medi: o ORM manda um `executemany` só,
    não 1.500 comandos. O que não cabe é o TAMANHO da transação.

    Com commit a cada 200, cada transação é curta o bastante para terminar, e
    o que já passou fica gravado. Uma execução interrompida no meio deixa
    trabalho feito, e a próxima só cuida do que falta -- a comparação abaixo
    pula quem já está com o valor certo.

    A leitura também mudou: `select` das quatro colunas que interessam, em
    vez de `query(Debenture).all()`, que trazia as catorze de cada linha só
    para comparar três.
    """
    from sqlalchemy import select

    atuais = db.execute(
        select(
            Debenture.codigo, Debenture.setor,
            Debenture.subsetor, Debenture.grupo_economico,
        )
    ).all()

    mudancas: list[dict] = []
    inalteradas = sem_classificacao = 0
    for codigo, setor, subsetor, grupo in atuais:
        dados = por_ticker.get((codigo or "").upper())
        if dados is None:
            sem_classificacao += 1
            continue
        if (setor, subsetor, grupo) == (
            dados["setor"], dados["subsetor"], dados["grupo_economico"]
        ):
            inalteradas += 1
            continue
        mudancas.append({"codigo": codigo, **dados})

    if not simular and mudancas:
        total = len(mudancas)
        for i in range(0, total, lote):
            try:
                _gravar_lote(db, mudancas[i:i + lote])
            except Exception:
                gravadas = i
                logger.error(
                    "parou no lote %d -- %d de %d debêntures já foram gravadas e"
                    " ficam gravadas. Rode de novo: ele continua de onde parou.",
                    i // lote + 1, gravadas, total,
                )
                raise
            feitas = min(i + lote, total)
            logger.info("gravadas %d/%d", feitas, total)

    return {
        "debentures": len(atuais),
        "tickers_na_planilha": len(por_ticker),
        "atualizadas": len(mudancas),
        "inalteradas": inalteradas,
        "sem_classificacao": sem_classificacao,
    }


COLUNAS_TAXONOMIA = ("setor", "subsetor", "grupo_economico")


def _colunas_existem() -> bool:
    """As três colunas da taxonomia existem no banco?

    POR QUE (09/09/2026). Sem esta conferência, rodar este script antes da
    migração devolve noventa linhas de traceback terminando em
    `column debentures.grupo_economico does not exist` -- que é a verdade,
    mas não diz o que fazer. O SELECT que estoura é montado pelo ORM a partir
    do `models.py`, então ele pede a coluna mesmo que este script não fosse
    usá-la ainda.
    """
    from app.db import _catalogo_colunas, criar_engine_manutencao

    try:
        catalogo = _catalogo_colunas(criar_engine_manutencao(), ["debentures"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("nao consegui conferir o esquema (%s) -- seguindo assim mesmo",
                       type(exc).__name__)
        return True
    if catalogo is None:  # SQLite
        return True

    faltando = [c for c in COLUNAS_TAXONOMIA if c not in catalogo.get("debentures", {})]
    if not faltando:
        return True

    logger.error("a tabela `debentures` ainda nao tem: %s", ", ".join(faltando))
    logger.error("A taxonomia nao tem onde ser gravada. Crie as colunas primeiro,")
    logger.error("por um destes dois caminhos:")
    logger.error("  1) python -m scripts.init_db")
    logger.error("  2) se o banco estiver engasgado: cole scripts/migracao_manual.sql")
    logger.error("     no SQL Editor da Supabase (roda dentro do servidor)")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arquivo", help="caminho do Taxonomia_Emissores.xlsx")
    ap.add_argument("--simular", action="store_true", help="relata sem gravar")
    args = ap.parse_args()

    caminho = _achar_planilha(args.arquivo)
    logger.info("planilha: %s", caminho)
    por_ticker = ler_taxonomia(caminho)
    logger.info("%d ticker(s) classificado(s) na planilha", len(por_ticker))

    if not _colunas_existem():
        return 1

    with SessionLocal() as db:
        r = importar(db, por_ticker, simular=args.simular)

    logger.info(
        "%d debênture(s) na base: %d atualizada(s), %d já estavam certas, "
        "%d sem classificação na planilha",
        r["debentures"], r["atualizadas"], r["inalteradas"], r["sem_classificacao"],
    )
    if r["sem_classificacao"]:
        cobertura = 100 * (r["debentures"] - r["sem_classificacao"]) / max(r["debentures"], 1)
        logger.info("cobertura: %.1f%% das debêntures têm setor", cobertura)
        logger.info("as sem classificação aparecem como 'Sem classificação' na tela, "
                    "não somem das somas")
    if args.simular:
        logger.info("(simulação -- nada foi gravado)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
