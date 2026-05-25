#!/usr/bin/env python3
"""
tequio_loader.py — Carga masiva de leyes federales pendientes a Supabase.

Diseñado para que Helio lo corra de su máquina con Python 3.10+.
Lee la cola `_overnight_progress`, baja los PDFs de diputados.gob.mx,
extrae texto, y postea a la RPC chat_load_leyes_chunks.

Uso:
    pip install requests pdfplumber supabase
    export SUPABASE_URL="https://mhsuihwjgtzxflesbnxv.supabase.co"
    export SUPABASE_SERVICE_ROLE_KEY="<service role>"
    python tequio_loader.py --priority P2 --limit 50

Args:
    --priority  P2 (federales), P3 (reglamentos), P1 (constituciones estatales)
    --limit     Cuántos procesar antes de salir (default 20)
    --dry-run   Solo lista lo que haría, no carga
    --verbose   Más logging

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
    print("ERROR: falta SUPABASE_SERVICE_ROLE_KEY en env. Cópiala de Supabase Dashboard → Settings → API.")
    sys.exit(1)


def headers():
    return {
        "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
        "Content-Type": "application/json",
    }


def fetch_queue(priority: str, limit: int) -> list:
    """Get pending docs from queue."""
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
    """diputados.gob.mx/ref/X.htm → diputados.gob.mx/pdf/X.pdf (primer intento)"""
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
    """Fallback: si /pdf/X.pdf no funciona, fetch /ref/X.htm y busca link real al PDF.

    Las páginas /ref/ tienen un link tipo:
      <a href="../pdf/LAREFAM.pdf">Texto vigente</a>
    o también pueden tener pdf con sufijos como _LFT, _ref01, etc.
    """
    try:
        r = requests.get(ref_url, timeout=timeout, headers={"User-Agent": "Tequio-Loader/1.0"})
        r.raise_for_status()
        html = r.text
        # Busca cualquier link a /pdf/X.pdf en el HTML
        matches = re.findall(r'(?:\.\./|/)?(?:LeyesBiblio/)?pdf/([A-Z0-9_]+\.pdf)', html, re.IGNORECASE)
        if matches:
            # Devuelve el primero (el más reciente / texto vigente)
            pdf_name = matches[0]
            return f"https://www.diputados.gob.mx/LeyesBiblio/pdf/{pdf_name}"
    except Exception as e:
        print(f"  · discover_pdf_from_ref failed: {e}")
    return None


def fetch_pdf_text(url: str, timeout: int = 30) -> Optional[str]:
    """Download PDF and extract text via pdfplumber."""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Tequio-Loader/1.0"})
        r.raise_for_status()
        if "pdf" not in r.headers.get("content-type", "").lower():
            return None
        import io
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
            return "\n".join(pages).strip()
    except Exception as e:
        print(f"  ✗ fetch failed: {e}")
        return None


def chunk_text(text: str, chunk_size: int = 4000, overlap: int = 200) -> list:
    """Simple chunker by article boundaries + size."""
    # Split on "Artículo N" markers
    articles = re.split(r"(?=Art[íi]culo\s+\d+[º°.\-]?)", text)
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
    m = re.search(r"Art[íi]culo\s+(\d+[A-Z]?)", chunk_text)
    return m.group(1) if m else None


def insert_chunks(ley_id: int, ley_nombre: str, chunks: list, verbose: bool = False) -> int:
    """Bulk insert chunks via REST. Returns count inserted."""
    rows = []
    for idx, c in enumerate(chunks):
        rows.append({
            "ley_id": ley_id,
            "ley_nombre": ley_nombre,
            "chunk_idx": idx,
            "articulo_num": extract_articulo_num(c),
            "texto": c[:8000],  # safety cap
            "caracteres": len(c),
        })
    if not rows:
        return 0
    r = requests.post(
        f"{SB_URL}/rest/v1/leyes_chunks",
        json=rows,
        headers={**headers(), "Prefer": "return=minimal"},
        timeout=60,
    )
    if r.status_code >= 300:
        print(f"  ✗ insert failed {r.status_code}: {r.text[:200]}")
        return 0
    return len(rows)


def mark_done(progress_id: int, status: str, chunks_count: int = 0, error: str = None):
    payload = {
        "estado": status,
        "chunks_creados": chunks_count,
        "finished_at": "now()",
    }
    if error:
        payload["error"] = error[:500]
    r = requests.patch(
        f"{SB_URL}/rest/v1/_overnight_progress",
        params={"id": f"eq.{progress_id}"},
        json=payload,
        headers=headers(),
        timeout=15,
    )


def process_one(item: dict, dry_run: bool, verbose: bool) -> str:
    """Returns 'ok', 'no_pdf_url', 'fetch_fail', 'no_text', 'insert_fail'."""
    progress_id = item["id"]
    ley_id = item.get("ley_id")
    nombre = item["nombre"]
    raw_url = item.get("url_intentada")

    pdf_url = transform_url_to_pdf(raw_url)
    if not pdf_url:
        print(f"  · sin PDF derivable de {raw_url}")
        if not dry_run:
            mark_done(progress_id, "failed", error="no_pdf_url")
        return "no_pdf_url"

    if dry_run:
        print(f"  → would fetch {pdf_url}")
        return "dry"

    print(f"  → fetching {pdf_url}")
    text = fetch_pdf_text(pdf_url)

    # Fallback: si el PDF directo no existe (404), buscar el link real desde la página /ref/
    if (not text or len(text) < 500) and raw_url and "/ref/" in raw_url:
        print(f"  · primer intento falló · buscando PDF real en {raw_url}")
        alt_url = discover_pdf_from_ref(raw_url)
        if alt_url and alt_url != pdf_url:
            print(f"  → fetching (fallback) {alt_url}")
            text = fetch_pdf_text(alt_url)

    if not text or len(text) < 500:
        mark_done(progress_id, "failed", error="empty_or_short_text")
        return "no_text"

    chunks = chunk_text(text)
    if verbose:
        print(f"  → {len(text)} chars, {len(chunks)} chunks")

    inserted = insert_chunks(ley_id, nombre, chunks, verbose=verbose)
    if inserted == 0:
        mark_done(progress_id, "fai