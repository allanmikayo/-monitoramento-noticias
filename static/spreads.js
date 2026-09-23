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
  const rotuloInicio = document.getElementById("rotulo-inicio");

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
    if (!visaoDataInput.value && data.data_referencia) visaoDataInput.max = data.data_referencia;
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
    // Mora na barra fixa: curta de propósito, para caber numa linha.
    document.getElementById("nota-base").textContent =
      data.data_referencia
        ? `Boletim ${PADRAO_GRAFICO.diaMesCurto(data.data_referencia)}` +
          (data.data_comparacao ? ` vs ${PADRAO_GRAFICO.diaMesCurto(data.data_comparacao)}`
                                : ` · ${currentBase} sem histórico para comparar`)
        : "Sem dado para a data selecionada.";
    escreverFonte("kpi-nota", "Médias ponderadas por estoque. Fonte: Anbima, Debentures.com e Itaú BBA",
                  data.data_referencia);
  }

  // ------------------------------------------------------------------
  // Gráfico 1 -- Evolução do spread médio (linha)
  // ------------------------------------------------------------------
  async function loadSeriesChart() {
    const { series } = await fetchJSON("/api/spreads/series", {
      classe: currentClasse, setor: filtros.setor, subsetor: filtros.subsetor, grupo: filtros.grupo,
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
          // BASE COMPARÁVEL (21/09/2026) -- índice encadeado só com papéis
          // presentes nos dois dias de cada passo, ancorado no spread de
          // hoje (ver queries._serie_base_comparavel). A distância entre as
          // duas linhas numa data passada é quanto do movimento de lá até
          // hoje veio da MUDANÇA DE BASE, e não de repricing. Preta e fina,
          // sem preenchimento: é a linha de controle, a laranja segue sendo
          // o protagonista.
          label: "Base comparável",
          data: series.map((r) => r.spread_comparavel ?? null),
          borderColor: "#111111",
          backgroundColor: "transparent",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.15,
          fill: false,
        }],
      },
      options: PADRAO_GRAFICO.eixoData(datasIso, {
        responsive: true,
        plugins: { legend: { display: true, position: "bottom" } },
        scales: { y: { title: { display: true, text: "bps" } } },
      }),
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
      `Top 10 ${aberturas ? "aberturas" : "fechamentos"}`;
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
        <td class="col-texto">${r.nome || "—"}</td>
        <td>${num1(r.spread)}</td>
        <td class="${cellClass}">${fmtBps(r.variacao_bps)}</td>
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
        else { openDrilldown(l.rotulo, l.nome); }
      });
      tbody.appendChild(tr);
    });
  }

  async function openDrilldown(codigo, nome) {
    registrarUso("ticker", codigo);
    currentDrilldownCodigo = codigo;
    drilldownWrap.style.display = "block";
    document.getElementById("drilldown-titulo").textContent = `${codigo}${nome ? " — " + nome : ""}`;
    drilldownWrap.scrollIntoView({ behavior: "smooth", block: "nearest" });

    const { series } = await fetchJSON("/api/spreads/series", { classe: currentClasse, codigo });
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

  btnFecharDrilldown.addEventListener("click", () => {
    drilldownWrap.style.display = "none";
    destroyChart("drilldown");
    currentDrilldownCodigo = null;
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
    document.getElementById("titulo-composicao").textContent =
      `Evolução da variação de spreads — ${currentClasse}`;

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
    document.getElementById("titulo-dispersao").textContent =
      `Spread x Duration — ${currentClasse}`;

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
          legend: {
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
          openDrilldown(p.codigo, p.nome);
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
  // depreciaram". Cada barra vai do PU atual até o par (100%): o
  // COMPRIMENTO da barra é o deságio. Uma barra começando no zero do eixo
  // mostraria 71% e 99% quase do mesmo tamanho e esconderia justamente a
  // diferença que interessa.
  // ------------------------------------------------------------------
  async function loadDesagios() {
    const data = await fetchJSON("/api/spreads/desagios", {
      classe: currentClasse, data: visaoDataInput.value || undefined,
      setor: filtros.setor, subsetor: filtros.subsetor, grupo: filtros.grupo,
    });
    destroyChart("desagios");
    const el = document.getElementById("chart-desagios");
    el.parentElement.querySelector(".muted-msg")?.remove();
    document.getElementById("titulo-desagios").textContent =
      `Maiores deságios — ${currentClasse}`;
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
    const menor = Math.min(...papeis.map((p) => p.pct_pu_par));
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
      return `${curto} · ${p.codigo}${p.spread === null ? " *" : ""}`;
    };
    const semTaxa = papeis.filter((p) => p.spread === null).length;

    charts.desagios = new Chart(el.getContext("2d"), {
      type: "bar",
      data: {
        labels: papeis.map(rotulo),
        datasets: [{
          label: "% do PU par",
          data: papeis.map((p) => [p.pct_pu_par, 100]),
          backgroundColor: "#FF6200",
          borderWidth: 0,
          barPercentage: 0.7,
          rotulos: papeis.map((p) => `${fmt(p.pct_pu_par, 1)}%`),
        }],
      },
      plugins: [{
        // Valor escrito à esquerda da barra, onde ela começa -- é o número
        // que se lê primeiro.
        id: "valorDesagio",
        afterDatasetsDraw(chart) {
          const { ctx } = chart;
          const meta = chart.getDatasetMeta(0);
          ctx.save();
          ctx.font = `600 11px ${Chart.defaults.font.family}`;
          ctx.fillStyle = PADRAO_GRAFICO.PRETO;
          ctx.textAlign = "right";
          ctx.textBaseline = "middle";
          meta.data.forEach((barra, i) => {
            const { x, base, y } = barra.getProps(["x", "base", "y"], true);
            ctx.fillText(chart.data.datasets[0].rotulos[i], Math.min(x, base) - 5, y);
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
            // Espaço à esquerda para o rótulo de valor da barra mais longa.
            min: Math.floor((menor - 6) / 5) * 5,
            max: 102,
            title: { display: true, text: "% do PU par" },
            ticks: { callback: (v) => `${v}%` },
            grid: { color: PADRAO_GRAFICO.GRADE },
          },
          y: { grid: { display: false }, ticks: { autoSkip: false, font: { size: 10.5 } } },
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
          openDrilldown(p.codigo, p.emissor);
        },
      },
    });
    // A nota do asterisco fica na linha de fonte, que já é o lugar das
    // ressalvas de base nesta página.
    escreverFonte("fonte-desagios",
      semTaxa
        ? "* papel com PU publicado e sem taxa indicativa na data — fica fora das contas de spread. "
          + "Fonte: Anbima e Itaú BBA"
        : "Fonte: Anbima e Itaú BBA",
      data.data_referencia);
  }

  async function reloadAll() {
    await Promise.all([
      loadKPI(), loadSeriesChart(), loadSetor(), loadMovers(),
      loadComposicao(), loadDispersao(), loadDesagios(),
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
      drilldownWrap.style.display = "none";
      destroyChart("drilldown");
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
  }

  baseTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      baseTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentBase = btn.dataset.base;
      registrarUso("filtro", "Base: " + btn.dataset.base);
      const personalizada = currentBase === BASE_PERSONALIZADA;
      rotuloInicio.style.display = personalizada ? "flex" : "none";
      if (personalizada && !inicioInput.value) {
        // Sem data ainda: mostra o campo, dá o foco e espera -- pedir ao
        // servidor uma comparação sem ponto de partida daria 400.
        document.getElementById("nota-base").textContent =
          "Escolha a data inicial da comparação em “De”.";
        inicioInput.focus();
        return;
      }
      recarregarComparacao();
    });
  });

  // Data inicial da comparação (base "Personalizado").
  inicioInput.addEventListener("change", () => {
    if (currentBase !== BASE_PERSONALIZADA) return;
    registrarUso("filtro", "Base: data escolhida");
    recarregarComparacao();
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

  // Campo de data da Visão Geral (pedido do Allan, 27/07/2026) -- não mexe
  // no gráfico de evolução (a linha mostra o histórico inteiro de qualquer
  // forma), só nos cartões, na tabela de setor e nos movers, que passam a
  // olhar "como se hoje fosse" a data escolhida.
  visaoDataInput.addEventListener("change", () => {
    loadKPI();
    loadSetor();
    loadMovers();
    loadComposicao();
    loadDispersao();
    loadDesagios();
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
  const emissorLimpar = document.getElementById("emissor-limpar");
  const emissorRecolher = document.getElementById("emissor-recolher");
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
  // Estado do "Recolher linhas" da tabela de dívidas. Guardado aqui, e não
  // lido do DOM, para sobreviver ao redesenho da tabela a cada seleção.
  let tickersRecolhidos = false;

  // Linha de fonte de cada visual. A data é a DO PRÓPRIO VISUAL, não a do
  // cabeçalho da página: cada bloco aqui pode se apoiar num dia diferente
  // (papel sem publicação num pregão traz o dia anterior), e uma data global
  // afirmaria que todos estão no mesmo dia quando não estão.
  function escreverFonte(id, texto, iso) {
    const el = document.getElementById(id);
    if (!el) return;
    const [, m, d] = (iso || "").split("-");
    el.textContent = d ? `${texto} (dado disponível até ${d}/${m})` : texto;
  }
  let currentNivel = document.querySelector("#emissor-nivel-tabs .win-btn.active")?.dataset.nivel || "emissor";
  let emissoresCarregados = false;

  secaoTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      secaoTabs.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const secao = btn.dataset.secao;
      registrarUso("subaba", btn.textContent.trim());
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
      div.className = "search-item" + (todos ? " grupo-selecionado" : "");
      div.innerHTML = `<span>${g.grupo}</span>` +
        `<span class="muted small">${g.n_emissores} emissor${g.n_emissores === 1 ? "" : "es"}` +
        `${todos ? " · clique para remover" : ""}</span>`;
      div.addEventListener("click", () => {
        // Alterna: grupo inteiro já selecionado sai da seleção. Sem isto,
        // desfazer um clique que trouxe 11 emissores seria 11 cliques nos
        // "×" dos chips.
        if (todos) {
          currentEmissores = currentEmissores.filter((n) => !g.emissores.includes(n));
        } else {
          registrarUso("grupo", g.grupo);
          g.emissores.forEach((n) => {
            if (!currentEmissores.includes(n)) currentEmissores.push(n);
          });
        }
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
    // Só aparece quando há o que limpar -- botão morto na tela é ruído.
    emissorLimpar.style.display = currentEmissores.length ? "" : "none";
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
          registrarUso("emissor", nome);
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

  emissorLimpar.addEventListener("click", () => {
    currentEmissores = [];
    renderChips();
    renderGrupos();
    atualizarPainelEmissor();
  });

  emissorRecolher.addEventListener("click", () => {
    tickersRecolhidos = !tickersRecolhidos;
    aplicarRecolhimento();
  });

  // Esconde/mostra as linhas de papel, deixando cabeçalho de grupo, totais
  // por classe e total geral sempre visíveis -- é o resumo que a pessoa quer
  // ver quando recolhe.
  function aplicarRecolhimento() {
    emissorRecolher.textContent = tickersRecolhidos ? "Expandir linhas" : "Recolher linhas";
    document
      .querySelectorAll("#tabela-emissor-tickers tbody tr.linha-ticker")
      .forEach((tr) => { tr.hidden = tickersRecolhidos; });
  }

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
      loadEmissorTabela(), loadEmissorCharts(),
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
    const datasCards = (data.classes || []).map((c) => c.data).filter(Boolean).sort();
    escreverFonte("fonte-cards", "Fonte: Anbima e Itaú BBA",
                  datasCards[datasCards.length - 1]);
  }

  async function loadEmissorTabela() {
    document.getElementById("emissor-titulo-tabela").textContent =
      currentEmissores.length === 1
        ? `Debêntures Emitidas — ${currentEmissores[0]}`
        : `Debêntures Emitidas — ${currentEmissores.length} emissores selecionados`;
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
        cab.innerHTML = `<td colspan="9">${classe}</td>`;
        tbody.appendChild(cab);
      }
      const tr = document.createElement("tr");
      // `linha-ticker` é o que o botão "Recolher linhas" esconde -- cabeçalho
      // de grupo e totais nunca somem, senão recolher apagaria o resumo.
      tr.className = "linha-ticker";
      tr.innerHTML = `
        <td><strong>${t.codigo}</strong></td>
        <td class="col-emissor">${t.emissor || "—"}</td>
        <td>${t.indexador || "—"}</td>
        <td>${t.incentivada || "—"}</td>
        <td>${t.data_emissao ? fmtData(t.data_emissao) : "—"}</td>
        <td>${t.taxa_emissao || "—"}</td>
        <td>${num(t.duration, 2)}</td>
        <td>${num(t.estoque, 1)}</td>
        <td>${t.data_estoque ? fmtData(t.data_estoque) : "—"}</td>
      `;
      tbody.appendChild(tr);
    });
    if (classeAtual !== null) tbody.appendChild(linhaTotal(classeAtual));

    // TOTAL GERAL (11/09/2026, pedido do Allan). Só estoque e contagem.
    // Spread e duration NÃO entram aqui: IPCA+ se mede contra a NTN-B e CDI+
    // contra o DI, então uma média entre as duas não significaria nada.
    // Estoque é saldo em reais -- a dívida a mercado do emissor é a soma de
    // tudo que ele tem em pé, qualquer que seja o indexador.
    const geral = data.total_geral;
    if (geral && (data.tickers || []).length) {
      const tr = document.createElement("tr");
      tr.className = "total-geral";
      tr.innerHTML = `
        <td colspan="7" class="col-emissor">Total geral · ${geral.n_ativos} ativo${geral.n_ativos === 1 ? "" : "s"}</td>
        <td>${num(geral.estoque, 1)}</td>
        <td></td>
      `;
      tbody.appendChild(tr);
    }

    aplicarRecolhimento();
    const datasTabela = (data.tickers || []).map((t) => t.data_estoque).filter(Boolean).sort();
    escreverFonte("fonte-tabela", "Fonte: Debentures.com.br, Anbima e Itaú BBA",
                  datasTabela[datasTabela.length - 1]);

    function linhaTotal(classe) {
      const t = totalPorClasse[classe] || {};
      const tr = document.createElement("tr");
      tr.className = "total-classe";
      tr.innerHTML = `
        <td colspan="7" class="col-emissor">
          Total ${classe} · ${t.n_ativos || 0} ativo${(t.n_ativos || 0) === 1 ? "" : "s"}
        </td>
        <td>${num(t.estoque, 1)}</td>
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
    escreverFonte(`fonte-grafico-${sufixo}`, "Fonte: Anbima e Itaú BBA",
                  labels[labels.length - 1]);

    // Emissor sem papel nesta classe simplesmente não vira linha (decisão do
    // Allan, 11/09/2026). A versão anterior listava os ausentes no subtítulo;
    // virava um parágrafo comprido dizendo o que NÃO está no gráfico.

    if (!labels.length) {
      charts[chave] = null;
      el.style.display = "none";
      const msg = document.createElement("p");
      msg.className = "muted small muted-msg";
      msg.textContent = "Sem papel precificado nesta classe.";
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
      data: { labels, datasets },
      options: PADRAO_GRAFICO.eixoData(labels, {
        responsive: true,
        plugins: { legend: { display: true, position: "bottom" } },
        scales: { y: { title: { display: true, text: "bps" } } },
      }),
    });
  }

  // O PAINEL DE NOTÍCIAS SAIU DESTA ABA (11/09/2026, pedido do Allan) --
  // a tela ficou densa demais e notícia tem a aba própria dela. A rota
  // /api/spreads/emissor/noticias e as consultas (`company_news`,
  // `sector_news`, `setores_dos_emissores`) continuam de pé e testadas: o
  // que saiu foi a caixa, não a capacidade.

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
