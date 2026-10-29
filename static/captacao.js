/* Aba "Cap. Líquida" (29/10/2026). Ver templates/captacao.html e
 * app/captacao.py. Infra em laranja, CP Tradicional (CP ex Infra) em preto. */
(function () {
  const P = window.PADRAO_GRAFICO;
  const LARANJA = "#FF6200";
  const PRETO = "#111111";
  const INFRA = "Infra";
  const CP = "CP ex Infra";
  const COR = { [INFRA]: LARANJA, [CP]: PRETO };
  const NOME_SEG = { "Total CP": "Total CP", [CP]: "CP ex Infra", [INFRA]: "Infra" };

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const num = (v, casas) => Number(v).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  const dataBR = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : "—");

  /* R$ com unidade automática, como no dashboard original: bi, mi ou mil. */
  function reais(v, casas) {
    if (v == null || isNaN(v)) return "—";
    const a = Math.abs(v), c = casas == null ? 1 : casas;
    if (a >= 1e12) return num(v / 1e12, 2) + " tri";
    if (a >= 1e9) return num(v / 1e9, c) + " bi";
    if (a >= 1e6) return num(v / 1e6, c) + " mi";
    if (a >= 1e3) return num(v / 1e3, 0) + " mil";
    return num(v, 0);
  }
  const pct = (v, casas) => (v == null ? "—" : num(v, casas == null ? 2 : casas) + "%");

  function buscar(url) {
    return fetch(url, { credentials: "same-origin" }).then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  /* Valor em cima da barra positiva e embaixo da negativa (rótulos de dados
     do dashboard original). Com barra demais, o texto encavala: só escreve
     até `maxRotulos` barras. */
  const rotuloValor = {
    id: "rotuloValor",
    afterDatasetsDraw(chart, _args, opts) {
      const ds = chart.data.datasets[0];
      if (!ds || ds.data.length > (opts.maxRotulos || 30)) return;
      const { ctx } = chart;
      const meta = chart.getDatasetMeta(0);
      ctx.save();
      ctx.font = `600 10px ${Chart.defaults.font.family}`;
      ctx.textAlign = "center";
      ctx.fillStyle = PRETO;
      meta.data.forEach((barra, i) => {
        const v = ds.data[i];
        if (v == null) return;
        const neg = v < 0;
        ctx.textBaseline = neg ? "top" : "bottom";
        ctx.fillText(reais(v), barra.x, barra.y + (neg ? 3 : -3));
      });
      ctx.restore();
    },
  };

  const graficos = {};
  function barras(id, labels, valores, cor, opcoes) {
    const o = opcoes || {};
    if (graficos[id]) graficos[id].destroy();
    const max = Math.max(0, ...valores.filter((v) => v != null));
    const min = Math.min(0, ...valores.filter((v) => v != null));
    const folga = (max - min) * 0.14 || 1;
    graficos[id] = new Chart(document.getElementById(id), {
      type: "bar",
      data: {
        labels,
        datasets: [{
          data: valores,
          backgroundColor: valores.map((_, i) => (o.parcial && o.parcial(i) ? cor + "80" : cor)),
          borderWidth: 0, maxBarThickness: 34,
        }],
      },
      options: {
        maintainAspectRatio: false,
        layout: { padding: { top: 6, left: 14, right: 14 } },
        plugins: {
          legend: { display: false },
          rotuloValor: { maxRotulos: o.maxRotulos || 30 },
          tooltip: {
            callbacks: {
              title: (itens) => (o.titulo ? o.titulo(itens[0].dataIndex) : itens[0].label),
              label: (it) => " R$ " + reais(it.raw, 2),
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { autoSkip: true, maxRotation: 90, minRotation: o.girar ? 90 : 0 },
          },
          y: {
            suggestedMax: max + folga, suggestedMin: min - folga,
            ticks: { callback: (v) => reais(v, 0) },
          },
        },
      },
      plugins: [P.linhaZero, rotuloValor],
    });
  }

  // ---------------------------------------------------------------------
  // Quadros do topo
  // ---------------------------------------------------------------------
  function classeLinha(seg) {
    return seg === INFRA ? "seg-infra" : seg === CP ? "seg-cp" : "seg-total";
  }

  function tabelaSeg(id, cab, linhas, celulas) {
    const t = document.getElementById(id);
    t.innerHTML = `<thead><tr><th></th>${cab.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${
      linhas.map((l) => `<tr class="${classeLinha(l.segmento)}"><td class="col-texto">${esc(NOME_SEG[l.segmento] || l.segmento)}</td>${
        celulas(l).map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody>`;
  }

  let resumoAtual = null;
  function desenharQuadros(r) {
    resumoAtual = r;
    document.getElementById("dados-ate").textContent = "Dados até: " + dataBR(r.referencia);
    if (r.last_pl) {
      tabelaSeg("q-pl", ["PL (R$)", "Fundos"], r.last_pl, (l) => [reais(l.pl), l.fundos == null ? "—" : num(l.fundos, 0)]);
      document.getElementById("q-pl-nota").textContent = `PL na data mais recente da base (${dataBR(r.referencia)}).`;
    }
    const anos = r.anos || [];
    const rot = (a, i) => (i === anos.length - 1 ? `YTD'${a.slice(2)}` : `FY'${a.slice(2)}`);
    tabelaSeg("q-ano", [...anos.map(rot), "%PL"], r.por_ano || [],
      (l) => [...anos.map((a) => reais(l[a])), pct(l.pct_pl_ytd, 1)]);

    const f = r.mes_fechado, c = r.mes_corrente;
    const segs = ((f || c) || { linhas: [] }).linhas.map((l) => l.segmento);
    const achar = (q, seg) => (q ? q.linhas.find((l) => l.segmento === seg) || {} : {});
    const cab = [];
    if (f) cab.push(P.mesAno(f.mes), "%PL");
    if (c) cab.push(`${P.mesAno(c.mes)}*`, "%PL");
    tabelaSeg("q-mes", cab, segs.map((s) => ({ segmento: s })), (l) => {
      const out = [];
      if (f) { const x = achar(f, l.segmento); out.push(reais(x.cap), pct(x.pct_pl)); }
      if (c) { const x = achar(c, l.segmento); out.push(reais(x.cap), pct(x.pct_pl)); }
      return out;
    });
    if (c) {
      const nota = document.querySelector("#q-mes + .fonte-visual");
      nota.textContent = `%PL = captação do mês ÷ PL do mesmo mês. * ${P.mesAno(c.mes)} até ${dataBR(r.referencia)}.`;
    }
  }

  // ---------------------------------------------------------------------
  // Mensal + matriz
  // ---------------------------------------------------------------------
  let serieMensal = null;
  let janelaMeses = 13;

  function mesParcial(isoMes) {
    const ref = resumoAtual && resumoAtual.referencia;
    return !!ref && isoMes.slice(0, 7) === ref.slice(0, 7);
  }

  function desenharMensal() {
    const d = serieMensal;
    if (!d || !d.meses.length) return;
    const ini = janelaMeses ? Math.max(0, d.meses.length - janelaMeses) : 0;
    const meses = d.meses.slice(ini);
    const rotulos = meses.map((m) => P.mesAno(m) + (mesParcial(m) ? "*" : ""));
    const muitos = meses.length > 24;
    [[INFRA, "g-mensal-infra", "nota-mensal-infra"], [CP, "g-mensal-cp", "nota-mensal-cp"]].forEach(([seg, id, nota]) => {
      const vals = d.series[seg].captacao.slice(ini);
      barras(id, rotulos, vals, COR[seg], {
        girar: true, maxRotulos: 24,
        parcial: (i) => mesParcial(meses[i]),
      });
      const parcial = meses.some(mesParcial);
      const soma = vals.reduce((a, v) => a + (v || 0), 0);
      document.getElementById(nota).textContent =
        `Soma no período: R$ ${reais(soma)}.` + (parcial ? ` * mês corrente, até ${dataBR(resumoAtual.referencia)}.` : "") +
        (muitos ? " Valores na dica de ferramenta (rótulos omitidos com mais de 24 meses)." : "");
    });
  }

  /* Faixas do dashboard original (por gestora: ±100 mi e ±500 mi), ×10
     para o agregado do segmento. */
  function corFaixa(v) {
    if (v == null) return "";
    if (v < -5e9) return "#ED6B64";
    if (v < -1e9) return "#EF9A9A";
    if (v <= 1e9) return "#EEEEEE";
    if (v <= 5e9) return "#E8F5E9";
    return "#7B9B72";
  }

  function desenharMatriz() {
    const d = serieMensal, r = resumoAtual;
    if (!d || !d.meses.length || !r) return;
    const ano = d.meses[d.meses.length - 1].slice(0, 4);
    const idx = d.meses.map((m, i) => [m, i]).filter(([m]) => m.startsWith(ano));
    const ult = idx[idx.length - 1];
    document.getElementById("matriz-titulo").innerHTML = `<b>Captação líquida mensal</b> | por segmento, ${ano}`;
    const segs = ["Total CP", CP, INFRA];
    const valor = (seg, i) => {
      if (seg === "Total CP") {
        const a = d.series[CP].captacao[i], b = d.series[INFRA].captacao[i];
        return a == null && b == null ? null : (a || 0) + (b || 0);
      }
      return d.series[seg].captacao[i];
    };
    const plMes = (seg, i) => (seg === "Total CP"
      ? (d.series[CP].pl[i] || 0) + (d.series[INFRA].pl[i] || 0) : d.series[seg].pl[i]);
    const anoLinha = (seg) => (r.por_ano || []).find((l) => l.segmento === seg) || {};
    const cab = idx.map(([m]) => P.mesAno(m) + (mesParcial(m) ? "*" : ""));
    const corpo = segs.map((seg) => {
      const ytd = idx.reduce((a, [, i]) => a + (valor(seg, i) || 0), 0);
      const mtd = ult ? valor(seg, ult[1]) : null;
      const mtdPct = ult && plMes(seg, ult[1]) ? (100 * mtd) / plMes(seg, ult[1]) : null;
      return `<tr class="${classeLinha(seg)}"><td class="col-texto">${esc(NOME_SEG[seg])}</td>${
        idx.map(([, i]) => { const v = valor(seg, i); return `<td style="background:${corFaixa(v)}">${reais(v)}</td>`; }).join("")
      }<td class="cl-sep">${reais(ytd)}</td><td class="cl-sep">${pct(mtdPct)}</td><td>${pct(anoLinha(seg).pct_pl_ytd, 1)}</td></tr>`;
    }).join("");
    document.getElementById("matriz").innerHTML =
      `<thead><tr><th class="col-texto">Segmento</th>${cab.map((c) => `<th>${c}</th>`).join("")}` +
      `<th class="cl-sep">YTD'${ano.slice(2)}</th><th class="cl-sep">MTD / %PL</th><th>YTD %PL</th></tr></thead><tbody>${corpo}</tbody>`;
  }

  // ---------------------------------------------------------------------
  // Diário
  // ---------------------------------------------------------------------
  function carregarDiario(dias) {
    buscar(`/api/captacao/diario?dias=${dias}`).then((d) => {
      const rot = d.datas.map((x) => P.diaMesCurto(x));
      [[INFRA, "g-diario-infra", "nota-diario-infra"], [CP, "g-diario-cp", "nota-diario-cp"]].forEach(([seg, id, nota]) => {
        const vals = d.series[seg] || [];
        barras(id, rot, vals, COR[seg], {
          girar: true, maxRotulos: 20,
          titulo: (i) => P.dataCheia(d.datas[i]),
        });
        const soma = vals.reduce((a, v) => a + (v || 0), 0);
        document.getElementById(nota).textContent = d.datas.length
          ? `De ${dataBR(d.datas[0])} a ${dataBR(d.datas[d.datas.length - 1])}: R$ ${reais(soma)} no acumulado.`
            + (d.datas.length > 20 ? " Valores na dica de ferramenta." : "")
          : "Sem dados diários.";
      });
    }).catch(() => {});
  }

  // ---------------------------------------------------------------------
  // Amplitude
  // ---------------------------------------------------------------------
  function carregarAmplitude(seg) {
    buscar(`/api/captacao/amplitude?segmento=${encodeURIComponent(seg)}`).then((d) => {
      const m = d.meses || [];
      const rot = m.map((x) => P.mesAno(x.mes) + (mesParcial(x.mes) ? "*" : ""));
      if (graficos.amp) graficos.amp.destroy();
      const conj = [
        ["Captando", "pct_pl_captando", "#7B9B72", "#ffffff"],
        ["Sem fluxo", "pct_pl_neutro", "#EEEEEE", PRETO],
        ["Resgatando", "pct_pl_resgatando", "#ED6B64", "#ffffff"],
      ];
      graficos.amp = new Chart(document.getElementById("g-amplitude"), {
        type: "bar",
        data: {
          labels: rot,
          datasets: conj.map(([nome, campo, cor, corRot]) => ({
            label: nome + " (% do PL)", data: m.map((x) => x[campo]), backgroundColor: cor,
            rotulos: m.map((x) => (x[campo] >= 8 ? num(x[campo], 0) + "%" : "")), corRotulo: corRot,
            borderWidth: 0, maxBarThickness: 46,
          })),
        },
        options: {
          maintainAspectRatio: false,
          plugins: { tooltip: { callbacks: { label: (it) => ` ${it.dataset.label}: ${pct(it.raw, 1)}` } } },
          scales: {
            x: { stacked: true, grid: { display: false } },
            y: { stacked: true, max: 100, ticks: { callback: (v) => v + "%" } },
          },
        },
        plugins: [P.rotulosNasBarras],
      });
      const linhas = [
        ["Captação líquida (R$)", (x) => reais(x.liquida)],
        ["Captação bruta (R$)", (x) => reais(x.captacao_bruta)],
        ["Resgate bruto (R$)", (x) => reais(x.resgate_bruto)],
        ["Gestoras captando", (x) => num(x.gestoras_captando, 0)],
        ["Gestoras resgatando", (x) => num(x.gestoras_resgatando, 0)],
        [`Top ${d.top} resgates / resgate bruto`, (x) => pct(x.top_resgate_pct, 0)],
        [`Top ${d.top} captações / captação bruta`, (x) => pct(x.top_captacao_pct, 0)],
      ];
      document.getElementById("amplitude-tabela").innerHTML =
        `<thead><tr><th class="col-texto">${seg === INFRA ? "Infra" : "CP Tradicional"}</th>${rot.map((x) => `<th>${x}</th>`).join("")}</tr></thead>` +
        `<tbody>${linhas.map(([n, f]) => `<tr><td class="col-texto">${n}</td>${m.map((x) => `<td>${f(x)}</td>`).join("")}</tr>`).join("")}</tbody>`;
    }).catch(() => {});
  }

  // ---------------------------------------------------------------------
  // Abas e carga
  // ---------------------------------------------------------------------
  function abas(idGrupo, atributo, aoTrocar) {
    const grupo = document.getElementById(idGrupo);
    grupo.addEventListener("click", (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      grupo.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
      aoTrocar(b.dataset[atributo]);
    });
  }
  abas("mensal-janela", "meses", (v) => { janelaMeses = Number(v); desenharMensal(); });
  abas("diario-janela", "dias", (v) => carregarDiario(Number(v)));
  abas("amplitude-segmento", "seg", (v) => carregarAmplitude(v));

  Promise.all([buscar("/api/captacao/resumo"), buscar("/api/captacao/mensal")])
    .then(([r, m]) => {
      if (!r.referencia && !(m.meses || []).length) {
        document.getElementById("cl-vazio").style.display = "";
        document.getElementById("cl-conteudo").style.display = "none";
        return;
      }
      desenharQuadros(r);
      serieMensal = m;
      desenharMensal();
      desenharMatriz();
      carregarDiario(15);
      carregarAmplitude(INFRA);
    })
    .catch(() => {
      document.getElementById("dados-ate").textContent = "Erro ao carregar";
    });
})();
