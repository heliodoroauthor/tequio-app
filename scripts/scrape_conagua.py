#!/usr/bin/env python3
"""
scrape_conagua.py
==================
Obtiene 4 tipos de datos de CONAGUA / SMN y los upserta a Supabase:

  1) Pronóstico diario por municipio (~2,471 municipios × 4 días)
  2) Monitor de Sequía por entidad federativa (quincenal)
  3) Alertas meteorológicas activas
  4) Almacenamiento de presas principales

NO requiere token. Es un servicio público.

Variables de entorno requeridas:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY

Diseñado para correr en GitHub Actions (Python 3.11).
"""
import io
import os
import json
import time
import zipfile
import datetime as dt
import requests

SUPABASE_URL = os.environ['SUPABASE_URL'].rstrip('/')
SERVICE_KEY  = os.environ['SUPABASE_SERVICE_ROLE_KEY']

HEADERS_HTTP = {
    'User-Agent': 'TequioCivicBot/1.0 (+https://tequio.app)',
    'Accept': 'application/json, */*',
}
TIMEOUT = 60


def supa_upsert(table, rows, on_conflict):
    if not rows: return 0
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    r = requests.post(url, json=rows, headers={'Content-Type': 'application/json', 'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}', 'Prefer': 'resolution=merge-duplicates,return=minimal'}, timeout=TIMEOUT)
    if not r.ok: print(f"   ⚠️ Supabase {table} {r.status_code}: {r.text[:200]}"); return 0
    return len(rows)


def supa_insert(table, rows):
    if not rows: return 0
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    r = requests.post(url, json=rows, headers={'Content-Type': 'application/json', 'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}', 'Prefer': 'return=minimal'}, timeout=TIMEOUT)
    if not r.ok: print(f"   ⚠️ Supabase {table} {r.status_code}: {r.text[:200]}"); return 0
    return len(rows)


def _num(v):
    try:
        if v in (None, '', '-', 'N/D', 'NA'): return None
        return float(str(v).replace(',', '').strip())
    except Exception: return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def scrape_clima_municipal():
    print("\n🌤️  CONAGUA — pronóstico diario por municipio...")
    candidatas = ["https://smn.conagua.gob.mx/webservices/?method=1", "https://smn.conagua.gob.mx/tools/GUI/webservices/?method=1"]
    data = None
    for url in candidatas:
        try:
            r = requests.get(url, headers=HEADERS_HTTP, timeout=TIMEOUT)
            if not r.ok: continue
            ct = r.headers.get('content-type', '')
            body = r.content
            if 'zip' in ct.lower() or body[:2] == b'PK':
                with zipfile.ZipFile(io.BytesIO(body)) as zf:
                    data = json.loads(zf.read(zf.namelist()[0]).decode('utf-8'))
            else:
                data = json.loads(body.decode('utf-8', errors='ignore'))
            print(f"   ✅ Descarga OK desde {url} ({len(data)} registros)"); break
        except Exception as e: print(f"   ⚠️ Falló {url}: {e}")
    if not data: print("   ❌ Sin datos."); return
    rows = []
    today = dt.date.today()
    for d in data:
        try:
            fecha = d.get('dloc') or d.get('dh') or d.get('fechac')
            if not fecha: fecha = (today + dt.timedelta(days=int(d.get('dia', 0)))).isoformat()
            else: fecha = str(fecha)[:10]
            rows.append({'fecha_pronostico': fecha, 'municipio_id': str(d.get('ides') or d.get('idmun') or d.get('cv', '')), 'municipio': d.get('nmun') or d.get('localidad') or '', 'estado': d.get('nes') or d.get('estado') or '', 'temp_max': _num(d.get('tmax')), 'temp_min': _num(d.get('tmin')), 'prob_lluvia': _int(d.get('probprec') or d.get('prec')), 'desc_cielo': d.get('desciel') or d.get('descip') or '', 'velocidad_viento': _num(d.get('velvien')), 'direccion_viento': d.get('dirvienc') or ''})
        except Exception: continue
    total = 0
    for i in range(0, len(rows), 500): total += supa_upsert('clima_municipal', rows[i:i+500], on_conflict='fecha_pronostico,municipio_id')
    print(f"   📊 {total} pronósticos upserted.")


