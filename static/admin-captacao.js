/* Importação dos exports de captação líquida (29/10/2026). O CSV é lido
 * AQUI, no navegador, e vai em lotes: a função da Vercel aceita até 4,5 MB
 * e 10 s por chamada, e o diário tem mais de 100 mil linhas. O primeiro
 * lote leva o período do arquivo inteiro, que o servidor apaga antes de
 * gravar (o export é uma extração completa). O nome da gestora vira hash
 * no servidor e não é gravado (ver app/captacao.py). */
(function () {
  const LOTE = 4000;
  const status = document.getElementById("cl-status");
  const prog = document.getElementById("cl-progresso");
  const btn = document.getElementById("cl-importar");
  const n = (v) => Number(v || 0).toLocaleString("pt-BR");
  const dataBR = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : "—");

  function carregarStatus() {
    fetch("/api/admin/captacao/status", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => {
        const t = (x, nome) => (x.linhas ? `${nome}: ${n(x.linhas)} linhas, de ${dataBR(x.de)} a ${dataBR(x.ate)}` : `${nome}: vazio`);
        status.textContent = "Base atual — " + t(d.diario, "diário") + "; " + t(d.mensal, "mensal") + ".";
      })
      .catch(() => { status.textContent = "Base atual: vazia."; });
  }

  /* CSV simples: separador vírgula ou ponto e vírgula, campos entre aspas
     opcionais, aspas duplicadas dentro do campo. */
  function lerCsv(texto) {
    const primeira = texto.slice(0, texto.indexOf("\n"));
    const sep = (primeira.match(/;/g) || []).length > (primeira.match(/,/g) || []).length ? ";" : ",";
    const linhas = [];
    let campo = "", linha = [], aspas = false;
    for (let i = 0; i < texto.length; i++) {
      const c = texto[i];
      if (aspas) {
        if (c === '"') {
          if (texto[i + 1] === '"') { campo += '"'; i++; } else aspas = false;
        } else campo += c;
      } else if (c === '"') aspas = true;
      else if (c === sep) { linha.push(campo); campo = ""; }
      else if (c === "\n") { linha.push(campo); linhas.push(linha); linha = []; campo = ""; }
      else if (c !== "\r") campo += c;
    }
    if (campo || linha.length) { linha.push(campo); linhas.push(linha); }
    return linhas.filter((l) => l.some((x) => x !== ""));
  }

  async function importarArquivo(arq) {
    let texto = await arq.text();
    if (texto.charCodeAt(0) === 0xfeff) texto = texto.slice(1);
    const todas = lerCsv(texto);
    const cab = todas[0].map((c) => c.trim());
    const dados = todas.slice(1);
    const iData = cab.findIndex((c) => c === "data_anls" || c === "mes_ref");
    if (iData < 0) throw new Error(`${arq.name}: não achei a coluna data_anls (diário) nem mes_ref (mensal).`);
    const datas = dados.map((l) => String(l[iData] || "").slice(0, 10)).filter((x) => /^\d{4}-\d{2}-\d{2}$/.test(x)).sort();
    if (!datas.length) throw new Error(`${arq.name}: nenhuma data válida.`);
    const periodo = [datas[0], datas[datas.length - 1]];
    let gravadas = 0, descartadas = 0, tipo = "";
    for (let k = 0; k < dados.length; k += LOTE) {
      prog.textContent = `${arq.name}: enviando ${n(Math.min(k + LOTE, dados.length))} de ${n(dados.length)} linhas…`;
      const corpo = { cabecalho: cab, linhas: dados.slice(k, k + LOTE) };
      if (k === 0) corpo.periodo = periodo;
      const r = await fetch("/api/admin/captacao/importar", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(`${arq.name}: ${d.detail || "HTTP " + r.status}`);
      gravadas += d.gravadas; descartadas += d.descartadas; tipo = d.tipo;
    }
    return `${tipo === "diario" ? "Diário" : "Mensal"}: ${n(gravadas)} linhas, de ${dataBR(periodo[0])} a ${dataBR(periodo[1])}`
      + (descartadas ? ` (${n(descartadas)} ignoradas)` : "");
  }

  async function importar() {
    const arquivos = Array.from(document.getElementById("cl-arquivo").files);
    if (!arquivos.length) { prog.textContent = "Escolha o(s) arquivo(s)."; return; }
    btn.disabled = true;
    const feitos = [];
    try {
      for (const arq of arquivos) feitos.push(await importarArquivo(arq));
      prog.textContent = "Pronto. " + feitos.join(". ") + ".";
      carregarStatus();
    } catch (e) {
      prog.textContent = (feitos.length ? feitos.join(". ") + ". " : "") + "Erro: " + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener("click", importar);
  carregarStatus();
})();
