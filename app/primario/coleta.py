"""Coleta das ofertas públicas de dívida no Portal de Dados Abertos da CVM
(23/09/2026).

O QUE A FONTE É
---------------
Um zip único (`oferta_distribuicao.zip`, ~5 MB) reescrito TODA madrugada,
com dois CSVs dentro:

    oferta_resolucao_160.csv   jan/2023 -> hoje, rito automático  <- usamos este
    oferta_distribuicao.csv    regime antigo (ICVM 400/476), acaba em 2022

Os dois são latin-1, separados por ";", datas em ISO e decimal com ponto.
Ficamos só com o segundo regime (decisão do Allan, 23/09/2026): a série de
2023 em diante tem os mesmos campos em todas as linhas, e o arquivo antigo
usa outros nomes de coluna e mistura ofertas dispensadas de registro.

POR QUE NÃO DÁ PARA SÓ "APPENDAR"
---------------------------------
O arquivo é uma FOTO do estado atual, não um extrato do dia: a linha da
oferta muda de status no lugar (Registro Concedido -> Oferta Encerrada) e o
estado anterior some da origem. Então cada rodada compara o que chegou com
o que está no banco, grava a NOVIDADE em `ofertas_cvm_status` e só depois
sobrescreve a linha. É isso que sustenta o bloco "novidades da semana" da
aba sem guardar uma cópia do arquivo por dia.

TRAVA DE SEGURANÇA
------------------
Se o arquivo do dia vier menor que `PISO_RELATIVO` do que já temos (CVM
publicando um arquivo truncado, um zip incompleto), a rodada aborta sem
escrever. Um número que encolhe sozinho seria a pior falha possível aqui:
silenciosa e igualzinha a "o mercado parou".
"""
from __future__ import annotations

import csv
import io
import logging
import os
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import OfertaCVM, OfertaCVMStatus

logger = logging.getLogger(__name__)

URL_ZIP = "https://dados.cvm.gov.br/dados/OFERTA/DISTRIB/DADOS/oferta_distribuicao.zip"
ARQUIVO_INTERNO = "oferta_resolucao_160.csv"
TIMEOUT = 120

# Só instrumentos de dívida corporativa (pedido do Allan). "Certificados de
# Recebíveis" genérico e "Outros títulos de securitização" ficam de fora: são
# poucas dezenas de linhas e não dá para dizer com segurança o que são.
INSTRUMENTOS = {
    "Debêntures": "DEB",
    "Certificados de Recebíveis Imobiliários": "CRI",
    "Certificados de Recebíveis do Agronegócio": "CRA",
}
ROTULO_INSTRUMENTO = {"DEB": "Debêntures", "CRI": "CRI", "CRA": "CRA"}

# Status que a CVM usa. Agrupados pelo que significam para quem olha o
# mercado -- a tela nunca mostra "Requerimento Expirado" ao lado de uma
# oferta viva.
STATUS_ABERTO = ("Registro Concedido", "Aguardando Bookbuilding", "Oferta Suspensa")
STATUS_ENCERRADO = ("Oferta Encerrada",)
STATUS_MORTO = ("Registro Caducado", "Oferta Revogada", "Requerimento Expirado")

PISO_RELATIVO = 0.8
BACKUPS_MANTIDOS = 5

# Nome do líder: a CVM grava a razão social, então o mesmo banco aparece como
# "ITAU BBA ASSESSORIA FINANCEIRA S.A", "BANCO ITAU BBA S.A." e por aí vai.
# Sem isto a league table divide um banco em três. A busca é por PEDAÇO do
# nome em maiúsculas, na ordem: o primeiro que casar vence.
APELIDOS = [
    ("ITAU BBA", "Itaú BBA"), ("ITAÚ BBA", "Itaú BBA"), ("ITAU UNIBANCO", "Itaú BBA"),
    ("BRADESCO BBI", "Bradesco BBI"), ("BRADESCO", "Bradesco BBI"),
    ("XP INVESTIMENTOS", "XP"), ("XP CORRETORA", "XP"),
    ("BTG PACTUAL", "BTG Pactual"),
    ("SANTANDER", "Santander"),
    ("UBS BB", "UBS BB"),
    ("BANCO NACIONAL DE DESENVOLVIMENTO", "BNDES"), ("BNDES", "BNDES"),
    ("CAIXA ECONOMICA", "Caixa"), ("CAIXA ECONÔMICA", "Caixa"),
    ("SAFRA", "Safra"),
    ("VOTORANTIM", "BV"), ("BANCO BV", "BV"),
    ("CITIBANK", "Citi"), ("CITIGROUP", "Citi"),
    ("J.P. MORGAN", "J.P. Morgan"), ("JP MORGAN", "J.P. Morgan"), ("JPMORGAN", "J.P. Morgan"),
    ("MERRILL LYNCH", "BofA"), ("BANK OF AMERICA", "BofA"),
    ("MORGAN STANLEY", "Morgan Stanley"),
    ("GOLDMAN SACHS", "Goldman Sachs"),
    ("ABC BRASIL", "ABC Brasil"),
    ("DAYCOVAL", "Daycoval"),
    ("BANCO DO BRASIL", "Banco do Brasil"), ("BB BANCO DE INVESTIMENTO", "Banco do Brasil"),
    ("GENIAL", "Genial"),
    ("OPEA", "Opea"),
    ("VERT", "Vert"),
    ("VIRGO", "Virgo"),
    ("TRUE SECURITIZADORA", "True"),
    ("GAIA", "Gaia"),
    ("HABITASEC", "Habitasec"),
    ("CANAL", "Canal"),
    ("BANCO INTER", "Inter"),
    ("NU INVEST", "Nubank"), ("NU FINANCEIRA", "Nubank"),
    ("WARREN", "Warren"),
    ("ORAMA", "Órama"), ("ÓRAMA", "Órama"),
    ("MODAL", "Modal"),
    ("TERRA INVESTIMENTOS", "Terra"),
    ("RB CAPITAL", "RB Capital"),
    ("ISEC", "Isec"),
]

