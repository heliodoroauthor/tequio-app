#!/usr/bin/env python3
"""
scrape_compranet.py — Fase 3.1.C ComprasMX/Compranet (v4 — CSV directo, columnas reales)
========================================================================================
v3 falló porque mi COL_MAP usaba nombres CAMEL_CASE_UPPER pero el CSV real
usa Title Case con acentos: "Código del expediente", "Título del expediente",
"Tipo Procedimiento", etc.

v4 fixes:
  - Normaliza nombres de columna (lowercase, sin acentos, _ en lugar de espacios)
  - Imprime las 73 columnas reales en el primer run
  - Mapping flexible con fallback por substring
"""
import os, sys, csv, io, re, time, requests
from datetime import datetime

import urllib3
urllib3.disable_warnings()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

CSV_URLS = [
    'https://upcp-compranet.buengobierno.gob.mx/cnetassets/datos_abiertos_contratos_expedientes/Contratos_CompraNet2025.csv',
    'https://upcp-compranet.hacienda.gob.mx/cnetassets/datos_abiertos_contratos_expedientes/Contratos_CompraNet2024.csv',
]

UA = 'Mozilla/5.0 (compatible; TequioBot/1.0; +https://tequio.app)'
HEADERS_WEB = {'User-Agent': UA}
TIMEOUT_DOWNLOAD = 300

BATCH_INSERT = int(os.environ.get('BATCH_INSERT', '200'))
MAX_CONTRATOS = int(os.environ.get('MAX_CONTRATOS', '250000'))
UMBRAL_AD_ALTA_MXN = float(os.environ.get('UMBRAL_AD_ALTA_MXN', '10000000'))

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

print("Tequio · Scraper Compranet (v4 — CSV directo, columnas reales)")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE: {'OK' if SUPABASE_URL and SERVICE_KEY else 'MISSING'}")

if not (SUPABASE_URL and SERVICE_KEY):
    sys.exit(1)


def norm_col(s):
    """lowercase, sin acentos, alfanumérico+_."""
    if not s:
        return ''
    s = str(s).strip().lower()
    rep = {'á':'a','é':'e','í':'i','ó':'o','ú':'u','ü':'u','ñ':'n'}
    for k,v in rep.items():
        s = s.replace(k, v)
    s = re.sub(r'[^a-z0-9]+', '_', s).strip('_')
    return s


def descargar_csv(url):
    print(f"\n[GET] {url}")
    try:
        r = requests.get(url, headers=HEADERS_WEB, timeout=TIMEOUT_DOWNLOAD, verify=False, stream=False)
        print(f"  Status: {r.status_code}, bytes: {len(r.content)}")
        if not r.ok:
            return None, f'HTTP {r.status_code}'
        try:
            text = r.content.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = r.content.decode('utf-8')
            except UnicodeDecodeError:
                text = r.content.decode('latin-1', errors='replace')
        return text, None
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def detectar_delimitador(sample):
    counts = {d: sample.count(d) for d in [',', ';', '|', '\t']}
    return max(counts, key=counts.get)


def normalizar_procedimiento(s):
    s = (s or '').lower()
    if 'licitaci' in s:
        return 'licitacion_publica'
    if 'invitaci' in s:
        return 'invitacion_tres'
    if 'adjudic' in s:
        return 'adjudicacion_directa'
    if 'proyecto' in s and 'convocatoria' in s:
        return 'licitacion_publica'
    return None


def normalizar_caracter(s):
    s = (s or '').lower()
    if 'inter' in s:
        return 'internacional'
    if 'nacional' in s:
        return 'nacional'
    return None


def normalizar_categoria(s):
    s = (s or '').lower()
    if 'obra' in s:
        return 'obras'
    if 'servicio' in s:
        return 'servicios'
    if 'arrendamiento' in s:
        return 'arrendamientos'
    if 'adquisi' in s or 'bien' in s:
        return 'adquisiciones'
    return None


