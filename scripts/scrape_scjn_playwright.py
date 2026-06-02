#!/usr/bin/env python3
"""
scrape_scjn_playwright.py
==========================
Scrape sjf2.scjn.gob.mx para llenar tabla jurisprudencia_scjn.
Estrategia 2-fases:
  1. Listar paginas de tesis para una época, extraer registros digitales
  2. Para cada registro, navegar al detalle y extraer todos los campos

Uso: python3 scrape_scjn_playwright.py <epoca> <pagina_inicio> <pagina_fin>
  epoca: 1=Decima (2011-2021), 2=Undecima (2021+)
  Ejemplo: python3 scrape_scjn_playwright.py 2 1 50
"""
import os, sys, time, json, re, requests
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

EPOCA = int(sys.argv[1]) if len(sys.argv) > 1 else 2
PAGINA_START = int(sys.argv[2]) if len(sys.argv) > 2 else 1
PAGINA_END = int(sys.argv[3]) if len(sys.argv) > 3 else 50

EPOCA_NAMES = {1: 'Décima Época', 2: 'Undécima Época'}
EPOCA_NAME = EPOCA_NAMES.get(EPOCA, f'Época {EPOCA}')

print(f'🦎 SCJN Scraper · {EPOCA_NAME} · pages {PAGINA_START}-{PAGINA_END}', flush=True)


