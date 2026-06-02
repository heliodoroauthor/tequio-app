#!/usr/bin/env python3
"""
Mega-sweep Playwright v3 - 2026-06-02
======================================
Bypassa WAFs (Cloudflare, Radware, etc.) usando Playwright headless.
Multi-strategy: direct ayuntamiento + POE + DDGS fallback.

Uso: python3 playwright_sweep_v3.py <estado_clave> <max_per_muni>
Ejemplo: python3 playwright_sweep_v3.py 20 8
"""
import os, sys, json, csv, time, re, hashlib, urllib.parse
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://mhsuihwjgtzxflesbnxv.supabase.co')
SUPABASE_ANON = os.environ.get('SUPABASE_ANON', 'sb_publishable_grJAWIMoSr5b8YfaB7HVlw_OesNipBz')
HEADERS = {
    'apikey': SUPABASE_ANON,
    'Authorization': f'Bearer {SUPABASE_ANON}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=ignore-duplicates'
}

# Nombre canónico estado (matches existente en DB)
ESTADO_NAMES = {
    '01':'Aguascalientes','02':'Baja California','03':'Baja California Sur',
    '04':'Campeche','05':'Coahuila','06':'Colima','07':'Chiapas','08':'Chihuahua',
    '09':'CDMX','10':'Durango','11':'Guanajuato','12':'Guerrero','13':'Hidalgo',
    '14':'Jalisco','15':'Estado de México','16':'Michoacán de Ocampo',
    '17':'Morelos','18':'Nayarit','19':'Nuevo León','20':'Oaxaca','21':'Puebla',
    '22':'Querétaro','23':'Quintana Roo','24':'San Luis Potosí','25':'Sinaloa',
    '26':'Sonora','27':'Tabasco','28':'Tamaulipas','29':'Tlaxcala',
    '30':'Veracruz de Ignacio de la Llave','31':'Yucatán','32':'Zacatecas',
}

# Periódicos Oficiales del Estado (POE) - búsquedas por muni
POE_SEARCH = {
    '01': 'https://www.aguascalientes.gob.mx/poegob/buscar?q={muni}+reglamento',
    '02': 'https://www.bajacalifornia.gob.mx/portal/gobierno/poebc/poebc.jsp',
    '03': 'https://secfin.bcs.gob.mx/poebcs',
    '07': 'https://www.sgg.chiapas.gob.mx/po',
    '08': 'https://www.chihuahua.gob.mx/atach2/anexo/anexo_periodico_oficial.pdf',
    '09': 'https://data.consejeria.cdmx.gob.mx',
    '14': 'https://periodicooficial.jalisco.gob.mx',
    '15': 'https://periodicooficial.edomex.gob.mx',
    '16': 'https://www.poem.michoacan.gob.mx',
    '19': 'https://periodicooficial.nl.gob.mx',
    '20': 'https://www.periodicooficial.oaxaca.gob.mx',
    '21': 'https://periodicooficial.puebla.gob.mx',
    '30': 'https://www.editoraveracruz.gob.mx',
    '31': 'https://www.yucatan.gob.mx/periodico_oficial',
}

# Patrón regex para detectar PDFs de reglamentos
REGLAMENTO_KEYWORDS = [
    'reglamento','bando','codigo-municipal','codigo municipal',
    'ley-organica-municipal','ley organica municipal','marco-juridico',
    'normatividad','disposicion-administrativa','reglamento-interior'
]
BLACKLIST = [
    'gaceta','manual','convocatoria','informe','catalogo','catálogo',
    'directorio','tabulador','contrato','aviso-de-privacidad','tabloide'
]

# Slug strategies
SLUG_PATTERNS = [
    'www.{slug}.gob.mx',
    '{slug}.gob.mx',
    'www.municipiode{slug}.gob.mx',
    'ayuntamiento{slug}.gob.mx',
    'www.gob.mx/{slug}',
]
SUBPATHS = [
    '/', '/transparencia', '/normatividad', '/reglamentos',
    '/marco-juridico', '/leyes', '/transparencia/normatividad',
    '/marco-normativo', '/reglamento'
]


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r'[áàäâ]', 'a', s)
    s = re.sub(r'[éèëê]', 'e', s)
    s = re.sub(r'[íìïî]', 'i', s)
    s = re.sub(r'[óòöô]', 'o', s)
    s = re.sub(r'[úùüû]', 'u', s)
    s = re.sub(r'[ñ]', 'n', s)
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s


