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

    // ESTOQUE (09/09/2026). Vem em R$ milhões da captura; acima de mil vira
    // bilhão, porque "1.347,2 mi" é mais difícil de ler do que "R$ 1,35 bi"
    // num cartão que se lê de relance.
    const est = data.estoque_total;
    document.getElementById("kpi-estoque").textContent =
      est === null || est === undefined ? "—"
      : est >= 1000 ? `R$ ${(est / 1000).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} bi`
      : `R$ ${est.toLocaleString("pt-BR", { maximumFractionDigits: 0 })} mi`;
    document.getElementById("kpi-estoque-cobertura").textContent = data.estoque_cobertura || 0;

    // A linha de contexto que sobrou no lugar dos textos que saíram: diz a
    // data-base e contra o que se está comparando, e nada além disso.
    document.getElementById("nota-base").textContent =
      data.data_referencia
        ? `Boletim de ${fmtData(data.data_referencia)}` +
          (data.data_comparacao ? ` · comparado com ${fmtData(data.data_comparacao)} (${currentBase})`
                                : ` · ${currentBase} sem histórico suficiente para comparar`)
        : "Sem dado para a data selecionada.";
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
  // ------------------------------------------------------------------
  // Top 20 aberturas e fechamentos.
  //
  // O gráfico de dispersão (variação x duration) saiu em 09/09/2026 junto
  // com o resto da simplificação -- as duas tabelas respondem a mesma
  // pergunta de forma direta, e o `scatter` do payload deixou de ser lido
  // (a rota continua devolvendo, sem custo extra de consulta).
  // ------------------------------------------------------------------
  async function loadMovers() {
    const data = await fetchJSON("/api/spreads/movers", { classe: currentClasse, base: currentBase, top: 20, data: visaoDataInput.value || undefined });
    const sub = `Variação de ${fmtData(data.data_comparacao)} a ${fmtData(data.data_referencia)} (${currentBase}) · Fonte: ANBIMA e Debentures.com`;
    document.getElementById("aberturas-sub").textContent = sub;
    document.getElementById("fechamentos-sub").textContent = sub;
    renderMoversTable("tabela-aberturas", data.aberturas || [], "cell-abertura");
    renderMoversTable("tabela-fechamentos", data.fechamentos || [], "cell-fechamento");
  }

  function renderMoversTable(tableId, rows, cellClass) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    tbody.innerHTML = "";
    if (rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="muted">Sem dado suficiente para esse período ainda.</td></tr>';
      return;
    }
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.title = "Clique para ver a série histórica deste ativo";
      tr.innerHTML = `
        <td><strong>${r.codigo}</strong></td>
        <td>${r.nome || "—"}</td>
        <td style="text-align:right;">${r.spread.toFixed(1)}</td>
        <td class="${cellClass}" style="text-align:right;">${fmtBps(r.variacao_bps)}</td>
      `;
      tr.addEventListener("click", () => openDrilldown(r.codigo, r.nome));
      tbody.appendChild(tr);
    });
  }

  // ------------------------------------------------------------------
  // Spread por setor, com drill-down (09/09/2026)
  //
  // Três níveis na MESMA tabela: setor -> subsetor -> ticker. O estado do
  // drill-down vive aqui em `setorNivel/setorAtual/subsetorAtual`, não na
  // URL: é navegação dentro de uma tela, e voltar para "todos os setores"
  // tem que ser um clique, não um recarregamento.
  //
  // A ordenação vem pronta do servidor (maior abertura primeiro) -- é a
  // ordem que responde "o que se moveu", que é a pergunta que traz alguém
  // a esta tabela.
  // ------------------------------------------------------------------
  let setorNivel = "setor";
  let setorAtual = null;
  let subsetorAtual = null;

  function renderTrilhaSetor() {
    const trilha = document.getElementById("setor-trilha");
    const col = document.getElementById("setor-col-rotulo");
    if (setorNivel === "setor") {
      trilha.innerHTML = "";
      col.textContent = "Setor";
      return;
    }
    const partes = ['<a href="#" data-nivel="setor">Todos os setores</a>'];
    if (setorNivel === "ticker") {
      partes.push(`<a href="#" data-nivel="subsetor">${setorAtual}</a>`);
      partes.push(`<b>${subsetorAtual}</b>`);
      col.textContent = "Ticker";
    } else {
      partes.push(`<b>${setorAtual}</b>`);
      col.textContent = "Subsetor";
    }
    trilha.innerHTML = "› " + partes.join(" › ");
    trilha.querySelectorAll("a").forEach((a) => {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        const destino = a.dataset.nivel;
        if (destino === "setor") { setorNivel = "setor"; setorAtual = null; subsetorAtual = null; }
        else { setorNivel = "subsetor"; subsetorAtual = null; }
        loadSetor();
      });
    });
  }

  async function loadSetor() {
    const data = await fetchJSON("/api/spreads/por-setor", {
      classe: currentClasse, base: currentBase, data: visaoDataInput.value || undefined,
      nivel: setorNivel, setor: setorAtual || undefined, subsetor: subsetorAtual || undefined,
    });
    renderTrilhaSetor();

    const tbody = document.querySelector("#tabela-setor tbody");
    const vazio = document.getElementById("setor-vazio");
    tbody.innerHTML = "";
    const linhas = data.linhas || [];
    vazio.style.display = linhas.length ? "none" : "";

    linhas.forEach((l) => {
      const tr = document.createElement("tr");
      const podeDescer = setorNivel !== "ticker";
      if (podeDescer) {
        tr.style.cursor = "pointer";
        tr.title = setorNivel === "setor" ? "Abrir os subsetores" : "Ver os tickers";
      } else {
        tr.style.cursor = "pointer";
        tr.title = "Ver a série histórica deste papel";
      }
      const varCls = l.variacao_bps === null ? "" : l.variacao_bps > 0 ? "cell-abertura" : l.variacao_bps < 0 ? "cell-fechamento" : "";
      const rotulo = setorNivel === "ticker"
        ? `<strong>${l.rotulo}</strong>${l.nome ? ` <span class="muted">${l.nome}</span>` : ""}`
        : l.rotulo + (podeDescer ? ' <span class="muted">›</span>' : "");
      tr.innerHTML = `
        <td>${rotulo}</td>
        <td style="text-align:right;">${l.n_ativos}</td>
        <td style="text-align:right;">${l.estoque !== null ? l.estoque.toLocaleString("pt-BR", { maximumFractionDigits: 0 }) : "—"}</td>
        <td style="text-align:right;">${l.spread_medio !== null ? l.spread_medio.toFixed(1) : "—"}</td>
        <td class="${varCls}" style="text-align:right;">${l.variacao_bps !== null ? fmtBps(l.variacao_bps) : "—"}</td>
      `;
      tr.addEventListener("click", () => {
        if (setorNivel === "setor") { setorAtual = l.rotulo; setorNivel = "subsetor"; loadSetor(); }
        else if (setorNivel === "subsetor") { subsetorAtual = l.rotulo; setorNivel = "ticker"; loadSetor(); }
        else { openDrilldown(l.rotulo, l.nome); }
      });
      tbody.appendChild(tr);
    });
  }

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
    await Promise.all([loadKPI(), loadSeriesChart(), loadSetor(), loadMovers()]);
  }

  classeTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      classeTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentClasse = btn.dataset.classe;
      // Trocar de classe volta o drill-down para o topo: os setores de
      // "IPCA + Incentivadas" e "CDI + Tradicionais" não são os mesmos, e
      // ficar dentro de um subsetor que não existe na outra classe daria
      // tabela vazia sem explicação.
      setorNivel = "setor"; setorAtual = null; subsetorAtual = null;
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
      loadSetor();
      loadMovers();
    });
  });

  // Campo de data da Visão Geral (pedido do Allan, 27/07/2026) -- não mexe
  // no gráfico de evolução (a linha mostra o histórico inteiro de qualquer
  // forma), só nos cartões, na tabela de setor e nos movers, que passam a
  // olhar "como se hoje fosse" a data escolhida.
  visaoDataInput.addEventListener("change", () => {
    loadKPI();
    loadSetor();
    loadMovers();
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
  const emissorGrupoBtn = document.getElementById("emissor-grupo-btn");
  const emissorGrupoPainel = document.getElementById("emissor-grupo-painel");
  const emissorGrupoBusca = document.getElementById("emissor-grupo-busca");
  const emissorGrupoLista = document.getElementById("emissor-grupo-lista");
  const emissorNivelTabs = document.querySelectorAll("#emissor-nivel-tabs .win-btn");
  const emissorVazio = document.getElementById("emissor-vazio");
  const emissorConteudo = document.getElementById("emissor-conteudo");

  let emissoresDisponiveis = [];
  let currentEmissores = []; // selecao multipla, pedido do Allan 24/07/2026
  // As duas classes aparecem SEMPRE, lado a lado (11/09/2026) -- não há
  // mais um botão escolhendo uma delas para a aba inteira. A ordem aqui é a
  // ordem na tela: IPCA+ à esquerda, CDI+ à direita.
  const CLASSES_EMISSOR = [
    { classe: "IPCA + Incentivadas", canvas: "chart-emissor-ipca", chave: "emissorIpca" },
    { classe: "CDI + Tradicionais", canvas: "chart-emissor-cdi", chave: "emissorCdi" },
  ];
  let gruposEconomicos = [];
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
    });
  });

  async function carregarListaEmissores() {
    emissoresCarregados = true;
    const [lista, grupos] = await Promise.all([
      fetchJSON("/api/spreads/emissores", {}),
      fetchJSON("/api/spreads/grupos", {}),
    ]);
    emissoresDisponiveis = lista.emissores;
    gruposEconomicos = grupos.grupos || [];
    renderGrupos();
  }

  // ------------------------------------------------------------------
  // GRUPO ECONÔMICO (11/09/2026, pedido do Allan). Escolher um grupo
  // ADICIONA os emissores dele aos chips -- é um atalho de seleção, não um
  // segundo filtro. Por isso não guarda estado próprio: depois de clicar,
  // o que vale são os chips, que continuam editáveis um a um.
  // ------------------------------------------------------------------
  function renderGrupos() {
    const q = (emissorGrupoBusca.value || "").trim().toLowerCase();
    const visiveis = gruposEconomicos.filter((g) => g.grupo.toLowerCase().includes(q));
    emissorGrupoLista.innerHTML = "";
    if (!gruposEconomicos.length) {
      emissorGrupoLista.innerHTML =
        '<div class="search-item muted">Nenhum grupo econômico preenchido na taxonomia.</div>';
      return;
    }
    if (!visiveis.length) {
      emissorGrupoLista.innerHTML = '<div class="search-item muted">Nada encontrado.</div>';
      return;
    }
    visiveis.slice(0, 60).forEach((g) => {
      // "já selecionado" = TODOS os emissores do grupo já estão nos chips.
      // Um grupo parcialmente selecionado continua clicável para completar.
      const todos = g.emissores.every((n) => currentEmissores.includes(n));
      const div = document.createElement("div");
      div.className = "search-item" + (todos ? " muted" : "");
      div.innerHTML = `<span>${g.grupo}</span>` +
        `<span class="muted small">${g.n_emissores} emissor${g.n_emissores === 1 ? "" : "es"}` +
        `${todos ? " · já selecionado" : ""}</span>`;
      div.addEventListener("click", () => {
        g.emissores.forEach((n) => {
          if (!currentEmissores.includes(n)) currentEmissores.push(n);
        });
        renderChips();
        renderGrupos();
        atualizarPainelEmissor();
      });
      emissorGrupoLista.appendChild(div);
    });
  }

  emissorGrupoBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const abrindo = emissorGrupoPainel.style.display === "none";
    emissorGrupoPainel.style.display = abrindo ? "block" : "none";
    if (abrindo) {
      renderGrupos();
      emissorGrupoBusca.focus();
    }
  });
  emissorGrupoBusca.addEventListener("input", renderGrupos);
  emissorGrupoBusca.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", (e) => {
    if (!emissorGrupoPainel.contains(e.target) && e.target !== emissorGrupoBtn) {
      emissorGrupoPainel.style.display = "none";
    }
  });

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
      emissorConteudo.style.display = "none";
      return;
    }
    emissorVazio.style.display = "none";
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

  // `loadEmissorRanking` e `renderRankingTabela` saíram em 09/09/2026 junto
  // com o bloco de ranking B3 x Anbima da tela inicial -- ver o comentário
  // no template. A rota e a consulta continuam existindo; o que deixou de
  // acontecer é a chamada automática ao abrir a aba.


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

  emissorNivelTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      emissorNivelTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentNivel = btn.dataset.nivel;
      if (currentEmissores.length) loadEmissorCharts();
    });
  });

  async function reloadEmissor() {
    await Promise.all([
      loadEmissorTabela(), loadEmissorCharts(), loadEmissorNoticias(),
      loadEmissorNegociacoes(), loadEmissorCards(),
    ]);
  }

  // ------------------------------------------------------------------
  // SEIS CARDS, TRÊS POR CLASSE (11/09/2026, pedido do Allan): spread (com
  // a média 3M embaixo), duration média e estoque total. IPCA+Incentivadas
  // à esquerda, CDI+Tradicionais à direita.
  //
  // As duas colunas NUNCA se somam. As referências são diferentes (NTN-B e
  // DI) e uma média entre elas não significaria nada -- ficam lado a lado
  // para comparar, não para juntar. O servidor manda as duas prontas numa
  // resposta só (queries.emissor_cards), então esta função não faz conta
  // nenhuma: o mesmo número tem que sair de um lugar só.
  //
  // O card "SPREAD NEGOCIADO (B3)" saiu daqui; o negócio a negócio continua
  // na tabela embaixo.
  // ------------------------------------------------------------------
  async function loadEmissorCards() {
    const alvo = document.getElementById("emissor-cards-duplo");
    const data = await fetchJSON("/api/spreads/emissor/cards", { nome: currentEmissores });
    alvo.innerHTML = "";
    (data.classes || []).forEach((c) => {
      const col = document.createElement("div");
      col.className = "cards-classe";
      const n = c.n_ativos || 0;
      const media3m = c.spread_3m !== null && c.spread_3m !== undefined
        ? `Média 3M: ${fmtBpsSimples(c.spread_3m)} bps ${fmtIntervalo(c.spread_3m_inicio, c.spread_3m_fim)}`
        : "Média 3M: —";
      col.innerHTML = `
        <h3 class="cards-classe-titulo">${c.classe}
          <span class="muted small">${n} ativo${n === 1 ? "" : "s"} precificado${n === 1 ? "" : "s"}</span>
        </h3>
        <div class="kpi-row">
          <div class="kpi-card kpi-highlight">
            <span class="kpi-label">SPREAD ANBIMA <span class="kpi-tag">${c.data ? fmtData(c.data) : ""}</span></span>
            <span class="kpi-value">${c.spread !== null && c.spread !== undefined ? fmtBpsSimples(c.spread) + " bps" : "—"}</span>
            <div class="muted small">${media3m}</div>
          </div>
          <div class="kpi-card">
            <span class="kpi-label">DURATION MÉDIA</span>
            <span class="kpi-value">${c.duration !== null && c.duration !== undefined ? fmtNum(c.duration, 2) + " anos" : "—"}</span>
            <div class="muted small">${c.duration_fallback ? "Média simples (sem estoque cruzado)" : "Ponderada pelo estoque"}</div>
          </div>
          <div class="kpi-card">
            <span class="kpi-label">ESTOQUE TOTAL</span>
            <span class="kpi-value">${c.estoque !== null && c.estoque !== undefined ? fmtNum(c.estoque, 1) : "—"}</span>
            <div class="muted small">R$ milhões · soma dos papéis</div>
          </div>
        </div>
      `;
      alvo.appendChild(col);
    });
    if (!alvo.children.length) {
      alvo.innerHTML = '<p class="muted small">Sem papéis precificados para os emissores selecionados.</p>';
    }
  }

  async function loadEmissorTabela() {
    document.getElementById("emissor-titulo-tabela").textContent =
      currentEmissores.length === 1 ? `Tickers — ${currentEmissores[0]}` : `Tickers — ${currentEmissores.length} emissores selecionados`;
    const data = await fetchJSON("/api/spreads/emissor", { nome: currentEmissores });
    const tbody = document.querySelector("#tabela-emissor-tickers tbody");
    tbody.innerHTML = "";

    // AGRUPADO POR CLASSE (11/09/2026, pedido do Allan): "para ficar as
    // semelhantes juntas". O backend já devolve as linhas ordenadas por
    // classe e os totais por grupo -- aqui é só desenhar as quebras.
    // Somar no navegador daria um número que poderia divergir do card da
    // Visão Geral por arredondamento; o total vem pronto de lá.
    const totalPorClasse = {};
    (data.totais || []).forEach((t) => { totalPorClasse[t.classe] = t; });

    const num = (v, casas) =>
      v === null || v === undefined
        ? "—"
        : v.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });

    let classeAtual = null;
    (data.tickers || []).forEach((t) => {
      const classe = t.classe || "Sem classificação";
      if (classe !== classeAtual) {
        if (classeAtual !== null) tbody.appendChild(linhaTotal(classeAtual));
        classeAtual = classe;
        const cab = document.createElement("tr");
        cab.className = "grupo-classe";
        cab.innerHTML = `<td colspan="9"><strong>${classe}</strong></td>`;
        tbody.appendChild(cab);
      }
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${t.codigo}</strong></td>
        <td>${t.emissor || "—"}</td>
        <td>${t.indexador || "—"}</td>
        <td>${t.incentivada || "—"}</td>
        <td>${t.data_emissao ? fmtData(t.data_emissao) : "—"}</td>
        <td>${t.taxa_emissao || "—"}</td>
        <td style="text-align:right;">${num(t.duration, 2)}</td>
        <td style="text-align:right;">${num(t.estoque, 1)}</td>
        <td>${t.data_estoque ? fmtData(t.data_estoque) : "—"}</td>
      `;
      tbody.appendChild(tr);
    });
    if (classeAtual !== null) tbody.appendChild(linhaTotal(classeAtual));

    function linhaTotal(classe) {
      const t = totalPorClasse[classe] || {};
      const tr = document.createElement("tr");
      tr.className = "total-classe";
      tr.innerHTML = `
        <td colspan="7" style="text-align:right;font-weight:700;">
          Total ${classe} · ${t.n_ativos || 0} ativo${(t.n_ativos || 0) === 1 ? "" : "s"}
        </td>
        <td style="text-align:right;font-weight:700;">${num(t.estoque, 1)}</td>
        <td></td>
      `;
      return tr;
    }
  }

  // ------------------------------------------------------------------
  // UM GRÁFICO POR CLASSE, LADO A LADO (11/09/2026). Mesma razão dos cards:
  // as duas séries não compartilham escala nem referência, então duas caixas
  // separadas dizem a verdade que uma caixa só esconderia.
  //
  // Emissor sem papel numa das classes não vira linha naquele lado -- é
  // informação, não defeito, e o gráfico diz isso com todas as letras em vez
  // de ficar em branco. Foi exatamente o que confundiu em 11/09/2026: com
  // três CPFLs selecionadas apareceu uma linha só, porque as outras duas não
  // têm papel na classe que estava escolhida no botão.
  // ------------------------------------------------------------------
  async function loadEmissorCharts() {
    await Promise.all(CLASSES_EMISSOR.map((c) => desenharGraficoClasse(c)));
  }

  async function desenharGraficoClasse({ classe, canvas, chave }) {
    const data = await fetchJSON("/api/spreads/emissor/series", {
      nome: currentEmissores, classe, nivel: currentNivel,
    });
    destroyChart(chave);
    const el = document.getElementById(canvas);
    const ctx = el.getContext("2d");
    const card = el.parentElement;
    card.querySelector(".muted-msg")?.remove();

    const seriesList = data.series || [];
    const mercado = data.mercado || [];

    const datasEncontradas = new Set();
    seriesList.forEach((s) => s.pontos.forEach((p) => datasEncontradas.add(p.data)));
    mercado.forEach((p) => datasEncontradas.add(p.data));
    const labels = Array.from(datasEncontradas).sort();

    const sufixo = chave === "emissorIpca" ? "ipca" : "cdi";
    const sub = document.getElementById(`emissor-sub-grafico-${sufixo}`);
    const semLinha = currentEmissores.filter(
      (nome) => !seriesList.some((s) => s.codigo === nome)
    );
    if (currentNivel === "emissor" && semLinha.length && labels.length) {
      sub.textContent = `Fonte: Anbima e Debentures.com · sem papel nesta classe: ${semLinha.join(", ")}`;
    } else {
      sub.textContent = "Fonte: Anbima e Debentures.com";
    }

    if (!labels.length) {
      charts[chave] = null;
      el.style.display = "none";
      const msg = document.createElement("p");
      msg.className = "muted small muted-msg";
      msg.textContent = currentEmissores.length === 1
        ? `${currentEmissores[0]} não tem papel precificado nesta classe.`
        : "Nenhum dos emissores selecionados tem papel precificado nesta classe.";
      card.appendChild(msg);
      return;
    }
    el.style.display = "";

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
      label: "Mercado (mesma classe)",
      data: labels.map((d) => (d in mercadoPorData ? mercadoPorData[d] : null)),
      borderColor: "#bbbbbb",
      backgroundColor: "transparent",
      borderWidth: 1,
      borderDash: [4, 4],
      pointRadius: 0,
      tension: 0.15,
      spanGaps: true,
    });

    charts[chave] = new Chart(ctx, {
      type: "line",
      data: { labels: labels.map(fmtData), datasets },
      options: {
        responsive: true,
        plugins: { legend: { display: true, position: "bottom" } },
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
          y: { title: { display: true, text: "bps" }, grid: { color: "#eee" } },
        },
      },
    });
  }

  // ------------------------------------------------------------------
  // NOTÍCIAS EM DOIS BLOCOS (11/09/2026, pedido do Allan): "Do emissor" e
  // "Do setor".
  //
  // POR QUE O SEGUNDO BLOCO EXISTE. O painel só sabia buscar por empresa da
  // cobertura editorial, e emissor sem esse vínculo ficava com o painel
  // vazio -- foi o que aconteceu com as três CPFLs. O setor vem da taxonomia
  // (Debenture.setor), que está preenchida para quase todo ticker, então há
  // contexto para mostrar mesmo sem match de empresa.
  // ------------------------------------------------------------------
  function renderNoticias(container, noticias, vazio) {
    container.innerHTML = "";
    if (!noticias.length) {
      container.innerHTML = `<p class="muted small">${vazio}</p>`;
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

  async function loadEmissorNoticias() {
    const sub = document.getElementById("emissor-noticias-sub");
    const listaEmissor = document.getElementById("emissor-noticias-lista");
    const listaSetor = document.getElementById("emissor-noticias-setor-lista");
    const tituloSetor = document.getElementById("emissor-noticias-setor-titulo");

    const data = await fetchJSON("/api/spreads/emissor/noticias", { nome: currentEmissores });
    const nomesLigados = Object.values(data.empresas || {}).map((e) => e.company_name);
    const setores = data.setores || [];

    sub.textContent = nomesLigados.length
      ? `Empresa(s) na cobertura: ${nomesLigados.join(", ")}`
      : "Nenhum emissor selecionado está ligado a uma empresa da cobertura editorial.";

    renderNoticias(
      listaEmissor, data.noticias || [],
      nomesLigados.length
        ? "Nenhuma notícia encontrada ainda para essa(s) empresa(s)."
        : 'Sem vínculo com a cobertura. Rode <code>python -m scripts.match_debenture_issuers --apply</code>, ou cadastre um alias em Fontes &amp; Empresas.'
    );

    tituloSetor.textContent = setores.length ? `Do setor — ${setores.join(", ")}` : "Do setor";
    renderNoticias(
      listaSetor, data.noticias_setor || [],
      setores.length
        ? "Nenhuma notícia etiquetada com esse(s) setor(es)."
        : "Os emissores selecionados estão sem setor preenchido na taxonomia."
    );
  }

  // Últimas negociações (negócio a negócio, B3 -- pedido do Allan,
  // 24/07/2026). Filtrado pelos mesmos tickers do(s) emissor(es)
  // selecionados; hoje só aparece coisa pra DEB de verdade (CRI/CRA não
  // têm emissor ligado no cadastro ainda, ver queries.emissor_trades).
  async function loadEmissorNegociacoes() {
    // SEM FILTRO DE CLASSE (11/09/2026): o botão que escolhia uma classe
    // para a aba inteira saiu da tela. Em 27/07/2026 esta tabela passou a
    // filtrar por classe porque a contagem do card não batia com as linhas
    // daqui -- aquele card não existe mais, e esta é uma LISTAGEM, não uma
    // média: cada linha é um negócio isolado, com seu indexador na coluna.
    const data = await fetchJSON("/api/spreads/emissor/negociacoes", { nome: currentEmissores });
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
