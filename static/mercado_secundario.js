/* Aba Mercado Secundário (23/10/2026). Ver templates/mercado_secundario.html. */
(function () {
  const P = window.PADRAO_GRAFICO;
  const LARANJA = "#ec7000";
  const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

  const estado = {
    classe: document.querySelector("#classe-tabs .win-btn.active").dataset.classe,
    nivel: "dia",          // dia | mes | ano
    foco: null,            // drill-down: "2026-10" (dias do mês) ou "2026" (meses do ano)
    janela: "semana",
    metrica: "volume",
    meses: 1,
  };
  let serieDados = null;
  let graficoVolume = null;
  let graficoPontos = null;

  // ---- utilidades -----------------------------------------------------
  const fmt = (v, casas) => v == null || isNaN(v) ? "—"
    : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: casas || 0, maximumFractionDigits: casas || 0 });
  const mi = (v, casas) => v == null ? "—" : fmt(v / 1e6, casas == null ? 0 : casas);
  const pct = (v, casas) => v == null ? "—" : fmt(v * 100, casas == null ? 1 : casas) + "%";
  const dataBR = (iso) => iso ? iso.slice(8, 10) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4) : "—";
  const diaMes = (iso) => iso ? iso.slice(8, 10) + "/" + iso.slice(5, 7) : "—";
  const mesRot = (chave) => MESES[Number(chave.slice(5, 7)) - 1] + "/" + chave.slice(2, 4);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function buscar(url, params) {
    const usp = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => { if (v != null) usp.append(k, v); });
    const qs = usp.toString();
    return fetch(url + (qs ? "?" + qs : ""), { credentials: "same-origin" }).then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function ligarAbas(id, fn) {
    const box = document.getElementById(id);
    box.addEventListener("click", (ev) => {
      const b = ev.target.closest(".win-btn");
      if (!b) return;
      box.querySelectorAll(".win-btn").forEach((x) => x.classList.toggle("active", x === b));
      fn(b.dataset);
    });
  }

  // ---- 1. resumo --------------------------------------------------------
  function carregarResumo() {
    return buscar("/api/mercado-secundario/resumo").then((d) => {
      if (!d.referencia) {
        document.getElementById("ms-vazio").style.display = "";
        document.getElementById("ms-conteudo").style.display = "none";
        return false;
      }
      document.getElementById("dados-ate").textContent = "Dados até: " + dataBR(d.referencia);
      const tab = document.getElementById("tabela-resumo");
      let head = '<tr><th class="col-texto">Classe</th>';
      d.janelas.forEach((j) => {
        const periodo = j.pregoes ? diaMes(j.inicio) + "–" + diaMes(j.fim) + " · " + j.pregoes + " pregões" : "sem dados";
        head += `<th>${esc(j.rotulo)}${j.parcial ? "*" : ""}<br><span class="muted small" style="font-weight:400;">${periodo}</span></th>`;
      });
      tab.querySelector("thead").innerHTML = head + "</tr>";
      tab.querySelector("tbody").innerHTML = d.linhas.map((l) => {
        const tds = d.janelas.map((j) => {
          const c = l.janelas[j.chave];
          if (!c) return '<td class="muted">—</td>';
          return `<td><b>R$ ${mi(c.media)} mi</b><br><span class="muted small">${fmt(c.papeis_dia)} papéis/dia</span></td>`;
        }).join("");
        return `<tr><td class="col-texto"><b>${esc(l.classe)}</b></td>${tds}</tr>`;
      }).join("");

      const notas = [];
      if (d.janelas.some((j) => j.parcial)) {
        notas.push(`* A base começa em ${dataBR(d.inicio_base)}: a média é só do período disponível.`);
      }
      const vazias = d.janelas.filter((j) => !j.pregoes).map((j) => j.rotulo);
      if (vazias.length) notas.push(`Sem dados de ${vazias.join(", ")} na base.`);
      if (d.cobertura != null) {
        notas.push(`As duas classes somam ${pct(d.cobertura, 0)} do volume da base; o restante é prefixado, IPCA não incentivada, CDI incentivada, % do DI e papel sem cadastro.`);
      }
      notas.push("Média diária = volume da janela ÷ pregões da base na janela (dia sem negócio da classe conta como zero).");
      document.getElementById("resumo-nota").innerHTML = notas.map(esc).join("<br>");
      return true;
    });
  }

  // ---- 2a. volume -------------------------------------------------------
  function agrupar(pontos, chaveDe) {
    const mapa = new Map();
    pontos.forEach((p) => {
      const k = chaveDe(p.data);
      const a = mapa.get(k) || { chave: k, volume: 0, pregoes: 0, sv: 0, vs: 0 };
      a.volume += p.volume;
      a.pregoes += 1;
      if (p.spread != null) { a.sv += p.spread * p.volume; a.vs += p.volume; }
      mapa.set(k, a);
    });
    return [...mapa.values()].map((a) => Object.assign(a, { spread: a.vs ? a.sv / a.vs : null }));
  }

  function barrasDoNivel() {
    const pts = serieDados.pontos;
    if (estado.nivel === "dia") {
      return pts.map((p) => ({ chave: p.data, rotulo: p.data, volume: p.volume, pregoes: 1, papeis: p.papeis, spread: p.spread, diario: true }));
    }
    if (estado.nivel === "mes") {
      if (estado.foco) {  // dias de um mês
        return pts.filter((p) => p.data.slice(0, 7) === estado.foco)
          .map((p) => ({ chave: p.data, rotulo: diaMes(p.data), volume: p.volume, pregoes: 1, papeis: p.papeis, spread: p.spread, diario: true }));
      }
      return agrupar(pts, (d) => d.slice(0, 7)).map((a) => Object.assign(a, { rotulo: mesRot(a.chave) }));
    }
    if (estado.foco) {    // meses de um ano
      return agrupar(pts.filter((p) => p.data.slice(0, 4) === estado.foco), (d) => d.slice(0, 7))
        .map((a) => Object.assign(a, { rotulo: mesRot(a.chave) }));
    }
    return agrupar(pts, (d) => d.slice(0, 4)).map((a) => Object.assign(a, { rotulo: a.chave }));
  }

  function desenharVolume() {
    const barras = barrasDoNivel();
    const trilha = document.getElementById("volume-trilha");
    if (estado.foco) {
      const nome = estado.nivel === "mes" ? mesRot(estado.foco) : estado.foco;
      trilha.innerHTML = `<a href="#" id="volume-voltar">← todos os ${estado.nivel === "mes" ? "meses" : "anos"}</a> · ${esc(nome)}`;
      document.getElementById("volume-voltar").onclick = (ev) => { ev.preventDefault(); estado.foco = null; desenharVolume(); };
    } else {
      trilha.textContent = estado.nivel === "dia" ? "" : "Clique numa barra para abrir.";
    }
    const diario = estado.nivel === "dia" && !estado.foco;
    const labels = barras.map((b) => (diario ? b.chave : b.rotulo));
    const opcoes = {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: (it) => {
              const b = barras[it[0].dataIndex];
              return b.diario ? dataBR(b.chave) : b.rotulo;
            },
            label: (it) => {
              const b = barras[it.dataIndex];
              const linhas = [`Volume: R$ ${mi(b.volume, 1)} mi`];
              if (!b.diario) linhas.push(`Média diária: R$ ${mi(b.volume / b.pregoes, 1)} mi (${b.pregoes} pregões)`);
              else linhas.push(`Papéis negociados: ${fmt(b.papeis)}`);
              if (b.spread != null) linhas.push(`Spread médio pond.: ${fmt(b.spread, 1)} bps`);
              return linhas;
            },
          },
        },
      },
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: true, title: { display: true, text: "R$ milhões" }, ticks: { callback: (v) => fmt(v / 1e6) } },
      },
      onClick: (ev, els) => {
        if (!els.length || estado.foco || estado.nivel === "dia") return;
        estado.foco = barras[els[0].index].chave;
        desenharVolume();
      },
    };
    const config = {
      type: "bar",
      data: { labels, datasets: [{ data: barras.map((b) => b.volume), backgroundColor: LARANJA, borderRadius: 2, maxBarThickness: 46 }] },
      options: diario && P ? P.eixoData(labels, opcoes) : opcoes,
    };
    if (diario && P) {  // eixoData troca o título da dica; o nosso já trata
      config.options.plugins.tooltip.callbacks = opcoes.plugins.tooltip.callbacks;
    }
    if (graficoVolume) graficoVolume.destroy();
    graficoVolume = new Chart(document.getElementById("grafico-volume"), config);
  }

  function carregarSerie() {
    return buscar("/api/mercado-secundario/serie", { classe: estado.classe }).then((d) => {
      serieDados = d;
      desenharVolume();
    });
  }

  // ---- 2b. ranking ------------------------------------------------------
  function carregarRanking() {
    const tb = document.querySelector("#tabela-ranking tbody");
    tb.innerHTML = '<tr><td colspan="10" class="muted">Carregando…</td></tr>';
    document.querySelectorAll("#tabela-ranking th[data-col]").forEach((th) => {
      th.style.color = th.dataset.col === estado.metrica ? LARANJA : "";
    });
    buscar("/api/mercado-secundario/ranking", {
      classe: estado.classe, janela: estado.janela, metrica: estado.metrica, limite: 15,
    }).then((d) => {
      document.getElementById("ranking-contexto").textContent =
        `${dataBR(d.inicio)} a ${dataBR(d.fim)} · ${d.pregoes} pregões · ${fmt(d.papeis_negociados)} papéis negociados · R$ ${mi(d.volume_classe)} mi na classe`;
      if (!d.linhas.length) {
        tb.innerHTML = '<tr><td colspan="10" class="muted">Nenhum papel nesta janela.</td></tr>';
        return;
      }
      tb.innerHTML = d.linhas.map((r, i) => `
        <tr>
          <td>${i + 1}</td>
          <td class="col-texto"><b>${esc(r.codigo)}</b></td>
          <td class="col-texto">${esc(r.emissor)}</td>
          <td class="col-texto muted">${esc(r.setor || "—")}</td>
          <td>${mi(r.volume, 1)}</td>
          <td>${pct(r.participacao)}</td>
          <td>${r.dias}</td>
          <td>${fmt(r.spread, 0)}</td>
          <td title="${r.data_estoque ? "estoque de " + dataBR(r.data_estoque) : ""}">${fmt(r.estoque, 0)}</td>
          <td>${pct(r.giro)}</td>
        </tr>`).join("");
    }).catch(() => { tb.innerHTML = '<tr><td colspan="10" class="muted">Erro ao carregar.</td></tr>'; });
  }

  // ---- 2c. spread x data -----------------------------------------------
  function quantil(ordenado, q) {
    if (!ordenado.length) return null;
    return ordenado[Math.min(ordenado.length - 1, Math.max(0, Math.floor(q * (ordenado.length - 1))))];
  }

  function carregarPontos() {
    const ctx = document.getElementById("pontos-contexto");
    ctx.textContent = "Carregando…";
    buscar("/api/mercado-secundario/pontos", { classe: estado.classe, meses: estado.meses }).then((d) => {
      const labels = d.datas;
      const porData = new Map((serieDados ? serieDados.pontos : []).map((p) => [p.data, p]));
      const spreads = d.pontos.map((p) => p[1]).sort((a, b) => a - b);
      // Escala robusta: spreads de papel estressado (milhares de bps) achatariam
      // o resto. Corta no menor entre o percentil 2,5/97,5 e 3 intervalos
      // interquartis além dos quartis.
      const q1 = quantil(spreads, 0.25), q3 = quantil(spreads, 0.75), iqr = (q3 - q1) || 50;
      const lo = Math.max(quantil(spreads, 0.025), q1 - 3 * iqr);
      const hi = Math.min(quantil(spreads, 0.975), q3 + 3 * iqr);
      const fora = spreads.filter((s) => s < lo || s > hi).length;
      const volMax = d.pontos.reduce((m, p) => Math.max(m, p[2]), 0) || 1;
      ctx.textContent = `${fmt(d.pontos.length)} negociações (papel × dia) de ${fmt(d.tickers.length)} papéis` +
        (fora ? ` · ${fmt(fora)} fora da escala` : "");

      const pontos = d.pontos.map((p) => ({ x: labels[p[0]], y: p[1], v: p[2], t: p[3] }));
      // Linha: MEDIANA ponderada por volume, não média. Um único negócio
      // grande de papel estressado levava a média do dia a milhares de bps.
      const porDia = labels.map(() => []);
      d.pontos.forEach((p) => porDia[p[0]].push(p));
      const medianas = porDia.map((lista) => {
        if (!lista.length) return null;
        lista.sort((a, b) => a[1] - b[1]);
        const total = lista.reduce((t, p) => t + p[2], 0);
        if (!total) return lista[Math.floor(lista.length / 2)][1];
        let acc = 0;
        for (const p of lista) { acc += p[2]; if (acc >= total / 2) return p[1]; }
        return lista[lista.length - 1][1];
      });
      const opcoes = {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "nearest", intersect: true },
        plugins: {
          legend: { display: true, position: "bottom" },
          tooltip: {
            mode: "nearest", intersect: true,
            callbacks: {
              title: (it) => dataBR(it[0].raw && it[0].raw.x ? it[0].raw.x : labels[it[0].dataIndex]),
              label: (it) => {
                if (it.dataset.tipo === "pontos") {
                  const tk = d.tickers[it.raw.t];
                  return [`${tk.codigo} · ${tk.emissor}`, `Spread: ${fmt(it.raw.y, 0)} bps`, `Volume: R$ ${fmt(it.raw.v, 1)} mi`];
                }
                if (it.dataset.tipo === "volume") return `Volume do dia: R$ ${mi(it.raw, 0)} mi`;
                return `Spread mediano pond.: ${fmt(it.raw, 1)} bps`;
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false } },
          y: { position: "left", min: lo != null ? Math.floor(lo / 50) * 50 : undefined,
               max: hi != null ? Math.ceil(hi / 50) * 50 : undefined, title: { display: true, text: "Spread (bps)" },
               ticks: { callback: (v) => fmt(v) } },
          y2: { position: "right", beginAtZero: true, grid: { display: false },
                title: { display: true, text: "Volume (R$ mi)" }, ticks: { callback: (v) => fmt(v / 1e6) } },
        },
      };
      const config = {
        type: "bar",
        data: {
          labels,
          datasets: [
            { type: "line", tipo: "media", label: "Spread mediano do dia (pond. volume)", yAxisID: "y", order: 0,
              data: medianas,
              borderColor: LARANJA, backgroundColor: LARANJA, borderWidth: 2.5, pointRadius: 0, spanGaps: true },
            { type: "line", tipo: "pontos", label: "Papel no dia", yAxisID: "y", order: 1, showLine: false,
              data: pontos, backgroundColor: "rgba(17,17,17,0.22)", borderColor: "rgba(17,17,17,0.35)", borderWidth: 0.5,
              pointRadius: (c) => (c.raw ? 1.5 + 6 * Math.sqrt(c.raw.v / volMax) : 2), pointHoverRadius: 6 },
            { type: "bar", tipo: "volume", label: "Volume do dia", yAxisID: "y2", order: 2,
              data: labels.map((l) => (porData.get(l) || {}).volume || 0),
              backgroundColor: "rgba(236,112,0,0.16)", barPercentage: 1, categoryPercentage: 0.92 },
          ],
        },
        options: P ? P.eixoData(labels, opcoes) : opcoes,
      };
      config.options.plugins.tooltip.callbacks = opcoes.plugins.tooltip.callbacks;
      if (graficoPontos) graficoPontos.destroy();
      graficoPontos = new Chart(document.getElementById("grafico-pontos"), config);
    }).catch(() => { ctx.textContent = "Erro ao carregar."; });
  }

  // ---- controles --------------------------------------------------------
  function carregarClasse() {
    carregarSerie().then(carregarPontos);
    carregarRanking();
  }

  ligarAbas("classe-tabs", (ds) => {
    estado.classe = ds.classe;
    estado.foco = null;
    if (window.registrarUso) registrarUso("filtro", "Mercado secundário: " + ds.classe);
    carregarClasse();
  });
  ligarAbas("volume-nivel", (ds) => { estado.nivel = ds.nivel; estado.foco = null; if (serieDados) desenharVolume(); });
  ligarAbas("ranking-janela", (ds) => { estado.janela = ds.janela; carregarRanking(); });
  ligarAbas("ranking-metrica", (ds) => { estado.metrica = ds.metrica; carregarRanking(); });
  ligarAbas("pontos-periodo", (ds) => { estado.meses = Number(ds.meses); carregarPontos(); });

  carregarResumo().then((temDados) => { if (temDados) carregarClasse(); })
    .catch(() => { document.querySelector("#tabela-resumo tbody").innerHTML = '<tr><td class="muted">Erro ao carregar.</td></tr>'; });
})();
