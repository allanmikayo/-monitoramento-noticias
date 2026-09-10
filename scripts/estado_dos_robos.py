"""Estado dos robôs: cada workflow do GitHub Actions x o que chegou no banco.

POR QUE EXISTE (08/09/2026). O diagnóstico do Supabase achou `negocios_b3`
ZERADA numa terça-feira, 15h44, pregão aberto -- ela deveria ter 5 dias de
negócio a negócio. A poda não explica (o corte é hoje menos 5 dias), então
ou a captura parou, ou ela roda e não grava.

Responder isso pede olhar os dois lados ao mesmo tempo:

  1. O GitHub Actions rodou? Com que resultado? O workflow está sequer
     ATIVO -- o GitHub desliga workflow agendado sozinho em repositório
     parado, e desligado não avisa ninguém.
  2. O que efetivamente chegou no banco, por pipeline?

Um lado sozinho engana. Job verde que não grava nada parece saúde; banco
vazio sem o histórico de execução não diz se a culpa é da coleta ou da
fonte.

O lado do GitHub roda PRIMEIRO e imprime sozinho, de propósito: o banco
estava tão saturado em 08/09/2026 que um `count(*)` em tabela vazia não
voltava em 3 minutos. Se o banco travar, você ainda fica com metade da
resposta.

    python -m scripts.estado_dos_robos

Lê GITHUB_TOKEN / GITHUB_REPO do .env (qualquer arquivo .env da pasta) e
DATABASE_URL do mesmo lugar de sempre. Não escreve nada, em lugar nenhum.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
API = "https://api.github.com"
SAIDA = RAIZ / "data" / "estado_dos_robos.txt"

_linhas: list[str] = []


def out(s: str = "") -> None:
    """Imprime E guarda. A execução de 08/09/2026 mostrou só metade do
    relatório na janela do console; com arquivo, o que rodou não se perde."""
    _linhas.append(s)
    try:
        print(s, flush=True)
    except Exception:  # console do Windows engasgando com acento
        print(s.encode("ascii", "replace").decode("ascii"), flush=True)
    salvar()


def salvar() -> None:
    try:
        SAIDA.parent.mkdir(parents=True, exist_ok=True)
        SAIDA.write_text("\n".join(_linhas), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _credenciais_github(repo_cli: str = "") -> tuple[str, str, str]:
    """(token, repo, de_onde_veio) -- procurando em vários lugares.

    APRENDIDO EM 08/09/2026: estas duas variáveis NÃO existem na máquina do
    Allan. Elas moram só nas variáveis de ambiente da Vercel, que é quem
    precisa delas (é lá que roda o `/api/cron-trigger`). O `(2).env` da
    pasta tem as chaves escritas mas com valor VAZIO -- é um arquivo de
    exemplo, e a primeira versão desta função não distinguia "existe a
    linha" de "tem valor".

    Então: procura no ambiente, em cada .env da pasta (token e repo podem
    vir de arquivos diferentes), e aceita `--repo` na linha de comando. Se
    não achar, o script segue e faz a parte do banco -- a do GitHub é
    opcional por desenho.
    """
    import os

    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = (repo_cli or os.getenv("GITHUB_REPO", "")).strip()
    origem = "ambiente" if (token or repo) else ""

    for caminho in sorted(set(list(RAIZ.glob("*.env")) + list(RAIZ.glob(".env")))):
        vals = dotenv_values(caminho)
        if not token and (vals.get("GITHUB_TOKEN") or "").strip():
            token = vals["GITHUB_TOKEN"].strip()
            origem = f"{origem}+{caminho.name}" if origem else caminho.name
        if not repo and (vals.get("GITHUB_REPO") or "").strip():
            repo = vals["GITHUB_REPO"].strip()
            origem = f"{origem}+{caminho.name}" if origem else caminho.name
    return token, repo, origem


def _quando(iso: str | None) -> str:
    if not iso:
        return "-"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    delta = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    horas = delta.total_seconds() / 3600
    idade = f"{horas:.0f}h atrás" if horas < 48 else f"{horas/24:.0f}d atrás"
    return f"{dt:%d/%m %H:%M} ({idade})"


def lado_github(repo_cli: str = "") -> None:
    out("=" * 78)
    out("1. GITHUB ACTIONS -- os workflows estão rodando?")
    out("=" * 78)
    token, repo, origem = _credenciais_github(repo_cli)
    if not token or not repo:
        out("Sem GITHUB_TOKEN/GITHUB_REPO preenchidos aqui -- e é o esperado:")
        out("essas variáveis moram na Vercel, não nesta máquina.")
        out("")
        out("Para consultar o Actions daqui, rode com um token de leitura:")
        out("   set GITHUB_TOKEN=ghp_xxx")
        out("   python -m scripts.estado_dos_robos --repo usuario/repositorio")
        out("")
        out("Ou responda as mesmas perguntas na aba Actions do navegador:")
        out("  1. Filtro 'Event': aparece algum `workflow_dispatch`? Se TUDO for")
        out("     `schedule`, o relay do cron-job.org não está disparando --")
        out("     e é ele quem define a cadência real (o `schedule:` é fallback")
        out("     de 1x/hora).")
        out("  2. Abra a execução vermelha mais recente e leia o passo que")
        out("     falhou. Falha em ~40s costuma ser conexão com o banco;")
        out("     falha perto do limite de tempo é o banco travando no meio.")
        out("  3. No cabeçalho de cada workflow, confira se não aparece")
        out("     'This workflow was disabled' -- o GitHub desliga agendamento")
        out("     sozinho e não avisa.")
        return
    out(f"repositório: {repo}  (credenciais de {origem})")
    out("")

    ses = requests.Session()
    ses.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })

    try:
        r = ses.get(f"{API}/repos/{repo}/actions/workflows", timeout=30)
        r.raise_for_status()
        workflows = r.json().get("workflows", [])
    except Exception as exc:  # noqa: BLE001
        out(f"[falhou] não consegui listar os workflows: {type(exc).__name__}: {exc}")
        return

    for wf in sorted(workflows, key=lambda w: w["path"]):
        arquivo = wf["path"].split("/")[-1]
        estado = wf["state"]
        # `active` é o normal. `disabled_inactivity` = o GitHub desligou o
        # agendamento sozinho por falta de atividade no repositório;
        # `disabled_manually` = alguém desligou no botão.
        alerta = "" if estado == "active" else "   <<< DESLIGADO"
        out(f"--- {arquivo}  [{estado}]{alerta}")
        try:
            rr = ses.get(f"{API}/repos/{repo}/actions/workflows/{wf['id']}/runs",
                         params={"per_page": 8}, timeout=30)
            rr.raise_for_status()
            runs = rr.json().get("workflow_runs", [])
        except Exception as exc:  # noqa: BLE001
            out(f"    [falhou ao ler execuções] {exc}")
            continue
        if not runs:
            out("    nunca executou")
            continue
        for run in runs:
            out(f"    {(run['conclusion'] or run['status'] or '?'):>12}  "
                  f"{_quando(run['created_at']):<22} {run['event']}")
        conclusoes = [x["conclusion"] for x in runs if x["conclusion"]]
        if conclusoes and all(c != "success" for c in conclusoes):
            out("    !! nenhuma das últimas execuções teve sucesso")
        out()


def lado_banco() -> None:
    out("=" * 78)
    out("2. BANCO -- o que efetivamente chegou, por pipeline")
    out("=" * 78)
    from sqlalchemy import text

    from app.db import engine

    # `SET statement_timeout` é do Postgres. Rodando local contra o SQLite
    # o script tem que continuar servindo -- é o mesmo código nos dois.
    e_postgres = engine.dialect.name == "postgresql"

    def _com_limite(conn):
        if e_postgres:
            conn.execute(text("SET statement_timeout = 15000"))

    consultas = [
        ("notícias (articles)",
         "select count(*) as linhas, max(found_at) as ultimo from articles"),
        ("balcão B3 bruto (negocios_b3)",
         "select count(*) as linhas, max(data_negocio) as ultimo from negocios_b3"),
        ("balcão B3 agregado (negocios_b3_diario)",
         "select count(*) as linhas, max(data) as ultimo from negocios_b3_diario"),
        ("spreads de debênture",
         "select count(*) as linhas, max(data) as ultimo from debenture_spreads"),
        ("securitizados",
         "select count(*) as linhas, max(data) as ultimo from securitizado_spreads"),
        ("curva de NTN-B",
         "select count(*) as linhas, max(data) as ultimo from ntnb_referencia"),
        ("varreduras (run_logs)",
         "select count(*) as linhas, max(started_at) as ultimo from run_logs"),
    ]
    for rotulo, sql in consultas:
        # Conexão própria por consulta: se uma travar, as outras seguem.
        try:
            with engine.connect() as conn:
                _com_limite(conn)
                linhas, ultimo = conn.execute(text(sql)).one()
            out(f"  {rotulo:<42} {str(linhas):>9} linha(s)   último: {ultimo or '-'}")
        except Exception as exc:  # noqa: BLE001
            out(f"  {rotulo:<42} [falhou] {type(exc).__name__}: {str(exc)[:120]}")

    out("")
    try:
        with engine.connect() as conn:
            _com_limite(conn)
            existe = conn.execute(text(
                "select count(*) from pg_indexes "
                "where schemaname='public' and indexname='ix_articles_data'"
            )).scalar()
        out("  índice ix_articles_data: " + ("PRESENTE"
            if existe else "AUSENTE -- rode `python -m scripts.init_db`"))
    except Exception as exc:  # noqa: BLE001
        out(f"  índice ix_articles_data: [falhou] {type(exc).__name__}")


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Estado dos robôs")
    ap.add_argument("--repo", default="", help="usuario/repositorio no GitHub")
    args = ap.parse_args()

    out(f"gerado em {datetime.now().astimezone():%d/%m/%Y %H:%M:%S %z}")
    out("")
    lado_github(args.repo)
    out("")
    lado_banco()
    out("")
    out(f"relatório salvo em: {SAIDA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
