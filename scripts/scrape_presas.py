#!/usr/bin/env python3
"""
scrape_presas.py — CONAGUA SIH dam levels (replaces broken SINA scraper)
========================================================================
1. Descarga el catalogo maestro (.xls) con clave, nombre, estado, NAMO.
2. Para cada presa, descarga su CSV diario y extrae el ultimo VolumenAlm.
3. Calcula % de llenado vs NAMO y hace UPSERT en presas_cuencas.

Fuente: https://sih.conagua.gob.mx/basedatos/Presas/

H2.6-01b 2026-05-21: agregado cloudscraper para bypass de anti-bot Imperva
en sih.conagua.gob.mx. Si la libreria no esta disponible, hace fallback
silencioso a requests (comportamiento original).
"""
import os, sys, time, requests
from datetime import datetime

import urllib3
urllib3.disable_warnings()

# ---- Anti-bot bypass (Imperva/Akamai en gob.mx) ----
# H2.6-01b fix 3: urllib3 v2 rechaza verify=False con check_hostname=True
# tanto a nivel session como per-request. Solucion: dejar verify=True (default)
# y confiar en el cert valido de SIH. Si cert fallara, habria que pinear
# urllib3<2 en el workflow, pero asi evitamos el SSL context dance.
try:
    import cloudscraper
    _SCRAPER = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'darwin', 'mobile': False}
    )
    def cs_get(url, **kw):
        kw.pop('verify', None)  # ignorar verify=False legacy de llamadas
        return _SCRAPER.get(url, **kw)
    print("  [cloudscraper] OK — bypass anti-bot activo")
except ImportError:
    def cs_get(url, **kw):
        kw.pop('verify', None)
        return requests.get(url, **kw)
    print("  [cloudscraper] NO disponible — usando requests plano")

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

CATALOGO_URL = 'https://sih.conagua.gob.mx/basedatos/Presas/0_Catalogo_de_presas.xls'
CSV_BASE     = 'https://sih.conagua.gob.mx/basedatos/Presas'
HEADERS_WEB  = {'User-Agent': 'Mozilla/5.0 (compatible; TequioBot/1.0; +https://tequio-app.vercel.app)'}
TIMEOUT      = 30
MAX_PRESAS   = int(os.environ.get('MAX_PRESAS', '210'))

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

# -- Capacidades NAMO (hm3) de las ~50 presas mas grandes de Mexico --
# Fuente: CONAGUA - Inventario Nacional de Presas. Match por substring lowercase
# Esta tabla actua como fallback cuando el XLS-catalog no se parsea correctamente
CAPACIDAD_FALLBACK = {
    'angostura': 19737,        'malpaso': 12373,         'nezahualcoyotl': 12373,
    'chicoasen': 1376,         'penitas': 1090,          'infiernillo': 12000,
    'aguamilpa': 6960,         'cerro de oro': 2400,     'cerro prieto': 393,
    'cuchillo': 1123,          'rodrigo gomez': 39,      'la boca': 39,
    'vicente guerrero': 5340,  'falcon': 4080,           'amistad': 7069,
    'marte r': 740,            'marte r. gomez': 740,    'solis': 800,
    'allende': 178,            'begona': 60,             'capulin': 26,
    'zimapan': 1070,           'lazaro cardenas': 4419,  'el palmito': 4419,
    'huites': 4568,            'adolfo lopez mateos': 3386, 'humaya': 3386,
    'sanalona': 845,           'bacurato': 1860,         'comedero': 3395,
    'jose lopez portillo': 3395, 'plutarco elias calles': 339, 'cazadero': 50,
    'la boquilla': 2894,       'lago toronto': 2894,     'francisco i. madero': 351,
    'francisco i madero': 351, 'las virgenes': 388,      'luis l. leon': 326,
    'luis l leon': 326,        'el granero': 326,        'caracol': 1190,
    'la villita': 700,         'tepuxtepec': 370,        'don martin': 1329,
    'venustiano carranza': 1329, 'trigomil': 309,        'calderon': 80,
    'santa rosa': 366,         'yuriria': 188,           'necaxa': 47,
    'la angostura, son': 921,  'el novillo': 2925,       'oviachic': 3023,
    'alvaro obregon': 3023,    'el carrizo': 156,
}


