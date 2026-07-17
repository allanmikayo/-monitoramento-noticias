"""CVM RAD (rad.cvm.gov.br) — Fatos Relevantes e Comunicados ao Mercado.

A tabela de resultados (`table#grdDocumentos`, um DataTables jQuery) só é
montada via JavaScript -- precisa de Playwright, igual S&P/Moody's. Allan
mapeou a estrutura real da tabela ao vivo (16/07/2026): cada documento é
uma linha com 11 colunas (Código CVM, Empresa, Categoria, Tipo, Espécie,
Data Referência, Data Entrega, Status, V, Modalidade, Ações) e, quando o
documento tem um "Assunto" cadastrado, uma segunda linha de continuação
só com `<td colspan="9" class="celulaAssunto">`.

Diferença importante em relação às outras fontes: a tabela do RAD é um
feed de TODOS os arquivamentos do mercado (não só das empresas cobertas
por Allan) e tem volume alto -- paginar tudo seria inviável a cada 5min.
Por isso, ao contrário de spglobal/moodys_local/fitch (que trazem tudo e
deixam o pipeline decidir relevância), este scraper filtra por empresa da
cobertura AQUI DENTRO, por instrução explícita do Allan ("Para CVM pegue
apenas as empresas da cobertura"). A paginação para cedo quando a "Data
Entrega" da página cai fora da janela de interesse (a tabela já vem
ordenada por Data Entrega decrescente por padrão).
"""
from __future__ import annotations

import html as _html
import logging
import re
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta

from .base import BRT, RawArticle, brt_to_utc, dump_debug_html

logger = logging.getLogger(__name__)

BASE_URL = "https://www.rad.cvm.gov.br/ENETWeb/"
GENERIC_URL = "https://www.rad.cvm.gov.br/ENETWeb/frmConsultaExternaCVM.aspx"
LOOKBACK_DAYS = 10  # janela do dashboard e' "5 dias" -- usamos folga, igual ao "7 dias" do S&P
MAX_PAGES = 20  # trava de seguranca (mercado inteiro tem volume alto; nao pode paginar sem limite)

_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}))?")


def _carregar_nomes_cobertura() -> list[str]:
    """Nomes + apelidos das empresas ativas cadastradas -- usados só para
    filtrar linhas do RAD, sem misturar termos de setor (Allan pediu
    explicitamente 'apenas as empresas da cobertura' para esta fonte)."""
    try:
        from ..db import SessionLocal
        from ..models import Company, CompanyAlias

        with SessionLocal() as db:
            nomes: list[str] = []
            for c in db.query(Company).filter(Company.active.is_(True)).all():
                nomes.append(c.name)
            for a in db.query(CompanyAlias).all():
                if a.alias:
                    nomes.append(a.alias)
            return nomes
    except Exception as e:  # noqa: BLE001
        logger.warning("cvm_rad: falha carregando empresas da cobertura: %s", e)
        return []


