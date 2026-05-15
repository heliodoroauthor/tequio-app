#!/usr/bin/env python3
"""
Tequio - Scraper SRE (Embajadas + Consulados de Mexico en el Exterior) v4
Cambio: UA Mozilla para evitar bloqueo de SRE.
"""
import json, os, re, sys, unicodedata
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
GH_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")

if not SB_URL or not SB_KEY:
    print("[sre] Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY", file=sys.stderr)
    sys.exit(1)

SCRAPER_SLUG = "sre_directorio"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
EMBAJADAS_URL = "https://portales.sre.gob.mx/directorio/embajadas-de-mexico-en-el-exterior"
CONSULADOS_URL = "https://portales.sre.gob.mx/directorio/consulados-de-mexico-en-el-exterior"

BLACKLIST = {
    "directorio", "pais", "consulado", "embajada", "sre", "gob mx",
    "interruptor de navegacion", "tramites", "gobierno", "participa", "busqueda",
    "sre directorio", "english", "temas", "reformas", "leer mas",
    "accesibilidad", "politica de privacidad", "terminos y condiciones",
    "marco juridico", "portal de obligaciones de transparencia", "sistema infomex",
    "inai", "atencion ciudadana", "quejas y denuncias", "facebook", "twitter",
    "oficinas de pasaportes", "oficinas centrales", "dependencias federales",
    "embajadas de mexico", "consulados de mexico", "misiones de mexico",
    "oficinas de enlace de mexico",
    "centro de informacion y atencion a personas", "mi consulado",
}


