# Repositório de Relatórios — passo a passo

Catálogo dos relatórios de Fixed Income Credit Research publicados no Smart, tagueados por
empresa e setor. Resolve o problema de o Smart só tagear por ticker de bolsa: um relatório
chamado *"Resultados 2T26 – Parte 1"* analisa 20 empresas, e hoje quem busca pelo nome da
empresa não o encontra.

É um módulo novo dentro do Hub Credit Research que você já tem no ar — **nenhuma rota existente
foi alterada**. Vai aparecer como uma aba "Repositório" no menu, ao lado de Notícias e Spreads.

São **3 etapas, uns 15 minutos**. Depois disso, a carga inicial roda sozinha por uma hora.

---

## Etapa 1 — Criar a senha de ingestão na Vercel

Essa senha é o que autoriza o botão do navegador a gravar no seu banco. Sem ela, o
recurso fica desligado.

1. Entre em **https://vercel.com** e abra o projeto do monitoramento.
2. Vá em **Settings** → **Environment Variables**.
3. Clique em **Add New**.
4. Em **Key**, escreva exatamente:
   ```
   COBERTURA_INGEST_TOKEN
   ```
5. Em **Value**, cole:
   ```
   uOw7FVEJCOPobnE-XK7uigBeNV2PxSrfid9JqALXmf4
   ```
   (é uma senha aleatória que gerei agora; pode trocar por outra se preferir)
6. Deixe marcado **Production**, **Preview** e **Development**.
7. Clique em **Save**.

---

## Etapa 2 — Subir o código

Abra o **PowerShell** na pasta `credit_monitor` (mesma coisa que você fez no deploy do
módulo Spreads) e rode os comandos abaixo, um de cada vez.

> **Atenção:** não use `git add .` aqui. Você tem bastante coisa modificada e ainda não
> commitada nessa pasta (`CLAUDE.md`, `app/db.py`, `app/store.py`, `static/app.js` e outros)
> que **não** faz parte deste trabalho. Os comandos abaixo sobem só os arquivos do
> Repositório de Relatórios.

```powershell
git add app/cobertura_routes.py
git add templates/cobertura.html
git add templates/cobertura_bookmarklet.html
git add static/cobertura-coletor.js
git add app/models.py
git add app/app.py
git add templates/base.html
git add COBERTURA.md

git commit -m "Repositorio de Relatorios: catalogo dos relatorios do Smart por empresa e setor"
git push
```

A Vercel percebe o push e refaz o deploy sozinha, em 1–2 minutos. As tabelas novas no banco
são criadas automaticamente no primeiro acesso — você não precisa mexer no Supabase.

Para conferir se subiu: acesse o site e veja se apareceu **Repositório** no menu do topo.

---

## Etapa 3 — Instalar o botão e fazer a carga inicial

1. No site, **faça login com sua conta de admin**.
2. Acesse **`/cobertura/bookmarklet`** (ex.: `https://seu-site.vercel.app/cobertura/bookmarklet`).
3. Deixe a barra de favoritos do Chrome visível com **Ctrl+Shift+B**.
4. **Arraste** — não clique — os dois botões laranja/cinza da página para a barra de favoritos.
5. Abra o Smart em **Fixed Income → Relatórios** e confirme que está logado.
6. Clique no favorito **↻ Carga inicial completa**.
7. Vai aparecer um painel preto no canto da tela mostrando o progresso. **Deixe a aba aberta**,
   demora cerca de uma hora (são 1.285 relatórios, e ele abre cada um para ler o resumo).
8. Quando terminar, o painel mostra quantos entraram. Volte em `/cobertura` — está populado.

---

## Uso no dia a dia

Sempre que quiser atualizar: abra o Smart em **Fixed Income → Relatórios** e clique no favorito
**↻ Atualizar Repositório**. Ele lê as duas primeiras páginas (60 relatórios), leva cerca de um
minuto e só adiciona o que é novo. Pode clicar quantas vezes quiser por dia.

---

# Referência técnica

## O que foi adicionado

| Arquivo | Papel |
|---|---|
| `app/models.py` | Tabelas `reports`, `report_company`, `report_sector` (append, nada removido) |
| `app/cobertura_routes.py` | Rotas do módulo |
| `templates/cobertura.html` | A tela do catálogo |
| `templates/cobertura_bookmarklet.html` | Página de instalação do botão (admin) |
| `static/cobertura-coletor.js` | O coletor que roda dentro do Smart |
| `app/app.py` | Duas linhas: import e `include_router` |
| `templates/base.html` | Link "Repositório" no menu |

## Rotas

| Rota | Acesso |
|---|---|
| `GET /cobertura` | Público |
| `GET /api/cobertura/dados` | Público |
| `POST /api/cobertura/relatorio/{id}/tags` | Admin |
| `GET /cobertura/bookmarklet` | Admin |
| `POST /api/cobertura/ingest` | Token `X-Ingest-Token` |
| `GET /api/cobertura/empresas` | Token `X-Ingest-Token` |

