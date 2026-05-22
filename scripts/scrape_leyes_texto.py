#!/usr/bin/env python3
"""
scrape_leyes_texto.py (FIX-38)
================================
Descarga texto completo de leyes federales (www.diputados.gob.mx) y rellena
columna `leyes.texto`. Procesa solo URLs con texto NULL o muy corto.

Flujo:
  1) Selecciona leyes con url=diputados.gob.mx y texto NULL/corto
  2) GET URL con headers normales
  3) Parsea HTML con BeautifulSoup, extrae texto plano del body
  4) UPDATE leyes.texto

Logs a scraper_logs (slug=leyes_texto_diputados) para monitoreo SQL.
"""
import os, sys, time, json, re
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
MAX_TEXT_CHARS = 8000  # texto suficiente para embedding rico

STARTED = datetime.now(timezone.utc).isoformat()


def log_run(status, error_msg=None, notes=None, updated=0, failed=0):
    try:
        requests.post(
            f"{SB_URL}/rest/v1/scraper_logs",
            headers={**H_SB, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
            json=[{
                'scraper_slug': 'leyes_texto_diputados',
                'workflow_run_id': GH_RUN or None,
                'status': status,
                'rows_inserted': 0,
                'rows_updated': updated,
                'rows_skipped': failed,
                'fuente_url': 'https://www.diputados.gob.mx/LeyesBiblio/',
                'http_status': 200,
                'error_msg': (error_msg or '')[:1000] if error_msg else None,
                'notes': (json.dumps(notes) if notes else '')[:1000],
                'started_at': STARTED,
                'finished_at': datetime.now(timezone.utc).isoformat(),
            }],
            timeout=10,
        )
    except Exception as e:
        print(f"[log_run EXC] {e}")


def get_leyes_pendientes():
    """Leyes diputados.gob.mx con texto NULL o demasiado corto."""
    url = f"{SB_URL}/rest/v1/leyes?select=id,nombre,url,texto&url=like.*diputados.gob.mx*&order=id.asc"
    r = requests.get(url, headers=H_SB, timeout=30)
    r.raise_for_status()
    rows = r.json()
    # Filtrar en Python (Postgrest no permite filtros LENGTH facilmente)
    return [r for r in rows if not r.get('texto') or len(r.get('texto') or '') < 100]


def extract_text_from_html(html, base_url):
    """Extrae texto plano del documento legal, quitando navegación y scripts."""
    soup = BeautifulSoup(html, 'lxml')

    # Quitar elementos no-contenido
    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript', 'meta', 'link']):
        tag.decompose()

    # Buscar el contenedor principal con la ley.
    # Diputados.gob.mx pone el texto legal en <table> dentro de <body>
    main = soup.find('main') or soup.find('article') or soup.find('body')
    if not main:
        return ''

    # Extraer texto preservando saltos de línea
    text = main.get_text(separator='\n', strip=True)
    # Colapsar líneas múltiples
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text[:MAX_TEXT_CHARS]


def update_ley_texto(ley_id, texto):
    url = f"{SB_URL}/rest/v1/leyes?id=eq.{ley_id}"
    r = requests.patch(
        url,
        headers={**H_SB, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
        json={'texto': texto},
        timeout=20,
    )
    return r.ok, (r.text[:200] if not r.ok else None)


def fetch_ley(url):
    """GET con retries y detección de encoding."""
    h = {
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
        'Accept-Language': 'es-MX,es;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
    }
    for i in range(3):
        try:
            r = requests.get(url, headers=h, timeout=25, allow_redirects=True)
            if r.status_code == 429:
                time.sleep(3 * (i + 1))
                continue
            r.raise_for_status()
            # Encoding fix: si ISO-8859 dice charset utf-8 pero contenido es latin1
            if r.encoding and r.encoding.lower() in ('iso-8859-1', 'windows-1252'):
                r.encoding = 'cp1252'
            elif not r.encoding or 'utf' not in r.encoding.lower():
                r.encoding = r.apparent_encoding or 'utf-8'
            return r.text
        except Exception as e:
            if i == 2:
                raise
            time.sleep(2 * (i + 1))
    return None


def main():
    print("Tequio · Scrape texto completo de leyes (diputados.gob.mx)")
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
        print("  Nada que hacer.")
        return

    updated = 0
    failed = 0
    skipped_no_content = 0

    for i, ley in enumerate(pendientes, 1):
        ley_id = ley['id']
        nombre = (ley.get('nombre') or '')[:60]
        url = ley.get('url')
        try:
            html = fetch_ley(url)
            if not html:
                failed += 1
                print(f"  [{i}/{len(pendientes)}] id={ley_id} FAIL (no html) {nombre}")
                continue
            texto = extract_text_from_html(html, url)
            if not texto or len(texto) < 200:
                skipped_no_content += 1
                print(f"  [{i}/{len(pendientes)}] id={ley_id} SKIP (texto corto: {len(texto)}) {nombre}")
                continue
            ok, err = update_ley_texto(ley_id, texto)
            if ok:
                updated += 1
                if updated % 10 == 0:
                    print(f"  [{i}/{len(pendientes)}] id={ley_id} OK ({len(texto)} chars) {nombre}")
            else:
                failed += 1
                print(f"  [{i}/{len(pendientes)}] id={ley_id} FAIL update: {err}")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(pendientes)}] id={ley_id} EXC: {type(e).__name__}: {e}")

        time.sleep(0.4)  # rate limit benigno

    notes = {'updated': updated, 'failed': failed, 'skipped': skipped_no_content, 'total': len(pendientes)}
    status = 'ok' if failed == 0 else 'partial'
    log_run(status, notes=notes, updated=updated, failed=failed)
    print(f"\n[RESUMEN] updated={updated} failed={failed} skipped={skipped_no_content} total={len(pendientes)}")
    print(f"rows_updated={updated}")


if __name__ == '__main__':
    main()
