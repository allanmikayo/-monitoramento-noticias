/* Padrão visual dos gráficos do Hub (11/09/2026, pedido do Allan).
 *
 * Carregado DEPOIS do Chart.js e ANTES dos scripts que criam gráficos, em
 * todo template que tenha gráfico. Mexe em `Chart.defaults`, então vale para
 * todos eles de uma vez -- a alternativa seria repetir o mesmo bloco de
 * opções em cada `new Chart(...)` e deixar os gráficos divergirem com o
 * tempo, que é exatamente como um dashboard fica com sete estilos.
 *
 * O que muda, e por quê:
 *
 * - TEXTO PRETO nos eixos, nos títulos de eixo e na legenda. O cinza padrão
 *   do Chart.js é bonito num gráfico isolado e ilegível numa tela densa,
 *   principalmente impresso ou projetado.
 * - MARCADOR DE LEGENDA como bolinha, não retângulo. O retângulo colorido
 *   pesa mais que a própria linha que ele descreve.
 * - LINHA DE EIXO preta, grade continua clara: o eixo delimita, a grade só
 *   orienta. Com os dois no mesmo cinza, nenhum dos dois faz seu trabalho.
 * - DICA DE FERRAMENTA no modo "index": passar o mouse em qualquer ponto
 *   mostra a data e TODAS as séries naquela data, sem precisar acertar o
 *   pixel de uma linha.
 */
