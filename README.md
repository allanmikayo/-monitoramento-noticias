# Monitoramento de Notícias — Crédito Privado

Dashboard para acompanhar notícias, ações de rating (Fitch/Moody's/S&P) e
fatos relevantes das empresas que você cobre (debêntures, CRI, CRA).

## Como rodar (jeito fácil)

Dê **dois cliques** no arquivo **`Abrir Monitoramento.bat`** dentro desta
pasta. Na primeira vez, uma janela preta vai aparecer e demorar alguns
minutos preparando tudo (só acontece uma vez) — depois disso, o navegador
abre sozinho no site. Nas próximas vezes, é só dar dois cliques de novo e
esperar alguns segundos.

**Não feche a janela preta** enquanto estiver usando o site — é ela que
mantém o Monitoramento rodando. Para fechar o site, feche essa janela.

Se aparecer um aviso do Windows Defender/SmartScreen ("Windows protegeu o
computador"), clique em **"Mais informações"** → **"Executar assim mesmo"**
(é comum acontecer com arquivos `.bat` baixados/criados localmente).

Isso exige que o **Python** esteja instalado no computador. Se a janela
disser que não encontrou o Python, baixe em
https://www.python.org/downloads/ — na instalação, marque a caixinha
**"Add python.exe to PATH"** antes de clicar em Install — e dê dois
cliques no `.bat` de novo.

Na primeira vez, além do Python, o `.bat` também baixa um navegador
Chromium usado só para coletar as ações de rating da S&P e da Moody's
Local (essas páginas só mostram a tabela depois de rodar JavaScript, então
precisam de um navegador de verdade, não só de um download de HTML). São
uns 150–300MB, uma única vez — pode demorar alguns minutos dependendo da
internet.

## Como rodar (linha de comando, alternativa)

```powershell
cd credit_monitor
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
python -m scripts.seed
python run.py
```

Abra http://localhost:8765 — login inicial:

- **E-mail:** allancruz078@gmail.com
- **Senha:** a que estiver em `BOOTSTRAP_ADMIN_PASSWORD` no arquivo `.env`
  (padrão: `troque-esta-senha`)

Troque a senha em **"Minha conta"** assim que entrar.

## O que já funciona

- Login, cadastro com confirmação por e-mail, painel administrativo
  (usuários, sessões, tempo de expiração).
- Robô varre as fontes automaticamente a cada 5 minutos + botão "Forçar
  atualização".
- Filtro por 24h / 5 dias / último mês, por setor, empresa e tipo (notícia,
  ação de rating, fato relevante).
- Aba "Fontes & Empresas" para adicionar apelidos/tickers de empresa e
  termos de setor, e ligar/desligar fontes — sem precisar mexer em código.
- 6 fontes já coletando de verdade: S&P Brasil, Moody's Local (3 páginas),
  InfoMoney e Money Times.
- S&P e Moody's Local usam um navegador real (Playwright) por baixo dos
  panos, porque as páginas delas só mostram a tabela de ações de rating
  depois de carregar via JavaScript.

## O que falta (ver CLAUDE.md para detalhes)

- Fitch, Uqbar, CVM (fatos relevantes) e B3 Fundos.NET (CRI/CRA) estão
  cadastrados mas **desabilitados** — essas páginas exigem um navegador
  automatizado (Playwright) ou mapeamento de API interna, que é a próxima
  frente de trabalho.
- Está rodando local. O roteiro de migração para nuvem gratuita (Supabase
  + GitHub Actions + GitHub Pages) está documentado no `CLAUDE.md`.

## Se algo der erro na primeira execução

Este projeto foi construído e testado ponta a ponta (login, cadastro,
filtros, admin, dedupe) com dados simulados, mas os scrapers reais não
puderam ser testados contra a internet no ambiente onde foram gerados
(rede restrita). É bem provável que algum seletor precise de ajuste fino
depois do primeiro `python run.py` de verdade — me avise qual fonte deu
problema (0 resultados, título estranho, etc.) que eu ajusto. Use o painel
"Diagnóstico da última varredura" no topo do site para ver, fonte por
fonte, quantas notícias foram encontradas, quantas bateram com sua
cobertura e quantas eram novas.

Sobre notícias (InfoMoney/Money Times) mostrando pouca coisa: essas fontes
usam RSS, que só traz os itens mais recentes publicados pelo próprio site
— não dá para "puxar" 5 dias de história se o site simplesmente não
publicou muita coisa sobre o tema nesses 5 dias. O histórico completo vai
se formando sozinho a cada varredura (a cada 5 minutos, o que for novo
entra no banco e fica lá). Se depois de alguns dias de uso a cobertura
ainda parecer curta, me avise que a gente adiciona mais fontes.