def buscar_capacidad_fallback(nombre_presa):
    """Si el XLS no dio capacidad, intenta match por nombre con dict hardcoded."""
    if not nombre_presa:
        return None
    n = nombre_presa.lower().replace('é','e').replace('ó','o').replace('í','i').replace('á','a').replace('ú','u').replace('ñ','n')
    for key, cap in CAPACIDAD_FALLBACK.items():
        if key in n:
            return cap
    return None

print("Tequio Presas Scraper - CONAGUA SIH")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE_URL: {'OK' if SUPABASE_URL else 'MISSING'}")
print(f"  SERVICE_KEY:  {'OK' if SERVICE_KEY else 'MISSING'}")

if not (SUPABASE_URL and SERVICE_KEY):
    print("ERROR: env vars faltantes.")
    sys.exit(1)


def cargar_catalogo():
    """Baja el .xls maestro y extrae (clave, presa, estado, capacidad_NAMO)."""
    print(f"\n[CATALOGO] GET {CATALOGO_URL}")
    try:
        r = cs_get(CATALOGO_URL, headers=HEADERS_WEB, timeout=TIMEOUT, verify=False)
        print(f"  Status: {r.status_code}, bytes: {len(r.content)}")
        # Detectar anti-bot challenge
        if r.status_code == 200 and len(r.content) < 5000:
            head = r.content[:600].decode('latin-1', errors='ignore').lower()
            if 'challenge' in head or 'imperva' in head or 'akamai' in head:
                print("  WARN: respuesta parece anti-bot challenge, no XLS real")
                return []
        if not r.ok:
            return []
    except Exception as e:
        print(f"  EXC: {e}")
        return []

    try:
        import xlrd
    except ImportError:
        print("  ERR: xlrd no instalado (pip install xlrd==1.2.0)")
        return []

    try:
        book = xlrd.open_workbook(file_contents=r.content)
        print(f"  Hojas: {book.nsheets} -> {book.sheet_names()}")
        sheet = book.sheet_by_index(0)
        print(f"  Hoja[0]: {sheet.name}, filas={sheet.nrows}, cols={sheet.ncols}")
    except Exception as e:
        print(f"  EXC parse xls: {e}")
        return []

    # Detectar encabezados en las primeras 8 filas. Imprimir todo lo que vemos.
    header_row = None
    col_map = {}
    for ri in range(min(8, sheet.nrows)):
        try:
            row_raw = [str(sheet.cell_value(ri, c)).strip() for c in range(sheet.ncols)]
            row = [v.upper() for v in row_raw]
        except Exception:
            continue
        if any('CLAVE' in v for v in row) or any('ESTACI' in v for v in row):
            header_row = ri
            print(f"  Fila encabezado [{ri}]: {row_raw[:14]}")
            for ci, v in enumerate(row):
                v_clean = v.replace('.', '').replace(' ', '')
                if ('CLAVE' in v or v == 'ESTACION' or v == 'ESTACIÓN') and 'clave' not in col_map:
                    col_map['clave'] = ci
                elif (('NOMBRE' in v and 'OFICIAL' not in v) or 'COMUN' in v) and 'presa' not in col_map:
                    col_map['presa'] = ci
                elif ('ENTIDAD' in v or 'ESTADO' in v) and 'estado' not in col_map:
                    col_map['estado'] = ci
                elif ('NAMO' in v_clean or 'CAPACIDAD' in v or v_clean.startswith('CAP') or
                      'VOLUMENALNAMO' in v_clean or 'CAPNAMO' in v_clean) and 'capacidad' not in col_map:
                    col_map['capacidad'] = ci
            break

    if header_row is None or 'clave' not in col_map:
        print(f"  ERR: encabezados no encontrados. col_map: {col_map}")
        for ri in range(min(6, sheet.nrows)):
            print(f"    [{ri}] {[str(sheet.cell_value(ri,c))[:20] for c in range(min(sheet.ncols,12))]}")
        return []
    print(f"  Encabezados fila {header_row}, mapeo: {col_map}")
    if sheet.nrows > header_row + 1:
        sample = {k: sheet.cell_value(header_row+1, ci) for k, ci in col_map.items()}
        print(f"  Sample fila {header_row+1}: {sample}")

    presas = []
    for ri in range(header_row + 1, sheet.nrows):
        try:
            clave = str(sheet.cell_value(ri, col_map['clave'])).strip()
            if not clave or len(clave) < 2 or clave.lower() == 'none':
                continue
            presa  = str(sheet.cell_value(ri, col_map.get('presa', col_map['clave']))).strip()
            estado = str(sheet.cell_value(ri, col_map['estado'])).strip() if 'estado' in col_map else ''
            cap = None
            if 'capacidad' in col_map:
                cv = sheet.cell_value(ri, col_map['capacidad'])
                try:
                    cap = float(cv) if cv not in ('', None) else None
                except Exception:
                    cap = None
            presas.append({'clave': clave, 'presa': presa or clave, 'estado': estado, 'capacidad': cap})
        except Exception:
            continue
    print(f"  Total presas en catalogo: {len(presas)}")
    return presas[:MAX_PRESAS]


