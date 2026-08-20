# As bases do Hub Credit Research

Levantado em 20/08/2026 lendo o código, não a documentação — em pelo menos
três pontos os comentários diziam o oposto do que o código fazia.

---

## Visão geral

| Módulo | Tabelas | Fonte | Frequência | Automatizado? |
|---|---|---|---|---|
| Notícias | `articles`, `sources`, `companies`, `sectors` | ~15 sites (RSS + Playwright) | 5 min | Sim |
| Spreads debêntures | `debentures`, `debenture_spreads` | API ANBIMA + debentures.com.br | 1×/dia, 21h BRT | Sim |
| Curva de juro real | `ntnb_referencia` | API ANBIMA (títulos públicos) | 1×/dia, 21h BRT | Sim |
| Securitizados | `securitizados`, `securitizado_spreads` | API ANBIMA (CRI/CRA) | 1×/dia, após debêntures | Sim |
| Balcão B3 | `negocios_b3`, `negocios_b3_diario` | API pública do BDI da B3 | 15 min no pregão | Sim |
| ~~Ratings~~ | ~~`issuer_ratings`, `issuers`~~ | — | — | **Removido em 20/08** |
| Repositório | `reports` | Catálogo do Smart | Manual | Não |
| Infra | `users`, `sessions`, `app_settings`, `run_logs` | — | — | — |

---

## 1. Notícias

**Tabelas:** `articles` · `article_company` · `article_sector` · `sources` · `source_keywords` · `sectors` · `companies` · `company_aliases` · `sector_keywords` · `run_logs`

