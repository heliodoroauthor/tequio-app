#!/usr/bin/env python3
"""
scrape_inegi.py (v2 - FIX)
===========================
Cambios respecto v1:
  - Indicadores corregidos: usamos IDs del Censo 2020 (BISE).
  - Diccionario completo de 32 entidades federativas.
  - Mejor manejo de fechas y errores.
"""
import os
import time
import requests
from calendar import monthrange

SUPABASE_URL  = os.environ['SUPABASE_URL'].rstrip('/')
SERVICE_KEY   = os.environ['SUPABASE_SERVICE_ROLE_KEY']
INEGI_TOKEN   = os.environ['INEGI_TOKEN']

INDICADORES = {
    '1002000001': ('Poblacion total',             'Personas',  'BISE'),
    '1002000002': ('Poblacion femenina',          'Personas',  'BISE'),
    '1002000003': ('Poblacion masculina',         'Personas',  'BISE'),
    '446471':     ('Tasa de desocupacion (ENOE)', '%',         'BISE'),
    '6207020059': ('Poblacion en pobreza',        'Personas',  'BISE'),
    '6207019035': ('Promedio de escolaridad',     'Anios',     'BISE'),
}

ENTIDADES = {
    '0700': ('nacional', 'Mexico'),
    '01':   ('estado', 'Aguascalientes'),
    '02':   ('estado', 'Baja California'),
    '03':   ('estado', 'Baja California Sur'),
    '04':   ('estado', 'Campeche'),
    '05':   ('estado', 'Coahuila'),
    '06':   ('estado', 'Colima'),
    '07':   ('estado', 'Chiapas'),
    '08':   ('estado', 'Chihuahua'),
    '09':   ('estado', 'Ciudad de Mexico'),
    '10':   ('estado', 'Durango'),
    '11':   ('estado', 'Guanajuato'),
    '12':   ('estado', 'Guerrero'),
    '13':   ('estado', 'Hidalgo'),
    '14':   ('estado', 'Jalisco'),
    '15':   ('estado', 'Mexico (Estado de)'),
    '16':   ('estado', 'Michoacan'),
    '17':   ('estado', 'Morelos'),
    '18':   ('estado', 'Nayarit'),
    '19':   ('estado', 'Nuevo Leon'),
    '20':   ('estado', 'Oaxaca'),
    '21':   ('estado', 'Puebla'),
    '22':   ('estado', 'Queretaro'),
    '23':   ('estado', 'Quintana Roo'),
    '24':   ('estado', 'San Luis Potosi'),
    '25':   ('estado', 'Sinaloa'),
    '26':   ('estado', 'Sonora'),
    '27':   ('estado', 'Tabasco'),
    '28':   ('estado', 'Tamaulipas'),
    '29':   ('estado', 'Tlaxcala'),
    '30':   ('estado', 'Veracruz'),
    '31':   ('estado', 'Yucatan'),
    '32':   ('estado', 'Zacatecas'),
}


def fetch_indicator(indicator_id, area_clave, db):
    url = (f"https://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml/"
           f"INDICATOR/{indicator_id}/es/{area_clave}/false/{db}/2.0/{INEGI_TOKEN}?type=json")
    try:
        r = requests.get(url, timeout=30)
        if not r.ok:
            return []
        data = r.json()
        series = data.get('Series', [])
        if not series:
            return []
        return series[0].get('OBSERVATIONS', [])
    except Exception:
        return []


def parse_period(tp):
    try:
        if '/' in tp:
            y, m = tp.split('/')
            d = monthrange(int(y), int(m))[1]
            return f"{y}-{m.zfill(2)}-{str(d).zfill(2)}"
        return f"{tp}-12-31"
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
    print("Tequio INEGI Scraper v2")
    print(f"   {len(INDICADORES)} indicadores x {len(ENTIDADES)} ubicaciones")
    total = 0
    for ind_id, (nombre, unidad, db) in INDICADORES.items():
        print(f"\n   [{ind_id}] {nombre}")
        rows = []
        ok_areas = 0
        for area_clave, (nivel, ubicacion) in ENTIDADES.items():
            obs = fetch_indicator(ind_id, area_clave, db)
            if obs:
                ok_areas += 1
            for o in obs:
                fecha = parse_period(o.get('TIME_PERIOD', ''))
                try:
                    valor = float(str(o.get('OBS_VALUE', '')).replace(',', ''))
                except:
                    continue
                if not fecha:
                    continue
                rows.append({
                    'indicador_id':    ind_id,
                    'nombre':          nombre,
                    'unidad':          unidad,
                    'area_geografica': area_clave,
                    'nivel':           nivel,
                    'ubicacion':       ubicacion,
                    'fecha':           fecha,
                    'valor':           valor,
                })
            time.sleep(0.3)
        n = supa_upsert('demograficos_inegi', rows,
                        on_conflict='indicador_id,area_geografica,fecha')
        print(f"      [OK] {n} observaciones guardadas ({ok_areas}/{len(ENTIDADES)} ubicaciones con datos)")
        total += n
    print(f"\nINEGI scrape completo. {total} filas totales.")


if __name__ == '__main__':
    main()
