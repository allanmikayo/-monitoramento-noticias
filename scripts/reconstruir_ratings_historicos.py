"""Reconstrói o histórico de ratings a partir da view de spreads.

MOTIVO (pedido do Allan, 04/08/2026): a base de ratings dele começa em
29/04/2026, mas o histórico de spread vai até jan/2025. Sem rating pro
período anterior, a junção as-of cobre só 13% das linhas e as análises
por rating perdem 21 meses.

A saída, porém, JÁ TEM o dado: a "view final" de spreads que ele monta é
o cruzamento spread × rating, então cada linha carrega o rating que
estava valendo naquela data. Este script inverte o processo -- lê as
observações (ticker, data, fitch, sp, moodys) e deriva os EVENTOS de
mudança que as produziriam.

    python -m scripts.reconstruir_ratings_historicos --base base.json --dry-run
    python -m scripts.reconstruir_ratings_historicos --base base.json

O que é gravado: ações em `issuer_ratings` com `origem='RETROATIVO_VIEW'`,
distinguíveis das raspadas de verdade. Os períodos de vigência são
reconstruídos por `issuers.reconstruir_periodos_rating()` no fim.

TRÊS CUIDADOS QUE O DADO EXIGIU
-------------------------------
1. **A data é de OBSERVAÇÃO, não da ação.** A base tem ~84 datas em 18
   meses (semanal), então a mudança real aconteceu em algum ponto entre
   duas observações. Grava-se a PRIMEIRA data em que o valor novo
   aparece — é o mais cedo que se pode afirmar com o dado disponível.
   Consequência: uma ação de rating pode aparecer com até ~1 semana de
   atraso em relação à data real de publicação da agência. Quando o
   scraper trouxer a ação real, ela entra com a data correta e convive
   com esta (origens diferentes).

2. **Rating por EMISSÃO, não só por emissor.** Validado no dado: a COSAN
   tem três níveis simultâneos e estáveis (CSAN13/14/16 sempre AAA,
   CSAN15/18/23... sempre A+, CSANB2 sempre A) — tranches com garantias
   diferentes. Por isso o script decide, POR EMISSOR, se o rating é
   uniforme (grava com `codigo=None`) ou divergente entre papéis (grava
   por `codigo`). Forçar tudo pra nível de emissor mudaria as curvas que
   o Allan já analisa, que é justamente o que ele pediu pra evitar.

3. **Valor inválido não vira rating.** `N.R.`, `-`, `0` e `(blank)` são
   ausência; `A+` na coluna da Fitch (falta o `(bra)`) é erro de
   preenchimento. Nenhum dos dois entra — o segundo é contado e
   reportado.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine, run_migrations  # noqa: E402
from app.spreads import issuers as isv  # noqa: E402
from app.spreads.issuer_key import issuer_key  # noqa: E402
from app.spreads.ratings import eh_vazio, peso_de  # noqa: E402

ORIGEM = "RETROATIVO_VIEW"
CAMPO_AGENCIA = {"fitch": "FITCH", "sp": "SP", "moodys": "MOODYS"}


def _data(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def carregar_observacoes(caminho: Path) -> list[dict]:
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return dados if isinstance(dados, list) else dados.get("base", [])


def _valor_valido(campo: str, bruto) -> tuple[str | None, str | None]:
    """(rating válido, valor inválido) — no máximo um dos dois."""
    v = "" if bruto is None else str(bruto).strip()
    if not v or eh_vazio(v):
        return None, None
    if peso_de(campo, v) is None:
        return None, v
    return v, None


def analisar(observacoes: list[dict]) -> dict:
    """Deriva eventos de mudança de rating a partir das observações.

    Devolve, por emissor, a lista de ações a gravar e o diagnóstico.
    """
    # (chave_emissor, ticker) -> {data: {agencia: rating}}
    por_papel: dict[tuple[str, str], dict[date, dict[str, str]]] = collections.defaultdict(dict)
    invalidos: collections.Counter = collections.Counter()
    nomes: dict[str, str] = {}
    sem_nome = 0

    for obs in observacoes:
        chave = issuer_key(obs.get("nomeAnbima"))
        ticker = (obs.get("ticker") or "").strip().upper()
        dt = _data(obs.get("data"))
        if not chave or not ticker or dt is None:
            sem_nome += 1
            continue
        nomes.setdefault(chave, (obs.get("nomeAnbima") or "").strip())
        estado: dict[str, str] = {}
        for campo, agencia in CAMPO_AGENCIA.items():
            ok, ruim = _valor_valido(campo, obs.get(campo))
            if ruim:
                invalidos[(campo, ruim)] += 1
            if ok:
                estado[agencia] = ok
        # `_medio` é o rating médio COPIADO da view do Allan -- é ele que
        # congela o histórico, não o recálculo a partir de `estado`.
        estado["_medio"] = (obs.get("ratingMedio") or "").strip() or "N.A."
        por_papel[(chave, ticker)][dt] = estado

    # Por emissor: o rating é uniforme entre os papéis, ou divergente?
    por_emissor: dict[str, dict] = {}
    for (chave, ticker), serie in por_papel.items():
        por_emissor.setdefault(chave, {"papeis": {}})["papeis"][ticker] = serie

    resultado: dict[str, dict] = {}
    for chave, info in por_emissor.items():
        papeis = info["papeis"]
        # Assinatura de cada papel = sua série temporal inteira. Papéis com
        # assinaturas diferentes têm rating próprio.
        assinaturas = {
            tk: tuple(sorted((d, tuple(sorted(e.items()))) for d, e in serie.items()))
            for tk, serie in papeis.items()
        }
        # Considera uniforme se todos os papéis concordam onde AMBOS têm
        # observação -- cobertura parcial (um papel só tem Fitch, outro tem
        # Fitch+S&P) é união, não divergência.
        conflito = False
        combinado: dict[date, dict[str, str]] = collections.defaultdict(dict)
        for serie in papeis.values():
            for d, estado in serie.items():
                for ag, rt in estado.items():
                    if ag == "_medio":
                        continue
                    anterior = combinado[d].get(ag)
                    if anterior is not None and anterior != rt:
                        conflito = True
                    combinado[d][ag] = rt

        # SEMPRE grava as duas granularidades:
        #
        # - por TICKER (`codigo` preenchido): reproduz exatamente o que a
        #   view do Allan mostrava para aquele papel naquela data. É o que
        #   garante "sem alteração nas curvas e dados analisados" — pedido
        #   explícito dele.
        # - por EMISSOR (`codigo=None`): a união das observações de todos
        #   os papéis. Serve de fallback para debênture NOVA, que não
        #   existia na base histórica e portanto não tem série própria.
        #
        # MEDIDO (04/08/2026): só com a granularidade de emissor o replay
        # contra a base dele bate 92,4%; incluindo a de ticker, 98,4% —
        # os 6 pontos de diferença são justamente os papéis cuja série
        # individual difere da união do emissor. `rating_em()` resolve com
        # "mais específico vence", então a de ticker prevalece e a de
        # emissor só age onde não há série própria.
        series: dict[str | None, dict] = dict(papeis)
        series[None] = dict(combinado)

        resultado[chave] = {
            "nome": nomes.get(chave, chave),
            "por_emissao": conflito,
            "series": series,
            "n_papeis": len(papeis),
            "n_assinaturas": len(set(assinaturas.values())),
        }
    return {"emissores": resultado, "invalidos": invalidos, "descartadas": sem_nome}


def _eventos(serie: dict[date, dict[str, str]],
             registrar_retirada: bool = False) -> list[tuple[date, str, str | None]]:
    """Datas em que cada agência MUDA de valor.

    `registrar_retirada` controla o ponto mais delicado da reconstrução:
    o que fazer quando uma agência PARA de aparecer.

    - `False` (padrão): só mudanças entre valores presentes. A agência
      que some mantém a última nota. **Reproduz o comportamento da base
      do Allan**, que carrega o rating adiante quando a linha vem sem
      valor de agência.
    - `True`: a ausência vira evento e limpa a agência do cálculo.
      Conceitualmente mais correto (agência sem cobertura não deveria
      contar), mas MEDIDO em 04/08/2026: derruba a aderência ao histórico
      dele de 93,3% pra 92,0%, criando 1.093 linhas "calculei N.A., a
      base diz AAA".

    O padrão é `False` porque o pedido explícito foi "não ter alterações
    nas curvas e dados analisados". A opção fica disponível para quando o
    histórico passar a vir de ação de rating de verdade (com data e tipo
    de ação), em vez de observação semanal.
    """
    eventos: list[tuple[date, str, str | None]] = []
    anterior: dict[str, str | None] = {}
    for d in sorted(serie):
        estado = serie[d]
        escopo = ((set(estado) | set(anterior)) if registrar_retirada else set(estado)) - {"_medio"}
        for ag in escopo:
            atual = estado.get(ag)
            if not registrar_retirada and atual is None:
                continue
            if anterior.get(ag, "\0") != atual:
                eventos.append((d, ag, atual))
                anterior[ag] = atual
    return eventos


def _serie_congelada(serie: dict) -> list[tuple[date, str, int | None, dict]]:
    """Série de (data, rating_medio, notch, agências) sem repetição
    consecutiva — o `rating_medio` vem COPIADO da view, não recalculado.

    É isto que garante "o histórico não deve ser alterado": onde a
    planilha do Allan divergia do recálculo (linha defasada, ~1,7% da
    base), o valor DELE é o que fica.
    """
    from app.spreads.ratings import PADRAO_TO_PESO

    saida: list[tuple[date, str, int | None, dict]] = []
    for d in sorted(serie):
        estado = dict(serie[d])
        medio = estado.pop("_medio", "N.A.") or "N.A."
        if saida and saida[-1][1] == medio and saida[-1][3] == estado:
            continue
        saida.append((d, medio, PADRAO_TO_PESO.get(medio), estado))
    return saida


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", type=Path, required=True,
                    help="JSON com as observações (ticker, data, fitch, sp, moodys, nomeAnbima)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retirada-limpa", action="store_true",
                    help="agência que para de aparecer deixa de contar na média "
                         "(mais correto conceitualmente, mas altera o histórico "
                         "do Allan -- ver docstring de _eventos)")
    args = ap.parse_args()

    obs = carregar_observacoes(args.base)
    print(f"observações lidas: {len(obs):,}")
    an = analisar(obs)
    emissores = an["emissores"]

    por_emissao = [c for c, v in emissores.items() if v["por_emissao"]]
    print(f"emissores               : {len(emissores)}")
    print(f"  rating uniforme       : {len(emissores) - len(por_emissao)}")
    print(f"  rating por emissão    : {len(por_emissao)}")
    for chave in sorted(por_emissao)[:10]:
        v = emissores[chave]
        print(f"      {v['nome'][:44]:<46} {v['n_papeis']:3d} papéis, {v['n_assinaturas']} ratings distintos")
    if an["invalidos"]:
        print("  valores em formato inválido (ignorados):")
        for (campo, val), n in an["invalidos"].most_common(8):
            print(f"      {n:5d}x  {campo}: {val!r}")
    if an["descartadas"]:
        print(f"  observações sem emissor/ticker/data: {an['descartadas']}")

    Base.metadata.create_all(engine)
    run_migrations()
    db = SessionLocal()
    try:
        gravadas = duplicadas = 0
        sem_issuer: list[str] = []
        for chave, v in emissores.items():
            issuer = isv.resolver_issuer(db, v["nome"])
            if issuer is None:
                sem_issuer.append(v["nome"])
                continue
            for codigo, serie in v["series"].items():
                for dt, agencia, rating in _eventos(serie, args.retirada_limpa):
                    if args.dry_run:
                        gravadas += 1
                        continue
                    criada = isv.registrar_acao_rating(
                        db, issuer, agencia=agencia, rating=rating,
                        data_acao=dt, codigo=codigo, origem=ORIGEM,
                        acao=("Retirada de cobertura (observada)" if rating is None
                              else "Observado na view de spreads"),
                    )
                    if criada is None:
                        duplicadas += 1
                    else:
                        gravadas += 1
        print(f"\nações {'que seriam gravadas' if args.dry_run else 'gravadas'}: {gravadas}")
        if not args.dry_run:
            print(f"já existentes                : {duplicadas}")
        if sem_issuer:
            print(f"emissores não encontrados    : {len(sem_issuer)}")
            for n in sem_issuer[:8]:
                print(f"      {n}")

        if not args.dry_run:
            db.commit()
            # ORDEM IMPORTA: os observados primeiro. `reconstruir_periodos`
            # usa o fim da janela observada de cada escopo pra ignorar ação
            # já representada nela -- sem isso, ação antiga geraria período
            # concorrente e o salto de fronteira voltaria.
            print("\ngravando períodos OBSERVADOS (cópia da view)...")
            congelados = 0
            for chave, v in emissores.items():
                issuer = isv.resolver_issuer(db, v["nome"])
                if issuer is None:
                    continue
                for codigo, serie in v["series"].items():
                    congelados += isv.gravar_periodos_historicos(
                        db, issuer.id, codigo, _serie_congelada(serie))
            db.commit()
            # O último período de cada escopo fica ABERTO: o rating segue
            # valendo até uma ação NOVA chegar e fechá-lo. Ver
            # issuers.gravar_periodos_historicos.
            print(f"  períodos observados: {congelados} (último de cada escopo em aberto)")

            print("reconstruindo períodos DERIVADOS (ações posteriores à janela)...")
            r = isv.recalcular_todos_ratings(db)
            print(f"  emissores: {r['emissores']} | com rating: {r['com_rating']} | períodos derivados: {r['periodos']}")

            from app.spreads.views import conferir_view, criar_views
            print("\ncriando a view v_spread_rating...")
            criar_views(engine)
            chk = conferir_view(engine)
            print(f"  linhas: {chk['linhas_view']:,} (base {chk['linhas_base']:,})"
                  f" | duplicou: {'SIM -- BUG' if chk['duplicou'] else 'não'}")
            print(f"  rating_medio nulo: {chk['rating_nulo']} (tem que ser 0)"
                  f" | classificadas como N.A.: {chk['sem_rating']:,}")
            print(f"  {'OK' if chk['ok'] else 'FALHOU -- conferir a junção'}")
        else:
            print("\n[DRY-RUN] nada foi gravado.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
