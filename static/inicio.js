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

  // ---- Abertura: dia, saudação e o resumo em uma frase (28/10/2026) -------
  // A frase é montada com o que os outros blocos já trouxeram -- nenhuma
  // consulta a mais. Cada pedaço entra quando o seu bloco chega.
  const DIAS_SEMANA = ["domingo", "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado"];
  (function () {
    const agora = new Date();
    const h = Number(agora.toLocaleString("pt-BR", { hour: "numeric", hour12: false, timeZone: "America/Sao_Paulo" }));
    document.getElementById("hero-saudacao").textContent = h < 12 ? "Bom dia" : h < 18 ? "Boa tarde" : "Boa noite";
    document.getElementById("hero-dia").textContent = agora.toLocaleDateString("pt-BR",
      { weekday: "long", day: "numeric", month: "long", timeZone: "America/Sao_Paulo" });
  })();
  const brief = {};
  function briefing(chave, html) {
    brief[chave] = html;
    const ordem = ["spreads", "resultados", "ratings"];
    const partes = ordem.filter((k) => brief[k]).map((k) => `<span>${brief[k]}</span>`);
    if (partes.length) document.getElementById("hero-brief").innerHTML = partes.join("");
  }

  // ---- Placar: três cards no mesmo desenho (28/10/2026, 4ª rodada) -------
  // Cada card tem duas linhas: rótulo, número e variação à direita.
  function linhas(id, itens) {
    campo(document.getElementById(id), "linhas").innerHTML = itens.map((l) => `<span class="ic-par-linha">
        <span class="ic-par-rot"${l.dica ? ` title="${esc(l.dica)}"` : ""}>${l.rot}</span>
        <span class="ic-par-num">${l.num}</span>
        <span class="ic-par-var">${l.var || ""}</span>
      </span>`).join("");
  }
  const vazio = (id, texto) => { campo(document.getElementById(id), "linhas").innerHTML = `<span class="muted small">${texto}</span>`; };

  buscar("/api/inicio/spreads").then((d) => {
    linhas("card-spreads", d.classes.map((c, i) => ({
      rot: i === 0 ? "IPCA+ Incentivadas" : "CDI+ Tradicionais",
      num: `${fmt(c.spread, 1)} <small>bps</small>`,
      var: c.variacao_bps == null ? "" : variacao(c.variacao_bps, abs(c.variacao_bps, 1) + " bps", "spread") + ' <span class="ic-cinza">na semana</span>',
      dica: c.data_comparacao ? `${dm(c.data_referencia)} contra ${dm(c.data_comparacao)}` : "",
    })));
    const c0 = d.classes[0];
    if (c0 && c0.variacao_bps != null) {
      const v = c0.variacao_bps;
      briefing("spreads", Math.abs(v) < 0.05 ? "Spread IPCA+ estável na semana"
        : `Spread IPCA+ <b class="${v > 0 ? "h-abre" : "h-fecha"}">${v > 0 ? "abriu" : "fechou"} ${abs(v, 1)} bps</b> na semana`);
    }
    if (c0 && c0.data_referencia) atualizarDatas("spreads", "Spreads de " + dm(c0.data_referencia));
  }).catch(() => erro(document.getElementById("card-spreads")));

  buscar("/api/inicio/primario").then((d) => {
    const m = d.mes, y = d.ytd;
    const mes = Number(m.inicio.slice(5, 7)) - 1, ano = m.inicio.slice(0, 4);
    const anoAnt = String(Number(ano) - 1).slice(2);
    const fimMes = Number(y.fim.slice(5, 7)) - 1, anoY = y.inicio.slice(0, 4);
    linhas("card-primario", [
      { rot: `${MESES_LONGOS[mes]}/${ano.slice(2)}`, num: bi(m.volume),
        var: m.variacao == null ? "" : variacao(m.variacao, abs(m.variacao * 100, 0) + "%", "volume") + ` <span class="ic-cinza">vs ${MESES[mes]}/${anoAnt}</span>`,
        dica: `${MESES[mes]}/${anoAnt}: ${bi(m.volume_ano_anterior)}` },
      { rot: `${anoY} até ${dm(y.fim)}`, num: bi(y.volume),
        var: y.variacao == null ? "" : variacao(y.variacao, abs(y.variacao * 100, 0) + "%", "volume") + ` <span class="ic-cinza">vs ${Number(anoY) - 1}</span>`,
        dica: `jan–${MESES[fimMes]}/${String(Number(anoY) - 1).slice(2)}: ${bi(y.volume_ano_anterior)}` },
    ]);
    atualizarDatas("primario", "primário até " + dm(d.disponivel_ate));
  }).catch(() => erro(document.getElementById("card-primario")));

  buscar("/api/inicio/secundario").then((d) => {
    if (!d.referencia) { vazio("card-secundario", "Nenhum export importado ainda."); return; }
    linhas("card-secundario", d.classes.map((c) => {
      const v = c.media_mes ? c.media_5d / c.media_mes - 1 : null;
      return {
        rot: c.classe.startsWith("IPCA") ? "IPCA+ Incentivadas" : "CDI+ Tradicionais",
        num: `R$ ${fmt(c.media_5d / 1e6)} mi`,
        var: v == null ? "" : variacao(v, abs(v * 100, 0) + "%", "volume") + ' <span class="ic-cinza">vs mês</span>',
      };
    }));
    atualizarDatas("secundario", "secundário até " + dm(d.referencia));
  }).catch(() => erro(document.getElementById("card-secundario")));

  // ---- Temporada de resultados na abertura ---------------------------------
  // O calendário em si está na aba Calendário; aqui só o contador e a frase.
  buscar("/api/inicio/resultados").then((d) => {
    const t = d.temporada, r = d.resumo;
    if (!t) return;
    const dentro = r.total;
    const pct = (n) => (dentro ? (100 * n / dentro) : 0).toFixed(1) + "%";
    document.getElementById("hero-temp-rotulo").textContent = t.rotulo;
    document.getElementById("hero-temp-placar").innerHTML = ` <b>${r.divulgada}</b>/${dentro} divulgaram`;
    document.getElementById("hero-temp-barra").innerHTML =
      `<i class="b-div" style="width:${pct(r.divulgada)}"></i><i class="b-conf" style="width:${pct(r.agendada)}"></i>`;
    document.getElementById("hero-temp-prazo").textContent = t.encerrada
      ? "encerrada"
      : t.dias_para_prazo >= 0 ? `prazo ${dm(t.prazo)} · ${t.dias_para_prazo === 0 ? "hoje" : `${t.dias_para_prazo}d`}`
        : `prazo venceu ${dm(t.prazo)}`;
    document.getElementById("hero-temp-prazo").title = `Prazo da CVM para o ${t.documento} do ${t.rotulo}: ${dm(t.prazo)}`;
    document.getElementById("hero-temp").hidden = false;
    const hojeL = d.empresas.filter((e) => e.data === d.hoje && e.status !== "divulgada");
    const nomes = (l) => l.slice(0, 3).map((e) => `<b>${esc(e.nome)}</b>`).join(", ") + (l.length > 3 ? ` e mais ${l.length - 3}` : "");
    if (hojeL.length) briefing("resultados", `Divulgam hoje: ${nomes(hojeL)}`);
    else if (r.proxima_total) briefing("resultados", `<b>${r.semana_total - r.semana_divulgadas}</b> resultados ainda esta semana e <b>${r.proxima_total}</b> na próxima`);
    else if (!t.encerrada) briefing("resultados", `<b>${r.divulgada}</b> de ${dentro} já divulgaram o ${t.rotulo}`);
  }).catch(() => {});

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

  // Ratings e notícias numa lista só, da mais recente para a mais antiga.
  let feed = [], feedFiltro = "tudo";
  function desenharFeed() {
    const lista = feed.filter((a) => feedFiltro === "tudo" || a.grupo === feedFiltro);
    document.getElementById("lista-feed").innerHTML = lista.length ? lista.slice(0, 80).map((a) => {
      let selo;
      if (a.grupo === "rating") {
        const t = tipoRating(a.titulo);
        selo = `<span class="rt-pill rt-${t}">${TIPOS_RATING[t].simbolo} ${TIPOS_RATING[t].rotulo}</span>`;
      } else selo = "";   // notícia é o padrão; só o rating ganha selo
      const tag = a.empresas[0] || a.setores[0] || "";
      const fonte = a.grupo === "rating" ? agencia(a.fonte) : a.fonte;
      const partes = [tag ? `<b class="ic-meta-emp">${esc(tag)}</b>` : "", esc(fonte), quando(a.quando)].filter(Boolean).join(" · ");
      return item(a.url, `${selo ? selo + " " : ""}${partes}`, a.titulo);
    }).join("") : '<span class="muted small">Nada nos últimos dias.</span>';
  }
  document.getElementById("feed-tabs").addEventListener("click", (ev) => {
    const b = ev.target.closest(".win-btn");
    if (!b) return;
    ev.currentTarget.querySelectorAll(".win-btn").forEach((x) => x.classList.toggle("active", x === b));
    feedFiltro = b.dataset.f;
    desenharFeed();
    document.getElementById("lista-feed").scrollTop = 0;
  });

  buscar("/api/inicio/noticias").then((d) => {
    const rebaix = d.ratings.filter((a) => tipoRating(a.titulo) === "rebaixamento").length;
    if (d.ratings.length) briefing("ratings", `<b>${d.ratings.length}</b> ações de rating em 5 dias${rebaix ? `, ${rebaix} rebaixamento${rebaix > 1 ? "s" : ""}` : ""}`);
    feed = d.ratings.map((a) => ({ ...a, grupo: "rating" })).concat(d.noticias.map((a) => ({ ...a, grupo: "noticia" })))
      .sort((a, b) => String(b.quando || "").localeCompare(String(a.quando || "")));
    desenharFeed();
  }).catch(() => { document.getElementById("lista-feed").innerHTML = '<span class="muted small">Erro ao carregar.</span>'; });

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

  // Captação líquida mensal (29/10/2026): Infra laranja, CP Tradicional preto.
  buscar("/api/captacao/mensal").then((d) => {
    const cv = document.getElementById("graf-captacao");
    if (!cv) return;
    if (!d.meses || !d.meses.length) {
      cv.parentElement.innerHTML = '<div class="ic-vazio-grande">Sem dados de captação ainda.</div>';
      return;
    }
    const ini = Math.max(0, d.meses.length - 13);
    const P = window.PADRAO_GRAFICO;
    const ref = (d.referencia || "").slice(0, 7);
    const bi = (v) => (v == null ? null : v / 1e9);
    new Chart(cv, {
      type: "bar",
      data: {
        labels: d.meses.slice(ini).map((m) => P.mesAno(m) + (m.slice(0, 7) === ref ? "*" : "")),
        datasets: [
          { label: "Infra", data: d.series["Infra"].captacao.slice(ini).map(bi), backgroundColor: "#FF6200", borderWidth: 0 },
          { label: "CP Tradicional", data: d.series["CP ex Infra"].captacao.slice(ini).map(bi), backgroundColor: "#111111", borderWidth: 0 },
        ],
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "top", align: "end" },
          tooltip: { callbacks: { label: (it) => ` ${it.dataset.label}: R$ ${Number(it.raw).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} bi` } },
        },
        scales: { x: { grid: { display: false } }, y: { title: { display: true, text: "R$ bi" } } },
      },
      plugins: [P.linhaZero],
    });
  }).catch(() => {});
})();
