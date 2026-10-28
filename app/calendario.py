"""Calendário de resultados da cobertura (28/10/2026, pedido do Allan).

O QUE A TELA RESPONDE
---------------------
"Quem da cobertura divulga resultado nos próximos dias, e quem já
divulgou nesta temporada?" -- um cartão na página inicial.

DE ONDE VEM CADA DATA (em ordem de confiança)
---------------------------------------------
1. DIVULGADA -- a empresa já entregou. Duas fontes, vale a mais cedo:
   a. o coletor do RAD (app/sources/cvm_rad.py), que a cada 5 minutos
      grava como notícia os arquivamentos das empresas da cobertura --
      inclusive "ITR - Informações Trimestrais", "DFP" e "Press-release".
      É o que faz o calendário marcar "divulgou" no mesmo dia;
   b. os arquivos ITR/DFP dos Dados Abertos da CVM (tabela
      `resultados_cvm`), atualizados SEMANALMENTE pela CVM -- pegam quem o
      RAD perdeu.
2. CONFIRMADA -- data anunciada pela empresa e digitada por um admin no
   calendário (tabela `resultados_confirmados`).
3. ESTIMADA -- o mesmo trimestre do ano anterior, + 364 dias (mesmo dia da
   semana: empresa que divulga às quintas costuma continuar às quintas),
   limitada ao prazo da CVM. Sem o trimestre do ano anterior, usa o prazo
   que a empresa levou no último trimestre entregue.

A TEMPORADA
-----------
É o último trimestre encerrado: em 28/10 é o 3T (referência 30/09, prazo
do ITR 45 dias depois, 14/11). Para o 4T vale a DFP, prazo de 3 meses
(31/03). Fonte dos prazos: Resolução CVM 80.

QUEM É "A COBERTURA"
--------------------
As empresas do cadastro de notícias (`companies`, ~96). Cada uma pode ter
VÁRIOS CNPJs na CVM (holding e subsidiárias que emitem debênture): vêm de
`debentures.cnpj` e `issuers.cnpj` onde há `company_id`, mais as
companhias da CVM cujo nome normalizado (issuer_key) é igual ao nome ou a
um apelido da empresa. A empresa divulga quando a PRIMEIRA delas divulga.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    Article, Company, Debenture, DebentureCaracteristica, EmpresaTipoCobertura, EntregaResultadoCVM,
    Issuer, ResultadoConfirmado,
)
from .spreads.issuer_key import issuer_key

logger = logging.getLogger(__name__)

URL_ITR = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_{ano}.zip"
URL_DFP = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip"
TIMEOUT = 300

PRAZO_ITR_DIAS = 45
# Depois do prazo a temporada ainda fica na tela por um tempo: sempre há
# quem atrase, e "temporada encerrada: 58 de 61 divulgaram" é informação.
FOLGA_APOS_PRAZO = timedelta(days=21)


def so_digitos(cnpj: object) -> str:
    return re.sub(r"\D", "", str(cnpj or ""))


# ---------------------------------------------------------------------------
# Temporada
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Temporada:
    referencia: date      # fim do trimestre
    documento: str        # ITR | DFP
    prazo: date

    @property
    def trimestre(self) -> int:
        return (self.referencia.month - 1) // 3 + 1

    @property
    def rotulo(self) -> str:
        return f"{self.trimestre}T{self.referencia.year % 100:02d}"

    @property
    def inicio(self) -> date:
        return self.referencia + timedelta(days=1)

    def ano_anterior(self) -> date:
        return self.referencia.replace(year=self.referencia.year - 1)


def _fim_trimestre(ano: int, tri: int) -> date:
    mes = tri * 3
    return date(ano, mes, 31 if mes in (3, 12) else 30)


def temporada_para(hoje: date) -> Temporada:
    """O último trimestre encerrado ANTES de `hoje`."""
    tri = (hoje.month - 1) // 3          # trimestre anterior ao corrente (0 = 4T do ano passado)
    ref = _fim_trimestre(hoje.year - 1, 4) if tri == 0 else _fim_trimestre(hoje.year, tri)
    if ref.month == 12:
        return Temporada(ref, "DFP", date(ref.year + 1, 3, 31))
    return Temporada(ref, "ITR", ref + timedelta(days=PRAZO_ITR_DIAS))


# ---------------------------------------------------------------------------
# Quem é a cobertura na CVM
# ---------------------------------------------------------------------------
def entidades_por_empresa(db: Session, empresas: list[Company]) -> dict[int, set[str]]:
    """company_id -> CNPJs (só dígitos) que valem como "a empresa" na CVM."""
    ids = {c.id for c in empresas}
    saida: dict[int, set[str]] = {i: set() for i in ids}
    for cid, cnpj in db.execute(
        select(Debenture.company_id, Debenture.cnpj).where(Debenture.company_id.is_not(None)).distinct()
    ):
        if cid in saida and so_digitos(cnpj):
            saida[cid].add(so_digitos(cnpj))
    for cid, cnpj in db.execute(
        select(Issuer.company_id, Issuer.cnpj).where(Issuer.company_id.is_not(None))
    ):
        if cid in saida and so_digitos(cnpj):
            saida[cid].add(so_digitos(cnpj))

    # Pelo nome: companhia aberta sem debênture na base (a holding listada,
    # p. ex.). Igualdade de chave normalizada, sem aproximação -- ver o
    # porquê em app/spreads/issuer_key.py.
    chave_para_empresa: dict[str, int] = {}
    for c in empresas:
        for nome in [c.name] + [a.alias for a in c.aliases]:
            k = issuer_key(nome)
            if k and len(k) >= 4:      # "CSN", "EDP": curto demais para casar sozinho
                chave_para_empresa.setdefault(k, c.id)
    for cnpj, denom in db.execute(
        select(EntregaResultadoCVM.cnpj, EntregaResultadoCVM.denominacao).distinct()
    ):
        cid = chave_para_empresa.get(issuer_key(denom))
        if cid is not None:
            saida[cid].add(cnpj)
    return saida


# ---------------------------------------------------------------------------
# Divulgações já feitas, vistas pelo RAD (tempo real)
# ---------------------------------------------------------------------------
_CATEGORIA_RESULTADO = re.compile(
    r"ITR\s*-\s*Informa|DFP\s*-\s*Demonstra|Press-release|"
    r"Demonstra[cç][oõ]es Financeiras (Intermedi|Anuais)", re.I)
_TAG_TRIMESTRE = re.compile(r"\b([1-4])\s*T\s*(\d{2})\b", re.I)
DOMINIO_RAD = "rad.cvm.gov.br"


def _tag_combina(texto: str, temporada: Temporada) -> bool:
    """O assunto diz de qual trimestre é o documento ("Release de Resultados
    2T26")? Se diz e não é desta temporada, é entrega atrasada da anterior
    -- não conta. Sem etiqueta, vale a janela de datas."""
    tags = {(int(t), int(a)) for t, a in _TAG_TRIMESTRE.findall(texto or "")}
    if not tags:
        return True
    return (temporada.trimestre, temporada.referencia.year % 100) in tags


def divulgacoes_rad(db: Session, temporada: Temporada, ate: date) -> dict[int, dict]:
    """company_id -> primeira divulgação da temporada vista no RAD."""
    inicio = datetime.combine(temporada.inicio, datetime.min.time(), tzinfo=timezone.utc)
    fim = datetime.combine(ate + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    artigos = db.scalars(
        select(Article).options(selectinload(Article.companies))
        .where(Article.domain == DOMINIO_RAD,
               Article.published_at >= inicio, Article.published_at < fim,
               or_(Article.title.ilike("%ITR%"), Article.title.ilike("%DFP%"),
                   Article.title.ilike("%Press-release%"),
                   Article.title.ilike("%Demonstra%Financeiras%")))
        .order_by(Article.published_at)
    ).all()
    saida: dict[int, dict] = {}
    for a in artigos:
        if not _CATEGORIA_RESULTADO.search(a.title or ""):
            continue
        if not _tag_combina(a.snippet or "", temporada):
            continue
        for c in a.companies:
            if c.id not in saida:      # já em ordem de data: o primeiro vence
                saida[c.id] = {"quando": a.published_at, "link": a.url,
                               "origem": (a.title or "").split(" — ", 1)[-1][:80]}
    return saida


# ---------------------------------------------------------------------------
# A agenda
# ---------------------------------------------------------------------------
def _estimar(entregas: list[EntregaResultadoCVM], temporada: Temporada) -> tuple[date | None, str]:
    """Data provável e de onde ela veio, a partir do histórico de entregas
    das entidades de UMA empresa."""
    if not entregas:
        return None, ""
    ref_aa = temporada.ano_anterior()
    mesmo_tri = [e for e in entregas if e.data_referencia == ref_aa and e.documento == temporada.documento]
    if mesmo_tri:
        base = min(e.data_entrega for e in mesmo_tri)
        estimada = base + timedelta(days=364)
        origem = f"no {temporada.rotulo[:2]}{ref_aa.year % 100:02d} saiu em {base:%d/%m/%Y}"
    else:
        anteriores = [e for e in entregas if e.data_referencia < temporada.referencia]
        if not anteriores:
            return None, ""
        ultima_ref = max(e.data_referencia for e in anteriores)
        atraso = min((e.data_entrega - e.data_referencia).days
                     for e in anteriores if e.data_referencia == ultima_ref)
        estimada = temporada.referencia + timedelta(days=atraso)
        origem = f"no último trimestre levou {atraso} dias"
    estimada = min(max(estimada, temporada.inicio), temporada.prazo)
    return estimada, origem


def agenda(db: Session, hoje: date | None = None) -> dict:
    """A agenda da temporada, no formato do relatório semanal (28/10/2026).

    A DATA de cada empresa vem da lista que o time revisa (importada no
    calendário, ou ajustada por um admin chip a chip). A CVM entra para
    dizer quem JÁ divulgou (RAD em tempo real + arquivos ITR/DFP) e para
    sugerir uma data a quem está sem data na lista (`estimativa`, só como
    dica -- a agenda não inventa data). Toda empresa ativa da cobertura
    aparece: com data ("agendada"), sem data ("sem_data") ou já divulgada.
    """
    from zoneinfo import ZoneInfo

    brt = ZoneInfo("America/Sao_Paulo")
    hoje = hoje or datetime.now(brt).date()
    tp = temporada_para(hoje)
    empresas = db.scalars(
        select(Company).options(selectinload(Company.aliases), selectinload(Company.sector))
        .where(Company.active.is_(True))
    ).all()
    entidades = entidades_por_empresa(db, empresas)
    todos_cnpjs = set().union(*entidades.values()) if entidades else set()

    entregas = db.scalars(
        select(EntregaResultadoCVM).where(
            EntregaResultadoCVM.cnpj.in_(todos_cnpjs or {""}),
            EntregaResultadoCVM.data_referencia >= date(tp.referencia.year - 2, 1, 1),
        )
    ).all() if todos_cnpjs else []
    por_cnpj: dict[str, list[EntregaResultadoCVM]] = {}
    for e in entregas:
        por_cnpj.setdefault(e.cnpj, []).append(e)

    rad = divulgacoes_rad(db, tp, hoje)
    agendadas = {r.company_id: r for r in db.scalars(
        select(ResultadoConfirmado).where(ResultadoConfirmado.data_referencia == tp.referencia))}
    tipos = _tipos(db)

    linhas = []
    for c in empresas:
        cnpjs = entidades.get(c.id, set())
        hist = [e for cnpj in cnpjs for e in por_cnpj.get(cnpj, [])]
        na_cvm = bool(hist) or c.id in rad
        t = tipos.get(c.id)
        ag = agendadas.get(c.id)
        base = {"company_id": c.id, "nome": c.name,
                "setor": c.sector.name if c.sector else None,
                "tipo": t.tipo if t else None,
                "status_relatorio": t.status_relatorio if t else None,
                "na_cvm": na_cvm,
                "entidades": sorted({e.denominacao for e in hist if e.denominacao})[:6],
                "data_agenda": ag.data_prevista.isoformat() if ag else None,
                "horario": ag.horario if ag else None}

        # 1. Divulgada: RAD (tempo real) ou arquivo ITR/DFP, o que vier antes
        candidatos = []
        if c.id in rad:
            r = rad[c.id]
            quando = r["quando"]
            dia = (quando.astimezone(brt) if quando.tzinfo else quando.replace(tzinfo=timezone.utc).astimezone(brt)).date()
            candidatos.append((dia, r["link"], f"{r['origem']} (RAD)"))
        for e in hist:
            if e.data_referencia == tp.referencia and e.documento == tp.documento:
                candidatos.append((e.data_entrega, e.link, f"{e.documento} entregue à CVM"))
        if candidatos:
            d, link, origem = min(candidatos, key=lambda x: x[0])
            linhas.append({**base, "status": "divulgada", "data": d.isoformat(), "origem": origem, "link": link})
            continue

        # 2. Data da agenda do time
        if ag:
            if ag.data_prevista >= hoje:
                status, origem = "agendada", "agenda do time"
            elif na_cvm:
                # Passou da data e a CVM ainda não mostra entrega: pode ser
                # atraso de verdade ou só a CVM (o RAD costuma pegar no dia).
                status, origem = "pendente", "data da agenda já passou; sem entrega na CVM ainda"
            else:
                # Sem registro na CVM (offshore, fechada): não há como
                # confirmar por lá -- vale a data da agenda.
                status, origem = "divulgada", "data da agenda (sem registro na CVM para confirmar)"
            linhas.append({**base, "status": status, "data": ag.data_prevista.isoformat(), "origem": origem})
            continue

        # 3. Sem data na agenda. A estimativa pelo histórico vai só como dica.
        estimada, como = _estimar(hist, tp)
        linhas.append({**base, "status": "sem_data", "data": None,
                       "origem": "sem data na agenda" + (" · entrega ITR/DFP na CVM" if na_cvm else ""),
                       "estimativa": estimada.isoformat() if estimada else None,
                       "estimativa_origem": como or None})

    linhas.sort(key=lambda l: (l["data"] or "9999", l["nome"]))

    # Resumo no formato do relatório semanal
    seg = hoje - timedelta(days=hoje.weekday())
    sex, prox_seg = seg + timedelta(days=4), seg + timedelta(days=7)
    prox_sex = prox_seg + timedelta(days=4)
    na_semana = lambda l, a, b: l["data"] and a.isoformat() <= l["data"] <= b.isoformat()  # noqa: E731
    semana = [l for l in linhas if na_semana(l, seg, sex)]
    proxima = [l for l in linhas if na_semana(l, prox_seg, prox_sex)]
    resumo = {
        "total": len(linhas),
        "divulgada": sum(l["status"] == "divulgada" for l in linhas),
        "agendada": sum(l["status"] == "agendada" for l in linhas),
        "pendente": sum(l["status"] == "pendente" for l in linhas),
        "sem_data": sum(l["status"] == "sem_data" for l in linhas),
        "semana_total": len(semana),
        "semana_divulgadas": sum(l["status"] == "divulgada" for l in semana),
        "proxima_total": len(proxima),
    }
    ultima_cvm = db.scalar(select(EntregaResultadoCVM.data_entrega).order_by(
        EntregaResultadoCVM.data_entrega.desc()).limit(1))
    return {
        "hoje": hoje.isoformat(),
        "temporada": {"rotulo": tp.rotulo, "documento": tp.documento,
                      "referencia": tp.referencia.isoformat(), "prazo": tp.prazo.isoformat(),
                      "dias_para_prazo": (tp.prazo - hoje).days,
                      "encerrada": hoje > tp.prazo + FOLGA_APOS_PRAZO},
        "semanas": {"atual": [seg.isoformat(), sex.isoformat()],
                    "proxima": [prox_seg.isoformat(), prox_sex.isoformat()]},
        "resumo": resumo,
        "cvm_ate": ultima_cvm.isoformat() if ultima_cvm else None,
        "empresas": linhas,
    }


def _tipos(db: Session) -> dict:
    try:
        return {t.company_id: t for t in db.scalars(select(EmpresaTipoCobertura))}
    except Exception:  # tabela ainda não criada (nenhuma lista importada)
        db.rollback()
        return {}


TIPOS_COBERTURA = ("Local", "Offshore", "Offshore + Local")


def confirmar(db: Session, company_id: int, data_prevista: date | None, horario: str | None,
              autor: str | None, hoje: date | None = None, tipo: str | None = "",
              ) -> None:
    """Grava (ou apaga, com `data_prevista=None`) a data da agenda na
    temporada corrente. `tipo` (Local/Offshore/Offshore + Local) é
    atualizado junto quando vier; string vazia = não mexe."""
    tp = temporada_para(hoje or date.today())
    atual = db.scalar(select(ResultadoConfirmado).where(
        ResultadoConfirmado.company_id == company_id, ResultadoConfirmado.data_referencia == tp.referencia))
    if data_prevista is None:
        if atual:
            db.delete(atual)
    elif atual:
        atual.data_prevista, atual.horario, atual.criado_por = data_prevista, horario, autor
        atual.criado_em = datetime.now(timezone.utc)
    else:
        db.add(ResultadoConfirmado(company_id=company_id, data_referencia=tp.referencia,
                                   data_prevista=data_prevista, horario=horario, criado_por=autor))
    if tipo != "":
        _gravar_tipo(db, company_id, tipo or None, manter_status=True)
    db.commit()


def _gravar_tipo(db: Session, company_id: int, tipo: str | None, status: str | None = None,
                 manter_status: bool = False) -> None:
    t = db.get(EmpresaTipoCobertura, company_id)
    if t is None:
        db.add(EmpresaTipoCobertura(company_id=company_id, tipo=tipo, status_relatorio=status))
        return
    t.tipo = tipo
    if not manter_status:
        t.status_relatorio = status
    t.atualizado_em = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Lista de datas do time (28/10/2026)
# ---------------------------------------------------------------------------
# Formato colado do Excel: "Empresa  Data  Horário  Cobertura  Cor  Significado".
# Horário: After / Before / Confirmar. Linhas sem data: "Empresa (Local)".
_LINHA_COM_DATA = re.compile(
    r"^\s*(?P<nome>.+?)\s+(?P<data>\d{1,2}/\d{1,2}/\d{4})\s+(?P<hora>after|before|confirmar|a confirmar)\b"
    r"\s*(?P<cob>offshore\s*\+\s*local|local\s*\+\s*offshore|offshore|local)?(?P<resto>.*)$", re.I)
_LINHA_SEM_DATA = re.compile(r"^\s*(?P<nome>.+?)\s*\(\s*(?P<cob>offshore\s*\+\s*local|offshore|local)\s*\)\s*$", re.I)
HORARIOS = {"after": "após o fechamento", "before": "antes da abertura"}
STATUS_RELATORIO = (("restrit", "Restrito"), ("publicad", "Publicado"), ("execu", "Em execução"))


def _tipo_normalizado(txt: str | None) -> str | None:
    t = re.sub(r"\s+", " ", (txt or "").strip().lower())
    if not t:
        return None
    if "offshore" in t and "local" in t:
        return "Offshore + Local"
    return "Offshore" if "offshore" in t else "Local"


def ler_lista(texto: str) -> tuple[list[dict], list[str]]:
    """Linhas reconhecidas e linhas que não deu para ler (cabeçalho fica de fora)."""
    itens, ignoradas = [], []
    for bruta in (texto or "").splitlines():
        linha = bruta.replace("\t", " ").strip()
        if not linha or (linha.lower().startswith("empresa") and not re.search(r"\d{1,2}/\d{1,2}/", linha)):
            continue  # cabeçalho
        m = _LINHA_COM_DATA.match(linha)
        if m:
            d, mth, a = (int(x) for x in m["data"].split("/"))
            try:
                dia = date(a, mth, d)
            except ValueError:
                ignoradas.append(linha)
                continue
            resto = (m["resto"] or "").lower()
            status = next((rot for chave, rot in STATUS_RELATORIO if chave in resto), None)
            itens.append({"nome": m["nome"].strip(), "data": dia,
                          "horario": HORARIOS.get(m["hora"].lower()),
                          "tipo": _tipo_normalizado(m["cob"]), "status_relatorio": status})
            continue
        m = _LINHA_SEM_DATA.match(linha)
        if m:
            itens.append({"nome": m["nome"].strip(), "data": None, "horario": None,
                          "tipo": _tipo_normalizado(m["cob"]), "status_relatorio": None})
            continue
        ignoradas.append(linha)
    return itens, ignoradas


_ABREVIACOES = (("RIO JANEIRO", "RJ"), ("SAO PAULO", "SP"), ("MINAS GERAIS", "MG"))


def _abreviar(chave: str) -> str:
    """"Iguá Rio de Janeiro" e "Iguá RJ" viram a mesma chave."""
    for longo, curto in _ABREVIACOES:
        chave = re.sub(rf"\b{longo}\b", curto, chave)
    return chave


def _sem_espaco(chave: str) -> str:
    return chave.replace(" ", "")


def casar_nome(nome: str, empresas: list[Company]) -> Company | None:
    """Nome da lista -> empresa da cobertura. Em ordem, e só se a resposta
    for ÚNICA: (1) mesma chave normalizada do nome, de um apelido ou do nome
    sem o parêntese ("Norte Energia (Belo Monte)"); (2) igual sem espaços
    ("EZtec" x "EZ Tec"); (3) um é começo do outro ("BRK" x "BRK Ambiental",
    "CEEE Geração" x "CEEE-G"); (4) grafia quase igual ("Mercado Libre" x
    "Mercado Livre"). Ambíguo ou nada: devolve None e o nome vai para o
    relatório da importação, em vez de casar com a empresa errada."""
    import difflib

    alvo = _abreviar(issuer_key(nome))
    if not alvo:
        return None
    chaves: list[tuple[str, Company]] = []
    for c in empresas:
        for n in [c.name, re.sub(r"\(.*?\)", "", c.name)] + [a.alias for a in c.aliases]:
            k = _abreviar(issuer_key(n))
            if k:
                chaves.append((k, c))

    def unico(cands):
        ids = {c.id for c in cands}
        return cands[0] if len(ids) == 1 else None

    for teste in (lambda k: k == alvo,
                  lambda k: _sem_espaco(k) == _sem_espaco(alvo)):
        achou = unico([c for k, c in chaves if teste(k)])
        if achou:
            return achou
    a = _sem_espaco(alvo)
    if len(a) >= 3:
        achou = unico([c for k, c in chaves
                       if len(_sem_espaco(k)) >= 3 and (_sem_espaco(k).startswith(a) or a.startswith(_sem_espaco(k)))])
        if achou:
            return achou
    notas = sorted(((difflib.SequenceMatcher(None, a, _sem_espaco(k)).ratio(), c) for k, c in chaves),
                   key=lambda x: -x[0])
    if notas and notas[0][0] >= 0.85 and (len(notas) == 1 or notas[1][0] < notas[0][0] or notas[1][1].id == notas[0][1].id):
        return notas[0][1]
    return None


CHAVE_RELATORIO_IMPORTACAO = "agenda_ultima_importacao"


def importar_lista(db: Session, texto: str, autor: str | None, hoje: date | None = None) -> dict:
    """Substitui a agenda da temporada corrente pela lista colada.

    A lista é a revisão COMPLETA do time: empresas que estavam na agenda e
    sumiram da lista perdem a data (e voltam a "sem data"). Devolve -- e
    guarda, para a tela mostrar depois -- o que não casou e o que, na
    cobertura, ficou sem data, separando quem entrega ITR/DFP na CVM.
    """
    import json

    hoje = hoje or date.today()
    tp = temporada_para(hoje)
    itens, ignoradas = ler_lista(texto)
    if not itens:
        raise ValueError("nenhuma linha reconhecida -- confira o formato (Empresa, Data, Horário, Cobertura)")
    empresas = db.scalars(select(Company).options(selectinload(Company.aliases))
                          .where(Company.active.is_(True))).all()
    casados: dict[int, dict] = {}
    nao_reconhecidas = []
    for it in itens:
        c = casar_nome(it["nome"], empresas)
        if c is None:
            nao_reconhecidas.append(it["nome"])
        else:
            casados[c.id] = {**it, "empresa": c.name}

    db.query(ResultadoConfirmado).filter(ResultadoConfirmado.data_referencia == tp.referencia).delete()
    for cid, it in casados.items():
        if it["data"]:
            db.add(ResultadoConfirmado(company_id=cid, data_referencia=tp.referencia, data_prevista=it["data"],
                                       horario=it["horario"], criado_por=autor))
        _gravar_tipo(db, cid, it["tipo"], it["status_relatorio"])
    db.flush()

    # Quem da cobertura ficou sem data -- e, desses, quem entrega na CVM
    # (esses deveriam estar na lista: vão divulgar resultado).
    a = agenda(db, hoje)
    sem_data = [l for l in a["empresas"] if l["status"] == "sem_data"]
    relatorio = {
        "quando": datetime.now(timezone.utc).isoformat(), "por": autor, "temporada": tp.rotulo,
        "linhas_lidas": len(itens), "com_data": sum(1 for i in casados.values() if i["data"]),
        "nao_reconhecidas": nao_reconhecidas, "linhas_ignoradas": ignoradas[:20],
        "sem_data_entrega_cvm": [l["nome"] for l in sem_data if l["na_cvm"]],
        "sem_data_fora_cvm": [l["nome"] for l in sem_data if not l["na_cvm"]],
        "ja_divulgaram_fora_da_lista": [l["nome"] for l in a["empresas"]
                                         if l["status"] == "divulgada" and l["company_id"] not in casados],
    }
    from .models import AppSetting
    s = db.get(AppSetting, CHAVE_RELATORIO_IMPORTACAO)
    if s is None:
        db.add(AppSetting(key=CHAVE_RELATORIO_IMPORTACAO, value=json.dumps(relatorio, ensure_ascii=False)))
    else:
        s.value = json.dumps(relatorio, ensure_ascii=False)
    db.commit()
    return relatorio


def ultimo_relatorio(db: Session) -> dict | None:
    import json

    from .models import AppSetting
    s = db.get(AppSetting, CHAVE_RELATORIO_IMPORTACAO)
    return json.loads(s.value) if s and s.value else None


# ---------------------------------------------------------------------------
# Coleta dos arquivos ITR/DFP da CVM (scripts/coletar_resultados_cvm.py)
# ---------------------------------------------------------------------------
COLUNAS = {   # nome no arquivo da CVM -> nosso
    "CNPJ_CIA": "cnpj", "DT_REFER": "data_referencia", "VERSAO": "versao",
    "DENOM_CIA": "denominacao", "CD_CVM": "codigo_cvm", "DT_RECEB": "data_entrega",
    "LINK_DOC": "link",
}
OBRIGATORIAS = ("CNPJ_CIA", "DT_REFER", "DT_RECEB")


def ler_zip(conteudo: bytes, documento: str) -> list[dict]:
    """Linhas do CSV PRINCIPAL do zip (o `itr_cia_aberta_AAAA.csv`, não os de
    balanço/DRE). Latin-1 e ";" como todo arquivo da CVM. Uma coluna
    obrigatória que some vira erro alto: o job fica vermelho e o banco fica
    com o dado da semana passada, em vez de gravar datas vazias."""
    padrao = re.compile(rf"^{documento.lower()}_cia_aberta_\d{{4}}\.csv$", re.I)
    with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
        nomes = [n for n in z.namelist() if padrao.match(n.rsplit("/", 1)[-1])]
        if not nomes:
            raise ValueError(f"{documento}: CSV principal não encontrado no zip ({z.namelist()[:8]})")
        texto = z.read(nomes[0]).decode("latin-1")
    leitor = csv.DictReader(io.StringIO(texto), delimiter=";")
    cab = {(h or "").strip().upper(): h for h in (leitor.fieldnames or [])}
    faltam = [c for c in OBRIGATORIAS if c not in cab]
    if faltam:
        raise ValueError(f"{documento}: colunas {faltam} não estão no arquivo (veio: {list(cab)[:12]})")
    saida = []
    for linha in leitor:
        r = {nosso: (linha.get(cab[orig]) or "").strip() for orig, nosso in COLUNAS.items() if orig in cab}
        try:
            r["data_referencia"] = date.fromisoformat(r["data_referencia"][:10])
            r["data_entrega"] = date.fromisoformat(r["data_entrega"][:10])
        except ValueError:
            continue
        r["cnpj"] = so_digitos(r["cnpj"])
        r["versao"] = int(r["versao"]) if (r.get("versao") or "").isdigit() else None
        r["documento"] = documento
        if r["cnpj"]:
            saida.append(r)
    return saida


def cnpjs_de_interesse(db: Session) -> set[str]:
    """Todo emissor de debênture que o Hub conhece (cobertura ou não: é
    barato, e deixa pronto um calendário do mercado inteiro)."""
    cnpjs: set[str] = set()
    for (c,) in db.execute(select(Debenture.cnpj).distinct()):
        cnpjs.add(so_digitos(c))
    for (c,) in db.execute(select(Issuer.cnpj)):
        cnpjs.add(so_digitos(c))
    try:
        for (c,) in db.execute(select(DebentureCaracteristica.cnpj).distinct()):
            cnpjs.add(so_digitos(c))
    except Exception:  # tabela ainda não criada
        db.rollback()
    cnpjs.discard("")
    return cnpjs


def chaves_da_cobertura(db: Session) -> set[str]:
    chaves = set()
    for c in db.scalars(select(Company).options(selectinload(Company.aliases))):
        for nome in [c.name] + [a.alias for a in c.aliases]:
            k = issuer_key(nome)
            if k and len(k) >= 4:
                chaves.add(k)
    return chaves


def gravar(db: Session, linhas: list[dict]) -> dict:
    """Guarda a PRIMEIRA entrega de cada (CNPJ, documento, referência)."""
    interesse = cnpjs_de_interesse(db)
    chaves = chaves_da_cobertura(db)
    primeiras: dict[tuple, dict] = {}
    for r in linhas:
        if r["cnpj"] not in interesse and issuer_key(r.get("denominacao")) not in chaves:
            continue
        k = (r["cnpj"], r["documento"], r["data_referencia"])
        if k not in primeiras or r["data_entrega"] < primeiras[k]["data_entrega"]:
            primeiras[k] = r
    existentes = {(e.cnpj, e.documento, e.data_referencia): e for e in db.scalars(
        select(EntregaResultadoCVM).where(EntregaResultadoCVM.cnpj.in_({k[0] for k in primeiras} or {""})))}
    novas = atualizadas = 0
    agora = datetime.now(timezone.utc)
    for k, r in primeiras.items():
        e = existentes.get(k)
        if e is None:
            db.add(EntregaResultadoCVM(
                cnpj=r["cnpj"], codigo_cvm=r.get("codigo_cvm") or None, denominacao=r.get("denominacao") or None,
                documento=r["documento"], data_referencia=r["data_referencia"], data_entrega=r["data_entrega"],
                versao=r.get("versao"), link=(r.get("link") or None), atualizado_em=agora))
            novas += 1
        elif r["data_entrega"] < e.data_entrega:
            e.data_entrega, e.versao, e.link, e.atualizado_em = r["data_entrega"], r.get("versao"), r.get("link"), agora
            atualizadas += 1
    db.commit()
    return {"lidas": len(linhas), "de_interesse": len(primeiras), "novas": novas, "atualizadas": atualizadas}


def baixar(url: str) -> bytes:
    import requests

    ultimo = None
    for tentativa in range(3):
        try:
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code == 404:
                raise FileNotFoundError(url)
            r.raise_for_status()
            return r.content
        except FileNotFoundError:
            raise
        except Exception as e:  # noqa: BLE001
            ultimo = e
            logger.warning("falha baixando %s (tentativa %d/3): %s", url, tentativa + 1, e)
    raise RuntimeError(f"não consegui baixar {url}: {ultimo}")


def arquivos_a_coletar(hoje: date) -> list[tuple[str, str]]:
    """ITR deste ano e do anterior; DFP dos dois anos anteriores (a DFP de
    2025 sai em 2026). É o que cobre a temporada corrente e o mesmo
    trimestre do ano anterior."""
    return [("ITR", URL_ITR.format(ano=hoje.year)), ("ITR", URL_ITR.format(ano=hoje.year - 1)),
            ("DFP", URL_DFP.format(ano=hoje.year - 1)), ("DFP", URL_DFP.format(ano=hoje.year - 2))]
