#!/usr/bin/env python3
"""
scrape_presas.py v2 — CONAGUA SIH dam levels (rewrite)
========================================================================
v1 leía cols[5] del CSV (que es Vertedor, casi siempre 0) en lugar de
cols[3] (VolumenAlm). Resultado: 96% de los registros recientes salían
con NULL o 0.

v2 fixes:
  1. Auto-detect de columna VolumenAlm desde el header (no índice fijo)
  2. Encoding latin-1 (el CSV de CONAGUA viene en latin-1, no utf-8)
  3. Retry con backoff exponencial para vencer Imperva Challenge
  4. Quality guard: si >40% de presas fallan o devuelven vol=0, abortar
     antes de escribir BD (no envenenar la tabla)
  5. Descartar lecturas vol=0 cuando hay lecturas anteriores >0
     (probable error de captura del día más reciente)

Fuente: https://sih.conagua.gob.mx/basedatos/Presas/
"""
import os, sys, time, random, requests
from datetime import datetime

import urllib3
urllib3.disable_warnings()

# ---- Anti-bot bypass (Imperva en gob.mx) ----
try:
    import cloudscraper
    _SCRAPER = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'darwin', 'mobile': False}
    )
    def cs_get(url, **kw):
        kw.pop('verify', None)
        return _SCRAPER.get(url, **kw)
    print("  [cloudscraper] OK -- bypass anti-bot activo")
except ImportError:
    def cs_get(url, **kw):
        kw.pop('verify', None)
        return requests.get(url, **kw)
    print("  [cloudscraper] NO disponible -- usando requests plano")

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

CATALOGO_URL = 'https://sih.conagua.gob.mx/basedatos/Presas/0_Catalogo_de_presas.xls'
CSV_BASE     = 'https://sih.conagua.gob.mx/basedatos/Presas'
HEADERS_WEB  = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'es-MX,es;q=0.9,en;q=0.8',
}
TIMEOUT      = 30
MAX_PRESAS   = int(os.environ.get('MAX_PRESAS', '210'))
QUALITY_THRESHOLD = float(os.environ.get('QUALITY_THRESHOLD', '0.40'))  # abortar si fail > 40%
MAX_RETRIES  = 3

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

# -- Capacidades NAMO (hm3) de las ~50 presas más grandes de México --
# Fuente: CONAGUA - Inventario Nacional de Presas
CAPACIDAD_FALLBACK = {
    'angostura': 19737, 'malpaso': 12373, 'nezahualcoyotl': 12373,
    'chicoasen': 1376, 'penitas': 1090, 'infiernillo': 12000,
    'aguamilpa': 6960, 'cerro de oro': 2400, 'cerro prieto': 393,
    'cuchillo': 1123, 'rodrigo gomez': 39, 'la boca': 39,
    'vicente guerrero': 5340, 'falcon': 4080, 'amistad': 7069,
    'marte r': 740, 'marte r. gomez': 740, 'solis': 800,
    'allende': 178, 'begona': 60, 'capulin': 26,
    'zimapan': 1070, 'lazaro cardenas': 4419, 'el palmito': 4419,
    'huites': 4568, 'adolfo lopez mateos': 3386, 'humaya': 3386,
    'sanalona': 845, 'bacurato': 1860, 'comedero': 3395,
    'jose lopez portillo': 3395, 'plutarco elias calles': 339, 'cazadero': 50,
    'la boquilla': 2894, 'lago toronto': 2894, 'francisco i. madero': 351,
    'francisco i madero': 351, 'las virgenes': 388, 'luis l. leon': 326,
    'luis l leon': 326, 'el granero': 326, 'caracol': 1190,
    'la villita': 700, 'tepuxtepec': 370, 'don martin': 1329,
    'venustiano carranza': 1329, 'trigomil': 309, 'calderon': 80,
    'santa rosa': 366, 'yuriria': 188, 'necaxa': 47,
    'la angostura, son': 921, 'el novillo': 2925, 'oviachic': 3023,
    'alvaro obregon': 3023, 'el carrizo': 156,
}


def buscar_capacidad_fallback(nombre_presa):
    if not nombre_presa:
        return None
    n = nombre_presa.lower()
    n = n.replace('é','e').replace('ó','o').replace('í','i')
    n = n.replace('á','a').replace('ú','u').replace('ñ','n')
    for key, cap in CAPACIDAD_FALLBACK.items():
        if key in n:
            return cap
    return None


