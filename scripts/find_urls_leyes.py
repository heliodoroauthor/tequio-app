#!/usr/bin/env python3
"""
find_urls_leyes.py - resolver URLs faltantes en tabla leyes.

Las leyes estatales con fuente=DOF terminaron sin URL porque el DOF no publica
leyes estatales (las publica el Periodico Oficial de cada estado).

Estrategia:
1) SELECT leyes WHERE url IS NULL AND entidad IS NOT NULL
2) Por cada ley, intentar 2 metodos en orden:
   a) Buscar en DuckDuckGo con site: filter (sin API key, scraping HTML)
   b) Si no encuentra, dejar NULL
3) UPDATE solo si encontro PDF .pdf o pagina del sitio oficial

Modo conservador: solo guarda URLs que terminen en .pdf o que esten en el dominio
del estado. Cero invencion.

Env vars: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Args: --batch=N (default 100), --dry-run
"""
import os, sys, time, re, urllib.parse, requests
from bs4 import BeautifulSoup

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

if not (SUPABASE_URL and SERVICE_KEY):
    print("FATAL: vars Supabase missing", flush=True); sys.exit(1)

BATCH = 100
DRY_RUN = False
for a in sys.argv[1:]:
    if a.startswith('--batch='): BATCH = int(a.split('=',1)[1])
    elif a == '--dry-run': DRY_RUN = True

HEADERS = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}
UA = 'Mozilla/5.0 (Tequio civic platform; legitimate scraping for public laws)'

DOMINIOS = {
    'Aguascalientes':['congresoags.gob.mx','eservicios2.aguascalientes.gob.mx'],
    'Baja California':['congresobc.gob.mx'],
    'Baja California Sur':['cbcs.gob.mx'],
    'Campeche':['congresocam.gob.mx'],
    'Chiapas':['congresochiapas.gob.mx'],
    'Chihuahua':['congresochihuahua2.gob.mx','congresochihuahua.gob.mx'],
    'Ciudad de Mexico':['congresocdmx.gob.mx','data.consejeria.cdmx.gob.mx'],
    'Coahuila':['congresocoahuila.gob.mx'],
    'Colima':['congresocol.gob.mx'],
    'Durango':['congresodurango.gob.mx'],
    'Guanajuato':['congresogto.gob.mx'],
    'Guerrero':['congresogro.gob.mx'],
    'Hidalgo':['congreso-hidalgo.gob.mx'],
    'Jalisco':['congresojal.gob.mx'],
    'Mexico':['legislacion.edomex.gob.mx'],
    'Michoacan':['congresomich.gob.mx'],
    'Morelos':['congresomorelos.gob.mx'],
    'Nayarit':['congresonayarit.mx'],
    'Nuevo Leon':['hcnl.gob.mx'],
    'Oaxaca':['congresooaxaca.gob.mx'],
    'Puebla':['congresopuebla.gob.mx'],
    'Queretaro':['legislaturaqro.gob.mx'],
    'Quintana Roo':['congresoqroo.gob.mx'],
    'San Luis Potosi':['congresosanluis.gob.mx'],
    'Sinaloa':['congresosinaloa.gob.mx'],
    'Sonora':['congresoson.gob.mx'],
    'Tabasco':['congresotabasco.gob.mx'],
    'Tamaulipas':['congresotamaulipas.gob.mx'],
    'Tlaxcala':['congresotlaxcala.gob.mx'],
    'Veracruz':['legisver.gob.mx'],
    'Yucatan':['congresoyucatan.gob.mx'],
    'Zacatecas':['congresozac.gob.mx']
}


def fetch_pendientes():
    """SELECT leyes sin URL con entidad conocida. Ordenado por id ASC, cursor-able."""
    url = f"{SUPABASE_URL}/rest/v1/leyes?url=is.null&entidad=not.is.null&select=id,nombre,entidad&order=id.asc&limit={BATCH}"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def buscar_ddg(query, dominio):
    """Scrape DuckDuckGo HTML search. Devuelve primer link en dominio que sea .pdf
    o que pertenezca al dominio."""
    q = f'"{query}" site:{dominio}'
    try:
        r = requests.get(
            'https://html.duckduckgo.com/html/',
            params={'q': q},
            headers={'User-Agent': UA},
            timeout=15
        )
        if not r.ok:
            return None
    except Exception as e:
        print(f'  warn ddg: {e}', flush=True)
        return None

    soup = BeautifulSoup(r.text, 'html.parser')
    # DDG html results: <a class="result__a" href="...">
    for a in soup.select('a.result__a, a.result__url, a[href]'):
        href = a.get('href') or ''
        # DDG redirect: /l/?uddg=<encoded>
        m = re.search(r'uddg=([^&]+)', href)
        if m:
            try:
                href = urllib.parse.unquote(m.group(1))
            except Exception:
                pass
        if dominio in href and (href.endswith('.pdf') or '.pdf?' in href or dominio in href):
            # Solo aceptar si esta en el dominio oficial
            if href.startswith('http'):
                return href
    return None


def patch_url(lid, new_url):
    if DRY_RUN:
        print(f'  [DRY] id={lid} → {new_url[:120]}', flush=True)
        return True
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/leyes?id=eq.{lid}",
        headers={**HEADERS, 'Prefer': 'return=minimal'},
        json={'url': new_url},
        timeout=30
    )
    return r.ok


def main():
    rows = fetch_pendientes()
    print(f'Pendientes en batch: {len(rows)}', flush=True)
    if not rows:
        print('SUMMARY found=0 updated=0 skipped=0', flush=True)
        return

    found = updated = skipped = 0
    for i, r in enumerate(rows):
        ent = (r.get('entidad') or '').strip()
        nombre = (r.get('nombre') or '').strip()
        if not (ent and nombre):
            skipped += 1; continue
        dominios = DOMINIOS.get(ent, [])
        if not dominios:
            skipped += 1; continue

        nuevo_url = None
        for dom in dominios:
            print(f'[{i+1}/{len(rows)}] id={r["id"]} {nombre[:60]} ({ent}) ...', flush=True)
            nuevo_url = buscar_ddg(nombre, dom)
            if nuevo_url:
                break
            time.sleep(2)  # gentle rate limit DDG

        if nuevo_url:
            found += 1
            ok = patch_url(r['id'], nuevo_url)
            if ok:
                updated += 1
                print(f'  → {nuevo_url[:100]}', flush=True)
            else:
                print(f'  PATCH fail id={r["id"]}', flush=True)
        else:
            skipped += 1
        time.sleep(1)  # gentle

    print(f'\nSUMMARY found={found} updated={updated} skipped={skipped} batch={len(rows)}', flush=True)


if __name__ == '__main__':
    main()
