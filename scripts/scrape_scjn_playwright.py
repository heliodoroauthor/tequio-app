#!/usr/bin/env python3
"""
scrape_scjn_api.py v3 — API directa (sin Playwright)
====================================================
Descubierto: la SPA usa internamente:
  POST https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public/tesis?page=N&size=1000
Con headers Origin + Referer + Content-Type, sin auth.
Body vacío {}. Devuelve 311,544 tesis paginadas.

Uso: python3 scrape_scjn_api.py <epoca> <pagina_inicio> <pagina_fin>
  - epoca: ignorado (la API filtra mal — paginamos todo)
  - pagina_inicio/fin: usado para sharding

Cada shard procesa páginas [start..end] con size=1000 → ~30,000 tesis/shard.
10 shards × 30 páginas = 300k tesis (cubre todo el corpus SCJN).
"""
import os, sys, time, json, re, requests

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

if not all([SUPABASE_URL, SERVICE_KEY]):
    print('ERROR: SUPABASE_URL/SERVICE_KEY missing')
    sys.exit(1)

SB_HEADERS = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'resolution=ignore-duplicates'
}

# Args
EPOCA = int(sys.argv[1]) if len(sys.argv) > 1 else 0  # ignorado
PAGE_START = int(sys.argv[2]) if len(sys.argv) > 2 else 1
PAGE_END = int(sys.argv[3]) if len(sys.argv) > 3 else 30
SIZE_PER_PAGE = int(os.environ.get('SIZE_PER_PAGE', '1000'))

# SCJN API
SCJN_API = 'https://sjf2.scjn.gob.mx/services/sjftesismicroservice/api/public/tesis'
SCJN_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'es-MX,es;q=0.9',
    'Origin': 'https://sjf2.scjn.gob.mx',
    'Referer': 'https://sjf2.scjn.gob.mx/listado-de-tesis',
    'Content-Type': 'application/json',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
}

print(f'🦎 SCJN scraper v3 (API directa) · pages {PAGE_START}-{PAGE_END} · size={SIZE_PER_PAGE}', flush=True)


def parse_fecha(s):
    if not s:
        return None
    # ISO 8601: 2026-05-29T10:34:00Z
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return m.group(0)
    return None


def tipo_from_jurisprudencia(tj):
    """tipoJurisprudencia: 1=Aislada, 2=Jurisprudencia"""
    if tj == 2:
        return 'Jurisprudencia'
    return 'Aislada'


def transform_doc(d):
    """SCJN doc → tesis row para nuestra tabla."""
    ius = d.get('ius') or d.get('id')
    if not ius:
        return None
    try:
        ius = int(re.sub(r'\D', '', str(ius))[:9])
    except Exception:
        return None
    if ius < 100000 or ius > 5000000:
        return None
    rubro = (d.get('rubro') or '').strip()
    if not rubro:
        return None  # NOT NULL en DB
    return {
        'registro_digital': ius,
        'tipo': tipo_from_jurisprudencia(d.get('tipoJurisprudencia')),
        'rubro': rubro[:800],
        'texto': (d.get('texto') or '')[:8000] or None,
        'materia': (d.get('materias') or '')[:200] or None,
        'instancia': (d.get('sala') or d.get('instancia') or '')[:200] or None,
        'epoca': (d.get('epocaAbr') or d.get('epoca') or '')[:80] or None,
        'tesis_clave': (d.get('claveTesis') or '')[:80] or None,
        'precedentes': (d.get('precedentes') or '')[:5000] or None,
        'fecha_publicacion': parse_fecha(d.get('fechaPublicacion')),
        'url_oficial': f'https://sjf2.scjn.gob.mx/detalle/tesis/{ius}',
    }


def fetch_page(page, size, retries=3):
    """POST /tesis?page=N&size=S — devuelve lista de docs."""
    for attempt in range(retries):
        try:
            r = requests.post(
                f'{SCJN_API}?page={page}&size={size}',
                headers=SCJN_HEADERS,
                json={},
                timeout=60,
            )
            if r.ok:
                data = r.json()
                return data.get('documents', []), data.get('total', 0)
            print(f'  [page {page}] HTTP {r.status_code} ({r.text[:120]})', flush=True)
        except Exception as e:
            print(f'  [page {page}] attempt {attempt+1} exc: {e}', flush=True)
        time.sleep(2 ** attempt)
    return [], 0


def upsert_bulk(rows):
    if not rows:
        return 0
    # Dedup por registro_digital dentro del batch
    seen = set()
    unique = []
    for r in rows:
        if r['registro_digital'] not in seen:
            seen.add(r['registro_digital'])
            unique.append(r)
    inserted = 0
    batch_size = 100
    for i in range(0, len(unique), batch_size):
        batch = unique[i:i+batch_size]
        try:
            r = requests.post(
                f'{SUPABASE_URL}/rest/v1/jurisprudencia_scjn',
                headers=SB_HEADERS, json=batch, timeout=30
            )
            if r.ok:
                inserted += len(batch)
            elif r.status_code == 409:
                # Conflict (duplicate registro_digital): retry one-by-one
                for s in batch:
                    try:
                        rs = requests.post(
                            f'{SUPABASE_URL}/rest/v1/jurisprudencia_scjn',
                            headers=SB_HEADERS, json=[s], timeout=15
                        )
                        if rs.ok:
                            inserted += 1
                    except Exception:
                        pass
            else:
                print(f'  [upsert HTTP {r.status_code}]: {r.text[:200]}', flush=True)
        except Exception as e:
            print(f'  [upsert exc]: {e}', flush=True)
    return inserted


def main():
    inserted_total = 0
    api_total = 0

    for page in range(PAGE_START, PAGE_END + 1):
        docs, total = fetch_page(page, SIZE_PER_PAGE)
        if not api_total and total:
            api_total = total
            print(f'  [total] SCJN reporta {api_total:,} tesis en el corpus', flush=True)
        if not docs:
            print(f'  page {page}: 0 docs (fin del corpus o error)', flush=True)
            if page > PAGE_START + 2 and inserted_total == 0:
                # Probable bot detection — abortar
                print('  ⚠️ varias páginas vacías al inicio · abortando', flush=True)
                break
            continue

        rows = []
        for d in docs:
            t = transform_doc(d)
            if t:
                rows.append(t)
        inserted = upsert_bulk(rows)
        inserted_total += inserted
        print(f'  page {page}: {len(docs)} docs → {len(rows)} válidos → {inserted} insertados (acum {inserted_total:,})', flush=True)

        # Pequeña pausa entre páginas para no martillar
        time.sleep(0.5)

    print(f'\n═══ SUMMARY shard pages {PAGE_START}-{PAGE_END} ═══', flush=True)
    print(f'  insertados: {inserted_total:,}', flush=True)
    print(f'  api total:  {api_total:,}', flush=True)


if __name__ == '__main__':
    main()
