#!/usr/bin/env python3
"""
scrape_compranet.py — Fase 3.1.C ComprasMX/Compranet (v6 — sin retry individual)
================================================================================
v5 cargó 8,032 de 91,850 filas en 30 min porque cuando un batch fallaba reintentaba
fila por fila (200 requests por batch malo). Eso era 200x más lento.

v6 estrategia:
  - NO retry por fila. Si batch falla → log primera muestra + skip + continúa.
  - Batches pequeños (50) → si falla, perdemos solo 50 filas, no 200.
  - En la PRIMERA falla, imprimir JSON completo de la fila para diagnosticar.
  - Pre-validación agresiva: nada de NaN/Inf, strings ASCII printable, longitud cap.
  - Progress log cada 10k filas.
  - Objetivo: cargar todo el CSV en <5 minutos.
"""
import os, sys, csv, io, re, math, json, requests
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

BATCH_INSERT = int(os.environ.get('BATCH_INSERT', '50'))
MAX_CONTRATOS = int(os.environ.get('MAX_CONTRATOS', '250000'))
UMBRAL_AD_ALTA_MXN = float(os.environ.get('UMBRAL_AD_ALTA_MXN', '10000000'))
MAX_DEBUG_PRINTS = 3

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

print("Tequio · Scraper Compranet (v6 — sin retry individual)")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE: {'OK' if SUPABASE_URL and SERVICE_KEY else 'MISSING'}")
print(f"  BATCH={BATCH_INSERT}  MAX={MAX_CONTRATOS}")

if not (SUPABASE_URL and SERVICE_KEY):
    sys.exit(1)


_stats = {
    'http_errs': 0, 'http_debugs': 0,
    'no_ocid': 0, 'no_titulo': 0, 'no_proveedor': 0,
    'batches_ok': 0, 'batches_failed': 0
}


def norm_col(s):
    if not s:
        return ''
    s = str(s).strip().lower()
    rep = {'á':'a','é':'e','í':'i','ó':'o','ú':'u','ü':'u','ñ':'n'}
    for k,v in rep.items():
        s = s.replace(k, v)
    s = re.sub(r'[^a-z0-9]+', '_', s).strip('_')
    return s


def clean_str(s, maxlen=500):
    """Limpia string: strip, control chars out, longitud cap."""
    if s is None:
        return None
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    if not s:
        return None
    # Remove control characters except tab/newline (which we'll also strip)
    s = ''.join(ch for ch in s if ch == ' ' or (ord(ch) >= 32 and ch.isprintable()))
    s = re.sub(r'\s+', ' ', s).strip()
    if not s or s.lower() in ('null', 'none', 'n/a'):
        return None
    return s[:maxlen]


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
            d = datetime.strptime(s[:19], fmt).date()
            # Sanity: solo fechas razonables (2000-2030)
            if 2000 <= d.year <= 2030:
                return d.isoformat()
        except Exception:
            continue
    return None


def parse_monto(s):
    if s is None:
        return None
    s = str(s).strip().replace(',', '').replace('$', '').replace(' ', '')
    if not s or s.lower() in ('null', 'nan', 'inf', '-inf'):
        return None
    try:
        v = float(s)
        if math.isnan(v) or math.isinf(v):
            return None
        if abs(v) > 1e15 or v < 0:
            return None
        return round(v, 2)
    except Exception:
        return None


COL_MAP = {
    'codigo_del_expediente':         'ocid_origen',
    'codigo_expediente':             'ocid_origen',
    'referencia_del_expediente':     'numero_proc',
    'numero_de_procedimiento':       'numero_proc',
    'codigo_del_contrato':           'codigo_contrato',
    'codigo_contrato':               'codigo_contrato',
    'titulo_del_expediente':         'titulo',
    'titulo_del_contrato':           'titulo',
    'titulo_expediente':             'titulo',
    'titulo_contrato':               'titulo',
    'descripcion_del_contrato':      'descripcion',
    'descripcion_del_expediente':    'descripcion',
    'descripcion_contrato':          'descripcion',
    'descripcion_expediente':        'descripcion',
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
    'descripcion_ramo':              'ramo',
    'ramo':                          'ramo',
    'tipo_procedimiento':            'tipo_procedimiento',
    'tipo_de_procedimiento':         'tipo_procedimiento',
    'caracter':                      'caracter',
    'caracter_del_procedimiento':    'caracter',
    'caracter_procedimiento':        'caracter',
    'tipo_de_contratacion':          'tipo_contrato',
    'tipo_contratacion':             'tipo_contrato',
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
    'importe_del_contrato':          'monto_mxn',
    'importe_contrato':              'monto_mxn',
    'importe_total':                 'monto_mxn',
    'importe_total_del_contrato':    'monto_mxn',
    'monto':                         'monto_mxn',
    'monto_total':                   'monto_mxn',
    'moneda':                        'moneda',
    'tipo_de_moneda':                'moneda',
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
    'entidad_federativa':            'entidad_federativa',
    'estado':                        'entidad_federativa',
    'municipio':                     'municipio',
}


