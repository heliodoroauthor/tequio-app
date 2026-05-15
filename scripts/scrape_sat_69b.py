#!/usr/bin/env python3
"""
Tequio - Scraper SAT 69-B (Listas Negras de Contribuyentes)

El SAT publica los RFCs de contribuyentes con operaciones presuntamente
inexistentes (factureras / EFOS) en un CSV oficial actualizado mensualmente.

Fuente: http://omawww.sat.gob.mx/cifras_sat/Documents/Listado_Completo_69-B.csv
"""
import csv, io, json, os, sys
from datetime import datetime, timezone
import requests

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
GH_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")

if not SB_URL or not SB_KEY:
    print("[sat69b] Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
    sys.exit(1)

SCRAPER_SLUG = "sat_69b"
CSV_URL = "http://omawww.sat.gob.mx/cifras_sat/Documents/Listado_Completo_69-B.csv"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def fetch_csv():
    headers = {"User-Agent": UA, "Accept": "text/csv,application/octet-stream,*/*"}
    r = requests.get(CSV_URL, headers=headers, timeout=120)
    r.raise_for_status()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = r.content.decode(enc)
            if "RFC" in text[:500].upper() or "rfc" in text[:500]:
                print(f"[sat69b] decoded as {enc}, bytes={len(r.content)}")
                return text
        except UnicodeDecodeError:
            continue
    return r.content.decode("latin-1")


def parse_csv(text):
    sample = text[:2000]
    sep = "," if sample.count(",") > sample.count(";") else ";"
    print(f"[sat69b] separator={sep!r}")
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    headers = None
    rows = []
    for row in reader:
        if not row or not any(c.strip() for c in row):
            continue
        if headers is None:
            headers = [h.strip().lower() for h in row]
            print(f"[sat69b] headers: {headers}")
            continue
        if len(row) < 2:
            continue
        rows.append(dict(zip(headers, row)))
    print(f"[sat69b] parsed rows: {len(rows)}")
    return rows, headers


def map_row(rec):
    def get(*keys):
        for k in keys:
            kn = k.replace(" ", "").replace("_", "").lower()
            for hkey in rec:
                if hkey.replace(" ", "").replace("_", "").lower() == kn:
                    v = (rec[hkey] or "").strip()
                    if v: return v
        return None
    rfc = get("RFC", "rfc")
    if not rfc: return None
    return {
        "rfc": rfc,
        "nombre": get("Nombre del Contribuyente", "nombre", "razon_social", "razonsocial"),
        "situacion": get("Situacion del Contribuyente", "situacion", "estatus"),
        "numero_publicacion": get("No. y fecha de oficio global de presuncion", "numero", "oficio"),
        "fecha_publicacion_sat": get("Publicacion pagina SAT presuntos", "fecha_publicacion", "fecha"),
        "fecha_dof": get("Publicacion DOF presuntos", "fecha_dof", "dof"),
        "fecha_extraccion": datetime.now(timezone.utc).date().isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def sb_upsert(table, rows, on_conflict, batch_size=500):
    if not rows: return 0
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        url = f"{SB_URL}/rest/v1/{table}?on_conflict={on_conflict}"
        r = requests.post(url, headers={
            "Content-Type": "application/json", "apikey": SB_KEY,
            "Authorization": f"Bearer {SB_KEY}",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }, data=json.dumps(batch), timeout=120)
        if not r.ok:
            raise RuntimeError(f"upsert batch status={r.status_code}: {r.text[:300]}")
        total += len(batch)
        if i % 5000 == 0 and i > 0:
            print(f"[sat69b] upserted {total}/{len(rows)}")
    return total


def log_scraper(status, summary, error_msg, started_at):
    try:
        requests.post(f"{SB_URL}/rest/v1/scraper_logs", headers={
            "Content-Type": "application/json", "apikey": SB_KEY,
            "Authorization": f"Bearer {SB_KEY}", "Prefer": "return=minimal",
        }, data=json.dumps([{
            "scraper_slug": SCRAPER_SLUG, "workflow_run_id": GH_RUN_ID or None,
            "status": status, "rows_inserted": summary.get("inserted", 0),
            "rows_updated": 0, "rows_skipped": 0,
            "fuente_url": CSV_URL, "http_status": 200,
            "error_msg": (error_msg or "")[:1000] or None,
            "notes": json.dumps(summary)[:1000],
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }]), timeout=30)
    except Exception as exc:
        print(f"[sat69b] no se pudo loguear: {exc}", file=sys.stderr)


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        text = fetch_csv()
        rows, _ = parse_csv(text)
        mapped = [m for m in (map_row(r) for r in rows) if m]
        print(f"[sat69b] mapped: {len(mapped)} de {len(rows)} con RFC")
        ins = sb_upsert("sat_69b", mapped, "rfc,situacion,fecha_publicacion_sat")
        print(f"[sat69b] upserted: {ins}")
        log_scraper("ok", {"inserted": ins}, None, started_at)
        print(f"[sat69b] DONE status=ok total={ins}")
    except Exception as exc:
        import traceback; traceback.print_exc()
        log_scraper("fail", {"inserted": 0}, str(exc), started_at)
        sys.exit(1)


if __name__ == "__main__":
    main()
