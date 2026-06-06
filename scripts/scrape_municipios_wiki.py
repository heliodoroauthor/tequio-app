#!/usr/bin/env python3
"""
scrape_municipios_wiki.py
Descubre artículos Wikipedia de cada municipio mexicano y poblar:
- municipio_docs (categoría 'simbolos') con URL del artículo

Wikipedia es fuente verificable. Cero invención.
"""
import os, sys, json, time, urllib.request, urllib.parse, requests

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
                # Tomar el primer match que parezca municipio
                for i, titulo in enumerate(data[1]):
                    descripcion = data[2][i] if i < len(data[2]) else ''
                    url_wiki = data[3][i]
                    if any(kw in (titulo + ' ' + descripcion).lower()
                           for kw in ['municipio', 'localidad', 'ciudad', 'pueblo', estado_short.lower()]):
                        return titulo, url_wiki
                # Fallback: primer resultado si el título empieza con el muni
                if data[1][0].lower().startswith(muni.lower()[:8]):
                    return data[1][0], data[3][0]
        except Exception:
            pass
    return None, None

print("─── Tequio · Wikipedia Municipios Scraper ───")
print(f"  Supabase: {'OK' if SB_URL else 'MISSING'}")

# Bajar todos los municipios sin doc 'simbolos' aún
print("\n[1] Descargando municipios pendientes...")
url = f"{SB_URL}/rest/v1/municipios?select=id,nombre,nombre_estado,poblacion_total&order=poblacion_total.desc.nullslast"
munis = requests.get(url, headers=H_SB, timeout=30).json()
print(f"  {len(munis)} municipios totales")

url_existentes = f"{SB_URL}/rest/v1/municipio_docs?select=municipio_id&categoria_id=eq.simbolos&vigente=eq.true"
existentes = {r['municipio_id'] for r in requests.get(url_existentes, headers=H_SB, timeout=30).json()}
pendientes = [m for m in munis if m['id'] not in existentes]
print(f"  {len(pendientes)} pendientes (ya hay {len(existentes)} cargados)")

print(f"\n[2] Buscando en Wikipedia (rate limit 0.3 s/req)...")
hits = []
fails = 0
for i, m in enumerate(pendientes):
    titulo, url_wiki = buscar_wiki(m['nombre'], m['nombre_estado'])
    if titulo:
        hits.append({
            'municipio_id': m['id'],
            'categoria_id': 'simbolos',
            'url': url_wiki,
            'nombre_doc': titulo,
            'fuente': 'Wikipedia ES (CC BY-SA)',
            'vigente': True,
        })
    else:
        fails += 1
    if (i + 1) % 100 == 0:
        print(f"  [{i+1}/{len(pendientes)}] hits={len(hits)} ({len(hits)*100//(i+1)}%) fails={fails}")
    time.sleep(0.3)

print(f"\n[3] Insertando {len(hits)} en municipio_docs...")
# Batches de 100
inserted = 0
for batch_start in range(0, len(hits), 100):
    batch = hits[batch_start:batch_start + 100]
    try:
        r = requests.post(
            f"{SB_URL}/rest/v1/municipio_docs",
            headers={**H_SB, 'Prefer': 'return=minimal'},
            json=batch, timeout=30
        )
        if r.ok:
            inserted += len(batch)
        else:
            # Fallback: insertar 1 por 1 para saltar duplicados
            for row in batch:
                rr = requests.post(f"{SB_URL}/rest/v1/municipio_docs",
                                   headers={**H_SB, 'Prefer': 'return=minimal'},
                                   json=row, timeout=10)
                if rr.ok: inserted += 1
        print(f"  [{batch_start + len(batch)}/{len(hits)}] inserted={inserted}")
    except Exception as e:
        print(f"  batch fail: {e}")

print(f"\n✅ Resumen final: insertados {inserted} / {len(hits)} hits / {len(pendientes)} pendientes")