SUFIXOS = (
    " S.A.", " S.A", " S/A", " SA", " LTDA.", " LTDA", " S.A.S", " EIRELI",
    " DISTRIBUIDORA DE TITULOS E VALORES MOBILIARIOS", " CCTVM", " DTVM", " CVM",
)


def limpar_lider(nome: str | None) -> str | None:
    """Razão social do líder -> nome curto para a league table."""
    if not nome:
        return None
    bruto = " ".join(nome.split()).upper()
    for pedaco, apelido in APELIDOS:
        if pedaco in bruto:
            return apelido
    limpo = bruto
    for s in SUFIXOS:
        if limpo.endswith(s):
            limpo = limpo[: -len(s)]
    limpo = limpo.strip(" .,-")
    # Sem apelido conhecido: Title Case, que é melhor do que CAIXA ALTA numa
    # tabela, e o nome cru continua guardado em `nome_lider`.
    return (limpo.title()[:80] or None)


def _data(valor: str | None) -> date | None:
    valor = (valor or "").strip()
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        return None


def _float(valor: str | None) -> float | None:
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        return float(valor)
    except ValueError:
        return None


def _int(valor: str | None) -> int | None:
    f = _float(valor)
    return int(f) if f is not None else None


def _txt(valor: str | None, tamanho: int) -> str | None:
    valor = " ".join((valor or "").split())
    return valor[:tamanho] or None


@dataclass
class Oferta:
    """Uma linha do CSV já traduzida para os campos que guardamos."""
    dados: dict

    @property
    def chave(self) -> str:
        return self.dados["numero_requerimento"]


def baixar_zip(url: str = URL_ZIP) -> bytes:
    resposta = requests.get(url, timeout=TIMEOUT)
    resposta.raise_for_status()
    return resposta.content


def extrair_csv(conteudo_zip: bytes, nome: str = ARQUIVO_INTERNO) -> str:
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as z:
        nomes = z.namelist()
        if nome not in nomes:
            raise ValueError(f"{nome} não veio no zip (veio: {nomes})")
        return z.read(nome).decode("latin-1")


def parse(texto_csv: str) -> list[Oferta]:
    """CSV cru -> ofertas de dívida, já com o nome do líder limpo."""
    leitor = csv.DictReader(io.StringIO(texto_csv), delimiter=";")
    faltando = {"Numero_Requerimento", "Valor_Mobiliario", "Status_Requerimento"} - set(
        leitor.fieldnames or []
    )
    if faltando:
        raise ValueError(f"colunas ausentes no CSV da CVM: {sorted(faltando)}")

    ofertas: list[Oferta] = []
    for linha in leitor:
        instrumento = INSTRUMENTOS.get((linha.get("Valor_Mobiliario") or "").strip())
        if not instrumento:
            continue
        chave = (linha.get("Numero_Requerimento") or "").strip()
        if not chave:
            continue
        ofertas.append(Oferta({
            "numero_requerimento": chave[:40],
            "numero_processo": _txt(linha.get("Numero_Processo"), 50),
            "data_requerimento": _data(linha.get("Data_requerimento")),
            "data_registro": _data(linha.get("Data_Registro")),
            "data_encerramento": _data(linha.get("Data_Encerramento")),
            "status": _txt(linha.get("Status_Requerimento"), 40) or "",
            "instrumento": instrumento,
            "valor_mobiliario": _txt(linha.get("Valor_Mobiliario"), 80),
            "cnpj_emissor": _txt(linha.get("CNPJ_Emissor"), 20),
            "nome_emissor": _txt(linha.get("Nome_Emissor"), 250) or "",
            "cnpj_lider": _txt(linha.get("CNPJ_Lider"), 20),
            "nome_lider": _txt(linha.get("Nome_Lider"), 250),
            "lider": limpar_lider(linha.get("Nome_Lider")),
            "valor_total": _float(linha.get("Valor_Total_Registrado")),
            "quantidade": _float(linha.get("Qtde_Total_Registrada")),
            "emissao": _txt(linha.get("Emissao"), 20),
            "tipo_oferta": _txt(linha.get("Tipo_Oferta"), 30),
            "tipo_requerimento": _txt(linha.get("Tipo_requerimento"), 150),
            "publico_alvo": _txt(linha.get("Publico_alvo"), 30),
            "regime_distribuicao": _txt(linha.get("Regime_distribuicao"), 40),
            "bookbuilding": _txt(linha.get("Bookbuilding"), 5),
            "oferta_inicial": _txt(linha.get("Oferta_inicial"), 5),
            "reabertura": _txt(linha.get("Reabertura_serie"), 5),
            "incentivado": _txt(linha.get("Titulo_incentivado"), 5),
            "sustentavel": _txt(linha.get("Titulo_classificado_como_sustentavel"), 5),
            "destinacao": _txt(linha.get("Destinacao_recursos"), 4000),
            "tipo_lastro": _txt(linha.get("Tipo_lastro"), 120),
            "agente_fiduciario": _txt(linha.get("Agente_fiduciario"), 250),
            "invest_pf_n": _int(linha.get("Num_Invest_Pessoa_Natural")),
            "invest_pf_qtd": _float(linha.get("Qtde_VM_Pessoa_Natural")),
            "invest_fundos_n": _int(linha.get("Num_Invest_Fundos_Investimento")),
            "invest_fundos_qtd": _float(linha.get("Qtde_VM_Fundos_Investimento")),
            "invest_prev_n": _int(linha.get("Num_Invest_Entidade_Previdencia_Privada")),
            "invest_prev_qtd": _float(linha.get("Qtde_VM_Entidade_Previdencia_Privada")),
        }))
    return ofertas


