"""Diagnóstico temporário: o que a base da CVM tem, por instrumento e semestre."""
import collections, csv, io
from app.primario.coleta import baixar_zip, extrair_csv
from datetime import date

txt = extrair_csv(baixar_zip())
rd = csv.DictReader(io.StringIO(txt), delimiter=";")
print("COLUNAS:", rd.fieldnames)
linhas = list(rd)
print("LINHAS:", len(linhas))
c = collections.Counter((l.get("Valor_Mobiliario") or "").strip() for l in linhas)
print("VALOR_MOBILIARIO:", c.most_common())
print("TIPO_OFERTA:", collections.Counter(l.get("Tipo_Oferta") for l in linhas).most_common())
print("STATUS:", collections.Counter(l.get("Status_Requerimento") for l in linhas).most_common())
print("TIPO_REQ:", collections.Counter(l.get("Tipo_requerimento") for l in linhas).most_common(15))
MORTO = {"Registro Caducado", "Oferta Revogada", "Requerimento Expirado"}
def f(v):
    try: return float(v)
    except: return 0.0
def sem(d):
    try: d = date.fromisoformat((d or "")[:10])
    except: return None
    return f"{d.year}S{1 if d.month <= 6 else 2}"
for base in ("Data_Registro", "Data_Encerramento"):
    tab = collections.defaultdict(float); n = collections.Counter()
    for l in linhas:
        if l.get("Status_Requerimento") in MORTO: continue
        if (l.get("Tipo_Oferta") or "") == "SECUNDARIA": continue
        if base == "Data_Encerramento" and l.get("Status_Requerimento") != "Oferta Encerrada": continue
        s = sem(l.get(base))
        if s not in ("2025S1", "2026S1", "2025S2"): continue
        k = ((l.get("Valor_Mobiliario") or "").strip(), s)
        tab[k] += f(l.get("Valor_Total_Registrado")); n[k] += 1
    print(f"\n=== {base} (sem mortas, sem secundaria{', so encerradas' if 'Enc' in base else ''}) R$ bi")
    vms = sorted({k[0] for k in tab})
    tot = collections.defaultdict(float)
    for vm in vms:
        vals = [(tab[(vm, s)] / 1e9, n[(vm, s)]) for s in ("2025S1", "2025S2", "2026S1")]
        for s, (v, _) in zip(("2025S1", "2025S2", "2026S1"), vals): tot[s] += v
        print(f"{vm[:60]:60s} " + " | ".join(f"{v:8.1f} ({k})" for v, k in vals))
    print("TOTAL", {s: round(v, 1) for s, v in tot.items()})
# Secundárias e mortas no 1S, para mostrar o que sai
for rot, cond in (("SECUNDARIA", lambda l: (l.get("Tipo_Oferta") or "") == "SECUNDARIA"),
                  ("MORTAS", lambda l: l.get("Status_Requerimento") in MORTO)):
    t = collections.defaultdict(float)
    for l in linhas:
        if cond(l) and sem(l.get("Data_Registro")) in ("2025S1", "2026S1"):
            t[sem(l.get("Data_Registro"))] += f(l.get("Valor_Total_Registrado"))
    print(rot, {k: round(v / 1e9, 1) for k, v in t.items()})
# Colunas de valor que existirem
print("COLUNAS_VALOR:", [c for c in rd.fieldnames if "Valor" in c or "Qtde_Total" in c])