def parse_fecha(s):
    if not s:
        return None
    s = str(s).strip()
    if not s or s.lower() == 'null':
        return None
    for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d', '%m/%d/%Y']:
        try:
            return datetime.strptime(s[:19], fmt).date().isoformat()
        except Exception:
            continue
    return None


def parse_monto(s):
    if s is None:
        return None
    s = str(s).strip().replace(',', '').replace('$', '').replace(' ', '')
    if not s or s.lower() == 'null':
        return None
    try:
        return float(s)
    except Exception:
        return None


# ─── Mapeo por nombre normalizado ─────────────────────────────────────────
# Keys = norm_col(nombre_columna_csv)
COL_MAP = {
    # IDs / Códigos
    'codigo_del_expediente':         'ocid_origen',
    'codigo_expediente':             'ocid_origen',
    'referencia_del_expediente':     'numero_proc',
    'numero_de_procedimiento':       'numero_proc',
    'codigo_del_contrato':           'codigo_contrato',
    'codigo_contrato':               'codigo_contrato',
    # Títulos / descripción
    'titulo_del_expediente':         'titulo',
    'titulo_del_contrato':           'titulo',
    'titulo_expediente':             'titulo',
    'titulo_contrato':               'titulo',
    'descripcion_del_contrato':      'descripcion',
    'descripcion_del_expediente':    'descripcion',
    'descripcion_contrato':          'descripcion',
    'descripcion_expediente':        'descripcion',
    # Dependencia
    'institucion':                   'dependencia',
    'nombre_de_la_institucion':      'dependencia',
    'dependencia':                   'dependencia',
    'nombre_de_la_dependencia':      'dependencia',
    'siglas_de_la_institucion':      'dependencia_codigo',
    'siglas_institucion':            'dependencia_codigo',
    'clave_institucion':             'dependencia_codigo',
    'clave_de_la_institucion':       'dependencia_codigo',
    'nombre_de_la_uc':               'unidad_compradora',
    'nombre_uc':                     'unidad_compradora',
    'clave_de_la_uc':                'clave_uc',
    'clave_uc':                      'clave_uc',
    'descripcion_ramo':              'ramo',
    'ramo':                          'ramo',
    'clave_ramo':                    'clave_ramo',
    # Procedimiento
    'tipo_procedimiento':            'tipo_procedimiento',
    'tipo_de_procedimiento':         'tipo_procedimiento',
    'caracter':                      'caracter',
    'caracter_del_procedimiento':    'caracter',
    'caracter_procedimiento':        'caracter',
    'tipo_de_contratacion':          'tipo_contrato',
    'tipo_contratacion':             'tipo_contrato',
    # Proveedor
    'proveedor_o_contratista':       'proveedor_nombre',
    'proveedor_contratista':         'proveedor_nombre',
    'nombre_del_proveedor_o_contratista': 'proveedor_nombre',
    'nombre_del_proveedor':          'proveedor_nombre',
    'razon_social':                  'proveedor_nombre',
    'razon_social_del_proveedor':    'proveedor_nombre',
    'rfc':                           'proveedor_rfc',
    'rfc_del_proveedor':             'proveedor_rfc',
    'rfc_del_proveedor_o_contratista': 'proveedor_rfc',
    'rfc_proveedor':                 'proveedor_rfc',
    'siglas_del_pais':               'proveedor_pais',
    'siglas_pais':                   'proveedor_pais',
    'pais_de_origen':                'proveedor_pais',
    'estratificacion_de_la_mipyme':  'estrat_pyme',
    'estratificacion_mipyme':        'estrat_pyme',
    # Montos
    'importe_del_contrato':          'monto_mxn',
    'importe_contrato':              'monto_mxn',
    'importe_total':                 'monto_mxn',
    'importe_total_del_contrato':    'monto_mxn',
    'monto':                         'monto_mxn',
    'monto_total':                   'monto_mxn',
    'moneda':                        'moneda',
    'tipo_de_moneda':                'moneda',
    # Fechas
    'fecha_de_firma':                'fecha_firma',
    'fecha_firma':                   'fecha_firma',
    'fecha_inicio':                  'fecha_inicio',
    'fecha_inicio_contrato':         'fecha_inicio',
    'fecha_de_inicio_del_contrato':  'fecha_inicio',
    'fecha_de_inicio':               'fecha_inicio',
    'fecha_fin':                     'fecha_fin',
    'fecha_fin_contrato':            'fecha_fin',
    'fecha_de_fin_del_contrato':     'fecha_fin',
    'fecha_de_fin':                  'fecha_fin',
    'fecha_termino':                 'fecha_fin',
    # Geográfico
    'entidad_federativa':            'entidad_federativa',
    'estado':                        'entidad_federativa',
    'municipio':                     'municipio',
    'estatus_del_contrato':          'estatus',
    'estatus_contrato':              'estatus',
}