def salvar_backup(conteudo_zip: bytes, pasta: str | os.PathLike) -> Path:
    """Guarda o zip do dia e mantém só os `BACKUPS_MANTIDOS` mais recentes.

    Pedido do Allan: se a CVM publicar um arquivo torto, dá para voltar ao
    de ontem sem esperar a próxima publicação. Guardar todos os dias seria
    ~1,8 GB por ano para nenhum ganho -- o histórico útil já está no banco.
    """
    destino = Path(pasta)
    destino.mkdir(parents=True, exist_ok=True)
    arquivo = destino / f"oferta_distribuicao_{date.today().isoformat()}.zip"
    arquivo.write_bytes(conteudo_zip)
    antigos = sorted(destino.glob("oferta_distribuicao_*.zip"))[:-BACKUPS_MANTIDOS]
    for velho in antigos:
        try:
            velho.unlink()
        except OSError:  # pragma: no cover - permissão/arquivo em uso
            logger.warning("não consegui apagar o backup antigo %s", velho)
    return arquivo


def gravar(db: Session, ofertas: list[Oferta], *, piso_relativo: float = PISO_RELATIVO) -> dict:
    """Aplica a foto do dia: grava a novidade e sobrescreve as linhas."""
    existentes = {o.numero_requerimento: o for o in db.scalars(select(OfertaCVM)).all()}
    if existentes and len(ofertas) < len(existentes) * piso_relativo:
        raise ValueError(
            f"arquivo da CVM veio com {len(ofertas)} ofertas contra {len(existentes)} "
            f"no banco -- abortando para não perder dado"
        )

    agora = datetime.now(timezone.utc)
    # CARGA INICIAL NÃO É NOVIDADE. Na primeira rodada as 4.400 ofertas dos
    # últimos três anos entrariam todas como "apareceu agora" e afogariam o
    # bloco de novidades da semana. A trilha passa a valer da segunda rodada
    # em diante, que é quando "novo" quer dizer novo de verdade.
    primeira_carga = not existentes
    novas = mudancas = 0
    for oferta in ofertas:
        atual = existentes.get(oferta.chave)
        if atual is None:
            db.add(OfertaCVM(**oferta.dados, coletado_em=agora, atualizado_em=agora))
            if not primeira_carga:
                db.add(OfertaCVMStatus(numero_requerimento=oferta.chave, status_anterior=None,
                                       status_novo=oferta.dados["status"], visto_em=agora))
            novas += 1
            continue
        if atual.status != oferta.dados["status"]:
            db.add(OfertaCVMStatus(numero_requerimento=oferta.chave,
                                   status_anterior=atual.status,
                                   status_novo=oferta.dados["status"], visto_em=agora))
            mudancas += 1
        for campo, valor in oferta.dados.items():
            setattr(atual, campo, valor)
        atual.atualizado_em = agora
    db.commit()
    return {"lidas": len(ofertas), "novas": novas, "mudancas": mudancas,
            "no_banco": len(existentes) + novas, "carga_inicial": primeira_carga}


def coletar(db: Session, *, url: str = URL_ZIP, pasta_backup: str | os.PathLike | None = None,
            conteudo_zip: bytes | None = None) -> dict:
    """Rodada completa: baixa, guarda o backup, converte e grava."""
    conteudo = conteudo_zip if conteudo_zip is not None else baixar_zip(url)
    if pasta_backup:
        salvar_backup(conteudo, pasta_backup)
    ofertas = parse(extrair_csv(conteudo))
    resumo = gravar(db, ofertas)
    logger.info("ofertas CVM: %(lidas)s lidas, %(novas)s novas, %(mudancas)s mudaram de status",
                resumo)
    return resumo
