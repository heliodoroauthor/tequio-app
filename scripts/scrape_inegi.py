#!/usr/bin/env python3
"""
scrape_inegi.py
"""
import os
import time
import datetime as dt
import requests

SUPABASE_URL  = os.environ['SUPABASE_URL'].rstrip('/')
SERVICE_KEY   = os.environ['SUPABASE_SERVICE_ROLE_KEY']
INEGI_TOKEN   = os.environ['INEGI_TOKEN']

INDICADORES = {
    '6207019014': ('Poblacion total', 'Personas', 'BISE'),
    '6207020032': ('Poblacion femenina', 'Personas', 'BISE'),
    '6207020033': ('Poblacion masculina', 'Personas', 'BISE'),
    '6200093906': ('Tasa de desocupacion', '%', 'BIE'),
    '628194':     ('PIB trimestral', 'Millones MXN base 2018', 'BIE'),
    '910393':     ('Tasa de pobreza laboral', '%', 'BIE'),
}

ENTIDADES = {
    '0700': ('nacional', 'Mexico'),
    '01':   ('estado', 'Aguascalientes'),
    '02':   ('estado', 'Baja California'),
    '03':   ('estado', 'Baja California Sur'),
    '04':   ('estado', 'Campeche'),
    '07':   ('estado', 'Chiapas'),
    '08':   ('estado', 'Chihuahua'),
    '09':   ('estado', 'Ciudad de Mexico'),
    '15':   ('estado', 'Estado de Mexico'),
    '14':   ('estado', 'Jalisco'),
    '19':   ('estado', 'Nuevo Leon'),
    '23':   ('estado', 'Quintana Roo'),
    '30':   ('estado', 'Veracruz'),
    '31':   ('estado', 'Yucatan'),
}


def fetch_indicator(indicator_id, area_clave, db):
    url = (f"https://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml/" f"INDICATOR/{indicator_id}/es/{area_clave}/false/{db}/2.0/{INEGI_TOKEN}?type=json")
    try:
        r = requests.get(url, timeout=30)
        if not r.ok:
            print(f"   HTTP {r.status_code} para {indicator_id}/{area_clave}")
            return []
        data = r.json()
        series = data.get('Series', [])
        if not series: return []
        return series[0].get('OBSERVATIONS', [])
    except Exception as e:
        print(f"   Error {indicator_id}/{area_clave}: {e}")
        return []


def parse_period(tp):
    try:
        if '/' in tp:
            y, m = tp.split('/')
            from calendar import monthrange
            d = monthrange(int(y), int(m))[1]
            return f"{p}-{m.zfill(2)}-{str(d).zfill(2)}".replace('{p}', y)
        return f"{tp}-12-31"
    except:
        return None


def supa_upsert(table, rows, on_conflict):
    if not rows: return 0
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    r = requests.post(url, json=rows, headers={'Content-Type': 'application/json', 'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}', 'Prefer': 'resolution=merge-duplicates,return=minimal'}, timeout=60)
    if not r.ok: print(f"   Supabase {r.status_code}: {r.text[:200]}"); return 0
    return len(rows)


def main():
    print("Tequio INEGI Scraper")
    total = 0
    for ind_id, (nombre, unidad, db) in INDICADORES.items():
        print(f"\n   {ind_id} {nombre}")
        rows = []
        for area_clave, (nivel, ubicacion) in ENTIDADES.items():
            obs = fetch_indicator(ind_id, area_clave, db)
            for o in obs:
                fecha = parse_period(o.get('TIME_PERIOD', ''))
                try: valor = float(o.get('OBS_VALUE', '').replace(',', ''))
                except: continue
                if not fecha: continue
                rows.append({'indicador_id': ind_id, 'nombre': nombre, 'unidad': unidad, 'area_geografica': area_clave, 'nivel': nivel, 'ubicacion': ubicacion, 'fecha': fecha, 'valor': valor})
            time.sleep(0.5)
        n = supa_upsert('demograficos_inegi', rows, on_conflict='indicador_id,area_geografica,fecha')
        print(f"   {n} observaciones")
        total += n
    print(f"\nTotal: {total}")


if __name__ == '__main__': main()
