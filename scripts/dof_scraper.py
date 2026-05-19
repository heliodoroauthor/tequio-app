"""
DOF Scraper CI — HTML scraping del homepage del DOF.
Extrae enlaces a nota_detalle.php?codigo=NNN&fecha=DD/MM/YYYY del HTML.
"""
import os, re, sys, json, requests
from bs4 import BeautifulSoup
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://mhsuihwjgtzxflesbnxv.supabase.co")
SERVICE_ROLE = os.environ.get("SUPABASE_SERVICE_ROLE")
if not SERVICE_ROLE:
    print("FATAL: missing SUPABASE_SERVICE_ROLE"); sys.exit(1)

# URLs candidatas — probamos en orden, usamos la primera que devuelva 200 con enlaces
DOF_URLS = [
    "https://dof.gob.mx/",
    "https://dof.gob.mx/index.php",
    "https://www.dof.gob.mx/",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9",
}

def fetch_dof_homepage():
    for url in DOF_URLS:
        print(f"Trying {url}")
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, verify=False, allow_redirects=True)
            print(f"  HTTP {r.status_code}, {len(r.text):,} bytes, final URL: {r.url}")
            if r.status_code == 200 and "nota_detalle" in r.text:
                return r.text, r.url
        except Exception as e:
            print(f"  Error: {e}")
    return None, None

def main():
    html, source_url = fetch_dof_homepage()
    if not html:
        print("FATAL: No DOF URL devolvió contenido válido")
        sys.exit(1)

    soup = BeautifulSoup(html, "html.parser")
    # Buscar todos los enlaces a nota_detalle.php
    enlaces = soup.find_all("a", href=re.compile(r"nota_detalle\.php\?codigo="))
    print(f"  Encontrados {len(enlaces)} enlaces a nota_detalle")

    items = []
    seen = set()
    for a in enlaces:
        href = a.get("href", "")
        # Normalizar a URL absoluta
        if href.startswith("/"):
            href = "https://dof.gob.mx" + href
        elif href.startswith("nota_detalle"):
            href = "https://dof.gob.mx/" + href
        elif not href.startswith("http"):
            continue

        # Extraer codigo y fecha del URL
        codigo_match = re.search(r"codigo=(\d+)", href)
        fecha_match = re.search(r"fecha=(\d{2})/(\d{2})/(\d{4})", href)
        if not codigo_match or not fecha_match:
            continue
        codigo = codigo_match.group(1)
        if codigo in seen:
            continue
        seen.add(codigo)
        fecha = f"{fecha_match.group(3)}-{fecha_match.group(2)}-{fecha_match.group(1)}"

        # Título: usar texto del anchor, o título cercano
        titulo = a.get_text(separator=" ", strip=True)
        if not titulo or len(titulo) < 5:
            # Buscar texto del padre
            parent = a.parent
            if parent: titulo = parent.get_text(separator=" ", strip=True)
        titulo = re.sub(r"\s+", " ", titulo).strip()
        if not titulo or len(titulo) < 5:
            titulo = f"DOF · {fecha}"
        titulo = titulo[:300]

        items.append({
            "titulo": titulo,
            "resumen": titulo[:500],
            "url_oficial": href,
            "fuente": "DOF",
            "fuente_url": "https://dof.gob.mx",
            "ambito": "nacional",
            "tema": "politica",
            "fecha_publicacion": fecha,
            "hash_url": f"dof_{codigo}",
        })

    print(f"  Parseados {len(items)} items únicos")
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