def already_covered(muni_name: str, estado_name: str, max_per_muni: int) -> int:
    """Devuelve cuántos docs ya hay para este muni"""
    try:
        params = {
            'select': 'id',
            'ambito': 'eq.municipal',
            'entidad': f'ilike.{muni_name}%{estado_name}*',
        }
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/leyes',
            headers={**HEADERS, 'Prefer': 'count=exact', 'Range-Unit': 'items', 'Range': '0-0'},
            params=params, timeout=8
        )
        cr = r.headers.get('Content-Range', '0-0/0')
        total = int(cr.split('/')[-1] or 0)
        return total
    except Exception:
        return 0


def insert_ley(nombre: str, url: str, entidad: str, fuente: str) -> bool:
    """Inserta un doc en leyes (ignora duplicates)"""
    try:
        payload = {
            'nombre': nombre[:500],
            'url': url[:2000],
            'ambito': 'municipal',
            'tipo': 'reglamento-municipal',
            'entidad': entidad,
            'fuente': fuente,
        }
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/leyes',
            headers=HEADERS, json=payload, timeout=12
        )
        return r.status_code in (201, 200, 409)  # 409 = duplicate
    except Exception:
        return False


def is_valid_reglamento_url(url: str) -> bool:
    """Filtra URLs sospechosas"""
    u = url.lower()
    if not u.endswith('.pdf'):
        return False
    # Whitelist: debe mencionar reglamento o variantes
    if not any(k in u for k in REGLAMENTO_KEYWORDS):
        return False
    # Blacklist
    if any(k in u for k in BLACKLIST):
        return False
    # Skip URLs muy cortas (probables basura)
    if len(u) < 30:
        return False
    return True


def normalize_name(filename: str) -> str:
    """Convierte filename de URL en nombre legible"""
    name = filename.rsplit('/', 1)[-1]
    name = re.sub(r'\.pdf$', '', name, flags=re.I)
    name = name.replace('_', ' ').replace('+', ' ').replace('-', ' ')
    name = re.sub(r'^\d+\s+', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    if not name:
        return 'Reglamento Municipal'
    # Title case con primera mayúscula
    name = name[0].upper() + name[1:]
    return name


# ────────────────────────────────────────────────────────────────
# Strategies
# ────────────────────────────────────────────────────────────────

def strategy_http_direct(muni_name: str, max_pdfs: int = 8) -> list[str]:
    """Strategy A: HTTP directo (rápido, sin browser)"""
    slug = slugify(muni_name)
    if len(slug) < 3:
        return []
    
    pdfs = []
    for pattern in SLUG_PATTERNS[:2]:  # Solo los dos primeros patrones (más rápidos)
        domain = pattern.format(slug=slug)
        for subpath in SUBPATHS[:4]:
            url = f'https://{domain}{subpath}'
            try:
                r = requests.get(url, timeout=6, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }, allow_redirects=True)
                if r.status_code != 200:
                    continue
                html = r.text
                # Buscar PDFs de reglamentos
                found = re.findall(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', html, re.I)
                for href in found:
                    if href.startswith('//'):
                        full = 'https:' + href
                    elif href.startswith('http'):
                        full = href
                    elif href.startswith('/'):
                        full = f'https://{domain}{href}'
                    else:
                        full = urllib.parse.urljoin(url, href)
                    
                    if is_valid_reglamento_url(full) and full not in pdfs:
                        pdfs.append(full)
                        if len(pdfs) >= max_pdfs:
                            return pdfs
            except Exception:
                pass
    return pdfs


def strategy_playwright(muni_name: str, max_pdfs: int = 8) -> list[str]:
    """Strategy B: Playwright headless (bypasa WAFs)"""
    slug = slugify(muni_name)
    if len(slug) < 3:
        return []
    
    pdfs = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1280, 'height': 720},
                locale='es-MX',
                timezone_id='America/Mexico_City',
            )
            page = context.new_page()
            
            for pattern in SLUG_PATTERNS[:3]:
                domain = pattern.format(slug=slug)
                base_url = f'https://{domain}'
                
                for subpath in ['/', '/transparencia', '/normatividad', '/reglamentos', '/marco-juridico']:
                    target = f'{base_url}{subpath}'
                    try:
                        page.goto(target, timeout=15000, wait_until='domcontentloaded')
                        time.sleep(0.5)  # Wait for any JS
                        
                        # Get all links
                        links = page.evaluate("""
                            () => Array.from(document.querySelectorAll('a[href]'))
                                .map(a => a.href)
                                .filter(h => h.toLowerCase().endsWith('.pdf'))
                        """)
                        
                        for href in links:
                            if is_valid_reglamento_url(href) and href not in pdfs:
                                pdfs.append(href)
                                if len(pdfs) >= max_pdfs:
                                    browser.close()
                                    return pdfs
                    except PlaywrightTimeout:
                        continue
                    except Exception:
                        continue
                
                if pdfs:
                    break  # Si ya encontré algo con este pattern, no probar otros
            
            browser.close()
    except Exception as e:
        print(f'    [PW Error] {e}', flush=True)
    
    return pdfs


