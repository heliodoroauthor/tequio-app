"""COFEPRIS Scraper CI."""
import os, re, sys, json, requests
from bs4 import BeautifulSoup

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://mhsuihwjgtzxflesbnxv.supabase.co")
SERVICE_ROLE = os.environ.get("SUPABASE_SERVICE_ROLE")
if not SERVICE_ROLE: print("FATAL: missing SUPABASE_SERVICE_ROLE"); sys.exit(1)

URL = "https://www.gob.mx/cofepris"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html",
    "Accept-Language": "es-MX,es;q=0.9",
}
MESES = {"enero":"01","febrero":"02","marzo":"03","abril":"04","mayo":"05","junio":"06",
         "julio":"07","agosto":"08","septiembre":"09","octubre":"10","noviembre":"11","diciembre":"12"}

def main():
    print(f"Fetching {URL}")
    r = requests.get(URL, headers=HEADERS, timeout=30)
    print(f"  HTTP {r.status_code}, {len(r.text):,} bytes")
    if r.status_code != 200:
        print(f"  ERROR: HTTP {r.status_code}"); sys.exit(1)
    soup = BeautifulSoup(r.text, "html.parser")
    links = soup.find_all("a", href=re.compile(r"/cofepris/(?:es/)?articulos/"))
    items = []
    seen = set()
    for a in links:
        href = a.get("href", "")
        if href.startswith("/"): href = "https://www.gob.mx" + href
        href = href.split("?")[0]
        if href in seen: continue
        seen.add(href)
        text = a.get_text(separator=" ", strip=True)
        if not text or len(text) < 30:
            parent = a.parent
            if parent: text = parent.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s*Continuar leyendo\s*$", "", text)
        m = re.search(r"(\d{1,2}) de ([a-záéíóú]+) de (\d{4})", text, re.IGNORECASE)
        if not m: continue
        mes = MESES.get(m.group(2).lower())
        if not mes: continue
        fecha = f"{m.group(3)}-{mes}-{m.group(1).zfill(2)}"
        titulo = text[m.end():].strip()
        if not titulo or len(titulo) < 10: continue
        slug = href.rstrip("/").split("/")[-1].split("?")[0]
        is_alerta = bool(re.search(r"alerta|riesgo|decomis|retir|falsific|contamin|brote|intoxic", titulo, re.IGNORECASE))
        items.append({
            "titulo": ("🚨 Alerta sanitaria" if is_alerta else "🏥 Comunicado COFEPRIS"),
            "resumen": titulo[:500],
            "url_oficial": href,
            "fuente": "COFEPRIS",
            "fuente_url": URL,
            "ambito": "nacional",
            "tema": "salud",
            "fecha_publicacion": fecha,
            "hash_url": f"cofepris_{re.sub(chr(92)+'W+', '_', slug)[:60]}",
        })
    print(f"  Parseados {len(items)} items")
    if not items:
        print("  Nothing to insert"); return
    api = f"{SUPABASE_URL}/rest/v1/noticias_civicas?on_conflict=hash_url"
    h = {"apikey": SERVICE_ROLE, "Authorization": f"Bearer {SERVICE_ROLE}",
         "Content-Type": "application/json", "Prefer": "resolution=ignore-duplicates,return=minimal"}
    resp = requests.post(api, headers=h, data=json.dumps(items), timeout=30)
    if resp.status_code in (200, 201, 204):
        print(f"  ✓ Inserted: {len(items)}")
    else:
        print(f"  ✗ HTTP {resp.status_code}: {resp.text[:300]}"); sys.exit(1)

if __name__ == "__main__":
    main()
