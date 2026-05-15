#!/usr/bin/env python3
"""
Tequio - Scraper SAT Articulo 69 (Deudores incumplidos)

Procesa los 6 listados publicados por SAT en datos.gob.mx:
  - firmes: creditos fiscales firmes
  - exigibles: creditos exigibles
  - no localizados
  - cancelados
  - sentencias (condenatorias por delito fiscal)
  - entes publicos omisos
"""
import csv, io, json, os, sys, unicodedata
from datetime import datetime, timezone
import requests

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
GH_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")

if not SB_URL or not SB_KEY:
    print("[sat69] Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
    sys.exit(1)

SCRAPER_SLUG = "sat_69"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

DATASET = "382fc296-5e90-4880-b0ca-4ed688f591ef"
LISTADOS = [
    ("entes_publicos_omisos", "https://repodatos.atdt.gob.mx/api_update/sat/contribuyentes_incumplidos/SAT_2_EntespublicosydeGobiernoomisos.csv"),
    ("sentencias", f"https://www.datos.gob.mx/dataset/{DATASET}/resource/4e0f0456-484c-4332-a63a-a6f2d3138dd5/download/sat_3_sentencias.csv"),
    ("no_localizados", f"https://www.datos.gob.mx/dataset/{DATASET}/resource/83fa79b9-357b-4ada-b0a4-950c97c50461/download/sat_4_nolocalizados.csv"),
    ("firmes", f"https://www.datos.gob.mx/dataset/{DATASET}/resource/29a7c943-1f77-42b2-95da-d3dc53549c94/download/sat_5_firmes.csv"),
    ("exigibles", f"https://www.datos.gob.mx/dataset/{DATASET}/resource/6301fffe-2388-489a-85e1-5c5ffcda4ce0/download/sat_6_exigibles.csv"),
    ("cancelados", f"https://www.datos.gob.mx/dataset/{DATASET}/resource/1b04d73d-faea-4056-bbab-81df9de5188f/download/sat_7_cancelados.csv"),
]


def deaccent(s):
    if not s: return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normkey(s):
    s = deaccent(s or "").lower()
    return "".join(ch for ch in s if ch.isalnum())


def fetch_csv(url):
    h = {"User-Agent": UA, "Accept": "text/csv,application/octet-stream,*/*"}
    r = requests.get(url, headers=h, timeout=180)
    r.raise_for_status()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = r.content.decode(enc)
            if "rfc" in text.lower()[:5000]:
                return text
        except UnicodeDecodeError:
            continue
    return r.content.decode("latin-1")


def parse_rows(text, supuesto_label):
    sample = text[:3000]
    sep = "," if sample.count(",") > sample.count(";") else ";"
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    all_rows = list(reader)
    if not all_rows:
        return []
    # Header siempre primera fila no vacia
    header_idx = 0
    for i, row in enumerate(all_rows):
        if row and any((c or "").strip().lower() == "rfc" for c in row):
            header_idx = i; break
    raw_headers = [(c or "").strip() for c in all_rows[header_idx]]
    nh = [normkey(h) for h in raw_headers]
    out = []
    for row in all_rows[header_idx + 1:]:
        if not row or not any((c or "").strip() for c in row):
            continue
        rec = dict(zip(nh, [(c or "").strip() for c in row]))
        rfc = rec.get("rfc") or rec.get(normkey("RFC"))
        if not rfc or len(rfc) < 11 or len(rfc) > 13:
            continue
        nombre = rec.get("razonsocial") or rec.get("nombre") or rec.get("nombredelcontribuyente")
        tipo = rec.get("tipopersona") or rec.get("tipo")
        fecha = rec.get("fechasprimerapublicacion") or rec.get("fechaprimerapublicacion") or rec.get("fecha")
        entidad = rec.get("entidadfederativaetq") or rec.get("entidadfederativa") or rec.get("entidad")
        out.append({
            "rfc": rfc.upper(),
            "nombre": nombre,
            "supuesto": supuesto_label,
            "tipo_persona": tipo,
            "fecha_primera_publicacion": fecha or "",
            "entidad_federativa": entidad,
            "fecha_extraccion": datetime.now(timezone.utc).date().isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    return out


def dedup(rows):
    seen, out = set(), []
    for r in rows:
        k = (r["rfc"], r["supuesto"], r["fecha_primera_publicacion"])
        if k in seen: continue
        seen.add(k); out.append(r)
    return out


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
            "fuente_url": "https://www.datos.gob.mx/dataset/contribuyentes_incumplidos",
            "http_status": 200,
            "error_msg": (error_msg or "")[:1000] or None,
            "notes": json.dumps(summary)[:1000],
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }]), timeout=30)
    except Exception as exc:
        print(f"[sat69] no log: {exc}", file=sys.stderr)


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    summary = {"inserted": 0, "by_supuesto": {}, "errors": []}
    hubo_error = False
    for label, url in LISTADOS:
        try:
            print(f"[sat69] fetching {label}: {url}")
            text = fetch_csv(url)
            print(f"[sat69] {label}: {len(text)} bytes")
            rows = parse_rows(text, label)
            print(f"[sat69] {label}: parsed {len(rows)} rows")
            deduped = dedup(rows)
            ins = sb_upsert("sat_69", deduped, "rfc,supuesto,fecha_primera_publicacion")
            summary["inserted"] += ins
            summary["by_supuesto"][label] = ins
            print(f"[sat69] {label}: upserted {ins}")
        except Exception as exc:
            hubo_error = True
            summary["errors"].append(f"{label}: {exc}")
            print(f"[sat69] FAIL {label}: {exc}", file=sys.stderr)
    status = "partial" if hubo_error else "ok"
    log_scraper(status, summary, ("; ".join(summary["errors"]) or None), started_at)
    print(f"[sat69] DONE status={status} total={summary['inserted']} by={summary['by_supuesto']}")
    sys.exit(1 if hubo_error else 0)


if __name__ == "__main__":
    main()
