/* Aba Mercado Secundário (23/10/2026). Ver templates/mercado_secundario.html. */
(function () {
  const P = window.PADRAO_GRAFICO;
  const LARANJA = "#ec7000";
  const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  // Uma cor por emissor marcado (na ordem em que foi marcado); os tickers do
  // mesmo emissor saem em tons dela -- mesma ideia da aba Dispersão.
  const PALETA = ["#ec7000", "#1f4e79", "#2e8b57", "#8e44ad", "#c0392b", "#16a085", "#7f6000", "#2c3e50", "#d4a017", "#6d4c41"];

  const estado = {
    classe: document.querySelector("#classe-tabs .win-btn.active").dataset.classe,
    nivel: "dia",          // dia | mes | ano
    foco: null,            // drill-down: "2026-10" (dias do mês) ou "2026" (meses do ano)
    janela: "semana",
    metrica: "volume",
    meses: 3,
  };
  const sel = {
    emissores: [],         // lista da lateral (do servidor)
    marcados: new Set(),   // tickers marcados
    abertos: new Set(),    // emissores com a lista de tickers aberta
    cores: new Map(),      // emissor -> índice na paleta
    iniciado: false,       // já aplicou o padrão desta classe?
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
    Object.entries(params || {}).forEach(([k, v]) => {
      if (Array.isArray(v)) v.forEach((x) => usp.append(k, x));
      else if (v != null) usp.append(k, v);
    });
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

  // Tom de uma cor: k=0 é a própria cor, depois clareia.
  function tom(hex, k, n) {
    if (n <= 1 || k === 0) return hex;
    const f = Math.min(0.6, (k / n) * 0.75);
    const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    return "#" + c.map((x) => Math.round(x + (255 - x) * f).toString(16).padStart(2, "0")).join("");
  }

  // ---- média diária -------------------------------------------------------
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
        const periodo = j.dias_uteis ? diaMes(j.inicio) + "–" + diaMes(j.fim) + " · " + j.dias_uteis + " dias úteis" : "sem dados";
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

      const notas = ["Média diária = volume da janela ÷ dias úteis da janela (calendário ANBIMA)."];
      if (d.janelas.some((j) => j.parcial)) {
        notas.push(`* A base começa em ${dataBR(d.inicio_base)}: a média é só dos dias úteis desde então.`);
      }
      if (d.dias_sem_dado && d.dias_sem_dado.length) {
        notas.push(`Dia útil sem dado no export (entra como zero): ${d.dias_sem_dado.map(dataBR).join(", ")}.`);
      }
      const vazias = d.janelas.filter((j) => !j.dias_uteis).map((j) => j.rotulo);
      if (vazias.length) notas.push(`Sem dados de ${vazias.join(", ")} na base.`);
      if (d.cobertura != null) {
        notas.push(`As duas classes somam ${pct(d.cobertura, 0)} do volume da base; o restante é prefixado, IPCA não incentivada, CDI incentivada, % do DI e papel sem cadastro.`);
      }
      document.getElementById("resumo-nota").innerHTML = notas.map(esc).join("<br>");
      return true;
    });
  }

  // ---- volume negociado ---------------------------------------------------
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
    const diario = (p, rot) => ({ chave: p.data, rotulo: rot, volume: p.volume, pregoes: 1, papeis: p.papeis, spread: p.spread, diario: true });
    if (estado.nivel === "dia") return pts.map((p) => diario(p, p.data));
    if (estado.nivel === "mes") {
      if (estado.foco) return pts.filter((p) => p.data.slice(0, 7) === estado.foco).map((p) => diario(p, diaMes(p.data)));
      return agrupar(pts, (d) => d.slice(0, 7)).map((a) => Object.assign(a, { rotulo: mesRot(a.chave) }));
    }
    if (estado.foco) {
      return agrupar(pts.filter((p) => p.data.slice(0, 4) === estado.foco), (d) => d.slice(0, 7))
        .map((a) => Object.assign(a, { rotulo: mesRot(a.chave) }));
    }
    return agrupar(pts, (d) => d.slice(0, 4)).map((a) => Object.assign(a, { rotulo: a.chave }));
  }

  // Média móvel dos últimos 5 dias (inclui o próprio dia).
  function mediaMovel(valores, n) {
    return valores.map((_, i) => {
      if (i < n - 1) return null;
      let s = 0;
      for (let k = i - n + 1; k <= i; k++) s += valores[k];
      return s / n;
    });
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
    const linhaDiaria = estado.nivel === "dia";
    document.getElementById("volume-nota").textContent = linhaDiaria
      ? "Linha tracejada: volume do dia. Laranja: média dos últimos 5 dias."
      : "Soma do volume do período; clique numa barra para abrir os dias (ou os meses) dela. A dica mostra também a média diária.";
    const labels = barras.map((b) => (linhaDiaria ? b.chave : b.rotulo));
    const volumes = barras.map((b) => b.volume);
    const mm5 = linhaDiaria ? mediaMovel(volumes, 5) : null;

    const opcoes = {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { display: linhaDiaria, position: "bottom" },
        tooltip: {
          callbacks: {
            title: (it) => {
              const b = barras[it[0].dataIndex];
              return b.diario ? dataBR(b.chave) : b.rotulo;
            },
            label: (it) => {
              const b = barras[it.dataIndex];
              if (it.dataset.mm) return `Média 5 dias: R$ ${mi(it.raw, 1)} mi`;
              const linhas = [`Volume: R$ ${mi(b.volume, 1)} mi`];
              if (!b.diario) linhas.push(`Média diária: R$ ${mi(b.volume / b.pregoes, 1)} mi (${b.pregoes} dias com dado)`);
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
    const datasets = linhaDiaria
      ? [
          { type: "line", label: "Média 5 dias", mm: true, data: mm5, borderColor: LARANJA, backgroundColor: LARANJA,
            borderWidth: 2.5, pointRadius: 0, order: 0 },
          { type: "line", label: "Volume do dia", data: volumes, borderColor: "#333333", backgroundColor: "#333333",
            borderWidth: 1.3, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 3, order: 1 },
        ]
      : [{ type: "bar", data: volumes, backgroundColor: LARANJA, borderRadius: 2, maxBarThickness: 46 }];
    const config = {
      type: linhaDiaria ? "line" : "bar",
      data: { labels, datasets },
      options: linhaDiaria && P ? P.eixoData(labels, opcoes) : opcoes,
    };
    config.options.plugins.tooltip.callbacks = opcoes.plugins.tooltip.callbacks;
    if (graficoVolume) graficoVolume.destroy();
    graficoVolume = new Chart(document.getElementById("grafico-volume"), config);
  }

  function carregarSerie() {
    return buscar("/api/mercado-secundario/serie", { classe: estado.classe }).then((d) => {
      serieDados = d;
      desenharVolume();
    });
  }

  // ---- mais negociados ----------------------------------------------------
  function carregarRanking() {
    const tb = document.querySelector("#tabela-ranking tbody");
    tb.innerHTML = '<tr><td colspan="9" class="muted">Carregando…</td></tr>';
    document.querySelectorAll("#tabela-ranking th[data-col]").forEach((th) => {
      th.style.color = th.dataset.col === estado.metrica ? LARANJA : "";
    });
    buscar("/api/mercado-secundario/ranking", {
      classe: estado.classe, janela: estado.janela, metrica: estado.metrica, limite: 15,
    }).then((d) => {
      document.getElementById("ranking-contexto").textContent =
        `${dataBR(d.inicio)} a ${dataBR(d.fim)} · ${d.dias_uteis} dias úteis · ${fmt(d.papeis_negociados)} papéis negociados · R$ ${mi(d.volume_classe)} mi na classe`;
      if (!d.linhas.length) {
        tb.innerHTML = '<tr><td colspan="9" class="muted">Nenhum papel nesta janela.</td></tr>';
        return;
      }
      tb.innerHTML = d.linhas.map((r, i) => `
        <tr>
          <td>${i + 1}</td>
          <td class="col-texto"><b>${esc(r.codigo)}</b></td>
          <td class="col-texto">${esc(r.emissor)}</td>
          <td class="col-texto muted">${esc(r.setor || "—")}</td>
          <td>${mi(r.volume, 1)}</td>
          <td>${r.dias} de ${d.dias_uteis}</td>
          <td>${fmt(r.spread, 0)}</td>
          <td title="${r.data_estoque ? "estoque de " + dataBR(r.data_estoque) : ""}">${fmt(r.estoque, 0)}</td>
          <td>${pct(r.giro)}</td>
        </tr>`).join("");
    }).catch(() => { tb.innerHTML = '<tr><td colspan="9" class="muted">Erro ao carregar.</td></tr>'; });
  }

  // ---- spread negociado x data, por emissor -------------------------------
  function corEmissor(nome) {
    if (!sel.cores.has(nome)) {
      const usadas = new Set(sel.cores.values());
      let i = 0;
      while (usadas.has(i) && i < PALETA.length) i++;
      sel.cores.set(nome, i % PALETA.length);
    }
    return PALETA[sel.cores.get(nome)];
  }

  function corTicker(e, codigo) {
    const marcados = e.tickers.filter((t) => sel.marcados.has(t.codigo));
    const k = marcados.findIndex((t) => t.codigo === codigo);
    return tom(corEmissor(e.emissor), Math.max(0, k), marcados.length);
  }

  // Emissor sem ticker marcado devolve a cor para a paleta.
  function liberarCores() {
    sel.emissores.forEach((e) => {
      if (!e.tickers.some((t) => sel.marcados.has(t.codigo))) sel.cores.delete(e.emissor);
    });
  }

  function desenharLista() {
    const lista = document.getElementById("disp-lista");
    const termo = (document.getElementById("pontos-busca").value || "").trim().toLowerCase();
    lista.innerHTML = "";
    // Marcados primeiro, depois ordem alfabética.
    const ordem = [...sel.emissores].sort((a, b) => {
      const ma = a.tickers.some((t) => sel.marcados.has(t.codigo)), mb = b.tickers.some((t) => sel.marcados.has(t.codigo));
      return (mb - ma) || a.emissor.localeCompare(b.emissor, "pt-BR", { sensitivity: "base" });
    });
    ordem.forEach((e) => {
      const casaEmissor = e.emissor.toLowerCase().includes(termo);
      const tickersBusca = termo ? e.tickers.filter((t) => t.codigo.toLowerCase().includes(termo)) : e.tickers;
      if (termo && !casaEmissor && !tickersBusca.length) return;
      const nMarc = e.tickers.filter((t) => sel.marcados.has(t.codigo)).length;
      const aberto = sel.abertos.has(e.emissor) || (termo && !casaEmissor && tickersBusca.length);
      const bloco = document.createElement("div");
      bloco.className = "disp-setor";
      const linha = document.createElement("div");
      linha.className = "disp-linha" + (nMarc ? "" : " oculto");
      linha.title = nMarc ? "Desmarcar todos os tickers do emissor" : "Marcar todos os tickers do emissor";
      linha.innerHTML = `<button type="button" class="disp-abrir" aria-expanded="${aberto ? "true" : "false"}" aria-label="Ver tickers">▸</button>`
        + `<span class="disp-cor" style="background:${nMarc ? corEmissor(e.emissor) : "#cccccc"}"></span>`
        + `<span class="disp-nome">${esc(e.emissor)}</span>`
        + `<span class="disp-n">${nMarc ? nMarc + "/" : ""}${e.tickers.length}</span>`;
      linha.addEventListener("click", (ev) => {
        if (ev.target.closest(".disp-abrir")) {
          if (sel.abertos.has(e.emissor)) sel.abertos.delete(e.emissor); else sel.abertos.add(e.emissor);
          desenharLista();
          return;
        }
        if (nMarc) e.tickers.forEach((t) => sel.marcados.delete(t.codigo));
        else e.tickers.forEach((t) => sel.marcados.add(t.codigo));
        liberarCores();
        atualizarPontos();
      });
      bloco.appendChild(linha);
      if (aberto) {
        const itens = document.createElement("div");
        itens.className = "disp-itens";
        (termo && !casaEmissor ? tickersBusca : e.tickers).forEach((t) => {
          const marcado = sel.marcados.has(t.codigo);
          const l = document.createElement("div");
          l.className = "disp-linha" + (marcado ? "" : " oculto");
          l.title = `R$ ${mi(t.volume, 1)} mi em ${t.dias} dia(s)`;
          l.innerHTML = `<span class="disp-cor" style="background:${marcado ? corTicker(e, t.codigo) : "#cccccc"}"></span>`
            + `<span class="disp-nome">${esc(t.codigo)}</span>`
            + `<span class="disp-n">R$ ${mi(t.volume)} mi</span>`;
          l.addEventListener("click", () => {
            if (marcado) sel.marcados.delete(t.codigo); else sel.marcados.add(t.codigo);
            liberarCores();
            atualizarPontos();
          });
          itens.appendChild(l);
        });
        bloco.appendChild(itens);
      }
      lista.appendChild(bloco);
    });
    if (!lista.children.length) lista.innerHTML = '<div class="muted small" style="padding:8px 4px;">Nada encontrado.</div>';
  }

  function carregarEmissores() {
    return buscar("/api/mercado-secundario/emissores", { classe: estado.classe, meses: estado.meses }).then((d) => {
      sel.emissores = d.emissores;
      const existentes = new Set(d.emissores.flatMap((e) => e.tickers.map((t) => t.codigo)));
      [...sel.marcados].forEach((c) => { if (!existentes.has(c)) sel.marcados.delete(c); });
      if (!sel.iniciado && d.padrao) {
        sel.marcados.add(d.padrao.codigo);
        sel.abertos.add(d.padrao.emissor);
        sel.iniciado = true;
      }
      liberarCores();
      atualizarPontos();
    });
  }

  function atualizarPontos() {
    desenharLista();
    const ctx = document.getElementById("pontos-contexto");
    const codigos = [...sel.marcados];
    if (!codigos.length) {
      ctx.textContent = "Marque um emissor ou ticker na lista ao lado.";
      if (graficoPontos) { graficoPontos.destroy(); graficoPontos = null; }
      return;
    }
    buscar("/api/mercado-secundario/pontos", { classe: estado.classe, meses: estado.meses, codigo: codigos }).then((d) => {
      const labels = d.datas;
      const emissorDe = new Map(sel.emissores.map((e) => e.tickers.map((t) => [t.codigo, e])).flat());
      const volMax = Math.max(1, ...d.series.flatMap((s) => s.pontos.map((p) => p[2])));
      const volDia = labels.map(() => 0);
      let nPontos = 0;
      const datasets = d.series.map((s) => {
        const e = emissorDe.get(s.codigo);
        const cor = e ? corTicker(e, s.codigo) : "#555555";
        const dados = labels.map(() => null);
        s.pontos.forEach(([i, spread, vol]) => {
          volDia[i] += vol;
          if (spread != null) { dados[i] = { y: spread, v: vol }; nPontos += 1; }
        });
        return {
          type: "line", label: s.codigo, yAxisID: "y", order: 0,
          data: dados.map((p, i) => (p ? { x: labels[i], y: p.y, v: p.v } : { x: labels[i], y: null })),
          parsing: { xAxisKey: "x", yAxisKey: "y" },
          borderColor: cor, backgroundColor: cor, borderWidth: 1.2, spanGaps: true,
          pointRadius: (c) => (c.raw && c.raw.y != null ? 2.5 + 5.5 * Math.sqrt(c.raw.v / volMax) : 0),
          pointHoverRadius: 7, emissor: s.emissor,
        };
      });
      datasets.push({
        type: "bar", label: "Volume dos marcados", yAxisID: "y2", order: 2, volume: true,
        data: volDia.map((v) => v * 1e6), backgroundColor: "rgba(236,112,0,0.16)",
        barPercentage: 1, categoryPercentage: 0.92,
      });
      ctx.textContent = `${fmt(codigos.length)} ticker(s) marcado(s) · ${fmt(nPontos)} dias com negociação no período · ${dataBR(labels[0])} a ${dataBR(labels[labels.length - 1])}`;

      const opcoes = {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "nearest", intersect: true },
        plugins: {
          legend: { display: true, position: "bottom" },
          tooltip: {
            mode: "nearest", intersect: true,
            callbacks: {
              title: (it) => dataBR(labels[it[0].dataIndex]),
              label: (it) => {
                if (it.dataset.volume) return `Volume dos marcados: R$ ${mi(it.raw, 1)} mi`;
                return [`${it.dataset.label} · ${it.dataset.emissor}`, `Spread: ${fmt(it.raw.y, 0)} bps`, `Volume: R$ ${fmt(it.raw.v, 1)} mi`];
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false } },
          y: { position: "left", title: { display: true, text: "Spread (bps)" }, ticks: { callback: (v) => fmt(v) } },
          y2: { position: "right", beginAtZero: true, grid: { display: false },
                title: { display: true, text: "Volume (R$ mi)" }, ticks: { callback: (v) => fmt(v / 1e6) } },
        },
      };
      const config = { type: "bar", data: { labels, datasets }, options: P ? P.eixoData(labels, opcoes) : opcoes };
      config.options.plugins.tooltip.callbacks = opcoes.plugins.tooltip.callbacks;
      if (graficoPontos) graficoPontos.destroy();
      graficoPontos = new Chart(document.getElementById("grafico-pontos"), config);
    }).catch(() => { ctx.textContent = "Erro ao carregar."; });
  }

  // ---- controles --------------------------------------------------------
  function carregarClasse() {
    carregarSerie();
    carregarRanking();
    sel.marcados.clear();
    sel.abertos.clear();
    sel.cores.clear();
    sel.iniciado = false;
    carregarEmissores();
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
  ligarAbas("pontos-periodo", (ds) => { estado.meses = Number(ds.meses); carregarEmissores(); });
  let temporizador = null;
  document.getElementById("pontos-busca").addEventListener("input", () => {
    clearTimeout(temporizador);
    temporizador = setTimeout(desenharLista, 150);
  });
  document.getElementById("pontos-limpar").addEventListener("click", () => {
    sel.marcados.clear();
    sel.cores.clear();
    atualizarPontos();
  });

  carregarResumo().then((temDados) => { if (temDados) carregarClasse(); })
    .catch(() => { document.querySelector("#tabela-resumo tbody").innerHTML = '<tr><td class="muted">Erro ao carregar.</td></tr>'; });
})();
