"""FGR Scraper CI."""
import os, re, sys, json, requests
from bs4 import BeautifulSoup

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://mhsuihwjgtzxflesbnxv.supabase.co")
SERVICE_ROLE = os.environ.get("SUPABASE_SERVICE_ROLE")
if not SERVICE_ROLE: print("FATAL: missing SUPABASE_SERVICE_ROLE"); sys.exit(1)

FGR_URL = "https://www.fgr.org.mx/es/FGR/Comunicados"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html",
    "Accept-Language": "es-MX,es;q=0.9",
}
MESES = {"enero":"01","febrero":"02","marzo":"03","abril":"04","mayo":"05","junio":"06",
         "julio":"07","agosto":"08","septiembre":"09","octubre":"10","noviembre":"11","diciembre":"12"}

def main():
    print(f"Fetching {FGR_URL}")
    r = requests.get(FGR_URL, headers=HEADERS, timeout=30)
    print(f"  HTTP {r.status_code}, {len(r.text):,} bytes")
    if r.status_code != 200:
        print(f"  ERROR: HTTP {r.status_code}"); sys.exit(1)
    text = re.sub(r"\s+", " ", r.text)
    pattern = re.compile(r"(Nacional|Estatal)\s+(\d{1,2}) de ([a-záéíóú]+) de (\d{4})\s+Comunicado FGR (\d+)\s+([^<]{20,400})", re.IGNORECASE)
    items = []
    for m in pattern.finditer(text):
        ambito, dd, mes_name, yyyy, num, titulo = m.groups()
        mes = MESES.get(mes_name.lower())
        if not mes: continue
        fecha = f"{yyyy}-{mes}-{dd.zfill(2)}"
        items.append({
            "titulo": f"FGR DPE/{num}/{yyyy[-2:]} · Nacional",
            "resumen": titulo.strip()[:500],
            "url_oficial": FGR_URL,
            "fuente": "FGR",
            "fuente_url": "https://www.fgr.org.mx",
            "ambito": "nacional" if ambito.lower() == "nacional" else "estatal",
            "tema": "seguridad",
            "fecha_publicacion": fecha,
            "hash_url": f"fgr_{num}_{yyyy}",
        })
    print(f"  Parseados {len(items)} items")
    if not items:
        print("  Nothing to insert"); return
    url = f"{SUPABASE_URL}/rest/v1/noticias_civicas?on_conflict=hash_url"
    h = {"apikey": SERVICE_ROLE, "Authorization": f"Bearer {SERVICE_ROLE}",
         "Content-Type": "application/json", "Prefer": "resolution=ignore-duplicates,return=minimal"}
    resp = requests.post(url, headers=h, data=json.dumps(items), timeout=30)
    if resp.status_code in (200, 201, 204):
        print(f"  ✓ Inserted: {len(items)}")
    else:
        print(f"  ✗ HTTP {resp.status_code}: {resp.text[:300]}"); sys.exit(1)

if __name__ == "__main__":
    main()
