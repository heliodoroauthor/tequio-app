#!/usr/bin/env python3
"""
scrape_periodicos_oficiales.py · v2
====================================
v2 cambios:
  - URLs actualizadas (CDMX, NL, EdoMex usaban URLs muertas/viejas)
  - Handlers custom para CDMX, NL, EdoMex, Jalisco (top-4 estados)
  - Timeout 45s (gov.mx es LENTO)
  - wait_until='networkidle' para SPAs
  - Date parsing en el título del PDF
"""
import os, sys, re, time, json, requests, urllib.parse
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
if not all([SUPABASE_URL, SERVICE_KEY]):
    print('ERROR: SUPABASE_URL/SERVICE_KEY missing', flush=True)
    sys.exit(1)

HEADERS = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=ignore-duplicates'
}

# Catálogo URLs (v2)
ESTADOS = {
    '01': ('Aguascalientes', 'https://eservicios2.aguascalientes.gob.mx/PeriodicoOficial'),
    '02': ('Baja California', 'https://www.bajacalifornia.gob.mx/portal/gobierno/poebc/poebc.jsp'),
    '03': ('Baja California Sur', 'https://secfin.bcs.gob.mx/poebcs'),
    '04': ('Campeche', 'https://www.gob.mx/admincamp/articulos/periodico-oficial-del-estado'),
    '05': ('Coahuila', 'https://periodico.sfpcoahuila.gob.mx'),
    '06': ('Colima', 'https://periodicooficial.col.gob.mx'),
    '07': ('Chiapas', 'https://www.sgg.chiapas.gob.mx/po'),
    '08': ('Chihuahua', 'https://www.chihuahua.gob.mx/poegoe'),
    # CDMX: URL nueva (data.consejeria + index.php/gaceta)
    '09': ('CDMX', 'https://data.consejeria.cdmx.gob.mx/index.php/gaceta'),
    '10': ('Durango', 'https://www.gob.mx/durango/articulos/periodico-oficial'),
    '11': ('Guanajuato', 'https://periodico.guanajuato.gob.mx'),
    '12': ('Guerrero', 'https://periodicooficial.guerrero.gob.mx'),
    '13': ('Hidalgo', 'http://periodico.hidalgo.gob.mx'),
    # Jalisco: handler custom (secciones dinámicas)
    '14': ('Jalisco', 'https://periodicooficial.jalisco.gob.mx'),
    # EdoMex: agregar /ve_periodico_oficial (path correcto v2026)
    '15': ('Estado de México', 'https://legislacion.edomex.gob.mx/ve_periodico_oficial'),
    '16': ('Michoacán de Ocampo', 'http://www.periodicooficial.michoacan.gob.mx'),
    '17': ('Morelos', 'http://periodico.morelos.gob.mx'),
    '18': ('Nayarit', 'http://periodicooficial.nayarit.gob.mx'),
    # NL: po.nl.gob.mx MUERTO → usar HCNL
    '19': ('Nuevo León', 'https://www.hcnl.gob.mx/archivo/periodico-oficial/'),
    '20': ('Oaxaca', 'http://www.periodicooficial.oaxaca.gob.mx'),
    '21': ('Puebla', 'http://periodicooficial.puebla.gob.mx'),
    '22': ('Querétaro', 'https://lasombradearteaga.queretaro.gob.mx'),
    '23': ('Quintana Roo', 'https://po.segob.qroo.gob.mx'),
    '24': ('San Luis Potosí', 'http://sgg.slp.gob.mx/PeriodicoOficial.nsf'),
    '25': ('Sinaloa', 'https://laipsinaloa.gob.mx/po'),
    '26': ('Sonora', 'https://boletinoficial.sonora.gob.mx'),
    '27': ('Tabasco', 'https://tabasco.gob.mx/PeriodicoOficial'),
    '28': ('Tamaulipas', 'http://po.tamaulipas.gob.mx'),
    '29': ('Tlaxcala', 'https://periodico.tlaxcala.gob.mx'),
    '30': ('Veracruz de Ignacio de la Llave', 'https://www.editoraveracruz.gob.mx'),
    '31': ('Yucatán', 'http://www.yucatan.gob.mx/gobierno/periodico_oficial'),
    '32': ('Zacatecas', 'https://periodicooficial.zacatecas.gob.mx'),
}

