(function () {
  // Blocos analíticos da aba Spreads (pedido do Allan, 12/08/2026):
  // percentil histórico, curva por rating, dispersão, compressão de
  // ciclo e valor relativo. Ver app/spreads/analitico.py pro desenho e
  // pros números medidos que justificam cada gráfico.
  //
  // ARQUIVO SEPARADO DO spreads.js DE PROPÓSITO. O spreads.js já tem 887
  // linhas e cuida do que existia antes (KPIs, movers, distribuição,
  // detalhes, aba Emissores). Misturar as duas coisas num arquivo só
  // significaria que qualquer mexida nos blocos novos arrisca o que já
  // está validado e em uso.
  //
  // COMO OS DOIS CONVERSAM: não conversam. Este arquivo escuta os MESMOS
  // controles (#classe-tabs, #visao-data) e faz suas próprias chamadas.
  // Lê `e.currentTarget.dataset.classe` direto do botão clicado, e não
  // `.win-btn.active`, porque os dois listeners disparam no mesmo clique
  // e a ordem em que o browser roda não é garantida -- lendo do alvo do
  // evento não existe corrida.

  const PALETA = {
    accent: "#FF6200",
    accentDark: "#d95400",
    header: "#000000",
    muted: "#6b6b6b",
    border: "#d8d8d8",
    ok: "#1a7f4e",
    off: "#a4302a",
  };

  // Uma cor por faixa de rating, do mais forte pro mais fraco. Escala
  // sequencial (não categórica): rating É ordenado, então cores que não
  // respeitam a ordem fazem o gráfico mentir sobre a hierarquia de risco.
  const CORES_RATING = [
    "#0b3d2e", "#1a7f4e", "#4f9d69", "#8ab17d",
    "#e9c46a", "#f4a261", "#e76f51", "#a4302a",
  ];

  const charts = {};

  function corRating(i) {
    return CORES_RATING[Math.min(i, CORES_RATING.length - 1)];
  }

  function fmtBps(v, casas = 1) {
    if (v === null || v === undefined) return "—";
    return `${v.toFixed(casas)} bps`;
  }

  function fmtSinal(v, casas = 0) {
    if (v === null || v === undefined) return "—";
    return `${v > 0 ? "+" : ""}${v.toFixed(casas)}`;
  }

  function fmtMilhoes(v) {
    if (v === null || v === undefined) return "—";
    if (v >= 1000) return `R$ ${(v / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} bi`;
    return `R$ ${v.toLocaleString("pt-BR", { maximumFractionDigits: 0 })} mm`;
  }

  function fmtData(iso) {
    if (!iso) return "—";
    const [y, m, d] = iso.split("-");
    return `${d}/${m}/${y}`;
  }

  function destruir(nome) {
    if (charts[nome]) {
      charts[nome].destroy();
      delete charts[nome];
    }
  }

  function texto(id, valor) {
    const el = document.getElementById(id);
    if (el) el.textContent = valor;
  }

  function linhas(seletor, html) {
    const tb = document.querySelector(seletor);
    if (tb) tb.innerHTML = html;
  }

  // Sufixo ordinal em português pro percentil ("1º", "51º") -- invariável
  // em pt-BR, ao contrário do inglês (st/nd/rd/th).
  function ordinal(n) {
    return `${n}º`;
  }

  async function json(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${url} -> ${r.status}`);
    return r.json();
  }

  function params(classe, data) {
    const p = new URLSearchParams({ classe });
    if (data) p.set("data", data);
    return p.toString();
  }

  // -------------------------------------------------------------------
  // BLOCO 1 — nível
  // -------------------------------------------------------------------

  async function carregarPosicao(classe, data) {
    const d = await json(`/api/spreads/posicao-historica?${params(classe, data)}`);
    if (d.percentil === null || d.percentil === undefined) {
      texto("kpi-percentil", "—");
      texto("kpi-percentil-sub", "sem histórico suficiente");
      return;
    }
    texto("kpi-percentil", ordinal(d.percentil));
    // A leitura em palavras existe porque "percentil 51" não diz sozinho
    // se é pra comprar ou vender. Os cortes em 20/80 são convenção de
    // mesa, não estatística -- e ficam explícitos aqui pra quem olhar o
    // código saber que é escolha, não resultado.
    let leitura = "neutro vs. 2 anos";
    if (d.percentil <= 10) leitura = "muito apertado vs. 2 anos";
    else if (d.percentil <= 25) leitura = "apertado vs. 2 anos";
    else if (d.percentil >= 90) leitura = "muito largo vs. 2 anos";
    else if (d.percentil >= 75) leitura = "largo vs. 2 anos";
    texto(
      "kpi-percentil-sub",
      `${leitura} · faixa ${d.minimo.toFixed(0)} a ${d.maximo.toFixed(0)} bps`
    );
  }

  async function carregarCurva(classe, data) {
    const d = await json(`/api/spreads/curva?${params(classe, data)}`);
    const el = document.getElementById("chart-curva");
    if (!el) return;
    destruir("curva");

    const datasets = [];
    d.curvas.forEach((c, i) => {
      const cor = corRating(i);
      // Nuvem de pontos do rating...
      datasets.push({
        type: "scatter",
        label: c.rating,
        data: c.pontos.map((p) => ({ x: p.duration, y: p.spread, cod: p.codigo, em: p.emissor })),
        backgroundColor: cor + "55",
        borderColor: cor + "55",
        pointRadius: 2.5,
        pointHoverRadius: 5,
      });
      // ...e a reta robusta por cima, só com os dois extremos (Chart.js
      // liga com uma reta; não precisa de pontos intermediários).
      datasets.push({
        type: "line",
        label: `${c.rating} · ajuste`,
        data: [
          { x: c.duration_min, y: c.intercepto + c.roll_down_bps_ano * c.duration_min },
          { x: c.duration_max, y: c.intercepto + c.roll_down_bps_ano * c.duration_max },
        ],
        borderColor: cor,
        borderWidth: 2,
        pointRadius: 0,
        fill: false,
        // Tira a reta da legenda: senão a legenda dobra de tamanho com
        // "AAA" e "AAA · ajuste" lado a lado dizendo a mesma coisa.
        _semLegenda: true,
      });
    });

    charts.curva = new Chart(el, {
      data: { datasets },
      options: {
        responsive: true,
        interaction: { mode: "nearest", intersect: true },
        scales: {
          x: {
            type: "linear",
            title: { display: true, text: "Duration (anos)", color: PALETA.muted },
            grid: { color: "#eee" },
          },
          y: {
            title: { display: true, text: "Spread (bps)", color: PALETA.muted },
            grid: { color: "#eee" },
          },
        },
        plugins: {
          legend: {
            labels: {
              filter: (item, chart) => !chart.datasets[item.datasetIndex]._semLegenda,
              usePointStyle: true,
              boxWidth: 8,
            },
          },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const p = ctx.raw;
                if (!p.cod) return null; // ponto da reta de ajuste
                return `${p.cod} — ${p.em || ""}: ${p.y.toFixed(1)} bps · ${p.x.toFixed(1)}a`;
              },
            },
          },
        },
      },
    });

    // O roll-down é o número mais acionável do gráfico e some se ficar só
    // como inclinação visual — então vai escrito embaixo.
    const leg = d.curvas
      .map((c) => `<b>${c.rating}</b> ${fmtSinal(c.roll_down_bps_ano, 1)} bps/ano (n=${c.n})`)
      .join(" · ");
    const elLeg = document.getElementById("curva-legenda");
    if (elLeg) {
      elLeg.innerHTML = d.curvas.length
        ? `Roll-down por faixa: ${leg}`
        : "Sem faixas com papéis suficientes para ajustar curva nesta data.";
    }

    // Aproveita a curva pro card de duration: o roll-down da faixa de
    // maior estoque é a leitura de carrego que interessa no topo.
    const principal = d.curvas[0];
    if (principal) {
      texto("kpi-rolldown", `roll-down ${principal.rating}: ${fmtSinal(principal.roll_down_bps_ano, 1)} bps/ano`);
    }
  }

  async function carregarDispersao(classe, data) {
    const d = await json(`/api/spreads/dispersao?${params(classe, data)}`);
    const el = document.getElementById("chart-dispersao");
    if (!el) return;
    destruir("dispersao");

    const rotulos = d.faixas.map((f) => f.rating);
    charts.dispersao = new Chart(el, {
      type: "bar",
      data: {
        labels: rotulos,
        datasets: [
          {
            // Barra flutuante [p10, p90]: Chart.js aceita um par [min,max]
            // por ponto numa barra, que é como se desenha uma faixa sem
            // precisar de plugin de boxplot.
            label: "Faixa p10–p90",
            data: d.faixas.map((f) => [f.p10, f.p90]),
            backgroundColor: d.faixas.map((_, i) => corRating(i) + "44"),
            borderColor: d.faixas.map((_, i) => corRating(i)),
            borderWidth: 1,
          },
          {
            type: "scatter",
            label: "Mediana",
            data: d.faixas.map((f, i) => ({ x: i, y: f.mediana })),
            backgroundColor: PALETA.header,
            pointRadius: 5,
            pointStyle: "line",
            borderColor: PALETA.header,
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        scales: {
          y: { title: { display: true, text: "Spread (bps)", color: PALETA.muted }, grid: { color: "#eee" } },
          x: { grid: { display: false } },
        },
        plugins: {
          legend: { labels: { usePointStyle: true, boxWidth: 8 } },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const f = d.faixas[ctx.dataIndex];
                if (!f) return "";
                return `n=${f.n} · p10 ${f.p10} · mediana ${f.mediana} · p90 ${f.p90} (amplitude ${f.amplitude} bps)`;
              },
            },
          },
        },
      },
    });
  }

  async function carregarPorRating(classe, data) {
    const d = await json(`/api/spreads/por-rating?${params(classe, data)}`);
    linhas(
      "#tabela-por-rating tbody",
      d.linhas
        .map((l) => {
          // Divergência grande entre ponderado e mediana = faixa dominada
          // por um emissor grande. Marcada visualmente porque é armadilha
          // clássica de leitura: em AAA a média ponderada é basicamente
          // Petrobras e Vale.
          const div =
            l.spread_ponderado !== null && Math.abs(l.spread_ponderado - l.spread_mediano) > 40;
          return `<tr>
            <td><b>${l.rating}</b></td>
            <td class="num">${l.n}</td>
            <td class="num">${l.spread_ponderado === null ? "—" : l.spread_ponderado.toFixed(1)}</td>
            <td class="num"${div ? ' title="Ponderado e mediana bem diferentes: a faixa está dominada por poucos emissores grandes."' : ""}>
              ${l.spread_mediano.toFixed(1)}${div ? ' <span class="tag tag-off">⚠</span>' : ""}
            </td>
            <td class="num">${l.duration === null ? "—" : l.duration.toFixed(2)}</td>
            <td class="num">${l.estoque.toLocaleString("pt-BR", { maximumFractionDigits: 0 })}</td>
            <td class="num">${l.pct_estoque.toFixed(1)}%</td>
          </tr>`;
        })
        .join("")
    );
    texto("kpi-estoque", fmtMilhoes(d.total_estoque));
  }

  // -------------------------------------------------------------------
  // BLOCO 2 — movimento
  // -------------------------------------------------------------------

  async function carregarCompressao(classe) {
    const d = await json(`/api/spreads/compressao?classe=${encodeURIComponent(classe)}`);
    const el = document.getElementById("chart-compressao");
    if (!el) return;
    destruir("compressao");

    const rotulos = (d.pares[0]?.serie || []).map((p) => fmtData(p.data));
    charts.compressao = new Chart(el, {
      type: "line",
      data: {
        labels: rotulos,
        datasets: d.pares.map((p, i) => ({
          label: p.rotulo,
          data: p.serie.map((s) => s.valor),
          borderColor: i === 0 ? PALETA.accent : PALETA.header,
          backgroundColor: "transparent",
          borderWidth: 2,
          pointRadius: 2,
          tension: 0.25,
        })),
      },
      options: {
        responsive: true,
        scales: {
          y: {
            title: { display: true, text: "Diferencial (bps)", color: PALETA.muted },
            grid: { color: "#eee" },
          },
          x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
        },
        plugins: { legend: { labels: { usePointStyle: true, boxWidth: 8 } } },
      },
    });
  }

  // -------------------------------------------------------------------
  // BLOCO 3 — valor relativo
  // -------------------------------------------------------------------

  function preencherBarra(e) {
    const seg = (id, pct, rotulo) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.style.width = `${Math.max(pct, 0)}%`;
      // Só escreve o rótulo dentro do segmento se couber -- abaixo de 12%
      // o texto sai por cima do segmento vizinho.
      el.querySelector("span").textContent = pct >= 12 ? `${rotulo} ${pct}%` : "";
      el.title = `${rotulo}: ${pct}% da variância do spread`;
    };
    seg("seg-rating", e.pct_rating, "Rating + prazo");
    seg("seg-setor", e.pct_setor, "Setor");
    seg("seg-idio", e.pct_idiossincratico, "Idiossincrático");
    texto(
      "explica-texto",
      `${e.n} papéis · desvio do spread ${e.desvio_bruto} bps → ${e.desvio_sem_rating} bps tirando ` +
        `rating e prazo → ${e.desvio_idiossincratico} bps tirando também o setor. ` +
        `Ou seja: ${e.pct_idiossincratico}% do spread não é explicado por nenhuma classificação — ` +
        `é onde mora a análise de crédito.`
    );
  }

  function tabelaValor(seletor, itens, comSetor = true) {
    if (!itens.length) {
      linhas(seletor, `<tr><td colspan="${comSetor ? 7 : 6}" class="muted small">Sem papéis nesta faixa hoje.</td></tr>`);
      return;
    }
    linhas(
      seletor,
      itens
        .map((b) => {
          const cor = b.idiossincratico > 0 ? PALETA.ok : PALETA.off;
          return `<tr>
            <td><b>${b.codigo}</b></td>
            <td title="${b.emissor || ""}">${(b.emissor || "—").slice(0, 26)}</td>
            <td>${b.rating}</td>
            ${comSetor ? `<td class="muted small">${(b.setor || "—").slice(0, 18)}</td>` : ""}
            <td class="num">${b.spread.toFixed(1)}</td>
            <td class="num muted">${b.esperado.toFixed(1)}</td>
            <td class="num" style="color:${cor};font-weight:700;"
                title="${Math.abs(b.z).toFixed(1)}× a dispersão típica do rating ${b.rating}">
              ${fmtSinal(b.idiossincratico, 0)}
            </td>
          </tr>`;
        })
        .join("")
    );
  }

  async function carregarValorRelativo(classe, data) {
    const d = await json(`/api/spreads/valor-relativo?${params(classe, data)}`);
    if (!d.explicacao || !d.explicacao.n) {
      texto("explica-texto", "Sem faixas de rating com papéis suficientes nesta data.");
      return;
    }
    preencherBarra(d.explicacao);
    tabelaValor("#tabela-baratos tbody", d.baratos);
    tabelaValor("#tabela-caros tbody", d.caros);
    tabelaValor("#tabela-revisar tbody", d.revisar, false);

    const vazio = document.getElementById("revisar-vazio");
    if (vazio) vazio.style.display = d.revisar.length ? "none" : "";
    texto(
      "revisar-sub",
      `Papéis a mais de ${d.z_revisao.toFixed(0)}× a dispersão típica do próprio rating — ` +
        `quase sempre estresse conhecido ou taxa encalhada por falta de negócio. ` +
        `Ficam fora das listas de valor relativo para não contaminá-las.`
    );

    // Prêmio setorial
    const el = document.getElementById("chart-setor");
    if (!el) return;
    destruir("setor");
    charts.setor = new Chart(el, {
      type: "bar",
      data: {
        labels: d.setores.map((s) => s.setor.slice(0, 22)),
        datasets: [
          {
            label: "Prêmio sobre a curva do rating (bps)",
            data: d.setores.map((s) => s.premio_bps),
            backgroundColor: d.setores.map((s) => (s.premio_bps >= 0 ? PALETA.accent : PALETA.header)),
          },
        ],
      },
      options: {
        indexAxis: "y", // setores têm nome longo: barra horizontal é legível sem girar rótulo
        responsive: true,
        scales: {
          x: { title: { display: true, text: "bps", color: PALETA.muted }, grid: { color: "#eee" } },
          y: { grid: { display: false } },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const s = d.setores[ctx.dataIndex];
                return `${fmtSinal(s.premio_bps, 1)} bps · ${s.n} papéis`;
              },
            },
          },
        },
      },
    });
  }

  // -------------------------------------------------------------------
  // Orquestração
  // -------------------------------------------------------------------

  async function carregarTudo(classe, data) {
    if (!classe) return;
    // Em paralelo de propósito: o bloco de valor relativo é o mais caro
    // (~0,4 s) e em série ele seguraria os outros três.
    const tarefas = [
      carregarPosicao(classe, data),
      carregarCurva(classe, data),
      carregarDispersao(classe, data),
      carregarPorRating(classe, data),
      carregarCompressao(classe),
      carregarValorRelativo(classe, data),
    ];
    // `allSettled`, não `all`: se uma análise falhar (faixa sem papéis
    // suficientes, data sem dado), as outras cinco continuam na tela em
    // vez de a página inteira ficar vazia.
    const r = await Promise.allSettled(tarefas);
    r.filter((x) => x.status === "rejected").forEach((x) =>
      console.error("[spreads_analitico]", x.reason)
    );
  }

  function estadoAtual() {
    return {
      classe: document.querySelector("#classe-tabs .win-btn.active")?.dataset.classe || "",
      data: document.getElementById("visao-data")?.value || "",
    };
  }

  document.querySelectorAll("#classe-tabs .win-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      // Lê do botão clicado, não do `.active` — ver o comentário sobre
      // corrida de listeners no topo do arquivo.
      carregarTudo(e.currentTarget.dataset.classe, document.getElementById("visao-data")?.value || "");
    });
  });

  const inputData = document.getElementById("visao-data");
  if (inputData) {
    inputData.addEventListener("change", () => {
      const s = estadoAtual();
      carregarTudo(s.classe, s.data);
    });
  }

  const s = estadoAtual();
  carregarTudo(s.classe, s.data);
})();
