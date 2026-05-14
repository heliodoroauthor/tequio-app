#!/usr/bin/env python3
"""
scrape_compranet.py — Fase 3.1.C ComprasMX/Compranet (v3 — CSV directo)
========================================================================
La API JSON `api.datos.gob.mx/v2/contratacionesabiertas` está caída en mayo 2026.
Plan B: descargar el CSV oficial de UPCP-Compranet directamente.

Fuentes:
  - 2025: https://upcp-compranet.buengobierno.gob.mx/cnetassets/datos_abiertos_contratos_expedientes/Contratos_CompraNet2025.csv
  - 2024: https://upcp-compranet.hacienda.gob.mx/cnetassets/datos_abiertos_contratos_expedientes/Contratos_CompraNet2024.csv

Estos CSVs tienen formato propio de Compranet (no OCDS), con columnas tipo:
  CODIGO_EXPEDIENTE, NUMERO_PROCEDIMIENTO, TITULO_EXPEDIENTE,
  TIPO_PROCEDIMIENTO, CARACTER, ESTATUS_CONTRATO, CLAVE_UC, NOMBRE_UC,
  TITULO_CONTRATO, FECHA_INICIO, FECHA_FIN, IMPORTE_CONTRATO,
  PROVEEDOR_CONTRATISTA, ESTRATIFICACION_MUC, etc.
"""
import os, sys, csv, io, time, requests
from datetime import datetime

import urllib3
urllib3.disable_warnings()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

# Fuentes oficiales (probar en orden, usar la primera que responda)
CSV_URLS = [
    'https://upcp-compranet.buengobierno.gob.mx/cnetassets/datos_abiertos_contratos_expedientes/Contratos_CompraNet2025.csv',
    'https://upcp-compranet.hacienda.gob.mx/cnetassets/datos_abiertos_contratos_expedientes/Contratos_CompraNet2024.csv',
]

UA = 'Mozilla/5.0 (compatible; TequioBot/1.0; +https://tequio.app)'
HEADERS_WEB = {'User-Agent': UA}
TIMEOUT_DOWNLOAD = 300  # 5 min para descargar CSV completo

BATCH_INSERT = int(os.environ.get('BATCH_INSERT', '200'))
MAX_CONTRATOS = int(os.environ.get('MAX_CONTRATOS', '250000'))
UMBRAL_AD_ALTA_MXN = float(os.environ.get('UMBRAL_AD_ALTA_MXN', '10000000'))

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

print("Tequio · Scraper Compranet (v3 — CSV directo)")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE: {'OK' if SUPABASE_URL and SERVICE_KEY else 'MISSING'}")

if not (SUPABASE_URL and SERVICE_KEY):
    sys.exit(1)


def descargar_csv(url):
    """Intenta descargar un CSV. Devuelve (text, err)."""
    print(f"\n[GET] {url}")
    try:
        r = requests.get(url, headers=HEADERS_WEB, timeout=TIMEOUT_DOWNLOAD, verify=False, stream=False)
        print(f"  Status: {r.status_code}, bytes: {len(r.content)}")
        if not r.ok:
            return None, f'HTTP {r.status_code}'
        # Detectar encoding (Compranet CSVs suelen venir en latin-1 o utf-8-sig)
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
    """Compranet usa ',' o ';' o '|'. Detectar."""
    counts = {d: sample.count(d) for d in [',', ';', '|', '\t']}
    return max(counts, key=counts.get)


