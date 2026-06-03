#!/usr/bin/env python3
"""
scrape_periodicos_oficiales.py · v3
====================================
v3 cambios sobre v2:
  - CDMX: probar consejeria.cdmx.gob.mx (sin "data.") + Wayback fallback
  - EdoMex: ENUMERAR PDFs por URL pattern (bypassa el portal roto)
  - Jalisco: enumerar /seccion/periodico/<id> recientes (PDFs reales)
  - Resto: handlers v2 sin cambios
"""
import os, sys, re, time, json, requests, urllib.parse
from datetime import datetime, timedelta
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

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

ESTADOS = {
    '01': ('Aguascalientes', 'https://eservicios2.aguascalientes.gob.mx/PeriodicoOficial'),
    '02': ('Baja California', 'https://www.bajacalifornia.gob.mx/portal/gobierno/poebc/poebc.jsp'),
    '03': ('Baja California Sur', 'https://secfin.bcs.gob.mx/poebcs'),
    '04': ('Campeche', 'https://www.gob.mx/admincamp/articulos/periodico-oficial-del-estado'),
    '05': ('Coahuila', 'https://periodico.sfpcoahuila.gob.mx'),
    '06': ('Colima', 'https://periodicooficial.col.gob.mx'),
    '07': ('Chiapas', 'https://www.sgg.chiapas.gob.mx/po'),
    '08': ('Chihuahua', 'https://www.chihuahua.gob.mx/poegoe'),
    '09': ('CDMX', 'https://consejeria.cdmx.gob.mx/gaceta-oficial'),  # v3: sin "data."
    '10': ('Durango', 'https://www.gob.mx/durango/articulos/periodico-oficial'),
    '11': ('Guanajuato', 'https://periodico.guanajuato.gob.mx'),
    '12': ('Guerrero', 'https://periodicooficial.guerrero.gob.mx'),
    '13': ('Hidalgo', 'http://periodico.hidalgo.gob.mx'),
    '14': ('Jalisco', 'https://periodicooficial.jalisco.gob.mx'),
    '15': ('Estado de México', 'https://legislacion.edomex.gob.mx/ve_periodico_oficial'),
    '16': ('Michoacán de Ocampo', 'http://www.periodicooficial.michoacan.gob.mx'),
    '17': ('Morelos', 'http://periodico.morelos.gob.mx'),
    '18': ('Nayarit', 'http://periodicooficial.nayarit.gob.mx'),
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
print(f'🦎 Periódico Oficial · {ESTADO_NOMBRE} · max {MAX_PUBS}', flush=True)


# ── Helpers ────────────────────────────────────────────────
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
        if mn in months: return f'{y}-{months[mn]:02d}-{int(d):02d}'
    m = re.match(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', s)
    if m:
        d, mn, y = m.groups()
        return f'{y}-{int(mn):02d}-{int(d):02d}'
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m: return m.group(0)
    return None


def extract_date_from_text(text):
    if not text: return None
    m = re.search(r'(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})', text, re.IGNORECASE)
    if m: return fmt_date(m.group(1))
    m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if m: return m.group(1)
    m = re.search(r'(\d{1,2}[/\-]\d{1,2}[/\-]\d{4})', text)
    if m: return fmt_date(m.group(1))
    m = re.search(r'/(\d{4})/(\w+)/(\w+)(\d{1,2})', text)
    if m:
        y, mn, _, d = m.groups()
        months = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
                  'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12,
                  'ene':1,'feb':2,'mar':3,'abr':4,'may':5,'jun':6,'jul':7,'ago':8,'sep':9,'oct':10,'nov':11,'dic':12}
        if mn.lower() in months: return f'{y}-{months[mn.lower()]:02d}-{int(d):02d}'
    return None


def insert_pubs(pubs):
    if not pubs: return 0
    try:
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/periodicos_oficiales',
            headers=HEADERS, json=pubs, timeout=30
        )
        if r.ok: return len(pubs)
        print(f'  insert error: {r.status_code} {r.text[:200]}', flush=True)
    except Exception as e:
        print(f'  insert exc: {e}', flush=True)
    return 0


def safe_goto(page, url, timeout=45000, wait='domcontentloaded'):
    try:
        page.goto(url, timeout=timeout, wait_until=wait)
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f'  goto fail ({wait}): {str(e)[:80]}', flush=True)
        return False


