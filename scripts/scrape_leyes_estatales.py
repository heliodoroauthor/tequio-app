#!/usr/bin/env python3
"""
scrape_leyes_estatales.py (FIX-39)
====================================
Scraper genérico para leyes/marcos jurídicos de los 32 congresos estatales.

Características:
  - Detecta HTML vs PDF por Content-Type / extensión
  - Fallback a Playwright headless cuando recibe Imperva/Cloudflare/anti-bot
  - HTML: BeautifulSoup
  - PDF: pdfplumber
  - Trunca a 8000 chars (suficiente para embedding)
  - Actualiza leyes.texto y deja logs en scraper_logs

NOTA: Las URLs estatales actuales en `public.leyes` apuntan a páginas índice
del marco jurídico de cada congreso (no a leyes individuales). El texto
extraído es el catálogo/listado de leyes vigentes por estado, útil para
RAG de "qué legislación tiene tal estado".
"""
import os, sys, time, json, re, io
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

SB_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SB_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
GH_RUN = os.environ.get('GITHUB_RUN_ID', '')

if not SB_URL or not SB_KEY:
    print("ERROR: SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY missing")
    sys.exit(1)

H_SB = {'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}'}
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36'
MAX_TEXT_CHARS = 8000
STARTED = datetime.now(timezone.utc).isoformat()

# Indicadores de anti-bot (Imperva, Cloudflare, etc) en HTML inicial
ANTIBOT_MARKERS = [
    'Challenge Validation',
    'Just a moment',
    'cf-browser-verification',
    'Checking your browser before',
    'Please Wait...',
    'security check to access',
]


def is_antibot_response(html):
    if not html or len(html) < 3000:
        return True  # demasiado corto, sospechoso
    sample = html[:5000]
    return any(m in sample for m in ANTIBOT_MARKERS)


def log_run(status, error_msg=None, notes=None, updated=0, failed=0):
    try:
        requests.post(
            f"{SB_URL}/rest/v1/scraper_logs",
            headers={**H_SB, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
            json=[{
                'scraper_slug': 'leyes_texto_estatales',
                'workflow_run_id': GH_RUN or None,
                'status': status,
                'rows_inserted': 0,
                'rows_updated': updated,
                'rows_skipped': failed,
                'fuente_url': 'multi (congresos estatales)',
                'http_status': 200,
                'error_msg': (error_msg or '')[:1000] if error_msg else None,
                'notes': (json.dumps(notes, ensure_ascii=False) if notes else '')[:1500],
                'started_at': STARTED,
                'finished_at': datetime.now(timezone.utc).isoformat(),
            }],
            timeout=10,
        )
    except Exception as e:
        print(f"[log_run EXC] {e}")


def get_leyes_pendientes():
    """Leyes que NO son diputados.gob.mx (estatales) con texto NULL/corto."""
    url = f"{SB_URL}/rest/v1/leyes?select=id,nombre,url,texto&url=not.like.*diputados.gob.mx*&order=id.asc"
    r = requests.get(url, headers=H_SB, timeout=30)
    r.raise_for_status()
    rows = r.json()
    return [r for r in rows if r.get('url') and (not r.get('texto') or len(r.get('texto') or '') < 100)]


def extract_text_from_html(html):
    soup = BeautifulSoup(html, 'lxml')
    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript', 'meta', 'link', 'iframe']):
        tag.decompose()
    main = soup.find('main') or soup.find('article') or soup.find('body')
    if not main:
        return ''
    text = main.get_text(separator='\n', strip=True)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text[:MAX_TEXT_CHARS]


def extract_text_from_pdf(pdf_bytes):
    try:
        import pdfplumber
    except ImportError:
        print("[WARN] pdfplumber no instalado, skip PDF")
        return ''
    try:
        chunks = []
        total = 0
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:30]:  # max 30 páginas
                t = page.extract_text() or ''
                if t:
                    chunks.append(t)
                    total += len(t)
                if total >= MAX_TEXT_CHARS:
                    break
        text = '\n\n'.join(chunks)
        return re.sub(r'\n{3,}', '\n\n', text)[:MAX_TEXT_CHARS]
    except Exception as e:
        print(f"[pdfplumber EXC] {e}")
        return ''


