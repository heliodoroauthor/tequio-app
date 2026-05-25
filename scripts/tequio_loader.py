#!/usr/bin/env python3
"""tequio_loader.py v5 — Carga masiva con Justia smart fallback.

v5: try_justia_variants() prueba múltiples patterns de URL en Justia cuando el directo falla.

🦎 Cero Invención · Tequio · 2026
"""
import os, sys, time, argparse, re
from typing import Optional
import requests

try:
    import pdfplumber
except ImportError:
    print("ERROR: falta pdfplumber. Instala: pip install pdfplumber")
    sys.exit(1)

SB_URL = os.environ.get("SUPABASE_URL", "https://mhsuihwjgtzxflesbnxv.supabase.co")
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
if not SB_KEY:
    print("ERROR: falta SUPABASE_SERVICE_ROLE_KEY")
    sys.exit(1)

UA = "Tequio-Loader/5.0"

def headers():
    return {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}

def fetch_queue(priority, limit):
    r = requests.get(f"{SB_URL}/rest/v1/_overnight_progress",
        params={"prioridad": f"eq.{priority}", "estado": "eq.pending", "limit": limit, "order": "id.asc"},
        headers=headers(), timeout=30)
    r.raise_for_status()
    return r.json()

def slugify(s):
    """Slugify spanish text."""
    if not s: return ""
    s = s.lower()
    repl = str.maketrans("áéíóúüñ", "aeiouun")
    s = s.translate(repl)
    s = re.sub(r"[^a-z0-9 -]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s

def state_slug(entidad):
    if entidad == "Estado de México": return "mexico"
    if entidad == "Ciudad de México": return "ciudad-de-mexico"
    return slugify(entidad)

def transform_url_to_pdf(url):
    if not url: return None
    m = re.match(r"https?://www\.diputados\.gob\.mx/LeyesBiblio/ref/([a-zA-Z0-9_]+)\.htm", url)
    if m:
        return f"https://www.diputados.gob.mx/LeyesBiblio/pdf/{m.group(1).upper()}.pdf"
    if url.endswith(".pdf"): return url
    return None

def discover_pdf_from_ref(ref_url, timeout=20):
    try:
        r = requests.get(ref_url, timeout=timeout, headers={"User-Agent": UA})
        r.raise_for_status()
        matches = re.findall(r'(?:\.\./|/)?(?:LeyesBiblio/)?pdf/([A-Z0-9_]+\.pdf)', r.text, re.IGNORECASE)
        if matches:
            return f"https://www.diputados.gob.mx/LeyesBiblio/pdf/{matches[0]}"
    except Exception as e:
        print(f"  - discover failed: {e}")
    return None

def try_justia_variants(doc_name, entidad):
    """Generate Justia URL variants based on doc + state name."""
    if not doc_name or not entidad: return []
    state = state_slug(entidad)
    full_state = slugify(entidad)  # "veracruz-de-ignacio-de-la-llave" if it's that
    base_slug = slugify(doc_name)
    
    # Extract doc-type prefix (e.g., "codigo-civil", "ley-de-educacion")
    # Strip "del-estado-de-X" or similar suffix to get just the doc type
    m = re.match(r"(.+?)-(?:del|para-el|de-la)-(?:estado|ciudad)(?:-libre-y-soberano)?(?:-de)?(?:-.+)?$", base_slug)
    doc_type = m.group(1) if m else base_slug
    
    state_short_options = []
    if state != full_state: state_short_options.append(state)
    state_short_options.append(full_state)
    
    variants = []
    seen = set()
    for s in state_short_options:
        for prefix in ["del-estado-de", "para-el-estado-de", "del-estado-libre-y-soberano-de", "para-el-estado-libre-y-soberano-de"]:
            url = f"https://docs.mexico.justia.com/estatales/{state}/{doc_type}-{prefix}-{s}.pdf"
            if url not in seen:
                variants.append(url); seen.add(url)
    # Last-ditch: just doc type
    variants.append(f"https://docs.mexico.justia.com/estatales/{state}/{doc_type}.pdf")
    variants.append(f"https://docs.mexico.justia.com/estatales/{state}/{base_slug}.pdf")
    return variants

def fetch_pdf_text(url, timeout=30):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
        if r.status_code != 200: return None
        if "pdf" not in r.headers.get("content-type", "").lower(): return None
        import io
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
            return "\n".join(pages).strip()
    except Exception as e:
        print(f"  X fetch failed: {e}")
        return None

def chunk_text(text, chunk_size=4000):
    articles = re.split(r"(?=Art[ií]culo\s+\d+)", text)
    chunks, buf = [], ""
    for art in articles:
        art = art.strip()
        if not art: continue
        if len(buf) + len(art) > chunk_size:
            if buf: chunks.append(buf.strip())
            buf = art
        else:
            buf += "\n" + art if buf else art
    if buf: chunks.append(buf.strip())
    return chunks

def extract_articulo_num(text):
    m = re.search(r"Art[ií]culo\s+(\d+[A-Z]?)", text)
    return m.group(1) if m else None

def insert_chunks(ley_id, ley_nombre, chunks, verbose=False):
    if not chunks: return 0
    total = 0
    BATCH = 50
    for start in range(0, len(chunks), BATCH):
        batch = chunks[start:start+BATCH]
        rows = [{"ley_id": ley_id, "ley_nombre": ley_nombre, "chunk_idx": start+i,
                 "articulo_num": extract_articulo_num(c), "texto": c[:8000], "caracteres": len(c)}
                for i, c in enumerate(batch)]
        r = requests.post(f"{SB_URL}/rest/v1/leyes_chunks", json=rows,
            headers={**headers(), "Prefer": "return=minimal"}, timeout=60)
        if r.status_code >= 300:
            print(f"  X batch {start} failed {r.status_code}: {r.text[:150]}")
            return total
        total += len(rows)
    return total

def mark_done(progress_id, status, chunks_count=0, error=None):
    payload = {"estado": status, "chunks_creados": chunks_count, "finished_at": "now()"}
    if error: payload["error"] = error[:500]
    requests.patch(f"{SB_URL}/rest/v1/_overnight_progress",
        params={"id": f"eq.{progress_id}"}, json=payload, headers=headers(), timeout=15)

def process_one(item, dry_run, verbose):
    pid = item["id"]
    ley_id = item.get("ley_id")
    nombre = item["nombre"]
    raw_url = item.get("url_intentada")
    entidad = item.get("entidad")
    
    pdf_url = transform_url_to_pdf(raw_url)
    if not pdf_url:
        print(f"  - sin PDF derivable de {raw_url}")
        if not dry_run: mark_done(pid, "failed", error="no_pdf_url")
        return "no_pdf_url"
    
    if dry_run:
        print(f"  -> would fetch {pdf_url}")
        return "dry"
    
    print(f"  -> fetching {pdf_url}")
    text = fetch_pdf_text(pdf_url)
    
    # Fallback 1: diputados.gob.mx /ref/ discovery
    if (not text or len(text) < 500) and raw_url and "/ref/" in raw_url:
        print(f"  - primer intento falló, buscando link real")
        alt_url = discover_pdf_from_ref(raw_url)
        if alt_url and alt_url != pdf_url:
            print(f"  -> fetching (diputados fallback) {alt_url}")
            text = fetch_pdf_text(alt_url)
    
    # Fallback 2: Justia variants (para docs estatales)
    if (not text or len(text) < 500) and entidad and "justia.com" in (pdf_url or ""):
        print(f"  - intentando Justia variants para {entidad}")
        for variant in try_justia_variants(nombre, entidad):
            print(f"  -> trying {variant}")
            text = fetch_pdf_text(variant)
            if text and len(text) >= 500:
                print(f"  ✓ variant funcionó")
                break
    
    if not text or len(text) < 500:
        mark_done(pid, "failed", error="empty_or_short_text")
        return "no_text"
    
    chunks = chunk_text(text)
    if verbose: print(f"  - {len(text)} chars, {len(chunks)} chunks")
    
    inserted = insert_chunks(ley_id, nombre, chunks, verbose=verbose)
    if inserted == 0:
        mark_done(pid, "failed", error="insert_returned_0")
        return "insert_fail"
    
    mark_done(pid, "embedded", chunks_count=inserted)
    print(f"  OK {inserted} chunks insertados")
    return "ok"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--priority", default="P2", choices=["P1","P2","P3","P4","P5"])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    
    print(f"Tequio Loader v5 - prioridad={args.priority} limit={args.limit}")
    queue = fetch_queue(args.priority, args.limit)
    print(f"{len(queue)} documentos en cola")
    if not queue: return
    
    stats = {"ok": 0, "no_pdf_url": 0, "no_text": 0, "insert_fail": 0, "dry": 0}
    for i, item in enumerate(queue, 1):
        print(f"\n[{i}/{len(queue)}] {item['nombre'][:80]}")
        try:
            result = process_one(item, args.dry_run, args.verbose)
            stats[result] = stats.get(result, 0) + 1
        except Exception as e:
            print(f"  X exception: {e}")
            stats["no_text"] = stats.get("no_text", 0) + 1
        time.sleep(1.5)
    
    print("\n=== RESUMEN ===")
    for k, v in stats.items(): print(f"  {k}: {v}")
    print("\nEmbeddings se generan via pg_cron (50/min).")

if __name__ == "__main__":
    main()
