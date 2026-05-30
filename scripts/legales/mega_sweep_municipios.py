#!/usr/bin/env python3
"""Mega-sweep municipios v2 — versión mejorada.
 
Cambios vs v1:
  - `ddgs` en lugar de `duckduckgo-search` (renombrada)
  - Scrape directo del dominio municipal como estrategia PRIMARIA
  - DDGS solo como fallback (los runners GitHub son rate-limited en DDG)
  - Skip rápido cuando muni no responde en 5s
  - Filtro: solo PDFs con "reglament" en el path
  - Logs concisos, sin spam de warnings
 
Uso:
  python3 mega_sweep_municipios.py <estado_clave|all> <max_por_muni>
"""
import os, sys, json, subprocess, time, re, urllib.parse, urllib.request, urllib.error
import warnings
from pathlib import Path
from html.parser import HTMLParser
 
# Silenciar warnings que floodean el log
warnings.filterwarnings('ignore')
 
ANON = os.environ['SUPABASE_ANON']
SBURL = os.environ['SUPABASE_URL']
ESTADO_KEY = sys.argv[1] if len(sys.argv) > 1 else 'all'
MAX_PER_MUNI = int(sys.argv[2]) if len(sys.argv) > 2 else 8
 
sys.path.insert(0, str(Path(__file__).parent))
from parser_v4 import parse_any
 
CATALOGO = Path(__file__).parent / 'data' / 'catalogo_inegi_municipios.csv'
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
HEADERS = {
    'apikey': ANON,
    'Authorization': f'Bearer {ANON}',
    'Content-Type': 'application/json',
}
 
# ─── Helpers ──────────────────────────────────────────────────────────────────
def slugify(text):
    """'Pueblo Nuevo' → 'pueblonuevo'."""
    t = text.lower()
    for a, b in [('á','a'),('é','e'),('í','i'),('ó','o'),('ú','u'),
                 ('ñ','n'),('ü','u')]:
        t = t.replace(a, b)
    return re.sub(r'[^a-z0-9]', '', t)
 
def fix_url(u):
    p = urllib.parse.urlsplit(u)
    path = '/'.join(urllib.parse.quote(seg, safe='') for seg in p.path.split('/'))
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment))
 
def fetch(url, timeout=8):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace'), resp.status
    except Exception:
        return None, 0
 
# ─── Catálogo INEGI ──────────────────────────────────────────────────────────
def load_catalogo():
    rows = []
    with open(CATALOGO) as f:
        next(f)
        for ln in f:
            parts = ln.strip().split(',')
            if len(parts) < 4: continue
            rows.append({
                'cve_ent': parts[0].zfill(2),
                'nom_ent': parts[1],
                'cve_mun': parts[2].zfill(3),
                'nom_mun': parts[3],
            })
    return rows
 
# ─── Strategy 1: Direct HTML scrape del dominio municipal ────────────────────
class LinkFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href', '')
            if href: self.links.append(href)
 
def absolutize(base_url, href):
    if href.startswith('http'):
        return href
    if href.startswith('//'):
        return f"https:{href}"
    if href.startswith('/'):
        return urllib.parse.urljoin(base_url, href)
    return urllib.parse.urljoin(base_url + '/', href)
 
def scrape_municipal_site(domain, max_results=8, depth=1):
    """Visita el dominio raíz y subpágina /transparencia/, /normatividad/, etc."""
    candidates = [
        f"https://www.{domain}",
        f"https://{domain}",
        f"http://www.{domain}",
    ]
    pdfs = []
    base_used = None
    for base in candidates:
        html, code = fetch(base, timeout=6)
        if code == 200 and html:
            base_used = base
            parser = LinkFinder()
            try: parser.feed(html)
            except: pass
            for href in parser.links:
                full = absolutize(base, href)
                if 'reglament' in full.lower() and full.lower().endswith('.pdf'):
                    pdfs.append(full)
                    if len(pdfs) >= max_results: return list(set(pdfs))[:max_results]
            break
 
    if not base_used: return []
 
    # Profundizar en páginas comunes
    if depth > 0:
        for sub in ['transparencia', 'normatividad', 'reglamentos',
                    'marco-juridico', 'marco-normativo']:
            url = f"{base_used}/{sub}"
            html, code = fetch(url, timeout=5)
            if code != 200 or not html: continue
            parser = LinkFinder()
            try: parser.feed(html)
            except: pass
            for href in parser.links:
                full = absolutize(url, href)
                if 'reglament' in full.lower() and full.lower().endswith('.pdf'):
                    pdfs.append(full)
                    if len(pdfs) >= max_results: break
            if len(pdfs) >= max_results: break
 
    return list(set(pdfs))[:max_results]
 
# ─── Strategy 2: DDGS search (fallback) ──────────────────────────────────────
def search_ddgs(query, max_results=8):
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [r.get('href','') for r in results
                if r.get('href','').lower().endswith('.pdf')]
    except Exception:
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            return [r.get('href','') for r in results
                    if r.get('href','').lower().endswith('.pdf')]
        except: return []
 
# ─── Discovery combinado ─────────────────────────────────────────────────────
def discover_pdfs(muni):
    nom_mun = muni['nom_mun']
    nom_ent = muni['nom_ent']
    slug = slugify(nom_mun)
 
    if len(slug) < 3: return []
 
    pdfs = []
    domain_candidates = [
        f"{slug}.gob.mx",
        f"municipiode{slug}.gob.mx",
        f"municipio{slug}.gob.mx",
        f"{slug}.{slugify(nom_ent)[:5]}.gob.mx",
    ]
 
    for domain in domain_candidates:
        found = scrape_municipal_site(domain, max_results=MAX_PER_MUNI - len(pdfs))
        if found:
            pdfs.extend(found)
            if len(pdfs) >= MAX_PER_MUNI: return pdfs[:MAX_PER_MUNI]
 
    if len(pdfs) < MAX_PER_MUNI:
        for domain in domain_candidates[:2]:
            q = f'site:{domain} reglamento filetype:pdf'
            found = search_ddgs(q, max_results=MAX_PER_MUNI - len(pdfs))
            if found:
                pdfs.extend(found)
                if len(pdfs) >= MAX_PER_MUNI: break
            time.sleep(0.5)
 
    seen, out = set(), []
    for u in pdfs:
        if u not in seen:
            seen.add(u); out.append(u)
    return out[:MAX_PER_MUNI]
 
