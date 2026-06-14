#!/usr/bin/env python3
"""
find_urls_compendios.py - resuelve URLs de leyes scrapeando compendios oficiales.

v2 (15-jun-2026): rediseno de find_urls_leyes.py (v1 fallido por DDG bot-block).
Cada estado tiene su propio sitio del Congreso con un compendio de leyes
vigentes con PDFs reales. Scrapear UNO de esos por estado es mas confiable
que buscar.

Patron registry: una funcion por estado que devuelve list de (nombre, url).
Match por lower(unaccent(name)) contra tabla leyes WHERE url IS NULL.

Estado actual:
  - Nuevo Leon: 198 leyes pendientes - implementado
  - Aguascalientes, Jalisco, QR, Guerrero, Veracruz: TODO (sitios complejos,
    JS-rendered o requieren OCR)

Env vars: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Args: --state="Nuevo Leon" (default all), --dry-run
"""
import os, sys, re, unicodedata, time, requests
from bs4 import BeautifulSoup

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

if not (SUPABASE_URL and SERVICE_KEY):
    print("FATAL: SUPABASE vars missing", flush=True); sys.exit(1)

DRY_RUN = '--dry-run' in sys.argv
ONLY_STATE = None
for a in sys.argv[1:]:
    if a.startswith('--state='):
        ONLY_STATE = a.split('=', 1)[1].strip()

HEADERS = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


def norm(s):
    """Normalizar string para match: lower, sin acentos, espacios colapsados."""
    if not s: return ''
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'\s+', ' ', s.lower()).strip()
    return s


def http_get(url, timeout=20):
    return requests.get(url, headers={'User-Agent': UA}, timeout=timeout, verify=False).text


# ============================================================================
# STATE SCRAPERS
# ============================================================================

def scrape_nuevo_leon():
    """HCNL.gob.mx/trabajo_legislativo/leyes/ - lista todas las leyes con links
    al detalle. El detalle tiene el PDF del texto original."""
    BASE = 'https://www.hcnl.gob.mx/trabajo_legislativo/leyes/'
    html = http_get(BASE)
    soup = BeautifulSoup(html, 'html.parser')

    out = []
    for a in soup.find_all('a', href=True):
        text = a.get_text(strip=True)
        href = a['href']
        # Filtrar solo links que vayan a detalles de ley: /trabajo_legislativo/leyes/leyes/<slug>/
        if '/trabajo_legislativo/leyes/leyes/' in href and text and len(text) > 12:
            # Limpiar trailing "TEXTO ORIGINAL" y otros sufijos
            nombre = re.sub(r'\s+TEXTO\s+ORIGINAL\s*$', '', text, flags=re.I)
            nombre = re.sub(r'\s+', ' ', nombre).strip()
            # Hacer URL absoluta si es relativa
            if href.startswith('http'):
                url = href
            else:
                url = 'https://www.hcnl.gob.mx' + (href if href.startswith('/') else '/' + href)
            out.append((nombre, url))

    # Deduplicar por nombre (keep primero)
    seen = set()
    dedup = []
    for nombre, url in out:
        k = norm(nombre)
        if k not in seen:
            seen.add(k)
            dedup.append((nombre, url))
    return dedup


def scrape_quintana_roo():
    """congresoqroo.gob.mx/leyes/ - index con 362 links tipo /leyes/<id>/.
    Cada link es el detalle de una ley. Usamos /leyes/<id>/ como URL final
    (esa pagina del Congreso lleva al PDF y es URL oficial estable)."""
    BASE = 'https://www.congresoqroo.gob.mx/leyes/'
    html = http_get(BASE)
    soup = BeautifulSoup(html, 'html.parser')

    out = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        text = a.get_text(strip=True)
        # Solo links a /leyes/<id>/ con nombre real (no #anchors, no exportar)
        if re.match(r'^/leyes/\d+/?$', href) and text and len(text) > 15:
            # Quitar punto final
            nombre = text.rstrip('.').strip()
            url_full = 'https://www.congresoqroo.gob.mx' + href
            out.append((nombre, url_full))

    seen = set()
    dedup = []
    for nombre, url in out:
        k = norm(nombre)
        if k not in seen:
            seen.add(k)
            dedup.append((nombre, url))
    return dedup


# TODO: estados con Cloudflare bot-block (AGS, JAL, GUE, VER, COA, SIN).
# Necesitan Playwright/Selenium con headless browser + residential proxy.
# Marcado para post-launch con infra dedicada.
# def scrape_aguascalientes(): pass
# def scrape_jalisco(): pass
# def scrape_guerrero(): pass
# def scrape_veracruz(): pass
# def scrape_coahuila(): pass
# def scrape_sinaloa(): pass

STATE_SCRAPERS = {
    'Nuevo Leon': scrape_nuevo_leon,
    'Quintana Roo': scrape_quintana_roo,
}


# ============================================================================
# MATCHER + UPDATER
# ============================================================================

def fetch_pendientes_estado(entidad):
    url = f"{SUPABASE_URL}/rest/v1/leyes?url=is.null&entidad=eq.{requests.utils.quote(entidad)}&select=id,nombre&order=id.asc&limit=500"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def patch_url(lid, new_url):
    if DRY_RUN:
        print(f'  [DRY] id={lid} → {new_url[:120]}', flush=True)
        return True
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/leyes?id=eq.{lid}",
        headers={**HEADERS, 'Prefer': 'return=minimal'},
        json={'url': new_url},
        timeout=30
    )
    return r.ok


def process_state(estado, scraper):
    print(f'\n=== {estado} ===', flush=True)
    try:
        compendio = scraper()
    except Exception as e:
        print(f'  ERROR scrape compendio: {e}', flush=True)
        return (0, 0, 0)
    print(f'  Compendio: {len(compendio)} leyes encontradas', flush=True)
    if not compendio:
        return (0, 0, 0)

    # Build lookup table by normalized name
    lookup = {}
    for nombre, url in compendio:
        lookup[norm(nombre)] = (nombre, url)

    pendientes = fetch_pendientes_estado(estado)
    print(f'  Pendientes DB: {len(pendientes)} leyes sin URL', flush=True)

    matched = updated = no_match = 0
    for row in pendientes:
        n = norm(row['nombre'])
        if n in lookup:
            _, url = lookup[n]
            matched += 1
            if patch_url(row['id'], url):
                updated += 1
                print(f'  ✓ id={row["id"]} → {url[:80]}', flush=True)
            else:
                print(f'  PATCH fail id={row["id"]}', flush=True)
        else:
            no_match += 1

    print(f'  matched={matched} updated={updated} no_match={no_match}', flush=True)
    return (matched, updated, no_match)


def main():
    total_matched = total_updated = total_no_match = 0
    states = [ONLY_STATE] if ONLY_STATE else list(STATE_SCRAPERS.keys())

    for estado in states:
        if estado not in STATE_SCRAPERS:
            print(f'WARN: sin scraper para "{estado}"', flush=True)
            continue
        m, u, nm = process_state(estado, STATE_SCRAPERS[estado])
        total_matched += m
        total_updated += u
        total_no_match += nm
        time.sleep(2)  # gentle entre estados

    print(f'\n=== SUMMARY estados={len(states)} matched={total_matched} updated={total_updated} no_match={total_no_match} ===', flush=True)


if __name__ == '__main__':
    import urllib3
    urllib3.disable_warnings()
    main()
