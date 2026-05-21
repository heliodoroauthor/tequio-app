#!/usr/bin/env python3
"""
scrape_diputados.py -- Camara de Diputados LXVI Legislatura (2024-2027)
=======================================================================
H2.2-01 fix (2026-05-21): iteracion atomica por dipt_id en lugar de
scrapear el listado raiz. El endpoint listado_diputados_gpnp.php tuvo
silent_fails (responde 200 pero a veces sin links). curricula.php?dipt=N
es estable (200 para todo N en rango y 404/HTML vacio fuera de rango).

Tambien fix: PVEM usa logo 'verde.webp' (no 'pvem.webp'). PRD usa 'sol'.
Esto resuelve los 63 diputados con partido=NULL en la base.

Fuente: https://sitl.diputados.gob.mx/LXVI_leg/

Etapas:
  A) Iterar dipt_id en rango [1..1000] -> curricula.php?dipt=N
  B) Parsear datos por diputado y upsert en politicos_diputados
  C) Iterar 5 periodos legislativos -> votaciones nominales
  D) Embedding del nombre+partido+entidad para busqueda semantica
"""
import os, sys, re, time, json, requests
from datetime import datetime

import urllib3
urllib3.disable_warnings()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')

BASE = 'https://sitl.diputados.gob.mx/LXVI_leg'
UA = 'Mozilla/5.0 (compatible; TequioBot/1.0; +https://tequio.app)'
HEADERS_WEB = {'User-Agent': UA, 'Accept-Language': 'es-MX,es;q=0.9'}
TIMEOUT = 30

# H2.2-01 fix: rango atomico de dipt_id en vez de listado
DIPT_ID_MIN = int(os.environ.get('DIPT_ID_MIN', '1'))
DIPT_ID_MAX = int(os.environ.get('DIPT_ID_MAX', '1000'))
SLEEP_BETWEEN = float(os.environ.get('SLEEP', '0.15'))
SKIP_DIPUTADOS = os.environ.get('SKIP_DIPUTADOS', '0') == '1'
SKIP_VOTACIONES = os.environ.get('SKIP_VOTACIONES', '0') == '1'

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

# Mapeo logo -> partido oficial.
# IMPORTANTE: SITL usa 'verde' para PVEM y 'sol' para PRD (no los acronimos).
LOGO_PARTIDO = {
    'morena': ('MORENA', 'morena'),
    'pan':    ('PAN', 'pan'),
    'pri':    ('PRI', 'pri'),
    'verde':  ('Partido Verde Ecologista de Mexico', 'pvem'),
    'pvem':   ('Partido Verde Ecologista de Mexico', 'pvem'),  # fallback por si cambian
    'pt':     ('Partido del Trabajo', 'pt'),
    'mc':     ('Movimiento Ciudadano', 'mc'),
    'sol':    ('PRD', 'prd'),
    'prd':    ('PRD', 'prd'),
    'na':     ('Nueva Alianza', 'na'),
    'es':     ('Encuentro Solidario', 'es'),
    'ind':    ('Independiente', 'ind'),
    'sg':     ('Sin Grupo Parlamentario', 'sg'),
}

# Regex que captura cualquiera de los logos arriba
LOGO_RE = re.compile(
    r'images/(' + '|'.join(LOGO_PARTIDO.keys()) + r')\.(?:webp|png|jpg|gif|svg)',
    re.I
)

print("Tequio - Scraper Camara de Diputados LXVI (iteracion atomica)")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE_URL: {'OK' if SUPABASE_URL else 'MISSING'}")
print(f"  SERVICE_KEY:  {'OK' if SERVICE_KEY else 'MISSING'}")
print(f"  GEMINI_KEY:   {'OK' if GEMINI_KEY else 'MISSING (embeddings disabled)'}")
print(f"  Rango dipt_id: {DIPT_ID_MIN}..{DIPT_ID_MAX}")

if not (SUPABASE_URL and SERVICE_KEY):
    print("ERROR: env vars faltantes.")
    sys.exit(1)


def http_get(url, retries=2):
    """GET con reintentos y delay anti-rate-limit."""
    last_err = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS_WEB, timeout=TIMEOUT, verify=False)
            if r.ok:
                return r.text
            last_err = f'HTTP {r.status_code}'
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
        if i < retries:
            time.sleep(2)
    return None


