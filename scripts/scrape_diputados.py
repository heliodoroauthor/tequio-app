#!/usr/bin/env python3
"""
scrape_diputados.py — Cámara de Diputados LXVI Legislatura (2024-2027)
=======================================================================
Conecta Tequio al SITL (Sistema de Información Legislativa) oficial.

Fuente: https://sitl.diputados.gob.mx/LXVI_leg/

Etapas:
  A) Iterar 32 estados → extraer dipt_id de cada uno de los 500 diputados
  B) Para cada diputado, fetchear curricula.php y parsear:
     - nombre, partido (vía logo), entidad, distrito, principio elección
     - email, teléfono, curul, fecha nacimiento, suplente, comisiones
  C) Iterar 5 periodos legislativos → extraer votaciones nominales:
     - votacion_id, fecha, asunto, tipo
  D) Embedding del nombre+partido+entidad para búsqueda semántica

No incluye (próxima iteración):
  E) Breakdown individual de votos por diputado (URL devuelve vacío sin sesión)
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

MAX_ESTADOS = int(os.environ.get('MAX_ESTADOS', '32'))
MAX_DIPUTADOS_DEBUG = int(os.environ.get('MAX_DIPUTADOS', '600'))  # safety cap
SLEEP_BETWEEN = float(os.environ.get('SLEEP', '0.2'))

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

# Mapeo logo → partido oficial
LOGO_PARTIDO = {
    'morena': ('MORENA', 'morena'),
    'pan':    ('PAN', 'pan'),
    'pri':    ('PRI', 'pri'),
    'pvem':   ('Partido Verde Ecologista de México', 'pvem'),
    'pt':     ('Partido del Trabajo', 'pt'),
    'mc':     ('Movimiento Ciudadano', 'mc'),
    'prd':    ('PRD', 'prd'),
    'na':     ('Nueva Alianza', 'na'),
    'es':     ('Encuentro Solidario', 'es'),
    'ind':    ('Independiente', 'ind'),
    'sg':     ('Sin Grupo Parlamentario', 'sg'),
}

print("Tequio · Scraper Cámara de Diputados LXVI")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE_URL: {'OK' if SUPABASE_URL else 'MISSING'}")
print(f"  SERVICE_KEY:  {'OK' if SERVICE_KEY else 'MISSING'}")
print(f"  GEMINI_KEY:   {'OK' if GEMINI_KEY else 'MISSING (embeddings disabled)'}")

if not (SUPABASE_URL and SERVICE_KEY):
    print("ERROR: env vars faltantes.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────
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
    print(f"    [WARN] {url} → {last_err}")
    return None


def extraer_dipt_ids_por_estado(edot):
    """Recorre la lista de diputados de un estado y extrae los IDs únicos."""
    url = f"{BASE}/listado_diputados_gpnp.php?tipot=Edo&edot={edot}"
    html = http_get(url)
    if not html:
        return []
    return list(set(int(m) for m in re.findall(r'curricula\.php\?dipt=(\d+)', html)))


def parse_curricula(html, dipt_id):
    """Parsea la página de currícula. v2: usa BeautifulSoup sobre HTML real (no markdown)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'lxml')

    # Texto plano sin tags, normalizado
    texto = soup.get_text(separator='\n')
    # Comprimir whitespace pero preservar saltos de línea
    lineas = [ln.strip() for ln in texto.split('\n')]
    lineas = [ln for ln in lineas if ln]
    texto_norm = '\n'.join(lineas)

    def find_after_label(label, multiline=False):
        # Busca "LABEL:" o "LABEL: valor" — toma el texto siguiente al colon
        pattern = re.compile(re.escape(label) + r'\s*:?\s*(.+)', re.I)
        for ln in lineas:
            m = pattern.match(ln)
            if m:
                return m.group(1).strip()
        return None

    def find_inline(label):
        # Buscar "Label: valor" en el texto completo
        m = re.search(re.escape(label) + r'\s*:\s*([^\n]+)', texto_norm, re.I)
        return m.group(1).strip() if m else None

    # Nombre: línea "DIP. NOMBRE COMPLETO"
    nombre = None
    for ln in lineas:
        m = re.match(r'^DIP\.?\s+(.+?)$', ln, re.I)
        if m:
            nombre = m.group(1).strip().title()
            break

    principio = find_inline('Principio de elección') or find_inline('Principio de elecci')
    entidad = find_inline('Entidad')
    distrito = find_inline('Distrito') or find_inline('Circunscripción') or find_inline('Circunscripci')
    curul = find_inline('Curul')
    reelecto = find_inline('Reelecto')
    suplente = find_inline('Suplente')

    # Email — buscar en todo el texto
    email_m = re.search(r'\b([\w.+-]+@[\w.-]+\.[a-zA-Z]{2,})\b', texto_norm)
    email = email_m.group(1) if email_m else None

    # Foto: extraer src del img que apunta a fotos_lxviconfondo
    foto_url = None
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if 'fotos_lxviconfondo' in src:
            if src.startswith('http'):
                foto_url = src
            else:
                foto_url = f"{BASE}/{src.lstrip('/')}" if not src.startswith('/') else f"https://sitl.diputados.gob.mx{src}"
            break

    # Partido: imagen logo (morena.webp, pan.webp, etc)
    partido = None
    partido_codigo = None
    for img in soup.find_all('img'):
        src = img.get('src', '').lower()
        m = re.search(r'images/(morena|pan|pri|pvem|pt|mc|prd|na|es|ind|sg)\.(webp|png|jpg|gif)', src)
        if m:
            codigo = m.group(1)
            partido, partido_codigo = LOGO_PARTIDO.get(codigo, (codigo.upper(), codigo))
            break

    # Comisiones: <a href="...comt=N">Nombre (Rol)</a>
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

    # Fecha nacimiento: "13-abril - 1963" o similar
    fecha_nacimiento = None
    meses_es = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
                'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
    fnac_m = re.search(r'\b(\d{1,2})\s*[-–\s]\s*(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s*[-–\s]\s*(\d{4})\b', texto_norm, re.I)
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
    }


