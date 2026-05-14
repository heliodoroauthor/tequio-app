#!/usr/bin/env python3
"""
scrape_compranet.py — Fase 3.1.C ComprasMX/Compranet
=====================================================
Conecta Tequio a la API oficial de contrataciones abiertas APF.

Fuente:
  - API: https://api.datos.gob.mx/v2/contratacionesabiertas
  - Formato: OCDS (Open Contracting Data Standard) v1.1
  - Estructura: recordPackages → records[].compiledRelease.*

Flujo:
  1. Iterar páginas hasta agotar o llegar a MAX_CONTRATOS
  2. Por cada recordPackage, extraer compiledRelease y mapear a tabla contratos_publicos
  3. Bulk upsert por ocid
  4. Al final, refresh materialized view proveedores_agregados
"""
import os, sys, time, json, requests
from datetime import datetime
from decimal import Decimal

import urllib3
urllib3.disable_warnings()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

API_BASE = 'https://api.datos.gob.mx/v2/contratacionesabiertas'
UA = 'Mozilla/5.0 (compatible; TequioBot/1.0; +https://tequio.app)'
HEADERS_WEB = {'User-Agent': UA, 'Accept': 'application/json'}
TIMEOUT = 60

PAGE_SIZE = int(os.environ.get('PAGE_SIZE', '500'))
MAX_PAGES = int(os.environ.get('MAX_PAGES', '500'))     # ~250k contratos cap
MAX_CONTRATOS = int(os.environ.get('MAX_CONTRATOS', '250000'))
BATCH_INSERT = int(os.environ.get('BATCH_INSERT', '200'))
SLEEP_BETWEEN_PAGES = float(os.environ.get('SLEEP', '0.5'))

# Umbral para flag de adjudicación directa alta (en MXN)
UMBRAL_AD_ALTA_MXN = float(os.environ.get('UMBRAL_AD_ALTA_MXN', '10000000'))  # $10M

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

print("Tequio · Scraper Compranet/ComprasMX (OCDS)")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE: {'OK' if SUPABASE_URL and SERVICE_KEY else 'MISSING'}")
print(f"  PAGE_SIZE: {PAGE_SIZE}, MAX_PAGES: {MAX_PAGES}, MAX_CONTRATOS: {MAX_CONTRATOS}")

if not (SUPABASE_URL and SERVICE_KEY):
    sys.exit(1)


# ── Helpers ─────────────────────────────────────────────────────
def safe_get(d, *keys, default=None):
    """Navega un dict anidado: safe_get(rec, 'tender', 'title')"""
    for k in keys:
        if d is None:
            return default
        if isinstance(d, list):
            if k == 0 or k == '0':
                d = d[0] if d else None
            else:
                return default
        else:
            d = d.get(k) if hasattr(d, 'get') else None
    return d if d is not None else default


def safe_first(lst):
    """Devuelve primer elemento de lista o None."""
    return lst[0] if (lst and isinstance(lst, list)) else None


def to_date(s):
    """Convierte ISO 8601 a 'YYYY-MM-DD'."""
    if not s:
        return None
    try:
        if 'T' in str(s):
            return str(s).split('T')[0]
        if len(str(s)) >= 10:
            return str(s)[:10]
    except Exception:
        pass
    return None


