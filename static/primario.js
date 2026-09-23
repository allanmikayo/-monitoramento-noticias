/* Aba "Mercado Primário" (23/09/2026) -- ver templates/primario.html,
 * app/primario/queries.py e o padrão de gráficos em chart-padrao.js.
 *
 * Uma chamada só (/api/primario/dados) traz todos os blocos: a base tem
 * poucos milhares de linhas e todo bloco reusa o mesmo recorte, então pedir
 * bloco por bloco seria pagar várias idas ao banco pela mesma tabela.
 */
(function () {
  const CORES = { DEB: "#FF6200", CRI: "#111111", CRA: "#9a9a9a" };
  const ROTULO = { DEB: "Debêntures", CRI: "CRI", CRA: "CRA" };
  const estado = { janela: "12m", instrumentos: ["DEB", "CRI", "CRA"], incentivada: "" };
  const graficos = {};

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const bi = (v) => `R$ ${(v ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} bi`;
  // Valor zero aqui quase sempre é campo não preenchido pela CVM, não uma
  // oferta de R$ 0 -- mostrar "0" seria afirmar algo que a fonte não diz.
  const mn = (v) => (v ? v.toLocaleString("pt-BR", { maximumFractionDigits: 0 }) : "—");
  const dm = (iso) => (iso ? iso.slice(8, 10) + "/" + iso.slice(5, 7) : "—");
  // Com janela de 12 ou 24 meses, dia/mês sem ano vira armadilha.
  const dma = (iso) => (iso ? dm(iso) + "/" + iso.slice(2, 4) : "—");

  function query() {
    const p = new URLSearchParams({ janela: estado.janela });
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
        datasets: estado.instrumentos.map((i) => ({
          label: ROTULO[i], data: serie.map((s) => s[i]),
          backgroundColor: CORES[i], borderRadius: 2, maxBarThickness: 26,
        })),
      },
      options: PADRAO_GRAFICO.eixoData(datas, {
        responsive: true,
        plugins: { legend: { display: estado.instrumentos.length > 1, position: "bottom" } },
        scales: {
          x: { stacked: true },
          y: { stacked: true, beginAtZero: true, title: { display: true, text: "R$ bi" } },
        },
      }),
    });
  }

  function grafIncentivadas(tri) {
    destruir("inc");
    // Trimestre não é data: o rótulo é o próprio "2026-T1", sem o eixo de
    // datas do padrão (que formata mês a mês).
    graficos.inc = new Chart($("chart-primario-inc").getContext("2d"), {
      type: "bar",
      data: {
        labels: tri.map((t) => t.trimestre.replace("-", " ")),
        datasets: [
          { label: "Incentivada", data: tri.map((t) => t.incentivada), backgroundColor: "#FF6200", borderRadius: 2 },
          { label: "Não incentivada", data: tri.map((t) => t.nao), backgroundColor: "#111111", borderRadius: 2 },
          { label: "Não informado", data: tri.map((t) => t.sem_info), backgroundColor: "#d0d0d0", borderRadius: 2 },
        ],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: true, position: "bottom" },
          tooltip: {
            callbacks: {
              afterBody: (itens) => {
                const t = tri[itens[0].dataIndex];
                return `Incentivadas: ${t.share_incentivada.toLocaleString("pt-BR")}% do volume do trimestre`;
              },
            },
          },
        },
        scales: {
          x: { stacked: true, grid: { display: false } },
          y: { stacked: true, beginAtZero: true, title: { display: true, text: "R$ bi" } },
        },
      },
    });
  }

  function lideres(lista) {
    const t = $("tabela-lideres");
    const max = Math.max(1, ...lista.map((l) => l.volume));
    const cab = '<thead><tr><th class="col-texto">Coordenador</th><th class="col-barra"></th>' +
      "<th>R$ bi</th><th>Ofertas</th><th>Share</th></tr></thead>";
    if (!lista.length) { t.innerHTML = cab + '<tbody><tr><td colspan="5" class="muted">Sem ofertas na janela.</td></tr></tbody>'; return; }
    t.innerHTML = cab + "<tbody>" + lista.map((l) => `
      <tr><td class="col-texto">${esc(l.lider)}</td>
        <td class="col-barra"><span class="uso-barra" style="width:${(l.volume / max) * 100}%"></span></td>
        <td><b>${l.volume.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</b></td>
        <td>${l.ofertas}</td><td>${l.share.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%</td></tr>`).join("") + "</tbody>";
  }

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
    $("primario-csv").href = `/api/primario/ofertas.csv?${query()}`;
    $("primario-ate").textContent = `Dados até: ${dm(d.disponivel_ate)}`;
    $("pk-volume").textContent = bi(d.kpis.volume);
    $("pk-ofertas").textContent = d.kpis.ofertas.toLocaleString("pt-BR");
    $("pk-ticket").textContent = mn(d.kpis.ticket_mediano);
    $("pk-firme").textContent = `${d.kpis.garantia_firme_pct.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;

    grafMensal(d.serie);
    grafIncentivadas(d.incentivadas);
    lideres(d.lideres);
    escreverFonte("fonte-primario-mes", d.disponivel_ate);
    escreverFonte("fonte-primario-inc", d.disponivel_ate);
    escreverFonte("fonte-pipeline", d.disponivel_ate);

    // O subtítulo carrega o tamanho da fila: a tabela rola, então o total
    // e o tanto de oferta parada precisam aparecer sem rolar.
    const paradas = d.pipeline.filter((o) => o.parada).length;
    const somaFila = d.pipeline.reduce((t, o) => t + (o.valor || 0), 0) / 1000;
    $("sub-pipeline").textContent =
      `${d.pipeline.length} ofertas, ${bi(somaFila)} · ${paradas} abertas há mais de 90 dias, `
      + "provável registro que não virou emissão";

    corpo("tabela-pipeline", d.pipeline, (o) => `
      <tr title="${esc(o.status)} · público ${esc(o.publico)}">
        <td>${dma(o.data_registro)}</td>
        <td class="${o.parada ? "cell-abertura" : ""}">${o.dias ?? "—"}</td>
        <td class="col-texto">${esc(o.emissor)}</td><td>${o.instrumento}</td>
        <td>${mn(o.valor)}</td><td class="col-texto">${esc(o.lider)}</td></tr>`,
      "Nenhuma oferta aberta com estes filtros.");

    corpo("tabela-novidades", d.novidades, (n) => `
      <tr><td>${dma(n.visto_em.slice(0, 10))}</td><td class="col-texto">${esc(n.emissor)}</td>
        <td>${n.instrumento}</td><td>${mn(n.valor)}</td>
        <td class="col-texto ${n.nova ? "cell-abertura" : ""}">${n.nova ? "entrou na base" : esc(n.de) + " → " + esc(n.para)}</td></tr>`,
      "Nada novo nos últimos 7 dias.");

    corpo("tabela-maiores", d.maiores, (o) => `
      <tr><td>${dma(o.data)}</td><td class="col-texto">${esc(o.emissor)}</td><td>${o.instrumento}</td>
        <td><b>${mn(o.valor)}</b></td><td>${o.incentivada === "S" ? "sim" : o.incentivada === "N" ? "não" : "—"}</td>
        <td class="col-texto">${esc(o.lider)}</td></tr>`,
      "Sem ofertas na janela.");

    corpo("tabela-estreantes", d.estreantes, (o) => `
      <tr><td>${dma(o.data)}</td><td class="col-texto">${esc(o.emissor)}</td><td>${o.instrumento}</td>
        <td>${mn(o.valor)}</td><td class="col-texto">${esc(o.lider)}</td></tr>`,
      "Nenhum emissor estreante na janela.");
  }

  document.querySelectorAll("#primario-janela .win-btn").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#primario-janela .win-btn").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      estado.janela = b.dataset.janela;
      if (window.registrarUso) registrarUso("filtro", "Janela: " + b.textContent.trim());
      carregar();
    });
  });

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
