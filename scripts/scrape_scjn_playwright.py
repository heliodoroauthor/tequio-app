#!/usr/bin/env python3
"""
scrape_scjn_playwright.py v2 — Network Intercept
==================================================
Captura las respuestas XHR del SJF para obtener los datos JSON directamente.
Más robusto que parsear DOM porque la app es 100% SPA.

Uso: python3 scrape_scjn_playwright.py <epoca> <pagina_inicio> <pagina_fin>
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

print(f'🦎 SCJN Scraper v2 · {EPOCA_NAME} · pages {PAGINA_START}-{PAGINA_END}', flush=True)


# Storage para responses capturadas
captured_responses = []


def on_response(response):
    """Captura respuestas JSON de API de SCJN"""
    try:
        url = response.url
        # Solo capturar XHR/fetch a APIs (descartar imágenes, css, html)
        if not any(p in url.lower() for p in ['api', 'sjfweb', 'sjfsem', 'json', 'tesis', 'detalle']):
            return
        ct = response.headers.get('content-type', '')
        if 'json' not in ct.lower():
            return
        try:
            body = response.json()
            captured_responses.append({
                'url': url,
                'status': response.status,
                'body': body
            })
            print(f'  [intercept] {response.status} {url[:120]}', flush=True)
        except Exception:
            pass
    except Exception:
        pass


def parse_fecha_es(s):
    months = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
              'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
    try:
        s = (s or '').lower().strip()
        m = re.match(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', s)
        if m:
            d, mn, y = m.groups()
            mn_num = months.get(mn.lower())
            if mn_num:
                return f'{y}-{mn_num:02d}-{int(d):02d}'
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
        if m:
            return m.group(0)
    except Exception:
        pass
    return None


def extract_tesis_from_response(resp_body):
    """Trata de extraer tesis del JSON de respuesta — diferentes formatos del SCJN"""
    tesis_list = []
    
    # Si es una lista directa
    if isinstance(resp_body, list):
        items = resp_body
    # Si tiene un campo "data" o "tesis" o "resultados"
    elif isinstance(resp_body, dict):
        items = (resp_body.get('data') or resp_body.get('tesis') 
                 or resp_body.get('resultados') or resp_body.get('listado')
                 or resp_body.get('items') or [])
        if isinstance(items, dict):
            items = [items]  # single tesis
        if not items and 'registro' in resp_body:
            items = [resp_body]  # detalle de 1 tesis
    else:
        return []
    
    for item in items:
        if not isinstance(item, dict):
            continue
        # Detect registro digital (campos comunes: ius, registro, registroDigital, idTesis)
        reg = (item.get('ius') or item.get('registro') or item.get('registroDigital') 
               or item.get('idTesis') or item.get('id'))
        if not reg:
            continue
        try:
            reg = int(re.sub(r'\D', '', str(reg))[:9])
        except Exception:
            continue
        if reg < 100000 or reg > 5000000:
            continue
        
        tesis = {
            'registro_digital': reg,
            'epoca': EPOCA_NAME,
            'url_oficial': f'https://sjf2.scjn.gob.mx/detalle/tesis/{reg}',
        }
        # Campos del API
        tesis['rubro'] = (item.get('rubro') or item.get('titulo') or '')[:800]
        tesis['texto'] = (item.get('texto') or item.get('contenido') or '')[:8000]
        # 'tipo' es NOT NULL en DB — default 'Aislada'
        tesis['tipo'] = (item.get('tipo') or item.get('tipoTesis') or 'Aislada')[:50]
        tesis['instancia'] = item.get('instancia') or item.get('organo')
        tesis['materia'] = (item.get('materia') or item.get('materias') or '')[:200]
        tesis['tesis_clave'] = (item.get('tesisClave') or item.get('clave') or item.get('numTesis') or '')[:80]
        tesis['precedentes'] = (item.get('precedentes') or '')[:5000]
        tesis['ponente'] = (item.get('ponente') or '')[:200]
        tesis['votacion'] = (item.get('votacion') or '')[:200]
        tesis['fecha_publicacion'] = parse_fecha_es(item.get('fecha') or item.get('fechaPublicacion') or '')
        
        # 'rubro' es NOT NULL en DB — skip si está vacío
        if not tesis['rubro'].strip():
            continue
        tesis_list.append(tesis)
    
    return tesis_list


def upsert_tesis_bulk(tesis_list):
    if not tesis_list:
        return 0
    # Dedup por registro_digital
    seen = set()
    unique = []
    for t in tesis_list:
        if t['registro_digital'] not in seen:
            seen.add(t['registro_digital'])
            unique.append(t)
    
    inserted = 0
    batch_size = 25
    for i in range(0, len(unique), batch_size):
        batch = unique[i:i+batch_size]
        try:
            r = requests.post(
                f'{SUPABASE_URL}/rest/v1/jurisprudencia_scjn',
                headers=HEADERS, json=batch, timeout=30
            )
            if r.ok:
                inserted += len(batch)
            elif r.status_code == 409:
                # Conflict: try one by one
                for single in batch:
                    try:
                        rs = requests.post(
                            f'{SUPABASE_URL}/rest/v1/jurisprudencia_scjn',
                            headers=HEADERS, json=[single], timeout=15
                        )
                        if rs.ok:
                            inserted += 1
                    except Exception:
                        pass
            else:
                print(f'  [upsert] {r.status_code}: {r.text[:200]}', flush=True)
        except Exception as e:
            print(f'  [upsert exc] {e}', flush=True)
    return inserted


def main():
    global captured_responses
    inserted_total = 0
    pages_visited = 0
    
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
        page.on('response', on_response)
        
        # Fase 1: Listado pages
        for pagina in range(PAGINA_START, PAGINA_END + 1):
            url = f'https://sjf2.scjn.gob.mx/listado-de-tesis?Epoca={EPOCA}&Pagina={pagina}'
            captured_responses = []
            try:
                page.goto(url, timeout=30000, wait_until='networkidle')
                time.sleep(2)  # esperar XHR adicionales
                
                # Procesar responses capturadas
                tesis_batch = []
                for r in captured_responses:
                    tesis_batch.extend(extract_tesis_from_response(r['body']))
                
                pages_visited += 1
                
                if tesis_batch:
                    inserted = upsert_tesis_bulk(tesis_batch)
                    inserted_total += inserted
                    print(f'  page {pagina}: {len(tesis_batch)} tesis encontradas, {inserted} insertadas (total {inserted_total}) · {len(captured_responses)} XHRs', flush=True)
                else:
                    print(f'  page {pagina}: 0 tesis en {len(captured_responses)} XHRs', flush=True)
                    # Print URLs capturadas como debug (siempre las primeras 5)
                    for r in captured_responses[:5]:
                        body_preview = ''
                        try:
                            body_preview = json.dumps(r['body'])[:200] if r.get('body') else ''
                        except Exception:
                            pass
                        print(f'    debug XHR [{r.get("status","?")}]: {r["url"][:120]} | body: {body_preview}', flush=True)
            except PWTimeout:
                print(f'  page {pagina}: timeout', flush=True)
            except Exception as e:
                print(f'  page {pagina}: error {e}', flush=True)
        
        browser.close()
    
    print(f'\n═══ SUMMARY ({EPOCA_NAME}, pages {PAGINA_START}-{PAGINA_END}) ═══', flush=True)
    print(f'  pages visited:  {pages_visited}', flush=True)
    print(f'  tesis inserted: {inserted_total}', flush=True)


if __name__ == '__main__':
    main()
