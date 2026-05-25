#!/usr/bin/env python3
"""
tequio_sina_scraper.py — Scraper SINA CONAGUA con Playwright

SINA tiene anti-bot que bloquea curl/requests, pero Playwright (real Chromium)
debería pasar.

Setup (una vez):
    pip install playwright requests
    playwright install chromium

Uso:
    export SUPABASE_URL="https://mhsuihwjgtzxflesbnxv.supabase.co"
    export SUPABASE_SERVICE_ROLE_KEY="<service role>"
    python tequio_sina_scraper.py [--headed] [--dry-run]

🦎 Cero Invención · Tequio · 2026
"""
import os, sys, json, argparse, time, re
from datetime import date

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: falta playwright. Instala:\n  pip install playwright\n  playwright install chromium")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: falta requests. pip install requests")
    sys.exit(1)


SB_URL = os.environ.get("SUPABASE_URL", "https://mhsuihwjgtzxflesbnxv.supabase.co")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
if not SB_KEY:
    print("ERROR: falta SUPABASE_SERVICE_ROLE_KEY en env.")
    sys.exit(1)

SINA_URL = "https://sinav30.conagua.gob.mx:8080/Presas/"
UA_REAL = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

def headers():
    return {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}

def scrape(headed=False, verbose=False):
    """Visita SINA y extrae datos de presas."""
    print(f"🌐 Lanzando Chromium ({'headed' if headed else 'headless'})...")
    with sync_playwright() as p:
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
        
        print(f"📡 Navegando a {SINA_URL}")
        try:
            page.goto(SINA_URL, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"⚠️ Timeout en networkidle, sigo: {e}")
        
        time.sleep(3)  # extra wait para JS dinámico
        
        # Detect anti-bot
        title = page.title()
        body_text_preview = page.evaluate("document.body.innerText.slice(0, 500)")
        print(f"📄 Title: {title}")
        if verbose:
            print(f"📄 Preview: {body_text_preview[:200]}")
        
        if "403" in title or "forbidden" in body_text_preview.lower() or "imperva" in page.content().lower():
            print("❌ Anti-bot detectado. SINA bloqueó Playwright también.")
            browser.close()
            return None
        
        # Inspect what's on the page
        all_links = page.eval_on_selector_all("a", "els => els.map(e => ({text:e.innerText.trim(), href:e.href})).filter(l => l.text)")
        if verbose:
            print(f"🔗 Links encontrados: {len(all_links)}")
            for link in all_links[:20]:
                print(f"  - {link['text']}: {link['href']}")
        
        # Look for table data or specific selectors
        tables = page.eval_on_selector_all("table", "els => els.length")
        print(f"📊 Tablas en página: {tables}")
        
        # Try to find a links to specific dam list or data
        # SINA usually has navigation: state → dam list
        # Capture HTML for debugging
        html = page.content()
        with open("/tmp/sina_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"💾 HTML guardado en /tmp/sina_debug.html ({len(html)} bytes)")
        
        # Look for iframe or data containers
        iframes = page.frames
        print(f"🪟 Frames: {len(iframes)}")
        for frame in iframes:
            print(f"  - {frame.name}: {frame.url}")
        
        # Take screenshot for diagnosis
        page.screenshot(path="/tmp/sina_screenshot.png", full_page=True)
        print(f"📸 Screenshot guardado en /tmp/sina_screenshot.png")
        
        # Try to scroll and trigger lazy load
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)
        
        # Re-inspect
        tables_after = page.eval_on_selector_all("table", "els => els.length")
        all_text_after = page.evaluate("document.body.innerText")
        
        # Look for dam data patterns
        presa_pattern = re.compile(r"([A-Z][a-zA-Z\s\.,]+)\s+([\d,\.]+)\s*(hm3|hm³|%)", re.IGNORECASE)
        matches = presa_pattern.findall(all_text_after)
        print(f"🎯 Posibles datos de presas detectados: {len(matches)}")
        if matches and verbose:
            for m in matches[:10]:
                print(f"  - {m}")
        
        browser.close()
        return {
            "title": title,
            "tables_count": tables,
            "iframes_count": len(iframes),
            "iframe_urls": [f.url for f in iframes],
            "data_matches": len(matches),
            "html_size": len(html)
        }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--headed", action="store_true", help="Mostrar Chromium (no headless)")
    p.add_argument("--dry-run", action="store_true", help="Solo escanear, no insertar")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    
    print("🦎 Tequio SINA Scraper · diagnóstico")
    print("=" * 50)
    
    result = scrape(headed=args.headed, verbose=args.verbose)
    if not result:
        print("\n❌ Scraping bloqueado. Revisa /tmp/sina_debug.html y /tmp/sina_screenshot.png")
        sys.exit(1)
    
    print("\n=== RESUMEN ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("\n💡 Próximos pasos:")
    print("   1. Revisa /tmp/sina_screenshot.png para ver qué carga la página")
    print("   2. Revisa /tmp/sina_debug.html para entender estructura")
    print("   3. Si hay iframes con datos, los scrapeamos por separado")
    print("   4. Si SINA usa JS-API interno, lo interceptamos con page.route")


if __name__ == "__main__":
    main()