def parse_csv_row(row, debug_first=False):
    out = {}
    if debug_first:
        print("  [SAMPLE ROW]:")
    for col_orig, valor in row.items():
        if not col_orig:
            continue
        col_norm = norm_col(col_orig)
        # 1) Exact match
        key = COL_MAP.get(col_norm)
        # 2) Fallback substring matching (para campos clave)
        if not key:
            if 'titulo' in col_norm and ('expediente' in col_norm or 'contrato' in col_norm):
                key = 'titulo'
            elif 'descripcion' in col_norm and ('expediente' in col_norm or 'contrato' in col_norm or 'partida' in col_norm):
                key = 'descripcion' if 'descripcion' not in out else None
            elif 'proveedor' in col_norm or 'contratista' in col_norm or 'razon_social' in col_norm:
                if 'rfc' in col_norm:
                    key = 'proveedor_rfc'
                elif 'pais' in col_norm:
                    key = 'proveedor_pais'
                elif 'estratificacion' in col_norm or 'mipyme' in col_norm or 'tipo' in col_norm:
                    pass  # ignore
                else:
                    key = 'proveedor_nombre'
            elif col_norm == 'rfc':
                key = 'proveedor_rfc'
            elif 'importe' in col_norm or col_norm.startswith('monto'):
                key = 'monto_mxn'
            elif 'fecha' in col_norm and 'firma' in col_norm:
                key = 'fecha_firma'
            elif 'fecha' in col_norm and 'inicio' in col_norm:
                key = 'fecha_inicio'
            elif 'fecha' in col_norm and ('fin' in col_norm or 'termino' in col_norm):
                key = 'fecha_fin'
            elif col_norm in ('institucion', 'dependencia') or 'nombre_de_la_institucion' in col_norm:
                key = 'dependencia'

        if debug_first and valor not in (None, ''):
            v_short = str(valor)[:60]
            mapped = f' → {key}' if key else ''
            print(f"    {col_orig!r}{mapped}: {v_short!r}")

        if key and valor not in (None, '', 'NULL'):
            # No sobrescribir si ya hay valor (preferir el primer match)
            if key not in out:
                out[key] = valor.strip() if isinstance(valor, str) else valor

    # OCID compuesto si no viene
    ocid = out.get('ocid_origen') or out.get('codigo_contrato') or out.get('numero_proc')
    if not ocid:
        return None
    out['ocid'] = f"CN-MX-{str(ocid).strip()}"

    # Limpiar campos a tipos correctos
    monto = parse_monto(out.get('monto_mxn'))
    out['monto_mxn'] = monto
    out['fecha_firma']  = parse_fecha(out.get('fecha_firma'))
    out['fecha_inicio'] = parse_fecha(out.get('fecha_inicio'))
    out['fecha_fin']    = parse_fecha(out.get('fecha_fin'))
    if out.get('fecha_inicio') and out.get('fecha_fin'):
        try:
            d1 = datetime.strptime(out['fecha_inicio'], '%Y-%m-%d').date()
            d2 = datetime.strptime(out['fecha_fin'], '%Y-%m-%d').date()
            out['duracion_dias'] = (d2 - d1).days
        except Exception:
            pass

    out['tipo_procedimiento'] = normalizar_procedimiento(out.get('tipo_procedimiento'))
    out['caracter']           = normalizar_caracter(out.get('caracter'))
    out['tipo_contrato']      = normalizar_categoria(out.get('tipo_contrato'))

    titulo = out.get('titulo') or out.get('descripcion') or ''
    out['titulo'] = (titulo or 'Sin título')[:500]
    if out.get('descripcion'):
        out['descripcion'] = out['descripcion'][:2000]

    out['flag_adjudicacion_directa_alta'] = bool(
        out.get('tipo_procedimiento') == 'adjudicacion_directa' and
        monto is not None and monto > UMBRAL_AD_ALTA_MXN
    )

    # Limpiar campos extra que no van en la tabla
    for k in ['ocid_origen', 'numero_proc', 'codigo_contrato', 'clave_uc',
              'clave_ramo', 'estrat_pyme', 'estatus']:
        out.pop(k, None)

    return out


