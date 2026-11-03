"""Peças comuns das fontes de agente fiduciário (Vórtx, Oliveira Trust,
Pentágono), reescritas em 03/11/2026 para HTTP direto (sem navegador).

Cada fonte precisa saber três coisas sobre a cobertura:
  * os NOMES (empresa + apelidos), para casar o nome que o site mostra;
  * a raiz de CNPJ (8 dígitos) de cada empresa, quando o site traz CNPJ;
  * os TICKERS: debêntures (`debentures.company_id`) e CRI/CRA
    (`securitizados.issuer_id` -> `issuers.company_id`). Para CRI e CRA o
    nome no site é o da securitizadora, então só o ticker leva ao devedor.

O título de todo documento sai como "EMPRESA — Categoria — Assunto", o mesmo
formato do RAD: a tela mostra a categoria (Edital, Ata, Fato relevante,
Relatório...) e o nome da empresa garante o vínculo no pipeline.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .base import get

logger = logging.getLogger(__name__)

# Janela de documentos: um pouco acima dos 30 dias do filtro "1 mês" da tela.
LOOKBACK_DAYS = 40


def corte() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)


def so_digitos(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


@dataclass
class Cobertura:
    nomes: list[str] = field(default_factory=list)          # empresa + apelidos
    nome_por_chave: dict[str, str] = field(default_factory=dict)  # nome/apelido -> empresa
    nome_por_cnpj8: dict[str, str] = field(default_factory=dict)
    nome_por_ticker: dict[str, str] = field(default_factory=dict)

    def empresa_no_texto(self, texto: str) -> str | None:
        """Nome da empresa da cobertura citada em `texto`, ou None."""
        from ..filter import match_keywords

        achados = match_keywords(texto or "", self.nomes)
        for k in sorted(achados, key=len, reverse=True):
            if k in self.nome_por_chave:
                return self.nome_por_chave[k]
        return None

    def empresa_do_cnpj(self, cnpj: str | None) -> str | None:
        return self.nome_por_cnpj8.get(so_digitos(cnpj)[:8]) if cnpj else None

    def empresa_do_ticker(self, codigo: str | None) -> str | None:
        return self.nome_por_ticker.get((codigo or "").strip().upper())


def carregar_cobertura() -> Cobertura:
    cob = Cobertura()
    try:
        from sqlalchemy import select

        from ..db import SessionLocal
        from ..models import Company, CompanyAlias, Debenture, Issuer, Securitizado

        with SessionLocal() as db:
            empresas = {c.id: c.name for c in db.scalars(select(Company).where(Company.active.is_(True)))}
            for cid, nome in empresas.items():
                cob.nomes.append(nome)
                cob.nome_por_chave[nome] = nome
            for a in db.scalars(select(CompanyAlias)):
                if a.alias and a.company_id in empresas:
                    cob.nomes.append(a.alias)
                    cob.nome_por_chave.setdefault(a.alias, empresas[a.company_id])
            for cid, cnpj, codigo in db.execute(
                select(Debenture.company_id, Debenture.cnpj, Debenture.codigo).where(Debenture.company_id.is_not(None))
            ):
                if cid not in empresas:
                    continue
                if codigo:
                    cob.nome_por_ticker[codigo.upper()] = empresas[cid]
                if so_digitos(cnpj):
                    cob.nome_por_cnpj8.setdefault(so_digitos(cnpj)[:8], empresas[cid])
            for cid, cnpj in db.execute(
                select(Issuer.company_id, Issuer.cnpj).where(Issuer.company_id.is_not(None))
            ):
                if cid in empresas and so_digitos(cnpj):
                    cob.nome_por_cnpj8.setdefault(so_digitos(cnpj)[:8], empresas[cid])
            for codigo, cid in db.execute(
                select(Securitizado.codigo, Issuer.company_id)
                .join(Issuer, Securitizado.issuer_id == Issuer.id)
                .where(Issuer.company_id.is_not(None))
            ):
                if cid in empresas and codigo:
                    cob.nome_por_ticker[codigo.upper()] = empresas[cid]
            _tickers_pelo_nome(db, cob)
    except Exception as e:  # noqa: BLE001
        logger.warning("fiduciarios: falha carregando a cobertura: %s", e)
    return cob


def _tickers_pelo_nome(db, cob: Cobertura) -> None:
    """Completa tickers e CNPJs pelo NOME do emissor/devedor (03/11/2026).
    No banco de produção os vínculos `company_id` dos cadastros estão quase
    vazios; sem isto a Pentágono (que só anda por ticker) não achava nada.
    Fontes: características das debêntures (todas registradas, com CNPJ),
    o cadastro ANBIMA e o originador dos CRI/CRA. Um nome por vez, com
    cache, porque o mesmo emissor aparece em dezenas de séries."""
    from sqlalchemy import select

    from ..models import Debenture, DebentureCaracteristica, Securitizado

    cache: dict[str, str | None] = {}

    def empresa(nome: str | None) -> str | None:
        if not nome:
            return None
        if nome not in cache:
            cache[nome] = cob.empresa_no_texto(nome)
        return cache[nome]

    linhas = list(db.execute(select(DebentureCaracteristica.codigo, DebentureCaracteristica.empresa,
                                    DebentureCaracteristica.cnpj, DebentureCaracteristica.situacao)))
    linhas += [(c, n, cnpj, None) for c, n, cnpj in db.execute(select(Debenture.codigo, Debenture.nome, Debenture.cnpj))]
    for codigo, nome, cnpj, situacao in linhas:
        if not codigo or (situacao or "").lower().startswith("exclu"):
            continue
        emp = empresa(nome)
        if emp:
            cob.nome_por_ticker.setdefault(codigo.strip().upper(), emp)
            if so_digitos(cnpj):
                cob.nome_por_cnpj8.setdefault(so_digitos(cnpj)[:8], emp)
    for codigo, originador in db.execute(select(Securitizado.codigo, Securitizado.originador_credito)):
        emp = empresa(originador)
        if codigo and emp:
            cob.nome_por_ticker.setdefault(codigo.strip().upper(), emp)


def get_json(url: str, tentativas: int = 4, **kwargs):
    """GET com até 3 tentativas (rede instável não pode zerar a fonte)."""
    ultimo: Exception | None = None
    for i in range(tentativas):
        try:
            return get(url, **kwargs).json()
        except Exception as e:  # noqa: BLE001
            ultimo = e
            # 429 (limite de requisições, visto na Vórtx): espera mais.
            time.sleep((6 if "429" in str(e) else 1.5) * (i + 1))
    raise ultimo  # type: ignore[misc]


def titulo(empresa: str, categoria: str, assunto: str) -> str:
    return " — ".join(p.strip() for p in (empresa, categoria, assunto) if p and p.strip())