def to_decimal(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def normalizar_procurement_method(m, detail=None):
    """Mapea OCDS procurementMethod + detail a categoría humana."""
    m = (m or '').lower()
    detail_low = (detail or '').lower()
    if 'open' in m or 'licitación pública' in detail_low or 'licitacion publica' in detail_low:
        return 'licitacion_publica'
    if 'selective' in m or 'invitación' in detail_low or 'invitacion' in detail_low:
        return 'invitacion_tres'
    if 'limited' in m or 'direct' in m or 'adjudicación' in detail_low or 'adjudicacion' in detail_low:
        return 'adjudicacion_directa'
    return None


def normalizar_categoria(c):
    c = (c or '').lower()
    if 'goods' in c or 'bienes' in c or 'adquisi' in c:
        return 'adquisiciones'
    if 'works' in c or 'obra' in c:
        return 'obras'
    if 'services' in c or 'servicio' in c:
        return 'servicios'
    return c if c else None


def normalizar_caracter(c):
    c = (c or '').lower()
    if 'inter' in c:
        return 'internacional'
    if 'nacional' in c or 'national' in c:
        return 'nacional'
    return c if c else None


# Mapeo estado (parsing simple desde nombres de dependencia / unidad compradora)
ESTADOS_MX = [
    'Aguascalientes','Baja California Sur','Baja California','Campeche','Coahuila','Colima',
    'Chiapas','Chihuahua','Ciudad de México','CDMX','Durango','Guanajuato','Guerrero',
    'Hidalgo','Jalisco','México','Michoacán','Morelos','Nayarit','Nuevo León','Oaxaca',
    'Puebla','Querétaro','Quintana Roo','San Luis Potosí','Sinaloa','Sonora','Tabasco',
    'Tamaulipas','Tlaxcala','Veracruz','Yucatán','Zacatecas',
]

def detectar_entidad(*textos):
    """Busca un estado mexicano en los textos dados."""
    blob = ' '.join(str(t or '') for t in textos)
    for e in ESTADOS_MX:
        if e.lower() in blob.lower():
            return 'Ciudad de México' if e in ('Ciudad de México','CDMX') else e
    return None


# ── Extracción ──────────────────────────────────────────────────
def parse_compiled_release(record):
    """Toma un record OCDS y devuelve un dict listo para insertar."""
    cr = record.get('compiledRelease') or record  # algunos packages no anidan
    ocid = cr.get('ocid') or record.get('ocid')
    if not ocid:
        return None

    # Parties: lookup roles
    parties = {p.get('id'): p for p in (cr.get('parties') or []) if p.get('id')}
    buyer_ref = safe_get(cr, 'buyer', 'id')
    buyer = parties.get(buyer_ref, cr.get('buyer') or {})

    dependencia = buyer.get('name')
    dependencia_codigo = safe_get(buyer, 'identifier', 'id') or safe_get(buyer, 'identifier', 'legalName')

    # Unidad compradora: en OCDS-MX puede venir como party con role 'procuringEntity'
    pe = None
    for p in (cr.get('parties') or []):
        if 'procuringEntity' in (p.get('roles') or []):
            pe = p
            break
    unidad = (pe or {}).get('name')

    # Ramo: a veces declarado en planning.budget o como tag en parties
    ramo = safe_get(cr, 'planning', 'budget', 'budgetBreakdown', 0, 'classifications', 0, 'description')

    # Tender
    tender = cr.get('tender') or {}
    titulo = tender.get('title') or ''
    descripcion = tender.get('description')
    proc_method = tender.get('procurementMethod')
    proc_method_detail = tender.get('procurementMethodDetails')
    tipo_proc = normalizar_procurement_method(proc_method, proc_method_detail)
    categoria = normalizar_categoria(tender.get('mainProcurementCategory'))
    caracter = normalizar_caracter(safe_get(tender, 'additionalProcurementCategories', 0))

    # Awards (toma el primer award activo)
    awards = cr.get('awards') or []
    award = None
    for a in awards:
        if (a.get('status') or '').lower() == 'active' or a.get('status') is None:
            award = a
            break
    if not award and awards:
        award = awards[0]

    proveedor_rfc = None
    proveedor_nombre = None
    proveedor_pais = 'MX'
    monto = None
    fecha_award = None
    if award:
        suppliers = award.get('suppliers') or []
        sup = safe_first(suppliers)
        if sup:
            proveedor_nombre = sup.get('name')
            proveedor_rfc = safe_get(sup, 'identifier', 'id')
        monto = to_decimal(safe_get(award, 'value', 'amount'))
        fecha_award = to_date(award.get('date'))

    # Contract (toma el primero)
    contracts = cr.get('contracts') or []
    contract = safe_first(contracts)
    fecha_firma = fecha_award
    fecha_inicio = None
    fecha_fin = None
    if contract:
        fecha_firma = to_date(contract.get('dateSigned')) or fecha_firma
        fecha_inicio = to_date(safe_get(contract, 'period', 'startDate'))
        fecha_fin = to_date(safe_get(contract, 'period', 'endDate'))
        if monto is None:
            monto = to_decimal(safe_get(contract, 'value', 'amount'))
        if not titulo:
            titulo = contract.get('title') or ''

    if not titulo:
        return None

    # Duración días
    duracion = None
    if fecha_inicio and fecha_fin:
        try:
            d1 = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            d2 = datetime.strptime(fecha_fin, '%Y-%m-%d').date()
            duracion = (d2 - d1).days
        except Exception:
            pass

    # Entidad federativa heurística
    entidad = detectar_entidad(dependencia, unidad, descripcion, titulo)

    # Flag adjudicación directa alta
    flag_ad = (tipo_proc == 'adjudicacion_directa') and (monto is not None) and (monto > UMBRAL_AD_ALTA_MXN)

    # URL de origen
    fuente_url = None
    docs = (tender.get('documents') or [])
    if docs and docs[0].get('url'):
        fuente_url = docs[0].get('url')

    return {
        'ocid': ocid,
        'dependencia': dependencia,
        'dependencia_codigo': dependencia_codigo,
        'unidad_compradora': unidad,
        'ramo': ramo,
        'proveedor_rfc': proveedor_rfc,
        'proveedor_nombre': proveedor_nombre,
        'proveedor_pais': proveedor_pais,
        'titulo': titulo[:500],
        'descripcion': (descripcion or '')[:2000] if descripcion else None,
        'monto_mxn': monto,
        'moneda': 'MXN',
        'fecha_firma': fecha_firma,
        'fecha_inicio': fecha_inicio,
        'fecha_fin': fecha_fin,
        'duracion_dias': duracion,
        'tipo_procedimiento': tipo_proc,
        'caracter': caracter,
        'tipo_contrato': categoria,
        'entidad_federativa': entidad,
        'fuente_url': fuente_url,
        'ocds_release_id': cr.get('id'),
        'flag_adjudicacion_directa_alta': flag_ad,
    }


def fetch_page(page):
    """Pide una página y devuelve (records[], hasMore)."""
    url = f"{API_BASE}?page={page}&pageSize={PAGE_SIZE}"
    try:
        r = requests.get(url, headers=HEADERS_WEB, timeout=TIMEOUT, verify=False)
        if not r.ok:
            return [], False, f'HTTP {r.status_code}'
        data = r.json()
        records = data.get('records') or data.get('data') or []
        # Heurística "hay más": si recibimos PAGE_SIZE records, asumir hay más
        return records, len(records) >= PAGE_SIZE, None
    except Exception as e:
        return [], False, f'{type(e).__name__}: {e}'


def bulk_upsert(rows):
    if not rows:
        return 0, ''
    url = f"{SUPABASE_URL}/rest/v1/contratos_publicos?on_conflict=ocid"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=rows, headers=h, timeout=60)
        if r.ok:
            return len(rows), ''
        return 0, f'HTTP {r.status_code}: {r.text[:300]}'
    except Exception as e:
        return 0, f'{type(e).__name__}: {e}'