## Cadastro de empresas — não há lista duplicada

As tags saem do cadastro que já existe em **Fontes & Empresas** (`sectors`, `companies`,
`company_aliases`). A ingestão **recusa** empresa fora do cadastro, e é isso que impede
"Petrobras" e "Petrobrás" virarem duas empresas.

Consequência prática: cadastrar uma empresa ou um alias melhora o casamento **nos dois módulos**
ao mesmo tempo — notícias e relatórios. Vale conferir se o cadastro tem os aliases que o Smart
usa nos títulos: `MercadoLibre` (→ Mercado Livre), `Petrobras`, `Ultrapar` (→ Ultra),
`AXIA Energia` (→ Axia), `Rede D'Or`, `FS` (→ FS Bio).

## Curadoria ganha do robô

Editar as tags de um relatório marca `reviewed = true`. Reingestões passam a atualizar só os
metadados e **não** mexem mais na classificação daquele relatório — sem isso, cada rodada do
botão desfaria a revisão feita à mão.

Relatórios de categoria **Market Dynamics** sem empresa no título (Semanal, Top Picks, Market
Highlights) são marcados como *mercado* e ficam fora da fila de pendências: são ~40% da base e
nunca são sobre empresa específica.

## Como o tagueamento funciona

1. O título é comparado com os nomes e aliases do cadastro de empresas. **Se resolve, para por
   aí — o resumo não é aberto** (13/08/2026). Além de cortar a carga completa de ~48 para
   ~9 minutos, isso melhora a precisão: o resumo de um *"Quick Take on PRIO"* cita Gerdau e
   Usiminas por comparação setorial, e isso virava tag de cobertura que não existe.
2. Se o título não resolve (ex.: *"Resultados 2T26 – Parte 1"*), aí sim o **Resumo** da página do
   relatório no Smart é lido — é ele que cita as empresas nominalmente. Esse relatório sozinho
   rendeu 20 tags; *"Resultados 1T26 – Parte 2"*, 25. É o caso que justifica o projeto inteiro.
3. **Market Dynamics** sem empresa no título nunca é sobre empresa: vira Mercado, sem abrir resumo.
4. O que sobra cai em Pendências de revisão.

Na base já coletada, isso significa abrir o resumo de 14 relatórios em cada 120 — 80% menos do
que antes.

O preço da regra 1 é perder a tag secundária legítima de vez em quando: *Nexa* citando
*Votorantim Cimentos*, que é a controladora. Se um dia incomodar, é uma linha em
`static/cobertura-coletor.js` (a condição `pri.length === 0`).

## A coleta grava em lotes

O coletor manda para o servidor **a cada 40 relatórios**, não no fim. Antes acumulava tudo na
memória do navegador e fazia um POST único — a carga inicial rodou uma hora, o POST com 1.285
relatórios estourou o tempo da função, e a hora inteira se perdeu (13/08/2026).

Junto disso, antes de começar ele pergunta ao servidor quais IDs já existem e pula esses. Então
clicar no botão de novo **retoma de onde parou**, sem refazer nada.

## Por que a atualização não roda sozinha na nuvem

O Smart exige sessão autenticada. A API que o portal consome
(`proxy-api.cloud.itau.com.br/research/v1/reports`) recusa requisição sem o header de
autorização que só o app dele emite — **testado em 13/08/2026** abrindo o endpoint direto no
navegador já logado: o Chrome recebeu página de erro.

Ou seja, um job do Actions bateria na mesma porta. É diferente das notícias, que vêm de sites
abertos (InfoMoney, CVM, B3). E guardar credencial pessoal de rede num secret do GitHub não é
opção: viola política de segurança, some a rastreabilidade de quem acessou o quê, e não passaria
pelo MFA.

**O caminho para automatizar de verdade** é uma credencial de serviço para esse endpoint, emitida
pela tecnologia do Itaú — exatamente o modelo do `ANBIMA_CLIENT_ID`/`ANBIMA_CLIENT_SECRET` que já
está no `.env`. Com ela, o coletor vira um script Python, entra num workflow com
`workflow_dispatch` + cron-job.org, e aí sim roda 6h/10h/14h sem ninguém clicar. O código de
casamento de empresa é o mesmo — muda só de onde vêm os dados.

## Segurança do token

O link do bookmarklet carrega o `COBERTURA_INGEST_TOKEN` embutido, por isso
`/cobertura/bookmarklet` é restrita a admin. Quem tem o token escreve na base (não lê nada do
Smart nem do seu login). Se vazar, troque a variável na Vercel e reinstale o botão.

O CORS do endpoint de ingestão aceita só `itau.com.br` — é a única origem de onde a coleta pode
legitimamente partir.

## A pasta `Cobertura/`

É o protótipo do catálogo em arquivo solto (HTML + `data.json`), de antes de virar módulo do app.
Não é usada por nada e **não precisa ser commitada**. Pode apagar quando quiser.
