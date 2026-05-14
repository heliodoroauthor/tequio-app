#!/usr/bin/env python3
"""
scrape_senadores.py — Fase 3.1.B: Senado de la República LXVI (128 senadores)
============================================================================

Fuente:
  - Lista: https://www.senado.gob.mx/66/senadores/directorio_de_senadores
  - Perfil: https://www.senado.gob.mx/66/senador/{ID}

Cada perfil tiene:
  - Nombre, suplente, tipo de elección, partido, comisiones, email, etc.

Genera embeddings con Gemini text-embedding-001 (MRL 768d) para búsqueda semántica.
"""
import os, sys, re, time, requests, json
from datetime import datetime
from bs4 import BeautifulSoup

import urllib3
urllib3.disable_warnings()

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')

DIRECTORIO_URL = 'https://www.senado.gob.mx/66/senadores/directorio_de_senadores'
SENADOR_URL    = 'https://www.senado.gob.mx/66/senador/{}'
LEGISLATURA = 66

UA = 'Mozilla/5.0 (compatible; TequioBot/1.0; +https://tequio.app)'
HEADERS_WEB = {'User-Agent': UA, 'Accept': 'text/html'}

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

GEMINI_EMBED_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent'

print("Tequio · Scraper Senadores LXVI")
print(f"  Python: {sys.version.split()[0]}")
print(f"  SUPABASE: {'OK' if SUPABASE_URL and SERVICE_KEY else 'MISSING'}")
print(f"  GEMINI: {'OK' if GEMINI_KEY else 'MISSING'}")

if not (SUPABASE_URL and SERVICE_KEY):
    sys.exit(1)


# ─── Partidos ─────────────────────────────────────────────────────────────
PARTIDOS_PATRONES = {
    'MORENA':   [r'\bMORENA\b', r'Movimiento Regeneración Nacional'],
    'PAN':      [r'Partido Acción Nacional', r'\bPAN\b'],
    'PRI':      [r'Partido Revolucionario Institucional', r'\bPRI\b'],
    'PVEM':     [r'Partido Verde Ecologista', r'\bPVEM\b'],
    'PT':       [r'Partido del Trabajo', r'\bdel Trabajo\b'],
    'MC':       [r'Movimiento Ciudadano', r'\bMC\b'],
    'INDEPENDIENTE': [r'Independiente'],
}


def detectar_partido(texto):
    if not texto:
        return None
    for sigla, patrones in PARTIDOS_PATRONES.items():
        for pat in patrones:
            if re.search(pat, texto, re.IGNORECASE):
                return sigla
    return None


def detectar_tipo_eleccion(texto):
    t = (texto or '').lower()
    if 'representación proporcional' in t or 'representacion proporcional' in t:
        return 'representacion_proporcional'
    if 'mayoría relativa' in t or 'mayoria relativa' in t:
        return 'mayoria_relativa'
    if 'primera minoría' in t or 'primera minoria' in t:
        return 'primera_minoria'
    return None


def fetch(url, retries=3):
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS_WEB, timeout=30, verify=False)
            if r.ok:
                r.encoding = 'utf-8'
                return r.text
        except Exception as e:
            print(f"  [retry {i+1}] {url}: {e}")
        time.sleep(2)
    return None


# ─── Etapa A: Lista de 128 senadores ──────────────────────────────────────
def obtener_lista_senadores():
    html = fetch(DIRECTORIO_URL)
    if not html:
        print("[ERR] No se pudo obtener directorio")
        return []
    soup = BeautifulSoup(html, 'lxml')

    senadores = []
    seen = set()
    for a in soup.find_all('a', href=True):
        m = re.match(r'.*/66/senador/(\d+)$', a['href'])
        if m:
            sid = int(m.group(1))
            if sid in seen:
                continue
            seen.add(sid)
            nombre = a.get_text(strip=True)
            if not nombre:
                continue
            senadores.append({
                'id': sid,
                'nombre_directorio': nombre,
                'url': SENADOR_URL.format(sid),
            })
    print(f"  Senadores detectados: {len(senadores)}")
    return senadores


