/* Aba "Mercado Primário" (23/09/2026; reorganizada para crédito em
 * 26/10/2026) -- ver templates/primario.html,
 * app/primario/queries.py e o padrão de gráficos em chart-padrao.js.
 *
 * Uma chamada só (/api/primario/dados) traz todos os blocos: a base tem
 * poucos milhares de linhas e todo bloco reusa o mesmo recorte, então pedir
 * bloco por bloco seria pagar várias idas ao banco pela mesma tabela.
 */
(function () {
  const CORES = { DEB: "#FF6200", CRI: "#111111", CRA: "#9a9a9a" };
  const ROTULO = { DEB: "Debêntures", CRI: "CRI", CRA: "CRA" };
  // Ofertas por subscritor: LARANJA é o que ficou com instituição financeira,
  // preto e cinzas são o papel efetivamente distribuído. A cor carrega a
  // leitura -- encarteirou ou colocou.
  const GRUPOS = [
    ["qtd_bancos_consorcio", "Bancos do consórcio", "#FF6200"],
    ["qtd_outras_if", "Outras inst. financeiras", "#ffa366"],
    ["qtd_fundos", "Fundos", "#111111"],
    ["qtd_pessoa_natural", "Pessoa física", "#6b6b6b"],
    ["qtd_institucionais", "Previdência e seguradoras", "#9a9a9a"],
    ["qtd_estrangeiro", "Estrangeiro", "#c9c9c9"],
    ["qtd_outros", "Outros", "#e4e4e4"],
  ];
  const estado = { janela: "12m", instrumentos: ["DEB", "CRI", "CRA"], incentivada: "", base: "registro" };
  const graficos = {};

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const n1 = (v) => (v ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const bi = (v) => `R$ ${n1(v)} bi`;
  // Valor zero aqui quase sempre é campo não preenchido pela CVM, não uma
  // oferta de R$ 0 -- mostrar "0" seria afirmar algo que a fonte não diz.
  const mn = (v) => (v ? v.toLocaleString("pt-BR", { maximumFractionDigits: 0 }) : "—");
  const dm = (iso) => (iso ? iso.slice(8, 10) + "/" + iso.slice(5, 7) : "—");
  // Com janela de 12 ou 24 meses, dia/mês sem ano vira armadilha.
  const dma = (iso) => (iso ? dm(iso) + "/" + iso.slice(2, 4) : "—");

  function query() {
    const p = new URLSearchParams({ janela: estado.janela, base: estado.base });
    estado.instrumentos.forEach((i) => p.append("instrumento", i));
    if (estado.incentivada) p.set("incentivada", estado.incentivada);
    return p.toString();
  }

  // A fonte fica ABAIXO de cada visual, com a data do dado -- padrão da casa.
  function escreverFonte(id, ate) {
    const el = $(id);
    if (el) el.textContent = `Fonte: CVM (Dados Abertos) e Itaú BBA (dado disponível até ${dm(ate)})`;
  }

  function destruir(nome) {
    if (graficos[nome]) { graficos[nome].destroy(); delete graficos[nome]; }
  }

  function grafMensal(serie) {
    destruir("mes");
    const datas = serie.map((s) => s.mes);
    graficos.mes = new Chart($("chart-primario-mes").getContext("2d"), {
      type: "bar",
      data: {
        labels: datas,
        datasets: [
          ...estado.instrumentos.map((i) => ({
            label: ROTULO[i], data: serie.map((s) => s[i]),
            backgroundColor: CORES[i], borderRadius: 2, maxBarThickness: 26, order: 2,
          })),
          // Média mensal da janela: diz de relance se o mês está acima ou
          // abaixo do ritmo do período.
          {
            type: "line", label: "Média mensal", order: 1, stack: "media",
            data: serie.map(() => serie.reduce((a, s) => a + s.total, 0) / (serie.length || 1)),
            borderColor: "#6b6b6b", borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, fill: false,
          },
        ],
      },
      options: PADRAO_GRAFICO.eixoData(datas, {
        responsive: true,
        plugins: { legend: { display: true, position: "bottom" } },
        scales: {
          x: { stacked: true },
          y: { stacked: true, beginAtZero: true, title: { display: true, text: "R$ bi" } },
        },
      }),
    });
  }

  function grafDistribuicao(tri) {
    destruir("dist");
    graficos.dist = new Chart($("chart-primario-dist").getContext("2d"), {
      type: "bar",
      data: {
        labels: tri.map((t) => t.trimestre.replace("-", " ")),
        datasets: GRUPOS.map(([campo, rotulo, cor]) => ({
          label: rotulo, data: tri.map((t) => t[campo]),
          backgroundColor: cor, borderRadius: 2,
        })),
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: true, position: "bottom" },
          tooltip: {
            callbacks: {
              label: (i) => `${i.dataset.label}: ${n1(i.parsed.y)}%`,
              afterBody: (itens) => {
                const t = tri[itens[0].dataIndex];
                return `Ficou com banco: ${n1(t.encarteirado)}% · base de ${bi(t.volume)} encerrados`;
              },
            },
          },
        },
        scales: {
          x: { stacked: true, grid: { display: false } },
          y: { stacked: true, beginAtZero: true, max: 100, title: { display: true, text: "% do volume" } },
        },
      },
    });
  }

  // Setor: barra cheia é a janela, contorno é o mesmo período um ano antes.
  // "Não classificado" fica por último e em cinza -- é o tamanho do que a
  // taxonomia não cobre, não um setor.
  function grafSetores(lista, temAnterior) {
    destruir("setor");
    const top = lista.filter((s) => s.setor !== "Não classificado").slice(0, 12);
    const nc = lista.find((s) => s.setor === "Não classificado");
    if (nc) top.push(nc);
    $("setores-wrap").style.height = `${Math.max(160, top.length * 26 + 40)}px`;
    const datasets = [{
      label: "Janela", data: top.map((s) => s.volume),
      backgroundColor: top.map((s) => (s.setor === "Não classificado" ? "#c9c9c9" : "#FF6200")),
      borderRadius: 2, barPercentage: 0.9, categoryPercentage: 0.8,
    }];
    if (temAnterior) datasets.push({
      label: "Mesmo período, ano anterior", data: top.map((s) => s.volume_anterior),
      backgroundColor: "transparent", borderColor: "#111111", borderWidth: 1, borderRadius: 2,
      barPercentage: 0.9, categoryPercentage: 0.8,
    });
    graficos.setor = new Chart($("chart-primario-setor").getContext("2d"), {
      type: "bar",
      data: { labels: top.map((s) => s.setor), datasets },
      options: {
        indexAxis: "y", responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: temAnterior, position: "bottom" },
          tooltip: { callbacks: {
            label: (i) => `${i.dataset.label}: ${bi(i.parsed.x)}`,
            afterBody: (it) => `${n1(top[it[0].dataIndex].share)}% do volume da janela`,
          } },
        },
        scales: {
          x: { beginAtZero: true, title: { display: true, text: "R$ bi" } },
          y: { grid: { display: false }, ticks: { autoSkip: false } },
        },
      },
    });
  }

  const bps = (v) => (v == null ? "—" : Math.round(v).toLocaleString("pt-BR"));
  const pct = (v) => (v == null ? "—" : `${Math.round(v)}%`);
  const prazo = (v) => (v == null ? "—" : `${n1(v)}a`);
  const variacao = (v) => {
    if (v == null) return "";
    const p = v * 100;
    return `${p >= 0 ? "+" : ""}${n1(p)}%`;
  };
  const etiqueta = (t) => `<span class="kpi-tag" style="margin-left:6px;font-size:10px;">${t}</span>`;
  // Em CRI e CRA o emissor é a securitizadora; quem interessa é o devedor.
  // Quando a CVM identifica o devedor, ele vira a linha de cima e o veículo
  // fica embaixo, pequeno.
  const quemDeve = (o) => (o.devedor && o.devedor !== o.emissor)
    ? `${esc(o.devedor)}<div class="muted small">via ${esc(o.emissor)}</div>`
    : esc(o.emissor);

  function corpo(id, linhas, montar, vazio) {
    const tb = document.querySelector(`#${id} tbody`);
    const colunas = document.querySelectorAll(`#${id} thead th`).length;
    tb.innerHTML = linhas.length ? linhas.map(montar).join("")
      : `<tr><td colspan="${colunas}" class="muted">${vazio}</td></tr>`;
  }

  async function carregar() {
    const r = await fetch(`/api/primario/dados?${query()}`, { credentials: "same-origin" });
    if (!r.ok) return;
    const d = await r.json();
    const porRegistro = d.base === "registro";
    const k = d.kpis;
    const temAnterior = k.volume_anterior != null;
    $("primario-csv").href = `/api/primario/ofertas.csv?${query()}`;
    $("primario-ate").textContent = `Dados até: ${dm(d.disponivel_ate)}`;

    $("pk-volume").textContent = bi(k.volume);
    const vs = temAnterior
      ? `${variacao(k.variacao)} vs ${bi(k.volume_anterior)} em ${dma(d.inicio_anterior)}–${dma(d.fim_anterior)}`
      : (porRegistro ? "registrado na janela" : "encerrado na janela");
    $("pk-volume-sub").textContent = vs;
    $("pk-volume-sub").className = "kpi-delta " + (k.variacao == null ? "muted" : k.variacao >= 0 ? "cell-fechamento" : "cell-abertura");
    $("pk-emissores").textContent = k.emissores.toLocaleString("pt-BR");
    $("pk-emissores-sub").textContent = k.estreantes == null ? "desde jan/2023"
      : `${k.estreantes} estreantes (1ª oferta desde jan/23)`;
    $("pk-ofertas").textContent = k.ofertas.toLocaleString("pt-BR");
    $("pk-ticket-sub").textContent = `ticket mediano R$ ${mn(k.ticket_mediano)} mn`;
    $("pk-encarteirado").textContent = `${n1(k.encarteirado_pct)}%`;
    $("pk-fundos-pf").textContent = `${n1(k.fundos_pf_pct)}%`;
    $("pk-pf-sub").textContent = `pessoa física sozinha ${n1(k.pessoa_fisica_pct)}%`;
    $("primario-nota").textContent =
      `Volume é o valor registrado na oferta primária (secundária fica fora), não o efetivamente distribuído. `
      + `Data de ${porRegistro ? "registro na CVM" : "encerramento da oferta"}. `
      + `Quem ficou com o papel só existe em oferta encerrada e cobre ${bi(k.volume_com_quebra)} da janela. `
      + `Fonte: CVM (Dados Abertos) e Itaú BBA`;

    $("titulo-serie").textContent = porRegistro ? "Volume registrado por mês" : "Volume encerrado por mês";
    $("sub-serie").textContent = `R$ bilhões, pela data de ${porRegistro ? "registro na CVM" : "encerramento da oferta"} · linha tracejada = média mensal da janela`;

    grafMensal(d.serie);
    grafSetores(d.setores, temAnterior);
    grafDistribuicao(d.distribuicao);
    const nc = d.setores.find((s) => s.setor === "Não classificado");
    $("sub-setores").textContent = "R$ bilhões · setor de quem deve (em CRI/CRA, o devedor)"
      + (temAnterior ? " · contorno = mesmo período do ano anterior" : "")
      + (nc ? ` · ${n1(nc.share)}% do volume sem setor na taxonomia` : "");
    ["fonte-primario-mes", "fonte-primario-setor", "fonte-primario-dist", "fonte-emissores",
      "fonte-balanco", "fonte-pipeline"].forEach((id) => escreverFonte(id, d.disponivel_ate));
    $("fonte-emissoes").textContent =
      `Fonte: CVM (Dados Abertos), debentures.com.br, ANBIMA e Itaú BBA (dado disponível até ${dm(d.disponivel_ate)})`;

    corpo("tabela-emissores", d.emissores, (e) => `
      <tr><td class="col-texto">${esc(e.nome)}${e.estreante ? etiqueta("estreante") : ""}</td>
        <td class="col-texto muted small">${esc(e.grupo || "—")}</td>
        <td class="col-texto">${esc(e.setor)}</td>
        <td class="muted small">${esc(e.instrumentos.join(", "))}</td>
        <td class="${e.ofertas > 2 ? "cell-abertura" : ""}">${e.ofertas}</td>
        <td><b>${mn(e.volume)}</b></td>
        <td class="${(e.pct_banco ?? 0) >= 50 ? "cell-abertura" : ""}">${pct(e.pct_banco)}</td>
        <td>${dma(e.ultima)}</td></tr>`,
      "Sem emissores na janela.");

    // Emissões: uma linha por SÉRIE; a primeira série da oferta carrega
    // data, emissor e setor, as demais ficam recuadas.
    const linhasEm = [];
    d.emissoes.forEach((o) => {
      const series = o.series.length ? o.series : [null];
      series.forEach((s, i) => {
        const prim = i === 0;
        const delta = s && s.spread_emissao != null && s.spread_atual != null ? s.spread_atual - s.spread_emissao : null;
        const dica = s ? `${esc(s.indexador || "")} · emitida ${dma(s.data_emissao)}`
          + (s.data_spread_atual ? ` · ANBIMA de ${dma(s.data_spread_atual)}` : "")
          + (o.confianca && o.confianca !== "alta" ? ` · casamento de confiança ${o.confianca}` : "")
          : "a CVM não traz o ticker e esta oferta não casou com nenhuma série do cadastro";
        linhasEm.push(`<tr title="${dica}" ${prim ? 'class="linha-oferta"' : ""}>
          <td>${prim ? dma(o.data) : ""}</td>
          <td class="col-texto">${prim ? esc(o.devedor) : ""}</td>
          <td class="col-texto muted small">${prim ? esc(o.setor) : ""}</td>
          <td>${s ? esc(s.codigo) : '<span class="muted">sem ticker</span>'}${o.confianca && o.confianca !== "alta" && s ? ' <span class="muted">?</span>' : ""}</td>
          <td class="col-texto">${s ? esc(s.remuneracao || "—") : ""}</td>
          <td>${s ? prazo(s.prazo_anos) : ""}</td>
          <td>${s ? mn(s.volume ? s.volume / 1e6 : null) : mn(o.valor)}</td>
          <td><b>${s ? bps(s.spread_emissao) : ""}</b></td>
          <td>${s ? bps(s.spread_atual) : ""}</td>
          <td class="${delta == null ? "" : delta > 0 ? "cell-abertura" : "cell-fechamento"}">${delta == null ? "" : (delta > 0 ? "+" : "") + bps(delta)}</td>
          <td>${prim ? pct(o.pct_banco) : ""}</td></tr>`);
      });
    });
    document.querySelector("#tabela-emissoes tbody").innerHTML = linhasEm.length ? linhasEm.join("")
      : '<tr><td colspan="11" class="muted">Sem debêntures na janela (ou o filtro de instrumento tirou DEB).</td></tr>';

    corpo("tabela-balanco", d.balanco, (o) => `
      <tr title="${esc(o.setor)}"><td>${dma(o.data)}</td><td class="col-texto">${quemDeve(o)}</td>
        <td>${o.instrumento}</td><td><b>${mn(o.valor)}</b></td>
        <td class="cell-abertura">${pct(o.pct_banco)}</td><td class="col-texto">${esc(o.lider)}</td></tr>`,
      "Nenhuma oferta encerrada com banco majoritário na janela.");

    const rp = d.resumo_pipeline;
    const porInstr = rp.por_instrumento.map((x) => `${ROTULO[x.instrumento]} ${bi(x.volume)}`).join(" · ");
    $("sub-pipeline").textContent =
      `${rp.vivas} ofertas de até 90 dias somando ${bi(rp.volume_vivas)}`
      + (porInstr ? ` (${porInstr})` : "")
      + (rp.paradas ? ` · ${rp.paradas} paradas há mais de 90 dias ficam ocultas` : "");
    corpo("tabela-pipeline", d.pipeline.filter((o) => !o.parada), (o) => `
      <tr title="${esc(o.status)} · líder ${esc(o.lider)} · público ${esc(o.publico)}">
        <td>${dma(o.data_registro)}</td><td>${o.dias ?? "—"}</td>
        <td class="col-texto">${quemDeve(o)}</td><td class="col-texto muted small">${esc(o.setor)}</td>
        <td>${o.instrumento}</td><td>${mn(o.valor)}</td></tr>`,
      "Nenhuma oferta na fila com estes filtros.");
  }

  function abas(id, campo, rotuloUso) {
    document.querySelectorAll(`#${id} .win-btn`).forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll(`#${id} .win-btn`).forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        estado[campo] = b.dataset[campo];
        if (window.registrarUso) registrarUso("filtro", `${rotuloUso}: ${b.textContent.trim()}`);
        carregar();
      });
    });
  }
  abas("primario-janela", "janela", "Janela");
  abas("primario-base", "base", "Data");

  // Instrumento é multi-seleção: desmarcar todos não faz sentido (a tela
  // ficaria vazia sem motivo), então o último marcado não sai.
  document.querySelectorAll("#primario-instrumentos .win-btn").forEach((b) => {
    b.addEventListener("click", () => {
      const i = b.dataset.instrumento;
      const marcado = estado.instrumentos.includes(i);
      if (marcado && estado.instrumentos.length === 1) return;
      estado.instrumentos = marcado ? estado.instrumentos.filter((x) => x !== i)
        : [...estado.instrumentos, i].sort((a, c) => ["DEB", "CRI", "CRA"].indexOf(a) - ["DEB", "CRI", "CRA"].indexOf(c));
      b.classList.toggle("active", !marcado);
      if (!marcado && window.registrarUso) registrarUso("filtro", "Instrumento: " + b.textContent.trim());
      carregar();
    });
  });

  $("primario-incentivada").addEventListener("change", (e) => {
    estado.incentivada = e.target.value;
    if (window.registrarUso && e.target.value) registrarUso("filtro", "Incentivadas: " + e.target.selectedOptions[0].text);
    carregar();
  });

  carregar();
})();
