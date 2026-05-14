#!/usr/bin/env python3
"""
scrape_gaceta_pendientes.py — Fase 4.1.A
=========================================
Lee la Gaceta Parlamentaria de la Cámara de Diputados, encuentra dictámenes
"para discusión y votación" y los inserta en `votaciones_pendientes` para que
los ciudadanos voten antes que el Congreso.

Fuentes:
  - https://gaceta.diputados.gob.mx/  (índice por día)
  - Cada Gaceta lista "Dictámenes a discusión" → títulos de las leyes que se votarán.

Estrategia:
  - Lee los últimos 14 días de Gaceta.
  - Para cada dictamen detectado:
      * extrae titulo, materia (heuristic), url
      * genera asunto_corto (resumen IA con Gemini si está disponible)
      * inserta en votaciones_pendientes (UPSERT por gaceta_url)
  - Si la fecha de votación se puede inferir del orden del día, la guarda.

Embeddings con Gemini para búsqueda semántica.
"""
import os, sys, re, time, requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

import urllib3
urllib3.disable_warnings()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')

GACETA_BASE = 'https://gaceta.diputados.gob.mx'
DIAS_HISTORIA = int(os.environ.get('DIAS_HISTORIA', '14'))

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
HEADERS_WEB = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'es-MX,es;q=0.9',
}
HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}
TIMEOUT = 45

print("Tequio · Scraper Gaceta Parlamentaria (Votación Ciudadana)")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE: {'OK' if SUPABASE_URL and SERVICE_KEY else 'MISSING'}")
print(f"  GEMINI: {'OK' if GEMINI_KEY else 'MISSING'}")
print(f"  Días: {DIAS_HISTORIA}")

if not (SUPABASE_URL and SERVICE_KEY):
    sys.exit(1)


_SESSION = requests.Session()
_SESSION.headers.update(HEADERS_WEB)


def fetch(url, retries=2):
    for i in range(retries + 1):
        try:
            r = _SESSION.get(url, timeout=TIMEOUT, verify=False)
            if r.ok and r.text:
                r.encoding = r.apparent_encoding or 'utf-8'
                return r.text
        except Exception as e:
            print(f"  [retry {i+1}] {url}: {e}")
        time.sleep(2)
    return None


def detectar_materia(texto):
    """Heurística: a qué materia pertenece la ley."""
    t = (texto or '').lower()
    pares = [
        ('seguridad', ['seguridad', 'guardia nacional', 'detención', 'sedena']),
        ('laboral', ['trabajo', 'laboral', 'empleo', 'salario']),
        ('fiscal', ['fiscal', 'impuesto', 'isr', 'iva', 'aduana', 'hacienda']),
        ('salud', ['salud', 'imss', 'issste', 'medicamento']),
        ('educacion', ['educación', 'educacion', 'sep', 'maestro', 'escolar']),
        ('energia', ['energía', 'energia', 'pemex', 'cfe', 'eléctric', 'electric']),
        ('justicia', ['justicia', 'penal', 'judicial', 'amparo', 'scjn']),
        ('derechos_humanos', ['derechos humanos', 'discriminaci', 'género', 'genero', 'indígena']),
        ('medio_ambiente', ['ambiental', 'agua', 'clima', 'forestal', 'biodiversidad']),
        ('economia', ['económic', 'economic', 'comercio', 'industria']),
        ('electoral', ['electoral', 'ine', 'partido político']),
        ('migracion', ['migra', 'extranjero', 'frontera']),
    ]
    for mat, kws in pares:
        for kw in kws:
            if kw in t:
                return mat
    return 'general'


def acortar_titulo(titulo):
    """Reduce un titulo legal largo a una versión legible de 1 línea."""
    if not titulo:
        return titulo
    # Quitar "DECRETO POR EL QUE SE..." y derivados
    t = titulo
    t = re.sub(r'^DECRETO POR EL QUE\s+', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^DICTAMEN\s+(DE LA COMISIÓN.*?,?\s*)?(QUE\s+|CON\s+)?', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\(EN LO GENERAL.*?\)', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s+', ' ', t).strip()
    if len(t) > 140:
        t = t[:137] + '…'
    return t


def generar_embedding(texto):
    if not GEMINI_KEY or not texto:
        return None
    try:
        r = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={GEMINI_KEY}',
            json={
                'model': 'models/gemini-embedding-001',
                'content': {'parts': [{'text': texto[:8000]}]},
                'outputDimensionality': 768,
            },
            timeout=20,
        )
        if r.ok:
            v = r.json().get('embedding', {}).get('values')
            if v and len(v) == 768:
                return v
    except Exception:
        pass
    return None


