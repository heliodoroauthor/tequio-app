#!/usr/bin/env python3
"""
scrape_banxico.py
"""
import os
import datetime as dt
import requests

SUPABASE_URL = os.environ['SUPABASE_URL'].rstrip('/')
SERVICE_KEY  = os.environ['SUPABASE_SERVICE_ROLE_KEY']
BANXICO_TOKEN = os.environ['BANXICO_TOKEN']

SERIES = {
    'SF43718': ('Tipo de cambio FIX (peso por dólar)', 'MXN/USD'),
    'SF43783': ('Tasa objetivo de Banxico', '%'),
    'SP1':     ('INPC (indice nacional de precios)', 'Indice 2QJUL2018=100'),
    'SP74660': ('Inflación anual', '%'),
    'SL11298': ('Salario mínimo general (zona libre frontera norte)', 'MXN/dia'),
    'SL11296': ('Salario mínimo general (resto del país)', 'MXN/dia'),
    'SR16722': ('Reservas internacionales', 'Millones USD'),
}

HOY = dt.date.today()
DESDE = (HOY - dt.timedelta(days=365)).isoformat()
HASTA = HOY.isoformat()


def get_series_data(serie_id):
    url = (f"https://www.banxico.org.mx/SieAPIRest/service/v1/series/" f"{serie_id}/datos/{DESDE}/{HASTA}")
    r = requests.get(url, headers={'Accept': 'application/json', 'Bmx-Token': BANXICO_TOKEN}, timeout=30)
    if not r.ok:
        print(f"   ⚠️ {serie_id} HTTP {r.status_code}: {r.text[:160]}")
        return []
    try: return r.json()['bmx']['series'][0].get('datos', [])
    except Exception: return []


def parse_fecha(s):
    try:
        d,m,y = s.split('/')
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    except: return None


def parse_valor(v):
    try: return float(str(v).replace(',', '').strip())
    except: return None


def supa_upsert_batch(table, rows, on_conflict):
    if not rows: return 0
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    r = requests.post(url, json=rows, headers={'Content-Type': 'application/json', 'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}', 'Prefer': 'resolution=merge-duplicates,return=minimal'}, timeout=60)
    if not r.ok: print(f"   ⚠️ Supabase {r.status_code}: {r.text[:200]}"); return 0
    return len(rows)


def main():
    print("🚀 Tequio Banxico Scraper")
    print(f"   Rango: {DESDE} → {HASTA}")
    total_filas = 0
    for serie_id, (nombre, unidad) in SERIES.items():
        print(f"\n   📨 {serie_id} — {nombre}")
        datos = get_series_data(serie_id)
        rows = []
        for d in datos:
            fecha = parse_fecha(d.get('fecha', ''))
            valor = parse_valor(d.get('dato', 'N/E'))
            if not fecha or valor is None: continue
            rows.append({'serie_id': serie_id, 'nombre': nombre, 'unidad': unidad, 'fecha': fecha, 'valor': valor})
        n = supa_upsert_batch('econ_banxico', rows, on_conflict='serie_id,fecha')
        print(f"      ✅ {n} puntos guardados")
        total_filas += n
    print(f"\n✅ Banxico scrape completo. {total_filas} filas totales.")


if __name__ == '__main__': main()