def ultimo_volumen(clave):
    """Descarga CSV y devuelve (fecha_iso, volumen_hm3) mas reciente con VolumenAlm no nulo."""
    url = f"{CSV_BASE}/{clave}.csv"
    try:
        r = cs_get(url, headers=HEADERS_WEB, timeout=TIMEOUT, verify=False)
        if not r.ok:
            return None, None
    except Exception:
        return None, None

    lines = r.text.split('\n')
    data_start = None
    for i, ln in enumerate(lines):
        if ln.startswith('Estacion,'):
            data_start = i + 1
            break
    if data_start is None:
        return None, None

    for ln in reversed(lines[data_start:]):
        ln = ln.strip()
        if not ln:
            continue
        cols = ln.split(',')
        if len(cols) < 6:
            continue
        fecha = cols[1].strip()
        vol_raw = cols[5].strip() if len(cols) > 5 else ''
        if not vol_raw or not fecha:
            continue
        try:
            vol = float(vol_raw)
            datetime.strptime(fecha, '%Y-%m-%d')
            return fecha, vol
        except Exception:
            continue
    return None, None


def upsert_presa(row):
    """Insert sin upsert estricto (acepta historia). El frontend toma fecha_corte DESC."""
    url = f"{SUPABASE_URL}/rest/v1/presas_cuencas?on_conflict=presa,fecha_corte"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=row, headers=h, timeout=20)
        if r.ok:
            return True
        if r.status_code == 400 and 'on_conflict' in r.text.lower():
            r2 = requests.post(f"{SUPABASE_URL}/rest/v1/presas_cuencas",
                               json=row, headers={**HEADERS_SB, 'Prefer': 'return=minimal'}, timeout=20)
            return r2.ok
        return False
    except Exception:
        return False


def main():
    presas = cargar_catalogo()
    if not presas:
        print("ERROR: catalogo vacio. Abortando.")
        sys.exit(1)

    ok = falla = sin_dato = 0
    for i, p in enumerate(presas, 1):
        fecha, vol = ultimo_volumen(p['clave'])
        if not fecha or vol is None:
            sin_dato += 1
            continue
        cap = p['capacidad']
        if not cap:
            cap = buscar_capacidad_fallback(p['presa'])
        pct = None
        if cap and cap > 0:
            pct = round((vol / cap) * 100, 2)
            if pct > 200:
                pct = None
        row = {
            'fecha_corte': fecha,
            'presa': (p['presa'] or p['clave'])[:120],
            'estado': (p['estado'][:80] if p['estado'] else None),
            'capacidad_total_hm3': cap,
            'almacenamiento_hm3': round(vol, 2),
            'pct_almacenamiento': pct,
            'fuente': 'CONAGUA SIH',
        }
        if upsert_presa(row):
            ok += 1
            if i 