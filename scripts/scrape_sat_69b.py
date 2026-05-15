#!/usr/bin/env python3
"""Tequio - Scraper SAT 69-B v4 (dedup batch)"""
import csv, io, json, os, sys, unicodedata
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


def deaccent(s):
    if not s: return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normkey(s):
    s = deaccent(s or "").lower()
    return "".join(ch for ch in s if ch.isalnum())


def fetch_csv():
    h = {"User-Agent": UA, "Accept": "text/csv,application/octet-stream,*/*"}
    r = requests.get(CSV_URL, headers=h, timeout=120)
    r.raise_for_status()
    print(f"[sat69b] HTTP {r.status_code} bytes={len(r.content)}")
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = r.content.decode(enc)
            if "RFC" in text.upper()[:200000]:
                print(f"[sat69b] decoded as {enc}")
                return text
        except UnicodeDecodeError:
            continue
    return r.content.decode("latin-1")


def parse_csv(text):
    sep = "," if text[:5000].count(",") > text[:5000].count(";") else ";"
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    all_rows = list(reader)
    header_idx = None
    for i, row in enumerate(all_rows):
        cells = [(c or "").strip() for c in row]
        if any(c.upper() == "RFC" for c in cells):
            header_idx = i; break
    if header_idx is None:
        raise RuntimeError("No se encontro fila de headers con RFC")
    raw_headers = [(c or "").strip() for c in all_rows[header_idx]]
    norm_headers = [normkey(h) for h in raw_headers]
    rows = []
    for row in all_rows[header_idx + 1:]:
        if not row or not any((c or "").strip() for c in row):
            continue
        rec = dict(zip(norm_headers, [(c or "").strip() for c in row]))
        rows.append(rec)
    print(f"[sat69b] data rows: {len(rows)}")
    return rows


def find_first(rec, *cands):
    for c in cands:
        v = rec.get(normkey(c))
        if v: return v
    return None


def derive_situacion(rec):
    if find_first(rec, "publicacion pagina sat sentencia favorable"): return "Sentencia favorable"
    if find_first(rec, "publicacion pagina sat definitivos"): return "Definitivo"
    if find_first(rec, "publicacion pagina sat desvirtuados"): return "Desvirtuado"
    if find_first(rec, "publicacion pagina sat presuntos"): return "Presunto"
    return "Sin clasificar"


def map_row(rec):
    rfc = find_first(rec, "rfc")
    if not rfc or len(rfc) < 11 or len(rfc) > 13: return None
    situ = derive_situacion(rec)
    fk = {"Sentencia favorable":"publicacion pagina sat sentencia favorable",
          "Definitivo":"publicacion pagina sat definitivos",
          "Desvirtuado":"publicacion pagina sat desvirtuados",
          "Presunto":"publicacion pagina sat presuntos"}.get(situ)
    fdof_k = {"Sentencia favorable":"publicacion dof sentencia favorable",
              "Definitivo":"publicacion dof definitivos",
              "Desvirtuado":"publicacion dof desvirtuados",
              "Presunto":"publicacion dof presuntos"}.get(situ)
    return {
        "rfc": rfc.upper(),
        "nombre": find_first(rec, "nombre del contribuyente"),
        "situacion": situ,
        "fecha_publicacion_sat": (find_first(rec, fk) if fk else None) or "",
        "fecha_dof": find_first(rec, fdof_k) if fdof_k else None,
        "fecha_extraccion": datetime.now(timezone.utc).date().isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def dedup(rows):
    seen, out = set(), []
    for r in rows:
        k = (r["rfc"], r["situacion"], r["fecha_publicacion_sat"])
        if k in seen: continue
        seen.add(k); out.append(r)
    return out


def sb_upsert(table, rows, on_conflict, batch_size=500):
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
            raise RuntimeError(f"upsert batch {i//batch_size} status={r.status_code}: {r.text[:300]}")
        total += len(batch)
    return total


def log_scraper(status, summary, error_msg, started_at):
    try:
        requests.post(f"{SB_URL}/rest/v1/scraper_logs", headers={
            "Content-Type": "application/json", "apikey": SB_KEY,
            "Authorization": f"Bearer {SB_KEY}", "Prefer": "return=minimal",
        }, data=json.dumps([{
            "scraper_slug": SCRAPER_SLUG, "workflow_run_id": GH_RUN_ID or None,
            "status": status, "rows_inserted": summary.get("inserted", 0),
            "rows_updated": 0, "rows_skipped": summary.get("skipped", 0),
            "fuente_url": CSV_URL, "http_status": 200,
            "error_msg": (error_msg or "")[:1000] or None,
            "notes": json.dumps(summary)[:1000],
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }]), timeout=30)
    except Exception as exc:
        print(f"[sat69b] no log: {exc}", file=sys.stderr)


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        text = fetch_csv()
        rows = parse_csv(text)
        mapped = [m for m in (map_row(r) for r in rows) if m]
        deduped = dedup(mapped)
        skipped = len(mapped) - len(deduped)
        from collections import Counter
        ct = Counter(m["situacion"] for m in deduped)
        print(f"[sat69b] mapped={len(mapped)} deduped={len(deduped)} skipped={skipped}")
        print(f"[sat69b] por situacion: {dict(ct)}")
        ins = sb_upsert("sat_69b", deduped, "rfc,situacion,fecha_publicacion_sat")
        print(f"[sat69b] upserted: {ins}")
        log_scraper("ok", {"inserted": ins, "skipped": skipped, "by_situacion": dict(ct)}, None, started_at)
        print(f"[sat69b] DONE total={ins}")
    except Exception as exc:
        import traceback; traceback.print_exc()
        log_scraper("fail", {"inserted": 0}, str(exc), started_at)
        sys.exit(1)


if __name__ == "__main__":
    main()
