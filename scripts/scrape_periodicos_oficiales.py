#!/usr/bin/env python3
"""
scrape_periodicos_oficiales.py
================================
Scrapea Periódicos Oficiales de los 32 estados.
Cada estado tiene su propio portal — handlers state-specific.

Uso: python3 scrape_periodicos_oficiales.py <estado_clave> [max_pubs]
Ejemplo: python3 scrape_periodicos_oficiales.py 14 50
"""
import os, sys, re, time, json, requests, urllib.parse
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
if not all([SUPABASE_URL, SERVICE_KEY]):
    print('ERROR: SUPABASE_URL/SERVICE_KEY missing')
    sys.exit(1)

HEADERS = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=ignore-duplicates'
}

ESTADOS = {
    '09': ('CDMX', 'https://data.consejeria.cdmx.gob.mx/portal_old/'),
    '15': ('Estado de México', 'https://legislacion.edomex.gob.mx/periodicooficial'),
    '14': ('Jalisco', 'https://periodicooficial.jalisco.gob.mx'),
    '19': ('Nuevo León', 'http://www.po.nl.gob.mx'),
    '30': ('Veracruz', 'https://www.editoraveracruz.gob.mx'),
    '21': ('Puebla', 'http://periodicooficial.puebla.gob.mx'),
    '11': ('Guanajuato', 'https://periodico.guanajuato.gob.mx'),
    '22': ('Querétaro', 'https://lasombradearteaga.queretaro.gob.mx'),
    '26': ('Sonora', 'https://boletinoficial.sonora.gob.mx'),
    '07': ('Chiapas', 'https://www.sgg.chiapas.gob.mx/po'),
    '13': ('Hidalgo', 'http://periodico.hidalgo.gob.mx'),
    '16': ('Michoacán de Ocampo', 'http://www.periodicooficial.michoacan.gob.mx'),
    '31': ('Yucatán', 'http://www.yucatan.gob.mx/gobierno/periodico_oficial'),
    '28': ('Tamaulipas', 'http://po.tamaulipas.gob.mx'),
    '20': ('Oaxaca', 'http://www.periodicooficial.oaxaca.gob.mx'),
}


ESTADO_CLAVE = sys.argv[1].zfill(2) if len(sys.argv) > 1 else '14'
MAX_PUBS = int(sys.argv[2]) if len(sys.argv) > 2 else 30

estado_data = ESTADOS.get(ESTADO_CLAVE)
if not estado_data:
    print(f'Estado {ESTADO_CLAVE} no soportado. Disponibles: {list(ESTADOS.keys())}')
    sys.exit(1)

ESTADO_NOMBRE, BASE_URL = estado_data
print(f'🦎 Periódico Oficial · {ESTADO_NOMBRE} · max {MAX_PUBS} publicaciones', flush=True)


