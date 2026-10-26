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
  const abs = (v, c) => fmt(Math.abs(v), c);
  const dm = (iso) => (iso ? iso.slice(8, 10) + "/" + iso.slice(5, 7) : "—");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const bi = (v) => "R$ " + fmt(v / 1e9, 1) + " bi";
  const datas = {};

  function buscar(url) {
    return fetch(url, { credentials: "same-origin" }).then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  const campo = (raiz, nome) => raiz.querySelector(`[data-campo="${nome}"]`);
  // Variação como TEXTO colorido (sem selo). Spread segue a convenção da aba
  // Spreads: abertura laranja, fechamento preto. Volume: alta verde, queda vermelha.
  function variacao(v, texto, tipo) {
    const cls = v == null ? "" : tipo === "spread" ? (v > 0 ? "abre" : v < 0 ? "fecha" : "")
      : (v > 0 ? "alta" : v < 0 ? "queda" : "");
    const seta = v > 0 ? "▲" : v < 0 ? "▼" : "";
    return `<span class="ic-delta ${cls}">${seta} ${texto}</span>`;
  }
  const erro = (raiz) => raiz.classList.add("ic-erro");
  function atualizarDatas(chave, texto) {
    datas[chave] = texto;
    const ordem = ["spreads", "secundario", "primario", "curva"];
    document.getElementById("inicio-datas").textContent = ordem.filter((k) => datas[k]).map((k) => datas[k]).join(" · ");
  }

  // ---- Spreads e movimentos --------------------------------------------
  let spreadsDados = null;

  function desenharMovers(i) {
    const c = spreadsDados && spreadsDados.classes[i];
    const linha = (m, cls) => `<a class="ic-mov" href="/spreads" title="${esc(m.nome || m.codigo)} · spread ${fmt(m.spread, 0)} bps">
        <b>${esc(m.codigo)}</b><span class="ic-mov-nome">${esc(m.nome || "")}</span>
        <span class="ic-mov-d ${cls}">${sinal(m.variacao_bps, 1)}</span></a>`;
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
      campo(raiz, "delta").innerHTML = c.variacao_bps == null ? ""
        : variacao(c.variacao_bps, abs(c.variacao_bps, 1) + " bps", "spread") + ' <span class="ic-cinza">na semana</span>';
      raiz.title = c.data_comparacao ? `${dm(c.data_referencia)} contra ${dm(c.data_comparacao)}` : "";
    });
    const ref = d.classes[0] && d.classes[0].data_referencia;
    if (ref) atualizarDatas("spreads", "Spreads de " + dm(ref));
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
    const alvo = campo(document.getElementById("card-secundario"), "classes");
    if (!d.referencia) { alvo.innerHTML = '<span class="muted small">Nenhum export importado ainda.</span>'; return; }
    alvo.innerHTML = d.classes.map((c) => {
      const v = c.media_mes ? c.media_5d / c.media_mes - 1 : null;
      return `<span class="ic-par-linha">
        <span class="ic-par-rot">${c.classe.startsWith("IPCA") ? "IPCA+ Incentivadas" : "CDI+ Tradicionais"}</span>
        <span class="ic-par-num">R$ ${fmt(c.media_5d / 1e6)} mi</span>
        <span class="ic-par-var">${v == null ? "" : variacao(v, abs(v * 100, 0) + "%", "volume") + ' <span class="ic-cinza">vs mês</span>'}</span>
      </span>`;
    }).join("");
    atualizarDatas("secundario", "secundário até " + dm(d.referencia));
  }).catch(() => erro(document.getElementById("card-secundario")));

  // ---- Primário -----------------------------------------------------------
  buscar("/api/inicio/primario").then((d) => {
    const raiz = document.getElementById("card-primario");
    const m = d.mes, y = d.ytd;
    const mes = Number(m.inicio.slice(5, 7)) - 1, ano = m.inicio.slice(0, 4);
    const anoAnt = String(Number(ano) - 1).slice(2);
    campo(raiz, "mes-rot").textContent = `${MESES_LONGOS[mes]}/${ano.slice(2)}`;
    campo(raiz, "mes").textContent = bi(m.volume);
    campo(raiz, "mes-delta").innerHTML = m.variacao == null ? ""
      : variacao(m.variacao, abs(m.variacao * 100, 0) + "%", "volume")
        + ` <span class="ic-cinza" title="${MESES[mes]}/${anoAnt}: ${bi(m.volume_ano_anterior)}">vs ${MESES[mes]}/${anoAnt}</span>`;
    const fimMes = Number(y.fim.slice(5, 7)) - 1;
    const anoY = y.inicio.slice(0, 4);
    campo(raiz, "ytd-rot").textContent = `${anoY} até ${dm(y.fim)}`;
    campo(raiz, "ytd").textContent = bi(y.volume);
    campo(raiz, "ytd-delta").innerHTML = y.variacao == null ? ""
      : variacao(y.variacao, abs(y.variacao * 100, 0) + "%", "volume")
        + ` <span class="ic-cinza" title="jan–${MESES[fimMes]}/${String(Number(anoY) - 1).slice(2)}: ${bi(y.volume_ano_anterior)}">vs ${Number(anoY) - 1}</span>`;
    atualizarDatas("primario", "primário até " + dm(d.disponivel_ate));
  }).catch(() => erro(document.getElementById("card-primario")));

  // ---- Ratings e notícias -------------------------------------------------
  const TIPOS_RATING = {   // mesma classificação da aba Notícias (static/app.js)
    rebaixamento: { rotulo: "Rebaixamento", simbolo: "▼" },
    elevacao: { rotulo: "Elevação", simbolo: "▲" },
    perspectiva: { rotulo: "Perspectiva", simbolo: "◐" },
    novo: { rotulo: "Novo rating", simbolo: "+" },
    afirmacao: { rotulo: "Afirmação", simbolo: "●" },
    outra: { rotulo: "Outra", simbolo: "·" },
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
  const agencia = (f) => (/fitch/i.test(f) ? "Fitch" : /s&p|standard/i.test(f) ? "S&P" : /moody/i.test(f) ? "Moody's" : f);
  const quando = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    const hoje = new Date().toDateString() === d.toDateString();
    return hoje ? d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: "America/Sao_Paulo" })
      : d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", timeZone: "America/Sao_Paulo" });
  };
  const item = (url, meta, titulo) => `<a class="ic-item" href="${esc(url)}" target="_blank" rel="noopener">
      <span class="ic-meta">${meta}</span><span class="ic-tit">${esc(titulo)}</span></a>`;

  buscar("/api/inicio/noticias").then((d) => {
    document.getElementById("ratings-total").textContent = d.ratings.length ? `· ${d.ratings.length} em 5 dias` : "";
    document.getElementById("lista-ratings").innerHTML = d.ratings.length ? d.ratings.slice(0, 5).map((a) => {
      const t = tipoRating(a.titulo);
      const meta = `<span class="rt-pill rt-${t}">${TIPOS_RATING[t].simbolo} ${TIPOS_RATING[t].rotulo}</span>`
        + `${a.empresas[0] ? ` <b class="ic-meta-emp">${esc(a.empresas[0])}</b>` : ""} · ${esc(agencia(a.fonte))} · ${quando(a.quando)}`;
      return item(a.url, meta, a.titulo);
    }).join("") : '<span class="muted small">Nenhuma ação nos últimos 5 dias.</span>';

    document.getElementById("lista-noticias").innerHTML = d.noticias.length ? d.noticias.slice(0, 5).map((a) => {
      const tag = a.empresas[0] || a.setores[0] || "";
      return item(a.url, `${quando(a.quando)} · ${esc(a.fonte)}${tag ? ` · <b class="ic-meta-emp">${esc(tag)}</b>` : ""}`, a.titulo);
    }).join("") : '<span class="muted small">Nenhuma notícia na última semana.</span>';
  }).catch(() => {
    ["lista-ratings", "lista-noticias"].forEach((id) => { document.getElementById(id).innerHTML = '<span class="muted small">Erro ao carregar.</span>'; });
  });

  // ---- Relatórios ---------------------------------------------------------
  buscar("/api/inicio/relatorios").then((d) => {
    document.getElementById("lista-relatorios").innerHTML = d.relatorios.length ? d.relatorios.slice(0, 5).map((r) => {
      const sobre = r.empresas[0] || r.categoria || "";
      return item(r.url, `${dm(r.data)}${r.analista ? " · " + esc(r.analista) : ""}${sobre ? ` · <b class="ic-meta-emp">${esc(sobre)}</b>` : ""}`, r.titulo);
    }).join("") : '<span class="muted small">Nenhum relatório no repositório.</span>';
  }).catch(() => { document.getElementById("lista-relatorios").innerHTML = '<span class="muted small">Erro ao carregar.</span>'; });

  // ---- Curvas -------------------------------------------------------------
  const ESTILO = {
    hoje: { cor: LARANJA, largura: 2.5, traco: [] },
    d5: { cor: "#222222", largura: 1.4, traco: [5, 3] },
    d21: { cor: "#a8a8a8", largura: 1.4, traco: [2, 3] },
  };
  const ROTULO = { hoje: "Hoje", d5: "d-5", d21: "d-21" };
  const ANOS_MAX = 10;

  function curva(canvasId, curvas, chave) {
    const datasets = curvas.map((c) => ({
      label: `${ROTULO[c.chave]} (${dm(c.data)})`,
      // A partir de 6 meses: a ponta curtíssima da ETTJ real é instável e
      // esticava a escala sem dizer nada.
      data: c.vertices.filter((v) => v[chave] != null && v.anos >= 0.5 && v.anos <= ANOS_MAX).map((v) => ({ x: v.anos, y: v[chave] })),
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
          legend: { position: "top", align: "end", labels: { boxWidth: 7, boxHeight: 7, padding: 8, font: { size: 10 } } },
          tooltip: { callbacks: { title: (it) => fmt(it[0].raw.x, 1) + " anos", label: (it) => `${it.dataset.label}: ${fmt(it.raw.y, 2)}%` } },
        },
        scales: {
          x: { type: "linear", min: 0, max: ANOS_MAX, ticks: { stepSize: 2, callback: (v) => v + "a" }, grid: { display: false } },
          y: { ticks: { maxTicksLimit: 5, callback: (v) => fmt(v, 1) }, grid: { color: "#f0f0f0" }, border: { display: false } },
        },
      },
    });
  }

  function nota(id, curvas, chave, vertice) {
    const ponto = (c) => (c ? c.vertices.find((v) => v.du === vertice) : null);
    const hoje = ponto(curvas.find((c) => c.chave === "hoje"));
    if (!hoje || hoje[chave] == null) return;
    const dif = (k, rot) => {
      const p = ponto(curvas.find((c) => c.chave === k));
      return p && p[chave] != null ? `<span>${sinal((hoje[chave] - p[chave]) * 100, 0)} bps ${rot}</span>` : "";
    };
    document.getElementById(id).innerHTML = `<b>10 anos: ${fmt(hoje[chave], 2)}%</b>${dif("d5", "em 5 dias")}${dif("d21", "em 21 dias")}`;
  }

  buscar("/api/inicio/curvas").then((d) => {
    if (!d.curvas.length) {
      ["nota-pre", "nota-real"].forEach((id) => { document.getElementById(id).textContent = "Curva ainda não capturada."; });
      return;
    }
    curva("graf-pre", d.curvas, "pre");
    curva("graf-real", d.curvas, "ipca");
    nota("nota-pre", d.curvas, "pre", 2520);
    nota("nota-real", d.curvas, "ipca", 2520);
    atualizarDatas("curva", "curvas de " + dm(d.curvas[0].data));
  }).catch(() => {
    ["nota-pre", "nota-real"].forEach((id) => { document.getElementById(id).textContent = "Erro ao carregar."; });
  });
})();