def update_ley_texto(ley_id, texto):
    url = f"{SB_URL}/rest/v1/leyes?id=eq.{ley_id}"
    r = requests.patch(
        url,
        headers={**H_SB, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
        json={'texto': texto},
        timeout=20,
    )
    return r.ok, (r.text[:200] if not r.ok else None)


def fetch_url(url):
    """GET con detección de encoding. Devuelve (content_bytes, content_type)."""
    h = {
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/pdf,*/*;q=0.8',
        'Accept-Language': 'es-MX,es;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
    }
    for i in range(3):
        try:
            r = requests.get(url, headers=h, timeout=30, allow_redirects=True)
            if r.status_code == 429:
                time.sleep(3 * (i + 1))
                continue
            r.raise_for_status()
            ct = (r.headers.get('Content-Type') or '').lower()
            return r.content, ct, r
        except Exception as e:
            if i == 2:
                raise
            time.sleep(2 * (i + 1))
    return None, '', None


_PLAYWRIGHT_CACHE = {'pw': None, 'browser': None, 'ctx': None}


def fetch_with_playwright(url, timeout_s=60):
    """Lazy-init Playwright y reusa el browser."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[WARN] playwright no instalado, fallback no disponible")
        return None
    try:
        if _PLAYWRIGHT_CACHE['ctx'] is None:
            pw = sync_playwright().start()
            _PLAYWRIGHT_CACHE['pw'] = pw
            _PLAYWRIGHT_CACHE['browser'] = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            _PLAYWRIGHT_CACHE['ctx'] = _PLAYWRIGHT_CACHE['browser'].new_context(
                user_agent=UA, locale='es-MX', viewport={'width': 1366, 'height': 768}
            )
        ctx = _PLAYWRIGHT_CACHE['ctx']
        page = ctx.new_page()
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=timeout_s * 1000)
            for _try in range(8):
                page.wait_for_timeout(4000)
                title = page.title()
                content_snip = page.content()[:500] if page.content() else ''
                if 'Challenge' not in title and 'Just a moment' not in content_snip:
                    break
            html = page.content()
            return html
        finally:
            page.close()
    except Exception as e:
        print(f"[playwright EXC] {e}")
        return None


def cleanup_playwright():
    try:
        if _PLAYWRIGHT_CACHE['ctx']:
            _PLAYWRIGHT_CACHE['ctx'].close()
        if _PLAYWRIGHT_CACHE['browser']:
            _PLAYWRIGHT_CACHE['browser'].close()
        if _PLAYWRIGHT_CACHE['pw']:
            _PLAYWRIGHT_CACHE['pw'].stop()
    except Exception:
        pass


def process_ley(ley):
    """Devuelve texto extraído (str) o '' si no se pudo."""
    url = ley.get('url')
    try:
        content, ct, resp = fetch_url(url)
    except Exception as e:
        print(f"  [fetch ERR] {type(e).__name__}: {e}")
        return ''
    if not content:
        return ''

    # PDF detection
    is_pdf = 'pdf' in ct or url.lower().endswith('.pdf') or content[:4] == b'%PDF'
    if is_pdf:
        return extract_text_from_pdf(content)

    # HTML: detect encoding
    if resp.encoding and resp.encoding.lower() in ('iso-8859-1', 'windows-1252'):
        resp.encoding = 'cp1252'
    elif not resp.encoding or 'utf' not in (resp.encoding or '').lower():
        resp.encoding = resp.apparent_encoding or 'utf-8'
    html = resp.text

    # Anti-bot detection → fallback Playwright
    if is_antibot_response(html):
        print(f"  [antibot detected on {url[:80]}] trying Playwright...")
        html = fetch_with_playwright(url) or ''
        if not html or is_antibot_response(html):
            print(f"  [playwright also blocked on {url[:80]}]")
            return ''

    return extract_text_from_html(html)


def main():
    print("Tequio · Scrape leyes estatales (32 congresos)")
    try:
        pendientes = get_leyes_pendientes()
    except Exception as e:
        msg = f"get_leyes_pendientes EXC: {type(e).__name__}: {e}"
        print(f"ERROR: {msg}")
        log_run('fail', error_msg=msg)
        sys.exit(1)

    print(f"  Pendientes: {len(pendientes)}")
    if not pendientes:
        log_run('ok', notes={'pendientes': 0})
        return

    updated = 0
    failed = 0
    skipped = 0
    pw_used = 0
    by_domain = {}

    for i, ley in enumerate(pendientes, 1):
        ley_id = ley['id']
        nombre = (ley.get('nombre') or '')[:50]
        url = ley.get('url')
        domain = re.match(r'^https?://([^/]+)', url or '')
        domain = domain.group(1) if domain else 'unknown'

        try:
            texto = process_ley(ley)
            if not texto or len(texto) < 200:
                skipped += 1
                by_domain.setdefault(domain, {'ok': 0, 'fail': 0, 'skip': 0})['skip'] += 1
                print(f"  [{i}/{len(pendientes)}] SKIP {domain} ({len(texto)} chars) {nombre}")
                continue
            ok, err = update_ley_texto(ley_id, texto)
            if ok:
                updated += 1
                by_domain.setdefault(domain, {'ok': 0, 'fail': 0, 'skip': 0})['ok'] += 1
                if updated % 10 == 0:
                    print(f"  [{i}/{len(pendientes)}] OK {domain} ({len(texto)} chars) {nombre}")
            else:
                failed += 1
                by_domain.setdefault(domain, {'ok': 0, 'fail': 0, 'skip': 0})['fail'] += 1
                print(f"  [{i}/{len(pendientes)}] FAIL update id={ley_id}: {err}")
        except Exception as e:
            failed += 1
            by_domain.setdefault(domain, {'ok': 0, 'fail': 0, 'skip': 0})['fail'] += 1
            print(f"  [{i}/{len(pendientes)}] EXC {domain}: {type(e).__name__}: {e}")

        time.sleep(0.3)

    cleanup_playwright()

    notes = {'updated': updated, 'failed': failed, 'skipped': skipped, 'total': len(pendientes), 'by_domain': by_domain}
    status = 'ok' if failed == 0 and skipped < len(pendientes) * 0.5 else 'partial'
    log_run(status, notes=notes, updated=updated, failed=failed)
    print(f"\n[RESUMEN] updated={updated} failed={failed} skipped={skipped} total={len(pendientes)}")
    print(f"rows_updated={updated}")


if __name__ == '__main__':
    main()