def parse_curricula(html, dipt_id):
    """Parsea la pagina de curricula. Devuelve None si no hay nombre (dipt invalido)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'lxml')

    texto = soup.get_text(separator='\n')
    lineas = [ln.strip() for ln in texto.split('\n')]
    lineas = [ln for ln in lineas if ln]
    texto_norm = '\n'.join(lineas)

    def find_inline(label):
        m = re.search(re.escape(label) + r'\s*:\s*([^\n]+)', texto_norm, re.I)
        return m.group(1).strip() if m else None

    # Nombre: linea "DIP. NOMBRE COMPLETO"
    nombre = None
    for ln in lineas:
        m = re.match(r'^DIP\.?\s+(.+?)$', ln, re.I)
        if m:
            nombre = m.group(1).strip().title()
            break

    # Si no hay nombre, dipt_id no existe / no es LXVI
    if not nombre:
        return None

    principio = find_inline('Principio de eleccion') or find_inline('Principio de elecci')
    entidad = find_inline('Entidad')
    distrito = find_inline('Distrito') or find_inline('Circunscripcion') or find_inline('Circunscripci')
    curul = find_inline('Curul')
    reelecto = find_inline('Reelecto')
    suplente = find_inline('Suplente')

    # Email
    email_m = re.search(r'\b([\w.+-]+@[\w.-]+\.[a-zA-Z]{2,})\b', texto_norm)
    email = email_m.group(1) if email_m else None

    # Foto
    foto_url = None
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if 'fotos_lxviconfondo' in src or 'fotos_lxvi' in src:
            if src.startswith('http'):
                foto_url = src
            elif src.startswith('/'):
                foto_url = f"https://sitl.diputados.gob.mx{src}"
            else:
                foto_url = f"{BASE}/{src.lstrip('./')}"
            break

    # Partido: imagen logo. H2.2-01 fix: ahora cubre verde, sol, etc.
    partido = None
    partido_codigo = None
    for img in soup.find_all('img'):
        src = img.get('src', '').lower()
        m = LOGO_RE.search(src)
        if m:
            codigo = m.group(1).lower()
            partido, partido_codigo = LOGO_PARTIDO.get(codigo, (codigo.upper(), codigo))
            break

    # Comisiones
    comisiones = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        m = re.search(r'comt=(\d+)', href)
        if not m:
            continue
        texto_a = a.get_text(strip=True)
        if not texto_a or len(texto_a) < 3:
            continue
        rol = 'Integrante'
        nombre_com = texto_a
        rol_m = re.search(r'\(([^)]+)\)\s*$', texto_a)
        if rol_m:
            rol = rol_m.group(1).strip()
            nombre_com = texto_a[:rol_m.start()].strip()
        comisiones.append({
            'comt_id': int(m.group(1)),
            'nombre': nombre_com[:120],
            'rol': rol[:50]
        })

    # Fecha nacimiento
    fecha_nacimiento = None
    meses_es = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
                'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
    fnac_m = re.search(
        r'\b(\d{1,2})\s*[-–\s]\s*(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s*[-–\s]\s*(\d{4})\b',
        texto_norm, re.I)
    if fnac_m:
        try:
            d = int(fnac_m.group(1))
            m = meses_es.get(fnac_m.group(2).lower(), 1)
            y = int(fnac_m.group(3))
            fecha_nacimiento = f"{y:04d}-{m:02d}-{d:02d}"
        except Exception:
            fecha_nacimiento = None

    return {
        'dipt_id': dipt_id,
        'nombre': nombre,
        'partido': partido,
        'partido_codigo': partido_codigo,
        'entidad': entidad,
        'distrito': distrito,
        'principio_eleccion': principio,
        'curul': curul,
        'email': email,
        'fecha_nacimiento': fecha_nacimiento,
        'reelecto': reelecto,
        'suplente': suplente,
        'foto_url': foto_url,
        'curricula_url': f"{BASE}/curricula.php?dipt={dipt_id}",
        'comisiones': comisiones if comisiones else None,
        'legislatura': 'LXVI',
    }


def get_embedding(texto):
    """Embedding 768d via gemini-embedding-001."""
    if not GEMINI_KEY:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={GEMINI_KEY}"
    try:
        r = requests.post(url, json={
            'model': 'models/gemini-embedding-001',
            'content': {'parts': [{'text': texto[:2000]}]},
            'taskType': 'RETRIEVAL_DOCUMENT',
            'outputDimensionality': 768,
        }, timeout=20)
        if r.ok:
            return r.json()['embedding']['values']
    except Exception:
        pass
    return None


def upsert_diputado(d):
    """Upsert via PostgREST on_conflict=dipt_id."""
    txt_emb = f"{d.get('nombre','')} - {d.get('partido','')} - {d.get('entidad','')} - {d.get('distrito','')}"
    emb = get_embedding(txt_emb)
    if emb:
        d['embedding'] = emb
    url = f"{SUPABASE_URL}/rest/v1/politicos_diputados?on_conflict=dipt_id"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=d, headers=h, timeout=20)
        return r.ok, (r.text[:200] if not r.ok else '')
    except Exception as e:
        return False, str(e)


def extraer_votaciones_de_periodo(pert):
    """Extrae lista de votaciones nominales en un periodo."""
    from bs4 import BeautifulSoup
    url = f"{BASE}/votacionesxperiodonplxvi.php?pert={pert}"
    html = http_get(url)
    if not html:
        return []
    soup = BeautifulSoup(html, 'lxml')

    meses_es = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
                'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}

    votaciones = []
    fecha_actual = None
    fecha_re = re.compile(
        r'^(\d{1,2})\s+(Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|Septiembre|Octubre|Noviembre|Diciembre)\s+(\d{4})\s*$',
        re.I)

    for tr in soup.find_all('tr'):
        texto_tr = tr.get_text(separator=' ', strip=True)
        if not texto_tr:
            continue

        fm = fecha_re.match(texto_tr)
        if fm:
            try:
                d = int(fm.group(1))
                m = meses_es.get(fm.group(2).lower(), 1)
                y = int(fm.group(3))
                fecha_actual = f"{y:04d}-{m:02d}-{d:02d}"
            except Exception:
                pass
            continue

        link = tr.find('a', href=re.compile(r'votaciont=\d+'))
        if not link:
            continue
        mvot = re.search(r'votaciont=(\d+)', link.get('href', ''))
        if not mvot:
            continue
        votacion_id = int(mvot.group(1))

        if any(v['votacion_id'] == votacion_id for v in votaciones):
            continue

        tds = tr.find_all('td')
        asunto = ''
        for td in tds:
            t = td.get_text(strip=True)
            if len(t) > len(asunto) and not t.isdigit():
                asunto = t
        if len(asunto) < 10:
            asunto = texto_tr
            num_link = link.get_text(strip=True)
            if num_link and asunto.startswith(num_link):
                asunto = asunto[len(num_link):].strip()
        if len(asunto) < 10:
            continue

        tipo = None
        up = asunto.upper()
        if 'EN LO GENERAL Y EN LO PARTICULAR' in up:
            tipo = 'general_particular'
        elif 'EN LO GENERAL' in up:
            tipo = 'general'
        elif 'EN LO PARTICULAR' in up:
            tipo = 'particular'

        votaciones.append({
            'votacion_id': votacion_id,
            'fecha': fecha_actual,
            'asunto': asunto[:1000],
            'tipo': tipo,
            'pert_id': pert,
            'url_oficial': f"{BASE}/estadistico_votacionnplxvi.php?votaciont={votacion_id}",
        })
    return votaciones


def upsert_votacion(v):
    if not v.get('fecha'):
        return False, 'sin fecha'
    txt_emb = f"{v.get('asunto','')}"
    emb = get_embedding(txt_emb)
    if emb:
        v['embedding'] = emb
    url = f"{SUPABASE_URL}/rest/v1/votaciones_diputados?on_conflict=votacion_id"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=v, headers=h, timeout=20)
        return r.ok, (r.text[:200] if not r.ok else '')
    except Exception as e:
        return False, str(e)


def scrapear_diputados():
    """H2.2-01 fix: iteracion atomica por dipt_id, sin scrape de listados."""
    print(f"\n[A+B] Iteracion atomica dipt_id {DIPT_ID_MIN}..{DIPT_ID_MAX}")
    ok = fail = no_existe = 0
    procesados = 0
    for dipt_id in range(DIPT_ID_MIN, DIPT_ID_MAX + 1):
        html = http_get(f"{BASE}/curricula.php?dipt={dipt_id}")
        if not html:
            fail += 1
            continue
        d = parse_curricula(html, dipt_id)
        if d is None:
            # dipt_id sin nombre = no es de LXVI o no existe
            no_existe += 1
            continue
        procesados += 1
        ok_db, err = upsert_diputado(d)
        if ok_db:
            ok += 1
            if procesados % 25 == 0:
                p = d.get('partido') or 'sin_partido'
                print(f"  [{procesados}] dipt={dipt_id:>4} {d['nombre'][:30]:30} {p}")
        else:
            fail += 1
            if fail <= 5:
                print(f"  [FAIL dipt={dipt_id}] {err[:120]}")
        time.sleep(SLEEP_BETWEEN)

    print(f"\n  Diputados: {ok} actualizados, {fail} fallidos, {no_existe} dipt_id sin diputado LXVI")
    # Stdout marcador para que el workflow capture el conteo
    print(f"  rows_inserted={ok}")
    return ok


def scrapear_votaciones():
    print("\n[C] Extraccion votaciones nominales por periodo")
    periodos = [1, 3, 5, 6, 8]
    total_vot = 0
    total_skipped = 0
    for pert in periodos:
        vots = extraer_votaciones_de_periodo(pert)
        print(f"  Periodo {pert}: {len(vots)} votaciones extraidas")
        for v in vots:
            ok_db, err = upsert_votacion(v)
            if ok_db:
                total_vot += 1
            elif err == 'sin fecha':
                total_skipped += 1
            elif err:
                print(f"    [WARN vot={v['votacion_id']}] {err[:100]}")
            time.sleep(0.1)
        time.sleep(SLEEP_BETWEEN)
    print(f"\n  Votaciones: {total_vot} actualizadas, {total_skipped} sin fecha")


def main():
    total = 0
    if SKIP_DIPUTADOS:
        print("\n[A+B] SALTADO (SKIP_DIPUTADOS=1)")
    else:
        total = scrapear_diputados()
    if SKIP_VOTACIONES:
        print("\n[C] SALTADO (SKIP_VOTACIONES=1)")
    else:
        scrapear_votaciones()

    if total == 0 and not SKIP_DIPUTADOS:
        print("\nERROR: 0 diputados procesados. Abortando con exit 2.")
        sys.exit(2)
    print(f"\nScrape Diputados LXVI completo. Total upserts diputados: {total}")


if __name__ == '__main__':
    main()
