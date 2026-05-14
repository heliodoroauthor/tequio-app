#!/usr/bin/env python3
"""
scrape_votos_individuales.py — Fase 3.1.A.bis "Mi Representante"
=================================================================
Para cada una de las 260 votaciones nominales × cada uno de los 7 partidos,
extrae el voto individual de cada diputado.

Endpoints:
  - estadistico_votacionnplxvi.php?votaciont=N  → totales por partido
  - listados_votacionesnplxvi.php?partidot=X&votaciont=N  → voto individual

Mapping partidot:
  1=PRI, 3=PAN, 4=PT, 5=PVEM, 6=MC, 9=IND, 14=MORENA
"""
import os, sys, re, time, requests
from datetime import datetime

import urllib3
urllib3.disable_warnings()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

BASE = 'https://sitl.diputados.gob.mx/LXVI_leg'
UA = 'Mozilla/5.0 (compatible; TequioBot/1.0; +https://tequio.app)'
HEADERS_WEB = {'User-Agent': UA, 'Accept-Language': 'es-MX,es;q=0.9'}
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

SLEEP = float(os.environ.get('SLEEP', '0.15'))
MAX_VOTACIONES = int(os.environ.get('MAX_VOTACIONES', '300'))

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

print("Tequio · Scraper Votos Individuales LXVI")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE: {'OK' if SUPABASE_URL and SERVICE_KEY else 'MISSING'}")

if not (SUPABASE_URL and SERVICE_KEY):
    sys.exit(1)

# Normalización votos
def normalizar_voto(s):
    s = (s or '').lower().strip()
    if 'favor' in s: return 'si'
    if 'contra' in s: return 'no'
    if 'abstenci' in s or 'abst' in s: return 'abst'
    if 'asistencia' in s or 'ausente' in s or 'quórum' in s or 'quorum' in s: return 'ausente'
    return None


def http_get(url, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS_WEB, timeout=TIMEOUT, verify=False)
            if r.ok:
                return r.text
            last = f'HTTP {r.status_code}'
        except Exception as e:
            last = f'{type(e).__name__}: {e}'
        if i < retries:
            time.sleep(1)
    return None


def cargar_diputados_nombre_to_id():
    """Lee politicos_diputados y construye dict nombre_normalizado → dipt_id."""
    url = f"{SUPABASE_URL}/rest/v1/politicos_diputados?select=dipt_id,nombre&limit=600"
    r = requests.get(url, headers=HEADERS_SB, timeout=20)
    if not r.ok:
        print(f"ERR cargando diputados: {r.status_code}")
        return {}
    mapa = {}
    for d in r.json():
        nombre = d.get('nombre', '').strip()
        if nombre:
            # Múltiples variantes de normalización para match robusto
            clave = normalizar_nombre(nombre)
            mapa[clave] = d['dipt_id']
    print(f"  Diputados cargados: {len(mapa)}")
    return mapa


def normalizar_nombre(n):
    """Normaliza nombre para matching robusto."""
    n = (n or '').lower().strip()
    # Quitar acentos
    n = (n.replace('á','a').replace('é','e').replace('í','i')
           .replace('ó','o').replace('ú','u').replace('ñ','n')
           .replace('Á','a').replace('É','e').replace('Í','i')
           .replace('Ó','o').replace('Ú','u').replace('Ñ','n'))
    # Quitar puntos y espacios extra
    n = re.sub(r'[.,]+', '', n)
    n = re.sub(r'\s+', ' ', n)
    return n.strip()


def extraer_votos_partido(votacion_id, partidot, nombre_to_id):
    """Devuelve lista de (dipt_id, voto) para todos los diputados de un partido en una votación."""
    from bs4 import BeautifulSoup
    url = f"{BASE}/listados_votacionesnplxvi.php?partidot={partidot}&votaciont={votacion_id}"
    html = http_get(url)
    if not html:
        return []
    soup = BeautifulSoup(html, 'lxml')

    votos = []
    # Cada diputado está en una <tr> con: nro, nombre (link a curricula), voto
    for tr in soup.find_all('tr'):
        cells = tr.find_all('td')
        if len(cells) < 3:
            continue
        # cells: [nro, nombre_link, voto] o similar
        link = tr.find('a', href=re.compile(r'dipt=\d+'))
        if not link:
            continue
        mdip = re.search(r'dipt=(\d+)', link.get('href', ''))
        if not mdip:
            continue
        dipt_id = int(mdip.group(1))
        # Extraer voto: última celda con texto significativo
        voto_text = ''
        for cell in reversed(cells):
            t = cell.get_text(strip=True)
            if t and not t.isdigit():
                voto_text = t
                break
        voto = normalizar_voto(voto_text)
        if voto:
            votos.append((dipt_id, voto))
    return votos


