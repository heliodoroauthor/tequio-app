#!/usr/bin/env python3
"""
tequio_sina_scraper.py v3 — Carga REAL de SINA via API interna

v3: Encontramos los endpoints JSON:
  - /SINA45/fechaMonitoreo/ultimo
  - /PresasPG/presas/reporte/{YYYY-MM-DD}

Setup:
    pip install playwright requests
    playwright install chromium

Uso:
    export SUPABASE_URL="..."
    export SUPABASE_SERVICE_ROLE_KEY="..."
    python tequio_sina_scraper.py [--headed] [--dry-run]

🦎 Cero Invención · Tequio · 2026
"""
import os, sys, json, argparse, time, tempfile
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
SINA_FECHA_URL = SINA_BASE + "SINA45/fechaMonitoreo/ultimo"
SINA_PRESAS_URL = SINA_BASE + "PresasPG/presas/reporte/{fecha}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

TMP = tempfile.gettempdir()
DATA_JSON = os.path.join(TMP, "sina_presas_data.json")


def headers():
    return {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}


def fetch_sina_data(headed=False, verbose=False):
    """Usa Playwright para llamar API JSON de SINA (necesita contexto browser)."""
    print(f"🌐 Lanzando Chromium ({'headed' if headed else 'headless'})...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed, args=['--no-sandbox','--disable-blink-features=AutomationControlled'])
        context = browser.new_context(
            user_agent=UA,
            ignore_https_errors=True,
        )
        page = context.new_page()
        
        # First load the main page to establish session
        print(f"📡 Calentando sesión con {SINA_BASE}Presas/")
        try:
            page.goto(SINA_BASE + "Presas/", wait_until="networkidle", timeout=45000)
            time.sleep(2)
        except Exception as e:
            print(f"⚠️ Warm timeout: {e}")
        
        # Now use page.request for API calls (uses browser context, passes anti-bot)
        print(f"📡 GET {SINA_FECHA_URL}")
        try:
            r = context.request.get(SINA_FECHA_URL, headers={"Accept": "application/json,text/plain,*/*"})
            print(f"  → {r.status} · {len(r.text())}b")
            raw_text = r.text()
            if verbose: print(f"  raw: {raw_text[:200]}")
            # Parse as JSON
            try:
                parsed = r.json()
            except Exception:
                # Maybe it's a plain string date
                fecha = raw_text.strip().strip('"').strip("'")
                if "T" in fecha: fecha = fecha.split("T")[0]
                fecha = fecha[:10]
            else:
                # JSON: could be string, list, or dict
                if isinstance(parsed, str):
                    fecha = parsed[:10]
                elif isinstance(parsed, list) and parsed:
                    first = parsed[0]
                    if isinstance(first, dict):
                        # Look for fecha-like fields
                        for k in ['fecha', 'fechaMonitoreo', 'fecha_monitoreo', 'fechaCorte', 'date', 'dateUltimo']:
                            if k in first and first[k]:
                                v = first[k]
                                fecha = v.split('T')[0] if 'T' in str(v) else str(v)[:10]
                                break
                        else:
                            # Pick any value that looks like a date
                            fecha = None
                            for v in first.values():
                                if isinstance(v, str) and re.match(r'^\d{4}-\d{2}-\d{2}', v):
                                    fecha = v[:10]; break
                    else:
                        fecha = str(first)[:10]
                elif isinstance(parsed, dict):
                    for k in ['fecha', 'fechaMonitoreo', 'fecha_monitoreo', 'fechaCorte', 'date']:
                        if k in parsed and parsed[k]:
                            v = parsed[k]
                            fecha = v.split('T')[0] if 'T' in str(v) else str(v)[:10]
                            break
                    else:
                        fecha = None
                else:
                    fecha = None
        except Exception as e:
            print(f"❌ Error fecha: {e}")
            browser.close()
            return None
        
        if not fecha:
            print("❌ No pude extraer fecha de la respuesta")
            browser.close()
            return None
        print(f"📅 Fecha de monitoreo más reciente: {fecha}")
        
        url = SINA_PRESAS_URL.format(fecha=fecha)
        print(f"📡 GET {url}")
        try:
            r = context.request.get(url, headers={"Accept": "application/json,text/plain,*/*"})
            print(f"  → {r.status} · {len(r.text())}b")
            data = r.json()
        except Exception as e:
            print(f"❌ Error reporte: {e}")
            browser.close()
            return None
        
        browser.close()
        return {"fecha": fecha, "data": data}


def normalize(raw, verbose=False):
    """Normaliza el JSON de SINA a formato presas_cuencas."""
    items = raw.get("data") if isinstance(raw, dict) else raw
    fecha = items.get("fecha") if isinstance(items, dict) else None
    
    # SINA returns various JSON shapes; figure out where the array is
    arr = None
    if isinstance(items, list):
        arr = items
    elif isinstance(items, dict):
        # Try common keys
        for k in ["presas", "data", "items", "rows", "result", "Presas"]:
            if k in items and isinstance(items[k], list):
                arr = items[k]
                break
        if arr is None:
            # Maybe items itself contains nested
            for v in items.values():
                if isinstance(v, list) and len(v) > 5:
                    arr = v
                    break
    
    if not arr:
        print("⚠️ No pude detectar array de presas en JSON. Inspecciona estructura:")
        if verbose:
            print(json.dumps(items, indent=2, ensure_ascii=False)[:1500])
        return []
    
    print(f"✓ Array detectado: {len(arr)} presas en JSON")
    if verbose and arr:
        print(f"  Sample row: {json.dumps(arr[0], indent=2, ensure_ascii=False)}")
    return arr


def upload_to_supabase(rows, fecha, verbose=False):
    """Inserta/update presas a Supabase."""
    if not rows:
        print("⚠️ Nada que subir")
        return 0
    
    # Map SINA fields to presas_cuencas schema. Common keys in SINA JSON:
    # - nombre / Nombre / nombrecomun
    # - cve_estado / estado / Estado
    # - capacidad / NAMO / NAMO_hm3
    # - almacenamiento / almactual / volumenactual
    # - llenado / porcentaje / pct
    payload = []
    for r in rows:
        rk = {k.lower(): v for k, v in r.items()} if isinstance(r, dict) else {}
        
        # SINA real field mapping
        nombre = rk.get("nombrecomun") or rk.get("nombreoficial") or rk.get("clavesih") or "Sin nombre"
        estado = rk.get("estado") or ""
        # NAMO = Nivel Aguas Máximo Ordinario (capacidad útil estándar)
        cap = rk.get("namoalmac") or rk.get("namealmac") or rk.get("capacidad")
        alm = rk.get("almacenaactual") or rk.get("almactual") or rk.get("almacenamiento")
        # SINA returns pct as decimal 0-1, multiply x100
        pct_raw = rk.get("llenano") or rk.get("llenamn") or rk.get("porcentaje") or rk.get("pct")
        
        try:
            cap_f = float(cap) if cap is not None else None
            alm_f = float(alm) if alm is not None else None
            if pct_raw is not None:
                p = float(pct_raw)
                # If pct is 0-1, multiply by 100
                pct_f = round(p * 100, 2) if p <= 1.5 else round(p, 2)
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
        })
    
    print(f"📤 Subiendo {len(payload)} presas a Supabase (fuente=SINA)...")
    BATCH = 100
    inserted = 0
    for i in range(0, len(payload), BATCH):
        batch = payload[i:i+BATCH]
        r = requests.post(f"{SB_URL}/rest/v1/presas_cuencas", json=batch,
                          headers={**headers(), "Prefer": "return=minimal"}, timeout=60)
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
    
    print("🦎 Tequio SINA Scraper v3 · API JSON directa")
    print("=" * 50)
    
    raw = fetch_sina_data(headed=args.headed, verbose=args.verbose)
    if not raw:
        print("❌ Falló fetch SINA")
        sys.exit(1)
    
    # Save raw JSON
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
