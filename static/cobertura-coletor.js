/* Coletor do Repositório de Relatórios — roda DENTRO da página do Smart,
 * acionado pelo bookmarklet (ver /cobertura/bookmarklet para instalar).
 *
 * Por que um bookmarklet e não um job do GitHub Actions, como as notícias:
 * o Smart exige sessão autenticada (SSO + MFA) e a API
 * proxy-api.cloud.itau.com.br/research/v1/reports recusa requisição sem
 * header de autorização — testado em 13/08/2026 abrindo o endpoint direto
 * no navegador já logado, deu erro. Um runner do Actions bateria na mesma
 * porta, e guardar credencial pessoal num secret está fora de cogitação.
 * Então a coleta roda onde a sessão já existe: no navegador do Allan.
 *
 * O que ele faz: varre as N primeiras páginas da listagem de Fixed Income,
 * abre cada relatório num iframe pra ler o Resumo (é lá que os relatórios
 * multi-empresa tipo "Resultados 2T26 - Parte 1" citam as 20 empresas
 * nominalmente — sem isso o especialista que busca pelo nome não acha) e
 * manda tudo pro endpoint de ingestão.
 */
(function () {
  'use strict';

  var CFG = window.__COBERTURA_CFG || {};
  var API = CFG.api;                       // https://<seu-app>/api/cobertura/ingest
  var TOKEN = CFG.token;                   // COBERTURA_INGEST_TOKEN
  var PAGINAS = CFG.paginas || 2;          // 2 páginas = 60 relatórios, folgado pra 3x/dia
  var LISTA = 'https://www.itau.com.br/itaubba-pt/portal/credit?tab=reports';

  if (location.hostname.indexOf('itau.com.br') === -1) {
    // Caso mais comum (13/08/2026, aconteceu com o Allan na primeira vez):
    // clicou no link da página de instalação em vez de ARRASTAR pra barra de
    // favoritos. Aí o script roda na própria página do Hub, que não é o
    // Smart. A mensagem antiga ("Abra o Smart primeiro") não ajudava, porque
    // o Smart já estava aberto em outra aba -- o problema era outro.
    alert(
      'Este botão precisa ser clicado DENTRO do Smart, não aqui.\n\n' +
      'Se você acabou de clicar no link da página de instalação: ele serve para ser ' +
      'ARRASTADO até a barra de favoritos (Ctrl+Shift+B), não clicado.\n\n' +
      'Depois de arrastar, vá até a aba do Smart em Fixed Income → Relatórios e ' +
      'clique no favorito que apareceu na barra.\n\n' + LISTA
    );
    return;
  }
  if (!API || !TOKEN) {
    alert('Bookmarklet sem configuração. Reinstale a partir da página /cobertura/bookmarklet.');
    return;
  }

  /* ---------- painel de progresso ---------- */
  var box = document.createElement('div');
  box.style.cssText = 'position:fixed;top:16px;right:16px;z-index:2147483647;background:#1A1A1A;' +
    'color:#fff;font:13px -apple-system,Segoe UI,Arial;padding:14px 16px;border-radius:10px;' +
    'box-shadow:0 8px 30px rgba(0,0,0,.4);min-width:260px';
  box.innerHTML = '<b style="color:#EC7000">Repositório de Relatórios</b><div id="__cb_msg" style="margin-top:6px">iniciando…</div>';
  document.body.appendChild(box);
  var msg = function (t) { document.getElementById('__cb_msg').innerHTML = t; };

  /* ---------- utilidades ---------- *
   * sleep via MessageChannel em vez de setTimeout: com a aba em segundo
   * plano o Chrome estrangula timers (chega a 1 disparo/minuto após alguns
   * minutos — "intensive throttling"), o que fazia a coleta travar. Isso
   * consome CPU, então só é usado durante a coleta.
   */
  function yieldMacro() {
    return new Promise(function (r) {
      var c = new MessageChannel(); c.port1.onmessage = function () { r(); }; c.port2.postMessage(0);
    });
  }
  async function sleep(ms) { var t = Date.now() + ms; while (Date.now() < t) await yieldMacro(); }
  function norm(s) {
    return (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '')
      .toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }

  /* ---------- leitura da listagem ---------- */
  function extrair() {
    var vistos = {}, out = [];
    document.querySelectorAll('a[href*="credit/report/"]').forEach(function (a) {
      var id = a.getAttribute('href').split('/').pop();
      if (vistos[id]) return; vistos[id] = 1;
      var row = a.closest('.gco-card-list__item'); if (!row) return;
      var q = function (sel) { var e = row.querySelector(sel); return e ? e.innerText.trim().replace(/\s+/g, ' ') : null; };
      var an = row.querySelector('a[href*="credit/analyst/"]');
      var inst = row.querySelector('a[href*="credit/instrument/"]');
      out.push({
        id: id,
        titulo: q('p.ids-body-text'),
        categoria: q('span.ids-label.-xsmall'),
        tipo_investimento: inst ? inst.innerText.trim() : null,
        dataBr: q('span.ids-label.-small'),
        analista: an ? an.innerText.trim() : null
      });
    });
    return out;
  }

  var MES = { jan: '01', fev: '02', mar: '03', abr: '04', mai: '05', jun: '06',
              jul: '07', ago: '08', set: '09', out: '10', nov: '11', dez: '12' };
  function iso(br) {
    var m = (br || '').match(/(\d{1,2})\s+(\w{3})\w*,?\s*(\d{4})/);
    if (!m) return null;
    var mm = MES[m[2].toLowerCase()]; if (!mm) return null;
    return m[3] + '-' + mm + '-' + ('0' + m[1]).slice(-2);
  }

  /* ---------- leitura do resumo, via iframe ---------- *
   * Valida que o texto carregado contém o título do relatório pedido. Sem
   * essa checagem o iframe às vezes devolve o conteúdo AINDA do relatório
   * anterior (o src troca antes do Angular repintar) e as tags de um
   * relatório vazam para dezenas de outros — aconteceu de verdade em
   * 12/08/2026, contaminou a base inteira com uma empresa só.
   */
  var frame = document.createElement('iframe');
  frame.style.cssText = 'position:fixed;bottom:0;right:0;width:600px;height:400px;opacity:.01;z-index:-1';
  document.body.appendChild(frame);

  async function lerResumo(id, titulo) {
    var chave = norm(titulo).slice(0, 45), t0, txt = '', prev = '', estavel = 0;
    frame.src = 'about:blank';
    t0 = Date.now();
    while (Date.now() - t0 < 6000) {
      await sleep(150);
      try { var d = frame.contentDocument;
        if (d && d.location.href === 'about:blank' && (!d.body || d.body.innerText.trim() === '')) break;
      } catch (e) {}
    }
    frame.src = 'https://www.itau.com.br/itaubba-pt/portal/credit/report/' + id;
    t0 = Date.now();
    while (Date.now() - t0 < 25000) {
      await sleep(300);
      try { var doc = frame.contentDocument; txt = (doc && doc.body) ? doc.body.innerText : ''; } catch (e) { txt = ''; }
      if (norm(txt).indexOf(chave) === -1) { prev = txt; estavel = 0; continue; }
      if (txt.indexOf('Resumo') !== -1 && txt.length > 800) return txt;
      if (txt === prev) { if (++estavel >= 4) return txt; } else estavel = 0;
      prev = txt;
    }
    return norm(txt).indexOf(chave) !== -1 ? txt : '';
  }

  function soResumo(txt) {
    var i = txt.indexOf('Resumo');
    if (i < 0) return '';
    var f = txt.indexOf('Please refer to the relevant page');
    return txt.slice(i + 6, f > i ? f : txt.length);
  }

  /* ---------- paginação ---------- */
  function botaoProxima() {
    return Array.prototype.slice.call(document.querySelectorAll('.ids-pagination-number button'))
      .filter(function (b) { return /seta_direita/.test(b.innerText || ''); })[0];
  }

  /* ---------- fluxo ---------- */
  (async function () {
    try {
      if (location.href.indexOf('tab=reports') === -1) {
        msg('Abra a aba <b>Fixed Income → Relatórios</b> do Smart e clique de novo.');
        return;
      }

      var todos = [], i;
      for (i = 1; i <= PAGINAS; i++) {
        msg('Lendo a listagem — página ' + i + ' de ' + PAGINAS + '…');
        extrair().forEach(function (r) {
          if (!todos.some(function (x) { return x.id === r.id; })) todos.push(r);
        });
        if (i < PAGINAS) {
          var b = botaoProxima(); if (!b) break;
          var antes = document.querySelector('a[href*="credit/report/"]').getAttribute('href');
          b.click();
          for (var k = 0; k < 60; k++) {
            await sleep(400);
            var a = document.querySelector('a[href*="credit/report/"]');
            if (a && a.getAttribute('href') !== antes) break;
          }
          await sleep(900);
        }
      }

      // Empresas conhecidas vêm do cadastro do app (Fontes & Empresas), não
      // de uma lista hardcoded aqui — assim cadastrar empresa nova numa
      // ponta já melhora o casamento na outra.
      msg('Buscando o cadastro de empresas…');
      var cadastro = await fetch(API.replace('/ingest', '/empresas'), { headers: { 'X-Ingest-Token': TOKEN } })
        .then(function (r) { return r.json(); });
      var termos = cadastro.termos || [];

      function casar(texto) {
        var t = ' ' + norm(texto) + ' ', hits = [];
        termos.forEach(function (e) {
          var achou = e.termos.some(function (al) {
            var n = norm(al);
            return new RegExp('(^| )' + n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '( |$)').test(t);
          });
          if (achou && hits.indexOf(e.empresa) === -1) hits.push(e.empresa);
        });
        return hits;
      }

      // Pula o que já está na base. Sem isso, refazer a carga custaria a
      // hora inteira de novo -- e é o que torna a retomada barata depois de
      // uma interrupção.
      msg('Vendo o que já está na base…');
      var jaTem = {};
      try {
        var exist = await fetch(API.replace('/ingest', '/ids'), { headers: { 'X-Ingest-Token': TOKEN } })
          .then(function (r) { return r.json(); });
        (exist.ids || []).forEach(function (id) { jaTem[id] = 1; });
      } catch (e) { /* sem a lista, segue e reprocessa tudo */ }

      var pendentes = todos.filter(function (r) { return !jaTem[r.id]; });
      var jaSalvos = todos.length - pendentes.length;
      if (!pendentes.length) {
        msg('<b style="color:#7BD88F">Nada novo.</b><br>' +
            '<span style="opacity:.6">' + todos.length + ' relatórios já estavam na base</span>');
        return;
      }

      // Envio em lotes: uma falha custa no máximo o lote atual, e cada
      // requisição fica pequena o bastante pra não estourar o tempo da
      // função. Antes ia tudo num POST só no final -- foi assim que a carga
      // inicial de 1.285 relatórios se perdeu inteira (13/08/2026).
      var LOTE = 40;
      var lote = [], enviados = 0, criados = 0, falhas = 0;

      async function despachar() {
        if (!lote.length) return;
        var corpo = JSON.stringify({ relatorios: lote });
        for (var tent = 1; tent <= 3; tent++) {
          try {
            var resp = await fetch(API, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-Ingest-Token': TOKEN },
              body: corpo
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var j = await resp.json();
            enviados += lote.length;
            criados += (j.criados || 0);
            lote = [];
            return;
          } catch (e) {
            if (tent === 3) { falhas += lote.length; lote = []; return; }
            await sleep(1500 * tent);
          }
        }
      }

      // Quem precisa que o resumo seja aberto, só pra estimar o tempo.
      function precisaResumo(r) {
        var p = casar(r.titulo);
        if (p.length) return false;                       // título já resolve
        if (r.categoria === 'Market Dynamics') return false;  // relatório de mercado
        return true;
      }
      var restamResumos = pendentes.filter(precisaResumo).length;

      var lidos = 0;
      for (i = 0; i < pendentes.length; i++) {
        var r = pendentes[i];
        var pri = casar(r.titulo);
        var citadas = pri.slice();
        // Relatório de mercado sem empresa no título nunca cita empresa.
        var mercado = (r.categoria === 'Market Dynamics') && pri.length === 0;
        // Título já nomeia a empresa: não abre o resumo (pedido do Allan,
        // 13/08/2026). Além de cortar ~37 min da carga completa, melhora a
        // precisão -- o resumo de um "Quick Take on PRIO" cita Gerdau e
        // Usiminas por comparação setorial, e isso virava tag de cobertura
        // que não existe. O custo é perder a tag secundária legítima de vez
        // em quando (ex.: Nexa citando Votorantim Cimentos, a controladora).
        if (pri.length === 0 && !mercado) {
          lidos++; restamResumos--;
          msg((i + 1) + ' de ' + pendentes.length + ' · <b>' + enviados + ' já salvos</b>' +
              (jaSalvos ? ' <span style="opacity:.6">(+' + jaSalvos + ' de antes)</span>' : '') +
              '<br><span style="opacity:.6">' + (r.titulo || '').slice(0, 46) + '</span>' +
              '<br><span style="opacity:.45">' + restamResumos + ' resumos a ler · ~' +
              Math.max(1, Math.ceil(restamResumos * 3.5 / 60)) + ' min</span>');
          var txt = await lerResumo(r.id, r.titulo);
          var res = soResumo(txt);
          if (res) casar(res).forEach(function (n) { if (citadas.indexOf(n) === -1) citadas.push(n); });
        }
        lote.push({
          id: r.id, titulo: r.titulo, data: iso(r.dataBr), categoria: r.categoria,
          tipo_investimento: r.tipo_investimento, analista: r.analista,
          empresas: citadas, empresa_principal: pri, mercado: mercado
        });
        if (lote.length >= LOTE) await despachar();
      }
      await despachar();

      msg('<b style="color:#7BD88F">Pronto.</b><br>' +
          enviados + ' gravados · ' + criados + ' novos' +
          (falhas ? ' · <b style="color:#FF8B8B">' + falhas + ' falharam</b>' : '') + '<br>' +
          '<span style="opacity:.6">' + (jaSalvos + enviados) + ' relatórios na base</span>' +
          (falhas ? '<br><span style="opacity:.6">rode o botão de novo pra tentar os que faltaram</span>' : ''));
    } catch (e) {
      msg('<b style="color:#FF8B8B">Erro:</b> ' + (e && e.message ? e.message : e));
    } finally {
      frame.remove();
      setTimeout(function () { box.remove(); }, 12000);
    }
  })();
})();
