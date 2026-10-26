"""Liga cada oferta da CVM ao que ela emitiu e a quem carrega o risco
(26/10/2026, pedido do Allan: "a que taxa saiu" e setor de quem emite).

O PROBLEMA. O arquivo de ofertas da CVM não traz ticker, taxa nem prazo.
O arquivo de características do debentures.com.br traz tudo isso por
ticker -- mas não traz o número do requerimento da CVM. Não existe chave
comum; o casamento é inferido.

O MÉTODO (debêntures). Para cada oferta, os candidatos são as debêntures
do MESMO CNPJ (obrigatório). Elas são agrupadas por emissão (número +
data do registro CVM, que é o que o arquivo de características chama de
"Registro CVM da Emissão") e cada grupo ganha pontos:

    número da emissão igual ...................... +4
    data do registro CVM a até 3 dias ............ +4   (até 30 dias: +2)
    data de emissão entre 120 dias antes do
      registro e 30 dias depois do encerramento .. +1
    volume (Σ quantidade × valor nominal) a até
      10% do valor registrado .................... +2   (até 35%: +1)

O melhor grupo leva a oferta se somar 5 ou mais. A confiança é ALTA com 8
ou mais (emissão e data batendo), MÉDIA de 6 a 7 e BAIXA com 5 -- o caso
típico de BAIXA é a reabertura de série, que repete o número da emissão
mas não a data. Nenhum critério sozinho basta: CNPJ + emissão erra em
reabertura e oferta secundária da mesma emissão; data + volume erra quando
a empresa registra duas emissões no mesmo dia.

SETOR. Debênture: setor/subsetor/grupo dos tickers do mesmo CNPJ no
cadastro de debêntures (a taxonomia do Hub é por ticker). CRI/CRA: o
emissor na CVM é a securitizadora, não quem deve -- então, nessa ordem:
  1. CNPJ citado no campo "devedores/coobrigados" da oferta, se for de uma
     empresa com debênture classificada;
  2. a mesma securitizadora + número da emissão no cadastro de CRI/CRA;
  3. "Não classificado".
"""
from __future__ import annotations

import collections
import re
import unicodedata
from datetime import date, timedelta

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from ..models import (Debenture, DebentureCaracteristica, OfertaCVM, OfertaCVMAtivo,
                      OfertaCVMClassificacao, Securitizado)

NAO_CLASSIFICADO = "Não classificado"
CORTE = 5
_CNPJ_RE = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}")
_SUFIXOS = re.compile(r"\b(S\.?A\.?|S/A|LTDA\.?|EIRELI|SECURITIZADORA|COMPANHIA|CIA\.?)\b")


def so_digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def emissao_num(valor: str | None) -> int | None:
    d = so_digitos(valor)
    return int(d) if d else None


def nome_normalizado(nome: str | None) -> str:
    s = unicodedata.normalize("NFKD", (nome or "").upper()).encode("ascii", "ignore").decode()
    s = _SUFIXOS.sub(" ", s)
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", s).split())


# ---------------------------------------------------------------------------
# Debêntures: oferta -> tickers
# ---------------------------------------------------------------------------
def _pontuar(oferta, grupo: list) -> tuple[int, list[str]]:
    pontos, motivos = 0, []
    em = emissao_num(oferta.emissao)
    if em is not None and grupo[0].emissao == em:
        pontos += 4
        motivos.append("emissão")
    reg = grupo[0].data_registro_cvm
    if oferta.data_registro and reg:
        dif = abs((reg - oferta.data_registro).days)
        if dif <= 3:
            pontos += 4
            motivos.append("registro")
        elif dif <= 30:
            pontos += 2
            motivos.append("registro~")
    datas_em = [c.data_emissao for c in grupo if c.data_emissao]
    if datas_em and oferta.data_registro:
        ini = oferta.data_registro - timedelta(days=120)
        fim = (oferta.data_encerramento or oferta.data_registro) + timedelta(days=30)
        if any(ini <= d <= fim for d in datas_em):
            pontos += 1
            motivos.append("data")
    volume = sum((c.quantidade_emitida or 0) * (c.valor_nominal_emissao or 0) for c in grupo)
    if volume and oferta.valor_total:
        desvio = abs(volume / oferta.valor_total - 1)
        if desvio <= 0.10:
            pontos += 2
            motivos.append("volume")
        elif desvio <= 0.35:
            pontos += 1
            motivos.append("volume~")
    return pontos, motivos


def _confianca(pontos: int) -> str:
    return "alta" if pontos >= 8 else "média" if pontos >= 6 else "baixa"


