(function () {
  // Módulo "Spreads" do Hub Credit Research (23/07/2026, ampliado 24/07/2026
  // com bases de comparação nomeadas e a aba "Marcação Emissores"). Padrão
  // de código igual ao app.js do dashboard de notícias: IIFE simples, sem
  // framework, fetch direto pras rotas em app/spreads_routes.py.
  //
  // "classe" (IPCA + Incentivadas | CDI + Tradicionais) é o filtro
  // principal -- pedido explícito do Allan: as duas bases não são
  // comparáveis entre si, então TODOS os gráficos desta página recarregam
  // do zero ao trocar de classe (não é um filtro "por cima", é uma troca
  // de contexto completa).

  const classeTabs = document.querySelectorAll("#classe-tabs .win-btn");
  const baseTabs = document.querySelectorAll("#base-tabs .win-btn");
  // Data de referência da Visão Geral (pedido do Allan, 27/07/2026): por
  // padrão vazio = sempre a última data disponível (mesmo comportamento
  // de sempre); quando o Allan escolhe uma data aqui, KPI/movers/
  // distribuição passam a olhar "como se hoje fosse" essa data (o
  // back-end resolve pra data disponível mais próxima pra trás, ver
  // `_resolve_hoje` em queries.py).
  const visaoDataInput = document.getElementById("visao-data");
  const buscaInput = document.getElementById("busca-ativo");
  const buscaResultados = document.getElementById("busca-resultados");
  const drilldownWrap = document.getElementById("drilldown-wrap");
  const btnFecharDrilldown = document.getElementById("btn-fechar-drilldown");

  // Botão "Detalhes" (pedido do Allan, 27/07/2026, simplificado no mesmo
  // dia pra um dia só em vez de "até uma data") -- tabela com o nível
  // mais granular de dado (uma linha por Código+Data, sem agregação, de
  // UM dia por vez), com filtro de classe PRÓPRIO (Todos/IPCA+/CDI+ --
  // independente do classeTabs principal, já que aqui misturar classe é
  // só listagem, não gráfico).
  const btnAbrirDetalhes = document.getElementById("btn-abrir-detalhes");
  const detalhesWrap = document.getElementById("detalhes-wrap");
  const btnFecharDetalhes = document.getElementById("btn-fechar-detalhes");
  const detalhesClasseTabs = document.querySelectorAll("#detalhes-classe-tabs .win-btn");
  const detalhesDataInput = document.getElementById("detalhes-data");
  const btnExportarDetalhes = document.getElementById("btn-exportar-detalhes");
  const detalhesTbody = document.querySelector("#tabela-detalhes tbody");
  const detalhesContagem = document.getElementById("detalhes-contagem");

  let currentClasse = document.querySelector("#classe-tabs .win-btn.active")?.dataset.classe || "";
  let currentBase = document.querySelector("#base-tabs .win-btn.active")?.dataset.base || "WoW";
  let currentDrilldownCodigo = null;

  let currentDetalhesClasse = document.querySelector("#detalhes-classe-tabs .win-btn.active")?.dataset.classe || "";
  let detalhesCarregados = false;

  const charts = {}; // nome -> instancia Chart.js (destruída/recriada a cada atualização)

  function fmtBps(v) {
    if (v === null || v === undefined) return "—";
    const s = v > 0 ? "+" : "";
    return `${s}${v.toFixed(1)} bps`;
  }

  function fmtData(iso) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-");
    return `${d}/${m}/${y}`;
  }

  // Intervalo explícito pra rótulos tipo "Média 3M"/"Média 7d" (pedido
  // do Allan, 27/07/2026: "sempre que colocar algum indicativo como
  // 'Média 7d:' coloque (data-data) explícito") -- formato curto
  // dd/mm (sem ano, já que essas janelas nunca passam de poucos meses).
  function fmtDataCurta(iso) {
    if (!iso) return "";
    const [, m, d] = iso.split("-");
    return `${d}/${m}`;
  }

  function fmtIntervalo(inicioIso, fimIso) {
    if (!inicioIso || !fimIso) return "";
    return `(${fmtDataCurta(inicioIso)}-${fmtDataCurta(fimIso)})`;
  }

  function fmtPct(v, casas) {
    return v !== null && v !== undefined ? `${v.toLocaleString("pt-BR", { maximumFractionDigits: casas ?? 2 })}%` : "—";
  }

  function fmtNum(v, casas) {
    return v !== null && v !== undefined ? v.toLocaleString("pt-BR", { maximumFractionDigits: casas ?? 2 }) : "—";
  }

  function destroyChart(name) {
    if (charts[name]) {
      charts[name].destroy();
      delete charts[name];
    }
  }

  async function fetchJSON(url, params) {
    // Suporta valor tipo array (ex.: varios "nome" pra selecao multipla de
    // emissores, 24/07/2026) -- URLSearchParams(objeto) sozinho junta um
    // array com virgula ("nome=A,B") em vez de repetir a chave
    // ("nome=A&nome=B"), que e' o formato que FastAPI espera pra list[str].
    const usp = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
      if (Array.isArray(v)) v.forEach((item) => usp.append(k, item));
      else if (v !== undefined && v !== null) usp.append(k, v);
    });
    const resp = await fetch(`${url}?${usp.toString()}`);
    if (!resp.ok) throw new Error(`${url}: ${resp.status}`);
    return resp.json();
  }

  // ------------------------------------------------------------------
  // KPIs
  // ------------------------------------------------------------------
  async function loadKPI() {
    const data = await fetchJSON("/api/spreads/summary", { classe: currentClasse, base: currentBase, data: visaoDataInput.value || undefined });
    // Trava o campo de data pra não deixar escolher além do que existe na
    // base (sem dado futuro pra mostrar) -- só quando o campo ainda está
    // vazio (nesse caso `data.data_referencia` é sempre a última
    // disponível de verdade); se já tem uma data escolhida, a resposta
    // reflete ESSA data, não a mais recente, então não mexe no max.
    if (!visaoDataInput.value && data.data_referencia) visaoDataInput.max = data.data_referencia;
    document.getElementById("kpi-data-ref").textContent = fmtData(data.data_referencia);
    document.getElementById("kpi-spread").textContent = data.spread_medio !== null ? `${data.spread_medio.toFixed(1)} bps` : "—";
    document.getElementById("kpi-spread-tag").textContent = data.spread_medio_fallback ? "sem estoque" : "pond. estoque";
    document.getElementById("kpi-n-ativos").textContent = data.n_ativos || "—";
    document.getElementById("dados-ate").textContent = `Dados até: ${fmtData(data.data_referencia)}`;

    document.getElementById("kpi-duration-tag").textContent = data.duration_ponderada_fallback ? "sem estoque" : "pond. estoque";
    document.getElementById("kpi-duration").textContent =
      data.duration_media_ponderada !== null ? `${data.duration_media_ponderada.toFixed(2)}a` : "—";

    const deltaEl = document.getElementById("kpi-variacao");
    if (data.variacao_bps === null || data.variacao_bps === undefined) {
      deltaEl.textContent = "sem dado suficiente pra comparar";
      deltaEl.className = "kpi-delta flat";
    } else {
      deltaEl.textContent = `${fmtBps(data.variacao_bps)} vs. ${fmtData(data.data_comparacao)}`;
      deltaEl.className = "kpi-delta " + (data.variacao_bps > 0.05 ? "up" : data.variacao_bps < -0.05 ? "down" : "flat");
    }

    document.getElementById("nota-base-comparacao").textContent =
      data.data_comparacao ? `Base de comparação: ${currentBase} (${fmtData(data.data_comparacao)})` : `Base de comparação: ${currentBase} — sem histórico suficiente ainda.`;
  }

  // ------------------------------------------------------------------
  // Gráfico 1 -- Evolução do spread médio (linha)
  // ------------------------------------------------------------------
  async function loadSeriesChart() {
    const { series } = await fetchJSON("/api/spreads/series", { classe: currentClasse });
    destroyChart("series");
    const ctx = document.getElementById("chart-series").getContext("2d");
    charts.series = new Chart(ctx, {
      type: "line",
      data: {
        labels: series.map((r) => fmtData(r.data)),
        datasets: [{
          label: `Spread médio — ${currentClasse}`,
          data: series.map((r) => r.spread_medio),
          borderColor: "#FF6200",
          backgroundColor: "rgba(255, 98, 0, 0.08)",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.15,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: true, position: "bottom" } },
        scales: {
          y: { title: { display: true, text: "bps" }, grid: { color: "#eee" } },
          x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } },
        },
      },
    });
  }

  // ------------------------------------------------------------------
  // Gráfico 2 -- Variação de spreads (bps) x Duration, com aberturas/
  // fechamentos destacados (réplica do gráfico do relatório semanal)
  // ------------------------------------------------------------------
  async function loadMoversAndScatter() {
    const data = await fetchJSON("/api/spreads/movers", { classe: currentClasse, base: currentBase, top: 10, data: visaoDataInput.value || undefined });
    const sub = `Variação de ${fmtData(data.data_comparacao)} até ${fmtData(data.data_referencia)} (${currentBase}) · Fonte: Anbima e Debentures.com`;
    document.getElementById("scatter-sub").textContent = sub;
    document.getElementById("aberturas-sub").textContent = sub;
    document.getElementById("fechamentos-sub").textContent = sub;

    const codigosAbertura = new Set((data.aberturas || []).map((r) => r.codigo));
    const codigosFechamento = new Set((data.fechamentos || []).map((r) => r.codigo));

    const pontosResto = [], pontosAbertura = [], pontosFechamento = [];
    (data.scatter || []).forEach((r) => {
      const ponto = { x: r.duration, y: r.variacao_bps, codigo: r.codigo, nome: r.nome };
      if (codigosAbertura.has(r.codigo)) pontosAbertura.push(ponto);
      else if (codigosFechamento.has(r.codigo)) pontosFechamento.push(ponto);
      else pontosResto.push(ponto);
    });

    destroyChart("scatter");
    const ctx = document.getElementById("chart-scatter").getContext("2d");
    charts.scatter = new Chart(ctx, {
      type: "scatter",
      data: {
        datasets: [
          { label: "Demais ativos", data: pontosResto, backgroundColor: "rgba(150,150,150,0.35)", pointRadius: 3 },
          { label: "Maiores fechamentos", data: pontosFechamento, backgroundColor: "#000000", pointRadius: 5 },
          { label: "Maiores aberturas", data: pontosAbertura, backgroundColor: "#FF6200", pointRadius: 5 },
        ],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: true, position: "bottom" },
          tooltip: {
            callbacks: {
              label: (item) => `${item.raw.codigo} (${item.raw.nome || "sem nome"}): ${fmtBps(item.raw.y)}, duration ${item.raw.x?.toFixed(1)}a`,
            },
          },
        },
        scales: {
          x: { title: { display: true, text: "Duration (anos)" }, grid: { color: "#eee" } },
          y: { title: { display: true, text: "Variação (bps)" }, grid: { color: "#eee" } },
        },
      },
    });

    renderMoversTable("tabela-aberturas", data.aberturas || [], "cell-abertura");
    renderMoversTable("tabela-fechamentos", data.fechamentos || [], "cell-fechamento");
  }

  function renderMoversTable(tableId, rows, cellClass) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    tbody.innerHTML = "";
    if (rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="muted">Sem dado suficiente pra esse período ainda.</td></tr>';
      return;
    }
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.title = "Clique para ver a série histórica deste ativo";
      tr.innerHTML = `
        <td><strong>${r.codigo}</strong></td>
        <td>${r.nome || "—"}</td>
        <td>${r.spread.toFixed(1)}</td>
        <td class="${cellClass}">${fmtBps(r.variacao_bps)}</td>
        <td>${r.duration !== null ? r.duration.toFixed(1) + "a" : "—"}</td>
      `;
      tr.addEventListener("click", () => openDrilldown(r.codigo, r.nome));
      tbody.appendChild(tr);
    });
  }

  // ------------------------------------------------------------------
  // Gráfico 3 -- Evolução da variação de spreads (% da base, barras
  // empilhadas). O STEP entre as barras é a própria base de comparação
  // selecionada (d-1 = últimos 5 dias, MoM = últimos 5 meses etc.) --
  // pedido do Allan, 24/07/2026.
  // ------------------------------------------------------------------
  async function loadDistributionChart() {
    const { snapshots } = await fetchJSON("/api/spreads/movement-distribution", { classe: currentClasse, base: currentBase, data: visaoDataInput.value || undefined });
    document.getElementById("dist-sub").textContent =
      `Composição da base por faixa de variação, últimos ${snapshots.length || 5} períodos em base ${currentBase} · Fonte: Anbima e Debentures.com`;
    destroyChart("distribution");
    const ctx = document.getElementById("chart-distribution").getContext("2d");
    if (!snapshots.length) {
      charts.distribution = null;
      ctx.canvas.parentElement.querySelector(".muted-msg")?.remove();
      const msg = document.createElement("p");
      msg.className = "muted small muted-msg";
      msg.textContent = "Histórico ainda curto demais pra essa base de comparação (precisa de mais dias capturados).";
      ctx.canvas.after(msg);
      return;
    }
    document.querySelector(".muted-msg")?.remove();
    const labels = Object.keys(snapshots[0]).filter((k) => k !== "data" && k !== "data_comparacao" && k !== "n_ativos");
    const cores = ["#1a7f4e", "#8fd4b0", "#ffd9b8", "#a4302a"]; // tightening forte -> widening forte
    charts.distribution = new Chart(ctx, {
      type: "bar",
      data: {
        labels: snapshots.map((s) => fmtData(s.data)),
        datasets: labels.map((label, i) => ({
          label,
          data: snapshots.map((s) => s[label]),
          backgroundColor: cores[i] || "#999",
        })),
      },
      options: {
        responsive: true,
        plugins: { legend: { display: true, position: "bottom" } },
        scales: {
          x: { stacked: true, grid: { display: false } },
          y: { stacked: true, title: { display: true, text: "% da base de ativos" }, grid: { color: "#eee" } },
        },
      },
    });
  }

  // ------------------------------------------------------------------
  // Busca + drill-down de um ativo específico
  // ------------------------------------------------------------------
  let buscaTimer = null;
  buscaInput.addEventListener("input", () => {
    clearTimeout(buscaTimer);
    const q = buscaInput.value.trim();
    if (!q) {
      buscaResultados.style.display = "none";
      return;
    }
    buscaTimer = setTimeout(async () => {
      const { results } = await fetchJSON("/api/spreads/search", { q, classe: currentClasse });
      buscaResultados.innerHTML = "";
      if (results.length === 0) {
        buscaResultados.innerHTML = '<div class="search-item muted">Nada encontrado nesta classe.</div>';
      } else {
        results.forEach((r) => {
          const div = document.createElement("div");
          div.className = "search-item";
          div.innerHTML = `<span><strong>${r.codigo}</strong></span><span class="nome">${r.nome || ""}</span>`;
          div.addEventListener("click", () => {
            openDrilldown(r.codigo, r.nome);
            buscaResultados.style.display = "none";
            buscaInput.value = "";
          });
          buscaResultados.appendChild(div);
        });
      }
      buscaResultados.style.display = "block";
    }, 300);
  });

  async function openDrilldown(codigo, nome) {
    currentDrilldownCodigo = codigo;
    drilldownWrap.style.display = "block";
    document.getElementById("drilldown-titulo").textContent = `${codigo}${nome ? " — " + nome : ""}`;
    drilldownWrap.scrollIntoView({ behavior: "smooth", block: "nearest" });

    const { series } = await fetchJSON("/api/spreads/series", { classe: currentClasse, codigo });
    destroyChart("drilldown");
    const ctx = document.getElementById("chart-drilldown").getContext("2d");
    charts.drilldown = new Chart(ctx, {
      type: "line",
      data: {
        labels: series.map((r) => fmtData(r.data)),
        datasets: [{
          label: `${codigo} — Spread (bps)`,
          data: series.map((r) => r.spread),
          borderColor: "#FF6200",
          backgroundColor: "rgba(255, 98, 0, 0.08)",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.15,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: true, position: "bottom" } },
        scales: {
          y: { title: { display: true, text: "bps" }, grid: { color: "#eee" } },
          x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } },
        },
      },
    });
  }

  btnFecharDrilldown.addEventListener("click", () => {
    drilldownWrap.style.display = "none";
    destroyChart("drilldown");
    currentDrilldownCodigo = null;
  });

  document.addEventListener("click", (e) => {
    if (!buscaResultados.contains(e.target) && e.target !== buscaInput) {
      buscaResultados.style.display = "none";
    }
  });

  // ------------------------------------------------------------------
  // Botão "Detalhes" -- tabela granular (Código+Data) de UM DIA + export
  // CSV (pedido do Allan, 27/07/2026; filtro de data simplificado pra um
  // dia só, no mesmo dia, em vez de "até uma data")
  // ------------------------------------------------------------------
  function detalhesParams() {
    const params = { classe: currentDetalhesClasse };
    if (detalhesDataInput.value) params.data = detalhesDataInput.value;
    return params;
  }

  function atualizarLinkExportar() {
    const usp = new URLSearchParams(detalhesParams());
    btnExportarDetalhes.href = `/api/spreads/detalhes/export?${usp.toString()}`;
  }

  async function loadDetalhes() {
    detalhesTbody.innerHTML = '<tr><td colspan="10" class="muted">Carregando…</td></tr>';
    const data = await fetchJSON("/api/spreads/detalhes", detalhesParams());
    detalhesTbody.innerHTML = "";
    if (data.rows.length === 0) {
      detalhesTbody.innerHTML = '<tr><td colspan="10" class="muted">Nenhum dado encontrado pra esse filtro.</td></tr>';
    } else {
      data.rows.forEach((r) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${r.codigo}</strong></td>
          <td>${fmtPct(r.taxa)}</td>
          <td>${fmtPct(r.pct_pu_par)}</td>
          <td>${fmtNum(r.pu)}</td>
          <td>${fmtData(r.data)}</td>
          <td>${r.indexador || "—"}</td>
          <td>${r.incentivada || "—"}</td>
          <td>${fmtBps(r.spread)}</td>
          <td>${fmtNum(r.estoque, 1)}</td>
          <td>${r.duration !== null ? r.duration.toFixed(1) + "a" : "—"}</td>
        `;
        detalhesTbody.appendChild(tr);
      });
    }

    // Se o front-end não mandou data (primeiro carregamento), o back-end
    // escolheu o dia mais recente disponível pra essa classe -- reflete
    // isso no input, assim o Allan vê (e pode mudar) a data que está
    // olhando em vez do campo ficar vazio.
    if (!detalhesDataInput.value && data.data) detalhesDataInput.value = data.data;

    detalhesContagem.textContent = data.data
      ? `${data.rows.length.toLocaleString("pt-BR")} linha(s) em ${fmtData(data.data)}`
      : "Nenhum dado disponível pra esse filtro.";
    atualizarLinkExportar();
  }

  btnAbrirDetalhes.addEventListener("click", () => {
    detalhesWrap.style.display = "block";
    detalhesWrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
    if (!detalhesCarregados) {
      detalhesCarregados = true;
      loadDetalhes();
    }
  });

  btnFecharDetalhes.addEventListener("click", () => {
    detalhesWrap.style.display = "none";
  });

  detalhesClasseTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      detalhesClasseTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentDetalhesClasse = btn.dataset.classe;
      loadDetalhes();
    });
  });

  detalhesDataInput.addEventListener("change", loadDetalhes);

  // ------------------------------------------------------------------
  // Orquestração / eventos dos toggles da Visão Geral
  // ------------------------------------------------------------------
  async function reloadAll() {
    await Promise.all([loadKPI(), loadSeriesChart(), loadMoversAndScatter(), loadDistributionChart()]);
  }

  classeTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      classeTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentClasse = btn.dataset.classe;
      drilldownWrap.style.display = "none";
      destroyChart("drilldown");
      reloadAll();
    });
  });

  baseTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      baseTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentBase = btn.dataset.base;
      loadKPI();
      loadMoversAndScatter();
      loadDistributionChart();
    });
  });

  // Campo de data da Visão Geral (pedido do Allan, 27/07/2026) -- não mexe
  // no gráfico "Evolução do Spread Médio" (linha de tendência mostra o
  // histórico inteiro de qualquer forma), só nos KPIs/movers/distribuição,
  // que passam a olhar "como se hoje fosse" a data escolhida.
  visaoDataInput.addEventListener("change", () => {
    loadKPI();
    loadMoversAndScatter();
    loadDistributionChart();
  });

  reloadAll();

  // ------------------------------------------------------------------
  // Aba "Emissores" (pedido do Allan, 24/07/2026 -- renomeada de "Marcação
  // Emissores": vai ganhar dado de negociação no futuro, não só spread, e
  // ganhou seleção múltipla: busca por nome em vez de <select> único)
  // ------------------------------------------------------------------
  const secaoTabs = document.querySelectorAll("#secao-tabs .win-btn");
  const painelVisaoGeral = document.getElementById("painel-visao-geral");
  const painelEmissores = document.getElementById("painel-emissores");
  const emissorBusca = document.getElementById("emissor-busca");
  const emissorBuscaResultados = document.getElementById("emissor-busca-resultados");
  const emissorChips = document.getElementById("emissor-chips");
  const emissorClasseTabs = document.querySelectorAll("#emissor-classe-tabs .win-btn");
  const emissorNivelTabs = document.querySelectorAll("#emissor-nivel-tabs .win-btn");
  const emissorVazio = document.getElementById("emissor-vazio");
  const emissorConteudo = document.getElementById("emissor-conteudo");
  const emissorRankingWrap = document.getElementById("emissor-ranking-wrap");
  const emissorRankingDataInput = document.getElementById("emissor-ranking-data");
  const emissorRankingDadosAte = document.getElementById("emissor-ranking-dados-ate");

  let emissoresDisponiveis = [];
  let currentEmissores = []; // selecao multipla, pedido do Allan 24/07/2026
  let currentEmissorClasse = document.querySelector("#emissor-classe-tabs .win-btn.active")?.dataset.classe || "";
  let currentNivel = document.querySelector("#emissor-nivel-tabs .win-btn.active")?.dataset.nivel || "emissor";
  let emissoresCarregados = false;

  secaoTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      secaoTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const secao = btn.dataset.secao;
      painelVisaoGeral.style.display = secao === "visao-geral" ? "block" : "none";
      painelEmissores.style.display = secao === "emissores" ? "block" : "none";
      if (secao === "emissores" && !emissoresCarregados) {
        carregarListaEmissores();
      }
      if (secao === "emissores" && !currentEmissores.length) {
        loadEmissorRanking();
      }
    });
  });

  async function carregarListaEmissores() {
    emissoresCarregados = true;
    const { emissores } = await fetchJSON("/api/spreads/emissores", {});
    emissoresDisponiveis = emissores;
  }

  function renderChips() {
    emissorChips.innerHTML = "";
    currentEmissores.forEach((nome) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = `<span>${nome}</span>`;
      const btnRemover = document.createElement("button");
      btnRemover.type = "button";
      btnRemover.setAttribute("aria-label", `Remover ${nome}`);
      btnRemover.textContent = "×";
      btnRemover.addEventListener("click", () => {
        currentEmissores = currentEmissores.filter((n) => n !== nome);
        renderChips();
        atualizarPainelEmissor();
      });
      chip.appendChild(btnRemover);
      emissorChips.appendChild(chip);
    });
  }

  function atualizarPainelEmissor() {
    if (!currentEmissores.length) {
      emissorVazio.style.display = "block";
      emissorRankingWrap.style.display = "block";
      emissorConteudo.style.display = "none";
      return;
    }
    emissorVazio.style.display = "none";
    emissorRankingWrap.style.display = "none";
    emissorConteudo.style.display = "block";
    reloadEmissor();
  }

  // ------------------------------------------------------------------
  // Ranking B3 vs. Anbima (tela inicial da aba Emissores, pedido do
  // Allan, 27/07/2026) -- ver templates/spreads.html pro HTML e
  // queries.emissor_ranking_diferencas pro cálculo.
  // ------------------------------------------------------------------
  function fmtBpsSimples(v) {
    return v !== null && v !== undefined ? v.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : "—";
  }

  function renderRankingTabela(tableId, rows, cellClass) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    tbody.innerHTML = "";
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="muted">Sem emissor com negócio B3 recente o bastante nessa classe.</td></tr>';
      return;
    }
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${r.emissor}</strong></td>
        <td>${fmtBpsSimples(r.anbima_spread)}</td>
        <td>${fmtBpsSimples(r.b3_spread_7d)}</td>
        <td class="${cellClass}">${fmtBps(r.variacao_bps)}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  async function loadEmissorRanking() {
    const data = await fetchJSON("/api/spreads/emissor/ranking-diferencas", {
      classe: currentEmissorClasse, top: 15, data: emissorRankingDataInput.value || undefined,
    });
    renderRankingTabela("tabela-ranking-aberturas", data.aberturas || [], "cell-abertura");
    renderRankingTabela("tabela-ranking-fechamentos", data.fechamentos || [], "cell-fechamento");
    emissorRankingDadosAte.textContent = `Dados até: ${fmtData(data.data_referencia)}`;
    // Trava o campo pra não deixar escolher além do que existe -- só
    // quando ainda vazio (mesma lógica do campo "Data analisada" da
    // Visão Geral, ver loadKPI()).
    if (!emissorRankingDataInput.value && data.data_referencia) emissorRankingDataInput.max = data.data_referencia;
  }

  emissorRankingDataInput.addEventListener("change", loadEmissorRanking);

  emissorBusca.addEventListener("input", () => {
    const q = emissorBusca.value.trim().toLowerCase();
    if (!q) {
      emissorBuscaResultados.style.display = "none";
      return;
    }
    const disponiveis = emissoresDisponiveis.filter(
      (nome) => nome.toLowerCase().includes(q) && !currentEmissores.includes(nome)
    );
    emissorBuscaResultados.innerHTML = "";
    if (!disponiveis.length) {
      emissorBuscaResultados.innerHTML = '<div class="search-item muted">Nada encontrado.</div>';
    } else {
      disponiveis.slice(0, 15).forEach((nome) => {
        const div = document.createElement("div");
        div.className = "search-item";
        div.innerHTML = `<span>${nome}</span>`;
        div.addEventListener("click", () => {
          currentEmissores.push(nome);
          emissorBusca.value = "";
          emissorBuscaResultados.style.display = "none";
          renderChips();
          atualizarPainelEmissor();
        });
        emissorBuscaResultados.appendChild(div);
      });
    }
    emissorBuscaResultados.style.display = "block";
  });

  document.addEventListener("click", (e) => {
    if (!emissorBuscaResultados.contains(e.target) && e.target !== emissorBusca) {
      emissorBuscaResultados.style.display = "none";
    }
  });

  emissorClasseTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      emissorClasseTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentEmissorClasse = btn.dataset.classe;
      if (currentEmissores.length) {
        loadEmissorChart();
        loadEmissorTaxas();
        // MUDOU (27/07/2026): tabela de negociações agora também filtra
        // por classe (bug corrigido, ver loadEmissorNegociacoes), então
        // precisa recarregar ao trocar de classe igual aos outros dois.
        loadEmissorNegociacoes();
      } else {
        loadEmissorRanking();
      }
    });
  });

  emissorNivelTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      emissorNivelTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentNivel = btn.dataset.nivel;
      if (currentEmissores.length) loadEmissorChart();
    });
  });

  async function reloadEmissor() {
    await Promise.all([
      loadEmissorTabela(), loadEmissorChart(), loadEmissorNoticias(),
      loadEmissorNegociacoes(), loadEmissorTaxas(),
    ]);
  }

  // Cards de taxa no topo da aba (pedido do Allan, 24/07/2026) -- taxa
  // Anbima (ponderada por Estoque, com a média 3M discreta ao lado) e taxa
  // negociada na B3 (ponderada por volume) NUNCA se misturam num mesmo
  // número -- são fontes/conceitos diferentes, ver queries.emissor_taxas.
  async function loadEmissorTaxas() {
    const data = await fetchJSON("/api/spreads/emissor/taxas", { nome: currentEmissores, classe: currentEmissorClasse });

    const anbimaTag = document.getElementById("emissor-taxa-anbima-data");
    const anbimaValor = document.getElementById("emissor-taxa-anbima");
    const anbima3m = document.getElementById("emissor-taxa-anbima-3m");
    if (data.anbima_spread !== null) {
      anbimaValor.textContent = `${data.anbima_spread.toLocaleString("pt-BR", { minimumFractionDigits: 1 })} bps`;
      anbimaTag.textContent = data.anbima_spread_fallback ? `${fmtData(data.anbima_data)} · sem estoque` : fmtData(data.anbima_data);
    } else {
      anbimaValor.textContent = "—";
      anbimaTag.textContent = "";
    }
    anbima3m.textContent = data.anbima_spread_3m !== null
      ? `Média 3M: ${data.anbima_spread_3m.toLocaleString("pt-BR", { minimumFractionDigits: 1 })} bps ${fmtIntervalo(data.anbima_spread_3m_inicio, data.anbima_spread_3m_fim)}`
      : "Média 3M: —";

    const b3Tag = document.getElementById("emissor-taxa-b3-data");
    const b3Valor = document.getElementById("emissor-taxa-b3");
    const b3Sub = document.getElementById("emissor-taxa-b3-sub");
    if (data.b3_spread !== null) {
      b3Valor.textContent = `${data.b3_spread.toLocaleString("pt-BR", { minimumFractionDigits: 1 })} bps`;
      b3Tag.textContent = `${fmtData(data.b3_data)} · ${data.b3_n_negocios} negócio(s)`;
    } else {
      b3Valor.textContent = "—";
      b3Tag.textContent = data.b3_n_negocios > 0 ? fmtData(data.b3_data) : "";
    }
    b3Sub.textContent = data.b3_spread_7d !== null
      ? `Média 7d: ${data.b3_spread_7d.toLocaleString("pt-BR", { minimumFractionDigits: 1 })} bps ${fmtIntervalo(data.b3_spread_7d_inicio, data.b3_spread_7d_fim)}`
      : (data.b3_n_negocios > 0 && data.b3_spread === null ? "Negócios sem spread calculado" : "Média 7d: —");
  }

  async function loadEmissorTabela() {
    document.getElementById("emissor-titulo-tabela").textContent =
      currentEmissores.length === 1 ? `Tickers — ${currentEmissores[0]}` : `Tickers — ${currentEmissores.length} emissores selecionados`;
    const data = await fetchJSON("/api/spreads/emissor", { nome: currentEmissores });
    const tbody = document.querySelector("#tabela-emissor-tickers tbody");
    tbody.innerHTML = "";
    let totalEstoque = 0;
    let temEstoque = false;
    (data.tickers || []).forEach((t) => {
      if (t.estoque !== null && t.estoque !== undefined) {
        totalEstoque += t.estoque;
        temEstoque = true;
      }
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${t.codigo}</strong></td>
        <td>${t.emissor || "—"}</td>
        <td>${t.indexador || "—"}</td>
        <td>${t.classe || "—"}</td>
        <td>${t.incentivada || "—"}</td>
        <td>${t.estoque !== null ? t.estoque.toLocaleString("pt-BR", { maximumFractionDigits: 1 }) : "—"}</td>
      `;
      tbody.appendChild(tr);
    });
    // Totalizador pedido pelo Allan (24/07/2026) -- soma o Estoque de
    // todas as dívidas mostradas na tabela (todos os tickers, de todos os
    // emissores selecionados).
    document.querySelector("#tabela-emissor-total td:last-child").textContent =
      temEstoque ? totalEstoque.toLocaleString("pt-BR", { maximumFractionDigits: 1 }) : "—";
  }

  async function loadEmissorChart() {
    document.getElementById("emissor-titulo-grafico").textContent =
      `Spread ao longo do tempo (${currentNivel === "emissor" ? "nível emissor, pond. estoque" : "nível ticker"})`;
    const data = await fetchJSON("/api/spreads/emissor/series", {
      nome: currentEmissores, classe: currentEmissorClasse, nivel: currentNivel,
    });
    destroyChart("emissor");
    const ctx = document.getElementById("chart-emissor").getContext("2d");

    const seriesList = data.series || [];
    const mercado = data.mercado || [];

    const datasEncontradas = new Set();
    seriesList.forEach((s) => s.pontos.forEach((p) => datasEncontradas.add(p.data)));
    mercado.forEach((p) => datasEncontradas.add(p.data));
    const labels = Array.from(datasEncontradas).sort();

    if (!labels.length) {
      charts.emissor = null;
      ctx.canvas.parentElement.querySelector(".muted-msg")?.remove();
      const msg = document.createElement("p");
      msg.className = "muted small muted-msg";
      msg.textContent = "Nenhum dos emissores selecionados tem ticker na classe escolhida.";
      ctx.canvas.after(msg);
      return;
    }
    document.querySelector("#chart-emissor").parentElement.querySelector(".muted-msg")?.remove();

    const cores = ["#FF6200", "#111111", "#8a5a2b", "#2b6e8a", "#6e2b8a", "#1a7f4e", "#a4302a", "#5c5c9e"];
    const datasets = seriesList.map((s, i) => {
      const porData = Object.fromEntries(s.pontos.map((p) => [p.data, p.spread]));
      return {
        label: s.codigo,
        data: labels.map((d) => (d in porData ? porData[d] : null)),
        borderColor: cores[i % cores.length],
        backgroundColor: "transparent",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.15,
        spanGaps: true,
      };
    });

    const mercadoPorData = Object.fromEntries(mercado.map((p) => [p.data, p.spread_medio]));
    datasets.push({
      label: `Mercado — ${currentEmissorClasse}`,
      data: labels.map((d) => (d in mercadoPorData ? mercadoPorData[d] : null)),
      borderColor: "#bbbbbb",
      backgroundColor: "transparent",
      borderWidth: 1,
      borderDash: [4, 4],
      pointRadius: 0,
      tension: 0.15,
      spanGaps: true,
    });

    charts.emissor = new Chart(ctx, {
      type: "line",
      data: { labels: labels.map(fmtData), datasets },
      options: {
        responsive: true,
        plugins: { legend: { display: true, position: "bottom" } },
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } },
          y: { title: { display: true, text: "bps" }, grid: { color: "#eee" } },
        },
      },
    });
  }

  async function loadEmissorNoticias() {
    const container = document.getElementById("emissor-noticias-lista");
    const sub = document.getElementById("emissor-noticias-sub");
    container.innerHTML = "";
    const data = await fetchJSON("/api/spreads/emissor/noticias", { nome: currentEmissores });
    const empresas = data.empresas || {};
    const nomesLigados = Object.values(empresas).map((e) => e.company_name);
    if (!nomesLigados.length) {
      sub.textContent = "Nenhum emissor selecionado está ligado a uma empresa da cobertura.";
      container.innerHTML = '<p class="muted small">Rode <code>python -m scripts.match_debenture_issuers --apply</code>, ou cadastre um alias com esse nome em Fontes &amp; Empresas.</p>';
      return;
    }
    sub.textContent = `Empresa(s): ${nomesLigados.join(", ")}`;
    const noticias = data.noticias || [];
    if (!noticias.length) {
      container.innerHTML = '<p class="muted small">Nenhuma notícia encontrada ainda pra essa(s) empresa(s).</p>';
      return;
    }
    noticias.forEach((n) => {
      const div = document.createElement("div");
      div.className = "news-item-mini";
      div.innerHTML = `
        <a href="${n.url}" target="_blank" rel="noopener">${n.title}</a>
        <div class="muted small">${n.source_name || ""} · ${fmtData((n.published_at || "").slice(0, 10))}</div>
      `;
      container.appendChild(div);
    });
  }

  // Últimas negociações (negócio a negócio, B3 -- pedido do Allan,
  // 24/07/2026). Filtrado pelos mesmos tickers do(s) emissor(es)
  // selecionados; hoje só aparece coisa pra DEB de verdade (CRI/CRA não
  // têm emissor ligado no cadastro ainda, ver queries.emissor_trades).
  async function loadEmissorNegociacoes() {
    // MUDOU (27/07/2026): passa `classe` -- bug real que o Allan reparou
    // (contagem "9 negócio(s)" do card não batia com o número de linhas
    // aqui embaixo): esta tabela não filtrava por classe, então um
    // emissor com séries em mais de uma classe aparecia aqui inteiro,
    // mesmo com o filtro IPCA+/CDI+ selecionado (ver docstring de
    // queries.emissor_trades).
    const data = await fetchJSON("/api/spreads/emissor/negociacoes", { nome: currentEmissores, classe: currentEmissorClasse });
    const negociacoes = data.negociacoes || [];
    const tbody = document.querySelector("#tabela-emissor-negociacoes tbody");
    const vazio = document.getElementById("emissor-negociacoes-vazio");
    tbody.innerHTML = "";
    vazio.style.display = negociacoes.length ? "none" : "block";
    negociacoes.forEach((n) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${fmtData(n.data_negocio)}</td>
        <td>${n.horario || "—"}</td>
        <td>${n.instrument_type || "—"}</td>
        <td><strong>${n.codigo}</strong></td>
        <td>${n.emissor || "—"}</td>
        <td>${n.indexador || "—"}</td>
        <td>${n.preco !== null ? n.preco.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 4 }) : "—"}</td>
        <td>${n.volume !== null ? n.volume.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—"}</td>
        <td>${n.taxa !== null ? n.taxa.toLocaleString("pt-BR", { minimumFractionDigits: 4, maximumFractionDigits: 4 }) + "%" : "—"}</td>
      `;
      tbody.appendChild(tr);
    });
  }
})();
