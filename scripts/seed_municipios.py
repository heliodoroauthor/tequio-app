#!/usr/bin/env python3
"""
Tequio - Seed de Municipios INEGI

Pobla la tabla `municipios` con los ~2,475 municipios de Mexico.

Fuente oficial: INEGI wscatgeo (sin token requerido)
  - https://gaia.inegi.org.mx/wscatgeo/mgem/{cve_ent}  -> municipios por estado con poblacion Censo 2020

Response schema:
  {"datos":[{"cvegeo":"01001","cve_agee":"01","cve_agem":"001","nom_agem":"Aguascalientes",
             "cve_cab":"0001","nom_cab":"Aguascalientes",
             "pob":"948990","pob_fem":"486917","pob_mas":"462073","viv":"266942"}, ...]}

Lat/lng/altitud no vienen en este endpoint; se agregan en M2 con scrape_inegi_geo_municipios.py

Env vars requeridas:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
Opcional:
  GITHUB_RUN_ID
"""
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
import requests

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
GH_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")

if not SB_URL or not SB_KEY:
    print("[seed_municipios] Faltan SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
    sys.exit(1)

SCRAPER_SLUG = "seed_municipios"
UA = "Tequio.app/1.0 (civic-data; +https://tequio.app)"
INEGI_BASE = "https://gaia.inegi.org.mx/wscatgeo"  # v1 (estable, con Censo 2020)


def deaccent(s):
    if not s:
        return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def slugify(texto):
    """'Ciudad de Mexico' -> 'ciudad-de-mexico'"""
    if not texto:
        return ""
    s = deaccent(texto)
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", s)
    s = s.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


# Mapeo oficial INEGI Cve_Ent -> nombre canonico
NOMBRES_ESTADO = {
    "01": "Aguascalientes",
    "02": "Baja California",
    "03": "Baja California Sur",
    "04": "Campeche",
    "05": "Coahuila",
    "06": "Colima",
    "07": "Chiapas",
    "08": "Chihuahua",
    "09": "Ciudad de Mexico",
    "10": "Durango",
    "11": "Guanajuato",
    "12": "Guerrero",
    "13": "Hidalgo",
    "14": "Jalisco",
    "15": "Estado de Mexico",
    "16": "Michoacan",
    "17": "Morelos",
    "18": "Nayarit",
    "19": "Nuevo Leon",
    "20": "Oaxaca",
    "21": "Puebla",
    "22": "Queretaro",
    "23": "Quintana Roo",
    "24": "San Luis Potosi",
    "25": "Sinaloa",
    "26": "Sonora",
    "27": "Tabasco",
    "28": "Tamaulipas",
    "29": "Tlaxcala",
    "30": "Veracruz",
    "31": "Yucatan",
    "32": "Zacatecas",
}


def http_get_json(url, retries=4, backoff=2):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    for i in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.json()
            print(f"[seed_municipios] HTTP {r.status_code} en {url} (intento {i+1})", file=sys.stderr)
        except Exception as exc:
            print(f"[seed_municipios] err {url}: {exc} (intento {i+1})", file=sys.stderr)
        time.sleep(backoff * (i + 1))
    return None


def fetch_municipios_estado(cve_ent):
    url = f"{INEGI_BASE}/mgem/{cve_ent}"
    data = http_get_json(url)
    if not data:
        return []
    if isinstance(data, dict):
        rows = data.get("datos") or data.get("data") or data.get("registros") or []
    else:
        rows = data
    return rows or []


def to_num(v):
    if v is None or v == "":
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


def map_municipio(row, cve_ent, nombre_estado, estado_slug):
    """Mapea row de INEGI wscatgeo a esquema municipios."""
    cve_mun = (row.get("cve_agem") or row.get("cve_mun") or row.get("cveMun") or "").strip()
    if not cve_mun:
        return None
    cve_mun_3 = cve_mun.zfill(3)
    cve_ent_2 = cve_ent.zfill(2)
    nombre = (row.get("nom_agem") or row.get("nom_mun") or row.get("nombre") or "").strip()
    if not nombre:
        return None

    lat = to_num(row.get("latitud") or row.get("lat"))
    lon = to_num(row.get("longitud") or row.get("lon") or row.get("long"))
    alt = to_int(row.get("altitud") or row.get("alt"))
    cab = (row.get("nom_cab") or row.get("cabecera") or "").strip() or None

    return {
        "estado_slug": estado_slug,
        "municipio_slug": slugify(nombre),
        "nombre": nombre,
        "nombre_estado": nombre_estado,
        "clave_inegi": f"{cve_ent_2}{cve_mun_3}",
        "clave_entidad": cve_ent_2,
        "clave_municipio": cve_mun_3,
        "poblacion_total": to_int(row.get("pob") or row.get("poblacion_total")),
        "poblacion_hombres": to_int(row.get("pob_mas") or row.get("poblacion_hombres")),
        "poblacion_mujeres": to_int(row.get("pob_fem") or row.get("poblacion_mujeres")),
        "viviendas_totales": to_int(row.get("viv") or row.get("viviendas_totales")),
        "latitud": lat,
        "longitud": lon,
        "altitud_msnm": alt,
        "cabecera_municipal": cab,
        "fuente": "INEGI wscatgeo + Censo 2020",
        "fuente_url": f"{INEGI_BASE}/mgem/{cve_ent_2}",
        "fecha_extraccion": datetime.now(timezone.utc).date().isoformat(),
    }


def sb_upsert(table, rows, on_conflict="clave_inegi", batch_size=500):
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        url = f"{SB_URL}/rest/v1/{table}?on_conflict={on_conflict}"
        r = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "apikey": SB_KEY,
                "Authorization": f"Bearer {SB_KEY}",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            data=json.dumps(batch),
            timeout=60,
        )
        if not r.ok:
            raise RuntimeError(f"upsert batch {i//batch_size} status={r.status_code}: {r.text[:300]}")
        total += len(batch)
        print(f"[seed_municipios] upserted {total}/{len(rows)}")
    return total


