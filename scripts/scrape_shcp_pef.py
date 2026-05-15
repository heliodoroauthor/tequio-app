#!/usr/bin/env python3
"""
Tequio - Scraper SHCP PEF (Presupuesto de Egresos de la Federacion)

Descarga el PEF de SHCP/Transparencia Presupuestaria via datos.gob.mx.
URL pattern: https://repodatos.atdt.gob.mx/.../PEF_{anio}.csv

Notas:
- El CSV es enorme (cientos de miles de filas con todos los programas presupuestarios)
- Streaming + batch upserts para evitar OOM
"""
import csv, io, json, os, sys, unicodedata
from datetime import datetime, timezone
import requests

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
GH_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
ANIOS = (os.environ.get("PEF_ANIOS") or "2026").split(",")

if not SB_URL or not SB_KEY:
    print("[shcp] Faltan SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
    sys.exit(1)

SCRAPER_SLUG = "shcp_pef"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
BASE_URL = "https://repodatos.atdt.gob.mx/api_update/secretaria_hacienda/presupuesto_egresos_federacion_pef/PEF_{anio}.csv"


def deaccent(s):
    if not s: return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normkey(s):
    s = deaccent(s or "").lower()
    return "".join(ch for ch in s if ch.isalnum())


def fetch_csv(anio):
    url = BASE_URL.format(anio=anio)
    print(f"[shcp] fetching {url}")
    h = {"User-Agent": UA, "Accept": "text/csv,application/octet-stream,*/*"}
    r = requests.get(url, headers=h, timeout=300)
    r.raise_for_status()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = r.content.decode(enc)
            if "CICLO" in text.upper()[:5000] or "ramo" in text.lower()[:5000]:
                print(f"[shcp] {anio} decoded as {enc}, bytes={len(r.content)}")
                return text, url
        except UnicodeDecodeError:
            continue
    return r.content.decode("latin-1"), url


def parse_csv(text):
    sample = text[:5000]
    sep = "," if sample.count(",") > sample.count(";") else ";"
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    all_rows = list(reader)
    if not all_rows: return []
    header_idx = 0
    raw_headers = [(c or "").strip() for c in all_rows[header_idx]]
    nh = [normkey(h) for h in raw_headers]
    print(f"[shcp] sep={sep!r} raw_headers: {raw_headers}")
    print(f"[shcp] normalized_headers: {nh}")
    rows = []
    for row in all_rows[header_idx + 1:]:
        if not row or not any((c or "").strip() for c in row):
            continue
        rows.append(dict(zip(nh, [(c or "").strip() for c in row])))
    print(f"[shcp] data rows: {len(rows)}")
    if rows:
        print(f"[shcp] sample row 0: {rows[0]}")
    return rows


def to_num(s):
    if not s: return None
    s = s.replace(",", "").replace("$", "").strip()
    try: return float(s)
    except: return None


def pick(rec, *keys):
    """Get first non-empty value among possible normalized keys."""
    for k in keys:
        v = rec.get(k)
        if v not in (None, ""):
            return v
    return None


def map_row(rec, anio):
    # SHCP PEF CSV headers (normalized via normkey, sin underscores/acentos):
    # ciclo, idramo, descramo, idur, descur, gpofuncional, descgpofuncional,
    # idfuncion, descfuncion, idsubfuncion, descsubfuncion, idai, descai,
    # idmodalidad, idpp, descpp, idobjetogasto/idog, descog,
    # idtipogasto/idtg, idfuentefinanciamiento/idff, identidadfederativa/idef,
    # montoaprobado / monto
    monto = to_num(pick(rec, "montoaprobado", "monto", "montototal", "montotot", "montoautorizado"))
    return {
        "ciclo": int(anio),
        "ramo": pick(rec, "idramo", "ramo"),
        "desc_ramo": pick(rec, "descramo", "descripcionramo"),
        "unidad_responsable": pick(rec, "idur", "ur", "unidadresponsable"),
        "desc_ur": pick(rec, "descur", "descripcionur"),
        "finalidad": pick(rec, "idfinalidad", "finalidad"),
        "funcion": pick(rec, "idfuncion", "funcion"),
        "desc_funcion": pick(rec, "descfuncion"),
        "subfuncion": pick(rec, "idsubfuncion", "subfuncion"),
        "desc_subfuncion": pick(rec, "descsubfuncion"),
        "actividad_institucional": pick(rec, "idai", "ai", "actividadinstitucional"),
        "desc_ai": pick(rec, "descai"),
        "modalidad": pick(rec, "idmodalidad", "modalidad"),
        "programa_presupuestario": pick(rec, "idpp", "pp", "programapresupuestario"),
        "desc_pp": pick(rec, "descpp"),
        "objeto_gasto": pick(rec, "idog", "idobjetogasto", "og", "objetogasto"),
        "desc_og": pick(rec, "descog"),
        "tipo_gasto": pick(rec, "idtg", "idtipogasto", "tg", "tipogasto"),
        "fuente_financiamiento": pick(rec, "idff", "idfuentefinanciamiento", "ff", "fuentefinanciamiento"),
        "entidad_federativa": pick(rec, "idef", "identidadfederativa", "ef", "entidadfederativa"),
        "monto": monto,
        "fecha_extraccion": datetime.now(timezone.utc).date().isoformat(),
    }


def sb_insert(table, rows, batch_size=500):
    if not rows: return 0
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        url = f"{SB_URL}/rest/v1/{table}"
        r = requests.post(url, headers={
            "Content-Type": "application/json", "apikey": SB_KEY,
            "Authorization": f"Bearer {SB_KEY}", "Prefer": "return=minimal",
        }, data=json.dumps(batch), timeout=120)
        if not r.ok:
            raise RuntimeError(f"insert batch {i//batch_size} status={r.status_code}: {r.text[:300]}")
        total += len(batch)
        if total % 10000 == 0:
            print(f"[shcp] inserted {total}/{len(rows)}")
    return total


def delete_ciclo(table, ciclo):
    url = f"{SB_URL}/rest/v1/{table}?ciclo=eq.{ciclo}"
    r = requests.delete(url, headers={
        "apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
        "Prefer": "return=minimal",
    }, timeout=60)
    print(f"[shcp] delete ciclo={ciclo} status={r.status_code}")


def log_scraper(status, summary, error_msg, started_at, fuente_url):
    try:
        requests.post(f"{SB_URL}/rest/v1/scraper_logs", headers={
            "Content-Type": "application/json", "apikey": SB_KEY,
            "Authorization": f"Bearer {SB_KEY}", "Prefer": "return=minimal",
        }, data=json.dumps([{
            "scraper_slug": SCRAPER_SLUG, "workflow_run_id": GH_RUN_ID or None,
            "status": status, "rows_inserted": summary.get("inserted", 0),
            "rows_updated": 0, "rows_skipped": summary.get("skipped", 0),
            "fuente_url": fuente_url, "http_status": 200,
            "error_msg": (error_msg or "")[:1000] or None,
            "notes": json.dumps(summary)[:1000],
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }]), timeout=30)
    except Exception as exc:
        print(f"[shcp] no log: {exc}", file=sys.stderr)


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    summary = {"inserted": 0, "by_anio": {}, "errors": []}
    last_url = "https://repodatos.atdt.gob.mx/api_update/secretaria_hacienda/presupuesto_egresos_federacion_pef/"
    hubo_error = False
    for anio in ANIOS:
        anio = anio.strip()
        try:
            text, url = fetch_csv(anio)
            last_url = url
            rows = parse_csv(text)
            mapped = [map_row(r, anio) for r in rows]
            print(f"[shcp] {anio} mapped: {len(mapped)}")
            delete_ciclo("shcp_pef", int(anio))
            ins = sb_insert("shcp_pef", mapped)
            summary["inserted"] += ins
            summary["by_anio"][anio] = ins
            print(f"[shcp] {anio} inserted: {ins}")
        except Exception as exc:
            hubo_error = True
            summary["errors"].append(f"{anio}: {exc}")
            print(f"[shcp] FAIL {anio}: {exc}", file=sys.stderr)
    status = "partial" if hubo_error else "ok"
    log_scraper(status, summary, ("; ".join(summary["errors"]) or None), started_at, last_url)
    print(f"[shcp] DONE status={status} total={summary['inserted']} by_anio={summary['by_anio']}")
    sys.exit(1 if hubo_error else 0)


if __name__ == "__main__":
    main()