def scrape_monitor_sequia():
    print("\n🏜️  CONAGUA — Monitor de Sequía por estado...")
    candidatas = ["https://smn.conagua.gob.mx/tools/RESOURCES/Diario/MSM.csv", "https://smn.conagua.gob.mx/tools/RESOURCES/MS/MSE.csv"]
    csv_text = None
    for url in candidatas:
        try:
            r = requests.get(url, headers=HEADERS_HTTP, timeout=TIMEOUT)
            if r.ok and r.text.strip(): csv_text = r.text; print(f"   ✅ Descarga OK desde {url}"); break
        except Exception as e: print(f"   ⚠️ Falló {url}: {e}")
    if not csv_text: print("   ⚠️ Monitor de Sequía no disponible. Skipping."); return
    import csv
    reader = csv.DictReader(io.StringIO(csv_text))
    fecha = dt.date.today().isoformat()
    rows = []
    for r in reader:
        estado = r.get('ESTADO') or r.get('Entidad') or r.get('estado') or ''
        if not estado: continue
        rows.append({'fecha_corte': fecha, 'estado': estado.strip(), 'nivel_sequia': r.get('NIVEL') or r.get('Categoria') or '', 'pct_anomalo_seco': _num(r.get('D0') or r.get('Anomalmente Seco')), 'pct_sequia_moderada': _num(r.get('D1') or r.get('Moderada')), 'pct_sequia_severa': _num(r.get('D2') or r.get('Severa')), 'pct_sequia_extrema': _num(r.get('D3') or r.get('Extrema')), 'pct_sequia_excepcional': _num(r.get('D4') or r.get('Excepcional'))})
    n = supa_upsert('monitor_sequia', rows, on_conflict='fecha_corte,estado')
    print(f"   📊 {n} filas de sequía upserted.")


def scrape_alertas_meteo():
    print("\n⛈️  CONAGUA — alertas vigentes...")
    urls = ["https://smn.conagua.gob.mx/tools/PHP/avisosCAT/avisos_cat_es.json", "https://smn.conagua.gob.mx/webservices/?method=avisos"]
    data = None
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS_HTTP, timeout=TIMEOUT)
            if r.ok: data = r.json(); print(f"   ✅ {url}"); break
        except Exception as e: print(f"   ⚠️ {url}: {e}")
    if not data: print("   ⚠️ Sin endpoint disponible."); return
    items = data if isinstance(data, list) else data.get('avisos', [])
    rows = [{'tipo': a.get('tipo') or a.get('categoria') or 'aviso', 'nombre': a.get('nombre') or a.get('titulo') or '', 'nivel': a.get('nivel') or a.get('color') or '', 'zona_afectada': a.get('zona') or a.get('estados') or '', 'descripcion': (a.get('descripcion') or a.get('detalle') or '')[:2000], 'vigente_desde': a.get('inicio'), 'vigente_hasta': a.get('fin'), 'url_oficial': a.get('url') or ''} for a in items]
    n = supa_insert('alertas_meteo', rows)
    print(f"   📊 {n} alertas insertadas.")


def scrape_presas():
    print("\n💧 CONAGUA — almacenamiento de presas...")
    urls = ["https://sinav30.conagua.gob.mx:8080/Presas/ConsultaPresas/ListaPresas", "https://smn.conagua.gob.mx/tools/PHP/sivea/json/presas.json"]
    data = None
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS_HTTP, timeout=TIMEOUT, verify=False)
            if r.ok: data = r.json(); print(f"   ✅ {url}"); break
        except Exception as e: print(f"   ⚠️ {url}: {e}")
    if not data: print("   ⚠️ Sin datos de presas."); return
    items = data if isinstance(data, list) else (data.get('presas') or data.get('data') or [])
    fecha = dt.date.today().isoformat()
    rows = [{'fecha_corte': fecha, 'presa': p.get('nombrecomun') or p.get('nombre') or p.get('presa') or '', 'estado': p.get('estado') or '', 'capacidad_total_hm3': _num(p.get('namo') or p.get('cap_total')), 'almacenamiento_hm3': _num(p.get('alma_actual') or p.get('almacenamiento')), 'pct_almacenamiento': _num(p.get('almacenaactual') or p.get('porcentaje')), 'region_hidrologica': p.get('region') or ''} for p in items]
    rows = [r for r in rows if r['presa']]
    n = supa_upsert('presas_cuencas', rows, on_conflict='fecha_corte,presa')
    print(f"   📊 {n} presas upserted.")


def main():
    print("🚀 Tequio CONAGUA Scraper")
    print(f"   Supabase: {SUPABASE_URL}")
    scrape_clima_municipal()
    scrape_monitor_sequia()
    scrape_alertas_meteo()
    scrape_presas()
    print("\n✅ CONAGUA scrape completo.")


if __name__ == '__main__':
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    main()
