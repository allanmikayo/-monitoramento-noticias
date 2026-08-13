# Corrigir o deploy quebrado — passo a passo

## O que aconteceu

O site está com erro 500 em todas as páginas. A causa foi um erro meu no passo a passo anterior.

Eu te mandei commitar `app/models.py` e `app/app.py`. Só que esses dois arquivos, no seu
computador, já continham **semanas de trabalho seu que nunca tinha sido commitado** (o Hub Fase 1
e 2 — emissores, ratings, securitizados, aba Banco de Dados). Eles importam módulos como
`app/spreads/ratings.py`, que também nunca foram para o GitHub.

Resultado: o servidor recebeu um `models.py` pedindo um arquivo que não existe no repositório, e
o app inteiro parou de subir. O log da Vercel diz exatamente isso:

```
ModuleNotFoundError: No module named 'app.spreads.ratings'
```

Eu deveria ter conferido as dependências dos arquivos antes de mandar você commitá-los.

## A correção

Subir o restante do seu trabalho — são 34 arquivos novos e 9 modificados. Já é o código que roda
na sua máquina, então não há nada a decidir: é o que faltava chegar ao servidor.

Abra o **PowerShell** na pasta `credit_monitor` e rode:

```powershell
git add app/ scripts/ tests/ templates/ static/ .github/
git commit -m "Sobe modulos do Hub que faltavam (ratings, issuers, securitizados, banco) + acesso publico so no Repositorio"
git push
```

O `.env` não vai junto — ele está no `.gitignore`.

Espere 1–2 minutos e recarregue o site.

## Segunda parte — o timeout (504)

Depois do push acima o erro mudou de 500 para **504 GATEWAY_TIMEOUT** em todas as rotas,
inclusive `/login`. Ou seja: o app passou a subir, mas o *boot* não terminava a tempo.

Motivo: o `startup` do `app.py` roda `create_all` + `run_migrations` a cada partida a frio — são
29 tabelas conferidas uma a uma e ~17 ALTERs, cada um uma ida e volta até o Supabase. Como o
commit trouxe várias tabelas novas (issuers, ratings, securitizados, reports), criá-las estourou
os 10 segundos do `vercel.json`. A função morria no meio, as tabelas ficavam pela metade, e a
requisição seguinte recomeçava do zero. Loop.

### Passo 1 — criar as tabelas da sua máquina (sem limite de tempo)

No PowerShell, dentro de `credit_monitor`:

```powershell
python -m scripts.init_db
```

Ele lê o `DATABASE_URL` do seu `.env` — o mesmo Supabase da Vercel — e cria o que falta. Deve
terminar com algo como `OK — 29 tabelas no banco.`

> Rode este comando de novo sempre que adicionar tabela ou coluna nova em `models.py`.

### Passo 2 — subir a margem de tempo do boot

```powershell
git add vercel.json scripts/init_db.py CORRIGIR_DEPLOY.md
git commit -m "Corrige timeout do boot: init_db fora do servidor + maxDuration 60s"
git push
```

O `maxDuration` foi de 10 para 60 segundos, dando folga para partidas a frio.

## Como saber se funcionou

- `https://credit-research-dashboard.vercel.app/cobertura` abre **sem login**.
- `https://credit-research-dashboard.vercel.app/` manda para a tela de login.

Se ainda der 500, me mande o que aparece em **Vercel → Logs** que eu leio o traceback.

---

## Também mudou: quem vê o quê

Conforme você pediu, o **Repositório de Relatórios é a única aba aberta**. Todo o resto agora
exige login aprovado por você.

| Página | Antes | Agora |
|---|---|---|
| `/cobertura` — Repositório | pública | **pública** |
| `/` — Notícias | pública | exige login |
| `/spreads` — Spreads | pública | exige login |
| `/fontes` — Fontes & Empresas | login | login |
| `/banco`, `/admin` | admin | admin |
| `/api/articles` | pública | exige login |

Quem não está logado vê no menu apenas **Repositório** e o botão **Entrar** — os outros links
ficam escondidos, para ninguém clicar e cair numa tela de senha.

Para liberar alguém: a pessoa se cadastra em `/cadastro` e você aprova em **Administração**,
como já funciona hoje.

## Depois que o site voltar

Aí sim os 3 passos do `COBERTURA.md`:

1. Criar `COBERTURA_INGEST_TOKEN` nas variáveis de ambiente da Vercel (se ainda não criou).
2. Acessar `/cobertura/bookmarklet` logado como admin e arrastar os dois botões para a barra de favoritos.
3. Abrir o Smart e clicar em **↻ Carga inicial completa**.
