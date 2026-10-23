"""Aba "Mercado Secundário" (23/10/2026, pedido do Allan) — volume negociado
de debêntures a partir do export que ele gera e sobe pela Administração.

Substitui a aba "Balcão B3". A pergunta da tela é "quanto o mercado está
girando, em quê e a que spread", em dois níveis:

    1. RESUMO GERAL   IPCA + Incentivadas e CDI + Tradicionais nas linhas,
                      média diária de volume em quatro janelas nas colunas.
    2. POR CLASSE     volume dia a dia (com drill-down mês/ano), papéis mais
                      negociados (volume ou giro) e spread × data.

O TICKER É A CHAVE (pedido explícito): emissor, setor, subsetor, grupo
econômico, indexador e Lei 12.431 vêm do cadastro de debêntures do Hub, não
das colunas do export. A ordem de procura é a mesma da aba Emissor:

    debentures                 papel que a Anbima precifica (classe pronta)
    debenture_caracteristicas  papel registrado que a Anbima não precifica
                               (classe derivada da remuneração da emissão)
    colunas do próprio export  só nome/setor, para não mostrar ticker cru;
                               sem cadastro o papel fica fora das classes

Resolvido na CONSULTA, não na importação: corrigir a taxonomia de um ticker
corrige o histórico inteiro sem reimportar nada.

"MÉDIA DIÁRIA" usa como denominador os PREGÕES DA BASE na janela (datas em
que o export trouxe negócio de qualquer papel), não dias de calendário. Um
dia em que uma classe não negociou nada conta como zero para ela — é o que
"média diária da classe" quer dizer.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta

from sqlalchemy import case, delete, func, insert, select, tuple_
from sqlalchemy.orm import Session

from ..models import Debenture, DebentureCaracteristica, DebentureSpread, MercadoSecundario
from .queries import _classe_por_caracteristica

logger = logging.getLogger(__name__)

CLASSES = ("IPCA + Incentivadas", "CDI + Tradicionais")
SEM_CADASTRO = "Sem cadastro"
_LOTE_IN = 800  # SQLite aceita 999 parâmetros por consulta

# ---------------------------------------------------------------------------
# Leitura do export
# ---------------------------------------------------------------------------
# Cabeçalho normalizado (minúsculo, sem acento, espaços simples) -> campo.
COLUNAS = {
    "ticker": "codigo",
    "data de ref. do negocio": "data",
    "volume": "volume",
    "spread (bps)": "spread",
    "taxa": "taxa",
    "indicativo anbima": "taxa_anbima",
    "taxa ntn-b/di": "taxa_referencia",
    "duration": "duration",
    "vencimento": "vencimento",
    "index.": "indexador_fonte",
    "devedor": "devedor_fonte",
    "setor": "setor_fonte",
    "subsetor": "subsetor_fonte",
}
OBRIGATORIAS = ("codigo", "data", "volume")
_NUMERICOS = ("volume", "spread", "taxa", "taxa_anbima", "taxa_referencia", "duration")
_DATAS = ("data", "vencimento")
_TEXTOS = {"indexador_fonte": 30, "devedor_fonte": 300, "setor_fonte": 120, "subsetor_fonte": 120}


def _norm(txt) -> str:
    s = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def mapear_cabecalho(cabecalho: list) -> dict[int, str]:
    """Posição da coluna -> campo. Levanta ValueError se faltar coluna
    obrigatória, com o nome que o Allan vê no arquivo."""
    mapa = {}
    for i, nome in enumerate(cabecalho):
        campo = COLUNAS.get(_norm(nome))
        if campo and campo not in mapa.values():
            mapa[i] = campo
    faltando = [c for c in OBRIGATORIAS if c not in mapa.values()]
    if faltando:
        nomes = {v: k for k, v in COLUNAS.items()}
        raise ValueError("coluna(s) não encontrada(s) no cabeçalho: "
                         + ", ".join(f'"{nomes[c]}"' for c in faltando))
    return mapa


def achar_cabecalho(linhas: list[list]) -> int:
    """Índice da linha de cabeçalho: o export do Excel abre com título e um
    aviso antes da tabela, então não dá para assumir a primeira linha."""
    for i, linha in enumerate(linhas[:30]):
        nomes = {_norm(c) for c in linha if c is not None}
        if "ticker" in nomes and "volume" in nomes:
            return i
    raise ValueError('cabeçalho não encontrado (procurei uma linha com "ticker" e "volume")')


def _numero(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("R$", "").replace(" ", "")
    if not s or s in ("-", "—"):
        return None
    if "," in s:  # padrão brasileiro: 1.234,56
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _data(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)):
        n = int(v)
        if 19000101 <= n <= 21001231:          # 20261021
            return _data(str(n))
        if 20000 <= n <= 80000:                # número de série do Excel
            return date(1899, 12, 30) + timedelta(days=n)
        return None
    s = str(v).strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%d/%m/%Y", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(s[:n], fmt).date()
        except ValueError:
            continue
    return None


def converter_linhas(cabecalho: list, linhas: list[list]) -> tuple[list[dict], int]:
    """Linhas cruas -> dicionários prontos para gravar. Devolve também
    quantas foram descartadas (sem ticker, sem data ou sem volume)."""
    mapa = mapear_cabecalho(cabecalho)
    saida, descartadas = [], 0
    for linha in linhas:
        reg = {}
        for i, campo in mapa.items():
            v = linha[i] if i < len(linha) else None
            if campo in _NUMERICOS:
                reg[campo] = _numero(v)
            elif campo in _DATAS:
                reg[campo] = _data(v)
            elif campo == "codigo":
                reg[campo] = re.sub(r"[^A-Za-z0-9]", "", str(v or "")).upper() or None
            else:
                t = str(v).strip() if v is not None else ""
                reg[campo] = t[: _TEXTOS[campo]] or None
        if not reg.get("codigo") or reg.get("data") is None or reg.get("volume") is None:
            descartadas += 1
            continue
        saida.append(reg)
    return saida, descartadas


def ler_arquivo(caminho: str) -> tuple[list, list[list], bool]:
    """(cabeçalho, linhas, truncado) de um .xlsx ou .csv. `truncado` = o
    Excel avisou que não exportou tudo (ver `descartar_dia_incompleto`)."""
    if caminho.lower().endswith(".csv"):
        with open(caminho, encoding="utf-8-sig", newline="") as f:
            texto = f.read()
        dialeto = csv.Sniffer().sniff(texto[:5000], delimiters=";,\t")
        todas = [list(r) for r in csv.reader(io.StringIO(texto), dialeto)]
    else:
        import openpyxl
        wb = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
        todas = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    i = achar_cabecalho(todas)
    truncado = any("not all data was exported" in _norm(c) for r in todas[:i] for c in r if c)
    return todas[i], todas[i + 1:], truncado


def descartar_dia_incompleto(registros: list[dict]) -> tuple[list[dict], date | None]:
    """O export para Excel corta em 50 mil linhas e avisa no topo. Como ele
    vem do dia mais recente para o mais antigo, o dia mais ANTIGO do arquivo
    sai pela metade — gravá-lo faria aquele dia parecer um pregão fraco.
    Descarta esse dia inteiro e devolve qual foi."""
    if not registros:
        return registros, None
    mais_antigo = min(r["data"] for r in registros)
    return [r for r in registros if r["data"] != mais_antigo], mais_antigo


def importar(db: Session, registros: list[dict]) -> dict:
    """Grava/atualiza por ticker+data (reimportar o mesmo dia substitui,
    não duplica). Não faz commit — quem chama decide."""
    unicos = {(r["codigo"], r["data"]): r for r in registros}  # repetido no lote: fica o último
    pares = list(unicos)
    for k in range(0, len(pares), 400):
        lote = pares[k:k + 400]
        db.execute(delete(MercadoSecundario).where(
            tuple_(MercadoSecundario.codigo, MercadoSecundario.data).in_(lote)))
    valores = list(unicos.values())
    for k in range(0, len(valores), 2000):
        db.execute(insert(MercadoSecundario), valores[k:k + 2000])
    datas = [r["data"] for r in valores]
    return {
        "gravadas": len(valores),
        "datas": len(set(datas)),
        "de": min(datas).isoformat() if datas else None,
        "ate": max(datas).isoformat() if datas else None,
    }


# ---------------------------------------------------------------------------
# Cadastro por ticker
# ---------------------------------------------------------------------------
def _em_lotes(db: Session, modelo, coluna, valores):
    valores = list(valores)
    for k in range(0, len(valores), _LOTE_IN):
        yield from db.scalars(select(modelo).where(coluna.in_(valores[k:k + _LOTE_IN])))


def mapa_tickers(db: Session) -> dict[str, dict]:
    """ticker -> classe, emissor, setor, subsetor, grupo, de onde veio."""
    fonte = {
        r.codigo: r for r in db.execute(
            select(MercadoSecundario.codigo,
                   func.max(MercadoSecundario.devedor_fonte).label("devedor"),
                   func.max(MercadoSecundario.setor_fonte).label("setor"),
                   func.max(MercadoSecundario.subsetor_fonte).label("subsetor"))
            .group_by(MercadoSecundario.codigo))
    }
    debs = {d.codigo: d for d in _em_lotes(db, Debenture, Debenture.codigo, fonte)}
    faltam = [c for c in fonte if c not in debs]
    cars = {c.codigo: c for c in _em_lotes(db, DebentureCaracteristica,
                                             DebentureCaracteristica.codigo, faltam)}
    mapa = {}
    for codigo, f in fonte.items():
        d, c = debs.get(codigo), cars.get(codigo)
        if d is not None:
            classe, origem = d.classe or "Outros", "anbima"
            emissor = d.nome
        elif c is not None:
            classe, origem = _classe_por_caracteristica(c) or "Outros", "caracteristicas"
            emissor = c.empresa
        else:
            classe, origem, emissor = SEM_CADASTRO, "export", None
        emissor = emissor or (f.devedor.title() if f.devedor else codigo)
        mapa[codigo] = {
            "classe": classe,
            "origem": origem,
            "emissor": emissor,
            "setor": (d.setor if d is not None and d.setor else (f.setor.title() if f.setor else None)),
            "subsetor": (d.subsetor if d is not None and d.subsetor
                         else (f.subsetor.title() if f.subsetor else None)),
            "grupo": (d.grupo_economico if d is not None and d.grupo_economico else emissor),
        }
    return mapa


def _codigos(mapa: dict, classe: str) -> list[str]:
    return [c for c, m in mapa.items() if m["classe"] == classe]


# ---------------------------------------------------------------------------
# Janelas
# ---------------------------------------------------------------------------
def _menos_um_mes(d: date) -> date:
    ano, mes = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    dia = min(d.day, [31, 29 if ano % 4 == 0 and (ano % 100 or ano % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1])
    return date(ano, mes, dia)


def _datas_base(db: Session) -> list[date]:
    return list(db.scalars(select(MercadoSecundario.data).distinct().order_by(MercadoSecundario.data)))


def _pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes = (h + l_ - 7 * m + 114) // 31
    dia = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados(ano: int) -> set[date]:
    """Feriados nacionais do calendário de dias úteis da ANBIMA (o mesmo do
    mercado de renda fixa): fixos + Carnaval, Sexta-feira Santa e Corpus
    Christi. Consciência Negra virou nacional em 2024."""
    p = _pascoa(ano)
    fixos = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)]
    if ano >= 2024:
        fixos.append((11, 20))
    return ({date(ano, m, d) for m, d in fixos}
            | {p - timedelta(days=48), p - timedelta(days=47), p - timedelta(days=2), p + timedelta(days=60)})


def dias_uteis(inicio: date, fim: date) -> list[date]:
    out, d, cache = [], inicio, {}
    while d <= fim:
        if d.weekday() < 5:
            if d.year not in cache:
                cache[d.year] = feriados(d.year)
            if d not in cache[d.year]:
                out.append(d)
        d += timedelta(days=1)
    return out


def _janelas(ref: date) -> dict[str, dict]:
    """Início/fim de cada janela, ancoradas na data mais recente da base.
    "Últimos 5 dias" são os 5 últimos DIAS ÚTEIS, não as 5 últimas datas da
    base (se a base pular um dia útil, esse dia entra como zero)."""
    ultimos5 = dias_uteis(ref - timedelta(days=15), ref)[-5:]
    return {
        "5d": {"rotulo": "Últimos 5 dias", "inicio": ultimos5[0], "fim": ref},
        "mes": {"rotulo": "Último mês", "inicio": _menos_um_mes(ref) + timedelta(days=1), "fim": ref},
        "ano": {"rotulo": f"{ref.year}", "inicio": date(ref.year, 1, 1), "fim": ref},
        "ano_anterior": {"rotulo": f"{ref.year - 1}", "inicio": date(ref.year - 1, 1, 1),
                         "fim": date(ref.year - 1, 12, 31)},
    }


def _vol_com_spread():
    """Volume só das linhas com spread: é o denominador da média ponderada
    (linha sem spread entra no volume, não no spread)."""
    ms = MercadoSecundario
    return case((ms.spread.isnot(None), func.coalesce(ms.volume, 0)), else_=0)


def _por_dia(db: Session, codigos: list[str] | None, inicio: date | None = None,
             fim: date | None = None) -> dict[date, dict]:
    """volume, nº de papéis e spread ponderado por volume, por data."""
    ms = MercadoSecundario
    peso = func.sum(func.coalesce(ms.volume, 0) * ms.spread)
    peso_vol = func.sum(_vol_com_spread())

    def consulta(lote):
        q = select(ms.data, func.sum(ms.volume), func.count(ms.codigo), peso, peso_vol).group_by(ms.data)
        if lote is not None:
            q = q.where(ms.codigo.in_(lote))
        if inicio:
            q = q.where(ms.data >= inicio)
        if fim:
            q = q.where(ms.data <= fim)
        return db.execute(q)

    lotes = [None] if codigos is None else [codigos[k:k + _LOTE_IN] for k in range(0, len(codigos), _LOTE_IN)]
    acc: dict[date, dict] = {}
    for lote in lotes:
        if lote == []:
            continue
        for data, vol, n, sv, vs in consulta(lote):
            a = acc.setdefault(data, {"volume": 0.0, "papeis": 0, "sv": 0.0, "vs": 0.0})
            a["volume"] += vol or 0
            a["papeis"] += n or 0
            a["sv"] += sv or 0
            a["vs"] += float(vs or 0)
    return acc


# ---------------------------------------------------------------------------
# Bloco 1 — resumo geral
# ---------------------------------------------------------------------------
def resumo(db: Session) -> dict:
    """MÉDIA DIÁRIA = volume da janela ÷ DIAS ÚTEIS da janela (calendário
    ANBIMA), recortada ao período que a base cobre. Dia útil sem negócio --
    ou que faltou no export -- entra como zero; os que faltam no export
    vão listados em `dias_sem_dado` para a tela avisar."""
    datas = _datas_base(db)
    if not datas:
        return {"referencia": None, "linhas": [], "janelas": []}
    ref, inicio_base = datas[-1], datas[0]
    jan = _janelas(ref)
    mapa = mapa_tickers(db)
    total = _por_dia(db, None)
    na_base = set(datas)

    def uteis(j):
        ini, fim = max(j["inicio"], inicio_base), min(j["fim"], ref)
        return dias_uteis(ini, fim) if ini <= fim else []

    janelas_out = []
    for chave, j in jan.items():
        u = uteis(j)
        janelas_out.append({
            "chave": chave, "rotulo": j["rotulo"],
            "inicio": (u[0] if u else j["inicio"]).isoformat(), "fim": (u[-1] if u else j["fim"]).isoformat(),
            "dias_uteis": len(u),
            # A base começa depois do início da janela: a média existe, mas
            # é de um pedaço do período. A tela avisa em vez de esconder.
            "parcial": bool(u) and inicio_base > j["inicio"],
        })

    linhas = []
    volume_classes = 0.0
    for classe in CLASSES:
        dia = _por_dia(db, _codigos(mapa, classe))
        celulas = {}
        for chave, j in jan.items():
            u = uteis(j)
            if not u:
                celulas[chave] = None
                continue
            vol = sum(dia[d]["volume"] for d in u if d in dia)
            pap = sum(dia[d]["papeis"] for d in u if d in dia)
            celulas[chave] = {"media": vol / len(u), "papeis_dia": pap / len(u)}
        volume_classes += sum(v["volume"] for v in dia.values())
        linhas.append({"classe": classe, "janelas": celulas})

    volume_total = sum(v["volume"] for v in total.values())
    return {
        "referencia": ref.isoformat(),
        "inicio_base": inicio_base.isoformat(),
        "janelas": janelas_out,
        "linhas": linhas,
        "dias_sem_dado": [d.isoformat() for d in dias_uteis(inicio_base, ref) if d not in na_base],
        # Quanto do volume da base cai nas duas classes. O resto é prefixado,
        # IPCA não incentivada, CDI incentivada, % do DI e papel sem cadastro.
        "cobertura": (volume_classes / volume_total) if volume_total else None,
    }


# ---------------------------------------------------------------------------
# Bloco 2 — por classe
# ---------------------------------------------------------------------------
def serie(db: Session, classe: str) -> dict:
    """Volume dia a dia da classe (o drill-down mês/ano é feito na tela a
    partir daqui: ~250 pontos por ano, não vale uma consulta por nível)."""
    datas = _datas_base(db)
    dia = _por_dia(db, _codigos(mapa_tickers(db), classe))
    pontos = []
    for d in datas:  # dia sem negócio da classe aparece como zero
        a = dia.get(d)
        pontos.append({
            "data": d.isoformat(),
            "volume": a["volume"] if a else 0.0,
            "papeis": a["papeis"] if a else 0,
            "spread": (a["sv"] / a["vs"]) if a and a["vs"] else None,
        })
    return {"classe": classe, "pontos": pontos}


def _estoques(db: Session, codigos: list[str], ate: date) -> dict[str, tuple[float, date]]:
    """Estoque mais recente de cada papel até `ate` (R$ milhões): último
    boletim da Anbima com estoque; para quem a Anbima não precifica, o do
    arquivo de características."""
    ds = DebentureSpread
    out: dict[str, tuple[float, date]] = {}
    for k in range(0, len(codigos), _LOTE_IN):
        lote = codigos[k:k + _LOTE_IN]
        ult = (select(ds.codigo, func.max(ds.data).label("d"))
               .where(ds.codigo.in_(lote), ds.estoque.isnot(None), ds.data <= ate)
               .group_by(ds.codigo).subquery())
        for codigo, est, d in db.execute(
                select(ds.codigo, ds.estoque, ds.data)
                .join(ult, (ds.codigo == ult.c.codigo) & (ds.data == ult.c.d))):
            out[codigo] = (est, d)
    faltam = [c for c in codigos if c not in out]
    for c in _em_lotes(db, DebentureCaracteristica, DebentureCaracteristica.codigo, faltam):
        if c.estoque:
            out[c.codigo] = (c.estoque, c.data_estoque)
    return out


def ranking(db: Session, classe: str, janela: str = "semana", metrica: str = "volume",
            limite: int = 15) -> dict:
    """Papéis mais negociados na janela, por volume ou por giro.

    GIRO = volume da janela ÷ estoque do papel no FIM da janela (último
    estoque publicado até lá). Numa janela de uma semana ou um mês o estoque
    praticamente não se move — a diferença para uma média do estoque no
    período só aparece em papel que amortizou ou foi emitido no meio dela,
    e aí o estoque do fim é o que de fato está em mercado.
    """
    datas = _datas_base(db)
    if not datas:
        return {"linhas": [], "inicio": None, "fim": None, "pregoes": 0}
    fim = datas[-1]
    jan = _janelas(fim)["5d" if janela == "semana" else "mes"]
    inicio = max(jan["inicio"], datas[0])
    pregoes = dias_uteis(inicio, fim)
    mapa = mapa_tickers(db)
    codigos = _codigos(mapa, classe)
    ms = MercadoSecundario
    agreg = {}
    for k in range(0, len(codigos), _LOTE_IN):
        for codigo, vol, sv, vs, dias in db.execute(
            select(ms.codigo, func.sum(ms.volume),
                   func.sum(func.coalesce(ms.volume, 0) * ms.spread),
                   func.sum(_vol_com_spread()),
                   func.count(ms.data))
            .where(ms.codigo.in_(codigos[k:k + _LOTE_IN]), ms.data >= inicio, ms.data <= fim)
            .group_by(ms.codigo)):
            agreg[codigo] = {"volume": vol or 0.0, "spread": (sv / float(vs)) if vs else None, "dias": dias}
    estoques = _estoques(db, list(agreg), fim)
    volume_classe = sum(a["volume"] for a in agreg.values())
    linhas = []
    for codigo, a in agreg.items():
        est = estoques.get(codigo)
        giro = (a["volume"] / (est[0] * 1e6)) if est and est[0] else None
        m = mapa[codigo]
        linhas.append({
            "codigo": codigo, "emissor": m["emissor"], "setor": m["setor"], "grupo": m["grupo"],
            "volume": a["volume"],
            "dias": a["dias"], "spread": a["spread"],
            "estoque": est[0] if est else None,
            "data_estoque": est[1].isoformat() if est and est[1] else None,
            "giro": giro,
        })
    if metrica == "giro":
        linhas = [linha for linha in linhas if linha["giro"] is not None]
        linhas.sort(key=lambda r: r["giro"], reverse=True)
    else:
        linhas.sort(key=lambda r: r["volume"], reverse=True)
    return {
        "classe": classe, "janela": janela, "metrica": metrica,
        "inicio": pregoes[0].isoformat() if pregoes else inicio.isoformat(),
        "fim": fim.isoformat(), "dias_uteis": len(pregoes),
        "papeis_negociados": len(agreg), "volume_classe": volume_classe,
        "linhas": linhas[:limite],
    }


def _inicio_periodo(datas: list[date], meses: int | None) -> date:
    if not meses:
        return datas[0]
    inicio = datas[-1]
    for _ in range(meses):
        inicio = _menos_um_mes(inicio)
    return max(inicio + timedelta(days=1), datas[0])


def emissores_negociados(db: Session, classe: str, meses: int | None = 3) -> dict:
    """Lista da lateral do gráfico spread × data: emissores da classe que
    negociaram no período, cada um com os seus tickers e o volume deles.
    `padrao` = o ticker mais negociado do emissor de maior volume -- é o que
    a tela abre marcado."""
    datas = _datas_base(db)
    if not datas:
        return {"emissores": [], "padrao": None}
    inicio = _inicio_periodo(datas, meses)
    mapa = mapa_tickers(db)
    codigos = _codigos(mapa, classe)
    ms = MercadoSecundario
    por_emissor: dict[str, dict] = {}
    for k in range(0, len(codigos), _LOTE_IN):
        for codigo, vol, dias in db.execute(
            select(ms.codigo, func.sum(ms.volume), func.count(ms.data))
            .where(ms.codigo.in_(codigos[k:k + _LOTE_IN]), ms.data >= inicio, ms.spread.isnot(None))
            .group_by(ms.codigo)):
            m = mapa[codigo]
            e = por_emissor.setdefault(m["emissor"], {"emissor": m["emissor"], "setor": m["setor"],
                                                       "grupo": m["grupo"], "volume": 0.0, "tickers": []})
            e["volume"] += vol or 0
            e["tickers"].append({"codigo": codigo, "volume": vol or 0.0, "dias": dias})
    emissores = sorted(por_emissor.values(), key=lambda e: e["emissor"].lower())
    for e in emissores:
        e["tickers"].sort(key=lambda t: -t["volume"])
    maior = max(emissores, key=lambda e: e["volume"], default=None)
    return {
        "classe": classe, "inicio": inicio.isoformat(), "fim": datas[-1].isoformat(),
        "emissores": emissores,
        "padrao": {"emissor": maior["emissor"], "codigo": maior["tickers"][0]["codigo"]} if maior else None,
    }


def pontos_spread(db: Session, classe: str, meses: int | None, codigos: list[str]) -> dict:
    """Série de cada ticker escolhido: um ponto por dia em que negociou
    (spread e volume). O eixo de datas é o de toda a base no período, para
    as barras de volume caírem no dia certo mesmo sem negócio."""
    datas = _datas_base(db)
    if not datas:
        return {"datas": [], "series": {}}
    inicio = _inicio_periodo(datas, meses)
    mapa = mapa_tickers(db)
    validos = [c for c in dict.fromkeys(codigos) if mapa.get(c, {}).get("classe") == classe][:60]
    janela = [d for d in datas if d >= inicio]
    idx = {d: i for i, d in enumerate(janela)}
    ms = MercadoSecundario
    series = {c: {"codigo": c, "emissor": mapa[c]["emissor"], "pontos": []} for c in validos}
    if validos:
        for data, codigo, spread, volume in db.execute(
            select(ms.data, ms.codigo, ms.spread, ms.volume)
            .where(ms.codigo.in_(validos), ms.data >= inicio).order_by(ms.data)):
            series[codigo]["pontos"].append(
                [idx[data], round(spread, 1) if spread is not None else None, round((volume or 0) / 1e6, 3)])
    return {"classe": classe, "datas": [d.isoformat() for d in janela], "series": list(series.values())}


def status_importacao(db: Session) -> dict:
    ms = MercadoSecundario
    linha = db.execute(select(func.count(ms.id), func.min(ms.data), func.max(ms.data),
                              func.count(func.distinct(ms.data)), func.max(ms.importado_em))).one()
    n, de, ate, dias, quando = linha
    return {"linhas": n, "de": de.isoformat() if de else None, "ate": ate.isoformat() if ate else None,
            "pregoes": dias, "importado_em": quando.isoformat() if quando else None}
