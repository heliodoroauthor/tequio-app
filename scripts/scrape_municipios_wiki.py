#!/usr/bin/env python3
"""
scrape_municipios_wiki.py — v2 (multi-categoría)
Descubre artículos Wikipedia de cada municipio mexicano y puebla:
- municipio_docs:
  * categoría 'simbolos'        → URL artículo Wikipedia
  * categoría 'sitio_web'        → campo 'sitio_web' del infobox
  * categoría 'redes_sociales'   → handles FB/TW/IG/YT detectados

Wikipedia ES API · CC BY-SA · Cero invención.
"""
import os, sys, json, time, re, urllib.request, urllib.parse, requests

SB_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SK = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
if not (SB_URL and SK):
    print("ERROR: env vars faltantes"); sys.exit(1)

UA = {'User-Agent': 'TequioBot/1.0 (https://tequio.app; hola@tequio.app)'}
H_SB = {'apikey': SK, 'Authorization': f'Bearer {SK}', 'Content-Type': 'application/json'}

def buscar_wiki(muni, estado):
    estado_short = estado.replace('Estado de ', '').replace('de Zaragoza', '').strip()
    queries = [
        f'{muni} (municipio)',
        f'{muni}, {estado_short}',
        f'{muni} ({estado_short})',
        f'Municipio de {muni}',
        muni,
    ]
    for q in queries:
        try:
            url = f'https://es.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote(q)}&limit=3&format=json'
            req = urllib.request.Request(url, headers=UA)
            r = urllib.request.urlopen(req, timeout=10).read()
            data = json.loads(r)
            if len(data) >= 4 and data[1] and data[3]:
                for i, titulo in enumerate(data[1]):
                    desc = data[2][i] if i < len(data[2]) else ''
                    url_wiki = data[3][i]
                    if any(kw in (titulo + ' ' + desc).lower()
                           for kw in ['municipio', 'localidad', 'ciudad', 'pueblo', estado_short.lower()]):
                        return titulo, url_wiki
                if data[1][0].lower().startswith(muni.lower()[:8]):
                    return data[1][0], data[3][0]
        except Exception:
            pass
    return None, None

def get_wikicode(titulo):
    """Trae el wikitext crudo del artículo."""
    try:
        url = (f'https://es.wikipedia.org/w/api.php?action=query&prop=revisions'
               f'&rvprop=content&rvslots=main&titles={urllib.parse.quote(titulo)}'
               f'&format=json&formatversion=2')
        req = urllib.request.Request(url, headers=UA)
        r = urllib.request.urlopen(req, timeout=12).read()
        data = json.loads(r)
        pages = data.get('query', {}).get('pages', [])
        if not pages: return None
        revs = pages[0].get('revisions', [])
        if not revs: return None
        return revs[0].get('slots', {}).get('main', {}).get('content', '')
    except Exception:
        return None

# Regex precompiled
RE_SITIO = re.compile(r'\|\s*(?:sitio[_ ]?web|web(?:_oficial)?|gobierno[_ ]?web|página[_ ]?web)\s*=\s*([^\n|]+)', re.IGNORECASE)
RE_FB = re.compile(r'(?:facebook\.com|fb\.com)/([A-Za-z0-9._-]+)', re.IGNORECASE)
RE_TW = re.compile(r'(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)', re.IGNORECASE)
RE_IG = re.compile(r'instagram\.com/([A-Za-z0-9._]+)', re.IGNORECASE)
RE_YT = re.compile(r'youtube\.com/(?:c/|channel/|user/|@)([A-Za-z0-9._-]+)', re.IGNORECASE)
RE_URL = re.compile(r'https?://[^\s\]\|}]+', re.IGNORECASE)

# Stopwords: URLs que NO son del municipio
STOP_DOMAINS = ['wikipedia.org', 'wikimedia.org', 'web.archive.org', 'commons.wikimedia',
                'gob.mx/inegi', 'inegi.org.mx/contenidos']

def limpiar_url(raw):
    """Limpia un valor extraído de un campo wikicode."""
    raw = re.sub(r'<!--.*?-->', '', raw)  # comentarios
    raw = re.sub(r'\{\{[^}]*\}\}', '', raw)  # plantillas
    raw = raw.strip()
    # Si viene como [[wiki link]] o [url texto], extraer URL
    m_ext = re.match(r'\[(\S+)(?:\s+([^\]]+))?\]', raw)
    if m_ext:
        return m_ext.group(1).strip()
    # Si es plain URL
    m_url = RE_URL.search(raw)
    if m_url:
        return m_url.group(0).rstrip('.,;)')
    return None

