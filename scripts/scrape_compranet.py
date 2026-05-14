#!/usr/bin/env python3
"""
scrape_compranet.py — Fase 3.1.C ComprasMX/Compranet (v5)
=========================================================
v4 logró cargar 400 contratos (LICONSA, ALEN DEL NORTE para "Alimentación
para el Bienestar"). Pero el resto (91,450 filas) NO se cargó.

Diagnóstico v4 → v5:
  - 1ros 2 batches OK; del 3ro en adelante falló todo. Sospecha: NaN/Infinity
    en monto_mxn (json.dumps falla) o fechas raras.
  - fecha_firma siempre NULL → el CSV no trae fecha_firma, usar fecha_inicio.
  - Log truncado por debug excesivo → reducir verbosidad.

v5 fixes:
  - parse_monto rechaza NaN/Infinity
  - fecha_firma = COALESCE(fecha_firma_real, fecha_inicio)
  - Si un batch falla con HTTP 4xx, reintentar fila por fila para identificar bad row
  - Cap de errores impresos (top 5) → log no se trunca
  - Resumen compacto de columnas mapeadas vs ignoradas
"""
import os, sys, csv, io, re, time, math, json, requests
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

BATCH_INSERT = int(os.environ.get('BATCH_INSERT', '100'))
MAX_CONTRATOS = int(os.environ.get('MAX_CONTRATOS', '250000'))
UMBRAL_AD_ALTA_MXN = float(os.environ.get('UMBRAL_AD_ALTA_MXN', '10000000'))
MAX_BAD_ROW_PRINTS = 5

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

print("Tequio · Scraper Compranet (v5)")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE: {'OK' if SUPABASE_URL and SERVICE_KEY else 'MISSING'}")
print(f"  BATCH={BATCH_INSERT}  MAX={MAX_CONTRATOS}")

if not (SUPABASE_URL and SERVICE_KEY):
    sys.exit(1)


_stats = {'http_errs': 0, 'bad_rows_printed': 0, 'no_ocid': 0, 'no_titulo': 0}


def norm_col(s):
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
    """Devuelve float válido o None. Rechaza NaN/Infinity (rompen JSON)."""
    if s is None:
        return None
    s = str(s).strip().replace(',', '').replace('$', '').replace(' ', '')
    if not s or s.lower() in ('null', 'nan', 'inf', '-inf'):
        return None
    try:
        v = float(s)
        if math.isnan(v) or math.isinf(v):
            return None
        # PostgreSQL numeric(20,2) max ~ 10^18; rechazar excesos
        if abs(v) > 1e15:
            return None
        return round(v, 2)
    except Exception:
        return None


# ─── COL_MAP (con variantes de nombre normalizado) ────────────────────────
COL_MAP = {
    # IDs
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
    'descripcion_ramo':              'ramo',
    'ramo':                          'ramo',
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
}


def map_columns(headers):
    """Devuelve dict {col_csv: campo_destino_o_None} y resumen."""
    mapping = {}
    mapped, unmapped = 0, 0
    for col in headers:
        norm = norm_col(col)
        key = COL_MAP.get(norm)
        # Fallback substring
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
    out = {}
    for col, valor in row.items():
        key = col_mapping.get(col)
        if not key:
            continue
        if valor in (None, '', 'NULL'):
            continue
        if key not in out:
            out[key] = valor.strip() if isinstance(valor, str) else valor

    # OCID
    ocid = out.get('ocid_origen') or out.get('codigo_contrato') or out.get('numero_proc')
    if not ocid:
        _stats['no_ocid'] += 1
        return None
    out['ocid'] = f"CN-MX-{str(ocid).strip()}"

    # Limpiar tipos
    monto = parse_monto(out.get('monto_mxn'))
    out['monto_mxn'] = monto
    out['fecha_firma']  = parse_fecha(out.get('fecha_firma'))
    out['fecha_inicio'] = parse_fecha(out.get('fecha_inicio'))
    out['fecha_fin']    = parse_fecha(out.get('fecha_fin'))

    # Fallback: si no hay fecha_firma, usar fecha_inicio
    if not out.get('fecha_firma') and out.get('fecha_inicio'):
        out['fecha_firma'] = out['fecha_inicio']

    if out.get('fecha_inicio') and out.get('fecha_fin'):
        try:
            d1 = datetime.strptime(out['fecha_inicio'], '%Y-%m-%d').date()
            d2 = datetime.strptime(out['fecha_fin'], '%Y-%m-%d').date()
            dd = (d2 - d1).days
            if -3650 < dd < 36500:  # sanity check
                out['duracion_dias'] = dd
        except Exception:
            pass

    out['tipo_procedimiento'] = normalizar_procedimiento(out.get('tipo_procedimiento'))
    out['caracter']           = normalizar_caracter(out.get('caracter'))
    out['tipo_contrato']      = normalizar_categoria(out.get('tipo_contrato'))

    titulo = out.get('titulo') or out.get('descripcion') or ''
    titulo = titulo.strip() if isinstance(titulo, str) else str(titulo)
    if not titulo:
        _stats['no_titulo'] += 1
        return None
    out['titulo'] = titulo[:500]
    if out.get('descripcion'):
        out['descripcion'] = out['descripcion'][:2000]

    out['flag_adjudicacion_directa_alta'] = bool(
        out.get('tipo_procedimiento') == 'adjudicacion_directa' and
        monto is not None and monto > UMBRAL_AD_ALTA_MXN
    )

    # Limpiar campos extra que no van en la tabla
    for k in ['ocid_origen', 'numero_proc', 'codigo_contrato']:
        out.pop(k, None)

    # Sanitizar strings (truncar a longitudes razonables)
    for k in ['dependencia', 'dependencia_codigo', 'unidad_compradora', 'ramo',
              'proveedor_rfc', 'proveedor_nombre', 'proveedor_pais',
              'tipo_contrato', 'entidad_federativa', 'municipio', 'moneda']:
        if k in out and isinstance(out[k], str):
            out[k] = out[k][:255]

    return out