def extraer_totales(votacion_id):
    """Lee estadistico_votacionnplxvi.php y extrae totales globales y resultado."""
    from bs4 import BeautifulSoup
    url = f"{BASE}/estadistico_votacionnplxvi.php?votaciont={votacion_id}"
    html = http_get(url)
    if not html:
        return None
    soup = BeautifulSoup(html, 'lxml')
    texto = soup.get_text(separator='\n')

    # Buscar fila "TOTAL" en la tabla
    # Formato: GRUPO PARLAMENTARIO  A FAVOR  EN CONTRA  ABSTENCIÓN  SOLO ASISTENCIA  AUSENTE  TOTAL
    # Iterar tr buscando primera col 'TOTAL'
    total_si = total_no = total_abst = total_aus = 0
    for tr in soup.find_all('tr'):
        cells = [c.get_text(strip=True) for c in tr.find_all(['td','th'])]
        if not cells:
            continue
        if cells[0].upper() == 'TOTAL':
            # Esperamos 7 celdas: TOTAL, sí, no, abst, asistencia, ausente, total
            nums = []
            for c in cells[1:]:
                try:
                    nums.append(int(c.replace(',','')))
                except Exception:
                    nums.append(None)
            if len(nums) >= 5 and nums[0] is not None:
                total_si  = nums[0] or 0
                total_no  = nums[1] or 0
                total_abst = nums[2] or 0
                total_aus = (nums[3] or 0) + (nums[4] or 0) if len(nums) > 4 else (nums[3] or 0)
            break

    resultado = None
    if total_si + total_no > 0:
        resultado = 'aprobada' if total_si > total_no else 'rechazada'
    return {
        'total_si': total_si,
        'total_no': total_no,
        'total_abst': total_abst,
        'total_ausente': total_aus,
        'resultado': resultado,
    }


def bulk_insert_votos(rows):
    """Inserta lote de votos. Manejo de conflicto: ignora duplicados via on_conflict."""
    if not rows:
        return 0
    url = f"{SUPABASE_URL}/rest/v1/votos_individuales?on_conflict=votacion_id,dipt_id"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=rows, headers=h, timeout=30)
        return len(rows) if r.ok else 0
    except Exception as e:
        return 0


def update_totales(votacion_id, totales):
    if not totales:
        return False
    url = f"{SUPABASE_URL}/rest/v1/votaciones_diputados?votacion_id=eq.{votacion_id}"
    h = {**HEADERS_SB, 'Prefer': 'return=minimal'}
    try:
        r = requests.patch(url, json=totales, headers=h, timeout=20)
        return r.ok
    except Exception:
        return False


def cargar_votaciones():
    """Lee la lista de votacion_ids ya scrapeadas."""
    url = f"{SUPABASE_URL}/rest/v1/votaciones_diputados?select=votacion_id&order=fecha.desc&limit={MAX_VOTACIONES}"
    r = requests.get(url, headers=HEADERS_SB, timeout=20)
    if not r.ok:
        return []
    return [row['votacion_id'] for row in r.json()]


def main():
    print("\n[1] Cargando mapa nombre → dipt_id desde Supabase...")
    nombre_to_id = cargar_diputados_nombre_to_id()
    if not nombre_to_id:
        print("ERR: sin diputados en DB")
        sys.exit(1)

    print("\n[2] Cargando lista de votaciones...")
    vot_ids = cargar_votaciones()
    print(f"  Total votaciones: {len(vot_ids)}")

    total_votos_insertados = 0
    total_totales_actualizados = 0

    for i, vot_id in enumerate(vot_ids, 1):
        votos_de_esta_votacion = []
        for partidot, nombre_p in PARTIDOS.items():
            votos = extraer_votos_partido(vot_id, partidot, nombre_to_id)
            for dipt_id, voto in votos:
                votos_de_esta_votacion.append({
                    'votacion_id': vot_id,
                    'dipt_id': dipt_id,
                    'voto': voto,
                })
            time.sleep(SLEEP)

        if votos_de_esta_votacion:
            inserted = bulk_insert_votos(votos_de_esta_votacion)
            total_votos_insertados += inserted

        # Totales
        totales = extraer_totales(vot_id)
        if totales and update_totales(vot_id, totales):
            total_totales_actualizados += 1
        time.sleep(SLEEP)

        if i % 10 == 0 or i == len(vot_ids):
            print(f"  [{i}/{len(vot_ids)}] vot={vot_id} → {len(votos_de_esta_votacion)} votos · totales: si={totales.get('total_si') if totales else '?'} no={totales.get('total_no') if totales else '?'}")

    print(f"\nResumen:")
    print(f"  Votos individuales insertados: {total_votos_insertados}")
    print(f"  Votaciones con totales actualizados: {total_totales_actualizados}/{len(vot_ids)}")


if __name__ == '__main__':
    main()