def parse_infobox(wikicode):
    """Extrae sitio_web + redes del wikicode."""
    info = {'sitio_web': None, 'fb': None, 'tw': None, 'ig': None, 'yt': None}
    if not wikicode: return info
    # 1) campo sitio_web del infobox
    m = RE_SITIO.search(wikicode)
    if m:
        cleaned = limpiar_url(m.group(1))
        if cleaned and not any(d in cleaned for d in STOP_DOMAINS):
            info['sitio_web'] = cleaned
    # 2) handles de redes (en infobox o en el texto)
    fb = RE_FB.search(wikicode)
    tw = RE_TW.search(wikicode)
    ig = RE_IG.search(wikicode)
    yt = RE_YT.search(wikicode)
    if fb: info['fb'] = f'https://facebook.com/{fb.group(1)}'
    if tw: info['tw'] = f'https://twitter.com/{tw.group(1)}'
    if ig: info['ig'] = f'https://instagram.com/{ig.group(1)}'
    if yt: info['yt'] = f'https://youtube.com/@{yt.group(1)}' if not yt.group(1).startswith('@') else f'https://youtube.com/{yt.group(1)}'
    return info

def upsert_doc(municipio_id, categoria_id, url, nombre_doc, fuente):
    """Insert con tolerancia a duplicados por (municipio_id, categoria_id) vigente."""
    row = {
        'municipio_id': municipio_id, 'categoria_id': categoria_id,
        'url': url, 'nombre_doc': nombre_doc[:200],
        'fuente': fuente, 'vigente': True,
    }
    try:
        r = requests.post(f"{SB_URL}/rest/v1/municipio_docs",
                          headers={**H_SB, 'Prefer': 'return=minimal'},
                          json=row, timeout=10)
        return r.ok
    except Exception:
        return False

print("─── Tequio · Wikipedia Municipios v2 (multi-categoría) ───")
print(f"  Supabase: {'OK' if SB_URL else 'MISSING'}")

print("\n[1] Descargando municipios...")
munis = requests.get(
    f"{SB_URL}/rest/v1/municipios?select=id,nombre,nombre_estado,poblacion_total"
    f"&order=poblacion_total.desc.nullslast",
    headers=H_SB, timeout=30).json()
print(f"  {len(munis)} municipios totales")

# Saltar los que ya tienen 'simbolos' (los demás campos siguen actualizables)
existentes_simbolos = {r['municipio_id'] for r in requests.get(
    f"{SB_URL}/rest/v1/municipio_docs?select=municipio_id&categoria_id=eq.simbolos&vigente=eq.true",
    headers=H_SB, timeout=30).json()}

print(f"\n[2] Iterando Wikipedia con rate limit 0.4 s/req...")
n_sim = n_web = n_red = 0
fails = 0
for i, m in enumerate(munis):
    if m['id'] in existentes_simbolos:
        continue
    titulo, url_wiki = buscar_wiki(m['nombre'], m['nombre_estado'])
    if not titulo:
        fails += 1
        continue
    # Símbolos: URL del artículo
    if upsert_doc(m['id'], 'simbolos', url_wiki, titulo, 'Wikipedia ES (CC BY-SA)'):
        n_sim += 1
    # Infobox: sitio_web + redes
    wc = get_wikicode(titulo)
    if wc:
        info = parse_infobox(wc)
        if info['sitio_web']:
            if upsert_doc(m['id'], 'sitio_web', info['sitio_web'],
                          f"Sitio oficial de {m['nombre']}", 'Wikipedia infobox'):
                n_web += 1
        redes = [u for u in [info['fb'], info['tw'], info['ig'], info['yt']] if u]
        if redes:
            # Para redes, juntar todas en un solo doc separado por ' | '
            redes_url = redes[0]  # el principal
            nombres = ' · '.join(['FB' if 'facebook' in u else 'TW' if 'twitter' in u else 'IG' if 'instagram' in u else 'YT' for u in redes])
            if upsert_doc(m['id'], 'redes_sociales', redes_url,
                          f"Redes oficiales ({nombres}) de {m['nombre']}",
                          'Wikipedia · infobox/texto'):
                n_red += 1
    if (i + 1) % 100 == 0:
        pct = (n_sim + n_web + n_red) * 100 // max(1, i + 1)
        print(f"  [{i+1}/{len(munis)}] sim={n_sim} web={n_web} red={n_red} fails={fails}")
    time.sleep(0.4)

print(f"\n✅ Resumen final:")
print(f"  Símbolos:          {n_sim}")
print(f"  Sitio web:         {n_web}")
print(f"  Redes sociales:    {n_red}")
print(f"  Fails (no encontró Wikipedia): {fails}")