def map_columns(headers):
    mapping = {}
    mapped, unmapped = 0, 0
    for col in headers:
        norm = norm_col(col)
        key = COL_MAP.get(norm)
        if not key:
            if 'titulo' in norm and ('expediente' in norm or 'contrato' in norm):
                key = 'titulo'
            elif 'descripcion' in norm and ('expediente' in norm or 'contrato' in norm):
                key = 'descripcion'
            elif ('proveedor' in norm or 'contratista' in norm or 'razon_social' in norm):
                if 'rfc' in norm:
                    key = 'proveedor_rfc'
                elif 'pais' in norm:
                    key = 'proveedor_pais'
                elif 'estratificacion' not in norm and 'tipo' not in norm:
                    key = 'proveedor_nombre'
            elif norm == 'rfc':
                key = 'proveedor_rfc'
            elif 'importe' in norm or norm.startswith('monto'):
                key = 'monto_mxn'
            elif 'fecha' in norm and 'firma' in norm:
                key = 'fecha_firma'
            elif 'fecha' in norm and 'inicio' in norm:
                key = 'fecha_inicio'
            elif 'fecha' in norm and ('fin' in norm or 'termino' in norm):
                key = 'fecha_fin'
            elif norm in ('institucion', 'dependencia'):
                key = 'dependencia'
        mapping[col] = key
        if key:
            mapped += 1
        else:
            unmapped += 1
    return mapping, mapped, unmapped


def parse_csv_row(row, col_mapping):
    raw = {}
    for col, valor in row.items():
        key = col_mapping.get(col)
        if not key:
            continue
        if valor in (None, '', 'NULL'):
            continue
        if key not in raw:
            raw[key] = valor.strip() if isinstance(valor, str) else valor

    ocid = raw.get('ocid_origen') or raw.get('codigo_contrato') or raw.get('numero_proc')
    if not ocid:
        _stats['no_ocid'] += 1
        return None
    ocid_clean = clean_str(str(ocid), 100)
    if not ocid_clean:
        return None

    out = {'ocid': f"CN-MX-{ocid_clean}"}

    # Strings sanitizados
    out['titulo']             = clean_str(raw.get('titulo') or raw.get('descripcion'), 500)
    out['descripcion']        = clean_str(raw.get('descripcion'), 2000)
    out['dependencia']        = clean_str(raw.get('dependencia'), 255)
    out['dependencia_codigo'] = clean_str(raw.get('dependencia_codigo'), 100)
    out['unidad_compradora']  = clean_str(raw.get('unidad_compradora'), 500)
    out['ramo']               = clean_str(raw.get('ramo'), 255)
    out['proveedor_nombre']   = clean_str(raw.get('proveedor_nombre'), 500)
    out['proveedor_rfc']      = clean_str(raw.get('proveedor_rfc'), 50)
    out['proveedor_pais']     = clean_str(raw.get('proveedor_pais'), 10)
    out['moneda']             = clean_str(raw.get('moneda'), 10)
    out['entidad_federativa'] = clean_str(raw.get('entidad_federativa'), 100)
    out['municipio']          = clean_str(raw.get('municipio'), 200)

    # Requisitos mínimos
    if not out['titulo']:
        _stats['no_titulo'] += 1
        return None
    if not out['proveedor_nombre']:
        _stats['no_proveedor'] += 1
        return None

    # Tipos enumerados
    out['tipo_procedimiento'] = normalizar_procedimiento(raw.get('tipo_procedimiento'))
    out['caracter']           = normalizar_caracter(raw.get('caracter'))
    out['tipo_contrato']      = normalizar_categoria(raw.get('tipo_contrato'))

    # Montos
    monto = parse_monto(raw.get('monto_mxn'))
    out['monto_mxn'] = monto

    # Fechas
    f_inicio = parse_fecha(raw.get('fecha_inicio'))
    f_fin    = parse_fecha(raw.get('fecha_fin'))
    f_firma  = parse_fecha(raw.get('fecha_firma')) or f_inicio
    out['fecha_firma']  = f_firma
    out['fecha_inicio'] = f_inicio
    out['fecha_fin']    = f_fin

    if f_inicio and f_fin:
        try:
            d1 = datetime.strptime(f_inicio, '%Y-%m-%d').date()
            d2 = datetime.strptime(f_fin, '%Y-%m-%d').date()
            dd = (d2 - d1).days
            if -3650 < dd < 36500:
                out['duracion_dias'] = dd
        except Exception:
            pass

    # Flags
    out['flag_adjudicacion_directa_alta'] = bool(
        out.get('tipo_procedimiento') == 'adjudicacion_directa'
        and monto is not None
        and monto > UMBRAL_AD_ALTA_MXN
    )

    # Drop None values to keep JSON small and avoid type confusion
    return {k: v for k, v in out.items() if v is not None}