def extract_registros_from_listado(page):
    """Extrae registros digitales de la página actual de listado"""
    try:
        # Esperar a que carguen los resultados
        page.wait_for_selector('text=/Tesis Aislada|Jurisprudencia|Registro digital/', timeout=15000)
        time.sleep(1)
        
        # Buscar links a detalles - el patrón es /detalle/tesis/XXXXXXX
        links = page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href*="/detalle/tesis/"]'))
                .map(a => a.href.match(/\\/detalle\\/tesis\\/(\\d+)/))
                .filter(m => m)
                .map(m => parseInt(m[1]))
        """)
        return list(set(links))
    except PWTimeout:
        return []
    except Exception as e:
        print(f'  [extract listado] error: {e}', flush=True)
        return []


def parse_tesis_detail(page, registro):
    """Parsea la página de detalle de una tesis"""
    try:
        page.wait_for_selector('text=/Rubro|Texto|Precedentes/', timeout=10000)
        time.sleep(0.5)
        
        # Extracción por patrones de texto
        text = page.evaluate("""
            () => {
                // Get all visible text in the main content area
                const main = document.querySelector('main, #root, .content, body');
                return main ? main.innerText : '';
            }
        """)
        
        if not text or len(text) < 100:
            return None
        
        result = {
            'registro_digital': registro,
            'epoca': EPOCA_NAME,
            'url_oficial': f'https://sjf2.scjn.gob.mx/detalle/tesis/{registro}',
        }
        
        # Tipo: Jurisprudencia / Tesis Aislada
        tipo_match = re.search(r'(Jurisprudencia|Tesis Aislada)', text)
        result['tipo'] = tipo_match.group(1) if tipo_match else None
        
        # Instancia (Pleno, Primera Sala, Segunda Sala, Tribunales Colegiados)
        inst_match = re.search(r'Instancia:?\s*([^\n]+)', text)
        if inst_match:
            result['instancia'] = inst_match.group(1).strip()
        
        # Materia
        mat_match = re.search(r'Materia(?:s)?:?\s*([^\n]+)', text)
        if mat_match:
            result['materia'] = mat_match.group(1).strip()[:200]
        
        # Tesis clave (ej. "1a./J. 50/2022")
        clave_match = re.search(r'Tesis(?:\s+clave)?:?\s*([A-Za-z0-9./\-\s]+\d+/\d+)', text)
        if clave_match:
            result['tesis_clave'] = clave_match.group(1).strip()
        
        # Rubro (suele ser todo en mayúsculas, antes del texto)
        rubro_match = re.search(r'Rubro:?\s*([^\n]+(?:\n[^\n]+)?)', text)
        if rubro_match:
            result['rubro'] = rubro_match.group(1).strip()[:800]
        
        # Texto principal (Texto:)
        texto_match = re.search(r'Texto:?\s*(.+?)(?=\n(?:Precedentes|Ejecutoria|Votación|Fuente:|$))', text, re.DOTALL)
        if texto_match:
            result['texto'] = texto_match.group(1).strip()[:8000]
        
        # Precedentes
        prec_match = re.search(r'Precedentes:?\s*(.+?)(?=\n(?:Ejecutoria|Votación|Fuente:|$))', text, re.DOTALL)
        if prec_match:
            result['precedentes'] = prec_match.group(1).strip()[:5000]
        
        # Fecha publicación
        fecha_match = re.search(r'(?:Publicación|Fecha):?\s*(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})', text)
        if fecha_match:
            result['fecha_publicacion'] = parse_fecha_es(fecha_match.group(1))
        
        # Ponente
        ponente_match = re.search(r'Ponente:?\s*([^\n.]+)', text)
        if ponente_match:
            result['ponente'] = ponente_match.group(1).strip()[:200]
        
        # Votación
        vot_match = re.search(r'Votación:?\s*([^\n]+)', text)
        if vot_match:
            result['votacion'] = vot_match.group(1).strip()[:200]
        
        # Solo conservar si hay rubro O texto
        if not result.get('rubro') and not result.get('texto'):
            return None
        
        return result
    except PWTimeout:
        return None
    except Exception as e:
        return None


def parse_fecha_es(s):
    """Convierte '15 de enero de 2024' → '2024-01-15'"""
    months = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
              'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
    try:
        m = re.match(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', s, re.I)
        if m:
            d, mn, y = m.groups()
            mn_num = months.get(mn.lower())
            if mn_num:
                return f'{y}-{mn_num:02d}-{int(d):02d}'
    except Exception:
        pass
    return None


def upsert_tesis(tesis_list):
    """Bulk upsert a Supabase"""
    if not tesis_list:
        return 0
    try:
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/jurisprudencia_scjn',
            headers=HEADERS,
            json=tesis_list,
            timeout=30
        )
        return len(tesis_list) if r.ok else 0
    except Exception as e:
        print(f'  [upsert] error: {e}', flush=True)
        return 0


def main():
    inserted_total = 0
    failed_total = 0
    all_registros = set()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 720},
            locale='es-MX',
        )
        page = context.new_page()
        
        # FASE 1: Recolectar registros digitales de listado
        print(f'\n📋 FASE 1: Listado pages {PAGINA_START}-{PAGINA_END}', flush=True)
        for pagina in range(PAGINA_START, PAGINA_END + 1):
            url = f'https://sjf2.scjn.gob.mx/listado-de-tesis?Epoca={EPOCA}&Pagina={pagina}'
            try:
                page.goto(url, timeout=30000, wait_until='networkidle')
                regs = extract_registros_from_listado(page)
                all_registros.update(regs)
                print(f'  page {pagina}: {len(regs)} registros (total {len(all_registros)})', flush=True)
            except Exception as e:
                print(f'  page {pagina}: error {e}', flush=True)
        
        # FASE 2: Detallar cada registro
        registros_lista = sorted(all_registros)
        print(f'\n📥 FASE 2: Detail scrape de {len(registros_lista)} registros', flush=True)
        
        batch = []
        for i, reg in enumerate(registros_lista, 1):
            url = f'https://sjf2.scjn.gob.mx/detalle/tesis/{reg}'
            try:
                page.goto(url, timeout=20000, wait_until='domcontentloaded')
                tesis = parse_tesis_detail(page, reg)
                if tesis:
                    batch.append(tesis)
                else:
                    failed_total += 1
            except Exception:
                failed_total += 1
            
            # Bulk insert cada 20
            if len(batch) >= 20:
                inserted = upsert_tesis(batch)
                inserted_total += inserted
                print(f'  [{i}/{len(registros_lista)}] inserted batch of {len(batch)} (total {inserted_total})', flush=True)
                batch = []
        
        # Insert final
        if batch:
            inserted_total += upsert_tesis(batch)
        
        browser.close()
    
    print(f'\n═══ SUMMARY ({EPOCA_NAME}, pages {PAGINA_START}-{PAGINA_END}) ═══', flush=True)
    print(f'  registros encontrados: {len(all_registros)}', flush=True)
    print(f'  insertados:            {inserted_total}', flush=True)
    print(f'  failed:                {failed_total}', flush=True)


if __name__ == '__main__':
    main()