def get_with_retry(url, **kw):
    """GET con retry y backoff exponencial para vencer Imperva."""
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            r = cs_get(url, **kw)
            # Detectar Imperva challenge page
            if r.status_code == 200:
                first_bytes = r.content[:300].decode('latin-1', errors='ignore').lower()
                if 'challenge validation' in first_bytes or 'imperva' in first_bytes:
                    last_err = f"imperva_challenge (intento {attempt+1})"
                    sleep_for = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(sleep_for)
                    continue
                return r
            if r.status_code in (429, 503):
                last_err = f"http_{r.status_code}"
                sleep_for = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(sleep_for)
                continue
            return r
        except Exception as e:
            last_err = str(e)
            time.sleep(1 + attempt)
    return None


print("Tequio Presas Scraper v2 - CONAGUA SIH")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE_URL: {'OK' if SUPABASE_URL else 'MISSING'}")
print(f"  SERVICE_KEY:  {'OK' if SERVICE_KEY else 'MISSING'}")
print(f"  Quality threshold: {QUALITY_THRESHOLD*100:.0f}% fail max")

if not (SUPABASE_URL and SERVICE_KEY):
    print("ERROR: env vars faltantes.")
    sys.exit(1)


def cargar_catalogo():
    """Baja el .xls maestro y extrae (clave, presa, estado, capacidad_NAMO)."""
    print(f"\n[CATALOGO] GET {CATALOGO_URL}")
    r = get_with_retry(CATALOGO_URL, headers=HEADERS_WEB, timeout=TIMEOUT)
    if not r or not r.ok:
        print(f"  ERR: catalogo no descargable")
        return []
    print(f"  Status: {r.status_code}, bytes: {len(r.content)}")

    try:
        import xlrd
    except ImportError:
        print("  ERR: xlrd no instalado (pip install xlrd==1.2.0)")
        return []

    try:
        book = xlrd.open_workbook(file_contents=r.content)
        sheet = book.sheet_by_index(0)
        print(f"  Hoja: {sheet.name}, filas={sheet.nrows}, cols={sheet.ncols}")
    except Exception as e:
        print(f"  EXC parse xls: {e}")
        return []

    header_row = None
    col_map = {}
    for ri in range(min(8, sheet.nrows)):
        try:
            row_raw = [str(sheet.cell_value(ri, c)).strip() for c in range(sheet.ncols)]
            row = [v.upper() for v in row_raw]
        except Exception:
            continue
        if any('CLAVE' in v for v in row) or any('NOMBRE' in v for v in row):
            header_row = ri
            print(f"  Header fila [{ri}]: {row_raw[:14]}")
            for ci, v in enumerate(row):
                v_clean = v.replace('.', '').replace(' ', '')
                if 'CLAVE' in v and 'clave' not in col_map:
                    col_map['clave'] = ci
                elif (('NOMBRE' in v and 'OFICIAL' not in v) or 'COMUN' in v) and 'presa' not in col_map:
                    col_map['presa'] = ci
                elif ('ENTIDAD' in v or 'ESTADO' in v) and 'estado' not in col_map:
                    col_map['estado'] = ci
                elif ('NAMO' in v_clean or 'CAPACIDAD' in v or 'CAPNAMO' in v_clean) and 'capacidad' not in col_map:
                    col_map['capacidad'] = ci
            break

    if header_row is None or 'clave' not in col_map:
        print(f"  ERR: encabezados no encontrados. col_map: {col_map}")
        return []
    print(f"  Header fila {header_row}, mapeo: {col_map}")

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