# ─── Supabase helpers ────────────────────────────────────────────────────────
def already_loaded(entidad_full):
    enc = urllib.parse.quote(entidad_full)
    r = urllib.request.Request(
        f"{SBURL}/rest/v1/leyes?entidad=eq.{enc}&ambito=eq.municipal&select=id",
        headers={**HEADERS, 'Prefer': 'count=exact'})
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            cr = resp.headers.get('content-range','0-0/0')
            n = int(cr.split('/')[1])
            return n >= MAX_PER_MUNI
    except: return False
 
def insert_shells(rows):
    req = urllib.request.Request(
        f"{SBURL}/rest/v1/leyes",
        data=json.dumps(rows).encode(),
        headers={**HEADERS, 'Prefer':'return=representation'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError:
        return []
 
def rpc_chunks(ley_id, ley_nombre, chunks):
    req = urllib.request.Request(
        f"{SBURL}/rest/v1/rpc/leyes_chunks_replace_all",
        data=json.dumps({'p_ley_id':ley_id,'p_ley_nombre':ley_nombre,
                         'p_chunks':chunks}).encode(),
        headers=HEADERS, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return int(resp.read().decode().strip() or 0)
    except: return 0
 
def delete_shell(ley_id):
    req = urllib.request.Request(
        f"{SBURL}/rest/v1/leyes?id=eq.{ley_id}",
        headers=HEADERS, method='DELETE')
    try: urllib.request.urlopen(req, timeout=10)
    except: pass
 
# ─── Process PDF ─────────────────────────────────────────────────────────────
def process(ley_id, url, nombre):
    pdf = f'/tmp/sw_{ley_id}.pdf'; txt = f'/tmp/sw_{ley_id}.txt'
    r = subprocess.run(
        ['curl','-sL','-k','-A',UA,'-m','25','-o',pdf,
         '-w','%{http_code}|%{size_download}', fix_url(url)],
        capture_output=True, text=True)
    parts = (r.stdout.split('|') + ['0','0'])[:2]
    code, sz = parts
    try: sz_i = int(sz)
    except: sz_i = 0
    if code != '200' or sz_i < 1000: return 0, 'bad_url'
    subprocess.run(['pdftotext','-layout',pdf,txt], timeout=45, capture_output=True)
    text = Path(txt).read_text(errors='replace') if Path(txt).exists() else ''
    if len(text) < 200:
        d = f'/tmp/img_{ley_id}'
        Path(d).mkdir(exist_ok=True)
        subprocess.run(['pdftoppm','-r','150',pdf,f'{d}/page',
                        '-f','1','-l','30','-jpeg'],
                       timeout=90, capture_output=True)
        for img in sorted(Path(d).glob('page-*.jpg')):
            subprocess.run(['tesseract','-l','spa',str(img),str(img)[:-4]],
                capture_output=True, timeout=25,
                env={**os.environ,
                     'TESSDATA_PREFIX': os.environ.get('TESSDATA_PREFIX','')})
        text = '\n'.join(p.read_text() for p in sorted(Path(d).glob('page-*.txt')))
    chunks, mode = parse_any(text)
    if not chunks: return 0, 'no_chunks'
    n = rpc_chunks(ley_id, nombre, chunks)
    for f in [pdf, txt]:
        try: os.remove(f)
        except: pass
    return n, mode
 
# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    catalogo = load_catalogo()
    if ESTADO_KEY != 'all':
        catalogo = [m for m in catalogo if m['cve_ent'] == ESTADO_KEY.zfill(2)]
    print(f"📋 Procesando {len(catalogo)} municipios (estado={ESTADO_KEY}, max_per_muni={MAX_PER_MUNI})\n")
 
    summary = {'discovered': 0, 'inserted': 0, 'loaded': 0,
               'skipped': 0, 'no_pdfs': 0, 'failed': 0}
 
    for i, muni in enumerate(catalogo, 1):
        entidad_full = f"{muni['nom_mun']}, {muni['nom_ent']}"
        try:
            if already_loaded(entidad_full):
                summary['skipped'] += 1
                if i % 50 == 0:
                    print(f"  ... [{i}/{len(catalogo)}] SKIP {entidad_full}")
                continue
        except: pass
 
        urls = discover_pdfs(muni)
        if not urls:
            summary['no_pdfs'] += 1
            if i % 20 == 0 or summary['no_pdfs'] % 50 == 0:
                print(f"  [{i}/{len(catalogo)}] ⚠️  Sin PDFs: {entidad_full}")
            continue
 
        print(f"\n[{i}/{len(catalogo)}] ▶ {entidad_full}  ({len(urls)} PDFs)")
        summary['discovered'] += len(urls)
 
        shells = [{'nombre': urllib.parse.unquote(url.rsplit('/',1)[-1])[:120],
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
                summary['failed'] += 1
                delete_shell(shell['id'])
 
    print(f"\n{'='*50}")
    print(f"SUMMARY (estado={ESTADO_KEY}):")
    for k, v in summary.items():
        print(f"  {k:15s}: {v}")
    print(f"{'='*50}")
 
if __name__ == '__main__':
    main()
 
