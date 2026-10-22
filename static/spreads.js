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

  const inicioInput = document.getElementById("visao-inicio");

  let currentClasse = document.querySelector("#classe-tabs .win-btn.active")?.dataset.classe || "";
  let currentBase = document.querySelector("#base-tabs .win-btn.active")?.dataset.base || "WoW";

  // RECORTE DA PÁGINA (24/09/2026). Setor, subsetor e grupo econômico valem
  // para TODA a Visão Geral. Um objeto só, montado num lugar só
  // (`paramsComuns`), para nenhum bloco ficar mostrando um recorte
  // diferente do bloco de cima -- erro que ninguém percebe olhando a tela.
  const filtros = { setor: [], subsetor: [], grupo: [] };
  const BASE_PERSONALIZADA = "Personalizado";

  function baseValida() {
    // "Personalizado" sem data escolhida ainda não é uma comparação: a tela
    // espera em vez de pedir ao servidor algo que ele recusaria com 400.
    return currentBase !== BASE_PERSONALIZADA || !!inicioInput.value;
  }

  function paramsComuns(extras) {
    return Object.assign({
      classe: currentClasse,
      base: currentBase,
      inicio: currentBase === BASE_PERSONALIZADA ? (inicioInput.value || undefined) : undefined,
      data: visaoDataInput.value || undefined,
      setor: filtros.setor,
      subsetor: filtros.subsetor,
      grupo: filtros.grupo,
    }, extras || {});
  }
  let currentDrilldownCodigo = null;

  let currentDetalhesClasse = document.querySelector("#detalhes-classe-tabs .win-btn.active")?.dataset.classe || "";
  let detalhesCarregados = false;

  const charts = {}; // nome -> instancia Chart.js (destruída/recriada a cada atualização)

  // NÚMEROS EM PT-BR EM TODA A ABA (21/09/2026). A Visão Geral misturava
  // "18.6 bps" e "6.19a" (ponto) com "R$ 102,91 bi" e "63,2%" (vírgula) na
  // mesma tela -- num material de research brasileiro, é o que mais parece
  // erro. `num1` é o formato padrão de spread (uma casa).
  function num1(v) {
    return v === null || v === undefined ? "—"
      : v.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  function fmtBps(v) {
    if (v === null || v === undefined) return "—";
    const s = v > 0 ? "+" : "";
    return `${s}${num1(v)} bps`;
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
    const data = await fetchJSON("/api/spreads/summary", paramsComuns());
    // Trava o campo de data pra não deixar escolher além do que existe na
    // base (sem dado futuro pra mostrar) -- só quando o campo ainda está
    // vazio (nesse caso `data.data_referencia` é sempre a última
    // disponível de verdade); se já tem uma data escolhida, a resposta
    // reflete ESSA data, não a mais recente, então não mexe no max.
    document.getElementById("kpi-spread").textContent = data.spread_medio !== null ? `${num1(data.spread_medio)} bps` : "—";
    document.getElementById("kpi-spread-tag").textContent = data.spread_medio_fallback ? "sem estoque" : "pond. estoque";
    document.getElementById("kpi-n-ativos").textContent =
      data.n_ativos ? data.n_ativos.toLocaleString("pt-BR") : "—";
    document.getElementById("dados-ate").textContent = `Dados até: ${fmtData(data.data_referencia)}`;

    document.getElementById("kpi-duration-tag").textContent = data.duration_ponderada_fallback ? "sem estoque" : "pond. estoque";
    document.getElementById("kpi-duration").textContent =
      data.duration_media_ponderada !== null
        ? data.duration_media_ponderada.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : "—";

    // Delta de média e mediana: mesma regra de cor (abriu = laranja).
    const escreverDelta = (id, v) => {
      const el = document.getElementById(id);
      if (v === null || v === undefined) {
        el.textContent = "sem dado suficiente pra comparar";
        el.className = "kpi-delta flat";
      } else {
        el.textContent = `${fmtBps(v)} vs. ${fmtData(data.data_comparacao)}`;
        el.className = "kpi-delta " + (v > 0.05 ? "up" : v < -0.05 ? "down" : "flat");
      }
    };
    escreverDelta("kpi-variacao", data.variacao_bps);
    // MEDIANA (22/10/2026): sem peso, mesma base de papéis do spread médio.
    document.getElementById("kpi-mediana").textContent =
      data.spread_mediana !== null && data.spread_mediana !== undefined ? `${num1(data.spread_mediana)} bps` : "—";
    escreverDelta("kpi-variacao-mediana", data.variacao_mediana_bps);

    // ESTOQUE (09/09/2026). Vem em R$ milhões da captura; acima de mil vira
    // bilhão, porque "1.347,2 mi" é mais difícil de ler do que "R$ 1,35 bi"
    // num cartão que se lê de relance.
    const est = data.estoque_total;
    document.getElementById("kpi-estoque").textContent =
      est === null || est === undefined ? "—"
      : est >= 1000 ? `R$ ${(est / 1000).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} bi`
      : `R$ ${est.toLocaleString("pt-BR", { maximumFractionDigits: 0 })} mi`;

    // A linha de contexto que sobrou no lugar dos textos que saíram: diz a
    // data-base e contra o que se está comparando, e nada além disso.
    // Mora na barra fixa: curta de propósito, para caber numa linha.
    document.getElementById("nota-base").textContent =
      data.data_referencia
        ? `Boletim ${PADRAO_GRAFICO.diaMesCurto(data.data_referencia)}` +
          (data.data_comparacao ? ` vs ${PADRAO_GRAFICO.diaMesCurto(data.data_comparacao)}`
                                : ` · ${currentBase} sem histórico para comparar`)
        : "Sem dado para a data selecionada.";
    escreverFonte("kpi-nota", "Spread médio e duration ponderados por estoque; mediana sem ponderação. Fonte: Anbima, Debentures.com e Itaú BBA",
                  data.data_referencia);
  }

  // ------------------------------------------------------------------
  // Gráfico 1 -- Evolução do spread médio (linha)
  // ------------------------------------------------------------------
  async function loadSeriesChart() {
    // Eixo x (22/10/2026): termina na data de referência ("Dados até" ou
    // fim do "Período") e, no Personalizado, começa no início do período.
    const { series } = await fetchJSON("/api/spreads/series", {
      classe: currentClasse, setor: filtros.setor, subsetor: filtros.subsetor, grupo: filtros.grupo,
      inicio: currentBase === BASE_PERSONALIZADA ? (inicioInput.value || undefined) : undefined,
      fim: visaoDataInput.value || undefined,
    });
    destroyChart("series");
    const ctx = document.getElementById("chart-series").getContext("2d");
    // LABELS EM ISO, NÃO FORMATADOS (11/09/2026). É o que permite ao eixo
    // mostrar "Mar-26" e à dica de ferramenta mostrar "15/03/2026" a partir
    // do MESMO dado -- ver PADRAO_GRAFICO.eixoData em static/chart-padrao.js.
    const datasIso = series.map((r) => r.data);
    escreverFonte("fonte-series", "Fonte: Anbima, Debentures.com e Itaú BBA", datasIso[datasIso.length - 1]);
    charts.series = new Chart(ctx, {
      type: "line",
      data: {
        labels: datasIso,
        datasets: [{
          label: "Spread médio",
          data: series.map((r) => r.spread_medio),
          borderColor: "#FF6200",
          backgroundColor: "rgba(255, 98, 0, 0.08)",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.15,
          fill: true,
        }, {
          // MEDIANA (22/10/2026, pedido do Allan): sem peso por estoque.
          // Preto e tracejada -- a média continua sendo a linha principal.
          label: "Mediana",
          data: series.map((r) => r.spread_mediana),
          borderColor: "#111111",
          backgroundColor: "#111111",
          borderWidth: 1.5,
          borderDash: [5, 3],
          pointRadius: 0,
          tension: 0.15,
          fill: false,
        }],
        // A linha "Base comparável" SAIU DO GRÁFICO (01/10/2026, pedido do
        // Allan: "vamos ter que pensar melhor nessa métrica"). O servidor
        // continua devolvendo `spread_comparavel` em /api/spreads/series
        // (queries._serie_base_comparavel, coberta por
        // tests/test_base_comparavel.py) -- só não é desenhada.
      },
      options: PADRAO_GRAFICO.eixoData(datasIso, {
        responsive: true,
        // Uma dica por data com as duas linhas (média e mediana).
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => ` ${item.dataset.label}: ${item.raw === null ? "—" : num1(item.raw) + " bps"}`,
            },
          },
        },
        scales: { y: { title: { display: true, text: "bps" } } },
      }),
    });
    // Período curto (Personalizado de poucas semanas): uma marca por mês
    // deixaria o eixo com um rótulo só. Até ~3 meses, marca por dia
    // ("15-out"), com no máximo 10 rótulos.
    if (datasIso.length && datasIso.length <= 66) {
      Object.assign(charts.series.options.scales.x.ticks, {
        autoSkip: true, maxTicksLimit: 10,
        callback: (valor, indice) => PADRAO_GRAFICO.diaMes(datasIso[indice]),
      });
      charts.series.update("none");
    }
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
  // TOP 10 COM ALTERNÂNCIA (21/09/2026). Eram duas tabelas de 20 linhas
  // (~800 px) repetindo o que a dispersão já destaca. Agora é uma tabela de
  // 10, e o botão escolhe o lado. A resposta do servidor fica guardada:
  // trocar de lado não é uma consulta nova.
  let moversDados = null;
  let moversLado = "aberturas";

  function renderMovers() {
    if (!moversDados) return;
    const aberturas = moversLado === "aberturas";
    document.getElementById("titulo-movers").textContent =
      `Top 10 ${aberturas ? "Aberturas" : "Fechamentos"}`;
    renderMoversTable("tabela-movers",
      (aberturas ? moversDados.aberturas : moversDados.fechamentos) || [],
      aberturas ? "cell-abertura" : "cell-fechamento");
  }

  async function loadMovers() {
    const data = await fetchJSON("/api/spreads/movers", paramsComuns({ top: 10 }));
    moversDados = data;
    document.getElementById("movers-sub").textContent =
      `bps · variação de ${fmtData(data.data_comparacao)} a ${fmtData(data.data_referencia)} (${currentBase}) · clique para ver a série`;
    escreverFonte("fonte-movers", "Fonte: Anbima, Debentures.com e Itaú BBA", data.data_referencia);
    renderMovers();
  }

  document.querySelectorAll("#movers-tabs .win-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#movers-tabs .win-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      moversLado = btn.dataset.lado;
      renderMovers();
    });
  });

  function renderMoversTable(tableId, rows, cellClass) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    if (tbody.contains(drilldownWrap)) recolherDrilldown();
    tbody.innerHTML = "";
    if (rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="muted">Sem dado suficiente para esse período ainda.</td></tr>';
      return;
    }
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.title = "Clique para ver a série histórica deste ativo";
      // Nome sem o sufixo societário: ganha espaço e evita reticências.
      const nomeCurto = (r.nome || "—").replace(/\s*-?\s*(S\.?\s?\/?\s?A\.?|LTDA\.?)\s*$/i, "");
      tr.innerHTML = `
        <td><strong>${r.codigo}</strong></td>
        <td class="col-texto" title="${r.nome || ""}">${nomeCurto}</td>
        <td>${num1(r.spread)}</td>
        <td class="${cellClass}">${fmtBps(r.variacao_bps)}</td>
      `;
      tr.addEventListener("click", () => openDrilldown(r.codigo, r.nome, tr));
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
  // QUATRO NÍVEIS (11/09/2026, pedido do Allan): setor -> subsetor ->
  // emissor -> ticker. O emissor no meio responde "quem puxou isso": sem
  // ele, abrir um subsetor despejava todos os papéis de todas as empresas
  // de uma vez, e um subsetor movido por uma emissora só ficava
  // indistinguível de um movido pelo setor inteiro.
  const NIVEIS_SETOR = ["setor", "subsetor", "emissor", "ticker"];
  const ROTULO_COLUNA = {
    setor: "Setor", subsetor: "Subsetor", emissor: "Emissor", ticker: "Ticker",
  };
  let setorNivel = "setor";
  let setorAtual = null;
  let subsetorAtual = null;
  let emissorAtual = null;

  // Volta para um nível acima, limpando o que fica abaixo dele. Guardar um
  // `subsetorAtual` de uma navegação anterior seria o jeito silencioso de
  // errar: a tela diria "todos os setores" e a consulta continuaria filtrada.
  function irParaNivel(destino) {
    setorNivel = destino;
    const i = NIVEIS_SETOR.indexOf(destino);
    if (i <= 0) setorAtual = null;
    if (i <= 1) subsetorAtual = null;
    if (i <= 2) emissorAtual = null;
    loadSetor();
  }

  function renderTrilhaSetor() {
    const trilha = document.getElementById("setor-trilha");
    const col = document.getElementById("setor-col-rotulo");
    col.textContent = ROTULO_COLUNA[setorNivel];
    if (setorNivel === "setor") {
      trilha.innerHTML = "";
      return;
    }
    // A trilha é montada a partir do caminho já percorrido: cada nível
    // acima do atual vira link, e o atual vira texto em negrito.
    const caminho = [
      { nivel: "setor", texto: "Todos os setores" },
      { nivel: "subsetor", texto: setorAtual },
      { nivel: "emissor", texto: subsetorAtual },
      { nivel: "ticker", texto: emissorAtual },
    ];
    const ate = NIVEIS_SETOR.indexOf(setorNivel);
    const partes = caminho.slice(0, ate + 1).map((p, i) =>
      i === ate
        ? `<b>${p.texto}</b>`
        : `<a href="#" data-nivel="${p.nivel}">${p.texto}</a>`
    );
    trilha.innerHTML = "› " + partes.join(" › ");
    trilha.querySelectorAll("a").forEach((a) => {
      a.addEventListener("click", (ev) => {
        ev.preventDefault();
        irParaNivel(a.dataset.nivel);
      });
    });
  }

  // ------------------------------------------------------------------
  // HALTERE (21/09/2026, item 4 do Allan): bolinha VAZIA na média dos
  // últimos 3 meses, CHEIA no spread de hoje, traço entre as duas --
  // laranja se o grupo abriu contra a própria média, preto se fechou.
  // SVG direto na célula, sem Chart.js: são dezenas de linhas, e um gráfico
  // por linha seria pesado para algo que é só dois pontos e um traço.
  // ------------------------------------------------------------------
  function escalaDoHaltere(linhas) {
    const valores = [];
    linhas.forEach((l) => {
      if (l.spread_3m !== null && l.spread_3m !== undefined) valores.push(l.spread_3m);
      if (l.spread_medio !== null && l.spread_medio !== undefined) valores.push(l.spread_medio);
    });
    if (!valores.length) return null;
    let min = Math.min(...valores);
    let max = Math.max(...valores);
    if (max - min < 1) { min -= 1; max += 1; }   // grupo único ou tudo igual
    const folga = (max - min) * 0.06;
    return { min: min - folga, max: max + folga };
  }

  function haltere(media3m, hoje, escala) {
    if (!escala || media3m === null || media3m === undefined || hoje === null || hoje === undefined) {
      return '<span class="muted">—</span>';
    }
    const L = 170, H = 16, R = 4.5;
    const x = (v) => R + ((v - escala.min) / (escala.max - escala.min)) * (L - 2 * R);
    const x3m = x(media3m), xh = x(hoje);
    const abriu = hoje > media3m;
    const cor = abriu ? "#FF6200" : "#111111";
    const dif = hoje - media3m;
    const dica = `Média 3M: ${num1(media3m)} bps → hoje: ${num1(hoje)} bps (${dif > 0 ? "+" : ""}${num1(dif)})`;
    // Zero da escala, quando ela atravessa o zero: referência discreta.
    const zero = escala.min < 0 && escala.max > 0
      ? `<line x1="${x(0)}" y1="1" x2="${x(0)}" y2="${H - 1}" stroke="#d0d0d0" stroke-width="1"/>` : "";
    return `<svg width="${L}" height="${H}" viewBox="0 0 ${L} ${H}" role="img" aria-label="${dica}">
      <title>${dica}</title>
      <line x1="${R}" y1="${H / 2}" x2="${L - R}" y2="${H / 2}" stroke="#ececec" stroke-width="1"/>
      ${zero}
      <line x1="${x3m}" y1="${H / 2}" x2="${xh}" y2="${H / 2}" stroke="${cor}" stroke-width="2.5"/>
      <circle cx="${x3m}" cy="${H / 2}" r="${R - 0.5}" fill="#ffffff" stroke="#7a7a7a" stroke-width="1.3"/>
      <circle cx="${xh}" cy="${H / 2}" r="${R}" fill="${cor}"/>
    </svg>`;
  }

  async function loadSetor() {
    // Atenção aos dois pares de nomes: `setor`/`subsetor` aqui são o
    // CAMINHO do drill-down; `filtro_*` é o recorte da barra, que vale para
    // a página inteira (ver a rota em spreads_routes.py).
    const data = await fetchJSON("/api/spreads/por-setor", {
      classe: currentClasse, base: currentBase,
      inicio: currentBase === BASE_PERSONALIZADA ? (inicioInput.value || undefined) : undefined,
      data: visaoDataInput.value || undefined,
      nivel: setorNivel, setor: setorAtual || undefined,
      subsetor: subsetorAtual || undefined, emissor: emissorAtual || undefined,
      filtro_setor: filtros.setor, filtro_subsetor: filtros.subsetor,
      filtro_grupo: filtros.grupo,
    });
    renderTrilhaSetor();

    const tbody = document.querySelector("#tabela-setor tbody");
    const vazio = document.getElementById("setor-vazio");
    if (tbody.contains(drilldownWrap)) recolherDrilldown();
    tbody.innerHTML = "";
    const linhas = data.linhas || [];
    vazio.style.display = linhas.length ? "none" : "";
    // Uma escala para o nível inteiro: os halteres só são comparáveis entre
    // linhas se todos usarem a mesma régua.
    const escalaHaltere = escalaDoHaltere(linhas);
    // A coluna é só "Variação" (24/09/2026): o QUE ela compara já está dito
    // na barra de filtros e na dica da própria coluna -- repetir "WoW" no
    // cabeçalho quebrava quando a base virava uma data escolhida à mão.
    const colVar = document.getElementById("setor-col-variacao");
    colVar.textContent = "Variação";
    colVar.title = data.data_comparacao
      ? `Contra ${PADRAO_GRAFICO.dataCheia(data.data_comparacao)}`
      : "Sem data de comparação disponível";
    escreverFonte("fonte-setor", "Fonte: Anbima, Debentures.com e Itaú BBA", data.data_referencia);

    linhas.forEach((l) => {
      const tr = document.createElement("tr");
      const podeDescer = setorNivel !== "ticker";
      if (podeDescer) {
        tr.style.cursor = "pointer";
        tr.title = {
          setor: "Abrir os subsetores",
          subsetor: "Ver os emissores",
          emissor: "Ver os tickers",
        }[setorNivel];
      } else {
        tr.style.cursor = "pointer";
        tr.title = "Ver a série histórica deste papel";
      }
      const varCls = l.variacao_bps === null ? "" : l.variacao_bps > 0 ? "cell-abertura" : l.variacao_bps < 0 ? "cell-fechamento" : "";
      const rotulo = setorNivel === "ticker"
        ? `<strong>${l.rotulo}</strong>${l.nome ? ` <span class="muted">${l.nome}</span>` : ""}`
        : l.rotulo + (podeDescer ? ' <span class="muted">›</span>' : "");
      tr.innerHTML = `
        <td class="col-texto">${rotulo}</td>
        <td>${l.n_ativos.toLocaleString("pt-BR")}</td>
        <td>${l.estoque !== null ? l.estoque.toLocaleString("pt-BR", { maximumFractionDigits: 0 }) : "—"}</td>
        <td>${l.duration !== null && l.duration !== undefined
              ? l.duration.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : "—"}</td>
        <td><strong>${num1(l.spread_medio)}</strong></td>
        <td>${num1(l.spread_3m)}</td>
        <td class="col-haltere">${haltere(l.spread_3m, l.spread_medio, escalaHaltere)}</td>
        <td class="${varCls}">${l.variacao_bps !== null ? fmtBps(l.variacao_bps) : "—"}</td>
      `;
      tr.addEventListener("click", () => {
        if (setorNivel === "setor") { setorAtual = l.rotulo; registrarUso("setor", l.rotulo); irParaNivel("subsetor"); }
        else if (setorNivel === "subsetor") { subsetorAtual = l.rotulo; registrarUso("filtro", "Subsetor: " + l.rotulo); irParaNivel("emissor"); }
        else if (setorNivel === "emissor") { emissorAtual = l.rotulo; registrarUso("emissor", l.rotulo); irParaNivel("ticker"); }
        else { openDrilldown(l.rotulo, l.nome, tr); }
      });
      tbody.appendChild(tr);
    });
  }

  // SÉRIE DO PAPEL PERTO DO CLIQUE (22/10/2026, pedido do Allan). Antes o
  // card da série abria no fim da página, longe de onde se clicou. Agora:
  //  - clique numa LINHA de tabela (ticker da tabela de setor, Top 10):
  //    a série abre numa linha logo abaixo dela, dentro da própria tabela;
  //    clicar de novo no mesmo papel fecha;
  //  - clique num GRÁFICO (dispersão, deságios): o card vai para logo
  //    abaixo do bloco do gráfico.
  // `casaDrilldown` guarda o lugar original do card para ele voltar antes
  // de qualquer tabela ser redesenhada (senão some junto com o tbody).
  const casaDrilldown = document.createComment("casa do drill-down");
  drilldownWrap.before(casaDrilldown);

  function recolherDrilldown() {
    drilldownWrap.style.display = "none";
    casaDrilldown.after(drilldownWrap);
    document.querySelectorAll("tr.linha-serie").forEach((l) => l.remove());
    document.querySelectorAll("tr.linha-aberta").forEach((l) => l.classList.remove("linha-aberta"));
    destroyChart("drilldown");
    currentDrilldownCodigo = null;
  }

  function posicionarDrilldown(ancora) {
    if (ancora && ancora.tagName === "TR") {
      const linha = document.createElement("tr");
      linha.className = "linha-serie";
      const td = document.createElement("td");
      td.colSpan = ancora.children.length;
      linha.appendChild(td);
      ancora.after(linha);
      ancora.classList.add("linha-aberta");
      td.appendChild(drilldownWrap);
    } else if (ancora) {
      const bloco = ancora.closest(".emissor-duplo") || ancora.closest(".chart-card");
      (bloco || casaDrilldown).after(drilldownWrap);
    }
  }

  async function openDrilldown(codigo, nome, ancora, classe) {
    // Segundo clique no mesmo papel da mesma linha: fecha.
    const mesmaLinha = ancora && ancora.tagName === "TR" && ancora.classList.contains("linha-aberta");
    recolherDrilldown();
    if (mesmaLinha) return;
    registrarUso("ticker", codigo);
    currentDrilldownCodigo = codigo;
    posicionarDrilldown(ancora);
    drilldownWrap.style.display = "block";
    document.getElementById("drilldown-titulo").textContent = `${codigo}${nome ? " — " + nome : ""}`;
    drilldownWrap.scrollIntoView({ behavior: "smooth", block: "nearest" });

    const { series } = await fetchJSON("/api/spreads/series", { classe: classe || currentClasse, codigo });
    destroyChart("drilldown");
    const ctx = document.getElementById("chart-drilldown").getContext("2d");
    const datasIso = series.map((r) => r.data);
    charts.drilldown = new Chart(ctx, {
      type: "line",
      data: {
        labels: datasIso,
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
      options: PADRAO_GRAFICO.eixoData(datasIso, {
        responsive: true,
        plugins: { legend: { display: true, position: "bottom" } },
        scales: { y: { title: { display: true, text: "bps" } } },
      }),
    });
  }

  btnFecharDrilldown.addEventListener("click", recolherDrilldown);

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
          <td>${r.duration !== null ? num1(r.duration) + " anos" : "—"}</td>
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
  // ------------------------------------------------------------------
  // ABERTURAS E FECHAMENTOS (21/09/2026, pedido do Allan) -- os dois
  // gráficos do relatório semanal, lado a lado. Mesma base nos dois: papéis
  // da classe com duration >= 1 e spread nas duas datas da comparação.
  // Toda a conta é do servidor (queries.movement_distribution e
  // queries.variacao_por_duration); aqui só se desenha.
  // ------------------------------------------------------------------

  // Cores das faixas, na ordem da legenda. Do extremo negativo ao extremo
  // positivo: cinza, preto, laranja, amarelo -- as mesmas do relatório.
  const CORES_FAIXA = ["#8c8c8c", "#111111", "#FF6200", "#FFC000"];
  // Texto dentro do segmento: branco nos escuros, preto no amarelo.
  const COR_ROTULO_FAIXA = ["#ffffff", "#ffffff", "#ffffff", "#111111"];

  function fmtPctInteiro(v) {
    return v === null || v === undefined ? "" : `${Math.round(Math.abs(v))}%`;
  }

  async function loadComposicao() {
    const data = await fetchJSON("/api/spreads/movement-distribution", paramsComuns());
    destroyChart("composicao");
    const el = document.getElementById("chart-composicao");
    el.parentElement.querySelector(".muted-msg")?.remove();
    // Título sem a classe (22/10/2026): ela já está no filtro, no topo.
    document.getElementById("titulo-composicao").textContent = "Evolução da Variação de Spreads";

    const cols = data.snapshots || [];
    const faixas = data.faixas || [];
    if (!cols.length) {
      el.style.display = "none";
      const msg = document.createElement("p");
      msg.className = "muted small muted-msg";
      msg.textContent = "Sem histórico suficiente para esta base de comparação.";
      el.after(msg);
      return;
    }
    el.style.display = "";

    // ORDEM DE EMPILHAMENTO. No Chart.js, positivos e negativos empilham
    // separados, cada lado a partir do zero, na ordem dos datasets. Para a
    // faixa MODERADA ficar colada no zero e a EXTREMA na ponta -- dos dois
    // lados, espelhado --, os datasets entram como: -10..0, <-10, 0..10, >10.
    // A legenda é reordenada abaixo para a ordem natural, da esquerda para a
    // direita: <-10, -10..0, 0..10, >10.
    const ordemPilha = [1, 0, 2, 3];
    const datasets = ordemPilha.map((k) => {
      const negativa = k < 2;
      const pcts = cols.map((c) => c.faixas[k].pct);
      return {
        label: faixas[k],
        ordemLegenda: k,
        data: pcts.map((v) => (v === null ? null : negativa ? -v : v)),
        backgroundColor: CORES_FAIXA[k],
        borderWidth: 0,
        stack: "base",
        rotulos: pcts.map(fmtPctInteiro),
        corRotulo: COR_ROTULO_FAIXA[k],
        contagens: cols.map((c) => c.faixas[k].n),
      };
    });

    charts.composicao = new Chart(el.getContext("2d"), {
      type: "bar",
      data: { labels: cols.map((c) => PADRAO_GRAFICO.diaMes(c.data)), datasets },
      plugins: [PADRAO_GRAFICO.linhaZero, PADRAO_GRAFICO.rotulosNasBarras],
      options: {
        responsive: true,
        datasets: { bar: { barPercentage: 0.62, categoryPercentage: 0.9 } },
        scales: {
          x: { stacked: true, grid: { display: false }, border: { display: false } },
          // Sem eixo y visível, como no relatório: os valores estão escritos
          // dentro das barras, e a régua só competiria com eles.
          y: { stacked: true, display: false },
        },
        plugins: {
          legend: {
            position: "bottom",
            labels: { sort: (a, b) =>
              datasets[a.datasetIndex].ordemLegenda - datasets[b.datasetIndex].ordemLegenda },
          },
          tooltip: {
            callbacks: {
              title(itens) {
                const c = cols[itens[0].dataIndex];
                return `${PADRAO_GRAFICO.dataCheia(c.data)} vs ${PADRAO_GRAFICO.dataCheia(c.data_comparacao)}`;
              },
              label(item) {
                const ds = datasets[item.datasetIndex];
                const n = ds.contagens[item.dataIndex];
                return ` ${ds.label}: ${fmtPctInteiro(item.raw)} (${n} papé${n === 1 ? "l" : "is"})`;
              },
              footer(itens) {
                return `Base: ${cols[itens[0].dataIndex].n_ativos} papéis`;
              },
            },
            // Ordem natural das faixas também na dica, do extremo negativo
            // ao extremo positivo.
            itemSort: (a, b) =>
              datasets[a.datasetIndex].ordemLegenda - datasets[b.datasetIndex].ordemLegenda,
          },
        },
      },
    });

    const ultima = cols[cols.length - 1];
    document.getElementById("sub-composicao").textContent =
      `% da base de ativos por faixa · ${currentBase} · ${ultima.n_ativos} papéis na última coluna`;
    escreverFonte("fonte-composicao",
      "Fonte: Anbima, Debentures.com e Itaú BBA",
      ultima.data);
  }

  // "spread" = nível de cada papel na curva; "abertura" = quanto andou.
  let modoDispersao = "spread";

  async function loadDispersao() {
    const data = await fetchJSON("/api/spreads/variacao-por-duration", paramsComuns());
    destroyChart("dispersao");
    const el = document.getElementById("chart-dispersao");
    el.parentElement.querySelector(".muted-msg")?.remove();
    const porSpread = modoDispersao === "spread";
    document.getElementById("titulo-dispersao").textContent = "Spread x Duration";

    const pontos = data.pontos || [];
    if (!pontos.length) {
      el.style.display = "none";
      const msg = document.createElement("p");
      msg.className = "muted small muted-msg";
      msg.textContent = "Sem papéis com spread nas duas datas desta comparação.";
      el.after(msg);
      return;
    }
    el.style.display = "";

    const eixoY = (p) => (porSpread ? p.spread : p.variacao);
    const serie = (destaque) => pontos
      // No modo "spread" não há destaque: a pergunta é onde o papel está na
      // curva, e pintar dez pontos por causa do movimento da semana
      // mandaria o olho para a pergunta errada.
      .filter((p) => (porSpread ? destaque === null : p.destaque === destaque))
      .map((p) => ({ x: p.duration, y: eixoY(p), p }));

    // A nuvem cinza vem PRIMEIRO na lista para ser desenhada por baixo: os
    // destaques laranja e preto ficam por cima dela, não escondidos.
    // Todos os pontos do MESMO tamanho (pedido do Allan, 21/09/2026): o
    // destaque é feito só pela cor. Ponto maior sugere peso maior -- e aqui
    // ninguém pesa mais que ninguém, cada ponto é um papel.
    const RAIO = 4.5;
    const datasets = [
      { label: "Demais papéis", data: serie(null), semLegenda: true,
        backgroundColor: "#c4c4c4", pointRadius: RAIO, pointHoverRadius: RAIO + 2 },
      { label: "Maiores aberturas", data: serie("abertura"),
        backgroundColor: "#FF6200", pointRadius: RAIO, pointHoverRadius: RAIO + 2 },
      { label: "Maiores fechamentos", data: serie("fechamento"),
        backgroundColor: "#111111", pointRadius: RAIO, pointHoverRadius: RAIO + 2 },
    ];

    const fmt1 = (v) => v.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

    charts.dispersao = new Chart(el.getContext("2d"), {
      type: "scatter",
      data: { datasets },
      // A linha do zero só faz sentido no modo de variação: no modo de
      // spread o zero não é a fronteira entre abrir e fechar, é só o começo
      // do eixo.
      plugins: porSpread ? [] : [PADRAO_GRAFICO.linhaZero],
      options: {
        responsive: true,
        // Dispersão não tem "data" no eixo x: o padrão global (modo index,
        // que mostra todas as séries da mesma posição) aqui mostraria dezenas
        // de papéis de uma vez. O alvo é o ponto sob o mouse.
        interaction: { mode: "nearest", intersect: true },
        scales: {
          x: {
            min: 1,
            title: { display: true, text: "Duration (anos)" },
            ticks: { callback: (v) => fmt1(v) },
            grid: { color: PADRAO_GRAFICO.GRADE },
          },
          y: {
            title: { display: true,
                     text: porSpread ? "Spread (bps)" : "Abertura / fechamento (bps)" },
            grid: { color: PADRAO_GRAFICO.GRADE },
          },
        },
        plugins: {
          // Legenda só no modo "Abertura" (22/10/2026): no modo "Spread"
          // todos os pontos são cinza e a legenda não descrevia nada.
          legend: {
            display: !porSpread,
            position: "bottom",
            labels: { filter: (item) => !datasets[item.datasetIndex].semLegenda },
          },
          tooltip: {
            mode: "nearest",
            intersect: true,
            callbacks: {
              title(itens) {
                const p = itens[0].raw.p;
                return `${p.codigo}${p.nome ? " — " + p.nome : ""}`;
              },
              label(item) {
                const p = item.raw.p;
                const sinal = p.variacao > 0 ? "+" : "";
                // Os dois números na dica nos dois modos: quem olha o nível
                // quer saber se ele acabou de andar, e vice-versa.
                return [` Duration ${fmt1(p.duration)} anos`,
                        ` Spread ${fmt1(p.spread)} bps`,
                        ` Variação ${sinal}${fmt1(p.variacao)} bps`];
              },
            },
          },
        },
        // Clicar num ponto abre a série histórica do papel -- o mesmo
        // drill-down dos tickers da tabela de setor.
        onClick(evento, elementos) {
          if (!elementos.length) return;
          const { datasetIndex, index } = elementos[0];
          const p = datasets[datasetIndex].data[index].p;
          openDrilldown(p.codigo, p.nome, el);
        },
      },
    });

    document.getElementById("sub-dispersao").textContent = porSpread
      ? `bps em ${PADRAO_GRAFICO.dataCheia(data.data_referencia)} · ${data.n_ativos} papéis · `
        + `clique num ponto para ver a série`
      : `bps · ${PADRAO_GRAFICO.dataCheia(data.data_referencia)} vs `
        + `${PADRAO_GRAFICO.dataCheia(data.data_comparacao)} · ${data.n_ativos} papéis · `
        + `clique num ponto para ver a série`;
    escreverFonte("fonte-dispersao",
      "Fonte: Anbima, Debentures.com e Itaú BBA",
      data.data_referencia);
  }

  // ------------------------------------------------------------------
  // MAIORES DESÁGIOS (21/09/2026, pedido do Allan): os 15 papéis com menor
  // % do PU par na data analisada -- "deixar claro quais papéis mais
  // depreciaram".
  // INVERTIDO EM 22/10/2026 (pedido do Allan): a barra agora sai do zero
  // (junção dos eixos) e vai até o % do PU par -- o papel com o maior
  // deságio tem a MENOR barra e aparece primeiro, no topo. A linha do par
  // (100%) continua como referência. Nomes alinhados à esquerda.
  // ------------------------------------------------------------------
  // "Todos" x "Só precificados" (22/10/2026, pedido do Allan). Em "Todos"
  // o * marca papel com PU publicado mas fora das contas de spread; em "Só
  // precificados" a lista vem do servidor já sem eles (continua com 15).
  let desagiosSoPrecificados = false;
  document.querySelectorAll("#desagios-tabs .win-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#desagios-tabs .win-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      desagiosSoPrecificados = btn.dataset.modo === "precificados";
      registrarUso("filtro", "Deságios: " + btn.textContent.trim());
      loadDesagios();
    });
  });

  async function loadDesagios() {
    const data = await fetchJSON("/api/spreads/desagios", {
      so_precificados: desagiosSoPrecificados ? 1 : undefined,
      classe: currentClasse, data: visaoDataInput.value || undefined,
      setor: filtros.setor, subsetor: filtros.subsetor, grupo: filtros.grupo,
    });
    destroyChart("desagios");
    const el = document.getElementById("chart-desagios");
    el.parentElement.querySelector(".muted-msg")?.remove();
    document.getElementById("titulo-desagios").textContent = "Maiores Deságios";
    const papeis = data.papeis || [];
    if (!papeis.length) {
      el.style.display = "none";
      const msg = document.createElement("p");
      msg.className = "muted small muted-msg";
      msg.textContent = "Sem % do PU par publicado para a data analisada.";
      el.after(msg);
      return;
    }
    el.style.display = "";
    const fmt = (v, c) => v.toLocaleString("pt-BR", { minimumFractionDigits: c, maximumFractionDigits: c });
    // Rótulo do eixo: emissor encurtado + ticker. O nome completo e os
    // demais dados ficam na dica de ferramenta.
    // PAPEL SEM TAXA INDICATIVA (24/09/2026, dúvida do Allan sobre a
    // Raízen). A Anbima publica o % do PU par de papéis que NÃO tiveram
    // taxa indicativa divulgada na data -- eles entram aqui (o deságio é
    // dado publicado) mas não entram em nenhuma conta de spread da página.
    // O asterisco marca isso na própria barra em vez de deixar a diferença
    // invisível.
    const rotulo = (p) => {
      const nome = (p.emissor || "").replace(/\s+(S\.?\/?A\.?|LTDA\.?)$/i, "");
      const curto = nome.length > 28 ? nome.slice(0, 27) + "…" : nome;
      return `${curto} · ${p.codigo}${p.precificado ? "" : " *"}`;
    };
    const semTaxa = papeis.filter((p) => !p.precificado).length;

    charts.desagios = new Chart(el.getContext("2d"), {
      type: "bar",
      data: {
        labels: papeis.map(rotulo),
        datasets: [{
          label: "% do PU par",
          data: papeis.map((p) => p.pct_pu_par),
          backgroundColor: "#FF6200",
          borderWidth: 0,
          barPercentage: 0.7,
          rotulos: papeis.map((p) => `${fmt(p.pct_pu_par, 1)}%`),
        }],
      },
      plugins: [{
        // Valor escrito logo depois da ponta da barra.
        id: "valorDesagio",
        afterDatasetsDraw(chart) {
          const { ctx } = chart;
          const meta = chart.getDatasetMeta(0);
          ctx.save();
          ctx.font = `600 12px ${Chart.defaults.font.family}`;
          ctx.fillStyle = PADRAO_GRAFICO.PRETO;
          ctx.textAlign = "left";
          ctx.textBaseline = "middle";
          meta.data.forEach((barra, i) => {
            const { x, y } = barra.getProps(["x", "y"], true);
            ctx.fillText(chart.data.datasets[0].rotulos[i], x + 5, y);
          });
          ctx.restore();
        },
      }, {
        // Linha do par (100%), a referência de todas as barras.
        id: "linhaPar",
        afterDatasetsDraw(chart) {
          const { ctx, chartArea, scales } = chart;
          const px = scales.x.getPixelForValue(100);
          ctx.save();
          ctx.strokeStyle = PADRAO_GRAFICO.PRETO;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(px, chartArea.top);
          ctx.lineTo(px, chartArea.bottom);
          ctx.stroke();
          ctx.restore();
        },
      }],
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: true, axis: "y" },
        scales: {
          x: {
            min: 0,
            max: 100,
            title: { display: true, text: "% do PU par" },
            ticks: { callback: (v) => `${v}%` },
            grid: { color: PADRAO_GRAFICO.GRADE },
          },
          // crossAlign "far": nomes encostados na margem esquerda, alinhados
          // entre si, em vez de colados no eixo com tamanhos diferentes.
          y: { grid: { display: false },
               ticks: { autoSkip: false, crossAlign: "far", color: PADRAO_GRAFICO.PRETO, font: { size: 12 } } },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (itens) => {
                const p = papeis[itens[0].dataIndex];
                return `${p.codigo} — ${p.emissor || ""}`;
              },
              label: (item) => {
                const p = papeis[item.dataIndex];
                const partes = [` ${fmt(p.pct_pu_par, 2)}% do PU par (deságio de ${fmt(100 - p.pct_pu_par, 1)} p.p.)`];
                partes.push(p.spread !== null ? ` Spread ${fmt(p.spread, 1)} bps`
                                              : " Sem taxa indicativa da Anbima nesta data");
                if (p.duration !== null) partes.push(` Duration ${fmt(p.duration, 2)} anos`);
                return partes;
              },
            },
          },
        },
        onClick(evento, elementos) {
          if (!elementos.length) return;
          const p = papeis[elementos[0].index];
          openDrilldown(p.codigo, p.emissor, el);
        },
      },
    });
    // A nota do asterisco fica na linha de fonte, que já é o lugar das
    // ressalvas de base nesta página.
    escreverFonte("fonte-desagios",
      semTaxa
        ? "* papel não precificado pela Anbima na data (PU publicado, sem taxa indicativa) — fica fora das contas de spread. "
          + "Fonte: Anbima e Itaú BBA"
        : "Fonte: Anbima e Itaú BBA",
      data.data_referencia);
  }

  // ------------------------------------------------------------------
  // DE ONDE VEIO O MOVIMENTO (01/10/2026, pedido do Allan) -- treemap.
  //
  // Reparte a variação do card SPREAD MÉDIO entre grupos/setores/emissores
  // (conta em queries.atribuicao_variacao: a soma dos blocos é a variação
  // do card). Área = |contribuição em bps|; laranja abriu, preto fechou,
  // cinza = papel que entrou ou saiu da base.
  //
  // SEM BIBLIOTECA: o layout "squarify" abaixo tem ~40 linhas e os blocos
  // são divs, o que deixa a dica de ferramenta ser HTML comum (tabela de
  // papéis) em vez de texto de canvas.
  // ------------------------------------------------------------------
  let atribuicaoNivel = "grupo";
  let atribuicaoDados = null;
  // Blocos de grupo desenhados um a um; o resto vira "Outros que abriram"
  // e "Outros que fecharam". Com ~250 grupos, desenhar todos daria
  // centenas de blocos de 2 px -- ninguém lê, e o que importa some.
  const ATRIBUICAO_MAX_BLOCOS = 30;
  const COR_BLOCO = {
    abriu: { fundo: "#FF6200", texto: "#ffffff" },
    fechou: { fundo: "#111111", texto: "#ffffff" },
    composicao: { fundo: "#c9c9c9", texto: "#111111" },
  };

  function escHtml(s) {
    return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function bps2(v) {
    if (v === null || v === undefined) return "—";
    const s = v > 0 ? "+" : "";
    return `${s}${v.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  // Squarified treemap (Bruls, Huizing & van Wijk). `itens` com `valor` > 0,
  // já em ordem decrescente; devolve cada item com x, y, w, h em px.
  function squarify(itens, x, y, w, h) {
    const total = itens.reduce((a, i) => a + i.valor, 0);
    if (!total || w <= 0 || h <= 0) return [];
    const escala = (w * h) / total;
    const fila = itens.map((i) => ({ ...i, area: i.valor * escala }));
    const saida = [];
    let linha = [];
    const pior = (l, lado) => {
      const s = l.reduce((a, i) => a + i.area, 0);
      const max = Math.max(...l.map((i) => i.area)), min = Math.min(...l.map((i) => i.area));
      return Math.max((lado * lado * max) / (s * s), (s * s) / (lado * lado * min));
    };
    const assentar = () => {
      const s = linha.reduce((a, i) => a + i.area, 0);
      if (w >= h) {               // coluna à esquerda
        const larg = s / h;
        let yy = y;
        linha.forEach((i) => { const alt = i.area / larg; saida.push({ ...i, x, y: yy, w: larg, h: alt }); yy += alt; });
        x += larg; w -= larg;
      } else {                    // faixa no topo
        const alt = s / w;
        let xx = x;
        linha.forEach((i) => { const larg = i.area / alt; saida.push({ ...i, x: xx, y, w: larg, h: alt }); xx += larg; });
        y += alt; h -= alt;
      }
      linha = [];
    };
    fila.forEach((item) => {
      const lado = Math.min(w, h);
      if (!linha.length || pior(linha.concat(item), lado) <= pior(linha, lado)) linha.push(item);
      else { assentar(); linha.push(item); }
    });
    if (linha.length) assentar();
    return saida;
  }

  // Quais blocos vão para a tela: os maiores grupos, os dois "Outros" e os
  // blocos de composição (sempre visíveis -- são a outra metade da
  // explicação, mesmo quando pequenos).
  function montarBlocosAtribuicao(dados) {
    const grupos = dados.blocos.filter((b) => b.tipo === "grupo" && b.contribuicao_bps !== 0);
    const comp = dados.blocos.filter((b) => b.tipo !== "grupo" && b.contribuicao_bps !== 0);
    const visiveis = grupos.slice(0, ATRIBUICAO_MAX_BLOCOS);
    const resto = grupos.slice(ATRIBUICAO_MAX_BLOCOS);
    const outros = [];
    [["Outros que abriram", (b) => b.contribuicao_bps > 0], ["Outros que fecharam", (b) => b.contribuicao_bps < 0]]
      .forEach(([rotulo, lado]) => {
        const membros = resto.filter(lado);
        if (!membros.length) return;
        const contrib = membros.reduce((a, b) => a + b.contribuicao_bps, 0);
        outros.push({
          rotulo: `${rotulo} (${membros.length})`, tipo: "outros", contribuicao_bps: contrib,
          pct_variacao: dados.variacao_bps && Math.abs(dados.variacao_bps) >= 0.05
            ? Math.round(1000 * contrib / dados.variacao_bps) / 10 : null,
          membros,
        });
      });
    // "Outros" vai SEMPRE por último no layout (canto inferior direito),
    // mesmo quando é o maior: o olho começa no canto superior esquerdo, e
    // ali tem que estar um nome que explica alguma coisa, não um resto.
    const comValor = (l) => l.map((b) => ({ ...b, valor: Math.abs(b.contribuicao_bps) }))
      .sort((a, b) => b.valor - a.valor);
    return comValor(visiveis.concat(comp)).concat(comValor(outros));
  }

  // TOM POR RANKING (22/10/2026, pedido do Allan). Cada cor (abriu,
  // fechou, entrou/saiu) continua sendo a cor-base, mas o tom clareia com
  // a posição do bloco dentro dela: o maior de cada cor fica com a cor
  // cheia, o 2º um pouco mais claro, e assim por diante até o último
  // (misturado com branco até CLAREAMENTO_MAX). O texto troca para preto
  // quando o fundo fica claro demais para o branco ser lido.
  const CLAREAMENTO_MAX = { abriu: 0.72, fechou: 0.68, composicao: 0.55 };

  function categoriaDoBloco(b) {
    if (b.tipo === "entrada" || b.tipo === "saida") return "composicao";
    return b.contribuicao_bps > 0 ? "abriu" : "fechou";
  }

  function atribuirTons(blocos) {
    const porCor = { abriu: [], fechou: [], composicao: [] };
    blocos.forEach((b) => { if (b.tipo !== "outros") porCor[categoriaDoBloco(b)].push(b); });
    Object.entries(porCor).forEach(([cor, lista]) => {
      lista.sort((a, b) => Math.abs(b.contribuicao_bps) - Math.abs(a.contribuicao_bps));
      lista.forEach((b, i) => {
        b.clareamento = lista.length > 1 ? (i / (lista.length - 1)) * CLAREAMENTO_MAX[cor] : 0;
      });
    });
    return blocos;
  }

  function clarear(hex, t) {
    const n = parseInt(hex.slice(1), 16);
    const canal = (c) => Math.round(c + (255 - c) * t);
    const [r, g, b] = [canal(n >> 16), canal((n >> 8) & 255), canal(n & 255)];
    const luz = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return { fundo: `rgb(${r}, ${g}, ${b})`, texto: luz > 0.62 ? "#111111" : "#ffffff" };
  }

  function corDoBloco(b) {
    if (b.tipo !== "outros") {
      return clarear(COR_BLOCO[categoriaDoBloco(b)].fundo, b.clareamento || 0);
    }
    // "Outros" listrado na cor do lado: lê-se "abriu"/"fechou" sem confundir
    // com o cinza de entradas e saídas. Listras no tom MAIS CLARO da cor
    // (22/10/2026): é a soma dos menores, então fica no fim da escala.
    const cor = categoriaDoBloco(b);
    const base = clarear(COR_BLOCO[cor].fundo, CLAREAMENTO_MAX[cor]);
    const claro = clarear(COR_BLOCO[cor].fundo, Math.min(0.9, CLAREAMENTO_MAX[cor] + 0.18));
    return { fundo: `repeating-linear-gradient(135deg, ${base.fundo} 0 8px, ${claro.fundo} 8px 16px)`, texto: "#111111" };
  }

  function textoPct(b) {
    return b.pct_variacao === null || b.pct_variacao === undefined ? ""
      : `${b.pct_variacao.toLocaleString("pt-BR", { maximumFractionDigits: 0 })}%`;
  }

  function htmlDica(b, dados) {
    const variacao = dados.variacao_bps;
    let h = `<div class="dica-titulo">${escHtml(b.rotulo)}</div>`;
    h += `<div>${bps2(b.contribuicao_bps)} bps`;
    if (b.pct_variacao !== null && b.pct_variacao !== undefined) {
      h += ` · ${textoPct(b)} da variação de ${bps2(variacao)} bps`;
    }
    h += "</div>";
    if (b.tipo === "grupo" && b.peso_bps !== null && Math.abs(b.peso_bps) >= 0.005) {
      h += `<div class="muted">repricing ${bps2(b.repricing_bps)} · mudança de peso ${bps2(b.peso_bps)}</div>`;
    }
    if (b.sem_grupo) h += '<div class="muted">sem grupo econômico cadastrado — agrupado pelo emissor</div>';

    if (b.tipo === "outros") {
      // "Outros" lista os grupos que ele junta, não papéis.
      h += "<table><thead><tr><th>Grupo</th><th>bps</th><th>Papéis</th></tr></thead><tbody>";
      b.membros.slice(0, 5).forEach((m) => {
        h += `<tr><td>${escHtml(m.rotulo)}</td><td>${bps2(m.contribuicao_bps)}</td><td>${m.n_tickers}</td></tr>`;
      });
      if (b.membros.length > 5) h += `<tr><td class="mais" colspan="3">… mais ${b.membros.length - 5}</td></tr>`;
      return h + "</tbody></table>";
    }

    const composicao = b.tipo === "entrada" || b.tipo === "saida";
    // Saída: o papel não tem dado na data analisada, então spread e
    // estoque são os da data INICIAL -- o cabeçalho diz isso.
    if (b.tipo === "saida") {
      h += `<div class="muted">spread e estoque de ${fmtData(dados.data_comparacao)}, último dia em que o papel pesava no card</div>`;
    }
    h += "<table><thead><tr><th>Código</th>"
      + (composicao || atribuicaoNivel !== "emissor" ? "<th style=\"text-align:left;\">Emissor</th>" : "")
      + "<th>Spread</th><th>Abertura</th><th>Estoque (R$ mi)</th></tr></thead><tbody>";
    b.tickers.forEach((t) => {
      // Entrada/saída não tem abertura: no lugar dela vai o porquê
      // ("sem spread" = estreou/venceu/parou de ser precificado; "sem
      // estoque" = tinha spread mas não pesava no card naquela data).
      const abertura = composicao
        ? `${b.tipo === "entrada" ? "entrou" : "saiu"} · ${(t.motivo || "").startsWith("sem estoque") ? "sem estoque" : "sem spread"}`
        : fmtBps(t.variacao_bps);
      h += `<tr><td>${escHtml(t.codigo)}</td>`
        + (composicao || atribuicaoNivel !== "emissor"
          ? `<td style="text-align:left;max-width:150px;overflow:hidden;text-overflow:ellipsis;">${escHtml(t.emissor)}</td>` : "")
        + `<td>${num1(t.spread)}</td><td>${abertura}</td>`
        + `<td>${t.estoque === null || t.estoque === undefined ? "—" : fmtNum(t.estoque, 0)}</td></tr>`;
    });
    if (b.n_tickers > b.tickers.length) {
      h += `<tr><td class="mais" colspan="5">… mais ${b.n_tickers - b.tickers.length} papéis</td></tr>`;
    }
    h += "</tbody></table>";
    return h;
  }

  function desenharAtribuicao() {
    const area = document.getElementById("atribuicao-mapa");
    const dica = document.getElementById("atribuicao-dica");
    const card = document.getElementById("atribuicao-card");
    area.innerHTML = "";
    dica.style.display = "none";
    const dados = atribuicaoDados;
    const vazio = !dados || !dados.blocos || !dados.blocos.length;
    document.getElementById("atribuicao-vazio").style.display = vazio ? "" : "none";
    area.style.display = vazio ? "none" : "";
    if (vazio) return;

    const blocos = squarify(atribuirTons(montarBlocosAtribuicao(dados)), 0, 0, area.clientWidth, area.clientHeight);
    blocos.forEach((b) => {
      const el = document.createElement("div");
      const cor = corDoBloco(b);
      el.className = "treemap-bloco";
      Object.assign(el.style, {
        left: `${b.x}px`, top: `${b.y}px`, width: `${b.w}px`, height: `${b.h}px`,
        background: cor.fundo, color: cor.texto,
      });
      if (b.w > 60 && b.h > 28) {
        const pct = textoPct(b);
        el.innerHTML = `<b>${escHtml(b.rotulo)}</b>${bps2(b.contribuicao_bps)} bps${pct && b.h > 40 ? `<br>${pct}` : ""}`;
      }
      const posicionar = (ev) => {
        // A dica segue o mouse e vira para o outro lado perto das bordas.
        const r = card.getBoundingClientRect();
        let x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14;
        if (x + dica.offsetWidth > r.width) x = ev.clientX - r.left - dica.offsetWidth - 14;
        if (y + dica.offsetHeight > r.height) y = ev.clientY - r.top - dica.offsetHeight - 14;
        dica.style.left = `${Math.max(0, x)}px`;
        dica.style.top = `${Math.max(0, y)}px`;
      };
      el.addEventListener("mouseenter", (ev) => {
        dica.innerHTML = htmlDica(b, dados);
        dica.style.display = "";
        posicionar(ev);
      });
      el.addEventListener("mousemove", posicionar);
      el.addEventListener("mouseleave", () => { dica.style.display = "none"; });
      area.appendChild(el);
    });
  }

  async function loadAtribuicao() {
    if (!baseValida()) return;
    const dados = await fetchJSON("/api/spreads/atribuicao", paramsComuns({ nivel: atribuicaoNivel }));
    atribuicaoDados = dados;
    const sub = document.getElementById("atribuicao-sub");
    const totais = document.getElementById("atribuicao-totais");
    if (dados.variacao_bps === null || dados.variacao_bps === undefined) {
      sub.textContent = "—";
      totais.textContent = "";
    } else {
      sub.textContent = `bps · spread médio ${num1(dados.spread_inicial)} → ${num1(dados.spread_final)} `
        + `(${bps2(dados.variacao_bps)} bps) de ${fmtData(dados.data_comparacao)} a ${fmtData(dados.data_referencia)} `
        + `(${currentBase}) · área = tamanho da contribuição · passe o mouse para ver os papéis`;
      totais.textContent = `fecharam ${bps2(dados.total_fechou_bps)} · abriram ${bps2(dados.total_abriu_bps)} · `
        + `entradas/saídas ${bps2(dados.composicao_bps)} = ${bps2(dados.variacao_bps)} bps`;
    }
    escreverFonte("fonte-atribuicao", "Fonte: Anbima, Debentures.com e Itaú BBA", dados.data_referencia);
    desenharAtribuicao();
  }

  document.querySelectorAll("#atribuicao-tabs .win-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#atribuicao-tabs .win-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      atribuicaoNivel = btn.dataset.nivel;
      registrarUso("filtro", "Movimento: " + btn.textContent.trim());
      loadAtribuicao();
    });
  });

  // Blocos em px: redesenha quando a largura muda (sem nova consulta).
  let atribuicaoResize = null;
  window.addEventListener("resize", () => {
    clearTimeout(atribuicaoResize);
    atribuicaoResize = setTimeout(desenharAtribuicao, 150);
  });

  async function reloadAll() {
    await Promise.all([
      loadKPI(), loadSeriesChart(), loadSetor(), loadMovers(),
      loadComposicao(), loadDispersao(), loadDesagios(), loadAtribuicao(),
    ]);
  }

  classeTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      classeTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentClasse = btn.dataset.classe;
      registrarUso("filtro", "Classe: " + btn.textContent.trim());
      // Trocar de classe volta o drill-down para o topo: os setores de
      // "IPCA + Incentivadas" e "CDI + Tradicionais" não são os mesmos, e
      // ficar dentro de um subsetor que não existe na outra classe daria
      // tabela vazia sem explicação.
      setorNivel = "setor"; setorAtual = null; subsetorAtual = null; emissorAtual = null;
      // A taxonomia das duas classes não é a mesma: um setor escolhido em
      // IPCA+ pode não existir em CDI+, e sobraria um filtro invisível
      // deixando a tela vazia.
      filtros.setor = []; filtros.subsetor = []; filtros.grupo = [];
      opcoesFiltro = { setores: [], subsetores: [], grupos: [] };
      atualizarMenus();
      if (barraFiltros2.style.display !== "none") carregarOpcoesFiltro();
      recolherDrilldown();
      carregarDatas();
      reloadAll();
    });
  });

  // Tudo que depende da comparação (KPIs, tabela de setor, movers,
  // composição e dispersão). A evolução e os deságios ficam de fora: a
  // linha mostra o histórico inteiro e o deságio é uma foto da data.
  function recarregarComparacao() {
    if (!baseValida()) return;
    loadKPI();
    loadSetor();
    loadMovers();
    loadComposicao();
    loadDispersao();
    loadAtribuicao();
  }

  // ------------------------------------------------------------------
  // SELETOR DE DATA (22/10/2026, pedido do Allan). Um campo só na barra:
  //  - d-1 / WoW / MoM: "Dados até" -- um dia, a data de referência de
  //    todos os visuais. Abre mostrando a mais recente; `visao-data` vazio
  //    continua significando "a última disponível".
  //  - Personalizado: "Período" -- dois cliques no mesmo calendário, início
  //    e fim. O início é a base de comparação; os dois recortam o eixo da
  //    evolução.
  // Calendário próprio porque o <input type="date"> nativo não deixa
  // bloquear os dias sem boletim -- e só esses podem ser comparados.
  // ------------------------------------------------------------------
  const btnData = document.getElementById("btn-data");
  const textoData = document.getElementById("texto-data");
  const rotuloData = document.getElementById("rotulo-data");
  const calendario = document.getElementById("calendario");
  const MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                 "agosto", "setembro", "outubro", "novembro", "dezembro"];
  let datasDisponiveis = [];   // AAAA-MM-DD, do mais antigo ao mais recente
  let setDatas = new Set();
  let calMes = null;           // 1º dia do mês exibido
  let selecaoParcial = null;   // início já clicado, esperando o fim

  const iso2br = (v) => (v ? `${v.slice(8, 10)}/${v.slice(5, 7)}/${v.slice(0, 4)}` : "—");
  const ultimaData = () => datasDisponiveis[datasDisponiveis.length - 1] || null;
  const modoPeriodo = () => currentBase === BASE_PERSONALIZADA;

  async function carregarDatas() {
    const r = await fetchJSON("/api/spreads/datas", { classe: currentClasse });
    datasDisponiveis = r.datas || [];
    setDatas = new Set(datasDisponiveis);
    atualizarCampoData();
  }

  function atualizarCampoData() {
    rotuloData.textContent = modoPeriodo() ? "Período" : "Dados até";
    if (modoPeriodo()) {
      textoData.textContent = inicioInput.value
        ? `${iso2br(inicioInput.value)} – ${iso2br(visaoDataInput.value || ultimaData())}`
        : "Escolha início e fim";
    } else {
      textoData.textContent = iso2br(visaoDataInput.value || ultimaData());
    }
  }

  function abrirCalendario() {
    selecaoParcial = null;
    const ref = (modoPeriodo() && inicioInput.value) || visaoDataInput.value || ultimaData();
    const base = ref ? new Date(+ref.slice(0, 4), +ref.slice(5, 7) - 1, 1) : new Date();
    calMes = new Date(base.getFullYear(), base.getMonth(), 1);
    calendario.style.display = "block";
    btnData.setAttribute("aria-expanded", "true");
    desenharCalendario();
  }

  function fecharCalendario() {
    if (calendario.style.display === "none") return;
    calendario.style.display = "none";
    btnData.setAttribute("aria-expanded", "false");
    selecaoParcial = null;
  }

  function desenharCalendario() {
    const ano = calMes.getFullYear(), mes = calMes.getMonth();
    const pad = (n) => String(n).padStart(2, "0");
    const chave = `${ano}-${pad(mes + 1)}`;
    const primeira = datasDisponiveis[0], ultima = ultimaData();
    const podeVoltar = !!primeira && chave > primeira.slice(0, 7);
    const podeAvancar = !!ultima && chave < ultima.slice(0, 7);
    let ini = null, fim = null;
    if (modoPeriodo()) {
      if (selecaoParcial) ini = selecaoParcial;
      else if (inicioInput.value) { ini = inicioInput.value; fim = visaoDataInput.value || ultima; }
    } else {
      fim = visaoDataInput.value || ultima;
    }
    let h = `<div class="cal-topo">
        <button type="button" class="cal-nav" data-nav="-1" aria-label="Mês anterior" ${podeVoltar ? "" : "disabled"}>‹</button>
        <b>${MESES[mes]} ${ano}</b>
        <button type="button" class="cal-nav" data-nav="1" aria-label="Próximo mês" ${podeAvancar ? "" : "disabled"}>›</button>
      </div><div class="cal-grade">`;
    ["D", "S", "T", "Q", "Q", "S", "S"].forEach((d) => { h += `<div class="cal-semana">${d}</div>`; });
    for (let i = 0; i < new Date(ano, mes, 1).getDay(); i++) h += "<div></div>";
    const nDias = new Date(ano, mes + 1, 0).getDate();
    for (let d = 1; d <= nDias; d++) {
      const iso = `${chave}-${pad(d)}`;
      let cls = "cal-dia";
      if (iso === ini || iso === fim) cls += " escolhido";
      else if (ini && fim && iso > ini && iso < fim) cls += " no-periodo";
      h += `<button type="button" class="${cls}" data-dia="${iso}" ${setDatas.has(iso) ? "" : "disabled"}>${d}</button>`;
    }
    h += "</div>";
    const dica = !modoPeriodo() ? "Só dias com boletim podem ser escolhidos."
      : selecaoParcial ? `Início ${iso2br(selecaoParcial)} · agora escolha a data final.`
      : "Escolha a data inicial da comparação.";
    h += `<div class="cal-dica">${dica}</div>`;
    if (!modoPeriodo()) {
      h += '<div class="cal-rodape"><button type="button" class="btn-small" data-acao="ultima">Usar a última data</button></div>';
    }
    calendario.innerHTML = h;
  }

  // Aplica a(s) data(s) escolhida(s) à página inteira.
  function aplicarDatas() {
    atualizarCampoData();
    loadSeriesChart();
    loadDesagios();
    recarregarComparacao();
  }

  btnData.addEventListener("click", (e) => {
    e.stopPropagation();
    if (calendario.style.display === "block") fecharCalendario();
    else abrirCalendario();
  });

  calendario.addEventListener("click", (e) => {
    e.stopPropagation();
    const nav = e.target.closest("[data-nav]");
    if (nav) {
      calMes = new Date(calMes.getFullYear(), calMes.getMonth() + Number(nav.dataset.nav), 1);
      desenharCalendario();
      return;
    }
    if (e.target.closest('[data-acao="ultima"]')) {
      visaoDataInput.value = "";
      fecharCalendario();
      aplicarDatas();
      return;
    }
    const dia = e.target.closest("[data-dia]");
    if (!dia || dia.disabled) return;
    const escolhido = dia.dataset.dia;
    // A última data disponível fica como "vazio": a página continua
    // acompanhando o boletim mais recente quando ele chegar.
    const comoValor = (v) => (v === ultimaData() ? "" : v);
    if (!modoPeriodo()) {
      visaoDataInput.value = comoValor(escolhido);
      registrarUso("filtro", "Dados até: data escolhida");
      fecharCalendario();
      aplicarDatas();
      return;
    }
    if (!selecaoParcial) { selecaoParcial = escolhido; desenharCalendario(); return; }
    if (escolhido === selecaoParcial) return;   // início e fim precisam ser dias diferentes
    const [a, b] = [selecaoParcial, escolhido].sort();
    inicioInput.value = a;
    visaoDataInput.value = comoValor(b);
    registrarUso("filtro", "Base: período escolhido");
    fecharCalendario();
    aplicarDatas();
  });

  document.addEventListener("click", fecharCalendario);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") fecharCalendario(); });

  baseTabs.forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      baseTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentBase = btn.dataset.base;
      registrarUso("filtro", "Base: " + btn.dataset.base);
      // Saindo do Personalizado, a data final do período continua como
      // "Dados até"; o início deixa de existir.
      if (!modoPeriodo()) inicioInput.value = "";
      atualizarCampoData();
      if (modoPeriodo() && !inicioInput.value) {
        // Sem período ainda: abre o calendário e espera -- pedir ao
        // servidor uma comparação sem ponto de partida daria 400.
        ev.stopPropagation();
        document.getElementById("nota-base").textContent = "Escolha o período no calendário.";
        abrirCalendario();
        return;
      }
      loadSeriesChart();
      recarregarComparacao();
    });
  });

  // ------------------------------------------------------------------
  // SEGUNDA LINHA DE FILTROS (24/09/2026): setor, subsetor e grupo
  // econômico, seleção múltipla com busca por nome. Valem para a página
  // inteira -- por isso mexer neles chama `reloadAll`, não um bloco só.
  // ------------------------------------------------------------------
  const btnMaisFiltros = document.getElementById("btn-mais-filtros");
  const barraFiltros2 = document.getElementById("barra-filtros-2");
  const btnLimparFiltros = document.getElementById("btn-limpar-filtros");
  let opcoesFiltro = { setores: [], subsetores: [], grupos: [] };

  btnMaisFiltros.addEventListener("click", () => {
    const abrindo = barraFiltros2.style.display === "none";
    barraFiltros2.style.display = abrindo ? "" : "none";
    btnMaisFiltros.setAttribute("aria-expanded", abrindo ? "true" : "false");
    // As setas apontam para o que vai acontecer no PRÓXIMO clique.
    btnMaisFiltros.querySelector(".setas").textContent = abrindo ? "⌃⌃" : "⌄⌄";
    if (abrindo && !opcoesFiltro.setores.length) carregarOpcoesFiltro();
  });

  function rotuloMenu(prefixo, escolhidos) {
    if (!escolhidos.length) return `${prefixo}: todos`;
    if (escolhidos.length === 1) return `${prefixo}: ${escolhidos[0]}`;
    return `${prefixo}: ${escolhidos.length} selecionados`;
  }

  // Um componente pequeno para os três menus -- lista com busca e
  // caixinhas. `itens()` é função (não lista) porque a de subsetor muda
  // conforme os setores escolhidos.
  function montarMenu(id, prefixo, chave, itens) {
    const raiz = document.getElementById(id);
    const botao = raiz.querySelector(".menu-multi-btn");
    const painel = raiz.querySelector(".menu-multi-painel");
    const busca = raiz.querySelector(".menu-multi-busca");
    const lista = raiz.querySelector(".menu-multi-lista");

    function desenhar() {
      const termo = (busca.value || "").trim().toLowerCase();
      const visiveis = itens().filter((n) => n.toLowerCase().includes(termo));
      lista.innerHTML = "";
      if (!visiveis.length) {
        lista.innerHTML = '<div class="muted small" style="padding:6px 2px;">Nada encontrado.</div>';
        return;
      }
      visiveis.slice(0, 300).forEach((nome) => {
        const label = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = filtros[chave].includes(nome);
        cb.addEventListener("change", () => {
          filtros[chave] = cb.checked
            ? [...filtros[chave], nome]
            : filtros[chave].filter((x) => x !== nome);
          if (chave === "setor") {
            // Subsetor de um setor que saiu do recorte não pode continuar
            // marcado -- ficaria um filtro invisível derrubando a tela.
            const permitidos = new Set(subsetoresDisponiveis());
            filtros.subsetor = filtros.subsetor.filter((x) => permitidos.has(x));
          }
          if (cb.checked) registrarUso("filtro", `${prefixo}: ${nome}`);
          atualizarMenus();
          reloadAll();
        });
        label.appendChild(cb);
        label.appendChild(document.createTextNode(nome));
        lista.appendChild(label);
      });
    }

    botao.addEventListener("click", (e) => {
      e.stopPropagation();
      const abrindo = painel.style.display === "none";
      document.querySelectorAll(".menu-multi-painel").forEach((p) => { p.style.display = "none"; });
      painel.style.display = abrindo ? "block" : "none";
      if (abrindo) { desenhar(); busca.focus(); }
    });
    painel.addEventListener("click", (e) => e.stopPropagation());
    busca.addEventListener("input", desenhar);
    // "Selecionar todos" (22/10/2026): marca tudo o que a busca está
    // mostrando -- sem termo digitado, a lista inteira.
    raiz.querySelector(".menu-multi-todos").addEventListener("click", () => {
      const termo = (busca.value || "").trim().toLowerCase();
      const visiveis = itens().filter((n) => n.toLowerCase().includes(termo));
      filtros[chave] = [...new Set([...filtros[chave], ...visiveis])];
      registrarUso("filtro", `${prefixo}: selecionar todos`);
      desenhar();
      atualizarMenus();
      reloadAll();
    });
    raiz.querySelector(".menu-multi-limpar").addEventListener("click", () => {
      filtros[chave] = [];
      if (chave === "setor") filtros.subsetor = [];
      desenhar();
      atualizarMenus();
      reloadAll();
    });
    return { botao, desenhar };
  }

  function subsetoresDisponiveis() {
    const setores = filtros.setor;
    return opcoesFiltro.subsetores
      .filter((x) => !setores.length || setores.includes(x.setor))
      .map((x) => x.subsetor);
  }

  const menus = {
    setor: montarMenu("menu-setor", "Setor", "setor", () => opcoesFiltro.setores),
    subsetor: montarMenu("menu-subsetor", "Subsetor", "subsetor", subsetoresDisponiveis),
    grupo: montarMenu("menu-grupo", "Grupo econômico", "grupo", () => opcoesFiltro.grupos),
  };
  const PREFIXO_MENU = { setor: "Setor", subsetor: "Subsetor", grupo: "Grupo econômico" };

  function atualizarMenus() {
    Object.entries(menus).forEach(([chave, menu]) => {
      menu.botao.textContent = rotuloMenu(PREFIXO_MENU[chave], filtros[chave]);
      menu.botao.classList.toggle("tem-selecao", filtros[chave].length > 0);
      menu.desenhar();
    });
    const total = filtros.setor.length + filtros.subsetor.length + filtros.grupo.length;
    btnLimparFiltros.style.display = total ? "" : "none";
    btnMaisFiltros.classList.toggle("tem-selecao", total > 0);
    document.getElementById("nota-filtros").textContent = total
      ? "Recorte ativo em toda a página"
      : "Sem recorte: mercado inteiro da classe";
  }

  btnLimparFiltros.addEventListener("click", () => {
    filtros.setor = []; filtros.subsetor = []; filtros.grupo = [];
    atualizarMenus();
    reloadAll();
  });

  document.addEventListener("click", () => {
    document.querySelectorAll(".menu-multi-painel").forEach((p) => { p.style.display = "none"; });
  });

  async function carregarOpcoesFiltro() {
    opcoesFiltro = await fetchJSON("/api/spreads/opcoes-filtro", { classe: currentClasse });
    atualizarMenus();
  }

  atualizarMenus();

  // Alternância do gráfico Spread x Duration.
  document.querySelectorAll("#dispersao-tabs .win-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#dispersao-tabs .win-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      modoDispersao = btn.dataset.modo;
      registrarUso("filtro", "Dispersão: " + btn.textContent.trim());
      loadDispersao();
    });
  });

  carregarDatas();
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
  const emissorRecolher = document.getElementById("emissor-recolher");

  // ------------------------------------------------------------------
  // PERFIL DO EMISSOR (22/10/2026; redesenho clean no mesmo dia). Um
  // perfil por vez: um emissor ou um grupo econômico consolidado.
  // `currentEmissores` é a lista de nomes que as rotas recebem -- um nome
  // para emissor, todos os do grupo para grupo.
  // ------------------------------------------------------------------
  let emissoresDisponiveis = [];
  let gruposEconomicos = [];
  let emissorPadrao = null;
  let currentEmissores = [];
  let perfilAtual = null;   // { tipo: "emissor" | "grupo", nome }
  const CLASSES_EMISSOR = [
    { classe: "IPCA + Incentivadas", curto: "ipca", canvas: "chart-emissor-ipca", chave: "emissorIpca" },
    { classe: "CDI + Tradicionais", curto: "cdi", canvas: "chart-emissor-cdi", chave: "emissorCdi" },
  ];
  let tickersRecolhidos = false;
  let emissoresCarregados = false;

  // Linha de fonte de cada visual (Visão Geral e Dispersão também usam).
  function escreverFonte(id, texto, iso) {
    const el = document.getElementById(id);
    if (!el) return;
    const [, m, d] = (iso || "").split("-");
    el.textContent = d ? `${texto} (dado disponível até ${d}/${m})` : texto;
  }

  function fmtBpsSimples(v) {
    return v !== null && v !== undefined ? v.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : "—";
  }

  // R$ milhões -> "R$ 1,35 bi" / "R$ 640 mi".
  function fmtReais(mi) {
    if (mi === null || mi === undefined) return "—";
    return mi >= 1000
      ? `R$ ${(mi / 1000).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} bi`
      : `R$ ${mi.toLocaleString("pt-BR", { maximumFractionDigits: 0 })} mi`;
  }

  function fmtCnpj(c) {
    const d = String(c || "").replace(/\D/g, "");
    return d.length === 14 ? `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}` : (c || "");
  }

  const semAcento = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

  secaoTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      secaoTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const secao = btn.dataset.secao;
      registrarUso("subaba", btn.textContent.trim());
      painelVisaoGeral.style.display = secao === "visao-geral" ? "block" : "none";
      // O treemap é desenhado em px: se a página abriu em outra subaba, o
      // painel estava escondido (largura 0) quando os dados chegaram.
      if (secao === "visao-geral") desenharAtribuicao();
      painelEmissores.style.display = secao === "emissores" ? "block" : "none";
      if (secao === "emissores" && !emissoresCarregados) carregarListaEmissores();
      document.getElementById("painel-dispersao").style.display = secao === "dispersao" ? "block" : "none";
      if (secao === "dispersao") abrirAbaDispersao();
    });
  });

  async function carregarListaEmissores() {
    emissoresCarregados = true;
    const [lista, grupos] = await Promise.all([
      fetchJSON("/api/spreads/emissores", {}),
      fetchJSON("/api/spreads/grupos", {}),
    ]);
    emissoresDisponiveis = lista.emissores || [];
    emissorPadrao = lista.padrao || emissoresDisponiveis[0] || null;
    gruposEconomicos = grupos.grupos || [];
    if (emissorBuscaResultados.style.display === "block") listarBusca();
    // A aba nunca abre vazia: o maior emissor por estoque já vem selecionado.
    if (!perfilAtual && emissorPadrao) abrirPerfil("emissor", emissorPadrao);
  }

  // BUSCA COM TIPO: o seletor ao lado escolhe o que a lista mostra --
  // emissores ou grupos econômicos, nunca os dois juntos. Clicar na caixa
  // abre a lista inteira em ordem alfabética; digitar filtra (sem acento),
  // quem começa com o termo primeiro.
  let tipoBusca = "emissor";
  const tipoBuscaTabs = document.querySelectorAll("#perfil-tipo-tabs .win-btn");

  function listarBusca() {
    const q = semAcento(emissorBusca.value.trim());
    const candidatos = tipoBusca === "grupo"
      ? gruposEconomicos.map((g) => ({ nome: g.grupo, extra: `${g.n_emissores} emissor${g.n_emissores === 1 ? "" : "es"}` }))
      : emissoresDisponiveis.map((n) => ({ nome: n, extra: "" }));
    const filtrados = candidatos.filter((c) => !q || semAcento(c.nome).includes(q));
    filtrados.sort((a, b) => (q ? semAcento(b.nome).startsWith(q) - semAcento(a.nome).startsWith(q) : 0)
      || a.nome.localeCompare(b.nome, "pt-BR", { sensitivity: "base" }));
    emissorBuscaResultados.innerHTML = "";
    if (!candidatos.length) {
      emissorBuscaResultados.innerHTML = '<div class="search-item muted">Carregando…</div>';
    } else if (!filtrados.length) {
      emissorBuscaResultados.innerHTML = '<div class="search-item muted">Nada encontrado.</div>';
    } else {
      const frag = document.createDocumentFragment();
      filtrados.forEach((c) => {
        const div = document.createElement("div");
        div.className = "search-item";
        div.innerHTML = `<span>${escHtml(c.nome)}</span>`
          + (c.extra ? `<span class="tipo-resultado">${c.extra}</span>` : "");
        div.addEventListener("click", () => {
          emissorBusca.value = "";
          emissorBuscaResultados.style.display = "none";
          emissorBusca.blur();
          abrirPerfil(tipoBusca, c.nome);
        });
        frag.appendChild(div);
      });
      emissorBuscaResultados.appendChild(frag);
    }
    emissorBuscaResultados.style.display = "block";
  }

  emissorBusca.addEventListener("input", listarBusca);
  emissorBusca.addEventListener("focus", listarBusca);
  emissorBusca.addEventListener("click", listarBusca);

  tipoBuscaTabs.forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      tipoBuscaTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      tipoBusca = btn.dataset.tipo;
      const rotulo = tipoBusca === "grupo" ? "grupo econômico" : "emissor";
      emissorBusca.placeholder = `Buscar ${rotulo}…`;
      emissorBusca.setAttribute("aria-label", `Buscar ${rotulo}`);
      emissorBusca.value = "";
      emissorBusca.focus();
      // Redesenha na hora: se a caixa já estava em foco, `focus()` não
      // dispara evento e a lista continuaria a do tipo anterior.
      listarBusca();
    });
  });

  document.addEventListener("click", (e) => {
    if (!emissorBuscaResultados.contains(e.target) && e.target !== emissorBusca) {
      emissorBuscaResultados.style.display = "none";
    }
  });

  function abrirPerfil(tipo, nome) {
    if (tipo === "grupo") {
      const g = gruposEconomicos.find((x) => x.grupo === nome);
      currentEmissores = g ? [...g.emissores] : [];
    } else {
      currentEmissores = [nome];
    }
    if (!currentEmissores.length) return;
    perfilAtual = { tipo, nome };
    registrarUso(tipo === "grupo" ? "grupo" : "emissor", nome);
    reloadEmissor();
  }

  emissorRecolher.addEventListener("click", () => {
    tickersRecolhidos = !tickersRecolhidos;
    aplicarRecolhimento();
  });

  function aplicarRecolhimento() {
    emissorRecolher.textContent = tickersRecolhidos ? "Expandir linhas" : "Recolher linhas";
    document
      .querySelectorAll("#tabela-emissor-tickers tbody tr.linha-ticker")
      .forEach((tr) => { tr.hidden = tickersRecolhidos; });
  }

  async function reloadEmissor() {
    const [tabela, cards, ...series] = await Promise.all([
      fetchJSON("/api/spreads/emissor", { nome: currentEmissores }),
      fetchJSON("/api/spreads/emissor/cards", { nome: currentEmissores }),
      ...CLASSES_EMISSOR.map((c) => fetchJSON("/api/spreads/emissor/perfil-series",
                                              { nome: currentEmissores, classe: c.classe })),
    ]);
    renderCabecalhoPerfil(tabela);
    CLASSES_EMISSOR.forEach((c, i) => renderClassePerfil(c, tabela, cards, series[i]));
    renderTabelaEmissor(tabela);
    const datas = [...(tabela.tickers || []).map((t) => t.data_estoque), ...(cards.classes || []).map((c) => c.data)]
      .filter(Boolean).sort();
    escreverFonte("fonte-perfil",
      "Spread médio ponderado por estoque; mediana sem peso. Fonte: Anbima, Debentures.com e Itaú BBA",
      datas[datas.length - 1]);
  }

  // ------------------------------------------------------------------
  // 1. QUEM É: tipo, grupo (atalho para o perfil do grupo), setor e
  // subsetor; nome; uma linha com CNPJ, dívida em circulação e papéis.
  // ------------------------------------------------------------------
  function renderCabecalhoPerfil(tabela) {
    const tickers = tabela.tickers || [];
    const grupo = perfilAtual.tipo === "grupo";
    document.getElementById("perfil-tipo").textContent = grupo ? "Grupo econômico" : "Emissor";
    document.getElementById("perfil-nome").textContent = perfilAtual.nome;

    const somaPor = (campo) => {
      const m = new Map();
      tickers.forEach((t) => { if (t[campo]) m.set(t[campo], (m.get(t[campo]) || 0) + (t.estoque || 0)); });
      return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
    };
    const tags = document.getElementById("perfil-tags");
    tags.innerHTML = "";
    if (!grupo) {
      somaPor("grupo_economico").forEach((g) => {
        const temPerfil = gruposEconomicos.some((x) => x.grupo === g);
        const el = document.createElement(temPerfil ? "button" : "span");
        el.className = "perfil-tag grupo";
        el.textContent = `Grupo ${g}`;
        if (temPerfil) {
          el.type = "button";
          el.title = "Abrir o perfil do grupo econômico";
          el.addEventListener("click", () => abrirPerfil("grupo", g));
        }
        tags.appendChild(el);
      });
    }
    somaPor("setor").forEach((s) => {
      const span = document.createElement("span");
      span.className = "perfil-tag setor";
      span.textContent = s;
      tags.appendChild(span);
    });
    somaPor("subsetor").forEach((s) => {
      const span = document.createElement("span");
      span.className = "perfil-tag";
      span.textContent = s;
      tags.appendChild(span);
    });

    const geral = tabela.total_geral || {};
    const totais = Object.fromEntries((tabela.totais || []).map((t) => [t.classe, t]));
    const outros = (geral.estoque || 0) - ((totais["IPCA + Incentivadas"] || {}).estoque || 0)
      - ((totais["CDI + Tradicionais"] || {}).estoque || 0);
    const partes = [];
    if (grupo) {
      partes.push(`${currentEmissores.length} emissor${currentEmissores.length === 1 ? "" : "es"}`);
    } else {
      const cnpjs = [...new Set(tickers.map((t) => t.cnpj).filter(Boolean))];
      if (cnpjs.length === 1) partes.push(`CNPJ ${fmtCnpj(cnpjs[0])}`);
    }
    partes.push(`Dívida em circulação <b>${fmtReais(geral.estoque)}</b> em ${geral.n_ativos || 0} `
      + `debênture${geral.n_ativos === 1 ? "" : "s"}`);
    if (outros > 0.5) partes.push(`${fmtReais(outros)} em outros indexadores`);
    ["IPCA + Incentivadas", "CDI + Tradicionais"].forEach((c) => {
      if (!(totais[c] || {}).n_ativos) partes.push(`sem dívida em ${c}`);
    });
    document.getElementById("perfil-linha").innerHTML = partes.join('<span class="sep">·</span>');
  }

  // ------------------------------------------------------------------
  // 2. QUANTO PAGA: uma coluna por classe. Spread médio em destaque e, ao
  // lado, a distância para o mercado da classe (positivo = o emissor paga
  // MAIS que a média do mercado, em laranja); depois volume e duration; e
  // a série. O spread de hoje sai de /emissor/cards (mesmo número da Visão
  // Geral); mercado e mediana, do último ponto da série.
  // ------------------------------------------------------------------
  function renderClassePerfil({ classe, curto, canvas, chave }, tabela, cards, serieResp) {
    const total = (tabela.totais || []).find((t) => t.classe === classe) || {};
    const geral = (tabela.total_geral || {}).estoque || 0;
    const card = (cards.classes || []).find((c) => c.classe === classe) || {};
    const serie = serieResp.series || [];
    const metricas = document.getElementById(`metricas-${curto}`);
    const peso = document.getElementById(`peso-${curto}`);
    const el = document.getElementById(canvas);
    destroyChart(chave);

    peso.textContent = total.n_ativos
      ? `${total.n_ativos} papéis · ${geral ? Math.round((100 * (total.estoque || 0)) / geral) : 0}% da dívida`
      : "";

    // Classe sem dívida: o card some e a outra coluna ocupa a largura toda
    // -- um card vazio do tamanho de um gráfico não diz nada. A ausência
    // vira nota no cabeçalho (renderCabecalhoPerfil).
    const secao = document.getElementById(`classe-${curto}`);
    secao.style.display = total.n_ativos ? "" : "none";
    if (!total.n_ativos) return;
    el.parentElement.style.display = "";

    const mercadoPorData = Object.fromEntries((serieResp.mercado || []).map((p) => [p.data, p.spread_medio]));
    const ultimo = serie[serie.length - 1];
    const mercadoHoje = ultimo ? mercadoPorData[ultimo.data] : null;
    const spread = card.spread;
    const dif = spread !== null && spread !== undefined && mercadoHoje !== null && mercadoHoje !== undefined
      ? spread - mercadoHoje : null;
    const sinal = (v) => (v > 0 ? "+" : v < 0 ? "−" : "");
    const fmtDif = (v) => `${sinal(v)}${fmtBpsSimples(Math.abs(v))} bps`;
    const m = (rotulo, valor, detalhe, extra) => `
      <div class="perfil-metrica ${extra || ""}">
        <span class="rotulo">${rotulo}</span>
        ${valor}
        <span class="detalhe">${detalhe}</span>
      </div>`;
    metricas.innerHTML =
      m("Spread médio", `<span class="valor">${spread !== null && spread !== undefined ? fmtBpsSimples(spread) + " bps" : "—"}</span>`,
        card.spread_3m !== null && card.spread_3m !== undefined ? `média 3M ${fmtBpsSimples(card.spread_3m)} bps` : "média 3M —",
        "principal")
      + m("vs. mercado", `<span class="valor${dif > 0.05 ? " acima" : ""}">${dif !== null ? fmtDif(dif) : "—"}</span>`,
          mercadoHoje !== null && mercadoHoje !== undefined ? `mercado ${fmtBpsSimples(mercadoHoje)} bps` : "")
      + m("Volume", `<span class="valor">${fmtReais(total.estoque)}</span>`,
          ultimo ? `mediana ${fmtBpsSimples(ultimo.spread_mediana)} bps` : "")
      + m("Duration", `<span class="valor">${card.duration !== null && card.duration !== undefined
            ? card.duration.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " anos" : "—"}</span>`,
          "ponderada por estoque");

    if (!serie.length) {
      el.parentElement.style.display = "none";
      return;
    }
    const labels = serie.map((p) => p.data);
    charts[chave] = new Chart(el.getContext("2d"), {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "Spread médio", data: serie.map((p) => p.spread_medio), borderColor: "#FF6200",
            backgroundColor: "rgba(255, 98, 0, 0.07)", fill: true, borderWidth: 2, pointRadius: 0, tension: 0.15 },
          { label: "Mediana", data: serie.map((p) => p.spread_mediana), borderColor: "#111111",
            borderDash: [4, 3], borderWidth: 1.2, pointRadius: 0, tension: 0.15, fill: false },
          { label: "Mercado", data: labels.map((d) => (d in mercadoPorData ? mercadoPorData[d] : null)),
            borderColor: "#c4c4c4", borderWidth: 1.2, pointRadius: 0, tension: 0.15, fill: false, spanGaps: true },
        ],
      },
      options: PADRAO_GRAFICO.eixoData(labels, {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (it) => ` ${it.dataset.label}: ${it.raw === null ? "—" : fmtBpsSimples(it.raw) + " bps"}` } },
        },
        scales: { y: { title: { display: false } } },
      }),
    });
  }

  // ------------------------------------------------------------------
  // 3. A CURVA: papel a papel, por classe, do mais curto ao mais longo.
  // A linha da classe já traz o resumo (papéis e volume); só o total geral
  // tem linha própria. A coluna Emissor só aparece no perfil de grupo.
  // Data base aparece só quando o papel está defasado.
  // ------------------------------------------------------------------
  function renderTabelaEmissor(data) {
    const tabela = document.getElementById("tabela-emissor-tickers");
    const grupo = perfilAtual.tipo === "grupo";
    tabela.classList.toggle("sem-emissor", !grupo);
    const tbody = tabela.querySelector("tbody");
    tbody.innerHTML = "";
    const totalPorClasse = Object.fromEntries((data.totais || []).map((t) => [t.classe, t]));
    const num = (v, casas) =>
      v === null || v === undefined ? "—"
        : v.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
    const ultimaData = (data.tickers || []).map((t) => t.data_estoque).filter(Boolean).sort().pop();

    const porClasse = new Map();
    (data.tickers || []).forEach((t) => {
      const c = t.classe || "Sem classificação";
      if (!porClasse.has(c)) porClasse.set(c, []);
      porClasse.get(c).push(t);
    });
    porClasse.forEach((lista, classe) => {
      const tot = totalPorClasse[classe] || {};
      const cab = document.createElement("tr");
      cab.className = "grupo-classe";
      cab.innerHTML = `<td colspan="7">${classe}<span class="resumo">${tot.n_ativos || lista.length} papéis · ${fmtReais(tot.estoque)}</span></td>`;
      tbody.appendChild(cab);
      lista.sort((a, b) => (a.duration === null) - (b.duration === null) || (a.duration || 0) - (b.duration || 0));
      lista.forEach((t) => {
        const tr = document.createElement("tr");
        tr.className = "linha-ticker";
        const defasado = t.data_estoque && t.data_estoque !== ultimaData
          ? `<span class="defasado" title="Último boletim do papel">até ${fmtData(t.data_estoque).slice(0, 5)}</span>` : "";
        const selo = classe !== "IPCA + Incentivadas" && String(t.incentivada || "").toUpperCase().startsWith("S")
          ? '<span class="selo-12431" title="Lei 12.431 — incentivada">12.431</span>' : "";
        tr.innerHTML = `
          <td class="col-texto"><strong>${t.codigo}</strong>${defasado}</td>
          <td class="col-texto col-emissor-perfil">${t.emissor || "—"}</td>
          <td class="col-texto">${t.taxa_emissao || t.indexador || "—"}${selo}</td>
          <td>${t.data_emissao ? fmtData(t.data_emissao) : "—"}</td>
          <td>${num(t.duration, 2)}</td>
          <td><strong>${num(t.spread, 1)}</strong></td>
          <td>${num(t.estoque, 1)}</td>
        `;
        tbody.appendChild(tr);
      });
    });

    const geral = data.total_geral;
    if (geral && (data.tickers || []).length) {
      const tr = document.createElement("tr");
      tr.className = "total-geral";
      tr.innerHTML = `
        <td class="col-texto" colspan="6">Total · ${geral.n_ativos} papéis</td>
        <td>${num(geral.estoque, 1)}</td>
      `;
      tbody.appendChild(tr);
    }
    const fora = data.fora_de_circulacao || [];
    const notaFora = document.getElementById("emissor-fora");
    notaFora.style.display = fora.length ? "" : "none";
    notaFora.textContent = fora.length
      ? `Fora da conta (vencidos ou sem publicação recente na Anbima): `
        + fora.map((f) => `${f.codigo} (último boletim ${fmtData(f.data_estoque)})`).join(", ")
      : "";
    aplicarRecolhimento();
  }

  // ==================================================================
  // ABA "DISPERSÃO" (22/10/2026, pedido do Allan)
  //
  // Spread x duration num nível escolhido (ativo, emissor, grupo, setor,
  // subsetor). Uma classe por vez -- IPCA+ Incentivadas por padrão; as duas
  // nunca se misturam (referências diferentes, NTN-B x DI).
  //
  // COR E TOM. Cada setor tem uma cor; dentro do setor, cada item ganha um
  // tom dela -- o de maior estoque com a cor cheia, os seguintes clareando.
  // No nível Ativo o tom é por EMISSOR (os papéis do mesmo emissor ficam
  // com o mesmo tom). A cor de um setor não muda ao trocar de nível, para o
  // olho não precisar reaprender a legenda.
  //
  // MENOS É MAIS. A lista ao lado é a legenda e o seletor: liga e desliga
  // setores inteiros ou itens dentro deles. Nome escrito ao lado do ponto
  // só quando há poucos pontos na tela -- com centenas, vira borrão.
  // ==================================================================
  const PALETA_SETOR = ["#FF6200", "#1F4E79", "#2A9D8F", "#7B3FA0", "#C0392B", "#B8860B",
                        "#3A7D44", "#0B7285", "#8D5524", "#D6336C", "#4C6EF5", "#5C6B73"];
  // Restos de classificação ficam em cinza: não disputam atenção com setor
  // de verdade.
  const COR_NEUTRA_SETOR = { "Sem classificação": "#9AA0A6", "Outros": "#B8BEC4" };
  const ROTULO_NIVEL_DISP = { ativo: "Ativo", emissor: "Emissor", grupo: "Grupo Econômico",
                              setor: "Setor", subsetor: "Subsetor" };
  const MAX_ROTULOS_DISP = 40;
  const disp = {
    classe: document.querySelector("#disp-classe-tabs .win-btn.active")?.dataset.classe || "IPCA + Incentivadas",
    metrica: "spread_medio",
    nivel: "setor",
    data: "",                 // vazio = última disponível
    datas: [],
    pontos: [],
    corSetor: new Map(),      // estável por classe
    setoresOcultos: new Set(),
    itensOcultos: new Set(),  // rótulo do item (no nível Ativo: emissor)
    abertos: new Set(),       // setores expandidos na lista
    carregada: false,
  };

  function misturarBranco(hex, t) {
    const n = parseInt(hex.slice(1), 16);
    const c = (v) => Math.round(v + (255 - v) * t);
    return `rgb(${c(n >> 16)}, ${c((n >> 8) & 255)}, ${c(n & 255)})`;
  }

  // Chave do TOM de um ponto: o emissor no nível Ativo, o próprio item nos
  // outros. É também a chave que a lista liga/desliga.
  const chaveItem = (p) => (disp.nivel === "ativo" ? (p.emissor || p.rotulo) : p.rotulo);

  // Monta a estrutura setor -> itens, já com cor e tom.
  function estruturaDisp() {
    const porSetor = new Map();
    disp.pontos.forEach((p) => {
      if (!porSetor.has(p.setor)) porSetor.set(p.setor, { setor: p.setor, estoque: 0, itens: new Map() });
      const s = porSetor.get(p.setor);
      s.estoque += p.estoque || 0;
      const k = chaveItem(p);
      if (!s.itens.has(k)) s.itens.set(k, { chave: k, estoque: 0, n: 0 });
      const it = s.itens.get(k);
      it.estoque += p.estoque || 0;
      it.n += 1;
    });
    const setores = [...porSetor.values()].sort((a, b) => b.estoque - a.estoque);
    // Cores novas vão para os setores ainda sem cor, na ordem de estoque.
    setores.forEach((s) => {
      if (!disp.corSetor.has(s.setor)) {
        const usadas = [...disp.corSetor.keys()].filter((k) => !COR_NEUTRA_SETOR[k]).length;
        disp.corSetor.set(s.setor, COR_NEUTRA_SETOR[s.setor] || PALETA_SETOR[usadas % PALETA_SETOR.length]);
      }
      s.cor = disp.corSetor.get(s.setor);
      s.itens = [...s.itens.values()].sort((a, b) => b.estoque - a.estoque);
      const n = s.itens.length;
      s.itens.forEach((it, i) => {
        it.cor = disp.nivel === "setor" || n === 1 ? s.cor : misturarBranco(s.cor, (i / (n - 1)) * 0.62);
      });
      s.tom = new Map(s.itens.map((it) => [it.chave, it.cor]));
    });
    return setores;
  }

  function pontoVisivel(p) {
    return !disp.setoresOcultos.has(p.setor) && !disp.itensOcultos.has(chaveItem(p));
  }

  function fmtDisp(v, casas) {
    return v === null || v === undefined ? "—"
      : v.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  }

  // ORDEM DA LISTA (22/10/2026, pedido do Allan): sempre alfabética, sem
  // diferenciar acento nem caixa. Só "Sem classificação" vai para o fim
  // (é a ausência de setor, não um setor) -- mesma regra dos menus de
  // filtro da Visão Geral. As cores e tons continuam
  // decididos pelo estoque (estruturaDisp) -- a ordem da lista não muda
  // nenhuma cor.
  const RESTOS_SETOR = new Set(["Sem classificação"]);
  const alfabetica = (a, b) => a.localeCompare(b, "pt-BR", { sensitivity: "base" });

  function desenharListaDisp(setoresPorEstoque) {
    const setores = [...setoresPorEstoque].sort((a, b) =>
      (RESTOS_SETOR.has(a.setor) - RESTOS_SETOR.has(b.setor)) || alfabetica(a.setor, b.setor));
    const lista = document.getElementById("disp-lista");
    const termo = (document.getElementById("disp-busca").value || "").trim().toLowerCase();
    lista.innerHTML = "";
    const comItens = disp.nivel !== "setor";
    setores.forEach((s) => {
      const casaSetor = s.setor.toLowerCase().includes(termo);
      const itensOrdem = [...s.itens].sort((a, b) => alfabetica(a.chave, b.chave));
      const itensBusca = termo ? itensOrdem.filter((it) => it.chave.toLowerCase().includes(termo)) : itensOrdem;
      if (termo && !casaSetor && !itensBusca.length) return;
      const bloco = document.createElement("div");
      bloco.className = "disp-setor";
      const aberto = comItens && (disp.abertos.has(s.setor) || (termo && itensBusca.length && !casaSetor));
      const nItens = s.itens.length;
      const linha = document.createElement("div");
      linha.className = "disp-linha" + (disp.setoresOcultos.has(s.setor) ? " oculto" : "");
      linha.title = disp.setoresOcultos.has(s.setor) ? "Mostrar o setor" : "Esconder o setor";
      linha.innerHTML = (comItens
          ? `<button type="button" class="disp-abrir" aria-expanded="${aberto ? "true" : "false"}" aria-label="Ver itens">▸</button>`
          : "")
        + `<span class="disp-cor" style="background:${s.cor}"></span>`
        + `<span class="disp-nome">${escHtml(s.setor)}</span>`
        + `<span class="disp-n">${comItens ? nItens : ""}</span>`;
      linha.addEventListener("click", (e) => {
        if (e.target.closest(".disp-abrir")) {
          if (disp.abertos.has(s.setor)) disp.abertos.delete(s.setor); else disp.abertos.add(s.setor);
          desenharListaDisp(setores);
          return;
        }
        if (disp.setoresOcultos.has(s.setor)) disp.setoresOcultos.delete(s.setor);
        else disp.setoresOcultos.add(s.setor);
        atualizarDisp();
      });
      bloco.appendChild(linha);
      if (aberto) {
        const itens = document.createElement("div");
        itens.className = "disp-itens";
        (termo && !casaSetor ? itensBusca : itensOrdem).forEach((it) => {
          const l = document.createElement("div");
          const oculto = disp.itensOcultos.has(it.chave);
          l.className = "disp-linha" + (oculto ? " oculto" : "");
          l.title = it.chave;
          l.innerHTML = `<span class="disp-cor" style="background:${it.cor}"></span>`
            + `<span class="disp-nome">${escHtml(it.chave)}</span>`
            + (disp.nivel === "ativo" ? `<span class="disp-n">${it.n}</span>` : "");
          l.addEventListener("click", () => {
            if (oculto) {
              disp.itensOcultos.delete(it.chave);
              disp.setoresOcultos.delete(s.setor);   // ligar um item liga o setor
            } else {
              disp.itensOcultos.add(it.chave);
            }
            atualizarDisp();
          });
          itens.appendChild(l);
        });
        bloco.appendChild(itens);
      }
      lista.appendChild(bloco);
    });
    if (!lista.children.length) lista.innerHTML = '<div class="muted small" style="padding:8px 4px;">Nada encontrado.</div>';
  }

  // Nome ao lado do ponto, só com poucos pontos visíveis.
  const rotulosDisp = {
    id: "rotulosDisp",
    afterDatasetsDraw(chart) {
      const visiveis = [];
      chart.data.datasets.forEach((ds, i) => {
        if (chart.isDatasetVisible(i)) chart.getDatasetMeta(i).data.forEach((el, j) => visiveis.push([el, ds.data[j]]));
      });
      if (!visiveis.length || visiveis.length > MAX_ROTULOS_DISP) return;
      const { ctx, chartArea } = chart;
      ctx.save();
      ctx.font = `11px ${Chart.defaults.font.family}`;
      ctx.fillStyle = "#333";
      ctx.textBaseline = "middle";
      // Cada nome tenta direita, esquerda, acima e abaixo do ponto e fica na
      // primeira posição livre (sem encostar noutro nome nem sair da área).
      // Sem posição livre, não escreve -- a dica do mouse continua lá.
      const ocupados = [];
      const livre = (r) => r.x0 >= chartArea.left && r.x1 <= chartArea.right
        && r.y0 >= chartArea.top && r.y1 <= chartArea.bottom
        && ocupados.every((o) => r.x1 < o.x0 || r.x0 > o.x1 || r.y1 < o.y0 || r.y0 > o.y1);
      visiveis.sort((a, b) => (b[1].p.estoque || 0) - (a[1].p.estoque || 0));
      visiveis.forEach(([el, d]) => {
        const nome = d.p.rotulo.length > 26 ? d.p.rotulo.slice(0, 25) + "…" : d.p.rotulo;
        const w = ctx.measureText(nome).width, h = 12, g = 9;
        const opcoes = [
          { x: el.x + g, y: el.y, a: "left", r: { x0: el.x + g, x1: el.x + g + w, y0: el.y - h / 2, y1: el.y + h / 2 } },
          { x: el.x - g, y: el.y, a: "right", r: { x0: el.x - g - w, x1: el.x - g, y0: el.y - h / 2, y1: el.y + h / 2 } },
          { x: el.x, y: el.y - 13, a: "center", r: { x0: el.x - w / 2, x1: el.x + w / 2, y0: el.y - 19, y1: el.y - 7 } },
          { x: el.x, y: el.y + 13, a: "center", r: { x0: el.x - w / 2, x1: el.x + w / 2, y0: el.y + 7, y1: el.y + 19 } },
        ];
        const pos = opcoes.find((o) => livre(o.r));
        if (!pos) return;
        ocupados.push(pos.r);
        ctx.textAlign = pos.a;
        ctx.fillText(nome, pos.x, pos.y);
      });
      ctx.restore();
    },
  };

  function desenharGraficoDisp(setores) {
    destroyChart("mapa");
    const el = document.getElementById("chart-mapa");
    const medio = disp.metrica === "spread_medio";
    const raio = disp.nivel === "ativo" ? 4 : disp.nivel === "emissor" || disp.nivel === "grupo" ? 5.5 : 7;
    const datasets = setores.filter((s) => !disp.setoresOcultos.has(s.setor)).map((s) => {
      const pts = disp.pontos.filter((p) => p.setor === s.setor && pontoVisivel(p)
                                            && p.duration !== null && p[disp.metrica] !== null);
      return {
        label: s.setor,
        data: pts.map((p) => ({ x: p.duration, y: p[disp.metrica], p })),
        backgroundColor: pts.map((p) => s.tom.get(chaveItem(p))),
        borderColor: "#ffffff",
        borderWidth: 1,
        pointRadius: raio,
        pointHoverRadius: raio + 2.5,
      };
    }).filter((d) => d.data.length);

    charts.mapa = new Chart(el.getContext("2d"), {
      type: "scatter",
      data: { datasets },
      plugins: [rotulosDisp],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 250 },
        interaction: { mode: "nearest", intersect: true },
        layout: { padding: { right: 8, top: 6 } },
        scales: {
          // Folga nas pontas para nenhum ponto ficar cortado no eixo; só a
          // grade horizontal, no padrão dos painéis do time.
          x: { grace: "6%", title: { display: true, text: "Duration (anos)" }, grid: { display: false },
               ticks: { callback: (v) => fmtDisp(v, 1) } },
          y: { grace: "8%", title: { display: true, text: medio ? "Spread médio (bps)" : "Mediana do spread (bps)" },
               grid: { color: PADRAO_GRAFICO.GRADE } },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (itens) => itens[0].raw.p.rotulo,
              label: (item) => {
                const p = item.raw.p;
                const linhas = [];
                if (disp.nivel === "ativo") linhas.push(` ${p.emissor || ""}`);
                if (disp.nivel !== "setor") linhas.push(` Setor: ${p.setor}`);
                if (disp.nivel === "ativo") {
                  linhas.push(` Spread: ${fmtDisp(p.spread_medio, 1)} bps`);
                } else {
                  linhas.push(` Spread médio: ${fmtDisp(p.spread_medio, 1)} bps · mediana: ${fmtDisp(p.spread_mediana, 1)} bps`);
                  linhas.push(` ${p.n_ativos} ${p.n_ativos === 1 ? "ativo" : "ativos"}`);
                }
                linhas.push(` Duration: ${fmtDisp(p.duration, 2)} anos`);
                if (p.estoque !== null) linhas.push(` Estoque: R$ ${fmtDisp(p.estoque, 0)} mi`);
                return linhas;
              },
            },
          },
        },
        onClick(evento, elementos) {
          // No nível Ativo, clicar abre a série do papel logo abaixo do gráfico.
          if (disp.nivel !== "ativo" || !elementos.length) return;
          const { datasetIndex, index } = elementos[0];
          const p = datasets[datasetIndex].data[index].p;
          openDrilldown(p.rotulo, p.emissor, el, disp.classe);
        },
      },
    });
  }

  function atualizarDisp() {
    const setores = estruturaDisp();
    const vazio = !disp.pontos.length;
    document.getElementById("disp-vazio").style.display = vazio ? "" : "none";
    desenharListaDisp(setores);
    desenharGraficoDisp(setores);
    const visiveis = disp.pontos.filter(pontoVisivel).length;
    const nomeNivel = ROTULO_NIVEL_DISP[disp.nivel];
    document.getElementById("disp-titulo").textContent =
      `${disp.metrica === "spread_medio" ? "Spread Médio" : "Mediana do Spread"} x Duration por ${nomeNivel}`;
    const legenda = disp.nivel === "setor" ? "cor = setor"
      : disp.nivel === "ativo" ? "cor = setor, tom = emissor" : `cor = setor, tom = ${nomeNivel.toLowerCase()}`;
    const nota = disp.nivel === "ativo" && disp.metrica === "spread_mediana"
      ? " · no nível Ativo média e mediana são o próprio spread do papel" : "";
    document.getElementById("disp-sub").textContent =
      `bps em ${iso2br(disp.dataEfetiva)} · ${visiveis} de ${disp.pontos.length} pontos na tela · ${legenda}`
      + `${disp.nivel === "ativo" ? " · clique num ponto para ver a série" : ""}${nota}`;
  }

  async function carregarDisp() {
    const r = await fetchJSON("/api/spreads/mapa-dispersao", {
      classe: disp.classe, nivel: disp.nivel, data: disp.data || undefined,
    });
    disp.pontos = r.pontos || [];
    disp.dataEfetiva = r.data_referencia;
    document.getElementById("disp-texto-data").textContent = iso2br(r.data_referencia);
    escreverFonte("fonte-mapa", "Fonte: Anbima, Debentures.com e Itaú BBA", r.data_referencia);
    atualizarDisp();
  }

  async function carregarDatasDisp() {
    const r = await fetchJSON("/api/spreads/datas", { classe: disp.classe });
    disp.datas = r.datas || [];
  }

  function ligarAbas(seletor, aoTrocar) {
    document.querySelectorAll(`${seletor} .win-btn`).forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(`${seletor} .win-btn`).forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        aoTrocar(btn);
      });
    });
  }

  ligarAbas("#disp-classe-tabs", (btn) => {
    disp.classe = btn.dataset.classe;
    // Setores e grupos não são os mesmos nas duas classes: começa limpo.
    disp.corSetor = new Map();
    disp.setoresOcultos.clear(); disp.itensOcultos.clear(); disp.abertos.clear();
    registrarUso("filtro", "Dispersão: " + disp.classe);
    carregarDatasDisp();
    carregarDisp();
  });
  ligarAbas("#disp-metrica-tabs", (btn) => {
    disp.metrica = btn.dataset.metrica;
    registrarUso("filtro", "Dispersão: " + btn.textContent.trim());
    atualizarDisp();   // as duas métricas já vieram na resposta
  });
  ligarAbas("#disp-nivel-tabs", (btn) => {
    disp.nivel = btn.dataset.nivel;
    // Setores escondidos continuam escondidos; itens são outra coisa em
    // cada nível, então a seleção deles recomeça.
    disp.itensOcultos.clear();
    registrarUso("filtro", "Dispersão: nível " + btn.textContent.trim());
    carregarDisp();
  });

  document.getElementById("disp-busca").addEventListener("input", () => desenharListaDisp(estruturaDisp()));
  document.getElementById("disp-mostrar-todos").addEventListener("click", () => {
    disp.setoresOcultos.clear(); disp.itensOcultos.clear();
    atualizarDisp();
  });
  document.getElementById("disp-esconder-todos").addEventListener("click", () => {
    estruturaDisp().forEach((s) => disp.setoresOcultos.add(s.setor));
    atualizarDisp();
  });

  // Calendário de um dia só (mesmo visual do "Dados até" da Visão Geral).
  (function calendarioDisp() {
    const btn = document.getElementById("disp-btn-data");
    const painel = document.getElementById("disp-calendario");
    let mes = null;
    const fechar = () => { painel.style.display = "none"; btn.setAttribute("aria-expanded", "false"); };
    function desenhar() {
      const ano = mes.getFullYear(), m = mes.getMonth();
      const pad = (n) => String(n).padStart(2, "0");
      const chave = `${ano}-${pad(m + 1)}`;
      const datas = new Set(disp.datas);
      const primeira = disp.datas[0], ultima = disp.datas[disp.datas.length - 1];
      const atual = disp.data || ultima;
      let h = `<div class="cal-topo">
          <button type="button" class="cal-nav" data-nav="-1" aria-label="Mês anterior" ${primeira && chave > primeira.slice(0, 7) ? "" : "disabled"}>‹</button>
          <b>${MESES[m]} ${ano}</b>
          <button type="button" class="cal-nav" data-nav="1" aria-label="Próximo mês" ${ultima && chave < ultima.slice(0, 7) ? "" : "disabled"}>›</button>
        </div><div class="cal-grade">`;
      ["D", "S", "T", "Q", "Q", "S", "S"].forEach((d) => { h += `<div class="cal-semana">${d}</div>`; });
      for (let i = 0; i < new Date(ano, m, 1).getDay(); i++) h += "<div></div>";
      for (let d = 1; d <= new Date(ano, m + 1, 0).getDate(); d++) {
        const iso = `${chave}-${pad(d)}`;
        h += `<button type="button" class="cal-dia${iso === atual ? " escolhido" : ""}" data-dia="${iso}" ${datas.has(iso) ? "" : "disabled"}>${d}</button>`;
      }
      h += '</div><div class="cal-dica">Só dias com boletim podem ser escolhidos.</div>'
        + '<div class="cal-rodape"><button type="button" class="btn-small" data-acao="ultima">Usar a última data</button></div>';
      painel.innerHTML = h;
    }
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (painel.style.display === "block") { fechar(); return; }
      const ref = disp.data || disp.datas[disp.datas.length - 1];
      const base = ref ? new Date(+ref.slice(0, 4), +ref.slice(5, 7) - 1, 1) : new Date();
      mes = new Date(base.getFullYear(), base.getMonth(), 1);
      painel.style.display = "block";
      btn.setAttribute("aria-expanded", "true");
      desenhar();
    });
    painel.addEventListener("click", (e) => {
      e.stopPropagation();
      const nav = e.target.closest("[data-nav]");
      if (nav) { mes = new Date(mes.getFullYear(), mes.getMonth() + Number(nav.dataset.nav), 1); desenhar(); return; }
      if (e.target.closest('[data-acao="ultima"]')) { disp.data = ""; fechar(); carregarDisp(); return; }
      const dia = e.target.closest("[data-dia]");
      if (!dia || dia.disabled) return;
      disp.data = dia.dataset.dia === disp.datas[disp.datas.length - 1] ? "" : dia.dataset.dia;
      registrarUso("filtro", "Dispersão: data escolhida");
      fechar();
      carregarDisp();
    });
    document.addEventListener("click", fechar);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") fechar(); });
  })();

  async function abrirAbaDispersao() {
    if (disp.carregada) { if (charts.mapa) charts.mapa.resize(); return; }
    disp.carregada = true;
    await Promise.all([carregarDatasDisp(), carregarDisp()]);
  }
})();
