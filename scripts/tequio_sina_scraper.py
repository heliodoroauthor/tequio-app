#!/usr/bin/env python3
"""
tequio_sina_scraper.py v7 — Fetch interno vía page.evaluate (evade Akamai 403)

v7: SINA bloquea API directas desde IPs GHA con 403 (Akamai/Imperva WAF).
    Solución: usar page.evaluate(async () => fetch(...)) para que la llamada
    se origine desde el JS del DOM, heredando cookies + origin + contexto.
v6: lat/lng para mapa Leaflet.
v3: descubrimos los endpoints JSON.

Setup:
    pip install playwright requests
    playwright install chromium

Uso:
    export SUPABASE_URL="..."
    export SUPABASE_SERVICE_ROLE_KEY="..."
    python tequio_sina_scraper.py [--headed] [--dry-run]

🦎 Cero Invención · Tequio · 2026
"""
import os, sys, json, argparse, time, tempfile, re
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

SINA_BASE = "https://sinav30.conagua.gob.mx:8080/"
SINA_FECHA_PATH = "SINA45/fechaMonitoreo/ultimo"
SINA_REPORTE_PATH = "PresasPG/presas/reporte/{fecha}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

TMP = tempfile.gettempdir()
DATA_JSON = os.path.join(TMP, "sina_presas_data.json")


def headers():
    return {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}