# ────────────────────────────────────────────────────────────────
# Main loop por estado
# ────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print('Uso: python3 playwright_sweep_v3.py <estado_clave> [max_per_muni]')
        sys.exit(1)
    
    estado_key = sys.argv[1].zfill(2)
    max_per_muni = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    
    estado_name = ESTADO_NAMES.get(estado_key)
    if not estado_name:
        print(f'Estado clave inválida: {estado_key}')
        sys.exit(1)
    
    # Cargar catálogo INEGI
    catalog_file = Path(__file__).parent / 'data' / 'catalogo_inegi_municipios.csv'
    munis = []
    with open(catalog_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['cve_ent'] == estado_key:
                munis.append(row['nom_mun'])
    
    print(f'📋 Procesando {len(munis)} municipios de {estado_name} (clave {estado_key})', flush=True)
    print(f'   max_per_muni={max_per_muni}', flush=True)
    print('', flush=True)
    
    stats = {'discovered': 0, 'inserted': 0, 'skipped': 0, 'no_pdfs': 0, 'failed': 0}
    
    for i, muni in enumerate(munis, 1):
        # Skip si ya tiene suficientes docs
        existing = already_covered(muni, estado_name, max_per_muni)
        if existing >= max_per_muni:
            stats['skipped'] += 1
            continue
        
        print(f'[{i}/{len(munis)}] ▶ {muni} (existing: {existing})', flush=True)
        
        # Strategy A: HTTP directo
        pdfs = strategy_http_direct(muni, max_pdfs=max_per_muni)
        
        # Strategy B: Playwright si HTTP no dio nada
        if not pdfs:
            print(f'    🌐 HTTP failed, trying Playwright...', flush=True)
            pdfs = strategy_playwright(muni, max_pdfs=max_per_muni)
        
        if not pdfs:
            stats['no_pdfs'] += 1
            print(f'    ⚠️  Sin PDFs', flush=True)
            continue
        
        stats['discovered'] += len(pdfs)
        entidad = f'{muni}, {estado_name}'
        slug = slugify(muni)
        fuente = f'Gobierno {muni}'
        
        for pdf_url in pdfs[:max_per_muni - existing]:
            name = normalize_name(pdf_url)
            if insert_ley(name, pdf_url, entidad, fuente):
                stats['inserted'] += 1
                print(f'    ✅ {name[:60]}', flush=True)
            else:
                stats['failed'] += 1
    
    print('', flush=True)
    print(f'═══ SUMMARY (estado={estado_key}) ═══', flush=True)
    for k, v in stats.items():
        print(f'  {k:<12} : {v}', flush=True)
    print('═══════════════════════════════════════', flush=True)


if __name__ == '__main__':
    main()