def bulk_upsert(rows):
    """Intenta upsert del batch entero; si falla, intenta fila por fila."""
    if not rows:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/contratos_publicos?on_conflict=ocid"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=rows, headers=h, timeout=60)
        if r.ok:
            return len(rows)
        # Falló batch: log y reintentar individual
        if _stats['http_errs'] < MAX_BAD_ROW_PRINTS:
            _stats['http_errs'] += 1
            print(f"  [HTTP {r.status_code}] batch falló: {r.text[:300]}")
    except Exception as e:
        if _stats['http_errs'] < MAX_BAD_ROW_PRINTS:
            _stats['http_errs'] += 1
            print(f"  [EXC] batch: {e}")

    # Reintentar fila por fila
    ok_count = 0
    for row in rows:
        try:
            rr = requests.post(url, json=[row], headers=h, timeout=30)
            if rr.ok:
                ok_count += 1
            else:
                if _stats['bad_rows_printed'] < MAX_BAD_ROW_PRINTS:
                    _stats['bad_rows_printed'] += 1
                    snippet = json.dumps(row, default=str)[:400]
                    print(f"  [BAD {rr.status_code}] {rr.text[:200]} | row={snippet}")
        except Exception as e:
            if _stats['bad_rows_printed'] < MAX_BAD_ROW_PRINTS:
                _stats['bad_rows_printed'] += 1
                print(f"  [EXC indiv] {e}")
    return ok_count


def refresh_view():
    try:
        r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/refresh_proveedores_agregados",
                          headers=HEADERS_SB, json={}, timeout=60)
        return r.ok
    except Exception:
        return False


def procesar_csv(text):
    delim = detectar_delimitador(text[:5000])
    print(f"  Delimitador: {delim!r}")

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    headers = reader.fieldnames or []
    col_mapping, n_mapped, n_unmapped = map_columns(headers)
    print(f"  Cols total: {len(headers)}  |  Mapeadas: {n_mapped}  |  Ignoradas: {n_unmapped}")
    print(f"  Cols mapeadas:")
    for col, key in col_mapping.items():
        if key:
            print(f"    {col!r} → {key}")

    batch = []
    total_filas = 0
    total_inserted = 0
    total_skipped = 0

    for row in reader:
        total_filas += 1
        if total_filas > MAX_CONTRATOS:
            print(f"  [CAP] Alcanzado MAX_CONTRATOS={MAX_CONTRATOS}")
            break
        parsed = parse_csv_row(row, col_mapping)
        if not parsed:
            total_skipped += 1
            continue
        batch.append(parsed)
        if len(batch) >= BATCH_INSERT:
            ok = bulk_upsert(batch)
            total_inserted += ok
            batch = []
        if total_filas % 10000 == 0:
            print(f"  [{total_filas}] inserted={total_inserted}, skipped={total_skipped}")

    if batch:
        total_inserted += bulk_upsert(batch)

    print(f"\n  Filas procesadas: {total_filas}")
    print(f"  Insertados:       {total_inserted}")
    print(f"  Skipped:          {total_skipped}")
    print(f"  Skip por no_ocid: {_stats['no_ocid']}")
    print(f"  Skip por no_tit:  {_stats['no_titulo']}")
    print(f"  HTTP errors:      {_stats['http_errs']}")
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
            print(f"  [STOP] {inserted_total} contratos cargados, basta.")
            break

    print(f"\nTotal insertado: {inserted_total}")
    print(f"\nRefrescando vista proveedores_agregados...")
    if refresh_view():
        print("  Refresh OK.")
    else:
        print("  [WARN] Refresh falló.")


if __name__ == '__main__':
    main()