def _selecionar_periodo_mes(page) -> None:
    """Por padrão o RAD só mostra os documentos DO DIA -- Allan pediu
    (17/07/2026) pra sempre buscar uma janela maior antes de paginar.

    O site tem 2 opções de período além do padrão: "Semana"
    (`#rdSemana`, value=1, sem mais nada pra preencher) e "Período"
    (`#rdPeriodo`, value=2, com `#txtDataIni`/`#txtDataFim` pra escolher o
    intervalo exato). Uso "Período" com início = hoje menos 1 mês e fim =
    hoje (instrução exata do Allan) em vez de "Semana", porque um mês é um
    superconjunto de uma semana e dá mais folga -- o nosso próprio corte
    por `LOOKBACK_DAYS` já filtra o resultado final pro tamanho que
    realmente importa, então não tem desvantagem em pedir um intervalo
    maior ao site.

    As datas são calculadas em horário de Brasília (`base.BRT`), já que
    "hoje" pro Allan é sempre o dia de Brasília, não o UTC do servidor."""
    hoje = datetime.now(BRT)
    inicio_str = (hoje - relativedelta(months=1)).strftime("%d/%m/%Y")
    fim_str = hoje.strftime("%d/%m/%Y")
    try:
        # BUG CORRIGIDO (17/07/2026): found=4 (so' hoje) + error=null nos
        # logs do GitHub Actions indicava que isto falhava CALADO -- sem
        # lancar excecao (senao apareceria o warning abaixo nos logs), mas
        # tambem sem aplicar o filtro de verdade. Suspeita: `Escape` so
        # fecha o popup do datepicker (jQuery UI) sem "commitar" o valor
        # (sem disparar change/blur), entao o site mantinha o filtro padrao
        # mesmo com o campo aparentando preenchido. Isso e' plausivel ser
        # so' no Ubuntu headless do Actions e nao ter aparecido nos testes
        # locais do Allan no Windows (timing diferente). Troquei por `Tab`
        # (commita o campo de verdade) e adicionei checagens que jogam uma
        # excecao explicita (cai no except abaixo, com log detalhado) se o
        # radio ou os campos nao ficarem com o valor esperado -- assim, se
        # continuar falhando, o log do proximo run vai dizer EXATAMENTE em
        # qual passo travou, em vez de falhar mudo de novo.
        page.wait_for_selector("#rdPeriodo", state="visible", timeout=10000)
        page.check("#rdPeriodo")
        page.wait_for_timeout(500)
        if not page.is_checked("#rdPeriodo"):
            raise RuntimeError("radio #rdPeriodo nao ficou marcado depois do check()")

        for campo, valor in (("#txtDataIni", inicio_str), ("#txtDataFim", fim_str)):
            page.click(campo)
            page.fill(campo, "")
            page.fill(campo, valor)
            page.keyboard.press("Tab")  # commita o valor (dispara change/blur) -- Escape so fecha o popup
            page.wait_for_timeout(400)
            valor_atual = page.input_value(campo)
            if valor not in valor_atual:
                raise RuntimeError(f"campo {campo} ficou com {valor_atual!r}, esperado {valor!r}")

        page.wait_for_timeout(300)
        page.click("#btnConsulta")
        page.wait_for_timeout(3000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001
            pass  # nao critico -- so' uma folga extra pra tabela terminar de recarregar
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "cvm_rad: falha ao selecionar periodo de 1 mes (%s) -- segue com o "
            "padrao do site (so' documentos de hoje)", e,
        )


def _maximizar_itens_por_pagina(page) -> None:
    for sel in ["#grdDocumentos_length select", "select[name='grdDocumentos_length']"]:
        el = page.query_selector(sel)
        if el:
            for v in ["100", "-1", "50"]:
                try:
                    el.select_option(value=v)
                    page.wait_for_timeout(1500)
                    return
                except Exception:
                    pass


def _clicar_proxima_pagina(page) -> bool:
    for sel in [
        "#grdDocumentos_next:not(.disabled) a",
        "#grdDocumentos_next a",
        "a.paginate_button.next:not(.disabled)",
        "a:has-text('Seguinte'):not(.disabled)",
    ]:
        try:
            el = page.query_selector(sel)
            if not el:
                continue
            container = page.query_selector("#grdDocumentos_next")
            classe = (container.get_attribute("class") if container else "") or ""
            if "disabled" in classe:
                return False
            el.click()
            page.wait_for_timeout(1800)
            return True
        except Exception:
            continue
    return False


