#!/usr/bin/env python3
"""
backfill_embeddings.py — One-shot para llenar embeddings vacíos en leyes/jurisprudencia
========================================================================================
Se corre UNA vez desde GitHub Actions o local.

Variables de entorno:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  GEMINI_API_KEY
"""
import os
import time
import requests
import sys

SUPABASE_URL = os.environ['SUPABASE_URL'].rstrip('/')
SERVICE_KEY  = os.environ['SUPABASE_SERVICE_ROLE_KEY']
GEMINI_KEY   = os.environ['GEMINI_API_KEY']

EMBED_URL = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={GEMINI_KEY}"

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

def get_embedding(text):
    """Genera embedding de 768 dimensiones via Gemini text-embedding-004."""
    try:
        r = requests.post(EMBED_URL, json={
            'model': 'models/text-embedding-004',
            'content': {'parts': [{'text': text[:2000]}]},
            'taskType': 'RETRIEVAL_DOCUMENT',
        }, timeout=30)
        if not r.ok:
            print(f"  [ERR Gemini] {r.status_code}: {r.text[:200]}")
            return None
        return r.json()['embedding']['values']
    except Exception as e:
        print(f"  [ERR Gemini] excepcion: {e}")
        return None


def backfill_table(tabla, id_col, texto_cols):
    """
    Busca filas con embedding IS NULL en `tabla`, genera embedding
    desde la concatenacion de `texto_cols`, y hace UPDATE.
    """
    print(f"\n=== {tabla} ===")
    # 1. Buscar filas sin embedding
    select_cols = id_col + ',' + ','.join(texto_cols) + ',embedding'
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{tabla}?embedding=is.null&select={select_cols}",
        headers=HEADERS_SB, timeout=30
    )
    if not r.ok:
        print(f"  [ERR Supabase select] {r.status_code}: {r.text[:200]}")
        return
    filas = r.json()
    print(f"  {len(filas)} filas sin embedding")
    if not filas:
        print("  Nada que hacer.")
        return

    actualizadas = 0
    for fila in filas:
        # Concatenar todos los campos de texto
        texto = ' '.join(str(fila.get(c) or '') for c in texto_cols).strip()
        if not texto:
            continue
        emb = get_embedding(texto)
        if not emb:
            continue
        # UPDATE
        u = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{tabla}?{id_col}=eq.{fila[id_col]}",
            headers={**HEADERS_SB, 'Prefer': 'return=minimal'},
            json={'embedding': emb},
            timeout=30
        )
        if u.ok:
            actualizadas += 1
            label = str(fila.get(texto_cols[0]) or fila[id_col])[:60]
            print(f"  [OK] {label}")
        else:
            print(f"  [ERR Supabase update] {u.status_code}: {u.text[:200]}")
        # Rate limit Gemini (1500 req/min en free tier, sobramos)
        time.sleep(0.5)

    print(f"  Total actualizadas: {actualizadas}/{len(filas)}")


def main():
    print("Tequio Backfill Embeddings — text-embedding-004 (768d)")
    backfill_table('leyes', 'id', ['nombre', 'texto'])
    backfill_table('jurisprudencia', 'id', ['rubro', 'texto'])
    print("\nBackfill completo.")


if __name__ == '__main__':
    main()
