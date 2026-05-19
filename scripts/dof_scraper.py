"""
DOF Scraper CI — corre en GitHub Actions, parsea RSS, inserta a Supabase.
Env vars requeridas: SUPABASE_SERVICE_ROLE
"""
import os, re, sys, json, requests
import xml.etree.ElementTree as ET
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://mhsuihwjgtzxflesbnxv.supabase.co")
SERVICE_ROLE = os.environ.get("SUPABASE_SERVICE_ROLE")
if not SERVICE_ROLE:
    print("FATAL: missing SUPABASE_SERVICE_ROLE"); sys.exit(1)

DOF_RSS = "https://dof.gob.mx/rss/sumario.xml"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml,text/xml,*/*",
    "Accept-Language": "es-MX,es;q=0.9",
}

def parse_fecha_from_url(url):
    m = re.search(r"fecha=(\d{2})/(\d{2})/(\d{4})", url)
    if not m: return None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

def main():
    print(f"Fetching {DOF_RSS}")
    r = requests.get(DOF_RSS, headers=HEADERS, timeout=30, verify=False)
    print(f"  HTTP {r.status_code}, {len(r.content):,} bytes")
    if r.status_code != 200:
        print(f"  ERROR: HTTP {r.status_code}"); sys.exit(1)

    root = ET.fromstring(r.content)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link  = (item.findtext("link") or "").strip()
        desc  = (item.findtext("description") or "").strip()
        if not title or not link: continue
        fecha = parse_fecha_from_url(link)
        codigo_match = re.search(r"codigo=(\d+)", link)
        codigo = codigo_match.group(1) if codigo_match else link[-20:]
        items.append({
            "titulo": title[:300],
            "resumen": desc[:500],
            "url_oficial": link,
            "fuente": "DOF",
            "fuente_url": "https://dof.gob.mx",
            "ambito": "nacional",
            "tema": "politica",
            "fecha_publicacion": fecha,
            "hash_url": f"dof_{codigo}",
        })
    print(f"  Parseados {len(items)} items")
    if not items:
        print("  Nothing to insert"); return

    url = f"{SUPABASE_URL}/rest/v1/noticias_civicas?on_conflict=hash_url"
    h = {
        "apikey": SERVICE_ROLE,
        "Authorization": f"Bearer {SERVICE_ROLE}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates,return=minimal",
    }
    resp = requests.post(url, headers=h, data=json.dumps(items), timeout=30)
    if resp.status_code in (200, 201, 204):
        print(f"  ✓ Inserted (or skipped duplicates): {len(items)} items")
    else:
        print(f"  ✗ HTTP {resp.status_code}: {resp.text[:300]}"); sys.exit(1)

if __name__ == "__main__":
    main()
