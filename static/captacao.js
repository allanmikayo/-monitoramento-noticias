/* Aba "Cap. Líquida" (29/10/2026). Ver templates/captacao.html e
 * app/captacao.py. Infra em laranja, CP Tradicional (CP ex Infra) em preto.
 * 2ª rodada (29/10): cards no padrão das outras abas; barras sem rótulo (o
 * valor fica na dica de ferramenta); notas só com datas e definições. */
(function () {
  const P = window.PADRAO_GRAFICO;
  const LARANJA = "#FF6200";
  const PRETO = "#111111";
  const INFRA = "Infra";
  const CP = "CP ex Infra";
  const COR = { [INFRA]: LARANJA, [CP]: PRETO };
  const NOME_SEG = { [CP]: "CP Tradicional", [INFRA]: "Infra" };

  const num = (v, casas) => Number(v).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  const dataBR = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : "—");

  /* R$ com unidade automática: tri, bi, mi ou mil. */
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

  const graficos = {};
  function barras(id, labels, valores, cor, opcoes) {
    const o = opcoes || {};
    if (graficos[id]) graficos[id].destroy();
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
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (itens) => (o.titulo ? o.titulo(itens[0].dataIndex) : itens[0].label),
              label: (it) => " R$ " + reais(it.raw, 2),
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { autoSkip: true, maxRotation: 0 } },
          y: {
            title: { display: true, text: o.unidade === 1e6 ? "R$ mi" : "R$ bi" },
            ticks: { callback: (v) => num(v / (o.unidade || 1e9), 0) },
          },
        },
      },
      plugins: [P.linhaZero],
    });
  }

  // ---------------------------------------------------------------------
  // Cards
  // ---------------------------------------------------------------------
  function texto(id, t) { const el = document.getElementById(id); if (el) el.textContent = t; }

  /* Infra e CP Tradicional numa linha só, embaixo do número do card. */
  function segs(id, linhas, valor) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = [INFRA, CP].map((s) => {
      const l = (linhas || []).find((x) => x.segmento === s);
      return `<span class="cl-seg"><i style="background:${COR[s]}"></i>${NOME_SEG[s]} <b>${l ? valor(l) : "—"}</b></span>`;
    }).join("");
  }

  let resumoAtual = null;
  function desenharCards(r) {
    resumoAtual = r;
    texto("dados-ate", "Dados até: " + dataBR(r.referencia));
    const total = (linhas) => (linhas || []).find((l) => l.segmento === "Total CP") || {};

    const pl = total(r.last_pl);
    texto("k-pl", "R$ " + reais(pl.pl));
    texto("k-pl-sub", pl.fundos == null ? "" : `${num(pl.fundos, 0)} fundos`);
    segs("k-pl-segs", r.last_pl, (l) => reais(l.pl));

    const c = r.mes_corrente;
    if (c) {
      const t = total(c.linhas);
      texto("k-mtd-rot", `CAPTAÇÃO ${P.mesAno(c.mes).toUpperCase()}*`);
      texto("k-mtd", "R$ " + reais(t.cap));
      texto("k-mtd-sub", pct(t.pct_pl) + " do PL");
      segs("k-mtd-segs", c.linhas, (l) => reais(l.cap));
    }

    const anos = r.anos || [];
    const ano = anos[anos.length - 1];
    if (ano) {
      const t = total(r.por_ano);
      texto("k-ano-rot", `CAPTAÇÃO ${ano}*`);
      texto("k-ano", "R$ " + reais(t[ano]));
      texto("k-ano-sub", pct(t.pct_pl_ytd, 1) + " do PL");
      segs("k-ano-segs", r.por_ano, (l) => reais(l[ano]));
    }
    texto("cl-cards-nota", `* Até ${dataBR(r.referencia)}. PL na mesma data. %PL do mês = captação do mês ÷ PL do mês; `
      + `%PL do ano = captação do ano ÷ PL de ${dataBR(r.referencia)}.`);
  }

  // ---------------------------------------------------------------------
  // Mensal
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
    const parcial = meses.some(mesParcial);
    const nota = `${P.mesAno(meses[0])} a ${P.mesAno(meses[meses.length - 1])}.`
      + (parcial ? ` * mês corrente, até ${dataBR(resumoAtual.referencia)}.` : "");
    [[INFRA, "g-mensal-infra", "nota-mensal-infra"], [CP, "g-mensal-cp", "nota-mensal-cp"]].forEach(([seg, id, idNota]) => {
      barras(id, rotulos, d.series[seg].captacao.slice(ini), COR[seg], { parcial: (i) => mesParcial(meses[i]) });
      texto(idNota, nota);
    });
  }

  // ---------------------------------------------------------------------
  // Diário
  // ---------------------------------------------------------------------
  function carregarDiario(dias) {
    buscar(`/api/captacao/diario?dias=${dias}`).then((d) => {
      const rot = d.datas.map((x) => P.diaMesCurto(x));
      const nota = d.datas.length ? `${dataBR(d.datas[0])} a ${dataBR(d.datas[d.datas.length - 1])}.` : "Sem dados diários.";
      [[INFRA, "g-diario-infra", "nota-diario-infra"], [CP, "g-diario-cp", "nota-diario-cp"]].forEach(([seg, id, idNota]) => {
        // Diário: Infra mexe em milhões, CP Tradicional em bilhões.
        barras(id, rot, d.series[seg] || [], COR[seg], { titulo: (i) => P.dataCheia(d.datas[i]), unidade: seg === INFRA ? 1e6 : 1e9 });
        texto(idNota, nota);
      });
    }).catch(() => {});
  }

  // ---------------------------------------------------------------------
  // Fundos (% da base)
  // ---------------------------------------------------------------------
  function carregarAmplitude(seg) {
    buscar(`/api/captacao/amplitude?segmento=${encodeURIComponent(seg)}`).then((d) => {
      const m = d.meses || [];
      const rot = m.map((x) => P.mesAno(x.mes) + (mesParcial(x.mes) ? "*" : ""));
      if (graficos.amp) graficos.amp.destroy();
      const conj = [
        ["Captação", "pct_pl_captando", "#7B9B72"],
        ["Sem fluxo", "pct_pl_neutro", "#C9C9C9"],
        ["Resgate", "pct_pl_resgatando", "#ED6B64"],
      ];
      graficos.amp = new Chart(document.getElementById("g-amplitude"), {
        type: "bar",
        data: {
          labels: rot,
          datasets: conj.map(([nome, campo, cor]) => ({
            label: nome, data: m.map((x) => x[campo]), backgroundColor: cor, borderWidth: 0, maxBarThickness: 46,
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
      });
      if (m.length) {
        texto("amplitude-nota", `${P.mesAno(m[0].mes)} a ${P.mesAno(m[m.length - 1].mes)}. Base = PL do segmento no mês. `
          + "Captação: gestoras com captação líquida positiva no mês; Resgate: negativa; Sem fluxo: nula. Agregado por gestora, sem identificação.");
      }
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
      desenharCards(r);
      serieMensal = m;
      desenharMensal();
      carregarDiario(15);
      carregarAmplitude(INFRA);
    })
    .catch(() => { texto("dados-ate", "Erro ao carregar"); });
})();