ESTADO_CLAVE = sys.argv[1].zfill(2) if len(sys.argv) > 1 else '14'
MAX_PUBS = int(sys.argv[2]) if len(sys.argv) > 2 else 30

estado_data = ESTADOS.get(ESTADO_CLAVE)
if not estado_data:
    print(f'Estado {ESTADO_CLAVE} no soportado.', flush=True)
    sys.exit(1)

ESTADO_NOMBRE, BASE_URL = estado_data
print(f'🦎 Periódico Oficial · {ESTADO_NOMBRE} · max {MAX_PUBS} publicaciones', flush=True)
print(f'   URL: {BASE_URL}', flush=True)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────
def fmt_date(s):
    if not s:
        return None
    months = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
              'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12,
              'ene':1,'feb':2,'mar':3,'abr':4,'may':5,'jun':6,'jul':7,'ago':8,'sep':9,'oct':10,'nov':11,'dic':12}
    s = s.lower().strip()
    m = re.match(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', s)
    if m:
        d, mn, y = m.groups()
        if mn in months:
            return f'{y}-{months[mn]:02d}-{int(d):02d}'
    m = re.match(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', s)
    if m:
        d, mn, y = m.groups()
        return f'{y}-{int(mn):02d}-{int(d):02d}'
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return m.group(0)
    return None


def extract_date_from_text(text):
    """Busca fecha en cualquier parte del texto/URL"""
    if not text:
        return None
    # "15 de marzo de 2026"
    m = re.search(r'(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})', text, re.IGNORECASE)
    if m:
        return fmt_date(m.group(1))
    # "2026-06-03"
    m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if m:
        return m.group(1)
    # "03/06/2026"
    m = re.search(r'(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})', text)
    if m:
        return fmt_date(m.group(1))
    # En URL: gct/2026/junio/jun030/
    m = re.search(r'/(\d{4})/(\w+)/(\w+)(\d{1,2})', text)
    if m:
        y, mn, _, d = m.groups()
        months = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
                  'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12,
                  'ene':1,'feb':2,'mar':3,'abr':4,'may':5,'jun':6,'jul':7,'ago':8,'sep':9,'oct':10,'nov':11,'dic':12}
        if mn.lower() in months:
            return f'{y}-{months[mn.lower()]:02d}-{int(d):02d}'
    return None


def insert_pubs(pubs):
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


def safe_goto(page, url, timeout=45000, wait='domcontentloaded'):
    """goto con retry, timeout largo, fallback de wait_until"""
    try:
        page.goto(url, timeout=timeout, wait_until=wait)
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f'  goto fail ({wait}) {url}: {str(e)[:100]}', flush=True)
        # Reintento con load (más permisivo)
        try:
            page.goto(url, timeout=timeout, wait_until='load')
            time.sleep(2)
            return True
        except Exception as e2:
            print(f'  goto fail (load) {url}: {str(e2)[:100]}', flush=True)
            return False


