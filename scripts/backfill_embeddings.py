#!/usr/bin/env python3
"""
backfill_embeddings.py v2 — Diagnóstico verboso
================================================
Llena embeddings NULL en leyes/jurisprudencia.
Usa filtro en Python (no PostgREST) para evitar bugs con columnas vector.
"""
import os, sys, time, json, requests

print("Tequio Backfill Embeddings v2 — text-embedding-004 (768d)")
print(f"  Python: {sys.version.split()[0]}")

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')

print(f"  SUPABASE_URL: {'OK' if SUPABASE_URL else 'MISSING'} ({SUPABASE_URL[:30] if SUPABASE_URL else 'n/a'}...)")
print(f"  SERVICE_KEY: {'OK ('+str(len(SERVICE_KEY))+' chars)' if SERVICE_KEY else 'MISSING'}")
print(f"  GEMINI_KEY: {'OK ('+str(len(GEMINI_KEY))+' chars)' if GEMINI_KEY else 'MISSING'}")

if not (SUPABASE_URL and SERVICE_KEY and GEMINI_KEY):
    print("ERROR: faltan variables de entorno. Abortando.")
    sys.exit(1)

EMBED_URL = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={GEMINI_KEY}"

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}


def test_gemini():
    """Smoke test: ¿La API key Gemini funciona?"""
    print("\n[TEST] Probando Gemini text-embedding-004...")
    try:
        r = requests.post(EMBED_URL, json={
            'model': 'models/text-embedding-004',
            'content': {'parts': [{'text': 'hola mundo'}]},
            'taskType': 'RETRIEVAL_DOCUMENT',
        }, timeout=20)
        print(f"  Status: {r.status_code}")
        if not r.ok:
            print(f"  Body: {r.text[:400]}")
            return False
        emb = r.json().get('embedding', {}).get('values', [])
        print(f"  Embedding OK: {len(emb)} dimensiones")
        return len(emb) == 768
    except Exception as e:
        print(f"  EXCEPCION: {type(e).__name__}: {e}")
        return False


def test_supabase():
    """Smoke test: ¿podemos leer la tabla leyes?"""
    print("\n[TEST] Probando Supabase select leyes...")
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/leyes?select=id,nombre&limit=3",
                         headers=HEADERS_SB, timeout=15)
        print(f"  Status: {r.status_code}")
        if not r.ok:
            print(f"  Body: {r.text[:400]}")
            return False
        data = r.json()
        print(f"  Leyes traidas: {len(data)} (sample: {data[0]['nombre'][:60] if data else 'vacio'})")
        return True
    except Exception as e:
        print(f"  EXCEPCION: {type(e).__name__}: {e}")
        return False


def get_embedding(text):
    """Genera embedding 768d via Gemini."""
    try:
        r = requests.post(EMBED_URL, json={
            'model': 'models/text-embedding-004',
            'content': {'parts': [{'text': text[:2000]}]},
            'taskType': 'RETRIEVAL_DOCUMENT',
        }, timeout=30)
        if not r.ok:
            print(f"    [Gemini ERR {r.status_code}] {r.text[:200]}")
            return None
        return r.json()['embedding']['values']
    except Exception as e:
        print(f"    [Gemini EXC] {e}")
        return None


def backfill_table(tabla, id_col, texto_cols):
    print(f"\n=== {tabla} ===")
    # Traer TODAS las filas con sus textos y filtrar embedding NULL en Python
    select_cols = id_col + ',' + ','.join(texto_cols) + ',embedding'
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{tabla}?select={select_cols}",
            headers=HEADERS_SB, timeout=30
        )
        if not r.ok:
            print(f"  [Supabase select ERR {r.status_code}] {r.text[:400]}")
            return
        todas = r.json()
        print(f"  Total filas en {tabla}: {len(todas)}")
        # Filtrar en Python
        filas = [f for f in todas if f.get('embedding') is None]
        print(f"  Filas con embedding NULL: {len(filas)}")
    except Exception as e:
        print(f"  [Supabase EXC] {e}")
        return

    if not filas:
        print("  Nada que hacer.")
        return

    actualizadas = 0
    fallidas = 0
    for fila in filas:
        texto = ' '.join(str(fila.get(c) or '') for c in texto_cols).strip()
        if not texto:
            print(f"  [SKIP id={fila[id_col]}] texto vacio")
            continue
        emb = get_embedding(texto)
        if not emb:
            fallidas += 1
            continue
        # UPDATE via PATCH
        try:
            u = requests.patch(
                f"{SUPABASE_URL}/rest/v1/{tabla}?{id_col}=eq.{fila[id_col]}",
                headers={**HEADERS_SB, 'Prefer': 'return=minimal'},
                json={'embedding': emb},
                timeout=30
            )
            if u.ok:
                actualizadas += 1
                label = str(fila.get(texto_cols[0]) or fila[id_col])[:60]
                print(f"  [OK] id={fila[id_col]} {label}")
            else:
                print(f"  [Update ERR {u.status_code}] {u.text[:200]}")
                fallidas += 1
        except Exception as e:
            print(f"  [Update EXC] {e}")
            fallidas += 1
        time.sleep(0.3)

    print(f"  RESUMEN {tabla}: {actualizadas} actualizadas, {fallidas} fallidas")


def main():
    if not test_supabase():
        print("ERROR: Supabase no responde. Abortando.")
        sys.exit(1)
    if not test_gemini():
        print("ERROR: Gemini no responde. Abortando.")
        sys.exit(1)
    backfill_table('leyes', 'id', ['nombre', 'texto'])
    backfill_table('jurisprudencia', 'id', ['rubro', 'texto'])
    print("\nBackfill completo.")


if __name__ == '__main__':
    main()
