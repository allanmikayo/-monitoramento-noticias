"""Por que a aba Emissores mostra menos do que deveria.

PEDIDO DO ALLAN (11/09/2026). Com tres CPFLs selecionadas, o grafico
desenhou uma linha so, o painel de noticias veio vazio e a tabela de
negocios da B3 tambem. Sao tres perguntas diferentes sobre os MESMOS
emissores, e nenhuma delas se responde olhando a tela -- todas dependem do
que existe (ou nao existe) no banco.

Este script nao muda nada. So le e imprime.

    python -m scripts.diagnostico_emissores
    python -m scripts.diagnostico_emissores "COPEL" "ENERGISA S/A"
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

from sqlalchemy import func

from app.db import criar_sessao_manutencao
from app.models import (
    Article, Company, Debenture, DebentureSpread, Issuer, NegocioB3,
)

SessionLocal = criar_sessao_manutencao()

PADRAO = [
    "CPFL ENERGIAS RENOVÁVEIS",
    "CPFL GERAÇÃO DE ENERGIA S/A",
    "CPFL TRANSMISSAO S.A",
]


def _titulo(txt: str) -> None:
    print()
    print(txt)
    print("-" * len(txt))


def main() -> int:
    nomes = sys.argv[1:] or PADRAO
    with SessionLocal() as db:
        print("Emissores consultados:")
        for n in nomes:
            print(f"  - {n}")

        # ------------------------------------------------------------------
        # 1. O GRAFICO. Ele filtra por classe: emissor sem ticker NAQUELA
        #    classe nao vira linha nenhuma. Se o papel existe mas o spread
        #    do dia e' nulo, tambem nao vira ponto.
        # ------------------------------------------------------------------
        _titulo("1) Tickers por emissor e classe (por que o grafico tem uma linha so)")
        linhas = (
            db.query(
                Debenture.nome, Debenture.classe,
                func.count(Debenture.codigo).label("n_tickers"),
            )
            .filter(Debenture.nome.in_(nomes))
            .group_by(Debenture.nome, Debenture.classe)
            .order_by(Debenture.nome, Debenture.classe)
            .all()
        )
        if not linhas:
            print("  NENHUM ticker encontrado com esses nomes exatos.")
            print("  (o nome tem que bater caractere a caractere com debentures.nome)")
        for nome, classe, n in linhas:
            codigos = [
                r[0] for r in db.query(Debenture.codigo)
                .filter(Debenture.nome == nome, Debenture.classe == classe).all()
            ]
            com_spread = (
                db.query(func.count(func.distinct(DebentureSpread.codigo)))
                .filter(DebentureSpread.codigo.in_(codigos),
                        DebentureSpread.spread.isnot(None))
                .scalar() or 0
            )
            print(f"  {nome:<35} {str(classe):<22} {n:>3} ticker(s), "
                  f"{com_spread} com spread calculado")

        # ------------------------------------------------------------------
        # 2. AS NOTICIAS. O painel busca por company_id -- so aparece noticia
        #    de emissor JA LIGADO a uma empresa da cobertura.
        # ------------------------------------------------------------------
        _titulo("2) Ligacao com a cobertura de noticias (por que o painel esta vazio)")
        for nome in nomes:
            deb = db.query(Debenture).filter(Debenture.nome == nome).first()
            if deb is None:
                print(f"  {nome}: sem ticker no cadastro")
                continue
            emp = db.get(Company, deb.company_id) if deb.company_id else None
            iss = db.get(Issuer, deb.issuer_id) if deb.issuer_id else None
            print(f"  {nome}")
            print(f"      company_id={deb.company_id} "
                  f"({emp.name if emp else 'SEM EMPRESA LIGADA'})")
            print(f"      issuer_id={deb.issuer_id} ({iss.nome if iss else '-'})")
            print(f"      setor={deb.setor!r} subsetor={deb.subsetor!r}")
            print(f"      grupo_economico={deb.grupo_economico!r}")
        print()
        print("  Empresas da cobertura com nome parecido (para saber se existe alvo):")
        for c in db.query(Company).filter(Company.name.ilike("%CPFL%")).all():
            print(f"      id={c.id} {c.name}")
        n_art = db.query(func.count(Article.id)).scalar() or 0
        ult = db.query(func.max(Article.published_at)).scalar()
        print(f"  Artigos na base: {n_art} (mais recente: {ult})")

        # ------------------------------------------------------------------
        # 3. OS NEGOCIOS DA B3. A captura esta parada desde a migracao (os
        #    jobs do GitHub Actions e do cron-job.org seguem desligados).
        # ------------------------------------------------------------------
        _titulo("3) Negocios da B3 (por que a tabela de baixo esta vazia)")
        total = db.query(func.count(NegocioB3.id)).scalar() or 0
        ultima = db.query(func.max(NegocioB3.data_negocio)).scalar()
        print(f"  Linhas em negocios_b3: {total}")
        print(f"  Negocio mais recente:  {ultima}")
        if ultima:
            atraso = (date.today() - ultima).days
            print(f"  Atraso: {atraso} dia(s) corridos")
        codigos = [
            r[0] for r in db.query(Debenture.codigo)
            .filter(Debenture.nome.in_(nomes)).all()
        ]
        n_desses = (
            db.query(func.count(NegocioB3.id))
            .filter(NegocioB3.codigo.in_(codigos)).scalar() or 0
        ) if codigos else 0
        recentes = (
            db.query(func.count(NegocioB3.id))
            .filter(NegocioB3.codigo.in_(codigos),
                    NegocioB3.data_negocio >= date.today() - timedelta(days=30))
            .scalar() or 0
        ) if codigos else 0
        print(f"  Negocios desses emissores (historico inteiro): {n_desses}")
        print(f"  Negocios desses emissores (ultimos 30 dias):   {recentes}")

        # ------------------------------------------------------------------
        # 4. GRUPO ECONOMICO -- insumo do filtro novo que o Allan pediu.
        # ------------------------------------------------------------------
        _titulo("4) Grupo economico (insumo do filtro novo)")
        preenchidos = (
            db.query(func.count(Debenture.codigo))
            .filter(Debenture.grupo_economico.isnot(None)).scalar() or 0
        )
        total_deb = db.query(func.count(Debenture.codigo)).scalar() or 0
        n_grupos = (
            db.query(func.count(func.distinct(Debenture.grupo_economico)))
            .filter(Debenture.grupo_economico.isnot(None)).scalar() or 0
        )
        print(f"  Tickers com grupo economico: {preenchidos} de {total_deb}")
        print(f"  Grupos economicos distintos: {n_grupos}")
        print("  Exemplo -- grupos das CPFLs consultadas:")
        for g in (
            db.query(Debenture.grupo_economico, func.count(Debenture.codigo))
            .filter(Debenture.nome.in_(nomes))
            .group_by(Debenture.grupo_economico).all()
        ):
            print(f"      {g[0]!r}: {g[1]} ticker(s)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
