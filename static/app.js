(function () {
  // Le a lista de empresas de um bloco <script type="application/json">, nao de um
  // atributo HTML -- atributos delimitados por aspas simples quebravam quando um
  // nome de empresa continha apostrofo (ex.: "Rede D'Or"), corrompendo o JSON e
  // travando esta IIFE inteira antes mesmo de registrar os cliques dos botoes.
  let companiesData = [];
  try {
    const raw = document.getElementById("companies-data").textContent || "[]";
    companiesData = JSON.parse(raw);
  } catch (e) {
    console.error("Falha ao carregar lista de empresas (companies-data):", e);
    companiesData = [];
  }
  // Só os botões de JANELA -- a página tem outras abas (.win-btn) nas colunas.
  const winButtons = document.querySelectorAll("#window-tabs .win-btn");
  const statusEl = document.getElementById("status-text");
  const countdownEl = document.getElementById("countdown");
  const refreshBtn = document.getElementById("btn-refresh");
  const progressWrap = document.getElementById("progress-wrap");
  const progressFill = document.getElementById("progress-fill");
  const progressLabel = document.getElementById("progress-label");

  // Padrao 30 dias (pedido do Allan, 17/07/2026) -- noticias/documentos
  // CVM tem published_at mais "curto" que acoes de rating (paginas de
  // rating listam historico mais largo por natureza), entao uma janela
  // inicial generosa evita a sensacao de "sumiu noticia" logo que abre.
  let currentWindow = "30d";
  const scanIntervalMs = (window.SCAN_INTERVAL_MINUTES || 5) * 60 * 1000;
  let secondsLeft = Math.floor(scanIntervalMs / 1000);

  // BUG REAL (27/07/2026): o botão "Forçar atualização" passou a só
  // renderizar pra quem está logado (`{% if user %}` em dashboard.html,
  // já que a Visão Geral virou pública) -- `refreshBtn` fica `null` pra
  // visitante anônimo, e as linhas que mexiam nele direto
  // (`refreshBtn.disabled = ...`) travavam a IIFE inteira com
  // TypeError logo na inicialização, antes até de `loadArticles()`
  // rodar -- ou seja, quebrava a página toda pro público, não só o
  // botão. Esse helper (e os `if (refreshBtn)` abaixo) tornam todo
  // acesso ao botão seguro quando ele não existe no DOM.
  function setRefreshBtnState(disabled, text) {
    if (!refreshBtn) return;
    refreshBtn.disabled = disabled;
    refreshBtn.textContent = text;
  }
  let pollTimer = null;

  // --------------------------------------------------------------------
  // Multi-select (setor/empresa/cobertura) -- pedido do Allan (03/08/2026):
  // poder marcar mais de uma opção em cada um desses 3 filtros. Select
  // nativo não faz isso de um jeito usável (Ctrl+clique não é óbvio pra
  // ninguém), então cada filtro virou um botão que abre um painel
  // flutuante de checkboxes (HTML em templates/dashboard.html, CSS em
  // static/style.css `.ms*`). `filter-type` continua select comum de
  // propósito -- não foi pedido multi-seleção nele.
  // --------------------------------------------------------------------
  const selectedSectors = new Set();
  const selectedCompanies = new Set();
  const selectedCoverage = new Set(["minha"]); // mesmo padrão de sempre
  const selectedSources = new Set();            // filtro de fonte (12/08/2026)

  function closeAllPanels(except) {
    document.querySelectorAll(".ms-panel").forEach((p) => {
      if (p !== except) p.hidden = true;
    });
    document.querySelectorAll(".ms-btn").forEach((b) => {
      if (b !== except) b.classList.remove("ms-open");
    });
  }

  function updateMsButtonLabel(btn, prefix, selectedSet, allLabel, nameLookup) {
    const n = selectedSet.size;
    if (n === 0) {
      btn.textContent = `${prefix}: ${allLabel}`;
    } else if (n === 1) {
      const only = [...selectedSet][0];
      btn.textContent = `${prefix}: ${nameLookup ? nameLookup(only) : only}`;
    } else {
      btn.textContent = `${prefix}: ${n} selecionados`;
    }
  }

  function populateCompanies() {
    const container = document.getElementById("ms-company-options");
    const searchInput = document.getElementById("ms-company-search");
    const filtro = (searchInput.value || "").trim().toLowerCase();

    // Trocar o setor selecionado invalida empresas que não pertencem mais
    // a nenhum setor marcado -- mesmo comportamento de antes (o <select>
    // nativo resetava sozinho ao trocar de sector porque a lista de
    // <option> era reconstruída do zero).
    if (selectedSectors.size > 0) {
      for (const cid of [...selectedCompanies]) {
        const c = companiesData.find((x) => String(x.id) === cid);
        if (!c || !selectedSectors.has(String(c.sector_id))) selectedCompanies.delete(cid);
      }
    }

    const visiveis = companiesData
      .filter((c) => selectedSectors.size === 0 || selectedSectors.has(String(c.sector_id)))
      .filter((c) => !filtro || c.name.toLowerCase().includes(filtro))
      .sort((a, b) => a.name.localeCompare(b.name));

    container.innerHTML = "";
    if (visiveis.length === 0) {
      container.innerHTML = '<div class="ms-empty">Nenhuma empresa encontrada.</div>';
    } else {
      visiveis.forEach((c) => {
        const label = document.createElement("label");
        label.className = "ms-option";
        const idStr = String(c.id);
        label.innerHTML = `<input type="checkbox" class="ms-check" data-ms="company" value="${idStr}" ${selectedCompanies.has(idStr) ? "checked" : ""}> ${c.name}`;
        container.appendChild(label);
      });
    }
    updateMsButtonLabel(
      document.getElementById("ms-company-btn"), "Empresa", selectedCompanies, "Todas",
      (id) => (companiesData.find((c) => String(c.id) === id) || {}).name || id
    );
  }

  function initMultiSelect({ msId, btnId, panelId, prefix, allLabel, selectedSet, nameLookup, onChange, acaoUso }) {
    const wrap = document.getElementById(msId);
    const btn = document.getElementById(btnId);
    const panel = document.getElementById(panelId);

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = panel.hidden;
      closeAllPanels(willOpen ? panel : null);
      panel.hidden = !willOpen;
      btn.classList.toggle("ms-open", willOpen);
      if (willOpen) {
        const search = panel.querySelector('input[type="search"]');
        if (search) search.focus();
      }
    });
    panel.addEventListener("click", (e) => e.stopPropagation());

    panel.addEventListener("change", (e) => {
      const cb = e.target;
      if (!cb.matches('input[type="checkbox"]')) return;
      if (cb.checked) selectedSet.add(cb.value);
      else selectedSet.delete(cb.value);
      // Registro de uso (22/09/2026): só quando MARCA -- desmarcar não é interesse.
      if (cb.checked && acaoUso && window.registrarUso) registrarUso(acaoUso, nameLookup(cb.value));
      updateMsButtonLabel(btn, prefix, selectedSet, allLabel, nameLookup);
      if (onChange) onChange();
    });

    const clearBtn = panel.querySelector(`.ms-clear[data-clear-target="${msId}"]`);
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        selectedSet.clear();
        panel.querySelectorAll('input[type="checkbox"]').forEach((cb) => (cb.checked = false));
        updateMsButtonLabel(btn, prefix, selectedSet, allLabel, nameLookup);
        if (onChange) onChange();
      });
    }

    const search = panel.querySelector('input[type="search"]');
    if (search) search.addEventListener("input", () => populateCompanies());

    return { wrap, btn, panel };
  }

  document.addEventListener("click", () => closeAllPanels(null));

  // ==================================================================
  // DUAS COLUNAS (22/10/2026, pedido do Allan): Imprensa à esquerda; CVM e
  // agentes fiduciários à direita. Cada uma com a própria consulta; os
  // filtros de cima valem para as duas; a busca filtra no navegador.
  // ==================================================================
  const TIPOS_IMPRENSA = ["news", "rating_action", "research"];
  const TIPOS_DOCS = ["fato_relevante", "assembleia"];
  const imprensaLista = document.getElementById("imprensa-lista");
  const docsLista = document.getElementById("docs-lista");
  const docsCategoria = document.getElementById("docs-categoria");
  const buscaNoticias = document.getElementById("busca-noticias");
  let imprensaDados = [];
  let docsDados = [];
  let docsTipo = "fato_relevante";

  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function typeLabel(t) {
    return {
      news: "Notícia",
      rating_action: "Ação de rating",
      fato_relevante: "Documento CVM",
      assembleia: "Assembleia (AGD/AGT)",
      research: "Research",
    }[t] || t;
  }

  // Dia de calendário em Brasília, para os separadores "Hoje / Ontem / 20/10".
  const fmtDiaChave = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Sao_Paulo" });
  function diaChave(iso) { return iso ? fmtDiaChave.format(new Date(iso)) : ""; }
  function rotuloDia(chave) {
    const hoje = fmtDiaChave.format(new Date());
    const ontem = fmtDiaChave.format(new Date(Date.now() - 86400000));
    if (chave === hoje) return "Hoje";
    if (chave === ontem) return "Ontem";
    const [a, m, d] = chave.split("-");
    const sem = new Date(`${chave}T12:00:00-03:00`).toLocaleDateString("pt-BR", { weekday: "long", timeZone: "America/Sao_Paulo" });
    return `${d}/${m}/${a} · ${sem}`;
  }
  function hora(iso) {
    return iso ? new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: "America/Sao_Paulo" }) : "";
  }

  function casaBusca(a) {
    const termo = (buscaNoticias.value || "").trim().toLowerCase();
    if (!termo) return true;
    const alvo = [a.title, a.snippet, a.source_name, ...(a.companies || []).map((c) => c.name),
                  ...(a.sector_tags || []).map((s) => s.name)].join(" ").toLowerCase();
    return termo.split(/\s+/).every((t) => alvo.includes(t));
  }

  // Agrupa por dia (já vem do mais novo para o mais antigo).
  function porDia(lista) {
    const grupos = [];
    lista.forEach((a) => {
      const chave = diaChave(a.published_at || a.found_at);
      if (!grupos.length || grupos[grupos.length - 1].chave !== chave) grupos.push({ chave, itens: [] });
      grupos[grupos.length - 1].itens.push(a);
    });
    return grupos;
  }

  function chipsDe(a) {
    // Empresas casadas viram chips para auditar de relance o vínculo; notícia
    // setorial (só termo de setor) ganha a tag do setor.
    const empresas = (a.companies || [])
      .map((c) => `<span class="company-chip" title="Empresa casada por keyword/alias">${esc(c.name)}</span>`).join("");
    const setores = (a.sector_tags || [])
      .map((s) => `<span class="sector-chip" title="Notícia setorial">Setor: ${esc(s.name)}</span>`).join("");
    return empresas || setores ? `<div class="companies">${setores}${empresas}</div>` : "";
  }

  // ------------------------------------------------------------------
  // AÇÕES DE RATING (23/10/2026): tipo lido do título da agência. A ordem
  // importa -- "Rebaixa ...; Perspectiva Negativa" é rebaixamento, e
  // "Revisa Perspectiva ...; Afirma Rating" é perspectiva, não afirmação.
  // ------------------------------------------------------------------
  const TIPOS_RATING = {
    rebaixamento: { rotulo: "Rebaixamento", plural: "rebaixamentos", simbolo: "▼" },
    elevacao: { rotulo: "Elevação", plural: "elevações", simbolo: "▲" },
    perspectiva: { rotulo: "Perspectiva / observação", plural: "perspectiva / observação", simbolo: "◐" },
    novo: { rotulo: "Novo rating", plural: "novos ratings", simbolo: "+" },
    afirmacao: { rotulo: "Afirmação", plural: "afirmações", simbolo: "●" },
    outra: { rotulo: "Outra ação", plural: "outras", simbolo: "·" },
  };
  const semAcentoN = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  function tipoRating(titulo) {
    const s = semAcentoN(titulo);
    if (/rebaix|downgrade/.test(s)) return "rebaixamento";
    if (/\beleva|elevad|upgrade/.test(s)) return "elevacao";
    if (/coloca|observacao|creditwatch|revisa (a )?perspectiva|altera (a )?perspectiva/.test(s)) return "perspectiva";
    if (/atribui|atribuid|novo rating/.test(s)) return "novo";
    if (/afirma|reafirm|mantem|mantid/.test(s)) return "afirmacao";
    return "outra";
  }
  const agenciaCurta = (fonte) => (/fitch/i.test(fonte) ? "Fitch" : /s&p|standard/i.test(fonte) ? "S&P"
    : /moody/i.test(fonte) ? "Moody's" : fonte);

  const JANELA_DESTAQUE_H = 48;
  let filtroRating = null;          // tipo escolhido nos contadores do destaque
  let destaqueAberto = false;
  const destaqueEl = document.getElementById("ratings-destaque");

  function pillRating(tipo) {
    const t = TIPOS_RATING[tipo];
    return `<span class="rt-pill rt-${tipo}">${t.simbolo} ${t.rotulo}</span>`;
  }

  function renderRatingsDestaque() {
    const limite = Date.now() - JANELA_DESTAQUE_H * 3600 * 1000;
    const todas = imprensaDados.filter((a) => a.article_type === "rating_action" && casaBusca(a));
    const recentes = todas.filter((a) => new Date(a.published_at || a.found_at).getTime() >= limite);
    if (!recentes.length) {
      const ultima = todas[0];
      destaqueEl.className = "ratings-destaque vazio";
      destaqueEl.innerHTML = `<span class="rd-texto">Nenhuma ação de rating nos últimos 2 dias</span>`
        + (ultima ? `<span class="rd-ultima">última: ${esc(agenciaCurta(ultima.source_name))} · `
          + `<a href="${esc(ultima.url)}" target="_blank" rel="noopener">${esc((ultima.companies || [])[0]?.name || ultima.title.slice(0, 60))}</a>`
          + ` · ${esc(rotuloDia(diaChave(ultima.published_at || ultima.found_at)))}</span>` : "");
      return;
    }
    const porTipo = {};
    recentes.forEach((a) => { const t = tipoRating(a.title); porTipo[t] = (porTipo[t] || 0) + 1; });
    if (filtroRating && !porTipo[filtroRating]) filtroRating = null;
    const lista = recentes.filter((a) => !filtroRating || tipoRating(a.title) === filtroRating);
    const visiveis = destaqueAberto ? lista : lista.slice(0, 4);
    const contadores = Object.keys(TIPOS_RATING).filter((t) => porTipo[t]).map((t) => `
      <button type="button" class="rd-cont rt-${t}${filtroRating === t ? " ativo" : ""}" data-tipo="${t}"
              aria-pressed="${filtroRating === t}" title="Filtrar: ${TIPOS_RATING[t].plural}">
        <b>${porTipo[t]}</b> ${TIPOS_RATING[t].simbolo} ${porTipo[t] === 1 ? TIPOS_RATING[t].rotulo.toLowerCase() : TIPOS_RATING[t].plural}
      </button>`).join("");
    destaqueEl.className = "ratings-destaque";
    destaqueEl.innerHTML = `
      <div class="rd-topo">
        <div class="rd-titulo"><span class="rd-numero">${recentes.length}</span>
          <span class="rd-texto">${recentes.length === 1 ? "ação de rating" : "ações de rating"} nos últimos 2 dias</span></div>
        <div class="rd-contadores">${contadores}</div>
      </div>
      <ul class="rd-lista">
        ${visiveis.map((a) => {
          const tipo = tipoRating(a.title);
          const empresa = (a.companies || [])[0]?.name;
          return `<li>
            ${pillRating(tipo)}
            <a href="${esc(a.url)}" target="_blank" rel="noopener" class="rd-manchete" title="${esc(a.title)}">
              ${empresa ? `<b>${esc(empresa)}</b> · ` : ""}${esc(a.title)}</a>
            <span class="rd-meta">${esc(agenciaCurta(a.source_name))} · ${hora(a.published_at || a.found_at)}</span>
          </li>`;
        }).join("")}
      </ul>
      ${lista.length > 4 ? `<button type="button" class="rd-mais">${destaqueAberto ? "Mostrar menos" : `Ver todas as ${lista.length}`}</button>` : ""}
    `;
    destaqueEl.querySelectorAll(".rd-cont").forEach((b) => b.addEventListener("click", () => {
      filtroRating = filtroRating === b.dataset.tipo ? null : b.dataset.tipo;
      if (filtroRating && window.registrarUso) registrarUso("filtro", "Ratings: " + TIPOS_RATING[filtroRating].plural);
      renderRatingsDestaque();
    }));
    const mais = destaqueEl.querySelector(".rd-mais");
    if (mais) mais.addEventListener("click", () => { destaqueAberto = !destaqueAberto; renderRatingsDestaque(); });
  }

  function renderImprensa() {
    renderRatingsDestaque();
    const lista = imprensaDados.filter(casaBusca);
    document.getElementById("imprensa-contagem").textContent =
      `${lista.length} ${lista.length === 1 ? "item" : "itens"}`;
    imprensaLista.innerHTML = "";
    if (!lista.length) {
      imprensaLista.innerHTML = '<p class="vazio-noticias">Nada na imprensa para estes filtros.</p>';
      return;
    }
    const frag = document.createDocumentFragment();
    porDia(lista).forEach((g) => {
      const dia = document.createElement("div");
      dia.className = "feed-dia";
      dia.textContent = rotuloDia(g.chave);
      frag.appendChild(dia);
      g.itens.forEach((a) => {
        const item = document.createElement("article");
        const rating = a.article_type === "rating_action";
        const tipo = rating ? tipoRating(a.title) : null;
        // Ação de rating: barra lateral na cor do tipo -- não se perde no feed.
        item.className = "feed-item" + (rating ? ` feed-rating rt-borda-${tipo}` : "");
        const fora = a.is_covered === false && !rating;
        item.innerHTML = `
          <div class="feed-meta">
            <span class="feed-hora">${hora(a.published_at || a.found_at)}</span>
            <span class="feed-fonte">${esc(a.source_name)}</span>
            ${rating ? pillRating(tipo) : ""}
            ${a.article_type === "research" ? '<span class="badge badge-research">Research</span>' : ""}
            ${fora ? '<span class="tag" title="Não bateu com nenhuma empresa/setor da sua cobertura">fora da cobertura</span>' : ""}
          </div>
          <h3><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a></h3>
          ${a.snippet ? `<p class="snippet">${esc(a.snippet)}</p>` : ""}
          ${chipsDe(a)}
        `;
        frag.appendChild(item);
      });
    });
    imprensaLista.appendChild(frag);
  }

  // Título do RAD: "EMPRESA — Categoria — Assunto". Assembleias e títulos
  // fora desse formato caem inteiros no assunto.
  function partesDocumento(a) {
    const p = String(a.title || "").split(" — ");
    if (a.article_type === "fato_relevante" && p.length >= 2) {
      return { emissor: p[0], categoria: p[1], assunto: p.slice(2).join(" — ") };
    }
    return { emissor: "", categoria: a.article_type === "assembleia" ? "Assembleia (AGD/AGT)" : typeLabel(a.article_type),
             assunto: a.title };
  }

  function atualizarCategorias() {
    // Categorias presentes na aba atual, da mais frequente para a menos.
    const contagem = new Map();
    docsDados.filter((a) => a.article_type === docsTipo).forEach((a) => {
      const c = partesDocumento(a).categoria;
      contagem.set(c, (contagem.get(c) || 0) + 1);
    });
    const atual = docsCategoria.value;
    docsCategoria.innerHTML = '<option value="">Todas as categorias</option>'
      + [...contagem.entries()].sort((x, y) => y[1] - x[1])
        .map(([c, n]) => `<option value="${esc(c)}">${esc(c)} (${n})</option>`).join("");
    docsCategoria.value = contagem.has(atual) ? atual : "";
  }

  function renderDocs() {
    const daAba = docsDados.filter((a) => a.article_type === docsTipo);
    document.getElementById("n-cvm").textContent = docsDados.filter((a) => a.article_type === "fato_relevante").length || "";
    document.getElementById("n-agd").textContent = docsDados.filter((a) => a.article_type === "assembleia").length || "";
    const cat = docsCategoria.value;
    const lista = daAba.filter((a) => (!cat || partesDocumento(a).categoria === cat) && casaBusca(a));
    document.getElementById("docs-contagem").textContent =
      `${lista.length} documento${lista.length === 1 ? "" : "s"}`;
    docsLista.innerHTML = "";
    if (!lista.length) {
      docsLista.innerHTML = docsTipo === "assembleia" && !daAba.length
        ? '<p class="vazio-noticias">Nenhum documento de agente fiduciário das empresas cobertas nesta janela.<br>'
          + '<span class="muted small">Fontes: Oliveira Trust, Vórtx e Pentágono.</span></p>'
        : '<p class="vazio-noticias">Nenhum documento para estes filtros.</p>';
      return;
    }
    const frag = document.createDocumentFragment();
    porDia(lista).forEach((g) => {
      const dia = document.createElement("div");
      dia.className = "feed-dia";
      dia.textContent = rotuloDia(g.chave);
      frag.appendChild(dia);
      g.itens.forEach((a) => {
        const p = partesDocumento(a);
        // CARTÃO DE DOCUMENTO (23/10/2026): à esquerda, empresa com a
        // categoria ao lado e "Tema:"; à direita, no canto, Visualizar e
        // Baixar. Fato Relevante ganha a etiqueta laranja.
        const curto = (a.companies || [])[0]?.name || p.emissor || a.source_name;
        const relevante = /fato relevante/i.test(p.categoria);
        const formulario = (a.url || "").includes("NumeroSequencialDocumento");
        const baixar = a.download_url
          ? `<a class="btn-doc btn-doc-baixar" href="${esc(a.download_url)}" download rel="noopener" title="Baixar o arquivo">Baixar</a>`
          : `<span class="btn-doc btn-doc-off" title="${formulario
              ? "Formulário estruturado do RAD (FRE, Código de Governança): abre por página, sem arquivo único"
              : "O RAD não informou o número do documento"}">Baixar</span>`;
        const item = document.createElement("div");
        item.className = "doc-card";
        item.innerHTML = `
          <div class="doc-corpo">
            <div class="doc-linha1">
              <span class="doc-empresa" title="${esc(p.emissor || curto)}">${esc(curto)}</span>
              <span class="doc-tag${relevante ? " relevante" : ""}">${esc(p.categoria)}</span>
            </div>
            ${p.assunto ? `<div class="doc-tema"><span>Tema:</span> ${esc(p.assunto)}</div>` : ""}
            <div class="doc-hora">${hora(a.published_at || a.found_at)}${a.article_type === "assembleia" ? ` · ${esc(a.source_name)}` : ""}</div>
          </div>
          <div class="doc-acoes">
            <a class="btn-doc" href="${esc(a.url)}" target="_blank" rel="noopener" title="Abrir o documento em outra aba">Visualizar</a>
            ${baixar}
          </div>
        `;
        frag.appendChild(item);
      });
    });
    docsLista.appendChild(frag);
  }

  function parametrosBase() {
    const params = new URLSearchParams({ window: currentWindow });
    // Um par `chave=valor` por item selecionado -- o backend junta em lista.
    selectedSectors.forEach((id) => params.append("sector_id", id));
    selectedCompanies.forEach((id) => params.append("company_id", id));
    selectedSources.forEach((v) => params.append("source_name", v));
    selectedCoverage.forEach((v) => params.append("coverage", v));
    return params;
  }

  async function loadArticles() {
    const pImprensa = parametrosBase();
    TIPOS_IMPRENSA.forEach((t) => pImprensa.append("article_type", t));
    const pDocs = parametrosBase();
    TIPOS_DOCS.forEach((t) => pDocs.append("article_type", t));
    statusEl.textContent = "Atualizando…";
    try {
      const [imp, docs] = await Promise.all([
        fetch(`/api/articles?${pImprensa.toString()}`).then((r) => r.json()),
        fetch(`/api/articles?${pDocs.toString()}`).then((r) => r.json()),
      ]);
      imprensaDados = imp.articles || [];
      docsDados = docs.articles || [];
      renderImprensa();
      atualizarCategorias();
      renderDocs();
      statusEl.textContent = `${imprensaDados.length} na imprensa · ${docsDados.length} documento(s) — atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
    } catch (e) {
      statusEl.textContent = "Erro ao carregar notícias.";
    }
  }

  function ligarAbasNoticias(id, aoTrocar) {
    document.querySelectorAll(`#${id} .win-btn`).forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(`#${id} .win-btn`).forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        aoTrocar(btn.dataset.tipo, btn);
      });
    });
  }
  ligarAbasNoticias("docs-tabs", (t, btn) => {
    docsTipo = t;
    if (window.registrarUso) registrarUso("filtro", "Documentos: " + btn.textContent.trim());
    docsCategoria.value = "";
    atualizarCategorias();
    renderDocs();
  });
  docsCategoria.addEventListener("change", renderDocs);
  let buscaTimer = null;
  buscaNoticias.addEventListener("input", () => {
    clearTimeout(buscaTimer);
    buscaTimer = setTimeout(() => { renderImprensa(); renderDocs(); }, 120);
  });

  function setProgress(current, total, sourceName) {
    progressWrap.style.display = "flex";
    const pct = total > 0 ? Math.round((current / total) * 100) : 0;
    progressFill.style.width = pct + "%";
    if (sourceName) {
      progressLabel.textContent = `Verificando ${current} de ${total}: ${sourceName}`;
    } else {
      progressLabel.textContent = `Verificando ${current} de ${total}…`;
    }
  }

  function hideProgress() {
    progressWrap.style.display = "none";
    progressFill.style.width = "0%";
  }

  async function pollRefreshStatus() {
    try {
      const resp = await fetch("/api/refresh-status");
      const s = await resp.json();
      if (s.running) {
        setProgress(s.current, s.total, s.source_name);
        pollTimer = setTimeout(pollRefreshStatus, 700);
        return;
      }
      // terminou
      hideProgress();
      setRefreshBtnState(false, "Forçar atualização");
      if (s.error) {
        statusEl.textContent = "A atualização falhou: " + s.error;
      } else if (s.summary) {
        statusEl.textContent = `Atualização concluída: ${s.summary.n_new} notícia(s) nova(s).`;
      }
      secondsLeft = Math.floor(scanIntervalMs / 1000);
      await loadArticles();
    } catch (e) {
      hideProgress();
      setRefreshBtnState(false, "Forçar atualização");
      statusEl.textContent = "Erro ao verificar o progresso da atualização.";
    }
  }

  async function forceRefresh() {
    setRefreshBtnState(true, "Buscando…");
    setProgress(0, 1, "");
    statusEl.textContent = "Varrendo fontes agora, pode levar alguns segundos…";
    try {
      const resp = await fetch("/api/force-refresh", { method: "POST" });
      const data = await resp.json();

      // Modo nuvem: o robô roda no GitHub Actions, não neste servidor --
      // não dá pra acompanhar progresso em tempo real (são máquinas
      // diferentes), então só avisamos que foi disparado e conferimos de
      // novo depois de um tempo, em vez de tentar a barra de progresso.
      if (data.dispatched_to_github) {
        hideProgress();
        setRefreshBtnState(false, "Forçar atualização");
        statusEl.textContent = "Atualização disparada no GitHub Actions — leva alguns minutos pra aparecer aqui.";
        setTimeout(() => { loadArticles(); }, 90000);
        return;
      }

      if (data.already_running) {
        statusEl.textContent = "Já existe uma atualização em andamento — acompanhando...";
      }
      if (pollTimer) clearTimeout(pollTimer);
      pollRefreshStatus();
    } catch (e) {
      hideProgress();
      setRefreshBtnState(false, "Forçar atualização");
      statusEl.textContent = "Erro ao iniciar a atualização.";
    }
  }

  winButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      winButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentWindow = btn.dataset.window;
      loadArticles();
    });
  });

  initMultiSelect({
    msId: "ms-sector", btnId: "ms-sector-btn", panelId: "ms-sector-panel",
    prefix: "Setor", allLabel: "Todos", selectedSet: selectedSectors, acaoUso: "setor",
    nameLookup: (id) => {
      const cb = document.querySelector(`#ms-sector-panel input[value="${id}"]`);
      return cb ? cb.parentElement.textContent.trim() : id;
    },
    onChange: () => { populateCompanies(); loadArticles(); },
  });

  initMultiSelect({
    msId: "ms-company", btnId: "ms-company-btn", panelId: "ms-company-panel",
    prefix: "Empresa", allLabel: "Todas", selectedSet: selectedCompanies, acaoUso: "empresa",
    nameLookup: (id) => (companiesData.find((c) => String(c.id) === id) || {}).name || id,
    onChange: () => loadArticles(),
  });

  // Fonte (12/08/2026): mesmo padrão de Setor/Empresa. O valor do
  // checkbox é o NOME da fonte, não um id -- é o que `Article.source_name`
  // guarda e o que aparece no card, então o filtro casa com o que o
  // usuário está vendo na tela.
  initMultiSelect({
    msId: "ms-source", btnId: "ms-source-btn", panelId: "ms-source-panel",
    prefix: "Fonte", allLabel: "Todas", selectedSet: selectedSources, acaoUso: "fonte",
    nameLookup: (v) => v,
    onChange: () => loadArticles(),
  });

  // Busca dentro do painel de fontes -- são 26 e crescendo; sem isso a
  // lista vira rolagem longa.
  const buscaFonte = document.getElementById("ms-source-search");
  if (buscaFonte) {
    buscaFonte.addEventListener("input", () => {
      const termo = buscaFonte.value.trim().toLowerCase();
      document.querySelectorAll("#ms-source-options .ms-option").forEach((el) => {
        el.hidden = termo !== "" && !(el.dataset.nome || "").includes(termo);
      });
    });
  }

  const coverageNames = { minha: "Minha cobertura", todos: "Todos" };
  initMultiSelect({
    msId: "ms-coverage", btnId: "ms-coverage-btn", panelId: "ms-coverage-panel",
    prefix: "Cobertura", allLabel: "Minha cobertura", selectedSet: selectedCoverage,
    nameLookup: (v) => coverageNames[v] || v,
    onChange: () => {
      // Nunca deixa ficar sem NENHUMA opção de cobertura marcada -- volta
      // pro padrão "minha" em vez de mandar uma lista vazia (o backend até
      // trataria vazio como "minha" sozinho, mas o botão ficaria mostrando
      // rótulo errado se nada estivesse marcado de verdade).
      if (selectedCoverage.size === 0) {
        selectedCoverage.add("minha");
        const cb = document.querySelector('#ms-coverage-panel input[value="minha"]');
        if (cb) cb.checked = true;
        updateMsButtonLabel(
          document.getElementById("ms-coverage-btn"), "Cobertura", selectedCoverage,
          "Minha cobertura", (v) => coverageNames[v] || v
        );
      }
      loadArticles();
    },
  });

  if (refreshBtn) refreshBtn.addEventListener("click", forceRefresh);

  setInterval(() => {
    if (refreshBtn && refreshBtn.disabled) return; // nao conta regressiva enquanto ja esta atualizando
    secondsLeft -= 1;
    if (secondsLeft <= 0) {
      secondsLeft = Math.floor(scanIntervalMs / 1000);
      loadArticles();
    }
    const m = Math.floor(secondsLeft / 60).toString().padStart(2, "0");
    const s = (secondsLeft % 60).toString().padStart(2, "0");
    countdownEl.textContent = `próxima atualização automática em ${m}:${s}`;
  }, 1000);

  // se ja tinha uma varredura manual rodando quando a pagina carregou
  // (ex.: usuario apertou o botao, atualizou a pagina, ela ainda esta rodando)
  (async function checkAlreadyRunning() {
    try {
      const resp = await fetch("/api/refresh-status");
      const s = await resp.json();
      if (s.running) {
        setRefreshBtnState(true, "Buscando…");
        pollRefreshStatus();
      }
    } catch (e) { /* ignora */ }
  })();

  populateCompanies();
  loadArticles();
})();