# ── Handler EdoMex v3: ENUMERATE PDF URLs (sin tocar portal) ──
def scrape_edomex_v3(page):
    """EdoMex: PDFs en URL pattern predecible
       https://legislacion.edomex.gob.mx/sites/legislacion.edomex.gob.mx/files/files/pdf/gct/YYYY/MONTH/MMMDDS/MMMDDSx.pdf
    """
    months_es = {1:'enero',2:'febrero',3:'marzo',4:'abril',5:'mayo',6:'junio',
                 7:'julio',8:'agosto',9:'septiembre',10:'octubre',11:'noviembre',12:'diciembre'}
    abbrs = {1:'ene',2:'feb',3:'mar',4:'abr',5:'may',6:'jun',
             7:'jul',8:'ago',9:'sep',10:'oct',11:'nov',12:'dic'}
    sections = ['a','b','c','d','e','f','g','h']
    pubs = []

    today = datetime.utcnow().date()
    # Probar últimos 90 días, 1 sección a la vez
    for delta in range(0, 90):
        if len(pubs) >= MAX_PUBS: break
        d = today - timedelta(days=delta)
        for sec_num in [1, 2, 3]:  # sección primera/segunda/tercera
            if len(pubs) >= MAX_PUBS: break
            base = f'https://legislacion.edomex.gob.mx/sites/legislacion.edomex.gob.mx/files/files/pdf/gct/{d.year}/{months_es[d.month]}/{abbrs[d.month]}{d.day:02d}{sec_num}/{abbrs[d.month]}{d.day:02d}{sec_num}'
            for sec_letter in sections:
                url = base + sec_letter + '.pdf'
                try:
                    r = requests.head(url, timeout=8, headers={'User-Agent': UA}, allow_redirects=True)
                    if r.status_code == 200:
                        pubs.append({
                            'estado_clave': ESTADO_CLAVE,
                            'estado_nombre': ESTADO_NOMBRE,
                            'titulo': f'Gaceta del Gobierno EdoMex · {d.strftime("%d/%m/%Y")} · Sección {sec_num}{sec_letter.upper()}',
                            'pdf_url': url[:2000],
                            'url_oficial': 'https://legislacion.edomex.gob.mx/ve_periodico_oficial',
                            'fuente': 'Gaceta del Gobierno EdoMex',
                            'fecha_publicacion': d.strftime('%Y-%m-%d'),
                            'tipo_doc': 'gaceta',
                        })
                        print(f'  ✅ {d.strftime("%Y-%m-%d")} S{sec_num}{sec_letter}', flush=True)
                except Exception:
                    pass
                if len(pubs) >= MAX_PUBS: break
    return pubs


# ── Handler Jalisco v3: enumerar /seccion/periodico/<id> ──
def scrape_jalisco_v3(page):
    """Jalisco: enumerar IDs recientes de /seccion/periodico/<id>
       Los IDs van crecientes; los más recientes son los mayores.
    """
    pubs = []
    # Empezar desde un ID estimado actual hacia abajo
    # En 2024 ~21756, asumimos crecimiento de ~250/año
    today = datetime.utcnow().date()
    # 2026: aprox 22500 + 250 * (2026 - 2024) = 23000
    start_id = 24000

    if not safe_goto(page, BASE_URL, timeout=45000, wait='networkidle'):
        print('  Jalisco: root sigue sin cargar', flush=True)
        return pubs

    # Ahora navegar a IDs específicos
    checked = 0
    for sid in range(start_id, start_id - 300, -1):
        if checked >= 50 or len(pubs) >= MAX_PUBS:
            break
        url = f'{BASE_URL}/seccion/periodico/{sid}'
        if not safe_goto(page, url, timeout=30000, wait='networkidle'):
            continue
        checked += 1
        time.sleep(0.5)
        pdfs = page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({href: a.href, text: (a.innerText||a.title||'').trim().substring(0,300)}))
                .filter(l => l.href.toLowerCase().includes('.pdf'))
        """)
        if pdfs:
            for l in pdfs[:MAX_PUBS - len(pubs)]:
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
            print(f'  ✅ sid={sid}: +{len(pdfs)} PDFs', flush=True)
    return pubs


# ── Handler CDMX v3: probar URL sin "data." subdomain ──
def scrape_cdmx_v3(page):
    """CDMX: probar consejeria.cdmx.gob.mx (sin data.) + data. portal"""
    pubs = []
    urls = [
        BASE_URL,  # consejeria.cdmx.gob.mx/gaceta-oficial
        'https://data.consejeria.cdmx.gob.mx/index.php/gaceta',
        'https://data.consejeria.cdmx.gob.mx/portal_old/',
    ]
    for url in urls:
        if not safe_goto(page, url, timeout=60000, wait='domcontentloaded'):
            continue
        links = page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({href: a.href, text: (a.innerText||a.title||'').trim().substring(0,300)}))
                .filter(l =>
                    l.href.toLowerCase().includes('.pdf') ||
                    l.href.includes('uploads/gacetas') ||
                    l.href.includes('gaceta'))
        """)
        print(f'  CDMX {url}: {len(links)} links', flush=True)
        if not links: continue
        for l in links[:MAX_PUBS]:
            titulo = l['text'] or urllib.parse.unquote(l['href'].split('/')[-1])
            fecha = extract_date_from_text(titulo) or extract_date_from_text(l['href'])
            pubs.append({
                'estado_clave': ESTADO_CLAVE,
                'estado_nombre': ESTADO_NOMBRE,
                'titulo': titulo[:500],
                'pdf_url': l['href'][:2000],
                'url_oficial': BASE_URL,
                'fuente': 'Gaceta Oficial CDMX',
                'fecha_publicacion': fecha,
                'tipo_doc': 'gaceta',
            })
        if pubs: break
    return pubs