def parse_csv(text):
    """
    Parsea el CSV y devuelve (fecha_iso, volumen_hm3) del último día con dato VÁLIDO.
    Auto-detecta la columna VolumenAlm desde el header.
    Encoding: latin-1.
    """
    lines = text.split('\n')

    # Buscar header dinámicamente
    header_idx = None
    vol_col_idx = None
    fecha_col_idx = None
    for i, ln in enumerate(lines[:30]):
        ln_norm = ln.strip().lower()
        if 'estacion' in ln_norm and 'fecha' in ln_norm and 'volumen' in ln_norm:
            header_idx = i
            cols = [c.strip().lower() for c in ln.split(',')]
            for ci, c in enumerate(cols):
                if 'volumenalm' in c.replace(' ',''):
                    vol_col_idx = ci
                elif c == 'fecha':
                    fecha_col_idx = ci
            break

    if vol_col_idx is None or fecha_col_idx is None or header_idx is None:
        return None, None

    # Recorrer de atrás hacia adelante buscando una fila con vol válido
    for ln in reversed(lines[header_idx+1:]):
        ln = ln.strip()
        if not ln:
            continue
        cols = ln.split(',')
        if len(cols) <= max(vol_col_idx, fecha_col_idx):
            continue
        fecha = cols[fecha_col_idx].strip()
        vol_raw = cols[vol_col_idx].strip()
        if not vol_raw or not fecha:
            continue
        try:
            vol = float(vol_raw)
            datetime.strptime(fecha, '%Y-%m-%d')
            if vol <= 0:
                # vol=0 o negativo: salta y busca un día anterior con dato real
                continue
            return fecha, vol
        except Exception:
            continue
    return None, None


def ultimo_volumen(clave):
    """Descarga CSV (con retry) y devuelve (fecha, volumen) del último día válido."""
    url = f"{CSV_BASE}/{clave}.csv"
    r = get_with_retry(url, headers=HEADERS_WEB, timeout=TIMEOUT)
    if not r or not r.ok:
        return None, None
    # CONAGUA sirve en latin-1
    try:
        text = r.content.decode('latin-1')
    except Exception:
        text = r.text
    return parse_csv(text)


def upsert_presa(row):
    """Insert con merge para acumular historia."""
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

    # Primera pasada: recolectar datos en memoria (NO escribir BD aún)
    print(f"\n[SCRAPE] Procesando {len(presas)} presas...")
    resultados = []
    sin_dato = 0
    for i, p in enumerate(presas, 1):
        fecha, vol = ultimo_volumen(p['clave'])
        if not fecha or vol is None:
            sin_dato += 1
            resultados.append((p, None, None, None))
            time.sleep(0.18)
            continue
        cap = p['capacidad']
        if not cap:
            cap = buscar_capacidad_fallback(p['presa'])
        pct = None
        if cap and cap > 0:
            pct = round((vol / cap) * 100, 2)
            if pct > 200:
                pct = None
        resultados.append((p, fecha, vol, pct))
        if i % 25 == 0 or i == len(presas):
            pct_str = f"{pct:.0f}%" if pct is not None else 'n/a'
            print(f"  [{i}/{len(presas)}] {p['presa'][:32]:32} {fecha}  {vol:>8.1f} hm3  {pct_str}")
        time.sleep(0.18)

    # Quality guard: si % de fallos > umbral, NO escribir BD
    fail_rate = sin_dato / len(presas) if presas else 1.0
    print(f"\n[QUALITY] Tasa fallo: {fail_rate*100:.1f}% ({sin_dato}/{len(presas)})")
    if fail_rate > QUALITY_THRESHOLD:
        print(f"❌ ABORTAR: fallo > {QUALITY_THRESHOLD*100:.0f}%. CONAGUA puede haber cambiado formato o estar bloqueando.")
        print(f"   No se escribió a BD para no envenenar la tabla. Investiga manualmente.")
        sys.exit(2)

    # Segunda pasada: escribir BD solo con datos válidos
    print(f"\n[WRITE] Escribiendo {len(presas)-sin_dato} registros válidos a BD...")
    ok = falla = 0
    for p, fecha, vol, pct in resultados:
        if fecha is None or vol is None:
            continue
        row = {
            'fecha_corte': fecha,
            'presa': (p['presa'] or p['clave'])[:120],
            'estado': (p['estado'][:80] if p['estado'] else None),
            'capacidad_total_hm3': p['capacidad'] or buscar_capacidad_fallback(p['presa']),
            'almacenamiento_hm3': round(vol, 2),
            'pct_almacenamiento': pct,
            'fuente': 'CONAGUA SIH',
        }
        if upsert_presa(row):
            ok += 1
        else:
            falla += 1

    print(f"\n✅ Resumen: {ok} escritas OK, {falla} fallaron al escribir, {sin_dato} sin dato CONAGUA")


if __name__ == '__main__':
    main()
