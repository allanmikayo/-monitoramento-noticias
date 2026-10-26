/* Página inicial do Hub (26/10/2026). Ver templates/inicio.html e app/inicio.py.
 * Cada bloco busca a própria rota e se desenha sozinho: um bloco lento ou sem
 * dado não segura os outros. */
(function () {
  const LARANJA = "#FF6200";
  const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  const MESES_LONGOS = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto",
    "Setembro", "Outubro", "Novembro", "Dezembro"];

  const fmt = (v, c) => (v == null || isNaN(v) ? "—"
    : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: c || 0, maximumFractionDigits: c || 0 }));
  const sinal = (v, c) => (v > 0 ? "+" : v < 0 ? "−" : "") + fmt(Math.abs(v), c);
  const dm = (iso) => (iso ? iso.slice(8, 10) + "/" + iso.slice(5, 7) : "—");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const bi = (v) => "R$ " + fmt(v / 1e9, v >= 10e9 ? 1 : 2) + " bi";
  const datas = {};

  function buscar(url) {
    return fetch(url, { credentials: "same-origin" }).then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  const campo = (raiz, nome) => raiz.querySelector(`[data-campo="${nome}"]`);
  function chip(el, v, texto) {
    el.textContent = texto;
    el.className = "ic-chip " + (v > 0 ? "up" : v < 0 ? "down" : "flat");
  }
  function erro(raiz) {
    raiz.classList.add("ic-erro");
    const v = raiz.querySelector(".ic-valor b");
    if (v) v.textContent = "—";
  }
  function atualizarDatas(chave, texto) {
    datas[chave] = texto;
    const ordem = ["spreads", "secundario", "primario", "curva"];
    document.getElementById("inicio-datas").textContent =
      ordem.filter((k) => datas[k]).map((k) => datas[k]).join(" · ") || "Carregando…";
  }

  // ---- Spreads e quem mexeu -------------------------------------------
  let spreadsDados = null;
  function sparkline(canvas, serie, cor) {
    const pts = serie.filter((p) => p.spread != null);
    new Chart(canvas, {
      type: "line",
      data: { labels: pts.map((p) => p.data), datasets: [{ data: pts.map((p) => p.spread), borderColor: cor, borderWidth: 1.8, pointRadius: 0, tension: 0.25, fill: false }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false }, tooltip: { enabled: true, callbacks: {
          title: (it) => dm(pts[it[0].dataIndex].data), label: (it) => fmt(it.raw, 1) + " bps" } } },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
  }

  function desenharMovers(i) {
    const c = spreadsDados && spreadsDados.classes[i];
    const linha = (m, cls) => `<a class="ic-mov" href="/spreads" title="${esc(m.nome || m.codigo)}">
        <b>${esc(m.codigo)}</b><span class="ic-mov-nome">${esc(m.nome || "")}</span>
        <span class="muted">${fmt(m.spread, 0)} bps</span><span class="ic-mov-d ${cls}">${sinal(m.variacao_bps, 1)}</span></a>`;
    const vazio = '<span class="muted small">Sem variação suficiente.</span>';
    document.getElementById("lista-aberturas").innerHTML = c && c.aberturas.length
      ? c.aberturas.map((m) => linha(m, "abre")).join("") : vazio;
    document.getElementById("lista-fechamentos").innerHTML = c && c.fechamentos.length
      ? c.fechamentos.map((m) => linha(m, "fecha")).join("") : vazio;
  }

  buscar("/api/inicio/spreads").then((d) => {
    spreadsDados = d;
    d.classes.forEach((c, i) => {
      const raiz = document.getElementById("card-spread-" + i);
      campo(raiz, "valor").textContent = fmt(c.spread, 1);
      if (c.variacao_bps != null) chip(campo(raiz, "delta"), c.variacao_bps, (c.variacao_bps > 0 ? "▲ " : c.variacao_bps < 0 ? "▼ " : "") + sinal(c.variacao_bps, 1) + " bps WoW");
      campo(raiz, "extra").textContent = c.n_ativos ? fmt(c.n_ativos) + " papéis" : "";
      raiz.title = c.data_comparacao ? `${dm(c.data_referencia)} contra ${dm(c.data_comparacao)}` : "";
      sparkline(raiz.querySelector("canvas"), c.serie, i === 0 ? LARANJA : "#111111");
    });
    const ref = d.classes[0] && d.classes[0].data_referencia;
    if (ref) atualizarDatas("spreads", "Spreads ANBIMA de " + dm(ref));
    desenharMovers(0);
  }).catch(() => { [0, 1].forEach((i) => erro(document.getElementById("card-spread-" + i))); });

  document.getElementById("movers-tabs").addEventListener("click", (ev) => {
    const b = ev.target.closest(".win-btn");
    if (!b) return;
    ev.currentTarget.querySelectorAll(".win-btn").forEach((x) => x.classList.toggle("active", x === b));
    desenharMovers(Number(b.dataset.i));
  });

  // ---- Secundário -------------------------------------------------------
  buscar("/api/inicio/secundario").then((d) => {
    const raiz = document.getElementById("card-secundario");
    if (!d.referencia) {
      campo(raiz, "extra").textContent = "Nenhum export importado ainda.";
      return;
    }
    campo(raiz, "valor").textContent = "R$ " + fmt(d.media_5d / 1e9, 2) + " bi";
    if (d.variacao != null) chip(campo(raiz, "delta"), d.variacao, sinal(d.variacao * 100, 0) + "% vs mês");
    campo(raiz, "extra").textContent = "mês: R$ " + fmt(d.media_mes / 1e9, 2) + " bi/dia";
    const max = Math.max(1, ...d.classes.map((c) => c.media_5d));
    campo(raiz, "barras").innerHTML = d.classes.map((c, i) => `
      <span class="ic-barra"><span class="ic-barra-rot">${c.classe.startsWith("IPCA") ? "IPCA+" : "CDI+"}</span>
      <span class="ic-barra-trilho"><span style="width:${Math.round((c.media_5d / max) * 100)}%;background:${i === 0 ? LARANJA : "#111"}"></span></span>
      <span class="ic-barra-num">${fmt(c.media_5d / 1e6)} mi</span></span>`).join("");
    atualizarDatas("secundario", "Secundário até " + dm(d.referencia));
  }).catch(() => erro(document.getElementById("card-secundario")));

  // ---- Primário -----------------------------------------------------------
  buscar("/api/inicio/primario").then((d) => {
    const raiz = document.getElementById("card-primario");
    const m = d.mes, y = d.ytd;
    const mes = Number(m.inicio.slice(5, 7)) - 1, ano = m.inicio.slice(0, 4);
    campo(raiz, "mes-rot").textContent = `${MESES_LONGOS[mes]}/${ano.slice(2)} · ${fmt(m.ofertas)} ofertas`;
    campo(raiz, "mes").textContent = bi(m.volume);
    if (m.variacao != null) chip(campo(raiz, "mes-delta"), m.variacao, sinal(m.variacao * 100, 0) + "% YoY");
    campo(raiz, "mes-base").textContent = `${MESES[mes]}/${String(Number(ano) - 1).slice(2)}: ${bi(m.volume_ano_anterior)}`;
    const fimMes = Number(y.fim.slice(5, 7)) - 1;
    campo(raiz, "ytd-rot").textContent = `${y.inicio.slice(0, 4)} até ${dm(y.fim)} · ${fmt(y.ofertas)} ofertas`;
    campo(raiz, "ytd").textContent = bi(y.volume);
    if (y.variacao != null) chip(campo(raiz, "ytd-delta"), y.variacao, sinal(y.variacao * 100, 0) + "% YTD");
    campo(raiz, "ytd-base").textContent = `jan–${MESES[fimMes]}/${String(Number(y.inicio.slice(0, 4)) - 1).slice(2)}: ${bi(y.volume_ano_anterior)}`;
    const p = d.pipeline;
    campo(raiz, "fila").innerHTML = `<b>Fila</b> ${fmt(p.ofertas)} ofertas · R$ ${fmt(p.volume, 1)} bi` +
      p.por_status.map((s) => `<span class="muted"> · ${esc(s.status)}: ${fmt(s.ofertas)} · R$ ${fmt(s.volume, 1)} bi</span>`).join("");
    atualizarDatas("primario", "Primário até " + dm(d.disponivel_ate));
  }).catch(() => erro(document.getElementById("card-primario")));

  // ---- Notícias e ratings -----------------------------------------------
  const TIPOS_RATING = {   // mesma classificação da aba Notícias (static/app.js)
    rebaixamento: { plural: "rebaixamentos", simbolo: "▼" },
    elevacao: { plural: "elevações", simbolo: "▲" },
    perspectiva: { plural: "perspectiva", simbolo: "◐" },
    novo: { plural: "novos", simbolo: "+" },
    afirmacao: { plural: "afirmações", simbolo: "●" },
    outra: { plural: "outras", simbolo: "·" },
  };
  const semAcento = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  function tipoRating(titulo) {
    const s = semAcento(titulo);
    if (/rebaix|downgrade/.test(s)) return "rebaixamento";
    if (/\beleva|elevad|upgrade/.test(s)) return "elevacao";
    if (/coloca|observacao|creditwatch|revisa (a )?perspectiva|altera (a )?perspectiva/.test(s)) return "perspectiva";
    if (/atribui|atribuid|novo rating/.test(s)) return "novo";
    if (/afirma|reafirm|mantem|mantid/.test(s)) return "afirmacao";
    return "outra";
  }
  const hora = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    const hoje = new Date().toDateString() === d.toDateString();
    return hoje ? d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: "America/Sao_Paulo" })
      : d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", timeZone: "America/Sao_Paulo" });
  };

  buscar("/api/inicio/noticias").then((d) => {
    const raiz = document.getElementById("card-ratings");
    const porTipo = {};
    d.ratings.forEach((a) => { const t = tipoRating(a.titulo); porTipo[t] = (porTipo[t] || 0) + 1; });
    campo(raiz, "valor").textContent = fmt(d.ratings.length);
    campo(raiz, "pills").innerHTML = Object.keys(TIPOS_RATING).filter((t) => porTipo[t])
      .map((t) => `<span class="rt-pill rt-${t}">${TIPOS_RATING[t].simbolo} ${porTipo[t]} ${TIPOS_RATING[t].plural}</span>`).join("");
    const u = d.ratings[0];
    campo(raiz, "ultima").textContent = u ? `Última: ${u.empresas[0] || ""} · ${u.titulo}` : "Nenhuma ação nos últimos 5 dias.";

    document.getElementById("lista-noticias").innerHTML = d.noticias.length ? d.noticias.slice(0, 5).map((a) => {
      const tag = a.empresas[0] || (a.setores[0] ? "Setor: " + a.setores[0] : "");
      return `<a class="ic-item" href="${esc(a.url)}" target="_blank" rel="noopener">
        <span class="ic-meta">${hora(a.quando)} · ${esc(a.fonte)}${tag ? ` · <b>${esc(tag)}</b>` : ""}</span>
        <span class="ic-tit">${esc(a.titulo)}</span></a>`;
    }).join("") : '<span class="muted small">Nenhuma notícia na última semana.</span>';
  }).catch(() => {
    erro(document.getElementById("card-ratings"));
    document.getElementById("lista-noticias").innerHTML = '<span class="muted small">Erro ao carregar.</span>';
  });

  // ---- Relatórios ---------------------------------------------------------
  buscar("/api/inicio/relatorios").then((d) => {
    document.getElementById("lista-relatorios").innerHTML = d.relatorios.length ? d.relatorios.slice(0, 5).map((r) => {
      const sobre = r.empresas[0] || r.categoria || "";
      return `<a class="ic-item" href="${esc(r.url)}" target="_blank" rel="noopener">
        <span class="ic-meta">${dm(r.data)}${r.analista ? " · " + esc(r.analista) : ""}${sobre ? ` · <b>${esc(sobre)}</b>` : ""}</span>
        <span class="ic-tit">${esc(r.titulo)}</span></a>`;
    }).join("") : '<span class="muted small">Nenhum relatório no repositório.</span>';
  }).catch(() => { document.getElementById("lista-relatorios").innerHTML = '<span class="muted small">Erro ao carregar.</span>'; });

  // ---- Curvas -------------------------------------------------------------
  const ESTILO = {
    hoje: { cor: LARANJA, largura: 2.6, traco: [] },
    d5: { cor: "#111111", largura: 1.6, traco: [6, 3] },
    d21: { cor: "#9a9a9a", largura: 1.6, traco: [2, 3] },
  };
  const ROTULO = { hoje: "Hoje", d5: "d-5", d21: "d-21" };
  const ANOS_MAX = 10;

  function curva(canvasId, curvas, chave) {
    const datasets = curvas.map((c) => ({
      label: `${ROTULO[c.chave]} (${dm(c.data)})`,
      data: c.vertices.filter((v) => v[chave] != null && v.anos <= ANOS_MAX).map((v) => ({ x: v.anos, y: v[chave] })),
      borderColor: ESTILO[c.chave].cor, backgroundColor: ESTILO[c.chave].cor,
      borderWidth: ESTILO[c.chave].largura, borderDash: ESTILO[c.chave].traco,
      pointRadius: 0, tension: 0.3, order: c.chave === "hoje" ? 0 : 1,
    }));
    new Chart(document.getElementById(canvasId), {
      type: "line",
      data: { datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 8, boxHeight: 8, padding: 10, font: { size: 10 } } },
          tooltip: { callbacks: { title: (it) => fmt(it[0].raw.x, 1) + " anos", label: (it) => `${it.dataset.label}: ${fmt(it.raw.y, 2)}%` } },
        },
        scales: {
          x: { type: "linear", min: 0, max: ANOS_MAX, ticks: { stepSize: 2, callback: (v) => v + "a" }, grid: { display: false } },
          y: { ticks: { maxTicksLimit: 5, callback: (v) => fmt(v, 2) } },
        },
      },
    });
  }

  function nota(id, curvas, chave, vertice) {
    const ponto = (c) => (c ? c.vertices.find((v) => v.du === vertice) : null);
    const hoje = ponto(curvas.find((c) => c.chave === "hoje"));
    if (!hoje || hoje[chave] == null) return;
    const dif = (k) => {
      const p = ponto(curvas.find((c) => c.chave === k));
      return p && p[chave] != null ? `${sinal((hoje[chave] - p[chave]) * 100, 0)} bps em ${k === "d5" ? "5d" : "21d"}` : null;
    };
    document.getElementById(id).textContent = [`${Math.round(vertice / 252)}a: ${fmt(hoje[chave], 2)}%`, dif("d5"), dif("d21")]
      .filter(Boolean).join(" · ");
  }

  buscar("/api/inicio/curvas").then((d) => {
    if (!d.curvas.length) {
      ["nota-real", "nota-implicita"].forEach((id) => { document.getElementById(id).textContent = "Curva ainda não capturada."; });
      return;
    }
    curva("graf-real", d.curvas, "ipca");
    curva("graf-implicita", d.curvas, "implicita");
    nota("nota-real", d.curvas, "ipca", 2520);
    nota("nota-implicita", d.curvas, "implicita", 2520);
    atualizarDatas("curva", "Curva de " + dm(d.curvas[0].data));
  }).catch(() => {
    ["nota-real", "nota-implicita"].forEach((id) => { document.getElementById(id).textContent = "Erro ao carregar."; });
  });
})();
