/* Painel "Uso do Hub" (22/09/2026) -- ver templates/uso.html e app/uso.py. */
(function () {
  const estado = { dias: 30, pessoa: "", admin: false };
  let grafico = null;

  const $ = (id) => document.getElementById(id);
  const num = (v) => (v ?? 0).toLocaleString("pt-BR");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function query() {
    const p = new URLSearchParams({ dias: estado.dias, incluir_admin: estado.admin ? 1 : 0 });
    if (estado.pessoa) p.set("user_id", estado.pessoa);
    return p.toString();
  }

  // Ranking em tabela com barrinha: a barra é proporcional ao 1º colocado.
  // Tabela e não gráfico porque os nomes (empresas, títulos de relatório)
  // são longos -- num eixo de gráfico eles seriam cortados.
  function ranking(id, itens, rotulo, opts = {}) {
    const t = $(id);
    const max = Math.max(1, ...itens.map((i) => i[opts.campo || "vezes"]));
    const cab = `<thead><tr><th class="col-texto">${rotulo}</th><th class="col-barra"></th>` +
      `<th>${opts.titVezes || "Vezes"}</th><th>Pessoas</th>` +
      (opts.onde ? `<th class="col-texto">Onde</th>` : "") + `</tr></thead>`;
    if (!itens.length) {
      t.innerHTML = cab + `<tbody><tr><td colspan="${opts.onde ? 5 : 4}" class="muted">Nada registrado no período.</td></tr></tbody>`;
      return;
    }
    const linhas = itens.map((i) => {
      const v = i[opts.campo || "vezes"];
      return `<tr><td class="col-texto" title="${esc(i.nome)}">${esc(i.nome)}</td>` +
        `<td class="col-barra"><span class="uso-barra" style="width:${(v / max) * 100}%"></span></td>` +
        `<td><b>${num(v)}</b></td><td>${num(i.pessoas)}</td>` +
        (opts.onde ? `<td class="col-texto muted small">${esc((i.onde || []).join(", "))}</td>` : "") +
        `</tr>`;
    }).join("");
    t.innerHTML = cab + `<tbody>${linhas}</tbody>`;
  }

  function desenharDia(serie) {
    const datas = serie.map((s) => s.dia);
    if (grafico) grafico.destroy();
    const opcoes = PADRAO_GRAFICO.eixoData(datas, {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "pessoas" } } },
    });
    // Janela curta (7 a 90 dias): o dia é a informação, não o mês. Fica o
    // padrão da casa (tooltip com a data cheia), só o rótulo do eixo muda
    // para "18-set", a cada tantos dias que caibam ~10 rótulos.
    const passo = Math.max(1, Math.ceil(datas.length / 10));
    const ultimo = datas.length - 1;
    opcoes.scales.x.ticks.callback = (v, i) =>
      (ultimo - i) % passo === 0 ? PADRAO_GRAFICO.diaMes(datas[i]) : "";
    grafico = new Chart($("uso-chart-dia").getContext("2d"), {
      type: "bar",
      data: {
        labels: datas,
        datasets: [{
          label: "Pessoas", data: serie.map((s) => s.pessoas),
          backgroundColor: "#FF6200", borderRadius: 3, maxBarThickness: 18,
        }],
      },
      options: opcoes,
    });
  }

  function pessoas(lista) {
    const tb = document.querySelector("#uso-pessoas tbody");
    if (!lista.length) {
      tb.innerHTML = '<tr><td colspan="7" class="muted">Ninguém com login usou o Hub no período.</td></tr>';
      return;
    }
    tb.innerHTML = lista.map((p) => `
      <tr class="linha-clicavel${estado.pessoa === p.user_id ? " selecionada" : ""}" data-id="${esc(p.user_id)}">
        <td class="col-texto">${esc(p.email)}</td><td class="col-texto">${esc(p.nome)}</td>
        <td>${new Date(p.ultimo).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</td>
        <td>${num(p.dias_ativos)}</td><td>${num(p.visitas)}</td><td>${esc(p.aba_preferida || "—")}</td>
        <td class="col-texto">${esc(p.interesses.join(", ") || "—")}</td>
      </tr>`).join("");
    tb.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", () => {
        estado.pessoa = estado.pessoa === tr.dataset.id ? "" : tr.dataset.id;
        $("uso-pessoa").value = estado.pessoa;
        carregar();
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    });
  }

  function preencherPessoas(usuarios) {
    const sel = $("uso-pessoa");
    if (sel.options.length > 1) return;
    usuarios.forEach((u) => sel.add(new Option(u.email, u.id)));
    sel.value = estado.pessoa;
  }

  async function carregar() {
    const r = await fetch(`/api/admin/uso?${query()}`, { credentials: "same-origin" });
    if (!r.ok) return;
    const d = await r.json();
    preencherPessoas(d.usuarios);
    $("uso-csv").href = `/api/admin/uso.csv?${query()}`;
    $("uso-k-pessoas").textContent = num(d.kpis.pessoas);
    $("uso-k-anonimos").textContent = num(d.kpis.anonimos);
    $("uso-k-visitas").textContent = num(d.kpis.visitas);
    $("uso-k-interacoes").textContent = num(d.kpis.eventos - d.kpis.visitas);
    const quem = estado.pessoa ? `Só ${$("uso-pessoa").selectedOptions[0]?.text || "a pessoa escolhida"}.`
      : (estado.admin ? "Inclui administradores." : "Administradores fora da conta.");
    $("uso-nota").textContent = `Últimos ${d.dias} dias. ${quem}`;
    desenharDia(d.serie);
    ranking("uso-abas", d.abas, "Aba", { campo: "visitas", titVezes: "Visitas" });
    ranking("uso-setores", d.setores, "Setor", { onde: true });
    ranking("uso-empresas", d.empresas, "Empresa / emissor", { onde: true });
    ranking("uso-relatorios", d.relatorios, "Relatório");
    ranking("uso-buscas", d.buscas, "Busca");
    ranking("uso-outros", d.outros, "Filtro / função", { onde: true });
    pessoas(d.por_pessoa);
  }

  document.querySelectorAll("#uso-periodo .win-btn").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#uso-periodo .win-btn").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      estado.dias = Number(b.dataset.dias);
      carregar();
    });
  });
  $("uso-pessoa").addEventListener("change", (e) => { estado.pessoa = e.target.value; carregar(); });
  $("uso-admin").addEventListener("change", (e) => { estado.admin = e.target.checked; carregar(); });
  carregar();
})();
