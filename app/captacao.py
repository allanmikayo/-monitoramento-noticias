"""Aba "Cap. Líquida" (29/10/2026): captação líquida dos fundos de crédito
privado, Infra e CP Tradicional (CP ex Infra).

BASE. Dois exports que o Allan gera e sobe na Administração (ou pelo
`Importar Captacao Liquida.bat`):
  * diário  -- data_anls, segmento, nom_clie_gesr, qtd_fundos, patr_liqo, cpit_liqa
  * mensal  -- mes_ref,   segmento, nom_clie_gesr, qtd_fundos, patr_liqo, cpit_liqa_mtd
O tipo é reconhecido pelo cabeçalho. Cada importação SUBSTITUI o período que
o arquivo cobre (do dia mais antigo ao mais recente dele): o export é uma
extração completa, e uma gestora que some numa revisão não pode ficar
esquecida na base.

SEM NOME DE GESTORA OU FUNDO. Pedido do Allan: a aba é de sell side e não
cita casa nenhuma. O nome da gestora vira um hash na importação e o nome
não é gravado. O hash só permite contar gestoras e medir concentração
("as 10 maiores resgatantes responderam por X% do resgate") sem identificar
ninguém. Por isso as matrizes "Top 10 por asset" e o "Analítico Gestoras"
do dashboard original (QuickSight) NÃO foram reproduzidas.

DEFINIÇÕES (as do documento de passagem do dashboard original):
  * Last PL      -- soma do PL na data mais recente do diário.
  * FY / YTD     -- soma da captação mensal no ano.
  * %PL do YTD   -- captação do ano ÷ Last PL.
  * %PL do mês   -- captação do mês ÷ PL do mesmo mês.
"""
from __future__ import annotations

import collections
import hashlib
import re
from datetime import date, datetime

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from .models import CaptacaoDiaria, CaptacaoMensal

INFRA = "Infra"
CP_EX = "CP ex Infra"
SEGMENTOS = (CP_EX, INFRA)

# Cabeçalho do export -> campo. Diário e mensal só diferem na data e no
# nome da coluna de captação.
_COLUNAS = {
    "data_anls": "data", "mes_ref": "data",
    "segmento": "segmento", "nom_clie_gesr": "gestora",
    "qtd_fundos": "qtd_fundos", "patr_liqo": "pl",
    "cpit_liqa": "captacao", "cpit_liqa_mtd": "captacao",
}


def _norm(texto) -> str:
    return re.sub(r"\s+", "", str(texto or "")).strip().lower()


def tipo_do_cabecalho(cabecalho: list) -> str:
    nomes = {_norm(c) for c in cabecalho}
    if "data_anls" in nomes and "cpit_liqa" in nomes:
        return "diario"
    if "mes_ref" in nomes and "cpit_liqa_mtd" in nomes:
        return "mensal"
    raise ValueError("não reconheci o arquivo: espero o export diário (data_anls, cpit_liqa) "
                     "ou o mensal (mes_ref, cpit_liqa_mtd)")


def hash_gestora(nome: str) -> str:
    """Identificador anônimo e estável da gestora. Mesmo nome (ignorando
    caixa e espaços repetidos) -> mesmo hash, em qualquer importação."""
    chave = " ".join(str(nome or "").split()).upper()
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()[:16]


def segmento_padrao(valor) -> str | None:
    t = _norm(valor)
    if t in ("infras", "infra"):
        return INFRA
    if t in ("cpexinfras", "cpexinfra"):
        return CP_EX
    return None