def log_scraper(status, summary, error_msg, started_at, fuente_url):
    try:
        requests.post(
            f"{SB_URL}/rest/v1/scraper_logs",
            headers={
                "Content-Type": "application/json",
                "apikey": SB_KEY,
                "Authorization": f"Bearer {SB_KEY}",
                "Prefer": "return=minimal",
            },
            data=json.dumps([{
                "scraper_slug": SCRAPER_SLUG,
                "workflow_run_id": GH_RUN_ID or None,
                "status": status,
                "rows_inserted": summary.get("inserted", 0),
                "rows_updated": 0,
                "rows_skipped": summary.get("skipped", 0),
                "fuente_url": fuente_url,
                "http_status": 200 if status == "ok" else 500,
                "error_msg": (error_msg or "")[:1000] or None,
                "notes": json.dumps(summary)[:1000],
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }]),
            timeout=30,
        )
    except Exception as exc:
        print(f"[seed_municipios] no log: {exc}", file=sys.stderr)


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    summary = {"inserted": 0, "skipped": 0, "by_estado": {}, "errors": []}
    all_rows = []
    last_url = INEGI_BASE

    for cve_ent, nombre_estado in NOMBRES_ESTADO.items():
        estado_slug = slugify(nombre_estado)
        try:
            municipios_raw = fetch_municipios_estado(cve_ent)
            if not municipios_raw:
                summary["errors"].append(f"{cve_ent} {nombre_estado}: sin datos")
                print(f"[seed_municipios] WARN sin datos para {cve_ent} {nombre_estado}", file=sys.stderr)
                continue
            mapped = []
            for row in municipios_raw:
                m = map_municipio(row, cve_ent, nombre_estado, estado_slug)
                if m:
                    mapped.append(m)
                else:
                    summary["skipped"] += 1
            summary["by_estado"][nombre_estado] = len(mapped)
            all_rows.extend(mapped)
            print(f"[seed_municipios] {cve_ent} {nombre_estado}: {len(mapped)} municipios")
            last_url = f"{INEGI_BASE}/mgem/{cve_ent}"
            time.sleep(0.2)
        except Exception as exc:
            summary["errors"].append(f"{cve_ent} {nombre_estado}: {exc}")
            print(f"[seed_municipios] FAIL {cve_ent}: {exc}", file=sys.stderr)

    print(f"[seed_municipios] total mapeados: {len(all_rows)}")

    seen = set()
    deduped = []
    for r in all_rows:
        k = r["clave_inegi"]
        if k in seen:
            summary["skipped"] += 1
            continue
        seen.add(k)
        deduped.append(r)

    print(f"[seed_municipios] tras dedup: {len(deduped)}")

    if deduped:
        try:
            ins = sb_upsert("municipios", deduped, on_conflict="clave_inegi")
            summary["inserted"] = ins
        except Exception as exc:
            summary["errors"].append(f"upsert: {exc}")
            print(f"[seed_municipios] FAIL upsert: {exc}", file=sys.stderr)

    hubo_error = bool(summary["errors"])
    status = "partial" if hubo_error and summary["inserted"] > 0 else ("fail" if hubo_error else "ok")
    log_scraper(status, summary, "; ".join(summary["errors"]) or None, started_at, last_url)
    print(f"[seed_municipios] DONE status={status} total={summary['inserted']} errors={len(summary['errors'])}")
    sys.exit(0 if status in ("ok", "partial") and summary["inserted"] > 0 else 1)


if __name__ == "__main__":
    main()