(function () {
  if (typeof Chart === "undefined") return;

  const PRETO = "#000000";
  const GRADE = "#e8e8e8";

  Chart.defaults.color = PRETO;
  Chart.defaults.font.family =
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif";
  Chart.defaults.font.size = 11;

  Chart.defaults.plugins.legend.labels.color = PRETO;
  // `usePointStyle` troca o retângulo pelo estilo do ponto do dataset; como
  // as séries desenham com `pointRadius: 0`, o padrão vira a bolinha aqui.
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.pointStyle = "circle";
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.legend.labels.boxHeight = 8;
  Chart.defaults.plugins.legend.labels.padding = 14;
  // Bolinha CHEIA, na cor da linha. Em série com preenchimento quase
  // transparente embaixo (o laranja do spread médio), a legenda herdava essa
  // cor clarinha e a bolinha saía oca -- parecendo outra série.
  const gerarRotulosPadrao = Chart.defaults.plugins.legend.labels.generateLabels;
  Chart.defaults.plugins.legend.labels.generateLabels = function (chart) {
    return gerarRotulosPadrao.call(this, chart).map((rot) => {
      const ds = chart.data.datasets[rot.datasetIndex];
      const tipo = (ds && ds.type) || chart.config.type;
      if (tipo === "line" && typeof rot.strokeStyle === "string") {
        rot.fillStyle = rot.strokeStyle;
      }
      return rot;
    });
  };

  // Modo "index" + `intersect: false`: o alvo é a POSIÇÃO no eixo x, não a
  // linha. Em série temporal é o que se quer -- "o que aconteceu neste dia".
  Chart.defaults.plugins.tooltip.enabled = true;
  Chart.defaults.plugins.tooltip.mode = "index";
  Chart.defaults.plugins.tooltip.intersect = false;
  Chart.defaults.plugins.tooltip.usePointStyle = true;
  Chart.defaults.plugins.tooltip.backgroundColor = "rgba(17,17,17,0.92)";
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.titleFont = { weight: "700" };
  Chart.defaults.interaction = { mode: "index", intersect: false };

  ["category", "linear", "logarithmic", "time"].forEach((tipo) => {
    const esc = Chart.defaults.scales[tipo];
    if (!esc) return;
    esc.ticks = Object.assign({}, esc.ticks, { color: PRETO });
    esc.grid = Object.assign({}, esc.grid, { color: GRADE });
    esc.border = Object.assign({}, esc.border, { color: PRETO, width: 1 });
    esc.title = Object.assign({}, esc.title, { color: PRETO });
  });

  const MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                 "Jul", "Ago", "Set", "Out", "Nov", "Dez"];

  /* "2026-03-15" -> "Mar-26". Formato pedido pelo Allan para eixo de data:
     num gráfico de meses, o dia é ruído -- a data exata continua disponível
     na dica de ferramenta. */
  function mesAno(iso) {
    if (!iso) return "";
    const [a, m] = String(iso).split("-");
    if (!m) return String(iso);
    return `${MESES[Number(m) - 1]}-${a.slice(2)}`;
  }

  /* "2026-03-15" -> "15/03/2026", para o título da dica de ferramenta. */
  function dataCheia(iso) {
    if (!iso) return "";
    const [a, m, d] = String(iso).split("-");
    return d ? `${d}/${m}/${a}` : String(iso);
  }

  /* Opções de um gráfico de série temporal cujos `labels` são datas ISO.
   *
   * POR QUE OS LABELS FICAM EM ISO: é o formato que permite ao eixo mostrar
   * "Mar-26" e à dica de ferramenta mostrar "15/03/2026" a partir do MESMO
   * dado. Formatando na hora de montar os labels (como era antes), o eixo
   * ficava preso ao formato escolhido ali e a dica repetia exatamente a
   * mesma string. */
  /* Em quais posições do eixo escrever o mês: só na VIRADA de mês.
   *
   * BUG CORRIGIDO (21/09/2026). A versão anterior deixava o Chart.js
   * escolher ~8 posições espaçadas e escrevia "Mês-Ano" em cada uma -- numa
   * série de 3 meses isso dava "Jul-26, Jul-26, Jul-26". Um rótulo de mês
   * só tem sentido uma vez por mês. Com muitos meses (histórico de 2 anos
   * num gráfico de meia largura), escreve um a cada N para não encavalar;
   * as marcas sem texto continuam lá, só em branco. */
  function viradasDeMes(labelsIso, maxRotulos) {
    const viradas = [];
    labelsIso.forEach((iso, i) => {
      const mes = String(iso).slice(0, 7);
      if (i === 0) {
        // O primeiro ponto só ganha rótulo se a série começa no início do
        // mês. Começando no dia 24, "Jun-26" ficaria colado em "Jul-26" logo
        // adiante, rotulando uma semana como se fosse um mês.
        if (Number(String(iso).slice(8, 10)) <= 7) viradas.push(i);
      } else if (mes !== String(labelsIso[i - 1]).slice(0, 7)) {
        viradas.push(i);
      }
    });
    const passo = Math.max(1, Math.ceil(viradas.length / (maxRotulos || 12)));
    return new Set(viradas.filter((_, k) => k % passo === 0));
  }

  function eixoData(labelsIso, opcoes) {
    const base = opcoes || {};
    const escalas = base.scales || {};
    const extra = (escalas.x || {}).ticks || {};
    const mostrar = viradasDeMes(labelsIso, extra.maxRotulosMes || 12);
    return Object.assign({}, base, {
      scales: Object.assign({}, escalas, {
        x: Object.assign({ grid: { display: false } }, escalas.x, {
          ticks: Object.assign(
            { maxRotation: 0 },
            extra,
            {
              // Uma marca por data, sem pular: quem decide o que aparece é
              // `mostrar`, não o autoSkip do Chart.js -- ele não sabe o que
              // é uma virada de mês.
              autoSkip: false,
              callback(valor, indice) {
                return mostrar.has(indice) ? mesAno(labelsIso[indice]) : "";
              },
            }
          ),
        }),
      }),
      plugins: Object.assign({}, base.plugins, {
        tooltip: Object.assign({}, (base.plugins || {}).tooltip, {
          callbacks: Object.assign(
            {
              title(itens) {
                return itens.length ? dataCheia(labelsIso[itens[0].dataIndex]) : "";
              },
            },
            ((base.plugins || {}).tooltip || {}).callbacks
          ),
        }),
      }),
    });
  }

  /* "2026-09-18" -> "18-set". Rótulo de coluna quando cada coluna é UMA
     data específica (composição semanal do relatório), e não um eixo
     contínuo de meses -- aí o dia é a informação, não ruído. */
  function diaMes(iso) {
    if (!iso) return "";
    const [, m, d] = String(iso).split("-");
    if (!d) return String(iso);
    return `${Number(d)}-${MESES[Number(m) - 1].toLowerCase()}`;
  }

  /* Linha preta no zero do eixo y. Em gráfico de abertura/fechamento o zero
     É a informação -- separa quem abriu de quem fechou -- e não pode ter o
     mesmo peso das linhas de grade. Uso: `plugins: [PADRAO_GRAFICO.linhaZero]`. */
  const linhaZero = {
    id: "linhaZero",
    afterDatasetsDraw(chart) {
      const y = chart.scales.y;
      if (!y || y.min > 0 || y.max < 0) return;
      const { ctx, chartArea } = chart;
      const py = y.getPixelForValue(0);
      ctx.save();
      ctx.strokeStyle = PRETO;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(chartArea.left, py);
      ctx.lineTo(chartArea.right, py);
      ctx.stroke();
      ctx.restore();
    },
  };

  /* Escreve o valor dentro de cada segmento de coluna empilhada, como no
     relatório ("49%", "13%"). O texto de cada dataset vem de
     `dataset.rotulos[i]` (já formatado) e a cor de `dataset.corRotulo`.
     Segmento baixo demais para caber o texto fica sem rótulo: o número
     continua na dica de ferramenta, e um "1%" espremido sobre a borda só
     vira borrão. */
  const rotulosNasBarras = {
    id: "rotulosNasBarras",
    afterDatasetsDraw(chart) {
      const { ctx } = chart;
      ctx.save();
      ctx.font = `600 11px ${Chart.defaults.font.family}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      chart.data.datasets.forEach((ds, i) => {
        const meta = chart.getDatasetMeta(i);
        if (meta.hidden || !ds.rotulos) return;
        meta.data.forEach((barra, j) => {
          const texto = ds.rotulos[j];
          if (!texto) return;
          const { y, base } = barra.getProps(["y", "base"], true);
          if (Math.abs(base - y) < 14) return;
          ctx.fillStyle = ds.corRotulo || "#ffffff";
          ctx.fillText(texto, barra.x, (y + base) / 2);
        });
      });
      ctx.restore();
    },
  };

  /* "2026-09-18" -> "18/09": data sem ano, para lugares apertados em que o
     ano é óbvio pelo contexto (a linha de contexto da barra fixa). */
  function diaMesCurto(iso) {
    if (!iso) return "";
    const [, m, d] = String(iso).split("-");
    return d ? `${d}/${m}` : String(iso);
  }

  window.PADRAO_GRAFICO = {
    mesAno, dataCheia, diaMes, diaMesCurto, eixoData, linhaZero, rotulosNasBarras, PRETO, GRADE,
  };
})();