def casar_debentures(ofertas: list, caracs: list) -> list[dict]:
    """Ofertas de debênture -> [{numero_requerimento, codigo, metodo, confianca, pontos}]."""
    por_cnpj: dict[str, dict[tuple, list]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for c in caracs:
        cnpj = so_digitos(c.cnpj)
        if cnpj:
            por_cnpj[cnpj][(c.emissao, c.data_registro_cvm)].append(c)
    saida = []
    for o in ofertas:
        grupos = por_cnpj.get(so_digitos(o.cnpj_emissor))
        if not grupos:
            continue
        melhor = max(((*_pontuar(o, g), g) for g in grupos.values()), key=lambda x: x[0])
        pontos, motivos, grupo = melhor
        if pontos < CORTE:
            continue
        for c in grupo:
            saida.append({"numero_requerimento": o.numero_requerimento, "codigo": c.codigo,
                          "metodo": "+".join(motivos), "confianca": _confianca(pontos), "pontos": pontos})
    return saida


# ---------------------------------------------------------------------------
# Setor de quem carrega o risco
# ---------------------------------------------------------------------------
def _moda(valores):
    valores = [v for v in valores if v]
    return collections.Counter(valores).most_common(1)[0][0] if valores else None


def _taxonomia_por_cnpj(db: Session) -> dict[str, dict]:
    """CNPJ -> setor/subsetor/grupo/nome, a partir dos tickers classificados."""
    cnpj_por_codigo = {c: so_digitos(cnpj) for c, cnpj in db.execute(
        select(DebentureCaracteristica.codigo, DebentureCaracteristica.cnpj))}
    acc: dict[str, list] = collections.defaultdict(list)
    for d in db.scalars(select(Debenture)):
        cnpj = so_digitos(d.cnpj) or cnpj_por_codigo.get(d.codigo, "")
        if cnpj:
            acc[cnpj].append(d)
    return {cnpj: {"setor": _moda([d.setor for d in ds]), "subsetor": _moda([d.subsetor for d in ds]),
                   "grupo": _moda([d.grupo_economico for d in ds]), "nome": _moda([d.nome for d in ds])}
            for cnpj, ds in acc.items()}


def _taxonomia_securitizados(db: Session) -> dict[tuple, dict]:
    """(securitizadora normalizada, emissão) -> setor/subsetor/grupo/devedor."""
    acc: dict[tuple, list] = collections.defaultdict(list)
    for s in db.scalars(select(Securitizado)):
        em = emissao_num(s.emissao)
        if s.emissor and em is not None:
            acc[(nome_normalizado(s.emissor), em)].append(s)
    return {k: {"setor": _moda([s.setor for s in v]), "subsetor": _moda([s.subsetor for s in v]),
                "grupo": _moda([s.grupo_economico for s in v]), "nome": _moda([s.originador_credito for s in v])}
            for k, v in acc.items()}


# Nome curto demais casa por acaso dentro de outro ("ALFA" em "ALFAVILLE").
MIN_NOME = 10


def _taxonomia_por_nome(db: Session) -> dict[str, dict]:
    """Nome normalizado -> setor/subsetor/grupo, de todo lugar onde o Hub já
    classificou alguém: debêntures (nome do emissor), CRI/CRA (originador)
    e o cadastro de emissores."""
    acc: dict[str, list] = collections.defaultdict(list)
    for d in db.scalars(select(Debenture)):
        if d.setor and d.nome:
            acc[nome_normalizado(d.nome)].append((d.setor, d.subsetor, d.grupo_economico, d.nome))
    for s in db.scalars(select(Securitizado)):
        if s.setor and s.originador_credito:
            acc[nome_normalizado(s.originador_credito)].append(
                (s.setor, s.subsetor, s.grupo_economico, s.originador_credito))
    try:
        from ..models import Issuer
        for i in db.scalars(select(Issuer)):
            if i.setor:
                for n in (i.nome, i.nome_anbima):
                    if n:
                        acc[nome_normalizado(n)].append((i.setor, i.sub_setor, i.grupo_economico, i.nome))
    except Exception:  # cadastro de emissores ausente: segue sem ele
        db.rollback()
    return {nome: {"setor": _moda([v[0] for v in vs]), "subsetor": _moda([v[1] for v in vs]),
                   "grupo": _moda([v[2] for v in vs]), "nome": vs[0][3]}
            for nome, vs in acc.items() if len(nome) >= MIN_NOME}


def _por_texto(texto: str, tax_nome: dict, nomes_ordenados: list[str]) -> dict | None:
    """Primeiro nome classificado que aparece INTEIRO no texto do devedor
    (mais longos primeiro, para "RAIZEN ENERGIA" ganhar de "RAIZEN")."""
    alvo = f" {nome_normalizado(texto)} "
    if len(alvo) < MIN_NOME:
        return None
    for nome in nomes_ordenados:
        if f" {nome} " in alvo:
            return tax_nome[nome]
    return None


def classificar(oferta, tax_cnpj: dict, tax_sec: dict, tax_nome: dict | None = None,
                nomes_ordenados: list[str] | None = None) -> dict:
    tax_nome = tax_nome or {}
    nomes_ordenados = nomes_ordenados or sorted(tax_nome, key=len, reverse=True)
    base = {"numero_requerimento": oferta.numero_requerimento, "devedor": None,
            "setor": NAO_CLASSIFICADO, "subsetor": None, "grupo": None, "origem": None}

    def preencher(t: dict | None, origem: str, devedor: str | None):
        if not t or not t.get("setor"):
            return False
        base.update(setor=t["setor"], subsetor=t["subsetor"], grupo=t["grupo"], origem=origem,
                    devedor=(devedor or t.get("nome") or base["devedor"]))
        return True

    if oferta.instrumento == "DEB":
        base["devedor"] = oferta.nome_emissor
        (preencher(tax_cnpj.get(so_digitos(oferta.cnpj_emissor)), "cnpj_emissor", oferta.nome_emissor)
         or preencher(tax_nome.get(nome_normalizado(oferta.nome_emissor)), "nome_emissor", oferta.nome_emissor))
        return base

    base["devedor"] = oferta.devedor_curto
    for achado in _CNPJ_RE.findall(oferta.devedor or ""):
        if preencher(tax_cnpj.get(so_digitos(achado)), "cnpj_devedor", None):
            return base
    em = emissao_num(oferta.emissao)
    if em is not None and preencher(tax_sec.get((nome_normalizado(oferta.nome_emissor), em)),
                                    "cadastro_cri_cra", oferta.devedor_curto):
        return base
    preencher(_por_texto(oferta.devedor or oferta.devedor_curto or "", tax_nome, nomes_ordenados),
              "nome_devedor", None)
    return base


# ---------------------------------------------------------------------------
# Rodada completa
# ---------------------------------------------------------------------------
def recalcular(db: Session) -> dict:
    """Refaz as duas tabelas do zero e devolve estatísticas do casamento."""
    ofertas = list(db.scalars(select(OfertaCVM)))
    debs = [o for o in ofertas if o.instrumento == "DEB"]
    caracs = list(db.scalars(select(DebentureCaracteristica)))
    ligacoes = casar_debentures(debs, caracs)

    tax_cnpj = _taxonomia_por_cnpj(db)
    tax_sec = _taxonomia_securitizados(db)
    tax_nome = _taxonomia_por_nome(db)
    nomes = sorted(tax_nome, key=len, reverse=True)
    classes = [classificar(o, tax_cnpj, tax_sec, tax_nome, nomes) for o in ofertas]

    db.execute(delete(OfertaCVMAtivo))
    db.execute(delete(OfertaCVMClassificacao))
    for k in range(0, len(ligacoes), 2000):
        db.execute(insert(OfertaCVMAtivo), ligacoes[k:k + 2000])
    for k in range(0, len(classes), 2000):
        db.execute(insert(OfertaCVMClassificacao), classes[k:k + 2000])
    db.commit()

    casadas = {x["numero_requerimento"]: x["confianca"] for x in ligacoes}
    debs_validas = [o for o in debs if (o.valor_total or 0) > 0 and o.data_registro]
    por_conf = collections.Counter(casadas[o.numero_requerimento] for o in debs_validas
                                   if o.numero_requerimento in casadas)
    vol_total = sum(o.valor_total or 0 for o in debs_validas)
    vol_casado = sum(o.valor_total or 0 for o in debs_validas if o.numero_requerimento in casadas)
    classificadas = collections.Counter(
        (o.instrumento, c["setor"] != NAO_CLASSIFICADO) for o, c in zip(ofertas, classes))
    vol_inst = collections.Counter()
    vol_cls = collections.Counter()
    origens = collections.Counter(c["origem"] for c in classes if c["origem"])
    for o, c in zip(ofertas, classes):
        if o.status in ("Oferta Encerrada",) and (o.valor_total or 0) > 0:
            vol_inst[o.instrumento] += o.valor_total
            if c["setor"] != NAO_CLASSIFICADO:
                vol_cls[o.instrumento] += o.valor_total
    return {
        "debentures_registradas": len(debs_validas),
        "debentures_casadas": sum(por_conf.values()),
        "por_confianca": dict(por_conf),
        "volume_casado_pct": round(100 * vol_casado / vol_total, 1) if vol_total else None,
        "tickers_ligados": len(ligacoes),
        "classificadas": {f"{i}:{'sim' if ok else 'nao'}": n for (i, ok), n in sorted(classificadas.items())},
        "volume_classificado_pct": {i: round(100 * vol_cls[i] / v, 1) for i, v in vol_inst.items() if v},
        "origens": dict(origens),
    }