def _extrair_url_documento(cel_acoes) -> tuple[str, str]:
    """Retorna (url_clicavel, identificador_estavel).

    A célula de ações tem VÁRIOS ícones (baixar, visualizar etc.), cada um
    com seu próprio onclick -- o ícone "Visualizar o Documento"
    (`fi-page-search`, `id="VisualizarDocumento"`) é o que Allan confirmou
    funcionar de verdade ao vivo: `OpenPopUpVer('frmExibirArquivoIPEExterno.
    aspx?NumeroProtocoloEntrega=...')`. A versão anterior deste código só
    olhava o PRIMEIRO onclick que encontrasse na célula -- se esse fosse o
    ícone de download (`OpenDownloadDocumentos`, formato/URL diferente e
    não confirmado), o link gerado não abria. Agora varremos todos os
    onclick da célula e priorizamos OpenPopUpVer sempre que existir."""
    onclicks = [tag.get("onclick") for tag in cel_acoes.find_all(True) if tag.get("onclick")]
    onclicks = [_html.unescape(oc) for oc in onclicks]

    for oc in onclicks:
        m = re.search(r"OpenPopUpVer\('([^']+)'\)", oc)
        if m:
            rel = m.group(1)
            return (BASE_URL + rel.lstrip("/"), rel)

    for oc in onclicks:
        m = re.search(
            r"OpenDownloadDocumentos\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*\)",
            oc,
        )
        if m:
            cod_cvm, versao, protocolo, tipo = m.groups()
            url = f"{BASE_URL}frmExibirArquivoIPEExterno.aspx?NumeroProtocoloEntrega={protocolo}"
            return (url, f"{cod_cvm}-{versao}-{protocolo}-{tipo}")

    digits = re.findall(r"\d+", " ".join(onclicks))
    return (
        GENERIC_URL,
        "-".join(digits) or (onclicks[0][:40] if onclicks else "sem-acao"),
    )


def _parse_data(texto: str) -> datetime | None:
    """A "Data Entrega"/"Data Referência" do RAD vem em horário de Brasília
    (site do regulador brasileiro) -- usa `brt_to_utc` pra converter de
    verdade, em vez de só rotular como UTC (bug corrigido em 17/07/2026,
    ver `base.brt_to_utc`)."""
    m = _DATE_RE.search(texto or "")
    if not m:
        return None
    data_str, hora_str = m.groups()
    try:
        dia, mes, ano = (int(x) for x in data_str.split("/"))
        if hora_str:
            hh, mm = (int(x) for x in hora_str.split(":"))
            return brt_to_utc(ano, mes, dia, hh, mm)
        return brt_to_utc(ano, mes, dia)
    except ValueError:
        return None


def _cell_text(td) -> str:
    return td.get_text(" ", strip=True)


def _parse_tabela(soup: BeautifulSoup) -> list[RawArticle]:
    table = soup.select_one("table#grdDocumentos")
    if not table:
        return []
    tbody = table.find("tbody") or table
    rows = tbody.find_all("tr", recursive=False) or tbody.find_all("tr")

    out: list[RawArticle] = []
    last_article: RawArticle | None = None

    for tr in rows:
        tds = tr.find_all("td", recursive=False) or tr.find_all("td")
        if not tds:
            continue

        primeira_classe = (tds[0].get("class") or [""])[0].lower()
        if len(tds) == 1 and "celulaassunto" in primeira_classe:
            if last_article is not None:
                extra = _cell_text(tds[0])
                if extra and extra not in last_article.snippet:
                    last_article.snippet = (last_article.snippet + " | " + extra)[:500] if last_article.snippet else extra[:500]
            continue

        if len(tds) < 10:
            continue

        codigo_cvm = _cell_text(tds[0])
        empresa = _cell_text(tds[1])
        categoria = _cell_text(tds[2])
        tipo = _cell_text(tds[3])
        especie = _cell_text(tds[4])
        data_ref_txt = _cell_text(tds[5])
        data_entrega_txt = _cell_text(tds[6])
        modalidade = _cell_text(tds[9]) if len(tds) > 9 else ""
        cel_acoes = tds[10] if len(tds) > 10 else None

        if not empresa:
            continue

        published = _parse_data(data_entrega_txt) or _parse_data(data_ref_txt)

        if cel_acoes is not None:
            url, ident = _extrair_url_documento(cel_acoes)
        else:
            url, ident = (GENERIC_URL, f"{codigo_cvm}-{data_entrega_txt}")

        # So' anexa um fragmento de dedupe quando NAO conseguimos um link
        # real e especifico do documento (caiu no GENERIC_URL) -- varios
        # documentos diferentes cairiam na mesma URL genérica sem isso. Um
        # link real (com NumeroProtocoloEntrega) já é único por natureza; a
        # versão anterior sempre grudava o fragmento, o que corrompia o
        # link real que o Allan confirmou funcionar (link virava algo como
        # ".../NumeroProtocoloEntrega=1545488#02715-4-...-NumeroProtocolo
        # Entrega=1545488" e dava erro ao abrir).
        if url == GENERIC_URL:
            date_key = published.strftime("%Y%m%d%H%M") if published else "nodate"
            synthetic_url = f"{url}#{codigo_cvm}-{date_key}-{ident}"[:800]
        else:
            synthetic_url = url

        titulo_bits = [b for b in [empresa, categoria, tipo if tipo not in ("-", "--", "") else ""] if b]
        titulo = " — ".join(titulo_bits)
        snippet = " | ".join(b for b in [especie if especie not in ("-", "--", "") else "", modalidade] if b)

        art = RawArticle(
            url=synthetic_url,
            title=titulo,
            snippet=snippet[:500],
            published_at=published,
            article_type="fato_relevante",
        )
        art._empresa_raw = empresa  # type: ignore[attr-defined]
        out.append(art)
        last_article = art

    return out


