#!/usr/bin/env python3
"""Tequio - Scraper INAFED Presidentes Municipales."""
import csv
import io
import json
import os
import sys
import unicodedata
from datetime import datetime, timezone
import requests

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
GH_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
INAFED_URL_OVERRIDE = os.environ.get("INAFED_CSV_URL", "").strip()

if not SB_URL or not SB_KEY:
    print("[inafed] Faltan SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
    sys.exit(1)

SCRAPER_SLUG = "inafed_presidentes"
UA = "Tequio.app/1.0 (civic-data; +https://tequio.app)"
INAFED_CSV = "https://www.datos.gob.mx/dataset/a67f0a5b-5933-4d7f-8d51-53cb874ffd29/resource/22dec87e-ec40-45e6-9bf7-3065ad1ad7b5/download/presidentes_municipales.csv"


def deaccent(s):
    if not s: return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normkey(s):
    s = deaccent(s or "").lower()
    return "".join(ch for ch in s if ch.isalnum())


def download_csv():
    url = INAFED_URL_OVERRIDE or INAFED_CSV
    print(f"[inafed] descargando {url}")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
    r.raise_for_status()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = r.content.decode(enc)
            if "estado" in text[:500].lower():
                print(f"[inafed] decoded {enc}, bytes={len(r.content)}")
                return text, url
        except UnicodeDecodeError:
            continue
    return r.content.decode("latin-1"), url


def parse_csv(text):
    sample = text[:3000]
    sep = "," if sample.count(",") > sample.count(";") else ";"
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    all_rows = list(reader)
    if not all_rows: return []
    raw_headers = [(c or "").strip() for c in all_rows[0]]
    nh = [normkey(h) for h in raw_headers]
    print(f"[inafed] sep={sep!r} headers ({len(raw_headers)}): {raw_headers[:10]}")
    rows = []
    for row in all_rows[1:]:
        if not row or not any((c or "").strip() for c in row): continue
        rows.append(dict(zip(nh, [(c or "").strip() for c in row])))
    print(f"[inafed] data rows: {len(rows)}")
    return rows


def parse_date(s):
    if not s: return None
    s = str(s).strip()
    if not s or s.lower() in ("nan", "none", "null"): return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def map_row(rec):
    cve_raw = rec.get("cveinegi") or rec.get("cve_inegi") or ""
    if not cve_raw: return None
    cve_str = str(cve_raw).strip().zfill(5)
    if len(cve_str) > 5: cve_str = cve_str[-5:]
    cve_ent = cve_str[:2]
    cve_mun = cve_str[2:]

    nombre = (rec.get("nombre") or "").strip().title() or None
    ap_pat = (rec.get("appaterno") or "").strip().title() or None
    ap_mat = (rec.get("apmaterno") or "").strip().title() or None
    sexo = (rec.get("sexo") or "").strip().upper() or None
    if sexo not in ("H", "M"): sexo = None

    partido = (rec.get("partido") or "").strip() or None
    coalicion = (rec.get("descripcion") or "").strip() or None
    integrantes = (rec.get("integrantes") or "").strip() or None

    pdo_ini = parse_date(rec.get("fechapdogobini") or rec.get("pdogobini"))
    pdo_fin = parse_date(rec.get("fechapdogobfin") or rec.get("pdogobfin"))

    period_label = f"{pdo_ini[:4]}-{pdo_fin[:4]}" if (pdo_ini and pdo_fin) else None

    return {
        "clave_inegi": cve_str,
        "clave_entidad": cve_ent,
        "clave_municipio": cve_mun,
        "nombre_estado": (rec.get("estado") or rec.get("entidad") or "").strip() or None,
        "nombre_municipio": (rec.get("municipio") or "").strip().title() or None,
        "nombre": nombre,
        "apellido_paterno": ap_pat,
        "apellido_materno": ap_mat,
        "sexo": sexo,
        "partido": coalicion or partido,
        "coalicion": coalicion if (coalicion and integrantes) else None,
        "integrantes_coalicion": integrantes,
        "periodo_inicio": pdo_ini,
        "periodo_fin": pdo_fin,
        "periodo_label": period_label,
        "direccion": (rec.get("direccion") or "").strip() or None,
        "pagina_web": (rec.get("pagweb") or "").strip() or None,
        "lada": (rec.get("cvelada") or "").strip() or None,
        "telefono": (rec.get("telefono") or "").strip() or None,
        "fuente": "INAFED via datos.gob.mx",
        "fuente_url": INAFED_CSV,
        "fecha_extraccion": datetime.now(timezone.utc).date().isoformat(),
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
        }, data=json.dumps(batch), timeout=60)
        if not r.ok:
            raise RuntimeError(f"upsert batch {i//batch_size} status={r.status_code}: {r.text[:300]}")
        total += len(batch)
        print(f"[inafed] upserted {total}/{len(rows)}")
    return total


def log_scraper(status, summary, error_msg, started_at, fuente_url):
    try:
        requests.post(f"{SB_URL}/rest/v1/scraper_logs", headers={
            "Content-Type": "application/json", "apikey": SB_KEY,
            "Authorization": f"Bearer {SB_KEY}", "Prefer": "return=minimal",
        }, data=json.dumps([{
            "scraper_slug": SCRAPER_SLUG, "workflow_run_id": GH_RUN_ID or None,
            "status": status, "rows_inserted": summary.get("inserted", 0),
            "rows_updated": 0, "rows_skipped": summary.get("skipped", 0),
            "fuente_url": (fuente_url or INAFED_CSV)[:500],
            "http_status": 200 if status == "ok" else 500,
            "error_msg": (error_msg or "")[:1000] or None,
            "notes": json.dumps(summary)[:1000],
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }]), timeout=30)
    except Exception as exc:
        print(f"[inafed] no log: {exc}", file=sys.stderr)


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    summary = {"inserted": 0, "skipped": 0, "errors": [], "by_estado": {}}
    src_url = INAFED_CSV
    try:
        text, src_url = download_csv()
        rows = parse_csv(text)
        mapped = []
        for rec in rows:
            m = map_row(rec)
            if m and m.get("clave_inegi") and m.get("periodo_inicio"):
                mapped.append(m)
            else:
                summary["skipped"] += 1
        print(f"[inafed] mapeados: {len(mapped)} de {len(rows)}")
        if mapped: print(f"[inafed] sample: {mapped[0]}")

        seen = set()
        deduped = []
        for m in mapped:
            k = (m["clave_inegi"], m["periodo_inicio"])
            if k in seen:
                summary["skipped"] += 1
                continue
            seen.add(k)
            deduped.append(m)

        ins = sb_upsert("presidentes_municipales", deduped, on_conflict="clave_inegi,periodo_inicio")
        summary["inserted"] = ins
        for m in deduped:
            est = m.get("nombre_estado") or "?"
            summary["by_estado"][est] = summary["by_estado"].get(est, 0) + 1
        status = "ok" if ins > 0 else "fail"
    except Exception as exc:
        import traceback; traceback.print_exc()
        summary["errors"].append(str(exc))
        status = "fail"

    log_scraper(status, summary, "; ".join(summary["errors"]) or None, started_at, src_url)
    print(f"[inafed] DONE status={status} total={summary['inserted']} estados={len(summary['by_estado'])}")
    sys.exit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
