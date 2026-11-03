"""Vórtx — documentos de agente fiduciário (DEB, CRI, CRA). Reescrito em
03/11/2026 para a API pública do site (`apis.vortx.com.br/vxsite/api/`),
sem navegador. A versão anterior abria cada operação no Playwright e levava
~27 min para voltar zero.

Fluxo (mapeado ao vivo no Chrome do Allan, ver CLAUDE.md):
  1. `operacoes?busca=NOME&pagina=N` para cada empresa da cobertura (10 por
     página, fixo). Fica a operação cujo código IF, CNPJ (debênture) ou
     apelido/emissor bate com a cobertura. Em CRI/CRA o CNPJ é o da
     securitizadora; o apelido e o código IF é que levam ao devedor.
  2. `operacao/assembleias/por-emissor?operationId=N`: assembleias da
     EMISSÃO (todas as séries), com data, tipo (Edital/Ata) e link do PDF.
     Uma chamada por emissão.
  3. Por operação: `fatos-relevantes` e `documentos-por-tipo/1` e `/2`
     (relatórios de covenants e garantias, avisos ao mercado, rating,
     notificações...), cada documento com data de envio.
Só entra o que tem data dentro de `LOOKBACK_DAYS`.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .base import RawArticle, brt_to_utc
from .fiduciarios import Cobertura, carregar_cobertura, corte, get_json, so_digitos, titulo

logger = logging.getLogger(__name__)

API = "https://apis.vortx.com.br/vxsite/api"
OPERACAO_URL = "https://www.vortx.com.br/investidor/dcm/operacao"
MAX_PAGINAS_BUSCA = 6       # 10 por página
TIPO_DEB = "3"
# Já vem pelo endpoint de assembleias (com data da assembleia, mais útil).
GRUPOS_IGNORADOS = {"assembleia investidores"}
TRABALHADORES = 8


def _data(valor: str | None, so_dia: bool = False) -> datetime | None:
    """ISO da API. Data sem hora (ou "T00:00:00.000Z" de assembleia) vira
    meio-dia de Brasília; data com hora sem fuso é horário de Brasília."""
    if not valor:
        return None
    try:
        s = str(valor).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(str(valor)[:10], "%Y-%m-%d")
            so_dia = True
        except ValueError:
            return None
    if so_dia or (dt.hour, dt.minute, dt.second) == (0, 0, 0):
        return brt_to_utc(dt.year, dt.month, dt.day)
    if dt.tzinfo is None:
        return brt_to_utc(dt.year, dt.month, dt.day, dt.hour, dt.minute)
    return dt.astimezone(timezone.utc)


def _buscar(nome: str) -> list[dict]:
    ops: list[dict] = []
    for pagina in range(1, MAX_PAGINAS_BUSCA + 1):
        try:
            d = get_json(f"{API}/operacoes", params={"busca": nome, "pagina": pagina})["data"]
        except Exception as e:  # noqa: BLE001
            logger.warning("vortx: busca %r pagina %d falhou: %s", nome, pagina, e)
            break
        lote = d.get("operations") or []
        ops.extend(lote)
        if len(lote) < 10 or pagina * 10 >= int(d.get("totalRecords") or 0):
            break
    return ops


def _empresa_da_operacao(op: dict, cob: Cobertura) -> str | None:
    return (
        cob.empresa_do_ticker(op.get("codIf"))
        or (cob.empresa_do_cnpj(op.get("cnpj_emissora")) if str(op.get("operation_type_id")) == TIPO_DEB else None)
        or cob.empresa_no_texto(f"{op.get('apelido') or ''} {op.get('emissor') or ''}")
    )


def _assembleias(op_id: str, empresa: str, limite: datetime) -> list[RawArticle]:
    d = get_json(f"{API}/operacao/assembleias/por-emissor", params={"operationId": op_id}).get("data") or []
    out = []
    for a in d:
        quando = _data(a.get("data_assembleia"), so_dia=True)
        if not a.get("url") or (quando and quando < limite):
            continue
        tipo = (a.get("tipo_documento") or "Documento").strip()
        out.append(RawArticle(
            url=a["url"], title=titulo(empresa, f"Assembleia ({tipo})", a.get("titulo") or ""),
            snippet="Vórtx — assembleia", published_at=quando, article_type="assembleia",
        ))
    return out


def _documentos_da_operacao(op: dict, empresa: str, limite: datetime) -> list[RawArticle]:
    op_id = op["codigo"]
    serie = " ".join(b for b in [op.get("codIf") or "", f"{op.get('emissao')}ª emissão" if op.get("emissao") else ""] if b)
    out: list[RawArticle] = []

    fatos = get_json(f"{API}/operacao/{op_id}/fatos-relevantes").get("relevantFacts") or []
    for f in fatos:
        quando = _data(f.get("createdDate") or f.get("referenceDate"))
        if quando and quando < limite:
            continue
        link = None
        for arq in (f.get("files") or []):
            if isinstance(arq, dict) and arq.get("url"):
                link = arq["url"]
                break
        doc = f.get("relevantFactsDocument")
        if not link and isinstance(doc, dict):
            link = doc.get("url")
        if not link and isinstance(doc, str) and doc.startswith("http"):
            link = doc
        link = link or f"{OPERACAO_URL}?id={op_id}#fato-{f.get('id')}"
        out.append(RawArticle(
            url=link, title=titulo(empresa, "Fato relevante", f"{f.get('title') or ''} ({serie})"),
            snippet=(f.get("description") or "")[:500], published_at=quando, article_type="assembleia",
        ))

    for tipo in (1, 2):
        grupos = get_json(f"{API}/operacao/{op_id}/documentos-por-tipo/{tipo}")
        grupos = grupos.get("data", grupos) if isinstance(grupos, dict) else grupos
        for g in grupos or []:
            nome_grupo = (g.get("type") or "Documento").strip()
            if nome_grupo.lower() in GRUPOS_IGNORADOS:
                continue
            for doc in g.get("documents") or []:
                quando = _data(doc.get("uploadTimestamp"))
                if not doc.get("url") or not quando or quando < limite:
                    continue
                out.append(RawArticle(
                    url=doc["url"], title=titulo(empresa, nome_grupo, f"{doc.get('description') or ''} ({serie})"),
                    snippet="Vórtx — documento da operação", published_at=quando, article_type="assembleia",
                ))
    return out


def fetch(url: str) -> list[RawArticle]:
    cob = carregar_cobertura()
    nomes = sorted(set(cob.nome_por_chave.values()))
    if not nomes:
        logger.warning("vortx: cobertura vazia -- nada será coletado")
        return []
    limite = corte()

    with ThreadPoolExecutor(TRABALHADORES) as ex:
        resultados = list(ex.map(_buscar, nomes))

    operacoes: dict[str, tuple[dict, str]] = {}
    for ops in resultados:
        for op in ops:
            empresa = _empresa_da_operacao(op, cob)
            if empresa and op.get("codigo"):
                operacoes[str(op["codigo"])] = (op, empresa)
    logger.info("vortx: %d operações da cobertura", len(operacoes))

    # Assembleias: uma chamada por emissão (todas as séries vêm juntas).
    emissoes: dict[tuple, tuple[str, str]] = {}
    for op_id, (op, empresa) in operacoes.items():
        chave = (so_digitos(op.get("cnpj_emissora")), str(op.get("emissao")), str(op.get("operation_type_id")))
        emissoes.setdefault(chave, (op_id, empresa))

    def _seguro(fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001
            logger.warning("vortx: %s%s falhou: %s", fn.__name__, args[:1], e)
            return []

    with ThreadPoolExecutor(TRABALHADORES) as ex:
        tarefas = [ex.submit(_seguro, _assembleias, op_id, emp, limite) for op_id, emp in emissoes.values()]
        tarefas += [ex.submit(_seguro, _documentos_da_operacao, op, emp, limite) for op, emp in operacoes.values()]
        artigos = [a for t in tarefas for a in t.result()]

    vistos: set[str] = set()
    out = []
    for a in artigos:
        if a.url not in vistos:
            vistos.add(a.url)
            out.append(a)
    return out