# ─── Etapa B: Parseo individual ───────────────────────────────────────────
def parsear_senador(sid, url):
    html = fetch(url)
    if not html:
        return None
    soup = BeautifulSoup(html, 'lxml')
    text = soup.get_text('\n', strip=True)

    out = {'id': sid, 'url': url}

    # Nombre
    h1 = soup.find(['h1', 'h2', 'h3'])
    nombre = None
    if h1:
        nombre = h1.get_text(strip=True)
        nombre = re.sub(r'^Sen\.\s*', '', nombre)
    out['nombre_completo'] = nombre

    # Buscar el bloque del senador (después del nombre)
    nombre_idx = text.find('Sen.') if 'Sen.' in text else -1
    bloque = text[nombre_idx:nombre_idx + 3000] if nombre_idx >= 0 else text[:3000]

    # Tipo de elección
    tipo_match = re.search(r'Senador[a]?\s+Electo[a]?\s+por\s+([^\n]+)', bloque)
    if tipo_match:
        raw = tipo_match.group(1).strip()
        out['tipo_eleccion'] = detectar_tipo_eleccion(raw)

    # Suplente
    sup_match = re.search(r'Suplente:\s*([^\n]+)', bloque)
    if sup_match:
        out['nombre_suplente'] = sup_match.group(1).strip()

    # Dirección
    dir_match = re.search(r'Dirección:\s*([^\n]+(?:\n[^\n]+)?)', bloque)
    if dir_match:
        out['direccion_oficina'] = dir_match.group(1).strip()[:500]

    # Email
    email_match = re.search(r'([\w.-]+@senado\.gob\.mx)', bloque)
    if email_match:
        out['email'] = email_match.group(1).strip()

    # Teléfono
    tel_match = re.search(r'(55\s*5345\s*3000[^\n]*)', bloque)
    if tel_match:
        out['telefono'] = tel_match.group(1).strip()[:200]

    # Partido / Cargo
    cargo_match = re.search(r'(Coordinador|Vicecoordinador|Presidente|Vicepresidente|Secretario)[\s\w]+Grupo Parlamentario[^\n]+', bloque)
    if cargo_match:
        out['cargo_especial'] = cargo_match.group(0).strip()[:200]

    # Partido (buscar en bloque entero)
    out['partido'] = detectar_partido(bloque)

    # Entidad federativa: viene de "Lista Nacional" o nombre de estado
    estado_match = re.search(r'(?:Senador[a]?\s+(?:Electo[a]?\s+)?por\s+(?:Mayoría|Primera).*?\n)([A-ZÁÉÍÓÚÑ ][^\n]+)', bloque)
    if 'Lista Nacional' in bloque:
        out['entidad_federativa'] = 'LISTA NACIONAL'
    elif estado_match:
        out['entidad_federativa'] = estado_match.group(1).strip()[:100]

    # Foto: buscar img que mencione el ID o "senadores"
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if src and (str(sid) in src or 'senadores.jpg' in src or 'integrantes' in src):
            full = src if src.startswith('http') else f'https://www.senado.gob.mx{src}'
            out['foto_url'] = full
            break

    # Comisiones
    com_idx = text.find('COMISIONES')
    if com_idx >= 0:
        com_text = text[com_idx:com_idx + 2500]
        # Secretario(a):
        sec_match = re.search(r'Secretari[oa]\(a\):\s*\n(.*?)(?=Integrante:|Presidente|Inform|$)', com_text, re.DOTALL)
        if sec_match:
            secs = [s.strip() for s in sec_match.group(1).split('\n') if s.strip() and len(s.strip()) < 200]
            out['comisiones_secretario'] = secs[:20]
        # Integrante:
        int_match = re.search(r'Integrante:\s*\n(.*?)(?=Informes|Iniciativas|$)', com_text, re.DOTALL)
        if int_match:
            ints = [s.strip() for s in int_match.group(1).split('\n') if s.strip() and len(s.strip()) < 200]
            out['comisiones_integrante'] = ints[:30]

    return out


# ─── Etapa C: Embeddings ──────────────────────────────────────────────────
def generar_embedding(texto):
    if not GEMINI_KEY or not texto:
        return None
    try:
        r = requests.post(
            f'{GEMINI_EMBED_URL}?key={GEMINI_KEY}',
            json={
                'model': 'models/gemini-embedding-001',
                'content': {'parts': [{'text': texto[:8000]}]},
                'outputDimensionality': 768,
            },
            timeout=30,
        )
        if r.ok:
            v = r.json().get('embedding', {}).get('values')
            if v and len(v) == 768:
                return v
    except Exception as e:
        print(f"  [embed err] {e}")
    return None


def texto_para_embedding(sen):
    parts = [
        sen.get('nombre_completo') or '',
        f"Partido: {sen.get('partido') or 'N/A'}",
        f"Entidad: {sen.get('entidad_federativa') or 'N/A'}",
        f"Tipo elección: {sen.get('tipo_eleccion') or 'N/A'}",
        f"Cargo: {sen.get('cargo_especial') or ''}",
    ]
    if sen.get('comisiones_secretario'):
        parts.append('Comisiones (secretario): ' + ', '.join(sen['comisiones_secretario']))
    if sen.get('comisiones_integrante'):
        parts.append('Comisiones (integrante): ' + ', '.join(sen['comisiones_integrante']))
    return '\n'.join(p for p in parts if p)


def upsert(sen):
    """Upsert un senador a Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/politicos_senadores?on_conflict=id"
    h = {**HEADERS_SB, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
    # Drop None values
    payload = {k: v for k, v in sen.items() if v is not None}
    try:
        r = requests.post(url, json=payload, headers=h, timeout=30)
        if r.ok:
            return True
        print(f"  [HTTP {r.status_code}] {r.text[:200]}")
    except Exception as e:
        print(f"  [exc upsert] {e}")
    return False


def main():
    print("\n[Etapa A] Obteniendo lista de senadores...")
    lista = obtener_lista_senadores()
    if not lista:
        print("[FATAL] No senadores")
        sys.exit(1)

    print(f"\n[Etapa B] Parseando {len(lista)} perfiles...")
    n_ok, n_emb = 0, 0
    for i, item in enumerate(lista, 1):
        sen = parsear_senador(item['id'], item['url'])
        if not sen:
            print(f"  [{i}/{len(lista)}] FAIL {item['id']}")
            continue
        # Si parser no encontró nombre, usar el del directorio
        if not sen.get('nombre_completo'):
            sen['nombre_completo'] = item['nombre_directorio']

        # Embedding
        emb_text = texto_para_embedding(sen)
        emb = generar_embedding(emb_text)
        if emb:
            sen['embedding'] = emb
            n_emb += 1

        # Upsert
        if upsert(sen):
            n_ok += 1
        if i % 10 == 0:
            print(f"  [{i}/{len(lista)}] OK={n_ok} EMB={n_emb}")
        time.sleep(0.4)  # rate limit respeto

    print(f"\n  ── Resumen ──")
    print(f"  Total perfiles:     {len(lista)}")
    print(f"  Upsert exitosos:    {n_ok}")
    print(f"  Embeddings:         {n_emb}")


if __name__ == '__main__':
    main()
