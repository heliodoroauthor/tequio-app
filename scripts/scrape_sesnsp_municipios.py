#!/usr/bin/env python3
"""
Tequio - Scraper SESNSP Incidencia Delictiva Municipal (IDEFC_NM).

SESNSP publica mensualmente un CSV con incidencia delictiva por municipio:
  https://www.gob.mx/sesnsp/acciones-y-programas/datos-abiertos-de-incidencia-delictiva

El CSV tiene formato wide con columnas: Ano, Clave_Ent, Entidad, Cve. Municipio,
Municipio, Bien juridico afectado, Tipo de delito, Subtipo de delito, Modalidad,
Enero, Febrero, ..., Diciembre

Strategy:
1. Scrape la pagina oficial gob.mx para encontrar el ultimo link IDEFC_NM_*.csv
2. Descargar el CSV (~80MB)
3. Unpivot meses a long format
4. Filtrar por anios si SESNSP_ANIOS esta definido
5. Upsert en delitos_municipios

Env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
Opcional:
  GITHUB_RUN_ID
  SESNSP_ANIOS - lista comma-separated, default = todos
  SESNSP_CSV_URL - URL directa al CSV (override del scraping)
"""
import csv
import io
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
import requests

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
GH_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
SESNSP_ANIOS = os.environ.get("SESNSP_ANIOS", "").strip()
SESNSP_CSV_URL_OVERRIDE = os.environ.get("SESNSP_CSV_URL", "").strip()

