#!/usr/bin/env python3
"""
backfill_chunks_embeddings_local.py
====================================
Llena embeddings NULL en leyes_chunks usando modelo LOCAL
(sentence-transformers/intfloat/multilingual-e5-base · 768d).

Ventajas vs API:
  - Cero costo (corre en runner GH Actions)
  - No depende de billing externo
  - Falla LOUD: cualquier error revienta el proceso

Uso: python3 backfill_chunks_embeddings_local.py <batch_idx> <total_batches> [--mode=null|all]
Ejemplo:
  python3 backfill_chunks_embeddings_local.py 0 10
  python3 backfill_chunks_embeddings_local.py 0 10 --mode=all
"""
import os
import sys
import time
import json
import requests
from typing import List, Optional

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

if not all([SUPABASE_URL, SERVICE_KEY]):
    print('FATAL: faltan vars SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY', flush=True)
    sys.exit(1)

BATCH_IDX = int(sys.argv[1]) if len(sys.argv) > 1 else 0
TOTAL_BATCHES = int(sys.argv[2]) if len(sys.argv) > 2 else 10
MODE = 'null'
for arg in sys.argv[3:]:
    if arg.startswith('--mode='):
        MODE = arg.split('=', 1)[1].strip().lower()
        if MODE not in ('null', 'all'):
            print(f'FATAL: --mode debe ser null|all, got "{MODE}"', flush=True)
            sys.exit(1)

HEADERS = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

MODEL_NAME = 'intfloat/multilingual-e5-base'  # 768d, multilingual
ENCODE_BATCH = 32       # cuántos chunks codificamos en una pasada del modelo
FETCH_BATCH = 200       # cuántas filas pedimos a Postgres por request
MAX_TEXT_CHARS = 4000   # truncar para no reventar memoria del runner

print(f'🦎 Backfill embeddings LOCAL · Batch {BATCH_IDX}/{TOTAL_BATCHES} · mode={MODE}', flush=True)
print(f'   Model: {MODEL_NAME} (768d)', flush=True)

# ------------------------------------------------------------------
# Carga del modelo (UNA sola vez al inicio)
# ------------------------------------------------------------------
print('⏳ Cargando sentence-transformers…', flush=True)
t_load = time.time()
try:
    from sentence_transformers import SentenceTransformer
    import torch
except ImportError as e:
    print(f'FATAL: faltan deps Python: {e}', flush=True)
    sys.exit(2)

device = 'cuda' if torch.cuda.is_available() else 'cpu'
model = SentenceTransformer(MODEL_NAME, device=device)
model.max_seq_length = 512
print(f'✅ Modelo listo en {time.time()-t_load:.1f}s · device={device}', flush=True)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def fetch_chunks(last_id: int) -> List[dict]:
    """Lee próximas N filas con id > last_id, opcionalmente filtrando por embedding NULL."""
    url = f'{SUPABASE_URL}/rest/v1/leyes_chunks'
    params = {
        'select': 'id,texto',
        'order': 'id.asc',
        'limit': str(FETCH_BATCH),
        'id': f'gt.{last_id}',
    }
    if MODE == 'null':
        params['embedding'] = 'is.null'
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    if not r.ok:
        raise RuntimeError(f'fetch fail {r.status_code}: {r.text[:200]}')
    return r.json() or []


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Codifica una lista de textos al espacio E5. Aplica el prefix 'passage:' requerido por E5."""
    prepped = [f'passage: {t[:MAX_TEXT_CHARS]}' for t in texts]
    vecs = model.encode(
        prepped,
        batch_size=ENCODE_BATCH,
        show_progress_bar=False,
        normalize_embeddings=True,  # cosine = dot product cuando normalizado
        convert_to_numpy=True,
    )
    return vecs.tolist()


def patch_embedding(chunk_id: int, vec: List[float]) -> bool:
    url = f'{SUPABASE_URL}/rest/v1/leyes_chunks'
    payload = {'embedding': vec}
    r = requests.patch(
        url,
        params={'id': f'eq.{chunk_id}'},
        headers={**HEADERS, 'Prefer': 'return=minimal'},
        json=payload,
        timeout=20,
    )
    if not r.ok:
        # Logueo loud pero NO levantamos — un solo PATCH fallido no debe matar la corrida
        print(f'⚠️  PATCH fail id={chunk_id}: {r.status_code} {r.text[:120]}', flush=True)
        return False
    return True


# ------------------------------------------------------------------
# Loop principal
# ------------------------------------------------------------------
def main():
    last_id = 0
    processed = 0
    embedded = 0
    failed = 0
    skipped = 0
    start_t = time.time()
    last_log_at = start_t

    while True:
        rows = fetch_chunks(last_id)
        if not rows:
            print(f'  [batch {BATCH_IDX}] No more rows. Done.', flush=True)
            break

        # Avanzar cursor SIEMPRE (incluso si filtramos todo)
        last_id = rows[-1]['id']

        # Filtrar a las filas de NUESTRO shard
        mine = [r for r in rows if r['id'] % TOTAL_BATCHES == BATCH_IDX]
        if not mine:
            continue

        # Filtrar textos válidos
        valid_rows = []
        valid_texts = []
        for r in mine:
            t = (r.get('texto') or '').strip()
            if len(t) < 10:
                skipped += 1
                continue
            valid_rows.append(r)
            valid_texts.append(t)

        if not valid_texts:
            continue

        # Encode en mini-batches del modelo
        try:
            vecs = embed_texts(valid_texts)
        except Exception as e:
            print(f'FATAL: embed_texts crashed: {e}', flush=True)
            sys.exit(3)

        for r, v in zip(valid_rows, vecs):
            if len(v) != 768:
                print(f'FATAL: vector dim != 768 (got {len(v)}) id={r["id"]}', flush=True)
                sys.exit(4)
            ok = patch_embedding(r['id'], v)
            if ok:
                embedded += 1
            else:
                failed += 1
            processed += 1

        # Log cada 10 segundos
        now = time.time()
        if now - last_log_at >= 10:
            elapsed = now - start_t
            rate = processed / elapsed if elapsed > 0 else 0
            print(
                f'  [batch {BATCH_IDX}] last_id={last_id} processed={processed} '
                f'embedded={embedded} failed={failed} skipped={skipped} rate={rate:.1f}/s',
                flush=True,
            )
            last_log_at = now

    elapsed = time.time() - start_t
    print(f'\n═══ SUMMARY batch {BATCH_IDX} ═══', flush=True)
    print(f'  processed: {processed}', flush=True)
    print(f'  embedded:  {embedded}', flush=True)
    print(f'  failed:    {failed}', flush=True)
    print(f'  skipped:   {skipped}', flush=True)
    print(f'  elapsed:   {elapsed:.0f}s', flush=True)
    print(f'  rate:      {processed/elapsed if elapsed > 0 else 0:.1f}/s', flush=True)

    # Si todo fallรณ, exit con error (visible en GH Actions)
    if processed > 0 and embedded == 0:
        print('FATAL: 0 embeddings persistidos pese a procesar filas. Aborting.', flush=True)
        sys.exit(5)


if __name__ == '__main__':
    main()