def slugify(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


BLACKLIST_SLUGS = {slugify(b) for b in BLACKLIST}


def fetch_html(url, intentos=3):
    h = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    for i in range(intentos):
        try:
            r = requests.get(url, headers=h, timeout=30, allow_redirects=True)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            print(f"[sre] fetch {url}: status={r.status_code} bytes={len(r.text)}")
            return r.text
        except Exception as exc:
            if i == intentos - 1:
                raise
            print(f"[sre] retry {i+1} {url}: {exc}", file=sys.stderr)
    return ""


def text_between(node_a, node_b):
    out = []
    cur = node_a
    if cur is None:
        return ""
    while True:
        cur = cur.next_element
        if cur is None or cur is node_b:
            break
        if isinstance(cur, str):
            out.append(cur)
    return "".join(out)


def parse_directorio(html, fuente_url, modo):
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.find_all("a")
    items = []
    prev_anchor = None
    dir_count = 0

    for i, a in enumerate(anchors):
        label = (a.get_text() or "").strip().lower()
        href = a.get("href") or ""
        if label.startswith("directorio"):
            dir_count += 1
            raw = text_between(prev_anchor, a) if prev_anchor is not None else ""
            if not raw and a.parent is not None:
                raw = a.parent.get_text(" ", strip=True)
            nombre = re.sub(r"\b(directorio|sitio web|pendiente)\b", "", raw, flags=re.IGNORECASE)
            nombre = re.sub(r"https?://\S+", "", nombre)
            nombre = re.sub(r"[\[\]\(\)]", "", nombre)
            nombre = re.sub(r"\s+", " ", nombre).strip(" -,\n\t")
            if nombre and 2 <= len(nombre) <= 80:
                sitio_web_url = None
                if i + 1 < len(anchors):
                    nxt = anchors[i + 1]
                    nlabel = (nxt.get_text() or "").strip().lower()
                    if "sitio" in nlabel or "web" in nlabel:
                        sitio_web_url = nxt.get("href") or None
                items.append({
                    "nombre_raw": nombre,
                    "directorio_url": href or None,
                    "sitio_web_url": sitio_web_url,
                    "fuente_url": fuente_url,
                })
            prev_anchor = a
        else:
            prev_anchor = a

    seen = set()
    deduped = []
    for it in items:
        slug = slugify(it["nombre_raw"])
        if not slug or slug in seen:
            continue
        if it["nombre_raw"].strip().lower() in BLACKLIST:
            continue
        if slug in BLACKLIST_SLUGS:
            continue
        durl = it["directorio_url"] or ""
        if modo == "embajadas" and "embajadas-de-mexico" not in durl and "/embajadas/" not in durl:
            continue
        if modo == "consulados" and "consulados-de-mexico" not in durl and "/consulados/" not in durl:
            continue
        seen.add(slug)
        deduped.append(it)
    print(f"[sre] {modo}: anchors={len(anchors)} dir_anchors={dir_count} items={len(items)} deduped={len(deduped)}")
    return deduped


def sb_upsert(table, rows, on_conflict):
    if not rows:
        return 0
    url = f"{SB_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    r = requests.post(url, headers={
        "Content-Type": "application/json", "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }, data=json.dumps(rows), timeout=60)
    if not r.ok:
        raise RuntimeError(f"upsert {table} {r.status_code}: {r.text[:300]}")
    return len(rows)


def log_scraper(status, summary, error_msg, started_at):
    payload = [{
        "scraper_slug": SCRAPER_SLUG, "workflow_run_id": GH_RUN_ID or None,
        "status": status, "rows_inserted": summary.get("inserted", 0),
        "rows_updated": 0, "rows_skipped": summary.get("skipped", 0),
        "fuente_url": "https://portales.sre.gob.mx/directorio/",
        "http_status": 200,
        "error_msg": (error_msg or "")[:1000] or None,
        "notes": json.dumps(summary)[:1000],
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }]
    try:
        requests.post(f"{SB_URL}/rest/v1/scraper_logs", headers={
            "Content-Type": "application/json", "apikey": SB_KEY,
            "Authorization": f"Bearer {SB_KEY}", "Prefer": "return=minimal",
        }, data=json.dumps(payload), timeout=30)
    except Exception as exc:
        print(f"[sre] no se pudo loguear: {exc}", file=sys.stderr)


def main():
    started_at = datetime.now(timezone.utc).isoformat()
    fecha_hoy = datetime.now(timezone.utc).date().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()
    summary = {"inserted": 0, "embajadas": 0, "consulados": 0}
    hubo_error = False
    primer_error = None

    try:
        html = fetch_html(EMBAJADAS_URL)
        items = parse_directorio(html, EMBAJADAS_URL, "embajadas")
        rows = [{
            "pais": it["nombre_raw"],
            "pais_slug": slugify(it["nombre_raw"]),
            "directorio_url": it["directorio_url"],
            "sitio_web_url": it["sitio_web_url"],
            "fecha_extraccion": fecha_hoy,
            "fuente_url": it["fuente_url"],
            "updated_at": now_iso,
        } for it in items]
        ins = sb_upsert("sre_embajadas", rows, "pais_slug")
        summary["embajadas"] = ins
        summary["inserted"] += ins
        print(f"[sre] embajadas upserted: {ins}")
    except Exception as exc:
        hubo_error = True
        primer_error = primer_error or f"embajadas: {exc}"
        print(f"[sre] FAIL embajadas: {exc}", file=sys.stderr)

    try:
        html = fetch_html(CONSULADOS_URL)
        items = parse_directorio(html, CONSULADOS_URL, "consulados")
        rows = []
        for it in items:
            nombre = it["nombre_raw"]
            ciudad = nombre
            pais = None
            if "," in nombre:
                partes = [p.strip() for p in nombre.split(",", 1)]
                if len(partes) == 2:
                    ciudad, pais = partes[0], partes[1]
            elif "(" in nombre and nombre.endswith(")"):
                m = re.match(r"^(.+?)\s*\((.+)\)$", nombre)
                if m:
                    ciudad, pais = m.group(1).strip(), m.group(2).strip()
            rows.append({
                "nombre": nombre, "nombre_slug": slugify(nombre),
                "pais": pais, "ciudad": ciudad,
                "directorio_url": it["directorio_url"],
                "sitio_web_url": it["sitio_web_url"],
                "tipo": "consulado",
                "fecha_extraccion": fecha_hoy,
                "fuente_url": it["fuente_url"],
                "updated_at": now_iso,
            })
        ins = sb_upsert("sre_consulados", rows, "nombre_slug")
        summary["consulados"] = ins
        summary["inserted"] += ins
        print(f"[sre] consulados upserted: {ins}")
    except Exception as exc:
        hubo_error = True
        primer_error = primer_error or f"consulados: {exc}"
        print(f"[sre] FAIL consulados: {exc}", file=sys.stderr)

    status = "partial" if hubo_error else "ok"
    log_scraper(status, summary, primer_error, started_at)
    print(f"[sre] DONE status={status} total={summary['inserted']} emb={summary['embajadas']} con={summary['consulados']}")
    sys.exit(1 if hubo_error else 0)


if __name__ == "__main__":
    main()
