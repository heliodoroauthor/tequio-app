#!/usr/bin/env python3
"""
Tequio - Geo enrichment de municipios (lat/lng/altitud).

Para cada municipio, obtiene las coordenadas del centroide via INEGI v2.
Endpoint: https://gaia.inegi.org.mx/wscatgeo/v2/mgem/{cvegeo}

v2 response schema:
  {"datos":[{"cve_ent":"01","cve_mun":"001","nom_mun":"Aguascalientes",
             "lat":"22.034","lng":"-102.362","minx":...,"maxx":...,"miny":...,"maxy":...}]}

Env vars:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
Opcional: GITHUB_RUN_ID, MAX_WORKERS (default 10)
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import requests

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
GH_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "10"))

if not SB_URL or not SB_KEY:
    print("[geo] Faltan SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
    sys.exit(1)

SCRAPER_SLUG = "inegi_geo_municipios"
UA = "Tequio.app/1.0 (civic-data; +https://tequio.app)"
INEGI_BASE = "https://gaia.inegi.org.mx/wscatgeo"
INEGI_V2 = "https://gaia.inegi.org.mx/wscatgeo/v2"


def to_num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_int(v):
    n = to_num(v)
    if n is None:
        return None
    try:
        return int(n)
    except (TypeError, ValueError):
        return None


def http_get_json(url, retries=3, backoff=1.5):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    for i in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except Exception:
            pass
        time.sleep(backoff * (i + 1))
    return None


def fetch_municipios_list():
    rows = []
    offset = 0
    chunk = 1000
    while True:
        url = f"{SB_URL}/rest/v1/municipios?select=clave_inegi,clave_entidad,clave_municipio,nombre&order=clave_inegi.asc&offset={offset}&limit={chunk}"
        r = requests.get(url, headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Accept": "application/json"}, timeout=60)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < chunk:
            break
        offset += chunk
    return rows


def fetch_geo(municipio):
    cve_ent = municipio["clave_entidad"]
    cve_mun = municipio["clave_municipio"]
    clave_inegi = municipio["clave_inegi"]

    url = f"{INEGI_V2}/mgem/{clave_inegi}"
    data = http_get_json(url)

    if not data:
        url_v1 = f"{INEGI_BASE}/mloc/{cve_ent}/{cve_mun}/0001"
        data = http_get_json(url_v1)

    if not data:
        return {"clave_inegi": clave_inegi, "error": "no_data"}

    rows = data.get("datos") if isinstance(data, dict) else data
    if not rows:
        return {"clave_inegi": clave_inegi, "error": "empty"}

    row = rows[0]
    lat = to_num(row.get("lat") or row.get("latitud"))
    lon = to_num(row.get("lng") or row.get("lon") or row.get("longitud") or row.get("long"))
    alt = to_int(row.get("alt") or row.get("altitud"))
    cab = (row.get("nom_cab") or row.get("nom_loc") or row.get("cabecera") or "").strip() or None

    if lat is None or lon is None:
        minx = to_num(row.get("minx"))
        maxx = to_num(row.get("maxx"))
        miny = to_num(row.get("miny"))
        maxy = to_num(row.get("maxy"))
        if None not in (minx, maxx, miny, maxy):
            lat = (miny + maxy) / 2
            lon = (minx + maxx) / 2

    return {
        "clave_inegi": clave_inegi,
        "latitud": lat,
        "longitud": lon,
        "altitud_msnm": alt,
        "cabecera_municipal": cab,
    }


def sb_patch_municipio(record):
    cve = record["clave_inegi"]
    payload = {k: v for k, v in record.items() if k != "clave_inegi"}
    if not payload:
        return False
    url = f"{SB_URL}/rest/v1/municipios?clave_inegi=eq.{cve}"
    r = requests.patch(url, headers={"Content-Type": "application/json", "apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Prefer": "return=minimal"}, data=json.dumps(payload), timeout=30)
    return r.ok


def log_scraper(status, summary, error_msg, started_at):
    try:
        requests.post(f"{SB_URL}/rest/v1/scraper_logs", headers={"Content-Type": "application/json", "apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Prefer": "return=minimal"}, data=json.dumps([{
            "scraper_slug": SCRAPER_SLUG, "workflow_run_id": GH_RUN_ID or None, "status": status,
            "rows_inserted": 0, "rows_updated": summary.get("updated", 0), "rows_skipped": summary.get("skipped", 0),
            "fuente_url": f"{INEGI_V2}/mgem/", "http_status": 200 if status == "ok" else 500,
            "error_msg": (error_msg or "")[:1000] or None, "notes": json.dumps(summary)[:1000],
            "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
        }]), timeout=30)
    except Exception as exc:
        print(f"[geo] no log: {exc}", file=sys.stderr)


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    summary = {"updated": 0, "skipped": 0, "errors": [], "with_geo": 0, "without_geo": 0}
    print("[geo] fetching lista municipios desde Supabase...")
    try:
        municipios = fetch_municipios_list()
    except Exception as exc:
        print(f"[geo] FAIL fetching municipios: {exc}", file=sys.stderr)
        log_scraper("fail", summary, str(exc), started_at)
        sys.exit(1)

    print(f"[geo] {len(municipios)} municipios para enriquecer")
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_geo, m): m for m in municipios}
        done = 0
        first_sample_logged = False
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)
            done += 1
            if not first_sample_logged and not res.get("error"):
                print(f"[geo] sample result: {res}")
                first_sample_logged = True
            if done % 250 == 0 or done == len(municipios):
                print(f"[geo] fetched {done}/{len(municipios)}")

    for res in results:
        if res.get("error"):
            summary["skipped"] += 1
            summary["without_geo"] += 1
            continue
        if res.get("latitud") is None and res.get("longitud") is None:
            summary["skipped"] += 1
            summary["without_geo"] += 1
            continue
        try:
            ok = sb_patch_municipio(res)
            if ok:
                summary["updated"] += 1
                summary["with_geo"] += 1
            else:
                summary["errors"].append(f"patch fail {res['clave_inegi']}")
        except Exception as exc:
            summary["errors"].append(f"{res['clave_inegi']}: {exc}")

    print(f"[geo] DONE updated={summary['updated']} skipped={summary['skipped']} errors={len(summary['errors'])}")
    status = "ok" if summary["updated"] > 0 and len(summary["errors"]) < 50 else "partial"
    log_scraper(status, summary, "; ".join(summary["errors"][:10]) or None, started_at)
    sys.exit(0 if summary["updated"] > 0 else 1)


if __name__ == "__main__":
    main()
