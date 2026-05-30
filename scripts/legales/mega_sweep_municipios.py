#!/usr/bin/env python3
"""Mega-sweep municipios — para llegar al 100% de los 2,478.

Uso:
  python3 mega_sweep_municipios.py <estado_clave|all> <max_por_muni>

Estrategia:
  1. Lee catálogo INEGI completo (data/catalogo_inegi_municipios.csv).
  2. Para cada municipio del estado seleccionado, busca PDFs vía DuckDuckGo
     con query: site:<dominio_municipal>.gob.mx "reglamento" filetype:pdf
  3. Inserta shells en public.leyes (skip si ya existe URL).
  4. Para cada shell nuevo: descarga + pdftotext + parser_v4 + OCR fallback.
  5. PATCH ambito=municipal + entidad="<municipio>, <estado>".

Pre-req:
  - SUPABASE_URL + SUPABASE_ANON env vars
  - parser_v4.py en mismo dir
  - tesseract spa + poppler-utils en PATH
"""
import os, sys, json, subprocess, time, re, urllib.parse, urllib.request
from pathlib import Path

ANON = os.environ['SUPABASE_ANON']
SBURL = os.environ['SUPABASE_URL']
ESTADO_KEY = sys.argv[1] if len(sys.argv) > 1 else 'all'
MAX_PER_MUNI = int(sys.argv[2]) if len(sys.argv) > 2 else 8

sys.path.insert(0, str(Path(__file__).parent))
from parser_v4 import parse_any

# ─── Catálogo INEGI ──────────────────────────────────────────────────────────
CATALOGO = Path(__file__).parent / 'data' / 'catalogo_inegi_municipios.csv'

def load_catalogo():
    rows = []
    with open(CATALOGO) as f:
        next(f)  # header
        for ln in f:
            parts = ln.strip().split(',')
            if len(parts) < 4: continue
            cve_ent, nom_ent, cve_mun, nom_mun = parts[0], parts[1], parts[2], parts[3]
            rows.append({
                'cve_ent': cve_ent.zfill(2),
                'nom_ent': nom_ent,
                'cve_mun': cve_mun.zfill(3),
                'nom_mun': nom_mun,
            })
    return rows

# ─── DuckDuckGo search ───────────────────────────────────────────────────────
def search_pdfs(query, max_results=10):
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [r['href'] for r in results if r.get('href','').endswith('.pdf')]
    except Exception as e:
        print(f"  search err: {e}")
        return []

# ─── Discovery: dominio probable del municipio ───────────────────────────────
def candidate_domains(nom_mun, nom_ent):
    """Genera URLs candidatas para buscar."""
    base = nom_mun.lower()
    base = base.replace(' de ', '').replace(' del ', '').replace(' ', '').replace('á','a').replace('é','e').replace('í','i').replace('ó','o').replace('ú','u').replace('ñ','n')
    return [
        f"{base}.gob.mx",
        f"municipio{base}.gob.mx",
        f"www.{base}.gob.mx",
        f"{base}{nom_ent.lower()[:3]}.gob.mx",
    ]

def discover_pdfs(nom_mun, nom_ent):
    """Combina varias búsquedas para maximizar cobertura."""
    pdfs = set()
    queries = [
        f'site:{d} reglamento filetype:pdf' for d in candidate_domains(nom_mun, nom_ent)
    ]
    queries.append(f'"{nom_mun}" reglamento municipal filetype:pdf')
    for q in queries:
        for url in search_pdfs(q, max_results=8):
            pdfs.add(url)
            if len(pdfs) >= MAX_PER_MUNI: break
        if len(pdfs) >= MAX_PER_MUNI: break
        time.sleep(1)  # rate limit
    return list(pdfs)[:MAX_PER_MUNI]

# ─── Supabase helpers ────────────────────────────────────────────────────────
HEADERS = {
    'apikey': ANON,
    'Authorization': f'Bearer {ANON}',
    'Content-Type': 'application/json',
}

def already_loaded(entidad_full):
    """Skip municipio si ya tiene >= MAX_PER_MUNI docs en DB."""
    enc = urllib.parse.quote(entidad_full)
    r = urllib.request.Request(
        f"{SBURL}/rest/v1/leyes?entidad=eq.{enc}&ambito=eq.municipal&select=id",
        headers={**HEADERS, 'Prefer': 'count=exact'})
    with urllib.request.urlopen(r, timeout=20) as resp:
        cr = resp.headers.get('content-range','0-0/0')
        n = int(cr.split('/')[1])
        return n >= MAX_PER_MUNI

