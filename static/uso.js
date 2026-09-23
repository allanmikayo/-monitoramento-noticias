/* Registro de uso do Hub (22/09/2026) -- ver app/uso.py.
 *
 * Carregado em TODAS as páginas pelo base.html, antes dos scripts delas.
 * Expõe duas funções que as páginas chamam nos cliques/filtros:
 *   registrarUso(acao, alvo)   -- ex.: registrarUso("empresa", "Sabesp")
 *   registrarBusca(texto)      -- espera a pessoa parar de digitar
 * A visita à aba é registrada sozinha, aqui mesmo.
 *
 * Nunca pode atrapalhar a página: tudo em try/catch, erro de rede ignorado,
 * e nada é aguardado.
 */
(function () {
  var ABAS = {
    "/cobertura": "Repositório", "/": "Notícias", "/spreads": "Spreads",
    "/balcao": "Balcão B3", "/primario": "Mercado Primário", "/fontes": "Fontes & Empresas",
    "/admin": "Administração", "/minha-conta": "Minha conta",
  };
  var aba = ABAS[location.pathname];

  // Id aleatório do navegador, só para contar visitantes distintos no
  // Repositório (que é aberto, sem login). Não identifica ninguém.
  var visitante = "";
  try {
    visitante = localStorage.getItem("hub_vid") || "";
    if (!visitante) {
      visitante = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
        : String(Math.random()).slice(2) + Date.now();
      localStorage.setItem("hub_vid", visitante);
    }
  } catch (e) { visitante = ""; }

  var recentes = {};
  function enviar(acao, alvo) {
    if (!aba) return;
    alvo = alvo == null ? "" : String(alvo).trim();
    if (acao !== "visita" && !alvo) return;
    // O mesmo clique repetido em sequência (abre/fecha/abre) conta uma vez.
    var chave = acao + "|" + alvo, agora = Date.now();
    if (recentes[chave] && agora - recentes[chave] < 5000) return;
    recentes[chave] = agora;
    try {
      fetch("/api/uso", {
        method: "POST", keepalive: true, credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Hub-Uso": "1" },
        body: JSON.stringify({ aba: aba, acao: acao, alvo: alvo, visitante: visitante }),
      }).catch(function () {});
    } catch (e) { /* nada */ }
  }

  var esperas = {};
  window.registrarUso = function (acao, alvo) { enviar(acao, alvo); };
  window.registrarBusca = function (texto, campo) {
    campo = campo || "busca";
    clearTimeout(esperas[campo]);
    texto = (texto || "").trim().toLowerCase();
    if (texto.length < 3) return;
    esperas[campo] = setTimeout(function () { enviar("busca", texto); }, 1500);
  };

  enviar("visita", "");
})();
