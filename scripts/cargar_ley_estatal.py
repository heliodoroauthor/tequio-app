#!/usr/bin/env python3
"""
Cargador de una ley estatal: download PDF → pdftotext → parser_leyes → bulk insert.

Workaround del bug en load-leyes-chunks (chunk_idx siempre NULL).
Usa RPCs SECURITY DEFINER ya desplegadas en Postgres:
  - public.leyes_chunks_replace_all(p_ley_id, p_ley_nombre, p_chunks)
  - public.leyes_chunks_bulk_insert(p_ley_id, p_ley_nombre, p_start_idx, p_chunks)

Uso:
  python3 cargar_ley_estatal.py <ley_id> <url_pdf> "<nombre_ley>" [entidad]

Ejemplo:
  python3 cargar_ley_estatal.py 3200 \\
    \"https://www.hcnl.gob.mx/.../LEY%20DE%20GOBIERNO%20MUNICIPAL.pdf\" \\
    \"Ley de Gobierno Municipal del Estado de Nuevo León\" \\
    \"Nuevo Leon\"
"""
import sys, json, os, subprocess
from pathlib import Path

# Importar parser desde el mismo directorio
sys.path.insert(0, str(Path(__file__).parent))
from parser_leyes import parse_leyes_texto

SUPABASE_URL = "https://mhsuihwjgtzxflesbnxv.supabase.co"
ANON_KEY = "sb_publishable_grJAWIMoSr5b8YfaB7HVlw_OesNipBz"


def call_rpc(rpc_name: str, payload: dict) -> str:
    """Llama RPC vía REST PostgREST (payload via --data-binary @file para evitar arg too long)."""
    tmp = f"/tmp/_rpc_{os.getpid()}.json"
    with open(tmp, "w") as f:
        json.dump(payload, f, ensure_ascii=False)
    r = subprocess.run([
        "curl", "-s", "-X", "POST",
        f"{SUPABASE_URL}/rest/v1/rpc/{rpc_name}",
        "-H", "Content-Type: application/json",
        "-H", f"apikey: {ANON_KEY}",
        "-H", f"Authorization: Bearer {ANON_KEY}",
        "--data-binary", f"@{tmp}"
    ], capture_output=True, text=True, timeout=120)
    return r.stdout


def cargar_ley(ley_id: int, url: str, nombre: str, entidad: str = "Nuevo Leon", fuente: str = "HCNL"):
    pdf_path = f"/tmp/ley_{ley_id}.pdf"
    txt_path = f"/tmp/ley_{ley_id}.txt"
    print(f"[{ley_id}] {nombre}")

    # 1. Download
    r = subprocess.run([
        "curl", "-sL", "-m", "25", "-w", "%{http_code}|%{size_download}",
        "-o", pdf_path, url
    ], capture_output=True, text=True)
    try:
        code, size = r.stdout.strip().split("|")
        size = int(size)
    except Exception:
        print(f"  ❌ curl error: {r.stdout}")
        return None
    if code != "200" or size < 1000:
        print(f"  ❌ PDF no descargable ({code}, {size}b)")
        return None
    print(f"  📥 PDF: {size:,}b")

    # 2. PDF a texto
    subprocess.run(["pdftotext", "-layout", pdf_path, txt_path], timeout=30, capture_output=True)
    text = open(txt_path).read()
    print(f"  📄 Texto: {len(text):,} chars")

    # 3. Parse
    chunks = parse_leyes_texto(text)
    if not chunks:
        print(f"  ⚠️  0 chunks — ley sin estructura ARTÍCULO reconocible")
        return None
    print(f"  🧩 {len(chunks)} chunks parseados")

    # 4. Replace all (purga + insert atómico)
    out = call_rpc("leyes_chunks_replace_all", {
        "p_ley_id": int(ley_id),
        "p_ley_nombre": nombre,
        "p_chunks": chunks
    })
    try:
        n = int(out.strip())
    except Exception:
        print(f"  ❌ RPC falló: {out[:300]}")
        return None

    # 5. Patch header de la ley
    patch = json.dumps({
        "url": url,
        "ambito": "estatal",
        "entidad": entidad,
        "tipo": "ley",
        "fuente": fuente,
        "fecha_publicacion": "2026-05-28"
    })
    subprocess.run([
        "curl", "-s", "-X", "PATCH",
        f"{SUPABASE_URL}/rest/v1/leyes?id=eq.{ley_id}",
        "-H", "Content-Type: application/json",
        "-H", f"apikey: {ANON_KEY}",
        "-H", f"Authorization: Bearer {ANON_KEY}",
        "-d", patch
    ], capture_output=True, timeout=15)

    # 6. Cleanup
    try:
        os.remove(pdf_path); os.remove(txt_path)
    except Exception:
        pass

    print(f"  ✅ Insertados: {n} chunks (embed cron los procesará en ≤15s)")
    return n


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    ley_id = int(sys.argv[1])
    url = sys.argv[2]
    nombre = sys.argv[3]
    entidad = sys.argv[4] if len(sys.argv) > 4 else "Nuevo Leon"
    cargar_ley(ley_id, url, nombre, entidad)