def bulk_upsert(rows):
    if not rows:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/contratos_publicos?on_conflict=ocid"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=rows, headers=h, timeout=60)
        if not r.ok:
            print(f"  [HTTP {r.status_code}] {r.text[:300]}")
            return 0
        return len(rows)
    except Exception as e:
        print(f"  [EXC] {e}")
        return 0


def refresh_view():
    try:
        r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/refresh_proveedores_agregados",
                          headers=HEADERS_SB, json={}, timeout=60)
        return r.ok
    except Exception:
        return False


def procesar_csv(text):
    delim = detectar_delimitador(text[:5000])
    print(f"  Delimitador detectado: {delim!r}")

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    headers = reader.fieldnames or []
    print(f"  Columnas encontradas ({len(headers)}):")
    for i, h in enumerate(headers, 1):
        norm = norm_col(h)
        mapped = COL_MAP.get(norm)
        flag = f' → {mapped}' if mapped else ''
        print(f"    {i:>2}. {h!r}{flag}")

    batch = []
    total_filas = 0
    total_inserted = 0
    total_skipped = 0
    first_debug = True

    for row in reader:
        total_filas += 1
        if total_filas > MAX_CONTRATOS:
            print(f"  [CAP] Alcanzado MAX_CONTRATOS={MAX_CONTRATOS}")
            break
        parsed = parse_csv_row(row, debug_first=first_debug)
        first_debug = False
        if not parsed:
            total_skipped += 1
            continue
        batch.append(parsed)
        if len(batch) >= BATCH_INSERT:
            ok = bulk_upsert(batch)
            total_inserted += ok
            batch = []
        if total_filas % 5000 == 0:
            print(f"  [{total_filas}] {total_inserted} insertados, {total_skipped} skipped")

    if batch:
        ok = bulk_upsert(batch)
        total_inserted += ok

    print(f"\n  Filas procesadas: {total_filas}")
    print(f"  Insertados:       {total_inserted}")
    print(f"  Skipped:          {total_skipped}")
    return total_inserted


def main():
    inserted_total = 0
    for url in CSV_URLS:
        text, err = descargar_csv(url)
        if err or not text:
            print(f"  [SKIP] {url} → {err}")
            continue
        inserted = procesar_csv(text)
        inserted_total += inserted
        if inserted_total >= 50000:
            print(f"  [STOP] Ya cargamos {inserted_total} contratos. Saltando CSVs adicionales.")
            break

    print(f"\nTotal insertado: {inserted_total}")
    print(f"\nRefrescando vista proveedores_agregados...")
    if refresh_view():
        print("  Refresh OK.")
    else:
        print("  [WARN] Refresh falló.")


if __name__ == '__main__':
    main()
