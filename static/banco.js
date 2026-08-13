/* Aba "Banco de Dados" (12/08/2026) — pré-visualização de tabela e
   consulta SQL somente leitura.

   Sem framework, igual ao resto do projeto (app.js e spreads.js seguem o
   mesmo padrão de fetch + montagem manual de DOM).

   NOTA DE SEGURANÇA: toda célula vinda do banco é inserida com
   `textContent`, nunca `innerHTML`. O conteúdo é dado de mercado e
   título de notícia — texto que o usuário não escreveu, mas que veio de
   fonte externa e não deve ser interpretado como HTML. */
(function () {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const fmtInt = (n) => (n ?? 0).toLocaleString("pt-BR");

  /* ---------------- Pré-visualização de tabela ---------------- */

  const estado = { tabela: null, coluna: null, pagina: 1, porPagina: 50, total: 0 };

  function montarTabela(tabelaEl, colunas, linhas) {
    const thead = tabelaEl.querySelector("thead tr");
    const tbody = tabelaEl.querySelector("tbody");
    thead.textContent = "";
    tbody.textContent = "";

    colunas.forEach((c) => {
      const th = document.createElement("th");
      th.textContent = c;
      thead.appendChild(th);
    });

    linhas.forEach((linha) => {
      const tr = document.createElement("tr");
      linha.forEach((v) => {
        const td = document.createElement("td");
        if (v === null || v === undefined) {
          td.textContent = "—";
          td.className = "muted";
        } else if (typeof v === "number") {
          // Número no padrão BR e alinhado à direita — o resto do app faz
          // igual, e alinhar número à esquerda dificulta comparar ordem
          // de grandeza ao correr o olho pela coluna.
          td.textContent = Number.isInteger(v) ? fmtInt(v) : v.toLocaleString("pt-BR", {
            maximumFractionDigits: 6,
          });
          td.className = "num";
        } else {
          td.textContent = String(v);
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function paramsPeriodo() {
    const de = $("#preview-de").value;
    const ate = $("#preview-ate").value;
    const p = new URLSearchParams({ tabela: estado.tabela });
    if (de) p.set("de", de);
    if (ate) p.set("ate", ate);
    return p;
  }

  async function carregarPreview() {
    const p = paramsPeriodo();
    p.set("pagina", estado.pagina);
    p.set("por_pagina", estado.porPagina);

    const resp = await fetch("/api/banco/tabela?" + p.toString());
    if (!resp.ok) {
      $("#preview-sub").textContent = "Erro ao carregar: " + (await resp.text());
      return;
    }
    const d = await resp.json();
    estado.total = d.total;

    const primeira = (estado.pagina - 1) * estado.porPagina + 1;
    const ultima = Math.min(estado.pagina * estado.porPagina, d.total);
    $("#preview-sub").textContent =
      d.total === 0
        ? "Nenhuma linha no recorte selecionado"
        : `${fmtInt(primeira)}–${fmtInt(ultima)} de ${fmtInt(d.total)} linhas`;

    montarTabela($("#tabela-preview"), d.colunas, d.linhas);
    $("#btn-anterior").disabled = estado.pagina <= 1;
    $("#btn-proxima").disabled = ultima >= d.total;

    const exp = paramsPeriodo();
    $("#btn-export-recorte").href = "/api/banco/export?" + exp.toString();
  }

  document.querySelectorAll("#tabela-inventario tbody tr").forEach((tr) => {
    tr.addEventListener("click", (ev) => {
      // Não sequestra o clique nos botões da própria linha.
      if (ev.target.closest("a") || ev.target.closest(".btn-small:not(.btn-ver)")) return;
      estado.tabela = tr.dataset.tabela;
      estado.coluna = tr.dataset.coluna || null;
      estado.pagina = 1;
      $("#preview-titulo").textContent = estado.tabela;
      $("#preview-wrap").style.display = "";
      // Tabela de cadastro não tem coluna de data — esconder o filtro é
      // melhor que deixá-lo visível sem efeito.
      $("#filtro-periodo").style.display = estado.coluna ? "" : "none";
      $("#preview-de").value = "";
      $("#preview-ate").value = "";
      carregarPreview();
      $("#preview-wrap").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  $("#btn-fechar-preview").addEventListener("click", () => {
    $("#preview-wrap").style.display = "none";
  });
  $("#btn-aplicar-periodo").addEventListener("click", () => {
    estado.pagina = 1;
    carregarPreview();
  });
  $("#btn-anterior").addEventListener("click", () => {
    if (estado.pagina > 1) {
      estado.pagina -= 1;
      carregarPreview();
    }
  });
  $("#btn-proxima").addEventListener("click", () => {
    estado.pagina += 1;
    carregarPreview();
  });

  /* ---------------- Consulta SQL ---------------- */

  async function rodarSQL() {
    const sql = $("#sql-input").value;
    const limite = parseInt($("#sql-limite").value, 10) || 500;
    const status = $("#sql-status");
    status.textContent = "Executando…";
    status.className = "muted small";

    const resp = await fetch("/api/banco/consulta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql, limite }),
    });

    if (!resp.ok) {
      let msg = await resp.text();
      try {
        msg = JSON.parse(msg).detail || msg;
      } catch (e) {
        /* corpo não-JSON: mostra cru mesmo */
      }
      // A mensagem de erro do banco é o que permite corrigir a consulta,
      // e quem está nesta tela já é admin.
      status.textContent = msg;
      status.className = "small";
      status.style.color = "var(--off)";
      montarTabela($("#tabela-sql"), [], []);
      return;
    }

    const d = await resp.json();
    status.style.color = "";
    status.className = "muted small";
    status.textContent =
      d.n === 0
        ? "A consulta não retornou linhas."
        : `${fmtInt(d.n)} linha(s)` +
          (d.n >= limite ? ` — limite de ${fmtInt(limite)} atingido, pode haver mais` : "");
    montarTabela($("#tabela-sql"), d.colunas, d.linhas);
    $("#btn-export-sql").href =
      "/api/banco/export?sql=" + encodeURIComponent(sql);
  }

  $("#btn-rodar").addEventListener("click", rodarSQL);

  // Ctrl/Cmd+Enter executa — atalho universal de cliente SQL.
  $("#sql-input").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      rodarSQL();
    }
  });
})();
