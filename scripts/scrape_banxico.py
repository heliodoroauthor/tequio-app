#!/usr/bin/env python3
"""
scrape_banxico.py (v2 - FIX)
=============================
Cambios respecto v1:
  - Series corregidas segun catalogo oficial Banxico SIE
  - Series viejas/incorrectas reemplazadas
  - Filtrado de N/E (no existente) mas estricto
"""
import os
import datetime as dt
import requests

SUPABASE_URL = os.environ['SUPABASE_URL'].rstrip('/')
SERVICE_KEY  = os.environ['SUPABASE_SERVICE_ROLE_KEY']
BANXICO_TOKEN = os.environ['BANXICO_TOKEN']

SERIES = {
    'SF43718':  ('Tipo de cambio FIX (peso por dolar)',     'MXN/USD'),
    'SF61745':  ('Tasa objetivo de Banxico',                 '%'),
    'SF43936':  ('CETES 28 dias - Tasa anualizada',          '%'),
    'SP1':      ('INPC General (Mensual)',                   'Indice 2QJUL2018=100'),
    'SP30577':  ('Inflacion general anual',                  '%'),
    'SP30578':  ('Inflacion subyacente anual',               '%'),
    'SF311408': ('Reservas internacionales',                 'Millones USD'),
    'SL12089':  ('Salario minimo zona libre frontera norte', 'MXN/dia'),
    'SL12087':  ('Salario minimo general (resto del pais)',  'MXN/dia'),
}

HOY = dt.date.today()
DESDE = (HOY - dt.timedelta(days=365)).isoformat()
HASTA = HOY.isoformat()


def get_series_data(serie_id):
    url = (f"https://www.banxico.org.mx/SieAPIRest/service/v1/series/"
           f"{serie_id}/datos/{DESDE}/{HASTA}")
    try:
        r = requests.get(url, headers={
            'Accept': 'application/json',
            'Bmx-Token': BANXICO_TOKEN,
        }, timeout=30)
        if not r.ok:
            print(f"   [WARN] {serie_id} HTTP {r.status_code}: {r.text[:120]}")
            return []
        data = r.json()
        return data['bmx']['series'][0].get('datos', [])
    except Exception as e:
        print(f"   [WARN] {serie_id} excepcion: {e}")
        return []


def parse_fecha(s):
    try:
        d, m, y = s.split('/')
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    except:
        return None


def parse_valor(v):
    if not v or v in ('N/E', 'NE', '-', ''):
        return None
    try:
        return float(str(v).replace(',', '').strip())
    except:
        return None


def supa_upsert(table, rows, on_conflict):
    if not rows: return 0
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    r = requests.post(url, json=rows, headers={
        'Content-Type': 'application/json',
        'apikey': SERVICE_KEY,
        'Authorization': f'Bearer {SERVICE_KEY}',
        'Prefer': 'resolution=merge-duplicates,return=minimal',
    }, timeout=60)
    if not r.ok:
        print(f"   [WARN] Supabase {r.status_code}: {r.text[:200]}")
        return 0
    return len(rows)


def main():
    print("Tequio Banxico Scraper v2")
    print(f"   Rango: {DESDE} -> {HASTA}")
    total_filas = 0
    for serie_id, (nombre, unidad) in SERIES.items():
        print(f"\n   [{serie_id}] {nombre}")
        datos = get_series_data(serie_id)
        rows = []
        for d in datos:
            fecha = parse_fecha(d.get('fecha', ''))
            valor = parse_valor(d.get('dato', 'N/E'))
            if not fecha or valor is None:
                continue
            rows.append({
                'serie_id': serie_id,
                'nombre':   nombre,
                'unidad':   unidad,
                'fecha':    fecha,
                'valor':    valor,
            })
        n = supa_upsert('econ_banxico', rows, on_conflict='serie_id,fecha')
        print(f"      [OK] {n} puntos guardados")
        total_filas += n
    print(f"\nBanxico scrape completo. {total_filas} filas totales.")


if __name__ == '__main__':
    main()