def fetch_sina_data(headed=False, verbose=False):
    """v8: upsert (merge-duplicates) + v7 fetch desde dentro del JS de la página (evade WAF)."""
    print(f"🌐 Lanzando Chromium ({'headed' if headed else 'headless'})...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
            ],
        )
        context = browser.new_context(
            user_agent=UA,
            ignore_https_errors=True,
            locale='es-MX',
            timezone_id='America/Mexico_City',
            viewport={'width': 1920, 'height': 1080},
        )
        # Stealth: remove webdriver flag
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['es-MX', 'es', 'en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
        """)
        page = context.new_page()

        print(f"📡 Cargando {SINA_BASE}Presas/ ...")
        try:
            page.goto(SINA_BASE + "Presas/", wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"⚠️ Page load timeout (sigue de todas formas): {e}")

        # Wait a bit for any SPA to initialize and Akamai cookie to be set
        time.sleep(4)

        # Verify we got past Akamai by checking the page has actual content
        title = page.title()
        body_snippet = page.evaluate("document.body ? document.body.innerText.slice(0,200) : ''")
        if verbose:
            print(f"  Title: {title!r}")
            print(f"  Body preview: {body_snippet[:200]!r}")

        # Now do the fetch from INSIDE the page JS - inherits all browser context
        print(f"📡 fetch interno → {SINA_FECHA_PATH}")
        try:
            fecha_response = page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch('/{SINA_FECHA_PATH}', {{
                            credentials: 'include',
                            headers: {{ 'Accept': 'application/json,text/plain,*/*' }}
                        }});
                        const status = r.status;
                        const text = await r.text();
                        return {{ status, text }};
                    }} catch(e) {{
                        return {{ status: 0, text: 'JS_ERROR: ' + (e.message||e) }};
                    }}
                }}
            """)
        except Exception as e:
            print(f"❌ Error evaluando JS fecha: {e}")
            browser.close()
            return None

        print(f"  → status {fecha_response.get('status')} · {len(fecha_response.get('text',''))}b")
        if verbose:
            print(f"  preview: {fecha_response.get('text','')[:300]!r}")

        if fecha_response.get('status') != 200:
            print(f"❌ SINA fecha API: status {fecha_response.get('status')}")
            print(f"   raw: {fecha_response.get('text','')[:400]}")
            browser.close()
            return None

        # Parse fecha
        raw_text = fecha_response.get('text', '')
        fecha = None
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, str):
                fecha = parsed[:10]
            elif isinstance(parsed, list) and parsed:
                first = parsed[0]
                if isinstance(first, dict):
                    for k in ['fecha', 'fechaMonitoreo', 'fecha_monitoreo', 'fechaCorte', 'date', 'dateUltimo']:
                        if k in first and first[k]:
                            v = first[k]
                            fecha = v.split('T')[0] if 'T' in str(v) else str(v)[:10]
                            break
                else:
                    fecha = str(first)[:10]
            elif isinstance(parsed, dict):
                for k in ['fecha', 'fechaMonitoreo', 'fecha_monitoreo', 'fechaCorte', 'date']:
                    if k in parsed and parsed[k]:
                        v = parsed[k]
                        fecha = v.split('T')[0] if 'T' in str(v) else str(v)[:10]
                        break
        except Exception as e:
            print(f"❌ No pude parsear fecha JSON: {e}")
            browser.close()
            return None

        if not fecha:
            print(f"❌ No pude extraer fecha de: {raw_text[:300]}")
            browser.close()
            return None
        print(f"📅 Fecha de monitoreo más reciente: {fecha}")

        reporte_path = SINA_REPORTE_PATH.format(fecha=fecha)
        print(f"📡 fetch interno → {reporte_path}")
        try:
            reporte_response = page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch('/{reporte_path}', {{
                            credentials: 'include',
                            headers: {{ 'Accept': 'application/json,text/plain,*/*' }}
                        }});
                        const status = r.status;
                        const text = await r.text();
                        return {{ status, text }};
                    }} catch(e) {{
                        return {{ status: 0, text: 'JS_ERROR: ' + (e.message||e) }};
                    }}
                }}
            """)
        except Exception as e:
            print(f"❌ Error evaluando JS reporte: {e}")
            browser.close()
            return None

        print(f"  → status {reporte_response.get('status')} · {len(reporte_response.get('text',''))}b")
        if reporte_response.get('status') != 200:
            print(f"❌ SINA reporte API: status {reporte_response.get('status')}")
            print(f"   raw: {reporte_response.get('text','')[:400]}")
            browser.close()
            return None

        try:
            data = json.loads(reporte_response.get('text', ''))
        except Exception as e:
            print(f"❌ Reporte no es JSON válido: {e}")
            print(f"   raw: {reporte_response.get('text','')[:300]}")
            browser.close()
            return None

        browser.close()
        return {"fecha": fecha, "data": data}


def normalize(raw, verbose=False):
    items = raw.get("data") if isinstance(raw, dict) else raw

    arr = None
    if isinstance(items, list):
        arr = items
    elif isinstance(items, dict):
        for k in ["presas", "data", "items", "rows", "result", "Presas"]:
            if k in items and isinstance(items[k], list):
                arr = items[k]
                break
        if arr is None:
            for v in items.values():
                if isinstance(v, list) and len(v) > 5:
                    arr = v
                    break

    if not arr:
        print("⚠️ No pude detectar array de presas en JSON.")
        if verbose:
            print(json.dumps(items, indent=2, ensure_ascii=False)[:1500])
        return []

    print(f"✓ Array detectado: {len(arr)} presas en JSON")
    if verbose and arr:
        print(f"  Sample row: {json.dumps(arr[0], indent=2, ensure_ascii=False)}")
    return arr


def _to_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except (ValueError, TypeError):
        return None


def upload_to_supabase(rows, fecha, verbose=False):
    if not rows:
        print("⚠️ Nada que subir")
        return 0

    payload = []
    geo_ok = 0
    for r in rows:
        rk = {k.lower(): v for k, v in r.items()} if isinstance(r, dict) else {}

        nombre = rk.get("nombrecomun") or rk.get("nombreoficial") or rk.get("clavesih") or "Sin nombre"
        estado = rk.get("estado") or ""
        cap = rk.get("namoalmac") or rk.get("namealmac") or rk.get("capacidad")
        alm = rk.get("almacenaactual") or rk.get("almactual") or rk.get("almacenamiento")
        pct_raw = rk.get("llenano") or rk.get("llenamn") or rk.get("porcentaje") or rk.get("pct")

        lat = _to_float(rk.get("latitud") or rk.get("lat") or rk.get("latitude"))
        lng = _to_float(rk.get("longitud") or rk.get("lng") or rk.get("lon") or rk.get("longitude"))
        if lat is not None and lng is not None:
            if not (10 <= lat <= 35 and -120 <= lng <= -85):
                lat, lng = None, None
            else:
                geo_ok += 1

        try:
            cap_f = _to_float(cap)
            alm_f = _to_float(alm)
            if pct_raw is not None:
                p = _to_float(pct_raw)
                if p is not None:
                    pct_f = round(p * 100, 2) if p <= 1.5 else round(p, 2)
                else:
                    pct_f = None
            elif cap_f and alm_f is not None and cap_f > 0:
                pct_f = round(alm_f / cap_f * 100, 2)
            else:
                pct_f = None
        except Exception:
            cap_f = alm_f = pct_f = None

        payload.append({
            "fecha_corte": fecha,
            "presa": str(nombre)[:200],
            "estado": str(estado)[:100],
            "capacidad_total_hm3": cap_f,
            "almacenamiento_hm3": alm_f,
            "pct_almacenamiento": pct_f,
            "fuente": "SINA",
            "region_hidrologica": (rk.get("regioncna") or "")[:100] if rk.get("regioncna") else None,
            "latitud": lat,
            "longitud": lng,
        })

    print(f"📤 Subiendo {len(payload)} presas a Supabase (fuente=SINA) · {geo_ok} con geocoord")
    BATCH = 100
    inserted = 0
    for i in range(0, len(payload), BATCH):
        batch = payload[i:i+BATCH]
        r = requests.post(f"{SB_URL}/rest/v1/presas_cuencas?on_conflict=presa,fecha_corte", json=batch,
                          headers={**headers(), "Prefer": "return=minimal,resolution=merge-duplicates"}, timeout=60)
        if r.status_code >= 300:
            print(f"  ✗ batch {i} failed {r.status_code}: {r.text[:200]}")
            return inserted
        inserted += len(batch)
        if verbose: print(f"  ✓ batch {i}: {len(batch)} ok")
    return inserted


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--headed", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    print("🦎 Tequio SINA Scraper v8 · fetch interno (evade Akamai)")
    print("=" * 60)

    raw = fetch_sina_data(headed=args.headed, verbose=args.verbose)
    if not raw:
        print("❌ Falló fetch SINA")
        sys.exit(1)

    with open(DATA_JSON, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)
    print(f"💾 Raw JSON guardado en {DATA_JSON}")

    rows = normalize(raw, verbose=args.verbose)

    if args.dry_run:
        print(f"🧪 dry-run · {len(rows)} presas detectadas, no subo")
        return

    inserted = upload_to_supabase(rows, raw["fecha"], verbose=args.verbose)
    print(f"\n=== RESUMEN ===\n  fecha:    {raw['fecha']}\n  detectadas: {len(rows)}\n  subidas:    {inserted}")


if __name__ == "__main__":
    main()