if not SB_URL or not SB_KEY:
    print("[sesnsp] Faltan SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
    sys.exit(1)

SCRAPER_SLUG = "sesnsp_municipios"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
SESNSP_PAGE = "https://www.gob.mx/sesnsp/acciones-y-programas/datos-abiertos-de-incidencia-delictiva"


def deaccent(s):
    if not s:
        return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normkey(s):
    s = deaccent(s or "").lower()
    return "".join(ch for ch in s if ch.isalnum())


def find_csv_url(html_text):
    matches = re.findall(r'(https?://[^"\'\s<>]+IDEFC[_\-A-Z0-9]*\.csv)', html_text, re.IGNORECASE)
    matches += re.findall(r'(https?://[^"\'\s<>]+attachment/file/\d+/IDEFC[^"\'\s<>]+)', html_text, re.IGNORECASE)
    nm = [u for u in matches if "_NM" in u.upper() or "NM_" in u.upper()]
    return list(dict.fromkeys(nm)) or list(dict.fromkeys(matches))


def discover_csv_url():
    if SESNSP_CSV_URL_OVERRIDE:
        print(f"[sesnsp] usando override URL: {SESNSP_CSV_URL_OVERRIDE}")
        return SESNSP_CSV_URL_OVERRIDE
    print(f"[sesnsp] descubriendo URL desde {SESNSP_PAGE}")
    r = requests.get(SESNSP_PAGE, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    urls = find_csv_url(r.text)
    if not urls:
        raise RuntimeError("No se encontro link IDEFC_NM en la pagina SESNSP")
    print(f"[sesnsp] encontrados {len(urls)} links candidatos")
    return urls[0]


def download_csv(url):
    print(f"[sesnsp] descargando {url}")
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/csv,*/*"}, timeout=300)
    r.raise_for_status()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = r.content.decode(enc)
            if "Clave" in text[:5000] or "Ent" in text[:5000]:
                print(f"[sesnsp] decoded as {enc}, bytes={len(r.content)}")
                return text
        except UnicodeDecodeError:
            continue
    return r.content.decode("latin-1")


MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def parse_int(s):
    if not s or s == "" or s.strip() == "":
        return None
    s = s.replace(",", "").strip()
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def parse_and_unpivot(text):
    sample = text[:5000]
    sep = "," if sample.count(",") > sample.count(";") else ";"
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    all_rows = list(reader)
    if not all_rows:
        return []

    header_idx = 0
    for i, row in enumerate(all_rows[:5]):
        joined = " ".join(row).lower()
        if "ano" in normkey(joined) or "anio" in normkey(joined) or "clave" in normkey(joined):
            header_idx = i
            break

    raw_headers = [(c or "").strip() for c in all_rows[header_idx]]
    nh = [normkey(h) for h in raw_headers]
    print(f"[sesnsp] sep={sep!r} headers (norm): {nh[:15]}...")

    def idx(*names):
        for n in names:
            if n in nh:
                return nh.index(n)
        return -1

    i_anio = idx("ano", "anio")
    i_cve_ent = idx("claveent", "cveent", "cveagee")
    i_entidad = idx("entidad", "estado")
    i_cve_mun = idx("cvemunicipio", "cveagemmunicipio", "cveagem", "clavemunicipio")
    i_municipio = idx("municipio", "nommun")
    i_bien = idx("bienjuridicoafectado", "bienjuridico")
    i_tipo = idx("tipodedelito", "tipodelito")
    i_subtipo = idx("subtipodedelito", "subtipodelito")
    i_modalidad = idx("modalidad")

    mes_indices = {}
    for i, h in enumerate(nh):
        mes_key = MESES.get(deaccent(h).lower())
        if mes_key:
            mes_indices[i] = mes_key

    print(f"[sesnsp] cols: anio={i_anio} cve_ent={i_cve_ent} cve_mun={i_cve_mun} bien={i_bien} tipo={i_tipo} subtipo={i_subtipo} mod={i_modalidad} meses={list(mes_indices.values())}")

    if i_anio == -1 or i_cve_mun == -1:
        raise RuntimeError(f"Headers faltantes. Headers: {raw_headers[:20]}")

    rows_long = []
    skipped = 0
    for row in all_rows[header_idx + 1:]:
        if not row or len([c for c in row if (c or "").strip()]) < 5:
            continue
        try:
            anio = parse_int(row[i_anio])
            cve_ent_raw = (row[i_cve_ent] or "").strip() if i_cve_ent >= 0 else ""
            cve_mun_raw = (row[i_cve_mun] or "").strip() if i_cve_mun >= 0 else ""
            if not anio or not cve_mun_raw:
                skipped += 1
                continue
            cve_ent = cve_ent_raw.zfill(2)
            cve_mun = cve_mun_raw.zfill(3)
            if len(cve_mun) > 3:
                cve_mun = cve_mun[-3:]
            clave_inegi = f"{cve_ent}{cve_mun}"

            bien = (row[i_bien] or "").strip() if i_bien >= 0 else None
            tipo = (row[i_tipo] or "").strip() if i_tipo >= 0 else None
            subtipo = (row[i_subtipo] or "").strip() if i_subtipo >= 0 else None
            modalidad = (row[i_modalidad] or "").strip() if i_modalidad >= 0 else None
            entidad = (row[i_entidad] or "").strip() if i_entidad >= 0 else None
            municipio = (row[i_municipio] or "").strip() if i_municipio >= 0 else None

            for col_idx, mes_num in mes_indices.items():
                if col_idx >= len(row):
                    continue
                cantidad = parse_int(row[col_idx])
                if cantidad is None or cantidad == 0:
                    continue
                rows_long.append({
                    "clave_inegi": clave_inegi,
                    "clave_entidad": cve_ent,
                    "clave_municipio": cve_mun,
                    "nombre_estado": entidad,
                    "nombre_municipio": municipio,
                    "anio": anio,
                    "mes": mes_num,
                    "bien_juridico": bien or None,
                    "tipo_delito": tipo or None,
                    "subtipo_delito": subtipo or None,
                    "modalidad": modalidad or None,
                    "cantidad": cantidad,
                })
        except Exception:
            skipped += 1

    print(f"[sesnsp] long rows: {len(rows_long)} (skipped {skipped})")
    return rows_long


def sb_upsert(table, rows, on_conflict, batch_size=500):
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
            timeout=120,
        )
        if not r.ok:
            raise RuntimeError(f"upsert batch {i//batch_size} status={r.status_code}: {r.text[:300]}")
        total += len(batch)
        if total % 5000 == 0:
            print(f"[sesnsp] upserted {total}/{len(rows)}")
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
        print(f"[sesnsp] no log: {exc}", file=sys.stderr)


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    summary = {"inserted": 0, "skipped": 0, "errors": []}
    csv_url = ""

    try:
        csv_url = discover_csv_url()
        text = download_csv(csv_url)
        rows = parse_and_unpivot(text)

        if SESNSP_ANIOS:
            anios_set = {int(a.strip()) for a in SESNSP_ANIOS.split(",") if a.strip()}
            before = len(rows)
            rows = [r for r in rows if r["anio"] in anios_set]
            print(f"[sesnsp] filtrado por anios {anios_set}: {before} -> {len(rows)}")

        seen = set()
        deduped = []
        for r in rows:
            k = (r["clave_inegi"], r["anio"], r["mes"], r.get("bien_juridico"), r.get("tipo_delito"), r.get("subtipo_delito"), r.get("modalidad"))
            if k in seen:
                summary["skipped"] += 1
                continue
            seen.add(k)
            deduped.append(r)
        print(f"[sesnsp] dedup: {len(rows)} -> {len(deduped)}")

        if deduped:
            ins = sb_upsert("delitos_municipios", deduped,
                            on_conflict="clave_inegi,anio,mes,bien_juridico,tipo_delito,subtipo_delito,modalidad")
            summary["inserted"] = ins
            print(f"[sesnsp] upserted: {ins}")

        status = "ok" if summary["inserted"] > 0 else "fail"
    except Exception as exc:
        import traceback; traceback.print_exc()
        summary["errors"].append(str(exc))
        status = "fail"

    log_scraper(status, summary, "; ".join(summary["errors"]) or None, started_at, csv_url or SESNSP_PAGE)
    print(f"[sesnsp] DONE status={status} total={summary['inserted']}")
    sys.exit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