def fmt_date(s):
    """Convierte fechas variables → YYYY-MM-DD"""
    if not s:
        return None
    months = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
              'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12,
              'ene':1,'feb':2,'mar':3,'abr':4,'may':5,'jun':6,'jul':7,'ago':8,'sep':9,'oct':10,'nov':11,'dic':12}
    s = s.lower().strip()
    # Patrón "15 de marzo de 2024"
    m = re.match(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', s)
    if m:
        d, mn, y = m.groups()
        if mn in months:
            return f'{y}-{months[mn]:02d}-{int(d):02d}'
    # Patrón "15/03/2024" or "15-03-2024"
    m = re.match(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', s)
    if m:
        d, mn, y = m.groups()
        return f'{y}-{int(mn):02d}-{int(d):02d}'
    # YYYY-MM-DD
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return m.group(0)
    return None


def insert_pubs(pubs):
    """Bulk insert"""
    if not pubs:
        return 0
    try:
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/periodicos_oficiales',
            headers=HEADERS, json=pubs, timeout=30
        )
        if r.ok:
            return len(pubs)
        print(f'  insert error: {r.status_code} {r.text[:200]}', flush=True)
    except Exception as e:
        print(f'  insert exc: {e}', flush=True)
    return 0


def scrape_jalisco(page):
    """Periódico Oficial Jalisco — buscar PDFs en /index.php o equivalente"""
    pubs = []
    urls = [
        f'{BASE_URL}/',
        f'{BASE_URL}/index.php',
        f'{BASE_URL}/historico',
    ]
    for url in urls:
        try:
            page.goto(url, timeout=20000, wait_until='domcontentloaded')
            time.sleep(1)
            links = page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href*=".pdf"]'))
                    .map(a => ({href: a.href, text: (a.innerText||a.title||'').trim().substring(0, 200)}))
                    .filter(l => l.href.endsWith('.pdf'))
            """)
            for l in links[:MAX_PUBS]:
                titulo = l['text'] or l['href'].split('/')[-1].replace('.pdf','').replace('_',' ')
                pubs.append({
                    'estado_clave': ESTADO_CLAVE,
                    'estado_nombre': ESTADO_NOMBRE,
                    'titulo': titulo[:500],
                    'pdf_url': l['href'][:2000],
                    'url_oficial': BASE_URL,
                    'fuente': f'Periódico Oficial {ESTADO_NOMBRE}',
                    'tipo_doc': 'edicion',
                })
            if pubs:
                break
        except Exception as e:
            print(f'  {url} error: {e}', flush=True)
    return pubs


def scrape_generic(page):
    """Generic scraper — busca PDFs en el portal raíz"""
    pubs = []
    urls = [BASE_URL]
    # Probar también /historico, /publicaciones
    for sub in ['/', '/historico', '/publicaciones', '/ediciones', '/recientes']:
        try:
            target = BASE_URL.rstrip('/') + sub
            page.goto(target, timeout=25000, wait_until='domcontentloaded')
            time.sleep(1)
            links = page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => ({href: a.href, text: (a.innerText||a.title||'').trim().substring(0, 300)}))
                    .filter(l => l.href.toLowerCase().includes('.pdf'))
            """)
            for l in links[:MAX_PUBS]:
                titulo = l['text']
                if not titulo or len(titulo) < 5:
                    titulo = urllib.parse.unquote(l['href'].split('/')[-1]).replace('.pdf','').replace('_',' ').replace('+',' ')
                # Buscar fecha en el texto
                fecha = None
                m = re.search(r'(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})', titulo)
                if m:
                    fecha = fmt_date(m.group(1))
                pubs.append({
                    'estado_clave': ESTADO_CLAVE,
                    'estado_nombre': ESTADO_NOMBRE,
                    'titulo': titulo[:500],
                    'pdf_url': l['href'][:2000],
                    'url_oficial': BASE_URL,
                    'fuente': f'Periódico Oficial {ESTADO_NOMBRE}',
                    'fecha_publicacion': fecha,
                    'tipo_doc': 'edicion',
                })
            if pubs:
                break
        except Exception as e:
            print(f'  {target} error: {e}', flush=True)
    return pubs


# Map de handlers (default: scrape_generic)
HANDLERS = {
    '14': scrape_jalisco,
}


def main():
    handler = HANDLERS.get(ESTADO_CLAVE, scrape_generic)
    pubs = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 720},
            locale='es-MX',
            ignore_https_errors=True,
        )
        page = context.new_page()
        page.set_default_timeout(25000)
        
        try:
            pubs = handler(page)
        except Exception as e:
            print(f'❌ Handler error: {e}', flush=True)
        
        browser.close()
    
    print(f'\n📥 {len(pubs)} publicaciones encontradas. Insertando...', flush=True)
    
    # Dedup interno (por pdf_url)
    seen = set()
    unique = []
    for p in pubs:
        if p['pdf_url'] not in seen:
            seen.add(p['pdf_url'])
            unique.append(p)
    
    # Bulk insert
    inserted = 0
    batch_size = 30
    for i in range(0, len(unique), batch_size):
        inserted += insert_pubs(unique[i:i+batch_size])
    
    print(f'\n═══ SUMMARY ({ESTADO_NOMBRE}) ═══', flush=True)
    print(f'  found:     {len(pubs)}', flush=True)
    print(f'  unique:    {len(unique)}', flush=True)
    print(f'  inserted:  {inserted}', flush=True)


if __name__ == '__main__':
    main()
