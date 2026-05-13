#!/usr/bin/env python3
"""
scrape_conagua.py (v2 — FIX)
=============================
Cambios respecto v1:
  - URL correcta del SMN: tools/GUI/webservices/?method=1 (validado en
    https://smn.conagua.gob.mx/es/web-service-api)
  - Soporte para JSON gzip-compressed (el ".GZ" del nombre del servicio)
  - Parser de 'dloc' formato YYYYMMDDTHH

Endpoints oficiales SMN:
  method=1 → pronóstico DIARIO por municipio (3 días)
  method=3 → pronóstico HORARIO por municipio (48 horas)

Variables de entorno:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""
import io
import os
import gzip
import json
import datetime as dt
import requests

SUPABASE_URL = os.environ['SUPABASE_URL'].rstrip('/')
SERVICE_KEY  = os.environ['SUPABASE_SERVICE_ROLE_KEY']

HEADERS_HTTP = {
    'User-Agent': 'Mozilla/5.0 (compatible; TequioCivicBot/2.0; +https://tequio.app)',
    'Accept': 'application/json, application/octet-stream, */*',
    'Accept-Encoding': 'gzip',
}
TIMEOUT = 60


def supa_upsert(table, rows, on_conflict):
    if not rows: return 0
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    r = requests.post(url, json=rows, headers={
        'Content-Type': 'application/json',
        'apikey': SERVICE_KEY,
        'Authorization': f'Bearer {SERVICE_KEY}',
        'Prefer': 'resolution=merge-duplicates,return=minimal',
    }, timeout=TIMEOUT)
    if not r.ok:
        print(f"   ⚠️ Supabase {table} {r.status_code}: {r.text[:200]}")
        return 0
    return len(rows)


def _num(v):
    try:
        if v in (None, '', '-', 'N/D', 'NA'): return None
        return float(str(v).replace(',', '').strip())
    except: return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def _parse_dloc(s):
    """SMN usa 'YYYYMMDDTHH' o similar. Devolvemos fecha YYYY-MM-DD."""
    if not s: return None
    s = str(s)
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def scrape_clima_municipal():
    """
    Pronóstico diario por municipio (3 días).
    El endpoint devuelve un cuerpo gzip-compressed con JSON adentro.
    """
    print("\n🌤️  CONAGUA — pronóstico diario por municipio...")
    url = "https://smn.conagua.gob.mx/tools/GUI/webservices/?method=1"
    try:
        r = requests.get(url, headers=HEADERS_HTTP, timeout=TIMEOUT)
        if not r.ok:
            print(f"   ❌ HTTP {r.status_code} desde {url}")
            return
        body = r.content
        # Intentar como gzip primero (es lo más común para este endpoint)
        try:
            text = gzip.decompress(body).decode('utf-8')
        except Exception:
            # Si no es gzip, lo tomamos como JSON directo
            text = body.decode('utf-8', errors='ignore')
        data = json.loads(text)
        print(f"   ✅ {len(data)} registros desde SMN")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return

    rows = []
    for d in data:
        try:
            fecha = _parse_dloc(d.get('dloc') or d.get('dh'))
            if not fecha:
                continue
            rows.append({
                'fecha_pronostico':  fecha,
                'municipio_id':      str(d.get('idmun') or d.get('ides') or ''),
                'municipio':         d.get('nmun') or '',
                'estado':            d.get('nes') or '',
                'temp_max':          _num(d.get('tmax')),
                'temp_min':          _num(d.get('tmin')),
                'prob_lluvia':       _int(d.get('probprec') or d.get('prec')),
                'desc_cielo':        d.get('desciel') or '',
                'velocidad_viento':  _num(d.get('velvien')),
                'direccion_viento':  d.get('dirvienc') or '',
            })
        except Exception:
            continue

    # Upsert en lotes de 500
    total = 0
    for i in range(0, len(rows), 500):
        total += supa_upsert('clima_municipal', rows[i:i+500],
                              on_conflict='fecha_pronostico,municipio_id')
    print(f"   📊 {total} pronósticos guardados en Supabase")


def scrape_alertas_meteo():
    print("\n⛈️  CONAGUA — alertas vigentes (scraping HTML)...")
    url = "https://smn.conagua.gob.mx/es/avisos-tiempo"
    try:
        from bs4 import BeautifulSoup
        r = requests.get(url, headers=HEADERS_HTTP, timeout=TIMEOUT)
        if not r.ok:
            print(f"   ⚠️ HTTP {r.status_code}")
            return
        soup = BeautifulSoup(r.text, 'lxml')
        rows = []
        for art in soup.select('article, .card, .aviso, tr')[:30]:
            titulo = art.find(['h1','h2','h3','h4','strong','td'])
            desc = art.get_text(separator=' ', strip=True)
            if not desc or len(desc) < 30 or 'aviso' not in desc.lower() and 'alerta' not in desc.lower():
                continue
            rows.append({'tipo': 'aviso', 'nombre': (titulo.get_text(strip=True) if titulo else '')[:200], 'nivel': '', 'zona_afectada': '', 'descripcion': desc[:2000], 'url_oficial': url})
        if rows:
            ins_url = f"{SUPABASE_URL}/rest/v1/alertas_meteo"
            r2 = requests.post(ins_url, json=rows, headers={'Content-Type': 'application/json', 'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}', 'Prefer': 'return=minimal'}, timeout=TIMEOUT)
            if r2.ok: print(f"   📊 {len(rows)} alertas insertadas")
            else: print(f"   ⚠️ Insert {r2.status_code}: {r2.text[:200]}")
        else: print("   ℹ️ Sin alertas vigentes en este momento")
    except Exception as e: print(f"   ⚠️ {e}")


def main():
    print("🚀 Tequio CONAGUA Scraper v2")
    print(f"   Supabase: {SUPABASE_URL}")
    scrape_clima_municipal()
    scrape_alertas_meteo()
    print("\n✅ CONAGUA scrape completo.")


if __name__ == '__main__': main()