def insert_shells(rows):
    req = urllib.request.Request(
        f"{SBURL}/rest/v1/leyes",
        data=json.dumps(rows).encode(),
        headers={**HEADERS, 'Prefer':'return=representation'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  insert err: {e.code} {e.read()[:200]}")
        return []

def rpc_chunks(ley_id, ley_nombre, chunks):
    req = urllib.request.Request(
        f"{SBURL}/rest/v1/rpc/leyes_chunks_replace_all",
        data=json.dumps({'p_ley_id':ley_id,'p_ley_nombre':ley_nombre,'p_chunks':chunks}).encode(),
        headers=HEADERS, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return int(resp.read().decode().strip() or 0)
    except: return 0

# ─── PDF download + parse + OCR fallback ─────────────────────────────────────
def fix_url(u):
    p = urllib.parse.urlsplit(u)
    path = '/'.join(urllib.parse.quote(seg, safe='') for seg in p.path.split('/'))
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment))

def process(ley_id, url, nombre):
    pdf = f'/tmp/sw_{ley_id}.pdf'; txt = f'/tmp/sw_{ley_id}.txt'
    r = subprocess.run(['curl','-sL','-k','-A','Mozilla/5.0','-m','30','-o',pdf,'-w','%{http_code}|%{size_download}', fix_url(url)],
        capture_output=True, text=True)
    code, sz = (r.stdout.split('|') + ['0','0'])[:2]
    if code != '200' or int(sz) < 1000: return 0, 'bad_url'
    subprocess.run(['pdftotext','-layout',pdf,txt], timeout=60, capture_output=True)
    text = Path(txt).read_text(errors='replace') if Path(txt).exists() else ''
    if len(text) < 200:
        # OCR fallback
        d = f'/tmp/img_{ley_id}'
        Path(d).mkdir(exist_ok=True)
        subprocess.run(['pdftoppm','-r','150',pdf,f'{d}/page','-f','1','-l','40','-jpeg'], timeout=120, capture_output=True)
        for img in sorted(Path(d).glob('page-*.jpg')):
            subprocess.run(['tesseract','-l','spa',str(img),str(img)[:-4]],
                capture_output=True, timeout=30,
                env={**os.environ, 'TESSDATA_PREFIX': os.environ.get('TESSDATA_PREFIX','')})
        text = '\n'.join(p.read_text() for p in sorted(Path(d).glob('page-*.txt')))
    chunks, mode = parse_any(text)
    if not chunks: return 0, 'no_chunks'
    n = rpc_chunks(ley_id, nombre, chunks)
    # cleanup
    for f in [pdf, txt]:
        try: os.remove(f)
        except: pass
    return n, mode

# ─── Main loop ───────────────────────────────────────────────────────────────
def main():
    catalogo = load_catalogo()
    if ESTADO_KEY != 'all':
        catalogo = [m for m in catalogo if m['cve_ent'] == ESTADO_KEY.zfill(2)]
    print(f"Procesando {len(catalogo)} municipios (estado={ESTADO_KEY})")
    summary = {'discovered': 0, 'inserted': 0, 'loaded': 0, 'skipped': 0}
    for muni in catalogo:
        entidad_full = f"{muni['nom_mun']}, {muni['nom_ent']}"
        try:
            if already_loaded(entidad_full):
                print(f"  SKIP {entidad_full} (ya tiene >={MAX_PER_MUNI})")
                summary['skipped'] += 1
                continue
        except: pass
        print(f"\n▶ {entidad_full}")
        urls = discover_pdfs(muni['nom_mun'], muni['nom_ent'])
        if not urls:
            print(f"  Sin PDFs descubiertos"); continue
        summary['discovered'] += len(urls)
        shells = [{'nombre': url.rsplit('/',1)[-1][:120],
                   'entidad': entidad_full,
                   'tipo': 'reglamento-municipal',
                   'ambito': 'municipal',
                   'fuente': f'Gobierno {muni["nom_mun"]}',
                   'url': url} for url in urls]
        inserted = insert_shells(shells)
        summary['inserted'] += len(inserted)
        for shell in inserted:
            n, mode = process(shell['id'], shell['url'], shell['nombre'])
            if n > 0:
                print(f"  ✅ [{shell['id']}] {mode} {n} chunks")
                summary['loaded'] += 1
            else:
                print(f"  ❌ [{shell['id']}] {mode}")
                # delete shell
                req = urllib.request.Request(
                    f"{SBURL}/rest/v1/leyes?id=eq.{shell['id']}",
                    headers=HEADERS, method='DELETE')
                try: urllib.request.urlopen(req, timeout=15)
                except: pass
    print(f"\n=== SUMMARY ===")
    print(f"  Discovered URLs: {summary['discovered']}")
    print(f"  Insertados:      {summary['inserted']}")
    print(f"  Cargados:        {summary['loaded']}")
    print(f"  Skipped (full):  {summary['skipped']}")

if __name__ == '__main__':
    main()
