/* Aba Balcão B3 — volumetria de negociação de DEB/CRI/CRA.
 *
 * Sem framework, igual ao resto do Hub. Chart.js vem do jsDelivr — a URL
 * exata da documentação oficial, NÃO adivinhada: o cdnjs tem path
 * case-sensitive e "Chart.js" devolve 404, o que já custou uma sessão
 * inteira de "nenhum gráfico aparece" (24/07/2026).
 */
(function () {
  "use strict";

  var estado = {
    tipos: [],            // [] = todos
    janela: 5,
    serieDias: 90,
    serieMetrica: "volume",
    rankingOrdem: "volume",
    vsClasse: "IPCA + Incentivadas",
    rankingCache: null,
  };
  var graficos = {};

  // ---- utilidades ------------------------------------------------------
  function qs(base, extra) {
    var p = new URLSearchParams();
    estado.tipos.forEach(function (t) { p.append("tipo", t); });
    Object.keys(extra || {}).forEach(function (k) {
      if (extra[k] !== null && extra[k] !== undefined && extra[k] !== "") p.append(k, extra[k]);
    });
    var s = p.toString();
    return base + (s ? "?" + s : "");
  }

  function buscar(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
      return r.json();
    });
  }

  var fmtInt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
  var fmt2 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var fmt4 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 4, maximumFractionDigits: 4 });

  /* Volume em R$ é sempre grande demais para ler cru: 3.771.006.842 não
   * comunica nada. Abreviado, com o valor exato no title da célula. */
  function moeda(v) {
    if (v === null || v === undefined) return "—";
    var a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(2).replace(".", ",") + " bi";
    if (a >= 1e6) return (v / 1e6).toFixed(1).replace(".", ",") + " mi";
    if (a >= 1e3) return (v / 1e3).toFixed(0) + " mil";
    return fmtInt.format(v);
  }
  function num(v, casas) {
    if (v === null || v === undefined) return "—";
    return casas === 4 ? fmt4.format(v) : fmt2.format(v);
  }
  function pct(v) {
    if (v === null || v === undefined) return "—";
    return fmt2.format(v * 100) + "%";
  }
  function dataBR(iso) {
    if (!iso) return "—";
    var p = String(iso).slice(0, 10).split("-");
    return p[2] + "/" + p[1] + "/" + p[0];
  }
  function celulas(linha, cols) {
    return cols.map(function (c) {
      return "<td" + (c.num ? ' class="num"' : "") +
        (c.title ? ' title="' + c.title + '"' : "") + ">" + c.v + "</td>";
    }).join("");
  }
  function vazio(tbody, colspan, msg) {
    tbody.innerHTML = '<tr><td colspan="' + colspan + '" class="muted">' + msg + "</td></tr>";
  }

  // ---- 1. termômetro ---------------------------------------------------
  function carregarTermometro() {
    buscar(qs("/api/balcao/termometro")).then(function (d) {
      var el = document.getElementById("termometro-tag");
      if (!d || d.razao === null || d.razao === undefined) {
        el.textContent = "Termômetro: sem base de comparação";
        return;
      }
      var r = d.razao;
      var leitura = r >= 1.3 ? "acima do normal" : (r <= 0.7 ? "abaixo do normal" : "dentro do normal");
      el.textContent = "Dia " + leitura + " · " + fmt2.format(r) + "× a mediana de " + d.pregoes_comparados + " pregões";
      el.title = "Volume do dia: " + moeda(d.volume) + " · mediana: " + moeda(d.mediana);
    }).catch(function () {
      document.getElementById("termometro-tag").textContent = "Termômetro: —";
    });
  }

  // ---- 2. volumetria ---------------------------------------------------
  function carregarVolumetria() {
    var wrap = document.getElementById("cartoes-volumetria");
    buscar(qs("/api/balcao/volumetria")).then(function (d) {
      document.getElementById("dados-ate").textContent = "Dados até: " + dataBR(d.referencia);
      if (!d.janelas || !d.janelas.length) {
        wrap.innerHTML = '<div class="kpi-card"><span class="muted small">Sem dado ainda.</span></div>';
        return;
      }
      wrap.innerHTML = d.janelas.map(function (j) {
        var semDado = !j.pregoes;
        return '<div class="kpi-card">' +
          '<div class="muted small">' + j.rotulo + ' <span class="kpi-tag">' +
            (semDado ? "sem pregão" : j.pregoes + " pregões") + '</span></div>' +
          '<div style="font-size:1.25rem;font-weight:700;margin-top:2px;" title="' +
            (j.volume ? fmtInt.format(j.volume) : "") + '">' +
            (semDado ? "—" : moeda(j.volume_dia)) + '<span class="muted small">/dia</span></div>' +
          '<div class="muted small">' +
            (semDado ? "" : fmtInt.format(Math.round(j.negocios_dia)) + " negócios/dia · " +
              fmtInt.format(j.tickers) + " tickers") +
          '</div></div>';
      }).join("");
    }).catch(function (e) {
      wrap.innerHTML = '<div class="kpi-card"><span class="muted small">Erro: ' + e.message + "</span></div>";
    });
  }

  // ---- 3. série --------------------------------------------------------
  function carregarSerie() {
    buscar(qs("/api/balcao/serie", { dias: estado.serieDias })).then(function (d) {
      var ctx = document.getElementById("grafico-serie");
      if (graficos.serie) graficos.serie.destroy();
      var pts = d.pontos || [];
      var labels = pts.map(function (p) { return dataBR(p.data); });
      var datasets;
      if (estado.serieMetrica === "negocios") {
        datasets = [{ label: "Negócios", data: pts.map(function (p) { return p.negocios; }),
                      backgroundColor: "#0b63c5" }];
      } else {
        datasets = [
          { label: "DEB", data: pts.map(function (p) { return p.DEB; }), backgroundColor: "#0b63c5" },
          { label: "CRI", data: pts.map(function (p) { return p.CRI; }), backgroundColor: "#e08b2f" },
          { label: "CRA", data: pts.map(function (p) { return p.CRA; }), backgroundColor: "#5aa469" },
        ];
      }
      graficos.serie = new Chart(ctx, {
        type: "bar",
        data: { labels: labels, datasets: datasets },
        options: {
          responsive: true, maintainAspectRatio: true,
          scales: {
            x: { stacked: true, ticks: { maxTicksLimit: 14, font: { size: 10 } }, grid: { display: false } },
            y: { stacked: true, ticks: { callback: function (v) { return moeda(v); }, font: { size: 10 } } },
          },
          plugins: {
            legend: { display: estado.serieMetrica === "volume", position: "bottom" },
            tooltip: {
              callbacks: {
                label: function (c) {
                  return c.dataset.label + ": " +
                    (estado.serieMetrica === "negocios" ? fmtInt.format(c.parsed.y) : moeda(c.parsed.y));
                },
              },
            },
          },
        },
      });
    });
  }

  // ---- 4. ranking ------------------------------------------------------
  function carregarRanking() {
    var tbody = document.querySelector("#tabela-ranking tbody");
    buscar(qs("/api/balcao/ranking", { dias: estado.janela, limite: 60 })).then(function (d) {
      estado.rankingCache = d;
      document.getElementById("ranking-janela").textContent =
        dataBR(d.inicio) + " a " + dataBR(d.referencia);
      desenharRanking();
    }).catch(function (e) { vazio(tbody, 12, "Erro: " + e.message); });
  }

  function desenharRanking() {
    var tbody = document.querySelector("#tabela-ranking tbody");
    var d = estado.rankingCache;
    if (!d || !d.linhas.length) { vazio(tbody, 12, "Sem negócio na janela."); return; }

    var linhas = d.linhas.slice();
    if (estado.rankingOrdem === "giro") {
      /* Papel sem giro (CRI/CRA sem cadastro de estoque) vai para o fim em
       * vez de sumir: some do ranking daria a impressão de que não negociou. */
      linhas.sort(function (a, b) {
        if (a.giro === null && b.giro === null) return b.volume - a.volume;
        if (a.giro === null) return 1;
        if (b.giro === null) return -1;
        return b.giro - a.giro;
      });
    }
    tbody.innerHTML = linhas.map(function (l) {
      return "<tr>" + celulas(l, [
        { v: "<b>" + l.codigo + "</b>" },
        { v: l.tipo || "—" },
        { v: l.emissor || '<span class="muted">—</span>' },
        { v: l.indexador || "—" },
        { v: moeda(l.volume), num: true, title: fmtInt.format(l.volume) },
        { v: fmtInt.format(l.negocios), num: true },
        { v: l.pregoes, num: true },
        { v: moeda(l.maior_negocio), num: true },
        { v: num(l.taxa_media, 4), num: true },
        { v: l.spread_medio === null ? '<span class="muted">—</span>' : fmt2.format(l.spread_medio), num: true },
        { v: moeda(l.estoque), num: true },
        { v: l.giro === null ? '<span class="muted" title="sem estoque cadastrado">—</span>' : pct(l.giro), num: true },
      ]) + "</tr>";
    }).join("");
  }

  // ---- 5. volume x spread ---------------------------------------------
  function carregarVolumeSpread() {
    buscar(qs("/api/balcao/volume-spread", { dias: estado.janela, classe: estado.vsClasse }))
      .then(function (d) {
        /* Duas exclusões diferentes, mostradas separadas de propósito:
         * "sem cadastro" é quase todo CRI/CRA e não é anomalia; "sem
         * spread" é papel da classe que deveria ter e não tem. Juntar os
         * dois num número só esconderia o segundo. */
        var tag = document.getElementById("vs-sem-spread");
        var partes = [];
        if (d.sem_spread) partes.push(d.sem_spread + " sem spread");
        if (d.sem_cadastro) partes.push(d.sem_cadastro + " sem cadastro (CRI/CRA)");
        tag.textContent = partes.length ? "Fora do gráfico: " + partes.join(" · ")
                                        : "todos os tickers no gráfico";

        var ctx = document.getElementById("grafico-volume-spread");
        if (graficos.vs) graficos.vs.destroy();
        var pts = (d.pontos || []).map(function (p) {
          return { x: p.volume, y: p.spread_medio, codigo: p.codigo,
                   emissor: p.emissor, negocios: p.negocios };
        });
        graficos.vs = new Chart(ctx, {
          type: "scatter",
          data: { datasets: [{ label: estado.vsClasse, data: pts,
                               backgroundColor: "rgba(11,99,197,0.6)", pointRadius: 5 }] },
          options: {
            responsive: true, maintainAspectRatio: true,
            scales: {
              x: { type: "logarithmic", title: { display: true, text: "Volume negociado (R$, escala log)" },
                   ticks: { callback: function (v) { return moeda(v); }, font: { size: 10 } } },
              y: { title: { display: true, text: "Spread médio (bps)" }, ticks: { font: { size: 10 } } },
            },
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: {
                  label: function (c) {
                    var p = c.raw;
                    return [p.codigo + (p.emissor ? " — " + p.emissor : ""),
                            "Volume: " + moeda(p.x),
                            "Spread: " + fmt2.format(p.y) + " bps",
                            p.negocios + " negócio(s)"];
                  },
                },
              },
            },
          },
        });
      });
  }

  // ---- 6. ao vivo ------------------------------------------------------
  function carregarDestaques() {
    buscar(qs("/api/balcao/destaques", { dias: 3, limite: 10 })).then(function (d) {
      document.getElementById("baseline-nota").textContent =
        "Baseline = mediana ponderada por volume de " + dataBR(d.baseline_inicio) +
        " a " + dataBR(d.baseline_fim) + ". Só entram papéis com pelo menos 3 negócios e " +
        "R$ 1 milhão na janela — sem esse piso o topo do ranking seria sempre papel " +
        "ilíquido com um print solto.";

      preencherDestaques("#tabela-aberturas tbody", d.aberturas, "cell-abertura");
      preencherDestaques("#tabela-fechamentos tbody", d.fechamentos, "cell-fechamento");
    }).catch(function (e) {
      vazio(document.querySelector("#tabela-aberturas tbody"), 5, "Erro: " + e.message);
      vazio(document.querySelector("#tabela-fechamentos tbody"), 5, "Erro: " + e.message);
    });
  }

  function preencherDestaques(sel, linhas, classe) {
    var tbody = document.querySelector(sel);
    if (!linhas || !linhas.length) {
      vazio(tbody, 5, "Nenhum papel com liquidez suficiente na janela.");
      return;
    }
    tbody.innerHTML = linhas.map(function (l) {
      return "<tr>" +
        "<td><b>" + l.codigo + "</b></td>" +
        '<td>' + (l.emissor || '<span class="muted">—</span>') + "</td>" +
        '<td class="num">' + fmt2.format(l.spread_hoje) + "</td>" +
        '<td class="num">' + fmt2.format(l.spread_baseline) + "</td>" +
        '<td class="num ' + classe + '">' +
          (l.variacao_bps > 0 ? "+" : "") + fmt2.format(l.variacao_bps) + "</td>" +
        "</tr>";
    }).join("");
  }

  function carregarTape() {
    var tbody = document.querySelector("#tabela-tape tbody");
    buscar(qs("/api/balcao/tape", { limite: 60 })).then(function (d) {
      var tag = document.getElementById("tape-atualizado");
      if (d.atualizado_em) {
        var dt = new Date(d.atualizado_em);
        tag.textContent = "capturado às " + dt.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
      } else {
        tag.textContent = "sem captura recente";
      }
      if (!d.linhas.length) { vazio(tbody, 10, "Nenhum negócio confirmado nos últimos 5 pregões."); return; }
      tbody.innerHTML = d.linhas.map(function (l) {
        return "<tr>" +
          "<td>" + dataBR(l.data) + "</td>" +
          "<td>" + (l.horario || "—") + "</td>" +
          "<td><b>" + l.codigo + "</b></td>" +
          "<td>" + (l.tipo || "—") + "</td>" +
          "<td>" + (l.emissor || '<span class="muted">—</span>') + "</td>" +
          '<td class="num">' + fmtInt.format(l.quantidade || 0) + "</td>" +
          '<td class="num">' + num(l.preco, 4) + "</td>" +
          '<td class="num" title="' + fmtInt.format(l.volume || 0) + '">' + moeda(l.volume) + "</td>" +
          '<td class="num">' + num(l.taxa, 4) + "</td>" +
          '<td class="num">' + (l.spread === null ? '<span class="muted">—</span>' : fmt2.format(l.spread)) + "</td>" +
          "</tr>";
      }).join("");
    }).catch(function (e) { vazio(tbody, 10, "Erro: " + e.message); });
  }

  // ---- ligação ---------------------------------------------------------
  function tudo() {
    carregarTermometro();
    carregarVolumetria();
    carregarSerie();
    carregarRanking();
    carregarVolumeSpread();
    carregarDestaques();
    carregarTape();
  }

  function grupoBotoes(sel, attr, aoTrocar) {
    var grupo = document.querySelector(sel);
    if (!grupo) return;
    grupo.addEventListener("click", function (ev) {
      var b = ev.target.closest("button");
      if (!b || !grupo.contains(b)) return;
      grupo.querySelectorAll("button").forEach(function (x) { x.classList.remove("active"); });
      b.classList.add("active");
      aoTrocar(b.getAttribute(attr));
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    grupoBotoes("#tipo-tabs", "data-tipo", function (v) {
      estado.tipos = v ? [v] : [];
      tudo();
    });
    grupoBotoes("#serie-tabs", "data-dias", function (v) {
      estado.serieDias = parseInt(v, 10); carregarSerie();
    });
    grupoBotoes("#serie-metrica", "data-metrica", function (v) {
      estado.serieMetrica = v; carregarSerie();
    });
    grupoBotoes("#ranking-ordem", "data-ordem", function (v) {
      estado.rankingOrdem = v; desenharRanking();   // reordena o que já veio
    });
    grupoBotoes("#vs-classe", "data-classe", function (v) {
      estado.vsClasse = v; carregarVolumeSpread();
    });

    var sel = document.getElementById("janela-dias");
    if (sel) sel.addEventListener("change", function () {
      estado.janela = parseInt(sel.value, 10);
      carregarRanking();
      carregarVolumeSpread();
    });

    tudo();
    /* Auto-refresh só da parte ao vivo. Recarregar os blocos históricos a
     * cada 5 min seria varrer o agregado inteiro para nada — o dado deles
     * só muda uma vez por dia, na rodada noturna. */
    setInterval(function () {
      carregarTape();
      carregarTermometro();
    }, 5 * 60 * 1000);
  });
})();
