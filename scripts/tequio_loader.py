#!/usr/bin/env python3
"""
tequio_loader.py v2 — Carga masiva de leyes federales pendientes a Supabase.

v2: Agrega fallback discover_pdf_from_ref() cuando /pdf/X.pdf da 404.

Uso:
    pip install requests pdfplumber
    export SUPABASE_URL="..."
    export SUPABASE_SERVICE_ROLE_KEY="..."
    python tequio_loader.py --priority P2 --limit 50

🦎 Cero Invención · Tequio · 2026
"""

import os
import sys
import time
import argparse
import re
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
    print("ERROR: falta SUPABASE_SERVICE_ROLE_KEY en env.")
    sys.exit(1)


def headers():
    return {
        "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
        "Content-Type": "application/json",
    }


def fetch_queue(priority: str, limit: int) -> list:
    r = requests.get(
        f"{SB_URL}/rest/v1/_overnight_progress",
        params={
            "prioridad": f"eq.{priority}",
            "estado": "eq.pending",
            "limit": limit,
            "order": "id.asc",
        },
        headers=headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def transform_url_to_pdf(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.match(r"https?://www\.diputados\.gob\.mx/LeyesBiblio/ref/([a-zA-Z0-9_]+)\.htm", url)
    if m:
        abbr = m.group(1).upper()
        return f"https://www.diputados.gob.mx/LeyesBiblio/pdf/{abbr}.pdf"
    if url.endswith(".pdf"):
        return url
    return None


def discover_pdf_from_ref(ref_url: str, timeout: int = 20) -> Optional[str]:
    """Fetch /ref/X.htm and parse for real PDF link."""
    try:
        r = requests.get(ref_url, timeout=timeout, headers={"User-Agent": "Tequio-Loader/2.0"})
        r.raise_for_status()
        html = r.text
        matches = re.findall(r'(?:\.\./|/)?(?:LeyesBiblio/)?pdf/([A-Z0-9_]+\.pdf)', html, re.IGNORECASE)
        if matches:
            pdf_name = matches[0]
            return f"https://www.diputados.gob.mx/LeyesBiblio/pdf/{pdf_name}"
    except Exception as e:
        print(f"  - discover failed: {e}")
    return None


def fetch_pdf_text(url: str, timeout: int = 30) -> Optional[str]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Tequio-Loader/2.0"})
        r.raise_for_status()
        if "pdf" not in r.headers.get("content-type", "").lower():
            return None
        import io
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
            return "\n".join(pages).strip()
    except Exception as e:
        print(f"  X fetch failed: {e}")
        return None


def chunk_text(text: str, chunk_size: int = 4000) -> list:
    articles = re.split(r"(?=Art[ií]culo\s+\d+)", text)
    chunks = []
    buf = ""
    for art in articles:
        art = art.strip()
        if not art:
            continue
        if len(buf) + len(art) > chunk_size:
            if buf:
                chunks.append(buf.strip())
            buf = art
        else:
            buf += "\n" + art if buf else art
    if buf:
        chunks.append(buf.strip())
    return chunks


def extract_articulo_num(chunk_text: str) -> Optional[str]:
    m = re.search(r"Art[ií]culo\s+(\d+[A-Z]?)", chunk_text)
    return m.group(1) if m else None


def insert_chunks(ley_id, ley_nombre, chunks, verbose=False) -> int:
    if not chunks:
        return 0
    total_inserted = 0
    BATCH = 50
    for batch_start in range(0, len(chunks), BATCH):
        batch = chunks[batch_start:batch_start+BATCH]
        rows = []
        for offset, c in enumerate(batch):
            idx = batch_start + offset
            rows.append({
                "ley_id": ley_id,
                "ley_nombre": ley_nombre,
                "chunk_idx": idx,
                "articulo_num": extract_articulo_num(c),
                "texto": c[:8000],
                "caracteres": len(c),
            })
        r = requests.post(
            f"{SB_URL}/rest/v1/leyes_chunks",
            json=rows,
            headers={**headers(), "Prefer": "return=minimal"},
            timeout=60,
        )
        if r.status_code >= 300:
            print(f"  X batch {batch_start} failed {r.status_code}: {r.text[:150]}")
            return total_inserted
        total_inserted += len(rows)
    return total_inserted


def mark_done(progress_id, status, chunks_count=0, error=None):
    payload = {
        "estado": status,
        "chunks_creados": chunks_count,
        "finished_at": "now()",
    }
    if error:
        payload["error"] = error[:500]
    requests.patch(
        f"{SB_URL}/rest/v1/_overnight_progress",
        params={"id": f"eq.{progress_id}"},
        json=payload,
        headers=headers(),
        timeout=15,
    )


def process_one(item, dry_run, verbose):
    progress_id = item["id"]
    ley_id = item.get("ley_id")
    nombre = item["nombre"]
    raw_url = item.get("url_intentada")

    pdf_url = transform_url_to_pdf(raw_url)
    if not pdf_url:
        print(f"  - sin PDF derivable de {raw_url}")
        if not dry_run:
            mark_done(progress_id, "failed", error="no_pdf_url")
        return "no_pdf_url"

    if dry_run:
        print(f"  -> would fetch {pdf_url}")
        return "dry"

    print(f"  -> fetching {pdf_url}")
    text = fetch_pdf_text(pdf_url)

    if (not text or len(text) < 500) and raw_url and "/ref/" in raw_url:
        print(f"  - primer intento falló, buscando link real en {raw_url}")
        alt_url = discover_pdf_from_ref(raw_url)
        if alt_url and alt_url != pdf_url:
            print(f"  -> fetching (fallback) {alt_url}")
            text = fetch_pdf_text(alt_url)

    if not text or len(text) < 500:
        mark_done(progress_id, "failed", error="empty_or_short_text")
        return "no_text"

    chunks = chunk_text(text)
    if verbose:
        print(f"  - {len(text)} chars, {len(chunks)} chunks")

    inserted = insert_chunks(ley_id, nombre, chunks, verbose=verbose)
    if inserted == 0:
        mark_done(progress_id, "failed", error="insert_returned_0")
        return "insert_fail"

    mark_done(progress_id, "embedded", chunks_count=inserted)
    print(f"  OK {inserted} chunks insertados")
    return "ok"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--priority", default="P2", choices=["P1", "P2", "P3"])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    print(f"Tequio Loader v2 - prioridad={args.priority} limit={args.limit}")
    queue = fetch_queue(args.priority, args.limit)
    print(f"{len(queue)} documentos en cola")
    if not queue:
        return

    stats = {"ok": 0, "no_pdf_url": 0, "no_text": 0, "insert_fail": 0, "dry": 0}
    for i, item in enumerate(queue, 1):
        print(f"\n[{i}/{len(queue)}] {item['nombre'][:80]}")
        try:
            result = process_one(item, args.dry_run, args.verbose)
            stats[result] = stats.get(result, 0) + 1
        except Exception as e:
            print(f"  X exception: {e}")
            stats["no_text"] = stats.get("no_text", 0) + 1
        time.sleep(2)

    print("\n=== RESUMEN ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print("\nEmbeddings se generan via pg_cron (50/min).")


if __name__ == "__main__":
    main()
