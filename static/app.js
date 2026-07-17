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
  const sectorSelect = document.getElementById("filter-sector");
  const companySelect = document.getElementById("filter-company");
  const typeSelect = document.getElementById("filter-type");
  const coverageSelect = document.getElementById("filter-coverage");
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

  let currentWindow = "5d";
  const scanIntervalMs = (window.SCAN_INTERVAL_MINUTES || 5) * 60 * 1000;
  let secondsLeft = Math.floor(scanIntervalMs / 1000);
  let pollTimer = null;

  function populateCompanies() {
    const sectorId = sectorSelect.value;
    companySelect.innerHTML = '<option value="">Todas as empresas</option>';
    companiesData
      .filter((c) => !sectorId || String(c.sector_id) === sectorId)
      .sort((a, b) => a.name.localeCompare(b.name))
      .forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.id;
        opt.textContent = c.name;
        companySelect.appendChild(opt);
      });
  }

  function typeLabel(t) {
    return {
      news: "Notícia",
      rating_action: "Ação de rating",
      fato_relevante: "Documento CVM",
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
    const params = new URLSearchParams({ window: currentWindow, coverage: coverageSelect.value });
    if (sectorSelect.value) params.set("sector_id", sectorSelect.value);
    if (companySelect.value) params.set("company_id", companySelect.value);
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
      refreshBtn.disabled = false;
      refreshBtn.textContent = "Forçar atualização";
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
      refreshBtn.disabled = false;
      refreshBtn.textContent = "Forçar atualização";
      statusEl.textContent = "Erro ao verificar o progresso da atualização.";
    }
  }

  async function forceRefresh() {
    refreshBtn.disabled = true;
    refreshBtn.textContent = "Buscando…";
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
        refreshBtn.disabled = false;
        refreshBtn.textContent = "Forçar atualização";
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
      refreshBtn.disabled = false;
      refreshBtn.textContent = "Forçar atualização";
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

  sectorSelect.addEventListener("change", () => {
    populateCompanies();
    loadArticles();
  });
  companySelect.addEventListener("change", loadArticles);
  typeSelect.addEventListener("change", loadArticles);
  coverageSelect.addEventListener("change", loadArticles);
  refreshBtn.addEventListener("click", forceRefresh);

  setInterval(() => {
    if (refreshBtn.disabled) return; // nao conta regressiva enquanto ja esta atualizando
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
        refreshBtn.disabled = true;
        refreshBtn.textContent = "Buscando…";
        pollRefreshStatus();
      }
    } catch (e) { /* ignora */ }
  })();

  populateCompanies();
  loadArticles();
  loadStatus();
})();
