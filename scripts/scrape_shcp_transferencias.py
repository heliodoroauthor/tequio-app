#!/usr/bin/env python3
"""Tequio - Scraper SHCP Transferencias a Entidades Federativas (estatal)."""
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
URL_OVERRIDE = os.environ.get("TRANSFERENCIAS_URL", "").strip()

if not SB_URL or not SB_KEY:
    print("[shcp_tre] Faltan SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
    sys.exit(1)

SCRAPER_SLUG = "shcp_transferencias"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
BASE = "https://repodatos.atdt.gob.mx/api_update/secretaria_hacienda/transferencias_entidades_federativas_2011_actual"


def deaccent(s):
    if not s: return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normkey(s):
    s = deaccent(s or "").lower()
    return "".join(ch for ch in s if ch.isalnum())


def candidate_urls():
    if URL_OVERRIDE:
        return [URL_OVERRIDE]
    now = datetime.utcnow()
    urls = []
    for offset in range(0, 6):
        m = now.month - offset
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        urls.append(f"{BASE}/transferencias_entidades_fed_{m:02d}{y}.csv")
    return urls


def download_csv():
    for url in candidate_urls():
        print(f"[shcp_tre] probando {url}")
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
            if r.status_code == 200 and len(r.content) > 1000:
                print(f"[shcp_tre] OK status={r.status_code} bytes={len(r.content)}")
                for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                    try:
                        text = r.content.decode(enc)
                        return text, url
                    except UnicodeDecodeError:
                        continue
                return r.content.decode("latin-1"), url
            print(f"[shcp_tre] status={r.status_code}")
        except Exception as exc:
            print(f"[shcp_tre] err: {exc}")
    raise RuntimeError("No se pudo descargar el CSV de ninguna URL candidata")


def parse_csv(text):
    sample = text[:5000]
    sep = "," if sample.count(",") > sample.count(";") else ";"
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    all_rows = list(reader)
    if not all_rows: return []
    raw_headers = [(c or "").strip() for c in all_rows[0]]
    nh = [normkey(h) for h in raw_headers]
    print(f"[shcp_tre] sep={sep!r} headers ({len(raw_headers)}): {raw_headers[:15]}")
    print(f"[shcp_tre] normalized: {nh[:15]}")
    rows = []
    for row in all_rows[1:]:
        if not row or not any((c or "").strip() for c in row): continue
        rows.append(dict(zip(nh, [(c or "").strip() for c in row])))
    print(f"[shcp_tre] data rows: {len(rows)}")
    if rows: print(f"[shcp_tre] sample row 0: {rows[0]}")
    return rows


def parse_num(v):
    if v is None or v == "": return None
    s = str(v).replace(",", "").replace("$", "").strip()
    if not s or s.lower() in ("nan", "null", "none"): return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_int(v):
    n = parse_num(v)
    if n is None: return None
    try: return int(n)
    except: return None


NOMBRES_ESTADO = {
    "01": "Aguascalientes", "02": "Baja California", "03": "Baja California Sur",
    "04": "Campeche", "05": "Coahuila", "06": "Colima", "07": "Chiapas",
    "08": "Chihuahua", "09": "Ciudad de Mexico", "10": "Durango",
    "11": "Guanajuato", "12": "Guerrero", "13": "Hidalgo", "14": "Jalisco",
    "15": "Estado de Mexico", "16": "Michoacan", "17": "Morelos", "18": "Nayarit",
    "19": "Nuevo Leon", "20": "Oaxaca", "21": "Puebla", "22": "Queretaro",
    "23": "Quintana Roo", "24": "San Luis Potosi", "25": "Sinaloa", "26": "Sonora",
    "27": "Tabasco", "28": "Tamaulipas", "29": "Tlaxcala", "30": "Veracruz",
    "31": "Yucatan", "32": "Zacatecas",
}


def pick(rec, *keys):
    for k in keys:
        v = rec.get(k)
        if v not in (None, ""): return v
    return None


def map_row(rec):
    anio = parse_int(pick(rec, "anio", "ano", "ciclo", "ejercicio", "year"))
    if not anio: return None
    mes = parse_int(pick(rec, "mes", "periodo", "month"))

    cve_raw = pick(rec, "cveent", "claveent", "idestado", "idente", "claveentidad", "cveagee", "ent")
    if cve_raw:
        cve_str = str(cve_raw).strip().zfill(2)
        if len(cve_str) > 2: cve_str = cve_str[-2:]
    else:
        ent_name = (pick(rec, "entidad", "estado", "nomentidad", "nomestado") or "").strip()
        cve_str = None
        for k, v in NOMBRES_ESTADO.items():
            if deaccent(v).lower() == deaccent(ent_name).lower():
                cve_str = k
                break
        if not cve_str: return None

    nombre_estado = NOMBRES_ESTADO.get(cve_str) or (pick(rec, "entidad", "estado") or "").strip()
    ramo = (pick(rec, "ramo", "idramo", "noramo") or "").strip() or None
    fondo = (pick(rec, "fondo", "idfondo", "descfondo") or "").strip() or None
    concepto = (pick(rec, "concepto", "descripcion", "rubro", "descrubro") or "").strip() or None

    base = {
        "clave_entidad": cve_str,
        "nombre_estado": nombre_estado,
        "anio": anio,
        "mes": mes,
        "ramo": ramo,
        "fondo": fondo,
        "concepto": concepto,
        "fuente": "SHCP Transferencias EF",
        "fuente_url": None,
        "fecha_extraccion": datetime.now(timezone.utc).date().isoformat(),
    }

    out = []
    for col, label in [
        ("aprobado", "aprobado"), ("modificado", "modificado"),
        ("devengado", "devengado"), ("pagado", "pagado"), ("ejercido", "pagado"),
        ("monto", "monto"), ("montoaprobado", "aprobado"),
        ("montomodificado", "modificado"), ("montopagado", "pagado"),
        ("montoejercido", "pagado"),
    ]:
        if col in rec:
            v = parse_num(rec[col])
            if v is not None:
                out.append({**base, "tipo_dato": label, "monto": v})
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
        if total % 5000 == 0 or total == len(rows):
            print(f"[shcp_tre] upserted {total}/{len(rows)}")
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
            "fuente_url": (fuente_url or BASE)[:500],
            "http_status": 200 if status == "ok" else 500,
            "error_msg": (error_msg or "")[:1000] or None,
            "notes": json.dumps(summary)[:1000],
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }]), timeout=30)
    except Exception as exc:
        print(f"[shcp_tre] no log: {exc}", file=sys.stderr)


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    summary = {"inserted": 0, "skipped": 0, "errors": []}
    src_url = ""
    try:
        text, src_url = download_csv()
        rows = parse_csv(text)
        mapped_all = []
        for rec in rows:
            mapped = map_row(rec)
            if mapped:
                for m in mapped:
                    m["fuente_url"] = src_url
                mapped_all.extend(mapped)
            else:
                summary["skipped"] += 1
        print(f"[shcp_tre] mapeados: {len(mapped_all)} (skipped {summary['skipped']})")
        if mapped_all: print(f"[shcp_tre] sample: {mapped_all[0]}")

        seen = set()
        deduped = []
        for m in mapped_all:
            k = (m["clave_entidad"], m["anio"], m.get("mes"), m.get("ramo"), m.get("fondo"), m.get("concepto"), m.get("tipo_dato"))
            if k in seen:
                summary["skipped"] += 1
                continue
            seen.add(k)
            deduped.append(m)

        ins = sb_upsert("transferencias_estatales", deduped,
                        on_conflict="clave_entidad,anio,mes,ramo,fondo,concepto,tipo_dato")
        summary["inserted"] = ins
        status = "ok" if ins > 0 else "fail"
    except Exception as exc:
        import traceback; traceback.print_exc()
        summary["errors"].append(str(exc))
        status = "fail"

    log_scraper(status, summary, "; ".join(summary["errors"]) or None, started_at, src_url)
    print(f"[shcp_tre] DONE status={status} total={summary['inserted']}")
    sys.exit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
