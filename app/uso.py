"""Registro de uso do Hub e o painel do admin (22/09/2026).

O Allan quer saber quais abas e funções são usadas e quais setores/empresas
as pessoas mais procuram -- insumo para decidir o que cobrir e o que
melhorar. Desenho deliberadamente simples:

  - o navegador manda eventos pequenos para POST /api/uso (static/uso.js);
  - uma tabela só (`uso_eventos`), vocabulário fixo em `ACOES`;
  - o painel /admin/uso agrega com GROUP BY na hora (volume de um time,
    não precisa de tabela resumo).
"""
from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import UsoEvento, User

ABAS = ("Repositório", "Notícias", "Spreads", "Balcão B3", "Mercado Primário",
        "Fontes & Empresas", "Administração", "Minha conta")

# O que cada `acao` quer dizer -- é também o rótulo que o painel mostra.
ACOES = {
    "visita": "Visita à aba",
    "setor": "Setor",
    "empresa": "Empresa",
    "emissor": "Emissor",
    "grupo": "Grupo econômico",
    "ticker": "Ticker",
    "fonte": "Fonte",
    "relatorio": "Relatório aberto",
    "busca": "Busca",
    "filtro": "Outro filtro",
    "subaba": "Sub-aba",
}
# Entram na lista "Empresas": o mesmo nome procurado no Repositório, nas
# Notícias e nos Emissores é o mesmo interesse.
ACOES_EMPRESA = ("empresa", "emissor")
ACOES_OUTROS = ("grupo", "ticker", "fonte", "filtro", "subaba")

_BRT = ZoneInfo("America/Sao_Paulo")


def registrar(db: Session, *, aba: str, acao: str, alvo: str = "",
              user_id: str | None = None, visitante: str | None = None) -> bool:
    """Grava um evento. Devolve False (e não grava) se vier fora do
    vocabulário -- o endpoint é aberto, então nada de texto livre em
    `aba`/`acao`, e `alvo` é cortado."""
    if aba not in ABAS or acao not in ACOES:
        return False
    alvo = " ".join((alvo or "").split())[:200]
    if acao != "visita" and not alvo:
        return False
    db.add(UsoEvento(aba=aba, acao=acao, alvo=alvo, user_id=user_id,
                     visitante=(visitante or "")[:40] or None))
    db.commit()
    return True


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _eventos(db: Session, dias: int, incluir_admin: bool, user_id: str | None):
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    q = select(UsoEvento).where(UsoEvento.criado_em >= desde)
    if user_id:
        q = q.where(UsoEvento.user_id == user_id)
    elif not incluir_admin:
        admins = select(User.id).where(User.role == "admin")
        q = q.where((UsoEvento.user_id.is_(None)) | (UsoEvento.user_id.not_in(admins)))
    return db.scalars(q.order_by(UsoEvento.criado_em)).all()


def _pessoa(ev: UsoEvento) -> str | None:
    """Chave de "pessoa distinta": usuário logado ou navegador anônimo."""
    return f"u:{ev.user_id}" if ev.user_id else (f"v:{ev.visitante}" if ev.visitante else None)


def _top(contagem: Counter, pessoas: dict, n: int, extra: dict | None = None) -> list[dict]:
    out = []
    for chave, qtd in contagem.most_common(n):
        item = {"nome": chave, "vezes": qtd, "pessoas": len(pessoas[chave])}
        if extra is not None:
            item["onde"] = sorted(extra[chave])
        out.append(item)
    return out