def _numero(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.lower() in ("nan", "null", "none"):
        return None
    try:
        return float(s)
    except ValueError:
        pass
    try:  # padrão brasileiro: 1.234.567,89
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _data(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    return None


def converter_linhas(cabecalho: list, linhas: list[list]) -> tuple[str, list[dict], int]:
    """(tipo, registros prontos para gravar, linhas descartadas). O nome da
    gestora sai daqui já como hash."""
    tipo = tipo_do_cabecalho(cabecalho)
    idx = {}
    for i, c in enumerate(cabecalho):
        campo = _COLUNAS.get(_norm(c))
        if campo and campo not in idx:
            idx[campo] = i
    faltando = {"data", "segmento", "gestora", "captacao"} - set(idx)
    if faltando:
        raise ValueError(f"faltam colunas no arquivo: {sorted(faltando)}")

    def pega(linha, campo):
        i = idx.get(campo)
        return linha[i] if i is not None and i < len(linha) else None

    saida, descartadas = [], 0
    for linha in linhas:
        dt, seg, nome = _data(pega(linha, "data")), segmento_padrao(pega(linha, "segmento")), pega(linha, "gestora")
        if dt is None or seg is None or not str(nome or "").strip():
            descartadas += 1
            continue
        if tipo == "mensal":
            dt = dt.replace(day=1)
        qtd = _numero(pega(linha, "qtd_fundos"))
        saida.append({
            "data": dt, "segmento": seg, "gestora": hash_gestora(nome),
            "qtd_fundos": int(qtd) if qtd is not None else None,
            "pl": _numero(pega(linha, "pl")), "captacao": _numero(pega(linha, "captacao")),
        })
    return tipo, saida, descartadas


def _modelo(tipo: str):
    return CaptacaoDiaria if tipo == "diario" else CaptacaoMensal


def _col_data(modelo):
    return modelo.data if modelo is CaptacaoDiaria else modelo.mes


def apagar_periodo(db: Session, tipo: str, de: date, ate: date) -> int:
    """Apaga o período que o arquivo cobre, antes de gravá-lo de novo."""
    modelo = _modelo(tipo)
    col = _col_data(modelo)
    if tipo == "mensal":
        de, ate = de.replace(day=1), ate.replace(day=1)
    return db.execute(delete(modelo).where(col >= de, col <= ate)).rowcount or 0


def importar(db: Session, tipo: str, registros: list[dict]) -> dict:
    """Grava um lote. Não faz commit -- quem chama decide. Repetição dentro
    do lote (mesma data, segmento e gestora) fica com a última."""
    modelo = _modelo(tipo)
    unicos = {(r["data"], r["segmento"], r["gestora"]): r for r in registros}
    valores = list(unicos.values())
    if modelo is CaptacaoMensal:
        valores = [{**{k: v for k, v in r.items() if k != "data"}, "mes": r["data"]} for r in valores]
    for k in range(0, len(valores), 2000):
        db.execute(insert(modelo), valores[k:k + 2000])
    datas = [r["data"] for r in unicos.values()]
    return {"tipo": tipo, "gravadas": len(valores),
            "de": min(datas).isoformat() if datas else None,
            "ate": max(datas).isoformat() if datas else None}


def status_importacao(db: Session) -> dict:
    out = {}
    for tipo in ("diario", "mensal"):
        modelo = _modelo(tipo)
        col = _col_data(modelo)
        try:
            n, de, ate = db.execute(select(func.count(modelo.id), func.min(col), func.max(col))).one()
        except Exception:  # tabela ainda não criada
            db.rollback()
            n, de, ate = 0, None, None
        out[tipo] = {"linhas": n, "de": de.isoformat() if de else None, "ate": ate.isoformat() if ate else None}
    return out


# ---------------------------------------------------------------------------
# Consultas da aba
# ---------------------------------------------------------------------------
def _linhas_segmento(valores: dict) -> list[dict]:
    """{segmento: {campo: valor}} -> três linhas, Total CP primeiro."""
    total = collections.defaultdict(float)
    for seg in SEGMENTOS:
        for k, v in valores.get(seg, {}).items():
            if v is not None:
                total[k] += v
    return ([{"segmento": "Total CP", **total}]
            + [{"segmento": seg, **valores.get(seg, {})} for seg in SEGMENTOS])


def _pct(num, den):
    return round(100 * num / den, 2) if num is not None and den else None


def resumo(db: Session) -> dict:
    """Os três quadros do topo: Last PL, captação por ano e do mês."""
    ref = db.scalar(select(func.max(CaptacaoDiaria.data)))
    ult_mes = db.scalar(select(func.max(CaptacaoMensal.mes)))
    if ref is None and ult_mes is None:
        return {"referencia": None}

    # Last PL e nº de fundos na data mais recente do diário.
    last = {}
    if ref is not None:
        for seg, pl, fundos in db.execute(
            select(CaptacaoDiaria.segmento, func.sum(CaptacaoDiaria.pl), func.sum(CaptacaoDiaria.qtd_fundos))
            .where(CaptacaoDiaria.data == ref).group_by(CaptacaoDiaria.segmento)
        ):
            last[seg] = {"pl": pl, "fundos": fundos}
    last_pl = {r["segmento"]: r.get("pl") for r in _linhas_segmento(last)}

    # Captação por mês e segmento (base dos quadros de ano e de mês).
    por_mes = collections.defaultdict(dict)
    for mes, seg, cap, pl in db.execute(
        select(CaptacaoMensal.mes, CaptacaoMensal.segmento, func.sum(CaptacaoMensal.captacao),
               func.sum(CaptacaoMensal.pl)).group_by(CaptacaoMensal.mes, CaptacaoMensal.segmento)
    ):
        por_mes[mes][seg] = {"cap": cap or 0.0, "pl": pl}

    ano = (ref or ult_mes).year
    anos = [ano - 2, ano - 1, ano]
    por_ano = {}
    for seg in SEGMENTOS:
        por_ano[seg] = {str(a): sum(v[seg]["cap"] for m, v in por_mes.items() if m.year == a and seg in v)
                        for a in anos}
    linhas_ano = _linhas_segmento(por_ano)
    for linha in linhas_ano:
        linha["pct_pl_ytd"] = _pct(linha.get(str(ano)), last_pl.get(linha["segmento"]))

    # Mês fechado = o último mês ANTES do mês da data mais recente; mês
    # corrente = o da data mais recente (parcial, "até dd/mm").
    mes_corrente = (ref or ult_mes).replace(day=1)
    fechados = sorted(m for m in por_mes if m < mes_corrente)
    mes_fechado = fechados[-1] if fechados else None

    def quadro_mes(mes):
        if mes is None or mes not in por_mes:
            return None
        linhas = _linhas_segmento({s: {"cap": v["cap"], "pl": v["pl"]} for s, v in por_mes[mes].items()})
        for linha in linhas:
            linha["pct_pl"] = _pct(linha.get("cap"), linha.get("pl"))
        return {"mes": mes.isoformat(), "linhas": linhas}

    return {
        "referencia": ref.isoformat() if ref else None,
        "ultimo_mes": ult_mes.isoformat() if ult_mes else None,
        "last_pl": _linhas_segmento(last),
        "anos": [str(a) for a in anos],
        "por_ano": linhas_ano,
        "mes_fechado": quadro_mes(mes_fechado),
        "mes_corrente": quadro_mes(mes_corrente),
    }


def mensal(db: Session) -> dict:
    """Série mensal por segmento: captação e PL do mês."""
    linhas = db.execute(
        select(CaptacaoMensal.mes, CaptacaoMensal.segmento, func.sum(CaptacaoMensal.captacao),
               func.sum(CaptacaoMensal.pl))
        .group_by(CaptacaoMensal.mes, CaptacaoMensal.segmento).order_by(CaptacaoMensal.mes)
    ).all()
    meses = sorted({m for m, *_ in linhas})
    serie = {seg: {"captacao": [None] * len(meses), "pl": [None] * len(meses)} for seg in SEGMENTOS}
    pos = {m: i for i, m in enumerate(meses)}
    for mes, seg, cap, pl in linhas:
        if seg in serie:
            serie[seg]["captacao"][pos[mes]] = cap
            serie[seg]["pl"][pos[mes]] = pl
    ref = db.scalar(select(func.max(CaptacaoDiaria.data)))
    return {"meses": [m.isoformat() for m in meses], "series": serie,
            "referencia": ref.isoformat() if ref else None}


def diario(db: Session, dias: int) -> dict:
    """Últimos `dias` dias com dado: captação por segmento."""
    datas = [d for (d,) in db.execute(
        select(CaptacaoDiaria.data).distinct().order_by(CaptacaoDiaria.data.desc()).limit(dias))]
    if not datas:
        return {"datas": [], "series": {}}
    datas.sort()
    linhas = db.execute(
        select(CaptacaoDiaria.data, CaptacaoDiaria.segmento, func.sum(CaptacaoDiaria.captacao))
        .where(CaptacaoDiaria.data >= datas[0])
        .group_by(CaptacaoDiaria.data, CaptacaoDiaria.segmento)
    ).all()
    pos = {d: i for i, d in enumerate(datas)}
    serie = {seg: [None] * len(datas) for seg in SEGMENTOS}
    for d, seg, cap in linhas:
        if seg in serie and d in pos:
            serie[seg][pos[d]] = cap
    return {"datas": [d.isoformat() for d in datas], "series": serie}


TOP_CONCENTRACAO = 10


def amplitude(db: Session, segmento: str, meses: int = 13) -> dict:
    """Quanto do segmento captou ou resgatou em cada mês, sem nomes.

    Para cada mês: % do PL do segmento em gestoras com captação positiva,
    negativa ou nula; quantas gestoras de cada lado; e quanto do resgate
    (ou da captação) bruto veio das 10 gestoras que mais resgataram (ou
    captaram). Gestora = hash (ver o topo do módulo)."""
    todos = [m for (m,) in db.execute(
        select(CaptacaoMensal.mes).distinct().order_by(CaptacaoMensal.mes.desc()).limit(meses))]
    if not todos:
        return {"meses": [], "segmento": segmento}
    todos.sort()
    por_mes = collections.defaultdict(list)
    for mes, pl, cap in db.execute(
        select(CaptacaoMensal.mes, CaptacaoMensal.pl, CaptacaoMensal.captacao)
        .where(CaptacaoMensal.segmento == segmento, CaptacaoMensal.mes >= todos[0])
    ):
        por_mes[mes].append((pl or 0.0, cap or 0.0))

    saida = []
    for mes in todos:
        g = por_mes.get(mes, [])
        pl_total = sum(pl for pl, _ in g)
        pos = [(pl, c) for pl, c in g if c > 0]
        neg = [(pl, c) for pl, c in g if c < 0]
        bruto_cap = sum(c for _, c in pos)
        bruto_res = sum(c for _, c in neg)
        top_cap = sum(sorted((c for _, c in pos), reverse=True)[:TOP_CONCENTRACAO])
        top_res = sum(sorted(c for _, c in neg)[:TOP_CONCENTRACAO])
        pl_pos, pl_neg = sum(pl for pl, _ in pos), sum(pl for pl, _ in neg)
        saida.append({
            "mes": mes.isoformat(),
            "pct_pl_captando": _pct(pl_pos, pl_total),
            "pct_pl_resgatando": _pct(pl_neg, pl_total),
            "pct_pl_neutro": _pct(pl_total - pl_pos - pl_neg, pl_total),
            "gestoras_captando": len(pos), "gestoras_resgatando": len(neg), "gestoras": len(g),
            "captacao_bruta": bruto_cap, "resgate_bruto": bruto_res, "liquida": bruto_cap + bruto_res,
            "top_resgate_pct": _pct(top_res, bruto_res),
            "top_captacao_pct": _pct(top_cap, bruto_cap),
        })
    return {"segmento": segmento, "top": TOP_CONCENTRACAO, "meses": saida}