# ── Handlers v2 (sin cambios) ──
def scrape_nl(page):
    pubs = []
    if not safe_goto(page, BASE_URL, timeout=45000, wait='networkidle'):
        return pubs
    links = page.evaluate("""
        () => Array.from(document.querySelectorAll('a[href]'))
            .map(a => ({href: a.href, text: (a.innerText||a.title||'').trim().substring(0,300)}))
            .filter(l =>
                l.href.toLowerCase().includes('.pdf') ||
                (l.text && /\\d{4}/.test(l.text) && (l.href.includes('periodico') || l.href.includes('archivo'))))
    """)
    for l in links[:MAX_PUBS]:
        titulo = l['text'] or urllib.parse.unquote(l['href'].split('/')[-1])
        fecha = extract_date_from_text(titulo) or extract_date_from_text(l['href'])
        pubs.append({
            'estado_clave': ESTADO_CLAVE,
            'estado_nombre': ESTADO_NOMBRE,
            'titulo': titulo[:500],
            'pdf_url': l['href'][:2000],
            'url_oficial': BASE_URL,
            'fuente': 'Periódico Oficial Nuevo León (HCNL)',
            'fecha_publicacion': fecha,
            'tipo_doc': 'edicion',
        })
    return pubs


def scrape_generic(page):
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
        if not links: continue
        for l in links[:MAX_PUBS]:
            titulo = l['text'] or urllib.parse.unquote(l['href'].split('/')[-1]).replace('.pdf','').replace('_',' ').replace('+',' ')
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
        if pubs: break
    return pubs


HANDLERS = {
    '09': scrape_cdmx_v3,
    '14': scrape_jalisco_v3,
    '15': scrape_edomex_v3,
    '19': scrape_nl,
}


def main():
    handler = HANDLERS.get(ESTADO_CLAVE, scrape_generic)
    pubs = []

    # EdoMex no necesita Playwright (HEAD-checks puros)
    if ESTADO_CLAVE == '15':
        try:
            pubs = handler(None)
        except Exception as e:
            print(f'❌ Handler error: {e}', flush=True)
    else:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )
            context = browser.new_context(
                user_agent=UA, viewport={'width': 1280, 'height': 720},
                locale='es-MX', ignore_https_errors=True,
            )
            page = context.new_page()
            page.set_default_timeout(45000)
            try:
                pubs = handler(page)
            except Exception as e:
                print(f'❌ Handler error: {e}', flush=True)
            browser.close()

    print(f'\n📥 {len(pubs)} publicaciones. Insertando...', flush=True)
    seen = set()
    unique = []
    for p in pubs:
        if p['pdf_url'] not in seen:
            seen.add(p['pdf_url'])
            unique.append(p)
    inserted = 0
    for i in range(0, len(unique), 30):
        inserted += insert_pubs(unique[i:i+30])
    print(f'\n═══ SUMMARY ({ESTADO_NOMBRE}) ═══', flush=True)
    print(f'  found: {len(pubs)} · unique: {len(unique)} · inserted: {inserted}', flush=True)
    print(f'  with date: {sum(1 for u in unique if u.get("fecha_publicacion"))}/{len(unique)}', flush=True)


if __name__ == '__main__':
    main()