def painel(db: Session, dias: int = 30, incluir_admin: bool = False,
           user_id: str | None = None) -> dict:
    dias = max(1, min(int(dias), 365))
    evs = _eventos(db, dias, incluir_admin, user_id)
    usuarios = {u.id: u for u in db.scalars(select(User)).all()}

    identificados, anonimos = set(), set()
    visitas_aba, pessoas_aba = Counter(), defaultdict(set)
    por_dia = defaultdict(set)
    grupos = {k: (Counter(), defaultdict(set), defaultdict(set)) for k in
              ("setor", "empresa", "relatorio", "busca", "outros")}
    por_user: dict[str, dict] = {}

    for ev in evs:
        p = _pessoa(ev)
        if ev.user_id:
            identificados.add(ev.user_id)
        elif ev.visitante:
            anonimos.add(ev.visitante)
        dia = _utc(ev.criado_em).astimezone(_BRT).date().isoformat()
        if p:
            por_dia[dia].add(p)
        if ev.acao == "visita":
            visitas_aba[ev.aba] += 1
            if p:
                pessoas_aba[ev.aba].add(p)
        else:
            if ev.acao in ACOES_EMPRESA:
                chave, nome = "empresa", ev.alvo
            elif ev.acao in ACOES_OUTROS:
                chave, nome = "outros", f"{ACOES[ev.acao]}: {ev.alvo}"
            else:
                chave, nome = ev.acao, ev.alvo
            cont, pess, onde = grupos[chave]
            cont[nome] += 1
            if p:
                pess[nome].add(p)
            onde[nome].add(ev.aba)

        if ev.user_id:
            r = por_user.setdefault(ev.user_id, {"visitas": 0, "eventos": 0, "dias": set(),
                                                  "abas": Counter(), "interesses": Counter(),
                                                  "ultimo": ev.criado_em})
            r["eventos"] += 1
            r["dias"].add(dia)
            r["ultimo"] = max(_utc(r["ultimo"]), _utc(ev.criado_em))
            if ev.acao == "visita":
                r["visitas"] += 1
                r["abas"][ev.aba] += 1
            elif ev.acao in ("setor",) + ACOES_EMPRESA:
                r["interesses"][ev.alvo] += 1

    # Série diária com todos os dias do período (dia sem uso = 0, não some).
    hoje = datetime.now(_BRT).date()
    serie = [{"dia": (hoje - timedelta(days=i)).isoformat(),
              "pessoas": len(por_dia.get((hoje - timedelta(days=i)).isoformat(), ()))}
             for i in range(dias - 1, -1, -1)]

    pessoas = []
    for uid, r in por_user.items():
        u = usuarios.get(uid)
        pessoas.append({
            "user_id": uid,
            "email": u.email if u else "(conta removida)",
            "nome": u.name if u else "",
            "ultimo": _utc(r["ultimo"]).isoformat(),
            "dias_ativos": len(r["dias"]),
            "visitas": r["visitas"],
            "eventos": r["eventos"],
            "aba_preferida": r["abas"].most_common(1)[0][0] if r["abas"] else "",
            "interesses": [n for n, _ in r["interesses"].most_common(3)],
        })
    pessoas.sort(key=lambda x: (-x["dias_ativos"], -x["eventos"]))

    return {
        "dias": dias,
        "kpis": {
            "pessoas": len(identificados),
            "anonimos": len(anonimos),
            "visitas": sum(visitas_aba.values()),
            "eventos": len(evs),
        },
        "abas": [{"nome": a, "visitas": n, "pessoas": len(pessoas_aba[a])}
                 for a, n in visitas_aba.most_common()],
        "serie": serie,
        "setores": _top(grupos["setor"][0], grupos["setor"][1], 15, grupos["setor"][2]),
        "empresas": _top(grupos["empresa"][0], grupos["empresa"][1], 15, grupos["empresa"][2]),
        "relatorios": _top(grupos["relatorio"][0], grupos["relatorio"][1], 10),
        "buscas": _top(grupos["busca"][0], grupos["busca"][1], 10, grupos["busca"][2]),
        "outros": _top(grupos["outros"][0], grupos["outros"][1], 10, grupos["outros"][2]),
        "por_pessoa": pessoas,
        "usuarios": sorted(({"id": u.id, "email": u.email} for u in usuarios.values()),
                           key=lambda x: x["email"]),
    }


def csv_eventos(db: Session, dias: int, incluir_admin: bool, user_id: str | None) -> str:
    """Todos os eventos do período, um por linha -- para o Allan abrir no
    Excel e cruzar do jeito que quiser. Separador ";" e BOM por causa do
    Excel em pt-BR."""
    evs = _eventos(db, max(1, min(int(dias), 365)), incluir_admin, user_id)
    emails = {u.id: u.email for u in db.scalars(select(User)).all()}
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["data_hora_brasilia", "email", "visitante_anonimo", "aba", "acao", "alvo"])
    for ev in evs:
        w.writerow([
            _utc(ev.criado_em).astimezone(_BRT).strftime("%d/%m/%Y %H:%M:%S"),
            emails.get(ev.user_id, "") if ev.user_id else "",
            "" if ev.user_id else (ev.visitante or ""),
            ev.aba, ACOES.get(ev.acao, ev.acao), ev.alvo,
        ])
    return buf.getvalue()