def listar_gacetas_recientes():
    """Devuelve lista de URLs de Gacetas de los últimos N días."""
    urls = []
    today = datetime.now().date()
    for i in range(DIAS_HISTORIA):
        d = today - timedelta(days=i)
        # Formato Gaceta: gaceta.diputados.gob.mx/Gaceta/66/2026/may/20260514.html (aprox)
        # En realidad la estructura cambia por año. Probemos varios patrones.
        anio = d.year
        mes_nom = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'][d.month - 1]
        # Posibles patrones:
        for pat in [
            f"{GACETA_BASE}/Gaceta/66/{anio}/{mes_nom}/{anio}{d.month:02d}{d.day:02d}.html",
            f"{GACETA_BASE}/Gaceta/66/{anio}/{mes_nom}/{anio}{d.month:02d}{d.day:02d}-I.html",
        ]:
            urls.append((d, pat))
    return urls


def extraer_dictamenes(html, fecha_gaceta):
    """De una página de Gaceta, extrae dictámenes a discusión/votación."""
    if not html:
        return []
    soup = BeautifulSoup(html, 'lxml')
    text = soup.get_text(separator='\n')

    items = []
    # Buscar bloques de texto con "Dictamen" + ley + url asociada
    # Estrategia conservadora: cualquier enlace que apunte a .doc/.docx/.pdf y mencione "dictamen" o "decreto"
    for a in soup.find_all('a', href=True):
        href = a['href']
        link_text = a.get_text(strip=True)
        # Filtrar links de dictámenes
        if not link_text or len(link_text) < 30:
            continue
        if not re.search(r'\b(decreto|dictamen|iniciativa|reforma)\b', link_text, re.IGNORECASE):
            continue
        # URL completa
        full_url = href if href.startswith('http') else f"{GACETA_BASE}{href if href.startswith('/') else '/' + href}"
        items.append({
            'titulo': link_text,
            'gaceta_url': full_url,
            'fecha_propuesta': fecha_gaceta,
        })
        if len(items) >= 30:  # cap por página
            break
    return items


def upsert_pendiente(item):
    titulo = item['titulo']
    payload = {
        'titulo': titulo,
        'asunto_corto': acortar_titulo(titulo),
        'descripcion': titulo,
        'materia': detectar_materia(titulo),
        'fecha_propuesta': item['fecha_propuesta'].isoformat(),
        'gaceta_url': item['gaceta_url'],
        'estado': 'abierta',
    }
    emb = generar_embedding(f"{titulo} · {payload['materia']}")
    if emb:
        payload['embedding'] = emb

    url = f"{SUPABASE_URL}/rest/v1/votaciones_pendientes?on_conflict=gaceta_url"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=payload, headers=h, timeout=30)
        return r.ok
    except Exception as e:
        print(f"  [upsert err] {e}")
        return False


def main():
    t0 = datetime.now()
    print("\n[1] Listando Gacetas recientes...")
    gacetas = listar_gacetas_recientes()
    print(f"  URLs a probar: {len(gacetas)}")

    print("\n[2] Descargando y extrayendo dictámenes...")
    todos_items = []
    urls_vistas = set()
    for fecha, url in gacetas:
        html = fetch(url)
        if not html:
            continue
        items = extraer_dictamenes(html, fecha)
        for item in items:
            if item['gaceta_url'] in urls_vistas:
                continue
            urls_vistas.add(item['gaceta_url'])
            todos_items.append(item)
        if items:
            print(f"  {fecha} → {len(items)} dictámenes")
        time.sleep(0.3)

    print(f"\n[3] Encontrados {len(todos_items)} dictámenes únicos. Insertando en Supabase...")
    n_ok = 0
    for item in todos_items:
        if upsert_pendiente(item):
            n_ok += 1
        time.sleep(0.2)

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n══ Resumen ({elapsed:.0f}s) ══")
    print(f"  Gacetas consultadas:  {len(gacetas)}")
    print(f"  Dictámenes únicos:    {len(todos_items)}")
    print(f"  Upsert exitosos:      {n_ok}")


if __name__ == '__main__':
    main()