def bulk_upsert(rows):
    if not rows:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/contratos_publicos?on_conflict=ocid"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=rows, headers=h, timeout=60)
        if r.ok:
            _stats['batches_ok'] += 1
            return len(rows)
        _stats['batches_failed'] += 1
        if _stats['http_debugs'] < MAX_DEBUG_PRINTS:
            _stats['http_debugs'] += 1
            print(f"\n  [HTTP {r.status_code}] {r.text[:400]}")
            # Dump the first row of the failed batch for diagnosis
            print(f"  [BAD ROW SAMPLE] {json.dumps(rows[0], ensure_ascii=False, default=str)[:600]}")
        return 0
    except Exception as e:
        _stats['batches_failed'] += 1
        if _stats['http_debugs'] < MAX_DEBUG_PRINTS:
            _stats['http_debugs'] += 1
            print(f"\n  [EXC batch] {e}")
        return 0


def refresh_view():
    try:
        r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/refresh_proveedores_agregados",
                          headers=HEADERS_SB, json={}, timeout=120)
        return r.ok
    except Exception:
        return False


def procesar_csv(text):
    delim = detectar_delimitador(text[:5000])
    print(f"  Delimitador: {delim!r}")

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    headers = reader.fieldnames or []
    col_mapping, n_mapped, n_unmapped = map_columns(headers)
    print(f"  Cols: {len(headers)} ({n_mapped} mapeadas, {n_unmapped} ignoradas)")

    batch = []
    total_filas = 0
    total_inserted = 0
    total_skipped = 0
    t0 = datetime.now()

    for row in reader:
        total_filas += 1
        if total_filas > MAX_CONTRATOS:
            print(f"  [CAP] MAX_CONTRATOS={MAX_CONTRATOS}")
            break
        parsed = parse_csv_row(row, col_mapping)
        if not parsed:
            total_skipped += 1
            continue
        batch.append(parsed)
        if len(batch) >= BATCH_INSERT:
            total_inserted += bulk_upsert(batch)
            batch = []
        if total_filas % 10000 == 0:
            elapsed = (datetime.now() - t0).total_seconds()
            rate = total_filas / max(elapsed, 1)
            print(f"  [{total_filas}] inserted={total_inserted} skipped={total_skipped}"
                  f" rate={rate:.0f}r/s batches_ok={_stats['batches_ok']} batches_fail={_stats['batches_failed']}")

    if batch:
        total_inserted += bulk_upsert(batch)

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n  ── Resumen ({elapsed:.0f}s) ──")
    print(f"  Filas procesadas:   {total_filas}")
    print(f"  Insertados:         {total_inserted}")
    print(f"  Skipped (parseo):   {total_skipped}")
    print(f"    · sin ocid:       {_stats['no_ocid']}")
    print(f"    · sin titulo:     {_stats['no_titulo']}")
    print(f"    · sin proveedor:  {_stats['no_proveedor']}")
    print(f"  Batches OK:         {_stats['batches_ok']}")
    print(f"  Batches fallidos:   {_stats['batches_failed']}")
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
        if inserted_total >= 100000:
            print(f"  [STOP] {inserted_total} contratos cargados.")
            break

    print(f"\nTotal insertado: {inserted_total}")
    print(f"\nRefrescando vista proveedores_agregados...")
    if refresh_view():
        print("  Refresh OK.")
    else:
        print("  [WARN] Refresh falló.")


if __name__ == '__main__':
    main()
