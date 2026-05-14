#!/usr/bin/env python3
"""
scrape_votos_individuales.py — Fase 3.1.A.bis "Mi Representante" v3
====================================================================
v1/v2 fallaban porque:
  - Parser buscaba <a href="...dipt=N...">  → ese link NO existe en listados.
  - SITL devuelve nombre como texto en celda; format "Apellido Apellido Nombre".
  - DB politicos_diputados guarda como "Nombre Apellido Apellido".

v3 fix:
  - Parser extrae nombre de cells[1] (texto puro).
  - Match nombre via SORTED WORDS (orden de palabras no importa).
  - Cookie de sesión + Referer (SITL anti-bot).

Endpoints:
  - listados_votacionesnplxvi.php?partidot=X&votaciont=N  (texto plano por partido)

Partidot codes: 1=PRI, 3=PAN, 4=PT, 5=PVEM, 6=MC, 9=IND/SP, 14=MORENA
"""
import os, sys, re, time, requests
from datetime import datetime
from bs4 import BeautifulSoup

import urllib3
urllib3.disable_warnings()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

BASE = 'https://sitl.diputados.gob.mx/LXVI_leg'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
HEADERS_WEB = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'es-MX,es;q=0.9,en;q=0.8',
    'Connection': 'keep-alive',
}
TIMEOUT = 30

PARTIDOS = {
    1:  'PRI',
    3:  'PAN',
    4:  'PT',
    5:  'PVEM',
    6:  'MC',
    9:  'IND',
    14: 'MORENA',
}

SLEEP = float(os.environ.get('SLEEP', '0.1'))
MAX_VOTACIONES = int(os.environ.get('MAX_VOTACIONES', '300'))
DEBUG_FIRST = 3  # primeras 3 votaciones imprimen detalles

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

print("Tequio · Scraper Votos Individuales LXVI (v3)")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE: {'OK' if SUPABASE_URL and SERVICE_KEY else 'MISSING'}")

if not (SUPABASE_URL and SERVICE_KEY):
    sys.exit(1)


def normalizar_voto(s):
    s = (s or '').lower().strip()
    if 'favor' in s: return 'si'
    if 'contra' in s: return 'no'
    if 'abstenci' in s or 'abst' in s: return 'abst'
    if 'ausente' in s or 'asistencia' in s or 'qu' in s: return 'ausente'
    if 'sin sentido' in s: return 'ausente'
    return None


def normalizar_nombre(n):
    """Lowercase, sin acentos, sin puntuación, espacios colapsados."""
    if not n: return ''
    n = n.lower().strip()
    rep = {'á':'a','é':'e','í':'i','ó':'o','ú':'u','ü':'u','ñ':'n'}
    for k,v in rep.items():
        n = n.replace(k, v)
    n = re.sub(r'[.,]+', '', n)
    n = re.sub(r'\s+', ' ', n)
    return n.strip()


def clave_sorted(n):
    """Clave de match: palabras del nombre ordenadas alfabéticamente.
    Robusto a permutaciones de orden (Nombre+Apellido vs Apellido+Nombre)."""
    norm = normalizar_nombre(n)
    palabras = [p for p in norm.split() if p]
    return ' '.join(sorted(palabras))


# ── Session con cookie PHP persistente ────────────────────────────────────
_SESSION = requests.Session()
_SESSION.headers.update(HEADERS_WEB)
_SESSION_INIT = False

def _init_session():
    global _SESSION_INIT
    if _SESSION_INIT: return
    try:
        r = _SESSION.get(f'{BASE}/votaciones_por_periodonplxvi.php', timeout=TIMEOUT, verify=False)
        _SESSION_INIT = True
        print(f"  [SESSION] init OK (status={r.status_code}, cookies={[c.name for c in _SESSION.cookies]})")
    except Exception as e:
        print(f"  [SESSION] init EXC: {e}")


def http_get(url, retries=2, referer=None):
    _init_session()
    h = {}
    if referer: h['Referer'] = referer
    last_err = None
    for i in range(retries + 1):
        try:
            r = _SESSION.get(url, headers=h, timeout=TIMEOUT, verify=False)
            if r.ok and r.text and len(r.text) > 200:
                return r.text
            last_err = f'HTTP {r.status_code} len={len(r.text)}'
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
        if i < retries:
            time.sleep(1 + i)
    return None


# ── Cargar mapa diputados ────────────────────────────────────────────────
def cargar_diputados():
    """Devuelve dict clave_sorted → dipt_id."""
    url = f"{SUPABASE_URL}/rest/v1/politicos_diputados?select=dipt_id,nombre&limit=600"
    r = requests.get(url, headers=HEADERS_SB, timeout=20)
    if not r.ok:
        print(f"ERR diputados: {r.status_code}")
        return {}
    mapa = {}
    colisiones = 0
    for d in r.json():
        nombre = (d.get('nombre') or '').strip()
        if not nombre:
            continue
        k = clave_sorted(nombre)
        if k in mapa:
            colisiones += 1
            continue
        mapa[k] = d['dipt_id']
    print(f"  Diputados mapeados: {len(mapa)} (colisiones: {colisiones})")
    return mapa