# ──────────────────────────────────────────────────────────────
# Handlers
# ──────────────────────────────────────────────────────────────
def scrape_cdmx(page):
    """CDMX · data.consejeria.cdmx.gob.mx · index.php/gaceta"""
    pubs = []
    if not safe_goto(page, BASE_URL, timeout=45000, wait='networkidle'):
        return pubs

    links = page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
            .map(a => ({href: a.href, text: (a.innerText||a.title||'').trim().substring(0,300)}))
            .filter(l => l.href.toLowerCase().includes('.pdf') || l.href.includes('uploads/gacetas/'))
    """)
    print(f'  CDMX: {len(links)} links candidatos', flush=True)

    for l in links[:MAX_PUBS]:
        titulo = l['text']
        if not titulo or len(titulo) < 5:
            titulo = urllib.parse.unquote(l['href'].split('/')[-1]).replace('.pdf','')
        fecha = extract_date_from_text(titulo) or extract_date_from_text(l['href'])
        pubs.append({
            'estado_clave': ESTADO_CLAVE,
            'estado_nombre': ESTADO_NOMBRE,
            'titulo': titulo[:500],
            'pdf_url': l['href'][:2000],
            'url_oficial': BASE_URL,
            'fuente': f'Gaceta Oficial CDMX',
            'fecha_publicacion': fecha,
            'tipo_doc': 'gaceta',
        })
    return pubs


def scrape_nl(page):
    """NL · HCNL Congreso Estatal Archive"""
    pubs = []
    if not safe_goto(page, BASE_URL, timeout=45000, wait='networkidle'):
        return pubs

    # Buscar TODO link que sea PDF o tenga año/fecha
    links = page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
            .map(a => ({href: a.href, text: (a.innerText||a.title||'').trim().substring(0,300)}))
            .filter(l =>
                l.href.toLowerCase().includes('.pdf') ||
                (l.text && /\\d{4}/.test(l.text) && (l.href.includes('periodico') || l.href.includes('archivo')))
            )
    """)
    print(f'  NL: {len(links)} links candidatos', flush=True)

    for l in links[:MAX_PUBS]:
        titulo = l['text'] or urllib.parse.unquote(l['href'].split('/')[-1])
        fecha = extract_date_from_text(titulo) or extract_date_from_text(l['href'])
        pubs.append({
            'estado_clave': ESTADO_CLAVE,
            'estado_nombre': ESTADO_NOMBRE,
            'titulo': titulo[:500],
            'pdf_url': l['href'][:2000],
            'url_oficial': BASE_URL,
            'fuente': f'Periódico Oficial Nuevo León (HCNL)',
            'fecha_publicacion': fecha,
            'tipo_doc': 'edicion',
        })
    return pubs