def normalizar_procedimiento(s):
    s = (s or '').lower()
    if 'licitaci' in s or 'lp' in s:
        return 'licitacion_publica'
    if 'invitaci' in s or 'ito' in s:
        return 'invitacion_tres'
    if 'adjudic' in s or 'ad' in s:
        return 'adjudicacion_directa'
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
    """Convierte fecha 'DD/MM/YYYY' o 'YYYY-MM-DD' a 'YYYY-MM-DD'."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d']:
        try:
            return datetime.strptime(s[:19], fmt).date().isoformat()
        except Exception:
            continue
    return None


def parse_monto(s):
    if not s:
        return None
    s = str(s).strip().replace(',', '').replace('$', '').replace(' ', '')
    try:
        return float(s)
    except Exception:
        return None


# Mapeo de nombres de columna conocidos → campos de nuestra tabla
COL_MAP = {
    'CODIGO_EXPEDIENTE':           'ocid_origen',
    'NUMERO_PROCEDIMIENTO':        'numero_proc',
    'EXPEDIENTE':                  'ocid_origen',
    'CODIGO_CONTRATO':             'codigo_contrato',
    'TITULO_EXPEDIENTE':           'titulo',
    'TITULO_CONTRATO':             'titulo',
    'DESCRIPCION_CONTRATO':        'descripcion',
    'DESCRIPCION_EXPEDIENTE':      'descripcion',
    'CLAVE_UC':                    'dependencia_codigo',
    'NOMBRE_UC':                   'unidad_compradora',
    'RESPONSABLE':                 'responsable',
    'ESTRATIFICACION_MUC':         'estratificacion',
    'SIGLAS_UC':                   'dependencia_codigo',
    'RAMO':                        'ramo',
    'CLAVE_DEPENDENCIA':           'dependencia_codigo',
    'DEPENDENCIA':                 'dependencia',
    'NOMBRE_DEPENDENCIA':          'dependencia',
    'TIPO_CONTRATACION':           'tipo_contrato',
    'TIPO_PROCEDIMIENTO':          'tipo_procedimiento',
    'FORMA_PROCEDIMIENTO':         'forma_proc',
    'CARACTER':                    'caracter',
    'CARACTER_PROCEDIMIENTO':      'caracter',
    'PROVEEDOR_CONTRATISTA':       'proveedor_nombre',
    'NOMBRE_PROVEEDOR':            'proveedor_nombre',
    'RAZON_SOCIAL':                'proveedor_nombre',
    'RFC':                         'proveedor_rfc',
    'RFC_PROVEEDOR':               'proveedor_rfc',
    'ESTRATIFICACION_MPYME':       'estrat_pyme',
    'SIGLAS_PAIS':                 'proveedor_pais',
    'IMPORTE_CONTRATO':            'monto_mxn',
    'IMPORTE_TOTAL':               'monto_mxn',
    'IMPORTE':                     'monto_mxn',
    'MONEDA':                      'moneda',
    'FECHA_INICIO':                'fecha_inicio',
    'FECHA_FIN':                   'fecha_fin',
    'FECHA_FIRMA':                 'fecha_firma',
    'FECHA_APERTURA_PROPOSICIONES':'fecha_firma',
    'FECHA_FALLO':                 'fecha_firma',
    'ENTIDAD_FEDERATIVA':          'entidad_federativa',
    'ESTATUS_CONTRATO':            'estatus',
}


def parse_csv_row(row):
    """Toma una row dict del CSV y devuelve dict listo para nuestra tabla."""
    out = {}
    for col, valor in row.items():
        if not col:
            continue
        key = COL_MAP.get(col.strip().upper())
        if key and valor not in (None, '', 'NULL'):
            out[key] = valor.strip() if isinstance(valor, str) else valor

    # OCID compuesto si no viene
    ocid = out.get('ocid_origen') or out.get('codigo_contrato') or out.get('numero_proc')
    if not ocid:
        return None
    out['ocid'] = f"CN-MX-{ocid}"

    # Limpiar campos a tipos correctos
    monto = parse_monto(out.get('monto_mxn'))
    out['monto_mxn'] = monto
    out['fecha_firma']  = parse_fecha(out.get('fecha_firma'))
    out['fecha_inicio'] = parse_fecha(out.get('fecha_inicio'))
    out['fecha_fin']    = parse_fecha(out.get('fecha_fin'))
    if out['fecha_inicio'] and out['fecha_fin']:
        try:
            d1 = datetime.strptime(out['fecha_inicio'], '%Y-%m-%d').date()
            d2 = datetime.strptime(out['fecha_fin'], '%Y-%m-%d').date()
            out['duracion_dias'] = (d2 - d1).days
        except Exception:
            pass

    out['tipo_procedimiento'] = normalizar_procedimiento(out.get('tipo_procedimiento'))
    out['caracter']           = normalizar_caracter(out.get('caracter'))
    out['tipo_contrato']      = normalizar_categoria(out.get('tipo_contrato'))

    out['titulo'] = (out.get('titulo') or out.get('descripcion') or 'Sin título')[:500]
    if out.get('descripcion'):
        out['descripcion'] = out['descripcion'][:2000]

    # Flag
    out['flag_adjudicacion_directa_alta'] = (
        out.get('tipo_procedimiento') == 'adjudicacion_directa' and
        monto is not None and monto > UMBRAL_AD_ALTA_MXN
    )

    # Limpiar campos extra que no van en la tabla
    for k in ['ocid_origen', 'numero_proc', 'codigo_contrato', 'forma_proc',
              'responsable', 'estratificacion', 'estrat_pyme', 'estatus']:
        out.pop(k, None)

    # Campos requeridos
    if not out.get('titulo') or out['titulo'] == 'Sin título':
        if not out.get('proveedor_nombre'):
            return None

    return out


def bulk_upsert(rows):
    if not rows:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/contratos_publicos?on_conflict=ocid"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=rows, headers=h, timeout=60)
        return len(rows) if r.ok else 0
    except Exception:
        return 0


def refresh_view():
    try:
        r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/refresh_proveedores_agregados",
                          headers=HEADERS_SB, json={}, timeout=60)
        return r.ok
    except Exception:
        return False


def procesar_csv(text):
    """Lee CSV text y procesa fila por fila, insertando en batches."""
    delim = detectar_delimitador(text[:5000])
    print(f"  Delimitador detectado: {delim!r}")

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    headers = reader.fieldnames or []
    print(f"  Columnas encontradas ({len(headers)}): {headers[:15]}")

    batch = []
    total_filas = 0
    total_inserted = 0
    total_skipped = 0

    for row in reader:
        total_filas += 1
        if total_filas > MAX_CONTRATOS:
            print(f"  [CAP] Alcanzado MAX_CONTRATOS={MAX_CONTRATOS}")
            break
        parsed = parse_csv_row(row)
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
        # Si ya cargamos suficientes, no procesar el siguiente CSV
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
