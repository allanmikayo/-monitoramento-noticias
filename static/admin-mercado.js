/* Importação do export de Mercado Secundário (23/10/2026). O arquivo é lido
 * AQUI, no navegador, e vai em lotes: a função da Vercel aceita até 4,5 MB e
 * 10 s por chamada, e o export tem dezenas de milhares de linhas. */
(function () {
  const LOTE = 4000;
  const status = document.getElementById("ms-status");
  const prog = document.getElementById("ms-progresso");
  const btn = document.getElementById("ms-importar");
  const dataBR = (iso) => iso ? iso.slice(8, 10) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4) : "—";
  const n = (v) => Number(v || 0).toLocaleString("pt-BR");
  const norm = (t) => String(t == null ? "" : t).normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/\s+/g, " ").trim().toLowerCase();

  function carregarStatus() {
    fetch("/api/admin/mercado-secundario/status", { credentials: "same-origin" })
      .then((r) => r.ok ? r.json() : Promise.reject(r.status))
      .then((d) => {
        status.textContent = d.linhas
          ? `Base atual: ${n(d.linhas)} linhas, de ${dataBR(d.de)} a ${dataBR(d.ate)} (${n(d.pregoes)} pregões).`
          : "Base atual: vazia.";
      })
      .catch(() => { status.textContent = "Base atual: vazia (tabela ainda não criada)."; });
  }

  // Data -> AAAAMMDD numérico, para achar o dia mais antigo do arquivo.
  function chaveData(v) {
    if (v == null || v === "") return null;
    if (typeof v === "number") {
      if (v > 19000101) return Math.floor(v);
      const d = new Date(Date.UTC(1899, 11, 30) + Math.floor(v) * 86400000);
      return d.getUTCFullYear() * 10000 + (d.getUTCMonth() + 1) * 100 + d.getUTCDate();
    }
    const s = String(v).trim();
    let m = s.match(/^(\d{4})-?(\d{2})-?(\d{2})/);
    if (m) return Number(m[1] + m[2] + m[3]);
    m = s.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
    if (m) return Number(m[3] + m[2] + m[1]);
    return null;
  }

  async function importar() {
    const arq = document.getElementById("ms-arquivo").files[0];
    if (!arq) { prog.textContent = "Escolha um arquivo."; return; }
    btn.disabled = true;
    try {
      prog.textContent = "Lendo o arquivo…";
      const wb = XLSX.read(await arq.arrayBuffer(), { type: "array" });
      const linhas = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, raw: true, defval: null });
      const iCab = linhas.slice(0, 30).findIndex((l) => {
        const nomes = l.map(norm);
        return nomes.includes("ticker") && nomes.includes("volume");
      });
      if (iCab < 0) throw new Error('não achei o cabeçalho (uma linha com "ticker" e "volume").');
      const cab = linhas[iCab];
      let dados = linhas.slice(iCab + 1).filter((l) => l.some((c) => c != null && c !== ""));
      const truncado = linhas.slice(0, iCab).some((l) => l.some((c) => norm(c).includes("not all data was exported")));
      let aviso = "";
      if (truncado) {
        const iData = cab.map(norm).indexOf("data de ref. do negocio");
        const chaves = dados.map((l) => chaveData(l[iData])).filter((k) => k != null);
        const minimo = Math.min(...chaves);
        const antes = dados.length;
        dados = dados.filter((l) => chaveData(l[iData]) !== minimo);
        const s = String(minimo);
        aviso = ` O Excel cortou o export: o dia mais antigo (${s.slice(6, 8)}/${s.slice(4, 6)}/${s.slice(0, 4)}, ${n(antes - dados.length)} linhas) veio incompleto e foi descartado.`;
      }
      let gravadas = 0, descartadas = 0, de = null, ate = null;
      for (let k = 0; k < dados.length; k += LOTE) {
        prog.textContent = `Enviando ${n(Math.min(k + LOTE, dados.length))} de ${n(dados.length)} linhas…`;
        const r = await fetch("/api/admin/mercado-secundario/importar", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cabecalho: cab, linhas: dados.slice(k, k + LOTE), primeiro: k === 0 }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
        gravadas += d.gravadas; descartadas += d.descartadas;
        if (d.de && (!de || d.de < de)) de = d.de;
        if (d.ate && (!ate || d.ate > ate)) ate = d.ate;
      }
      prog.textContent = `Pronto: ${n(gravadas)} linhas gravadas, de ${dataBR(de)} a ${dataBR(ate)}` +
        (descartadas ? ` (${n(descartadas)} sem ticker, data ou volume ignoradas).` : ".") + aviso;
      carregarStatus();
    } catch (e) {
      prog.textContent = "Erro: " + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener("click", importar);
  carregarStatus();
})();
