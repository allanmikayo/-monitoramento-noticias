/* Calendário de resultados (28/10/2026). Ver templates/calendario.html e
 * app/calendario.py. Mês tradicional, domingo a sábado; em cada dia, as
 * empresas da cobertura que divulgam. A data vem da agenda do time (lista
 * importada na lista completa, ou ajuste chip a chip por admin); a CVM diz
 * quem já divulgou. */
(function () {
  const MESES_LONGOS = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto",
    "Setembro", "Outubro", "Novembro", "Dezembro"];
  const dm = (iso) => (iso ? iso.slice(8, 10) + "/" + iso.slice(5, 7) : "—");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function buscar(url) {
    return fetch(url, { credentials: "same-origin" }).then((r) => {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  const ROT_STATUS = {
    divulgada: "Divulgou", agendada: "Agendada", pendente: "Data passou, sem entrega na CVM",
    sem_data: "Sem data",
  };
  const TIPOS = ["Local", "Offshore", "Offshore + Local"];
  const classeTipo = (t) => (t === "Local" ? "tp-local" : t === "Offshore" ? "tp-off" : t === "Offshore + Local" ? "tp-ambos" : "tp-nd");
  const HORA = { "após o fechamento": ["hr-apos", "após o fechamento"], "antes da abertura": ["hr-antes", "antes da abertura"] };
  const deIso = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
  const isoDia = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  let calDados = null;
  let mesVisto = null;   // dia 1 do mês na tela
  const calAdmin = document.getElementById("cal-card").dataset.admin === "sim";

  function chip(e) {
    const h = HORA[e.horario];
    const marca = e.status === "divulgada" ? '<span class="cal-ok" aria-hidden="true">✓</span>'
      : h ? `<i class="cal-hr ${h[0]}" aria-hidden="true"></i>` : "";
    return `<button type="button" class="cal-chip ${classeTipo(e.tipo)} st-${e.status}" data-id="${e.company_id}">${marca}${esc(e.nome)}<span class="sr-only"> — ${ROT_STATUS[e.status]}${e.tipo ? " · " + esc(e.tipo) : ""}${h ? " · " + h[1] : ""}</span></button>`;
  }

  // Grade do mês: do domingo da semana do dia 1 ao sábado da semana do
  // último dia. Dias de fora do mês aparecem apagados.
  function desenharMes() {
    const d = calDados;
    const grade = document.getElementById("cal-faixa");
    const hoje = d.hoje, prazo = d.temporada && d.temporada.prazo;
    const porDia = {};
    d.empresas.forEach((e) => { if (e.data) (porDia[e.data] = porDia[e.data] || []).push(e); });
    const ordem = { divulgada: 0, pendente: 1, agendada: 2 };
    const ano = mesVisto.getFullYear(), mes = mesVisto.getMonth();
    document.getElementById("cal-mes").textContent = `${MESES_LONGOS[mes]} de ${ano}`;
    const ini = new Date(ano, mes, 1); ini.setDate(1 - ini.getDay());
    const fim = new Date(ano, mes + 1, 0); fim.setDate(fim.getDate() + (6 - fim.getDay()));
    let n = 0;
    let html = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]
      .map((x, i) => `<div class="cm-cab${i === 0 || i === 6 ? " cm-fds" : ""}">${x}</div>`).join("");
    for (const x = new Date(ini); x <= fim; x.setDate(x.getDate() + 1)) {
      const iso = isoDia(x);
      const lista = (porDia[iso] || []).sort((a, b) => (ordem[a.status] ?? 3) - (ordem[b.status] ?? 3) || a.nome.localeCompare(b.nome));
      if (x.getMonth() === mes) n += lista.length;
      const cls = ["cm-dia", x.getMonth() !== mes ? "cm-fora" : "", x.getDay() === 0 || x.getDay() === 6 ? "cm-fds" : "",
        iso === hoje ? "cm-hoje" : "", iso < hoje ? "cm-passado" : ""].filter(Boolean).join(" ");
      const marcaPrazo = iso === prazo ? '<span class="cal-prazo" title="Prazo da CVM">prazo CVM</span>' : "";
      html += `<div class="${cls}"><div class="cm-num"><b>${x.getDate()}</b>${marcaPrazo}${lista.length ? `<span class="cm-n">${lista.length}</span>` : ""}</div>
        <div class="cm-chips">${lista.map(chip).join("")}</div></div>`;
    }
    grade.innerHTML = html;
    document.getElementById("cal-mes-n").textContent = n ? `${n} ${n === 1 ? "divulgação" : "divulgações"} no mês` : "nenhuma divulgação no mês";
  }

  function desenharCalendario(d) {
    calDados = d;
    const t = d.temporada;
    if (!t) {
      document.getElementById("cal-faixa").innerHTML = '<span class="muted small">Calendário ainda sem dados.</span>';
      return;
    }
    if (!mesVisto) { const h = deIso(d.hoje); mesVisto = new Date(h.getFullYear(), h.getMonth(), 1); }
    desenharMes();
    const r = d.resumo;
    const sd = document.getElementById("cal-semdata");
    sd.hidden = !r.sem_data;
    sd.innerHTML = `<b>${r.sem_data}</b> sem data`;
    document.getElementById("cal-temporada").textContent =
      `${t.rotulo} · ${r.divulgada} de ${r.total} já divulgaram · prazo CVM ${dm(t.prazo)}`;
    desenharLista(d);
  }

  function mudarMes(delta) {
    if (!calDados) return;
    if (delta === 0) { const h = deIso(calDados.hoje); mesVisto = new Date(h.getFullYear(), h.getMonth(), 1); }
    else mesVisto = new Date(mesVisto.getFullYear(), mesVisto.getMonth() + delta, 1);
    desenharMes();
  }
  document.getElementById("cal-ant").addEventListener("click", () => mudarMes(-1));
  document.getElementById("cal-prox").addEventListener("click", () => mudarMes(1));
  document.getElementById("cal-hoje-btn").addEventListener("click", () => mudarMes(0));

  // Lista completa (e, para admin, a importação da lista do time)
  function desenharLista(d) {
    const r = d.resumo;
    const ver = document.getElementById("cal-ver");
    ver.hidden = false;
    ver.dataset.total = r.total;
    ver.textContent = document.getElementById("cal-tabela").hidden ? `Ver as ${r.total}` : "Voltar ao calendário";
    const ordem = { sem_data: 0, pendente: 1, agendada: 2, divulgada: 3 };
    const linhas = [...d.empresas].sort((a, b) => ordem[a.status] - ordem[b.status] || (a.data || "").localeCompare(b.data || "") || a.nome.localeCompare(b.nome));
    const tipo = (e) => (e.tipo ? `<span class="cal-tag ${classeTipo(e.tipo)}">${esc(e.tipo)}</span>` : "");
    const obs = (e) => [e.status_relatorio ? `<b>${esc(e.status_relatorio)}</b>` : "",
      e.link ? `<a href="${esc(e.link)}" target="_blank" rel="noopener">${esc(e.origem)}</a>` : esc(e.origem || ""),
      e.estimativa ? `histórico sugere ${dm(e.estimativa)}` : ""].filter(Boolean).join(" · ");
    const tabela = `<table class="ic-cal-tab"><thead><tr><th>Empresa</th><th>Cobertura</th><th>Setor</th><th>Data</th><th>Situação</th><th>Observação</th></tr></thead><tbody>${
      linhas.map((e) => `<tr id="cal-l-${e.company_id}"><td><b>${esc(e.nome)}</b></td><td>${tipo(e)}</td><td>${esc(e.setor || "")}</td>
        <td class="cal-td-data">${e.data ? dm(e.data) : "—"}${HORA[e.horario] ? ` <i class="cal-hr ${HORA[e.horario][0]}" title="${HORA[e.horario][1]}"></i>` : ""}</td>
        <td><span class="cal-pill st-${e.status}">${ROT_STATUS[e.status]}</span></td><td class="muted">${obs(e)}</td></tr>`).join("")}</tbody></table>`;
    document.getElementById("cal-tabela").innerHTML = (calAdmin ? painelImportacao(d.importacao) : "") + tabela;
  }

  function painelImportacao(rel) {
    const lst = (rot, l) => (l && l.length ? `<div><b>${rot} (${l.length}):</b> ${l.map(esc).join(", ")}</div>` : "");
    const relatorio = rel ? `<div class="cal-rel"><div class="muted">Última importação: ${esc(rel.temporada)} · ${rel.com_data} com data de ${rel.linhas_lidas} linhas · ${esc(rel.por || "")}</div>
        ${lst("Nomes que não casaram com a cobertura", rel.nao_reconhecidas)}
        ${lst("Sem data, mas entregam ITR/DFP na CVM", rel.sem_data_entrega_cvm)}
        ${lst("Sem data, sem registro na CVM", rel.sem_data_fora_cvm)}
        ${lst("Já divulgaram e não estão na lista", rel.ja_divulgaram_fora_da_lista)}</div>` : "";
    return `<details class="cal-import"><summary>Atualizar agenda (colar a lista revisada)</summary>
      <form id="cal-import-form"><textarea name="texto" rows="6" placeholder="Empresa  Data  Horário  Cobertura … (cole direto do Excel; a lista substitui a agenda da temporada)"></textarea>
      <span class="pop-botoes"><button type="submit" class="btn-primary btn-small">Importar</button><span class="pop-erro" hidden></span></span></form></details>${relatorio}`;
  }

  document.getElementById("cal-tabela").addEventListener("submit", (ev) => {
    if (ev.target.id !== "cal-import-form") return;
    ev.preventDefault();
    const form = ev.target, er = form.querySelector(".pop-erro");
    const texto = new FormData(form).get("texto");
    if (!texto.trim()) return;
    form.querySelector("button").disabled = true;
    fetch("/api/inicio/resultados/importar", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto }),
    }).then((r) => r.json().then((j) => { if (!r.ok) throw new Error(j.detail || "HTTP " + r.status); return j; }))
      .then(() => buscar("/api/inicio/resultados"))
      .then((d) => desenharCalendario(d))
      .catch((x) => { er.hidden = false; er.textContent = x.message; form.querySelector("button").disabled = false; });
  });

  // Dica (hover) e ajuste de data (clique, só admin)
  const pop = document.getElementById("cal-pop");
  const card = document.getElementById("cal-card");
  let popFixo = false;
  function posicionar(alvo) {
    const r = card.getBoundingClientRect(), a = alvo.getBoundingClientRect();
    pop.hidden = false;
    let x = a.left - r.left, y = a.bottom - r.top + 6;
    if (x + pop.offsetWidth > r.width - 8) x = r.width - pop.offsetWidth - 8;
    if (y + pop.offsetHeight > r.height && a.top - r.top - pop.offsetHeight - 6 > 0) y = a.top - r.top - pop.offsetHeight - 6;
    pop.style.left = Math.max(8, x) + "px"; pop.style.top = y + "px";
  }
  function htmlDica(e) {
    const ent = e.entidades && e.entidades.length > 1 ? `<div class="muted">${e.entidades.map(esc).join(" · ")}</div>` : "";
    return `<div class="pop-tit">${esc(e.nome)} ${e.tipo ? `<span class="cal-tag ${classeTipo(e.tipo)}">${esc(e.tipo)}</span>` : ""}</div>
      <div><span class="cal-pill st-${e.status}">${ROT_STATUS[e.status]}</span>
      ${e.data ? `<b>${dm(e.data)}</b>` : ""}${e.horario ? " · " + esc(e.horario) : ""}</div>
      ${e.status_relatorio ? `<div>Relatório: <b>${esc(e.status_relatorio)}</b></div>` : ""}
      <div class="muted">${esc(e.origem || "")}${e.estimativa ? ` · histórico sugere ${dm(e.estimativa)}` : ""}</div>${ent}`;
  }
  const achar = (id) => calDados && calDados.empresas.find((e) => String(e.company_id) === String(id));

  card.addEventListener("mouseover", (ev) => {
    const b = ev.target.closest(".cal-chip");
    if (!b || popFixo) return;
    const e = achar(b.dataset.id);
    if (!e) return;
    pop.innerHTML = htmlDica(e) + (calAdmin ? '<div class="pop-acao">Clique para ajustar data ou cobertura</div>'
      : e.status === "divulgada" && e.link ? '<div class="pop-acao">Clique para abrir o documento</div>' : "");
    posicionar(b);
  });
  card.addEventListener("mouseout", (ev) => {
    if (!popFixo && ev.target.closest(".cal-chip") && !ev.relatedTarget?.closest?.(".cal-chip")) pop.hidden = true;
  });
  function mostrarLista(sim) {
    document.getElementById("cal-tabela").hidden = !sim;
    document.getElementById("cal-faixa").hidden = sim;
    const ver = document.getElementById("cal-ver");
    ver.setAttribute("aria-expanded", String(sim));
    ver.textContent = sim ? "Voltar ao calendário" : `Ver as ${ver.dataset.total || ""}`;
  }
  card.addEventListener("click", (ev) => {
    if (ev.target.closest("#cal-semdata")) {
      mostrarLista(true);
      const prim = document.querySelector("#cal-tabela .cal-pill.st-sem_data");
      if (prim) prim.closest("tr").scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const b = ev.target.closest(".cal-chip");
    if (!b) return;
    const e = achar(b.dataset.id);
    if (!e) return;
    if (!calAdmin) { if (e.status === "divulgada" && e.link) window.open(e.link, "_blank", "noopener"); return; }
    popFixo = true;
    const opcoes = ["<option value=\"\">—</option>"].concat(TIPOS.map((t) => `<option${t === e.tipo ? " selected" : ""}>${t}</option>`)).join("");
    const horas = [["", "a confirmar"], ["após o fechamento", "após o fechamento"], ["antes da abertura", "antes da abertura"]]
      .map(([v, rot]) => `<option value="${v}"${(e.horario || "") === v ? " selected" : ""}>${rot}</option>`).join("");
    pop.innerHTML = htmlDica(e) + `<form class="pop-form">
        <label>Data <input type="date" name="data" value="${e.data_agenda || ""}"></label>
        <label>Horário <select name="horario">${horas}</select></label>
        <label>Cobertura <select name="tipo">${opcoes}</select></label>
        <span class="pop-erro" hidden></span>
        <span class="pop-botoes"><button type="submit" class="btn-primary btn-small">Salvar</button>
        ${e.data_agenda ? '<button type="button" class="btn-small" data-acao="remover">Remover</button>' : ""}
        ${e.link ? `<a class="btn-small" href="${esc(e.link)}" target="_blank" rel="noopener">Documento</a>` : ""}
        <button type="button" class="btn-small" data-acao="fechar">Cancelar</button></span></form>`;
    posicionar(b);
    const form = pop.querySelector("form");
    const enviar = (corpo) => fetch("/api/inicio/resultados/confirmar", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company_id: e.company_id, ...corpo }),
    }).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return buscar("/api/inicio/resultados"); })
      .then((d) => { popFixo = false; pop.hidden = true; desenharCalendario(d); })
      .catch(() => { const er = pop.querySelector(".pop-erro"); er.hidden = false; er.textContent = "Não foi possível salvar."; });
    form.addEventListener("submit", (s) => {
      s.preventDefault();
      const f = new FormData(form);
      enviar({ data: f.get("data") || null, horario: f.get("horario") || null, tipo: f.get("tipo") || null });
    });
    form.addEventListener("click", (c) => {
      const acao = c.target.dataset.acao;
      if (acao === "fechar") { popFixo = false; pop.hidden = true; }
      if (acao === "remover") enviar({ data: null, horario: null });
    });
  });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { popFixo = false; pop.hidden = true; } });

  document.getElementById("cal-ver").addEventListener("click", () => {
    mostrarLista(document.getElementById("cal-tabela").hidden);
  });

  buscar("/api/inicio/resultados").then(desenharCalendario).catch(() => {
    document.getElementById("cal-faixa").innerHTML = '<span class="muted small">Erro ao carregar o calendário.</span>';
  });
})();

