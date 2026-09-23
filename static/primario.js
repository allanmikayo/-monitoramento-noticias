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
                return `Incentivadas: ${n1(t.share_incentivada)}% do volume do trimestre`;
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
        <td><b>${n1(l.volume)}</b></td><td>${l.ofertas}</td><td>${n1(l.share)}%</td></tr>`).join("") + "</tbody>";
  }

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
    $("primario-csv").href = `/api/primario/ofertas.csv?${query()}`;
    $("primario-ate").textContent = `Dados até: ${dm(d.disponivel_ate)}`;

    $("pk-volume").textContent = bi(d.kpis.volume);
    $("pk-volume-sub").textContent = porRegistro ? "registrado na janela" : "encerrado na janela";
    $("pk-ofertas").textContent = d.kpis.ofertas.toLocaleString("pt-BR");
    $("pk-ticket-sub").textContent = `ticket mediano R$ ${mn(d.kpis.ticket_mediano)} mn`;
    $("pk-encarteirado").textContent = `${n1(d.kpis.encarteirado_pct)}%`;
    $("pk-pf").textContent = `${n1(d.kpis.pessoa_fisica_pct)}%`;
    $("pk-firme").textContent = `${n1(d.kpis.garantia_firme_pct)}%`;
    $("pk-book-sub").textContent = `com bookbuilding ${n1(d.kpis.bookbuilding_pct)}%`;
    $("primario-nota").textContent =
      `Volume é o valor registrado na oferta, não o efetivamente distribuído. `
      + `Data de ${porRegistro ? "registro na CVM" : "encerramento da oferta"}. `
      + `Quem ficou com o papel só existe em oferta encerrada e cobre ${bi(d.kpis.volume_com_quebra)} da janela. `
      + `Fonte: CVM (Dados Abertos) e Itaú BBA`;

    $("titulo-serie").textContent = porRegistro ? "Volume registrado por mês" : "Volume encerrado por mês";
    $("sub-serie").textContent = `R$ bilhões, pela data de ${porRegistro ? "registro na CVM" : "encerramento da oferta"}`;

    grafMensal(d.serie);
    grafDistribuicao(d.distribuicao);
    grafIncentivadas(d.incentivadas);
    lideres(d.lideres);
    escreverFonte("fonte-primario-mes", d.disponivel_ate);
    escreverFonte("fonte-primario-dist", d.disponivel_ate);
    escreverFonte("fonte-primario-inc", d.disponivel_ate);
    escreverFonte("fonte-pipeline", d.disponivel_ate);

    // O subtítulo carrega o tamanho da fila: a tabela rola, então o total
    // precisa aparecer sem rolar. Separar o que está parado importa: oferta
    // com registro velho quase sempre não vira emissão.
    const rp = d.resumo_pipeline;
    const porInstr = rp.por_instrumento.map((x) => `${ROTULO[x.instrumento]} ${bi(x.volume)}`).join(" · ");
    $("sub-pipeline").textContent =
      `${rp.vivas} ofertas de até 90 dias somando ${bi(rp.volume_vivas)}`
      + (porInstr ? ` (${porInstr})` : "")
      + (rp.paradas ? ` · mais ${rp.paradas} paradas há mais de 90 dias, provável registro que não virou emissão` : "");

    corpo("tabela-pipeline", d.pipeline, (o) => `
      <tr title="${esc(o.status)} · público ${esc(o.publico)}">
        <td>${dma(o.data_registro)}</td>
        <td class="${o.parada ? "cell-abertura" : ""}">${o.dias ?? "—"}</td>
        <td class="col-texto">${quemDeve(o)}</td><td>${o.instrumento}</td>
        <td>${mn(o.valor)}</td><td class="col-texto">${esc(o.lider)}</td></tr>`,
      "Nenhuma oferta aberta com estes filtros.");

    corpo("tabela-movimentos", d.movimentos, (n) => `
      <tr><td>${dma(n.visto_em.slice(0, 10))}</td><td class="col-texto">${quemDeve(n)}</td>
        <td>${n.instrumento}</td><td>${mn(n.valor)}</td>
        <td class="col-texto ${n.nova ? "cell-abertura" : ""}">${n.nova ? "entrou na base" : esc(n.de) + " → " + esc(n.para)}</td></tr>`,
      d.trilha_vazia
        ? "O histórico de mudanças começa na próxima coleta — a carga inicial não conta como novidade."
        : "Nada novo nos últimos 30 dias.");

    corpo("tabela-maiores", d.maiores, (o) => `
      <tr title="${esc(o.lider)} · ${esc(o.status)}">
        <td>${dma(porRegistro ? o.data : (o.encerramento || o.data))}</td>
        <td class="col-texto">${quemDeve(o)}</td><td>${o.instrumento}</td>
        <td><b>${mn(o.valor)}</b></td>
        <td>${o.incentivada === "S" ? "sim" : o.incentivada === "N" ? "não" : "—"}</td>
        <td>${mn(o.investidores)}</td></tr>`,
      "Sem ofertas na janela.");

    corpo("tabela-captadores", d.captadores, (e) => `
      <tr><td class="col-texto">${esc(e.nome)}</td>
        <td class="${e.ofertas > 2 ? "cell-abertura" : ""}">${e.ofertas}</td>
        <td><b>${mn(e.volume)}</b></td>
        <td class="col-texto muted small">${esc(e.instrumentos.join(", "))}</td>
        <td>${dma(e.ultima)}</td></tr>`,
      "Sem emissores na janela.");
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