def get_embedding(texto):
    """Embedding 768d via gemini-embedding-001 (MRL truncation)."""
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
    txt_emb = f"{d.get('nombre','')} · {d.get('partido','')} · {d.get('entidad','')} · {d.get('distrito','')}"
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
    """Recorre la lista de votaciones del periodo. v2: parser sobre HTML real con BS4."""
    from bs4 import BeautifulSoup
    url = f"{BASE}/votacionesxperiodonplxvi.php?pert={pert}"
    html = http_get(url)
    if not html:
        return []
    soup = BeautifulSoup(html, 'lxml')

    meses_es = {'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
                'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}

    # Recorrer las celdas/filas en orden. Detectamos:
    # - filas con fecha "10 Febrero 2026"
    # - links con votaciont=NNN seguidos del título de la votación
    votaciones = []
    fecha_actual = None
    fecha_re = re.compile(r'^(\d{1,2})\s+(Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|Septiembre|Octubre|Noviembre|Diciembre)\s+(\d{4})$', re.I)

    # Estrategia: iterar todos los <tr> o filas de tabla, y dentro ver si tienen votaciont
    # Como fallback, iterar TODO el árbol y usar texto contextual
    nodos = soup.find_all(['tr', 'td', 'div', 'a'])
    for nodo in nodos:
        texto = nodo.get_text(strip=True)
        if not texto:
            continue
        # Es fecha?
        fm = fecha_re.match(texto)
        if fm:
            try:
                d = int(fm.group(1))
                m = meses_es.get(fm.group(2).lower(), 1)
                y = int(fm.group(3))
                fecha_actual = f"{y:04d}-{m:02d}-{d:02d}"
            except Exception:
                pass
            continue
        # Es link a votación?
        if nodo.name == 'a':
            href = nodo.get('href', '')
            mvot = re.search(r'votaciont=(\d+)', href)
            if mvot and texto.isdigit():
                # Encontrar asunto: el siguiente elemento o texto del padre
                parent = nodo.parent
                if parent:
                    parent_txt = parent.get_text(separator=' | ', strip=True)
                    # El número se asume al inicio; el asunto viene después del "|"
                    partes = [p.strip() for p in parent_txt.split('|')]
                    # Buscar la parte más larga (probablemente el asunto)
                    asunto = max(partes, key=len) if partes else ''
                    asunto = re.sub(r'^\d+\s*', '', asunto)  # quitar número al inicio
                    asunto = asunto.strip()
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
                    votacion_id = int(mvot.group(1))
                    # Deduplicar
                    if any(v['votacion_id'] == votacion_id for v in votaciones):
                        continue
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
    """Upsert vía PostgREST on_conflict=votacion_id."""
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


# ─────────────────────────────────────────────────────────────────
def main():
    # ETAPA A+B: Diputados
    print("\n[A+B] Extracción diputados por estado + curriculas")
    todos_dipt_ids = set()
    for edot in range(1, MAX_ESTADOS + 1):
        ids = extraer_dipt_ids_por_estado(edot)
        if ids:
            todos_dipt_ids.update(ids)
            print(f"  Estado {edot:02d}: {len(ids)} diputados")
        time.sleep(SLEEP_BETWEEN)
    print(f"\n  Total diputados únicos: {len(todos_dipt_ids)}")

    ok = fail = 0
    todos_ids = sorted(todos_dipt_ids)[:MAX_DIPUTADOS_DEBUG]
    for i, dipt_id in enumerate(todos_ids, 1):
        html = http_get(f"{BASE}/curricula.php?dipt={dipt_id}")
        if not html:
            fail += 1
            continue
        d = parse_curricula(html, dipt_id)
        if not d.get('nombre'):
            fail += 1
            continue
        ok_db, err = upsert_diputado(d)
        if ok_db:
            ok += 1
            if i % 20 == 0 or i == len(todos_ids):
                print(f"  [{i}/{len(todos_ids)}] dipt={dipt_id} {d['nombre'][:30]:30} {d.get('partido','')}")
        else:
            fail += 1
            print(f"  [FAIL dipt={dipt_id}] {err}")
        time.sleep(SLEEP_BETWEEN)
    print(f"\n  Diputados: {ok} actualizados, {fail} fallidos")

    # ETAPA C: Votaciones
    print("\n[C] Extracción votaciones nominales por periodo")
    periodos = [1, 3, 5, 6, 8]  # pert IDs conocidos LXVI
    total_vot = 0
    for pert in periodos:
        vots = extraer_votaciones_de_periodo(pert)
        print(f"  Periodo {pert}: {len(vots)} votaciones")
        for v in vots:
            ok_db, err = upsert_votacion(v)
            if ok_db:
                total_vot += 1
            elif err:
                print(f"    [WARN vot={v['votacion_id']}] {err[:100]}")
            time.sleep(0.1)
        time.sleep(SLEEP_BETWEEN)
    print(f"\n  Votaciones: {total_vot} actualizadas")

    print("\nScrape Diputados LXVI completo.")


if __name__ == '__main__':
    main()