**Fonte.** ~15 fontes em `app/config.py::KNOWN_SOURCES`: agências de rating (S&P, Moody's Local, Fitch), CVM RAD (fatos relevantes), assembleias de debenturistas (Oliveira Trust, Vórtx, Pentágono), imprensa (InfoMoney, Money Times, Brazil Journal, Valor, CanalEnergia…) e Banco Central (comunicados do Copom). RSS onde existe, Playwright onde o site é SPA.

**Cálculo.** Não há cálculo — há *casamento*. `taxonomy.build_index()` monta a lista de keywords (nome da empresa + aliases + termos do setor) e `filter.match_keywords` casa contra título e resumo. Um artigo só é guardado se citar empresa ou termo coberto. Se bate empresa específica, vincula só ela; se bate apenas termo de setor, ganha uma tag de setor.

**Frequência.** `scrape.yml` — cron-job.org dispara a cada 5 min; o `schedule:` nativo do GitHub é fallback de 1×/hora.

**Fonte de verdade da cobertura:** `data/Setores.xlsx` (96 empresas, 17 setores), carregada por `scripts/seed.py`. Aliases e termos podem ser editados pela aba Fontes & Empresas sem mexer em código.

---

## 2. Spreads de debêntures

**Tabelas:** `debentures` (cadastro, uma linha por código) · `debenture_spreads` (histórico, uma linha por código+data)

**Fontes.**

- **API oficial da ANBIMA** (`/feed/precos-indices/v1/debentures/mercado-secundario`) — taxa indicativa, PU, duration, % PU par, referência de NTN-B, emissor. OAuth2 client_credentials.
- **debentures.com.br** — estoque por ativo (R$ mil) e características das emissões (CNPJ, se é incentivada pela Lei 12.431).

Houve um pivô aqui: o boletim `.xls` público da ANBIMA só guarda os últimos ~5 dias úteis, inviável para histórico. A API oficial tem ~2 anos.

**Cálculos.**

*Spread em bps:*

- Papel com referência de NTN-B: `((1 + taxa/100) / (1 + taxa_ntnb/100) − 1) × 10000`
- CDI+ sem referência: `taxa × 100` (a taxa indicativa já é spread sobre o DI)
- IPCA+ sem referência: mesma fórmula, usando a NTN-B de vértice mais curto

*Classe* (`compute_classe`) — o filtro que nunca se mistura:

- IPCA+ e incentivada → **IPCA + Incentivadas**
- CDI+ e não incentivada → **CDI + Tradicionais**
- qualquer outra combinação → **Outros** (fora dos gráficos, de propósito)

*Convenções:* estoque ÷ 1000 (R$ mil → R$ milhões), duration ÷ 252 (dias úteis → anos). Médias de spread são **ponderadas por estoque**, nunca simples.

**Frequência.** 1×/dia às 21h BRT, dentro da rodada noturna.

---

## 3. Curva de juro real

**Tabela:** `ntnb_referencia` (chave: data)

**Fonte.** API ANBIMA, `/feed/precos-indices/v1/titulos-publicos/mercado-secundario-TPF`, filtrando `tipo_titulo = "NTN-B"`.

**Por que existe.** A taxa da NTN-B é publicada uma vez por dia. Sem cache, o job de negócio a negócio da B3 bateria na ANBIMA a cada 15 minutos pelo mesmo número — 30 a 40 chamadas OAuth por dia à toa. Guarda a curva inteira (`{vencimento: taxa}`), não só um vértice, porque cada papel tem sua própria referência.

**Frequência.** Uma vez por dia, populada de graça pelo job de debêntures (reaproveita a chamada que ele já faz).

---

## 4. Securitizados (CRI/CRA)

**Tabelas:** `securitizados` · `securitizado_spreads`

**Fonte.** API ANBIMA, `/feed/precos-indices/v1/cri-cra/mercado-secundario`.

**Cálculo.** Mesma lógica de spread das debêntures. Papel IPCA+ lê a curva já cacheada em `ntnb_referencia` — daí a ordem obrigatória: debêntures antes de securitizados.

**Frequência.** 1×/dia, logo depois de debêntures.

**Limitação conhecida:** o vínculo originador → emissor não funciona. O log de ontem mostra `0 de 354 ligados`. O nome que a ANBIMA traz é o da securitizadora (o veículo), não o do devedor real. Resolver isso exige abrir a escritura de cada papel — não está feito.

---

## 5. Balcão B3

**Tabelas:** `negocios_b3` (bruto, um registro por negócio) · `negocios_b3_diario` (agregado, uma linha por código+data)

**Fonte.** API pública do Boletim Diário do Mercado da B3, sem autenticação:
`POST https://arquivos.b3.com.br/bdi/table/Trade/{ini}/{fim}/{pág}/{tam}`. Filtra DEB, CRI e CRA. Máximo de 1000 linhas por página.

**Cálculo.** `compute_trade_spreads` converte a taxa negociada em spread na hora da gravação — não em tempo real quando a tela carrega. CDI+ é `taxa × 100`; IPCA+ desconta a NTN-B de referência do papel. A curva usada é sempre a do **último boletim ANBIMA publicado antes do negócio** (na prática D-1), nunca a do próprio dia: a do dia só fica pronta às 21h, então negócios da manhã ficariam sem spread para sempre.

**Frequência.** Captura a cada 15 min durante o pregão (9h–18h BRT). Agregação e poda 1×/dia, à noite.

**Duas retenções, de propósito:**

| | Retenção | Motivo |
|---|---|---|
| `negocios_b3` (bruto) | **5 dias** | ~15 MB fixos. Responde "o que aconteceu esta semana" |
| `negocios_b3_diario` | **para sempre** | ~34 MB/ano. Responde "como evolui no tempo" |

Sem a poda, o bruto cresce ~740 MB/ano. **Foi exatamente o que aconteceu:** a rodada noturna nunca rodava na nuvem, e o Supabase começou a alertar estouro de Disk IO em 13/08.

---

## 6. Ratings — REMOVIDO em 20/08/2026

> **Decisão do Allan (20/08/2026):** manter apenas a **coleta de notícias**
> de ação de rating, que já funciona; tirar todo o pipeline de coleta e
> consolidação de base. Assunto adiado.
>
> **O que continua:** os coletores de S&P, Moody's Local e Fitch em
> `app/sources/` seguem trazendo as ações de rating como **notícia**, para
> a tabela `articles`, na varredura de 5 minutos. Nada disso foi tocado.
>
> **O que saiu:** as etapas `ratings` e `periodos` da rodada noturna e os
> scripts `mapear_ratings_2026.py`, `reconstruir_ratings_historicos.py`,
> `importar_ws_credit_research.py`, `seed_issuers.py` mais o
> `Mapear Ratings 2026.bat`.
>
> **O que ficou dormente:** tabelas `issuers` / `issuer_ratings` /
> `issuer_rating_periodo`, o módulo `app/spreads/issuers.py` (incluindo
> `registrar_acao_rating()`, pronta e nunca chamada) e os quatro blocos de
> rating da aba Spreads. Eles já saíam vazios antes da remoção.
>
> **Para retomar:** `git log --diff-filter=D --name-only` acha os arquivos;
> `git checkout <commit>^ -- <caminho>` traz de volta.

O texto abaixo descreve como era, e continua valendo como mapa para quando
o assunto voltar.

### Como era

**Tabelas:** `issuers` · `issuer_aliases` · `issuer_ratings` (eventos) · `issuer_rating_atual` (view) · `issuer_rating_periodo` (derivada)

**Fonte.** Não há coletor. O dado veio de duas importações pontuais:

- `scripts/importar_ws_credit_research.py` — importou o seu `ws.credit_research.db` (78.197 observações, 110 datas desde jan/2025)
- `scripts/reconstruir_ratings_historicos.py` — derivou os eventos de mudança a partir da view de spreads, para cobrir o período anterior a abr/2026

**O scraper existe, mas não alimenta o banco.** `scripts/mapear_ratings_2026.py` raspa S&P, Moody's Local e Fitch via Playwright — só que ele **gera um `.xlsx`**, não grava. E é o mercado inteiro, não só a sua cobertura. Existe uma função `registrar_acao_rating()` em `app/spreads/issuers.py` pronta para receber os eventos, mas **nenhum script a chama**. É o encaixe que falta.

**Frequência.** Manual, quando você roda `Mapear Ratings 2026.bat` e importa o resultado.

**Problema mais grave, visível no log de ontem:**

```
ações de rating no banco: 0 | mais recente: None
períodos derivados: 0 | emissores com rating: 0
```

**O Supabase está com zero ratings.** As importações foram feitas no banco local; o de produção nunca recebeu. Toda análise por rating no site (curva por rating, compressão entre ratings, dispersão intra-rating) está saindo vazia hoje.

---

## 7. Repositório de Relatórios

**Tabela:** `reports` — catálogo dos relatórios do Smart, tagueados por empresa e setor. Alimentação manual pela interface. Única aba pública desde 13/08.

---

## Ordem de dependência da rodada noturna

Não é conveniência, é dependência real:

```
1. DEBÊNTURES    → busca e cacheia a curva de NTN-B
2. SECURITIZADOS → LÊ essa curva do cache
3. B3            → fecha o agregado do dia e poda o bruto
```

Uma etapa que falha não derruba as seguintes.

A view `v_spread_rating` era criada na antiga etapa `periodos`; passou para
`scripts/init_db.py`. Rode `python -m scripts.init_db` depois de qualquer
mudança de schema.

---

## O que está furado hoje

| Item | Situação | Gravidade |
|---|---|---|
| Originador → emissor em CRI/CRA | 0 de 354 ligados | Média |
| Blocos de rating na aba Spreads | Saem vazios (tabela zerada, pipeline removido) | Baixa — decisão consciente |
| Disk IO do Supabase | Corrigido em 20/08 (rodada noturna ligada) | Resolvido |
| Agregação B3 em Postgres | Corrigido em 20/08 (SQL era SQLite-only) | Resolvido |
| Pipeline de ratings | Removido em 20/08, por decisão | Resolvido |