def fetch(url: str) -> list[RawArticle]:
    from playwright.sync_api import sync_playwright

    from ..filter import match_keywords
    from .base import USER_AGENT

    nomes_cobertura = _carregar_nomes_cobertura()
    if not nomes_cobertura:
        logger.warning("cvm_rad: lista de empresas da cobertura vazia -- nada sera' filtrado/gravado")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    coletados: list[RawArticle] = []
    last_html = ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900}, locale="pt-BR")
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            _selecionar_periodo_mes(page)
            _maximizar_itens_por_pagina(page)

            # Diagnostico (17/07/2026): loga a data mais antiga vista na
            # primeira pagina logo apos tentar aplicar o filtro de 1 mes --
            # se aparecer so' a data de hoje aqui, confirma que o filtro nao
            # pegou (mesmo sem excecao), em vez de descobrir isso so' pelo
            # "found" baixo no resumo final.
            try:
                _diag_soup = BeautifulSoup(page.content(), "lxml")
                _diag_linhas = _parse_tabela(_diag_soup)
                _diag_datas = [a.published_at for a in _diag_linhas if a.published_at]
                if _diag_datas:
                    logger.info(
                        "cvm_rad: apos filtro de periodo, pagina 1 tem %d linha(s), "
                        "data mais antiga = %s, mais recente = %s",
                        len(_diag_linhas), min(_diag_datas).date(), max(_diag_datas).date(),
                    )
            except Exception:  # noqa: BLE001
                pass  # so' diagnostico -- nunca deve derrubar a coleta de verdade

            for page_num in range(1, MAX_PAGES + 1):
                html = page.content()
                last_html = html
                soup = BeautifulSoup(html, "lxml")
                linhas = _parse_tabela(soup)
                if not linhas and page_num == 1:
                    break

                oldest_on_page = None
                for art in linhas:
                    if art.published_at and (oldest_on_page is None or art.published_at < oldest_on_page):
                        oldest_on_page = art.published_at
                    coletados.append(art)

                if oldest_on_page and oldest_on_page < cutoff:
                    break
                if not _clicar_proxima_pagina(page):
                    break
        finally:
            browser.close()

    if not coletados:
        dump_debug_html("cvm_rad", last_html)
        return []

    relevantes: list[RawArticle] = []
    for art in coletados:
        if art.published_at and art.published_at < cutoff:
            continue
        empresa_raw = getattr(art, "_empresa_raw", art.title)
        if match_keywords(empresa_raw, nomes_cobertura):
            relevantes.append(art)

    return relevantes