# ── Parseo de listados por partido ───────────────────────────────────────
def parse_votos_html(html, debug=False):
    """Extrae lista de (nombre_raw, voto_norm) del HTML."""
    soup = BeautifulSoup(html, 'lxml')
    rows = []
    for tr in soup.find_all('tr'):
        cells = tr.find_all('td')
        if len(cells) < 3:
            continue
        # cells[0] = número de fila (esperamos dígito)
        nro = cells[0].get_text(strip=True)
        if not nro.isdigit():
            continue
        nombre = cells[1].get_text(strip=True)
        voto = cells[2].get_text(strip=True)
        if not nombre or not voto:
            continue
        rows.append((nombre, voto))
    if debug:
        print(f"    [PARSE] {len(rows)} filas, sample: {rows[:2]}")
    return rows


def extraer_votos_partido(votacion_id, partidot, mapa, debug=False):
    """Para una votación + partido, devuelve list[(dipt_id, voto_normalizado)]."""
    url = f"{BASE}/listados_votacionesnplxvi.php?partidot={partidot}&votaciont={votacion_id}"
    referer = f"{BASE}/estadistico_votacionnplxvi.php?votaciont={votacion_id}"
    html = http_get(url, referer=referer)
    if not html:
        if debug:
            print(f"    [HTTP fail] vot={votacion_id} partidot={partidot}")
        return []
    if debug:
        print(f"    [HTML] vot={votacion_id} partidot={partidot} len={len(html)}")
    raw_rows = parse_votos_html(html, debug=debug)

    out = []
    no_match = []
    for nombre, voto_raw in raw_rows:
        k = clave_sorted(nombre)
        dipt_id = mapa.get(k)
        if not dipt_id:
            no_match.append(nombre)
            continue
        voto = normalizar_voto(voto_raw)
        if voto:
            out.append((dipt_id, voto))
    if debug and no_match:
        print(f"    [NO MATCH] {len(no_match)} nombres no encontrados. Ejemplo: {no_match[:3]}")
    return out


# ── Persistencia ─────────────────────────────────────────────────────────
def bulk_insert_votos(rows):
    if not rows: return 0
    url = f"{SUPABASE_URL}/rest/v1/votos_individuales?on_conflict=votacion_id,dipt_id"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=rows, headers=h, timeout=30)
        if r.ok:
            return len(rows)
        print(f"    [UPSERT FAIL] {r.status_code}: {r.text[:200]}")
        return 0
    except Exception as e:
        print(f"    [UPSERT EXC] {e}")
        return 0


def cargar_votaciones():
    url = f"{SUPABASE_URL}/rest/v1/votaciones_diputados?select=votacion_id&order=fecha.desc&limit={MAX_VOTACIONES}"
    r = requests.get(url, headers=HEADERS_SB, timeout=20)
    if not r.ok: return []
    return [row['votacion_id'] for row in r.json()]


def main():
    t0 = datetime.now()
    print("\n[1] Mapa diputados...")
    mapa = cargar_diputados()
    if not mapa:
        sys.exit(1)

    print("\n[2] Lista de votaciones...")
    vot_ids = cargar_votaciones()
    print(f"  Total: {len(vot_ids)}")

    total_votos = 0
    total_votaciones_con_data = 0

    for i, vot_id in enumerate(vot_ids, 1):
        debug = (i <= DEBUG_FIRST)
        if debug:
            print(f"\n[{i}/{len(vot_ids)}] votacion={vot_id}")
        votos = []
        for partidot, nombre_p in PARTIDOS.items():
            v = extraer_votos_partido(vot_id, partidot, mapa, debug=debug)
            if debug:
                print(f"  partidot={partidot} ({nombre_p}) → {len(v)} votos válidos")
            for dipt_id, voto in v:
                votos.append({
                    'votacion_id': vot_id,
                    'dipt_id': dipt_id,
                    'voto': voto,
                })
            time.sleep(SLEEP)

        if votos:
            ok = bulk_insert_votos(votos)
            total_votos += ok
            total_votaciones_con_data += 1

        if i % 20 == 0 or i == len(vot_ids):
            elapsed = (datetime.now() - t0).total_seconds()
            rate = total_votos / max(elapsed, 1)
            print(f"  [{i}/{len(vot_ids)}] insertados={total_votos} ({rate:.0f} v/s) votaciones_con_data={total_votaciones_con_data}")

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n══ Resumen ({elapsed:.0f}s) ══")
    print(f"  Votos individuales insertados: {total_votos}")
    print(f"  Votaciones con datos:           {total_votaciones_con_data}/{len(vot_ids)}")


if __name__ == '__main__':
    main()
