"""Popula o banco a partir de data/Setores.xlsx + fontes default (app/config.py).

Uso:
    python -m scripts.seed

Idempotente: pode rodar de novo a qualquer momento — usa INSERT OR IGNORE /
merge, então não duplica nem apaga edições feitas manualmente na UI (troca
de modo de fonte, keywords adicionadas etc.).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from app.auth import hash_password

from app import config
from app.db import Base, SessionLocal, engine, run_migrations
from app.models import AppSetting, Company, CompanyAlias, Sector, Source, User

XLSX_PATH = Path(__file__).resolve().parent.parent / "data" / "Setores.xlsx"

# Aliases/tickers para empresas cujo nome sozinho é ambíguo (palavra comum em
# português) ou onde o ticker ajuda a pegar mais notícias. Lista curada —
# adicione mais via UI (aba Fontes/Empresas) conforme necessário.
ALIASES: dict[str, list[str]] = {
    "Vale": ["VALE3", "Vale S.A.", "Vale mining"],
    "Rumo": ["Rumo S.A.", "RAIL3"],
    "Natura": ["Natura &Co", "NTCO3"],
    "Ultra": ["Ultrapar", "UGPA3"],
    "Auren": ["Auren Energia", "AURE3"],
    "Axia": ["Axia Energia"],
    "Cosan": ["Cosan S.A.", "CSAN3"],
    "Embraer": ["EMBR3"],
    "Petrobrás": ["Petrobras", "PETR3", "PETR4"],
    "PRIO": ["PRIO3", "PetroRio"],
    "Vibra": ["Vibra Energia", "VBBR3"],
    "Localiza": ["RENT3"],
    "Movida": ["MOVI3"],
    "Simpar": ["SIMH3"],
    "Cemig": ["CMIG3", "CMIG4"],
    "CPFL": ["CPFL Energia", "CPFE3"],
    "Eneva": ["ENEV3"],
    "Equatorial": ["Equatorial Energia", "EQTL3"],
    "Engie": ["Engie Brasil", "EGIE3"],
    "Taesa": ["TAEE11"],
    "Isa Energia": ["ISA Energia", "ISA CTEEP", "TRPL4"],
    "Neoenergia": ["NEOE3"],
    "Sabesp": ["SBSP3"],
    "Gerdau": ["GGBR4", "GOAU4"],
    "CSN": ["CSNA3", "Companhia Siderúrgica Nacional"],
    "Usiminas": ["USIM5"],
    "Nexa Resources": ["NEXA"],
    "Tupy": ["TUPY3"],
    "JBS": ["JBSS3"],
    "MBRF": ["Marfrig", "MRFG3"],
    "Minerva": ["BEEF3", "Minerva Foods"],
    "Suzano": ["SUZB3"],
    "Klabin": ["KLBN11"],
    "Irani": ["RANI3"],
    "Cyrela": ["CYRE3"],
    "MRV&Co": ["MRV", "MRVE3"],
    "Direcional": ["DIRR3"],
    "EZ Tec": ["EZTEC3"],
    "Multiplan": ["MULT3"],
    "Iguatemi": ["IGTI11"],
    "Allos": ["ALOS3"],
    "JHSF": ["JHSF3"],
    "Cogna": ["COGN3"],
    "YDUQS": ["YDUQ3"],
    "Hapvida": ["HAPV3"],
    "Hypera": ["HYPE3"],
    "DASA": ["DASA3"],
    "Rede D'Or": ["Rede DOr", "RDOR3"],
    "Oncoclínicas": ["ONCO3"],
    "Assaí": ["ASAI3"],
    "GPA": ["Grupo Pão de Açúcar", "PCAR3"],
    "Grupo Mateus": ["GMAT3"],
    "Mercado Livre": ["MELI"],
    "Hidrovias": ["Hidrovias do Brasil", "HBSA3"],
    "JSL": ["JSLG3"],
    "VLI": ["VLI Logística"],
    "EcoRodovias": ["ECOR3"],
    "Arteris": [],
    "São Martinho": ["SMTO3"],
    "Adecoagro": ["AGRO3"],
    "Zamp": ["ZAMP3"],
    "Votorantim Cimentos": ["Votorantim Cimentos S.A."],
}


def _sector_name(raw: str) -> str:
    return raw.strip()


def run() -> None:
    Base.metadata.create_all(engine)
    run_migrations()
    db = SessionLocal()
    try:
        # --- setores/empresas a partir da planilha -------------------------
        wb = openpyxl.load_workbook(XLSX_PATH, data_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        header, data_rows = rows[0], rows[1:]

        sectors_cache: dict[str, Sector] = {s.name: s for s in db.query(Sector).all()}
        companies_cache: dict[tuple[int, str], Company] = {
            (c.sector_id, c.name): c for c in db.query(Company).all()
        }

        n_sectors = n_companies = n_aliases = 0
        for row in data_rows:
            if not row or not row[0]:
                continue
            sector_raw, company_raw, analyst = (row + (None, None, None))[:3]
            sector_name = _sector_name(str(sector_raw))
            company_name = str(company_raw).strip()
            analyst_name = str(analyst).strip() if analyst else None

            sector = sectors_cache.get(sector_name)
            if sector is None:
                sector = Sector(name=sector_name)
                db.add(sector)
                db.flush()
                sectors_cache[sector_name] = sector
                n_sectors += 1

            key = (sector.id, company_name)
            company = companies_cache.get(key)
            if company is None:
                company = Company(sector_id=sector.id, name=company_name, analyst=analyst_name)
                db.add(company)
                db.flush()
                companies_cache[key] = company
                n_companies += 1
            elif analyst_name and company.analyst != analyst_name:
                company.analyst = analyst_name

            existing_aliases = {a.alias for a in company.aliases}
            for alias in ALIASES.get(company_name, []):
                if alias not in existing_aliases:
                    db.add(CompanyAlias(company_id=company.id, alias=alias))
                    n_aliases += 1

        db.commit()
        print(f"Setores: {n_sectors} novos | Empresas: {n_companies} novas | Aliases: {n_aliases} novos")

        # --- fontes default --------------------------------------------------
        # Cria as que ainda não existem. Para as que já existem, sincroniza
        # só os campos "donos do código" (url, scraper, categoria, tipo,
        # notas) -- NÃO mexe em 'enabled', porque isso é controlado pelo
        # usuário na aba "Fontes & Empresas" (ligar/desligar fonte) e
        # sobrescrever aqui reverteria a escolha dele a cada reinício.
        existing_sources = {s.name: s for s in db.query(Source).all()}
        n_sources = n_synced = 0
        SYNC_FIELDS = ("url", "scraper_module", "category", "kind", "notes")
        for src in config.KNOWN_SOURCES:
            current = existing_sources.get(src["name"])
            if current is None:
                db.add(Source(**src))
                n_sources += 1
                continue
            changed = False
            for field in SYNC_FIELDS:
                novo_valor = src.get(field)
                if novo_valor is not None and getattr(current, field) != novo_valor:
                    setattr(current, field, novo_valor)
                    changed = True
            if changed:
                n_synced += 1
        db.commit()
        print(f"Fontes: {n_sources} novas | {n_synced} atualizadas")
        if n_synced:
            print(
                "  (nota: 'enabled' de fontes existentes nunca é alterado pelo seed -- "
                "use a aba 'Fontes & Empresas' no site para ligar/desligar)"
            )

        # --- configs default ---------------------------------------------
        defaults = {
            "session_ttl_minutes": str(config.DEFAULT_SESSION_TTL_MINUTES),
            "scan_interval_minutes": str(config.SCAN_INTERVAL_MINUTES),
        }
        for k, v in defaults.items():
            if db.get(AppSetting, k) is None:
                db.add(AppSetting(key=k, value=v))
        db.commit()

        # --- admin bootstrap -------------------------------------------------
        if db.query(User).count() == 0:
            admin = User(
                name=config.BOOTSTRAP_ADMIN_NAME,
                email=config.BOOTSTRAP_ADMIN_EMAIL.lower(),
                password_hash=hash_password(config.BOOTSTRAP_ADMIN_PASSWORD),
                role="admin",
                email_confirmed=True,
                active=True,
            )
            db.add(admin)
            db.commit()
            print(
                f"Usuário admin criado: {admin.email} "
                f"(senha inicial definida em BOOTSTRAP_ADMIN_PASSWORD — troque no primeiro login)"
            )
    finally:
        db.close()


if __name__ == "__main__":
    run()
