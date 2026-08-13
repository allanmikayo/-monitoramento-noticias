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
  const typeSelect = document.getElementById("filter-type");
  const winButtons = document.querySelectorAll(".win-btn");
  const listEl = document.getElementById("article-list");
  const statusEl = document.getElementById("status-text");
  const countdownEl = document.getElementById("countdown");
  const refreshBtn = document.getElementById("btn-refresh");
  const progressWrap = document.getElementById("progress-wrap");
  const progressFill = document.getElementById("progress-fill");
  const progressLabel = document.getElementById("progress-label");
  const diagnosticSummary = document.getElementById("diagnostic-summary");
  const diagnosticTbody = document.getElementById("diagnostic-tbody");

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

  function initMultiSelect({ msId, btnId, panelId, prefix, allLabel, selectedSet, nameLookup, onChange }) {
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

  function typeLabel(t) {
    return {
      news: "Notícia",
      rating_action: "Ação de rating",
      fato_relevante: "Documento CVM",
      assembleia: "Assembleia (AGD/AGT)",
      research: "Research",
    }[t] || t;
  }

  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function render(articles) {
    listEl.innerHTML = "";
    if (articles.length === 0) {
      listEl.innerHTML = '<p class="muted">Nenhuma notícia encontrada para este filtro. Veja o painel "Diagnóstico da última varredura" acima para entender o que cada fonte retornou.</p>';
      return;
    }
    articles.forEach((a) => {
      const card = document.createElement("article");
      card.className = "card";
      // Empresas casadas viram chips destacados (nao so' texto simples) --
      // pedido do Allan pra conseguir auditar de relance se a empresa
      // vinculada faz sentido pro conteudo da noticia (achamos e corrigimos
      // um bug em que uma empresa errada podia ficar grudada no artigo
      // pra sempre, mesmo depois do casamento de keywords ser corrigido).
      const companyChips = (a.companies || [])
        .map((c) => `<span class="company-chip" title="Empresa casada por keyword/alias nesta noticia">${c.name}</span>`)
        .join("");
      // Noticia setorial (bateu so' termo de setor, sem citar empresa
      // especifica) ganha uma tag do SETOR em vez de ficar "grudada" em
      // toda empresa do setor -- pedido do Allan, 17/07/2026.
      const sectorChips = (a.sector_tags || [])
        .map((s) => `<span class="sector-chip" title="Noticia setorial: afeta todo o setor, sem citar empresa especifica">Setor: ${s.name}</span>`)
        .join("");
      const foraCobertura = a.is_covered === false && a.article_type !== "rating_action";
      card.innerHTML = `
        <div class="card-meta">
          <span class="badge badge-${a.article_type}">${typeLabel(a.article_type)}</span>
          <span class="source">${a.source_name}</span>
          <span class="muted">${fmtDate(a.published_at || a.found_at)}</span>
          ${foraCobertura ? '<span class="tag" title="Não bateu com nenhuma empresa/setor da sua cobertura">fora da cobertura nomeada</span>' : ""}
        </div>
        <h3><a href="${a.url}" target="_blank" rel="noopener">${a.title}</a></h3>
        ${a.snippet ? `<p class="snippet">${a.snippet}</p>` : ""}
        ${sectorChips ? `<div class="companies">${sectorChips}</div>` : ""}
        ${companyChips ? `<div class="companies">${companyChips}</div>` : ""}
      `;
      listEl.appendChild(card);
    });
  }

  function situacaoFonte(s) {
    if (s.error) return { texto: "Erro: " + s.error, classe: "tag-off" };
    if (s.found === 0) return { texto: "Nada encontrado no site agora", classe: "" };
    if (s.matched === 0) return { texto: "Nada mencionando suas empresas cobertas", classe: "" };
    if (s.new === 0) return { texto: "Sem novidades (já coletado antes)", classe: "" };
    return { texto: s.new + " novo(s)", classe: "tag-ok" };
  }

  function renderDiagnostic(lastRun) {
    if (!lastRun) {
      diagnosticSummary.textContent = "(ainda não rodou nenhuma varredura)";
      diagnosticTbody.innerHTML = "";
      return;
    }
    const quando = fmtDate(lastRun.finished_at || lastRun.started_at);
    const origem = lastRun.triggered_by === "manual" ? "manual" : "automática";
    diagnosticSummary.textContent = `— ${quando} (${origem}), ${lastRun.n_found} novo(s) no total`;

    diagnosticTbody.innerHTML = "";
    (lastRun.sources || []).forEach((s) => {
      const sit = situacaoFonte(s);
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${s.name}</td>
        <td>${s.found}</td>
        <td>${s.matched}</td>
        <td>${s.new}</td>
        <td><span class="tag ${sit.classe}">${sit.texto}</span></td>
      `;
      diagnosticTbody.appendChild(tr);
    });
  }

  async function loadStatus() {
    try {
      const resp = await fetch("/api/status");
      const data = await resp.json();
      renderDiagnostic(data.last_run);
    } catch (e) {
      // silencioso -- painel de diagnostico e' informativo, nao critico
    }
  }

  async function loadArticles() {
    const params = new URLSearchParams({ window: currentWindow });
    // Setor/empresa/cobertura mandam um par `chave=valor` por item
    // selecionado (URLSearchParams.append, não .set) -- o backend
    // (`Query(default=[...])` em app.py) junta parâmetros repetidos numa
    // lista sozinho. Nenhum selecionado = comportamento de sempre (sem
    // filtro de setor/empresa; cobertura cai no default "minha" do backend
    // quando a lista vem vazia).
    selectedSectors.forEach((id) => params.append("sector_id", id));
    selectedCompanies.forEach((id) => params.append("company_id", id));
    selectedSources.forEach((v) => params.append("source_name", v));
    selectedCoverage.forEach((v) => params.append("coverage", v));
    if (typeSelect.value) params.set("article_type", typeSelect.value);

    statusEl.textContent = "Atualizando…";
    try {
      const resp = await fetch(`/api/articles?${params.toString()}`);
      const data = await resp.json();
      render(data.articles);
      statusEl.textContent = `${data.count} notícia(s) encontrada(s) — atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
    } catch (e) {
      statusEl.textContent = "Erro ao carregar notícias.";
    }
  }

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
      await loadStatus();
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
        setTimeout(() => { loadArticles(); loadStatus(); }, 90000);
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
    prefix: "Setor", allLabel: "Todos", selectedSet: selectedSectors,
    nameLookup: (id) => {
      const cb = document.querySelector(`#ms-sector-panel input[value="${id}"]`);
      return cb ? cb.parentElement.textContent.trim() : id;
    },
    onChange: () => { populateCompanies(); loadArticles(); },
  });

  initMultiSelect({
    msId: "ms-company", btnId: "ms-company-btn", panelId: "ms-company-panel",
    prefix: "Empresa", allLabel: "Todas", selectedSet: selectedCompanies,
    nameLookup: (id) => (companiesData.find((c) => String(c.id) === id) || {}).name || id,
    onChange: () => loadArticles(),
  });

  // Fonte (12/08/2026): mesmo padrão de Setor/Empresa. O valor do
  // checkbox é o NOME da fonte, não um id -- é o que `Article.source_name`
  // guarda e o que aparece no card, então o filtro casa com o que o
  // usuário está vendo na tela.
  initMultiSelect({
    msId: "ms-source", btnId: "ms-source-btn", panelId: "ms-source-panel",
    prefix: "Fonte", allLabel: "Todas", selectedSet: selectedSources,
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

  typeSelect.addEventListener("change", loadArticles);
  if (refreshBtn) refreshBtn.addEventListener("click", forceRefresh);

  setInterval(() => {
    if (refreshBtn && refreshBtn.disabled) return; // nao conta regressiva enquanto ja esta atualizando
    secondsLeft -= 1;
    if (secondsLeft <= 0) {
      secondsLeft = Math.floor(scanIntervalMs / 1000);
      loadArticles();
      loadStatus();
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
  loadStatus();
})();
