"""Diagnóstico temporário (2): mês a mês 2026 e rito."""
import collections, csv, io
from app.primario.coleta import baixar_zip, extrair_csv
from datetime import date
txt = extrair_csv(baixar_zip())
linhas = list(csv.DictReader(io.StringIO(txt), delimiter=";"))
print("RITO:", collections.Counter(l.get("Rito_Requerimento") for l in linhas).most_common())
MORTO = {"Registro Caducado", "Oferta Revogada", "Requerimento Expirado"}
def f(v):
    try: return float(v)
    except: return 0.0
for vm in ("Debêntures", "Notas Comerciais", "Cotas de FIDC", "Certificados de Recebíveis Imobiliários"):
  for base in ("Data_Encerramento", "Data_Registro"):
    t = collections.defaultdict(float); n = collections.Counter()
    for l in linhas:
        if (l.get("Valor_Mobiliario") or "").strip() != vm: continue
        if l.get("Status_Requerimento") in MORTO or l.get("Tipo_Oferta") == "SECUNDARIA": continue
        d = (l.get(base) or "")[:7]
        if d.startswith("2026-0") and d <= "2026-06" or d.startswith("2025-0") and d <= "2025-06":
            t[d] += f(l.get("Valor_Total_Registrado")); n[d] += 1
    print(vm[:20], base, " ".join(f"{m[2:]}:{t[m]/1e9:.1f}/{n[m]}" for m in sorted(t)))
# debêntures 1S26 com status ainda aberto (registradas, sem encerramento)
ab = [l for l in linhas if l.get("Valor_Mobiliario") == "Debêntures" and l.get("Status_Requerimento") not in MORTO and l.get("Status_Requerimento") != "Oferta Encerrada" and (l.get("Data_Registro") or "")[:7] <= "2026-06" and (l.get("Data_Registro") or "") >= "2026-01"]
print("DEB 1S26 registradas e ainda abertas:", len(ab), round(sum(f(l.get("Valor_Total_Registrado")) for l in ab)/1e9, 1))
