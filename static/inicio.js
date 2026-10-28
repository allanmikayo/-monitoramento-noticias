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

  // ---- Agenda de resultados (28/10/2026, formato de relatório semanal) ----
  // A data vem da lista do time (importada aqui mesmo ou ajustada chip a
  // chip por admin); a CVM diz quem já divulgou. Duas colunas: esta semana
  // e a próxima. Cor do chip = tipo de cobertura (Local / Offshore / os dois).
  const ROT_STATUS = {
    divulgada: "Divulgou", agendada: "Agendada", pendente: "Data passou, sem entrega na CVM",
    sem_data: "Sem data",
  };
  const TIPOS = ["Local", "Offshore", "Offshore + Local"];
  const classeTipo = (t) => (t === "Local" ? "tp-local" : t === "Offshore" ? "tp-off" : t === "Offshore + Local" ? "tp-ambos" : "tp-nd");
  const HORA = { "após o fechamento": ["☾", "após o fechamento"], "antes da abertura": ["☀", "antes da abertura"] };
  const POR_DIA = 7;
  const deIso = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
  const isoDia = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const DIA_CURTO = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];
  let calDados = null;
  const calAdmin = document.getElementById("cal-card").dataset.admin === "sim";

  function chip(e) {
    const h = HORA[e.horario];
    const icone = e.status === "divulgada" ? '<span class="cal-ok" aria-hidden="true">✓</span>'
      : h ? `<span class="cal-hora" aria-hidden="true">${h[0]}</span>` : "";
    return `<button type="button" class="cal-chip ${classeTipo(e.tipo)} st-${e.status}" data-id="${e.company_id}">${icone}${esc(e.nome)}<span class="sr-only"> — ${ROT_STATUS[e.status]}${e.tipo ? " · " + esc(e.tipo) : ""}${h ? " · " + h[1] : ""}</span></button>`;
  }

  function linhaDia(iso, lista, hoje, prazo) {
    const dt = deIso(iso);
    const ordem = { divulgada: 0, pendente: 1, agendada: 2 };
    lista.sort((a, b) => (ordem[a.status] ?? 3) - (ordem[b.status] ?? 3) || a.nome.localeCompare(b.nome));
    const vis = lista.slice(0, POR_DIA).map(chip).join("");
    const resto = lista.length - POR_DIA;
    const mais = resto > 0 ? `<button type="button" class="cal-mais" data-dia="${iso}" title="${esc(lista.slice(POR_DIA).map((e) => e.nome).join(", "))}">+${resto}</button>` : "";
    const cls = iso === hoje ? "cal-hoje" : iso < hoje ? "cal-passado" : "";
    const marca = iso === prazo ? '<span class="cal-prazo" title="Prazo da CVM">prazo</span>' : "";
    return `<div class="cal-linha ${cls}"><span class="cal-ddia"><span class="cal-dsem">${iso === hoje ? "hoje" : DIA_CURTO[dt.getDay()]}</span><b>${dt.getDate()}</b>${marca}</span>
      <span class="cal-chips">${vis || '<span class="cal-vazio">—</span>'}${mais}</span></div>`;
  }

  function semana(titulo, [ini, fim], porDia, hoje, prazo) {
    const dias = [];
    for (let d = deIso(ini); isoDia(d) <= fim; d.setDate(d.getDate() + 1)) dias.push(isoDia(d));
    const n = dias.reduce((s, iso) => s + (porDia[iso] || []).length, 0);
    return `<div class="cal-bloco"><div class="cal-semana-tit">${titulo} <span class="muted">${dm(ini)}–${dm(fim)} · ${n} ${n === 1 ? "empresa" : "empresas"}</span></div>
      ${dias.map((iso) => linhaDia(iso, porDia[iso] || [], hoje, prazo)).join("")}</div>`;
  }

  function desenharCalendario(d) {
    calDados = d;
    const corpo = document.getElementById("cal-faixa");
    const t = d.temporada;
    if (!t) {
      corpo.innerHTML = '<span class="muted small">Agenda ainda sem dados.</span>';
      return;
    }
    const r = d.resumo, hoje = d.hoje, sem = d.semanas;
    const porDia = {};
    d.empresas.forEach((e) => { if (e.data) (porDia[e.data] = porDia[e.data] || []).push(e); });

    // Coluna de números: o "relatório da semana"
    const depois = d.empresas.filter((e) => e.data && e.data > sem.proxima[1] && e.status !== "divulgada");
    const ultima = depois.length ? depois.map((e) => e.data).sort().pop() : null;
    const kpi = (n, rot, sub, extra) => `<${extra ? `button type="button" class="cal-kpi cal-kpi-alerta" data-abrir="1" title="Ver na lista"` : 'div class="cal-kpi"'}><b>${n}</b><span>${rot}</span>${sub ? `<em>${sub}</em>` : ""}</${extra ? "button" : "div"}>`;
    const kpis = kpi(`${r.divulgada}<small>/${r.total}</small>`, "já divulgaram", "")
      + kpi(r.semana_total, "esta semana", r.semana_total ? `${r.semana_divulgadas} já divulgaram` : "")
      + kpi(r.proxima_total, "próxima semana", depois.length ? `+${depois.length} até ${dm(ultima)}` : "")
      + (r.sem_data ? kpi(r.sem_data, "sem data", "ver lista ›", true) : "")
      + (r.pendente ? kpi(r.pendente, "data passou", "sem entrega na CVM", true) : "");

    corpo.innerHTML = `<div class="cal-corpo"><div class="cal-kpis">${kpis}</div>
      ${semana("Esta semana", sem.atual, porDia, hoje, t.prazo)}${semana("Próxima semana", sem.proxima, porDia, hoje, t.prazo)}</div>`;

    const cvm = d.cvm_ate ? ` · CVM até ${dm(d.cvm_ate)}` : "";
    document.getElementById("cal-sub").textContent = `· ${t.rotulo} · prazo CVM ${dm(t.prazo)}${cvm}`;

    desenharLista(d);

    // Abertura: contador da temporada
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

    // Frase do dia
    const hojeL = d.empresas.filter((e) => e.data === hoje && e.status !== "divulgada");
    const nomes = (l) => l.slice(0, 3).map((e) => `<b>${esc(e.nome)}</b>`).join(", ") + (l.length > 3 ? ` e mais ${l.length - 3}` : "");
    if (hojeL.length) briefing("resultados", `Divulgam hoje: ${nomes(hojeL)}`);
    else if (r.proxima_total) briefing("resultados", `<b>${r.semana_total - r.semana_divulgadas}</b> resultados ainda esta semana e <b>${r.proxima_total}</b> na próxima`);
    else if (!t.encerrada) briefing("resultados", `<b>${r.divulgada}</b> de ${dentro} já divulgaram o ${t.rotulo}`);
  }

  // Lista completa (e, para admin, a importação da lista do time)
  function desenharLista(d) {
    const r = d.resumo;
    const ver = document.getElementById("cal-ver");
    ver.hidden = false;
    ver.dataset.total = r.total;
    ver.textContent = document.getElementById("cal-tabela").hidden ? `Ver as ${r.total} ▾` : "Fechar lista ▴";
    const ordem = { sem_data: 0, pendente: 1, agendada: 2, divulgada: 3 };
    const linhas = [...d.empresas].sort((a, b) => ordem[a.status] - ordem[b.status] || (a.data || "").localeCompare(b.data || "") || a.nome.localeCompare(b.nome));
    const tipo = (e) => (e.tipo ? `<span class="cal-tag ${classeTipo(e.tipo)}">${esc(e.tipo)}</span>` : "");
    const obs = (e) => [e.status_relatorio ? `<b>${esc(e.status_relatorio)}</b>` : "",
      e.link ? `<a href="${esc(e.link)}" target="_blank" rel="noopener">${esc(e.origem)}</a>` : esc(e.origem || ""),
      e.estimativa ? `histórico sugere ${dm(e.estimativa)}` : ""].filter(Boolean).join(" · ");
    const tabela = `<table class="ic-cal-tab"><thead><tr><th>Empresa</th><th>Cobertura</th><th>Setor</th><th>Data</th><th>Situação</th><th>Observação</th></tr></thead><tbody>${
      linhas.map((e) => `<tr id="cal-l-${e.company_id}"><td><b>${esc(e.nome)}</b></td><td>${tipo(e)}</td><td>${esc(e.setor || "")}</td>
        <td class="cal-td-data">${e.data ? dm(e.data) : "—"}${HORA[e.horario] ? ` <span title="${HORA[e.horario][1]}">${HORA[e.horario][0]}</span>` : ""}</td>
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
  card.addEventListener("click", (ev) => {
    const abrir = ev.target.closest("[data-abrir]");
    if (abrir) {
      const tab = document.getElementById("cal-tabela");
      tab.hidden = false;
      document.getElementById("cal-ver").textContent = "Fechar lista ▴";
      tab.scrollIntoView({ behavior: "smooth", block: "start" });
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

  document.getElementById("cal-ver").addEventListener("click", (ev) => {
    const tab = document.getElementById("cal-tabela");
    tab.hidden = !tab.hidden;
    ev.currentTarget.setAttribute("aria-expanded", String(!tab.hidden));
    ev.currentTarget.textContent = tab.hidden ? `Ver as ${ev.currentTarget.dataset.total} ▾` : "Fechar lista ▴";
  });

  buscar("/api/inicio/resultados").then(desenharCalendario).catch(() => {
    document.getElementById("cal-faixa").innerHTML = '<span class="muted small">Erro ao carregar a agenda.</span>';
  });

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
    const c0 = d.classes[0];
    if (c0 && c0.variacao_bps != null) {
      const v = c0.variacao_bps;
      briefing("spreads", Math.abs(v) < 0.05 ? "Spread IPCA+ estável na semana"
        : `Spread IPCA+ <b class="${v > 0 ? "h-abre" : "h-fecha"}">${v > 0 ? "abriu" : "fechou"} ${abs(v, 1)} bps</b> na semana`);
    }
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
    const rebaix = d.ratings.filter((a) => tipoRating(a.titulo) === "rebaixamento").length;
    if (d.ratings.length) briefing("ratings", `<b>${d.ratings.length}</b> ações de rating em 5 dias${rebaix ? `, ${rebaix} rebaixamento${rebaix > 1 ? "s" : ""}` : ""}`);
    document.getElementById("lista-ratings").innerHTML = d.ratings.length ? d.ratings.slice(0, 4).map((a) => {
      const t = tipoRating(a.titulo);
      const meta = `<span class="rt-pill rt-${t}">${TIPOS_RATING[t].simbolo} ${TIPOS_RATING[t].rotulo}</span>`
        + `${a.empresas[0] ? ` <b class="ic-meta-emp">${esc(a.empresas[0])}</b>` : ""} · ${esc(agencia(a.fonte))} · ${quando(a.quando)}`;
      return item(a.url, meta, a.titulo);
    }).join("") : '<span class="muted small">Nenhuma ação nos últimos 5 dias.</span>';

    document.getElementById("lista-noticias").innerHTML = d.noticias.length ? d.noticias.slice(0, 4).map((a) => {
      const tag = a.empresas[0] || a.setores[0] || "";
      return item(a.url, `${quando(a.quando)} · ${esc(a.fonte)}${tag ? ` · <b class="ic-meta-emp">${esc(tag)}</b>` : ""}`, a.titulo);
    }).join("") : '<span class="muted small">Nenhuma notícia na última semana.</span>';
  }).catch(() => {
    ["lista-ratings", "lista-noticias"].forEach((id) => { document.getElementById(id).innerHTML = '<span class="muted small">Erro ao carregar.</span>'; });
  });

  // ---- Relatórios ---------------------------------------------------------
  buscar("/api/inicio/relatorios").then((d) => {
    document.getElementById("lista-relatorios").innerHTML = d.relatorios.length ? d.relatorios.slice(0, 4).map((r) => {
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