def scrape_edomex(page):
    """EdoMex · legislacion.edomex.gob.mx/ve_periodico_oficial"""
    pubs = []
    urls = [
        BASE_URL,                                     # /ve_periodico_oficial
        'https://legislacion.edomex.gob.mx/',         # root LEGISTEL
    ]
    for url in urls:
        if not safe_goto(page, url, timeout=45000, wait='networkidle'):
            continue
        links = page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({href: a.href, text: (a.innerText||a.title||'').trim().substring(0,300)}))
                .filter(l => l.href.toLowerCase().includes('.pdf') || l.href.includes('/gct/'))
        """)
        print(f'  EdoMex {url}: {len(links)} links candidatos', flush=True)
        if not links:
            continue
        for l in links[:MAX_PUBS]:
            titulo = l['text']
            if not titulo or len(titulo) < 5:
                titulo = urllib.parse.unquote(l['href'].split('/')[-1]).replace('.pdf','')
            fecha = extract_date_from_text(titulo) or extract_date_from_text(l['href'])
            pubs.append({
                'estado_clave': ESTADO_CLAVE,
                'estado_nombre': ESTADO_NOMBRE,
                'titulo': titulo[:500],
                'pdf_url': l['href'][:2000],
                'url_oficial': BASE_URL,
                'fuente': f'Gaceta del Gobierno EdoMex',
                'fecha_publicacion': fecha,
                'tipo_doc': 'gaceta',
            })
        if pubs:
            break
    return pubs


def scrape_jalisco(page):
    """Jalisco · seccion/periodico/N + apiperiodico"""
    pubs = []
    # Estrategia 1: root tiene últimas ediciones linkadas
    if not safe_goto(page, BASE_URL, timeout=45000, wait='networkidle'):
        return pubs

    # Buscar links a /seccion/periodico/N
    nav_links = page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
            .map(a => ({href: a.href, text: (a.innerText||'').trim().substring(0,300)}))
            .filter(l => /seccion\\/periodico\\/\\d+/.test(l.href) || l.href.toLowerCase().includes('.pdf'))
    """)
    print(f'  Jalisco root: {len(nav_links)} links candidatos', flush=True)

    # Si hay PDFs directos en root, usarlos
    pdf_links = [l for l in nav_links if l['href'].lower().endswith('.pdf')]
    if pdf_links:
        for l in pdf_links[:MAX_PUBS]:
            titulo = l['text'] or urllib.parse.unquote(l['href'].split('/')[-1])
            fecha = extract_date_from_text(titulo) or extract_date_from_text(l['href'])
            pubs.append({
                'estado_clave': ESTADO_CLAVE,
                'estado_nombre': ESTADO_NOMBRE,
                'titulo': titulo[:500],
                'pdf_url': l['href'][:2000],
                'url_oficial': BASE_URL,
                'fuente': 'Periódico Oficial Jalisco',
                'fecha_publicacion': fecha,
                'tipo_doc': 'edicion',
            })
        return pubs

    # Si no, navegar a /seccion/periodico links y extraer PDFs ahí
    seccion_links = [l for l in nav_links if 'seccion/periodico' in l['href']][:5]
    for sl in seccion_links:
        if not safe_goto(page, sl['href'], timeout=40000, wait='networkidle'):
            continue
        sub_pdfs = page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({href: a.href, text: (a.innerText||a.title||'').trim().substring(0,300)}))
                .filter(l => l.href.toLowerCase().includes('.pdf'))
        """)
        for l in sub_pdfs[:MAX_PUBS]:
            titulo = l['text'] or sl['text'] or urllib.parse.unquote(l['href'].split('/')[-1])
            fecha = extract_date_from_text(titulo) or extract_date_from_text(l['href'])
            pubs.append({
                'estado_clave': ESTADO_CLAVE,
                'estado_nombre': ESTADO_NOMBRE,
                'titulo': titulo[:500],
                'pdf_url': l['href'][:2000],
                'url_oficial': BASE_URL,
                'fuente': 'Periódico Oficial Jalisco',
                'fecha_publicacion': fecha,
                'tipo_doc': 'edicion',
            })
            if len(pubs) >= MAX_PUBS:
                return pubs
    return pubs


def scrape_generic(page):
    """Default · busca PDFs en root + 4 paths comunes"""
    pubs = []
    paths = ['/', '/historico', '/publicaciones', '/ediciones', '/recientes']
    for sub in paths:
        target = BASE_URL.rstrip('/') + sub
        if not safe_goto(page, target, timeout=45000, wait='domcontentloaded'):
            continue
        links = page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({href: a.href, text: (a.innerText||a.title||'').trim().substring(0,300)}))
                .filter(l => l.href.toLowerCase().includes('.pdf'))
        """)
        if not links:
            continue
        for l in links[:MAX_PUBS]:
            titulo = l['text']
            if not titulo or len(titulo) < 5:
                titulo = urllib.parse.unquote(l['href'].split('/')[-1]).replace('.pdf','').replace('_',' ').replace('+',' ')
            fecha = extract_date_from_text(titulo) or extract_date_from_text(l['href'])
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
    return pubs


HANDLERS = {
    '09': scrape_cdmx,
    '14': scrape_jalisco,
    '15': scrape_edomex,
    '19': scrape_nl,
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
        page.set_default_timeout(45000)
        try:
            pubs = handler(page)
        except Exception as e:
            print(f'❌ Handler error: {e}', flush=True)
        browser.close()

    print(f'\n📥 {len(pubs)} publicaciones encontradas. Insertando...', flush=True)

    seen = set()
    unique = []
    for p in pubs:
        if p['pdf_url'] not in seen:
            seen.add(p['pdf_url'])
            unique.append(p)

    inserted = 0
    batch_size = 30
    for i in range(0, len(unique), batch_size):
        inserted += insert_pubs(unique[i:i+batch_size])

    print(f'\n═══ SUMMARY ({ESTADO_NOMBRE}) ═══', flush=True)
    print(f'  found:     {len(pubs)}', flush=True)
    print(f'  unique:    {len(unique)}', flush=True)
    print(f'  inserted:  {inserted}', flush=True)
    print(f'  with date: {sum(1 for u in unique if u.get("fecha_publicacion"))}/{len(unique)}', flush=True)


if __name__ == '__main__':
    main()