def refresh_materialized_view():
    """Llama un RPC para refrescar proveedores_agregados."""
    # PostgREST no permite REFRESH MATERIALIZED VIEW directo; usamos RPC
    # En su defecto, intentamos via execute_sql endpoint
    url = f"{SUPABASE_URL}/rest/v1/rpc/refresh_proveedores_agregados"
    try:
        r = requests.post(url, headers=HEADERS_SB, json={}, timeout=60)
        return r.ok
    except Exception:
        return False


# ── Main ────────────────────────────────────────────────────────
def main():
    print("\n[1] Iniciando extracción OCDS paginada...")
    total_records = 0
    total_inserted = 0
    total_skipped = 0
    total_errors = 0

    batch_buffer = []
    page = 1

    while page <= MAX_PAGES and total_records < MAX_CONTRATOS:
        records, has_more, err = fetch_page(page)
        if err:
            print(f"  [WARN page={page}] {err}")
            total_errors += 1
            if total_errors > 5:
                print("  [ABORT] Demasiados errores consecutivos.")
                break
            time.sleep(2)
            page += 1
            continue

        total_errors = 0  # reset

        if not records:
            print(f"  [DONE] Página {page} vacía. Fin de datos.")
            break

        # Parse cada record
        for rec in records:
            parsed = parse_compiled_release(rec)
            if parsed:
                batch_buffer.append(parsed)
                total_records += 1
            else:
                total_skipped += 1

        # Flush por lote
        while len(batch_buffer) >= BATCH_INSERT:
            chunk = batch_buffer[:BATCH_INSERT]
            batch_buffer = batch_buffer[BATCH_INSERT:]
            inserted, err = bulk_upsert(chunk)
            total_inserted += inserted
            if err:
                print(f"  [INSERT ERR] {err[:200]}")

        if page % 5 == 0:
            print(f"  Page {page} → {total_records} records vistos, {total_inserted} insertados")

        page += 1
        if not has_more:
            print(f"  [DONE] Última página alcanzada.")
            break

        time.sleep(SLEEP_BETWEEN_PAGES)

    # Flush final
    if batch_buffer:
        inserted, err = bulk_upsert(batch_buffer)
        total_inserted += inserted
        if err:
            print(f"  [FINAL INSERT ERR] {err[:200]}")

    print(f"\n[2] Resumen:")
    print(f"  Records vistos: {total_records}")
    print(f"  Inserted/upserted: {total_inserted}")
    print(f"  Skipped (sin titulo o sin ocid): {total_skipped}")

    print(f"\n[3] Refrescando vista materializada proveedores_agregados...")
    if refresh_materialized_view():
        print("  Refresh OK.")
    else:
        print("  [WARN] Refresh falló. Ejecutar manualmente: REFRESH MATERIALIZED VIEW proveedores_agregados;")

    print("\nScrape Compranet completo.")


if __name__ == '__main__':
    main()
