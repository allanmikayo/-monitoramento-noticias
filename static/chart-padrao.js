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
  function eixoData(labelsIso, opcoes) {
    const base = opcoes || {};
    const escalas = base.scales || {};
    return Object.assign({}, base, {
      scales: Object.assign({}, escalas, {
        x: Object.assign({ grid: { display: false } }, escalas.x, {
          ticks: Object.assign(
            { maxTicksLimit: 8, autoSkip: true, maxRotation: 0 },
            (escalas.x || {}).ticks,
            {
              callback(valor, indice) {
                return mesAno(labelsIso[indice]);
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

  window.PADRAO_GRAFICO = { mesAno, dataCheia, eixoData };
})();
