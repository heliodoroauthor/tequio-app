#!/usr/bin/env python3
"""
scrape_gaceta_pendientes.py -- Fase 4.1.A (FIX-23 2026-05-21)
==============================================================
Lee la Gaceta Parlamentaria de la Camara de Diputados, encuentra iniciativas,
dictamenes y minutas, y los inserta en `votaciones_pendientes` para que
los ciudadanos voten antes que el Congreso.

Cambios FIX-23:
  - El parser anterior buscaba keywords en el TEXTO de los <a> links del
    indice. La realidad es que el indice solo tiene anchors intra-pagina
    (#Convocatoria3) y el contenido real esta en los Anexos (-I.html).
  - Ahora scrapea SOLO los -I.html (Anexo I) donde viven las iniciativas,
    dictamenes y minutas como secciones con <a name="IniciativaN"> seguido
    de <p class="Versales"> con el titulo.
  - Default DIAS_HISTORIA=60 para cubrir el ultimo periodo ordinario aun
    durante el receso (mayo-agosto).
  - Imprime `rows_inserted=N` para que el workflow lo capture.

Fuente:
  https://gaceta.diputados.gob.mx/Gaceta/66/{anio}/{mes}/{YYYYMMDD}-I.html
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
DIAS_HISTORIA = int(os.environ.get('DIAS_HISTORIA', '60'))

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

print("Tequio - Scraper Gaceta Parlamentaria (Votacion Ciudadana) FIX-23")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE: {'OK' if SUPABASE_URL and SERVICE_KEY else 'MISSING'}")
print(f"  GEMINI:   {'OK' if GEMINI_KEY else 'MISSING'}")
print(f"  Dias historia: {DIAS_HISTORIA}")

if not (SUPABASE_URL and SERVICE_KEY):
    sys.exit(1)

_SESSION = requests.Session()
_SESSION.headers.update(HEADERS_WEB)


def fetch(url, retries=2):
    for i in range(retries + 1):
        try:
            r = _SESSION.get(url, timeout=TIMEOUT, verify=False)
            if r.ok and r.text:
                r.encoding = r.apparent_encoding or 'iso-8859-1'
                return r.text
        except Exception:
            pass
        if i < retries:
            time.sleep(1)
    return None


def detectar_materia(texto):
    t = (texto or '').lower()
    pares = [
        ('seguridad', ['seguridad', 'guardia nacional', 'detencion', 'sedena']),
        ('laboral', ['trabajo', 'laboral', 'empleo', 'salario']),
        ('fiscal', ['fiscal', 'impuesto', 'isr', 'iva', 'aduana', 'hacienda']),
        ('salud', ['salud', 'imss', 'issste', 'medicamento']),
        ('educacion', ['educacion', 'sep', 'maestro', 'escolar']),
        ('energia', ['energia', 'pemex', 'cfe', 'electric']),
        ('justicia', ['justicia', 'penal', 'judicial', 'amparo', 'scjn']),
        ('derechos_humanos', ['derechos humanos', 'discrimina', 'genero', 'indigena']),
        ('medio_ambiente', ['ambiental', 'agua', 'clima', 'forestal', 'biodiversidad']),
        ('economia', ['economic', 'comercio', 'industria']),
        ('electoral', ['electoral', 'ine', 'partido politico']),
        ('migracion', ['migra', 'extranjero', 'frontera']),
        ('cultura', ['cultura fisica', 'deporte', 'cultural']),
    ]
    for mat, kws in pares:
        for kw in kws:
            if kw in t:
                return mat
    return 'general'


def acortar_titulo(titulo):
    if not titulo:
        return titulo
    t = re.sub(r'\s+', ' ', titulo).strip()
    # Limpiar prefijos comunes
    t = re.sub(r'^(Que reforma y adiciona |Que reforma |Que adiciona |Que abroga |Que expide |Por el que se )',
               '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s+presentad[oa] por.*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s+a cargo de.*$', '', t, flags=re.IGNORECASE)
    if len(t) > 140:
        t = t[:137] + '...'
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


MESES_NOM = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic']


def url_anexo_i(d):
    """Construye URL Anexo I para una fecha."""
    mes_nom = MESES_NOM[d.month - 1]
    return f"{GACETA_BASE}/Gaceta/66/{d.year}/{mes_nom}/{d.year}{d.month:02d}{d.day:02d}-I.html"


def url_index(d):
    """Construye URL indice principal para una fecha."""
    mes_nom = MESES_NOM[d.month - 1]
    return f"{GACETA_BASE}/Gaceta/66/{d.year}/{mes_nom}/{d.year}{d.month:02d}{d.day:02d}.html"


# Regex para detectar secciones de interes en el Anexo I
SECCION_RE = re.compile(r'^(Iniciativa|Dictamen|Minuta|Decreto)\d+$', re.I)


def extraer_de_anexo(html, fecha_gaceta, url_origen):
    """Extrae iniciativas/dictamenes/minutas de un Anexo I."""
    if not html:
        return []
    soup = BeautifulSoup(html, 'lxml')
    items = []

    for anchor in soup.find_all('a', attrs={'name': True}):
        name = anchor.get('name', '')
        if not SECCION_RE.match(name):
            continue
        tipo = re.match(r'^([A-Za-z]+)', name).group(1).lower()

        # Buscar el siguiente <p class="Versales"> como titulo
        titulo_p = None
        for sib in anchor.find_all_next(['p', 'div'], limit=10):
            cls = sib.get('class') or []
            if 'Versales' in cls:
                titulo_p = sib
                break
        if not titulo_p:
            continue

        titulo_raw = titulo_p.get_text(separator=' ', strip=True)
        titulo = re.sub(r'\s+', ' ', titulo_raw).strip()
        if len(titulo) < 20:
            continue

        # Buscar PDF link en los siguientes ~15 elementos
        pdf_url = None
        for sib in anchor.find_all_next('a', href=True, limit=30):
            href = sib.get('href', '')
            if '.pdf' in href.lower() and ('/PDF/' in href or '/Gaceta/' in href):
                pdf_url = href if href.startswith('http') else f"{GACETA_BASE}{href if href.startswith('/') else '/' + href}"
                break

        # gaceta_url: si hay PDF usalo, sino el Anexo + anchor
        gaceta_url = pdf_url or f"{url_origen}#{name}"

        items.append({
            'titulo': titulo,
            'tipo': tipo,
            'gaceta_url': gaceta_url,
            'fecha_propuesta': fecha_gaceta,
        })

    return items


def upsert_pendiente(item):
    titulo = item['titulo']
    payload = {
        'titulo': titulo[:500],
        'asunto_corto': acortar_titulo(titulo)[:200],
        'descripcion': titulo,
        'materia': detectar_materia(titulo),
        'fecha_propuesta': item['fecha_propuesta'].isoformat(),
        'gaceta_url': item['gaceta_url'],
        'estado': 'abierta',
    }
    emb = generar_embedding(f"{titulo} - {payload['materia']} - {item['tipo']}")
    if emb:
        payload['embedding'] = emb

    url = f"{SUPABASE_URL}/rest/v1/votaciones_pendientes?on_conflict=gaceta_url"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    try:
        r = requests.post(url, json=payload, headers=h, timeout=30)
        return r.ok, (r.text[:200] if not r.ok else '')
    except Exception as e:
        return False, str(e)[:200]


def main():
    t0 = datetime.now()
    print(f"\n[1] Iterando {DIAS_HISTORIA} dias hacia atras buscando Anexos I...")

    today = datetime.now().date()
    urls_intentadas = 0
    urls_con_contenido = 0
    todos_items = []
    urls_vistas = set()

    for i in range(DIAS_HISTORIA):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:  # Sat=5, Sun=6 — typically no Gaceta
            continue
        url = url_anexo_i(d)
        urls_intentadas += 1
        html = fetch(url)
        if not html or len(html) < 5000:
            continue
        items = extraer_de_anexo(html, d, url)
        if items:
            urls_con_contenido += 1
            print(f"  {d} -> {len(items)} ({', '.join(set(it['tipo'] for it in items))})")
            for it in items:
                if it['gaceta_url'] in urls_vistas:
                    continue
                urls_vistas.add(it['gaceta_url'])
                todos_items.append(it)
        time.sleep(0.25)

    print(f"\n[2] URLs intentadas: {urls_intentadas}, con contenido: {urls_con_contenido}")
    print(f"   Items unicos: {len(todos_items)}")

    print(f"\n[3] Upsert en Supabase...")
    n_ok = n_fail = 0
    for item in todos_items:
        ok, err = upsert_pendiente(item)
        if ok:
            n_ok += 1
        else:
            n_fail += 1
            if n_fail <= 3:
                print(f"  [FAIL] {err}")
        time.sleep(0.1)

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n== Resumen ({elapsed:.0f}s) ==")
    print(f"  URLs:       {urls_intentadas} intentadas, {urls_con_contenido} con contenido")
    print(f"  Items:      {len(todos_items)} unicos")
    print(f"  Upsert:     {n_ok} OK, {n_fail} fallidos")
    print(f"  rows_inserted={n_ok}")

    if n_ok == 0 and urls_con_contenido == 0:
        print("\n[INFO] Sin contenido en ultimas 60 dias (probable receso). Exit 0 silently.")


if __name__ == '__main__':
    main()
