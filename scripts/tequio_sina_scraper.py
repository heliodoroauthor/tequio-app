#!/usr/bin/env python3
"""
tequio_sina_scraper.py v2 — Scraper SINA Playwright

v2: Windows-compatible paths + navega a "Reporte" + extrae tabla de presas + intercepta XHR.

Setup:
    pip install playwright requests
    playwright install chromium

Uso:
    export SUPABASE_URL="https://mhsuihwjgtzxflesbnxv.supabase.co"
    export SUPABASE_SERVICE_ROLE_KEY="<service role>"
    python tequio_sina_scraper.py --headed --verbose

🦎 Cero Invención · Tequio · 2026
"""
import os, sys, json, argparse, time, re, tempfile
from datetime import date

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: pip install playwright && playwright install chromium")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)

SB_URL = os.environ.get("SUPABASE_URL", "https://mhsuihwjgtzxflesbnxv.supabase.co")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not SB_KEY:
    print("ERROR: falta SUPABASE_SERVICE_ROLE_KEY")
    sys.exit(1)

SINA_BASE = "https://sinav30.conagua.gob.mx:8080/Presas/"
UA_REAL = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

# Output paths: use OS temp dir (works on Windows + Linux)
TMP = tempfile.gettempdir()
DEBUG_HTML = os.path.join(TMP, "sina_debug.html")
SCREENSHOT = os.path.join(TMP, "sina_screenshot.png")
DATA_JSON = os.path.join(TMP, "sina_presas.json")
XHR_LOG = os.path.join(TMP, "sina_xhr.log")


def headers():
    return {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}


def scrape(headed=False, verbose=False, dry_run=False):
    """Visita SINA, navega a Reporte, intercepta XHR + extrae datos."""
    captured_xhr = []
    
    with sync_playwright() as p:
        print(f"🌐 Lanzando Chromium ({'headed' if headed else 'headless'})...")
        browser = p.chromium.launch(headless=not headed, args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
        ])
        context = browser.new_context(
            user_agent=UA_REAL,
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        page = context.new_page()
        page.set_default_timeout(45000)
        
        # Intercept XHR to capture data API calls
        def on_response(resp):
            url = resp.url
            ct = resp.headers.get("content-type", "")
            if "json" in ct or "xml" in ct or ".aspx" in url.lower() or "ashx" in url.lower():
                try:
                    body = resp.text()
                    captured_xhr.append({
                        "url": url,
                        "status": resp.status,
                        "content_type": ct,
                        "body_preview": body[:5000] if body else None,
                        "body_size": len(body) if body else 0,
                    })
                    if verbose:
                        print(f"📡 XHR captured: {resp.status} {url[:80]} ({len(body) if body else 0}b)")
                except Exception:
                    pass
        page.on("response", on_response)
        
        print(f"📡 Navegando a {SINA_BASE}")
        try:
            page.goto(SINA_BASE, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"⚠️ networkidle timeout: {e}")
        time.sleep(3)
        
        title = page.title()
        print(f"📄 Title: {title}")
        
        # Try to click "Reporte" link
        try:
            reporte_link = page.locator("a:has-text('Reporte')").first
            if reporte_link.is_visible(timeout=5000):
                print("🔗 Click en 'Reporte'...")
                reporte_link.click()
                time.sleep(4)
                page.wait_for_load_state("networkidle", timeout=20000)
                print(f"✓ Página después de click: {page.title()}")
        except Exception as e:
            print(f"⚠️ No pude click Reporte: {e}")
        
        # Now try to extract tabular data
        all_tables_data = page.evaluate("""
            () => {
                const tables = Array.from(document.querySelectorAll('table'));
                return tables.map((t, idx) => {
                    const rows = Array.from(t.querySelectorAll('tr')).map(tr => 
                        Array.from(tr.querySelectorAll('th,td')).map(c => c.innerText.trim())
                    );
                    return { idx, rows_count: rows.length, rows };
                });
            }
        """)
        
        print(f"📊 Tablas encontradas: {len(all_tables_data)}")
        for t in all_tables_data:
            print(f"  - Tabla {t['idx']}: {t['rows_count']} filas")
            if verbose and t['rows']:
                for r in t['rows'][:3]:
                    print(f"    {r}")
        
        # Save debug artifacts
        html = page.content()
        with open(DEBUG_HTML, "w", encoding="utf-8") as f:
            f.write(html)
        page.screenshot(path=SCREENSHOT, full_page=True)
        
        # Save XHR log
        with open(XHR_LOG, "w", encoding="utf-8") as f:
            json.dump(captured_xhr, f, indent=2, ensure_ascii=False)
        
        # Extract any useful patterns
        body_text = page.evaluate("document.body.innerText")
        
        # Look for dam-data patterns
        # Pattern: "Nombre Presa ... 1234.56 hm³ ... 12.3%"
        dam_pattern = re.compile(
            r"([A-ZÁÉÍÓÚÑ][A-Za-záéíóúñ\s\.,\-]{3,80})\s+([\d,\.]+)\s*hm[³3]\s+([\d,\.]+)\s*(%|hm)",
            re.IGNORECASE
        )
        matches = dam_pattern.findall(body_text)
        
        browser.close()
        
        return {
            "title": title,
            "tables_count": len(all_tables_data),
            "tables_summary": [{"idx": t["idx"], "rows": t["rows_count"]} for t in all_tables_data],
            "xhr_captured": len(captured_xhr),
            "xhr_with_data": sum(1 for x in captured_xhr if x.get("body_size", 0) > 100),
            "pattern_matches": len(matches),
            "tables_data": all_tables_data,
            "xhr_sample": [{"url": x["url"], "size": x["body_size"], "status": x["status"]} for x in captured_xhr[:20]],
        }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--headed", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    
    print("🦎 Tequio SINA Scraper v2 · diagnostico avanzado")
    print("=" * 50)
    
    result = scrape(headed=args.headed, verbose=args.verbose, dry_run=args.dry_run)
    if not result:
        print("\n❌ Falló scraping")
        sys.exit(1)
    
    # Save full result
    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print("\n=== RESUMEN ===")
    summary = {k: v for k, v in result.items() if k not in ["tables_data", "xhr_sample"]}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    
    print(f"\n💾 Artefactos guardados en {TMP}:")
    print(f"   - {DEBUG_HTML}")
    print(f"   - {SCREENSHOT}")
    print(f"   - {DATA_JSON}  ← incluye tablas + XHR samples")
    print(f"   - {XHR_LOG}  ← log completo XHR")
    
    if result["tables_count"] > 0:
        print(f"\n📊 Primeras 5 filas de la tabla mas grande:")
        biggest = max(result["tables_data"], key=lambda t: t["rows_count"])
        for r in biggest["rows"][:5]:
            print(f"   {r}")
    
    if result["xhr_captured"] > 0:
        print(f"\n📡 Top 5 XHR capturados:")
        for x in result["xhr_sample"][:5]:
            print(f"   {x['status']} {x['size']}b · {x['url'][:90]}")


if __name__ == "__main__":
    main()
