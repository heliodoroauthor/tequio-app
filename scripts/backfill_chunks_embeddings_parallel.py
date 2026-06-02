#!/usr/bin/env python3
"""
backfill_chunks_embeddings_parallel.py
=======================================
Llena embeddings NULL en leyes_chunks paralelizado por batch.
Cada job procesa WHERE id % total == batch_idx.

Uso: python3 backfill_chunks_embeddings_parallel.py <batch_idx> <total_batches>
Ejemplo: python3 backfill_chunks_embeddings_parallel.py 0 10
"""
import os, sys, time, json, requests
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')

if not all([SUPABASE_URL, SERVICE_KEY, GEMINI_KEY]):
    print('ERROR: faltan vars SUPABASE_URL/SERVICE_KEY/GEMINI_KEY')
    sys.exit(1)

BATCH_IDX = int(sys.argv[1]) if len(sys.argv) > 1 else 0
TOTAL_BATCHES = int(sys.argv[2]) if len(sys.argv) > 2 else 10

EMBED_URL = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={GEMINI_KEY}'
HEADERS = {'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}', 'Content-Type': 'application/json'}

CHUNK_BATCH_SIZE = 50  # leer chunks en lotes
SLEEP_AFTER_ERROR = 2
MAX_RETRIES = 3

print(f'🦎 Backfill embeddings parallel · Batch {BATCH_IDX}/{TOTAL_BATCHES}', flush=True)


def get_embedding(text):
    """Llama Gemini para 1 chunk"""
    text = (text or '').strip()
    if len(text) < 10:
        return None
    text = text[:9000]  # limit input
    
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(EMBED_URL, json={
                'model': 'models/gemini-embedding-001',
                'content': {'parts': [{'text': text}]},
                'taskType': 'RETRIEVAL_DOCUMENT',
                'outputDimensionality': 768,
            }, timeout=20)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if not r.ok:
                return None
            emb = r.json().get('embedding', {}).get('values', [])
            if len(emb) == 768:
                return emb
        except Exception as e:
            time.sleep(SLEEP_AFTER_ERROR)
    return None


def fetch_chunks_batch(offset):
    """Lee chunks sin embedding correspondientes a este batch"""
    url = f'{SUPABASE_URL}/rest/v1/leyes_chunks'
    params = {
        'select': 'id,texto',
        'embedding': 'is.null',
        'order': 'id.asc',
        'limit': str(CHUNK_BATCH_SIZE),
    }
    headers = {**HEADERS, 'Range-Unit': 'items', 'Range': f'{offset}-{offset + CHUNK_BATCH_SIZE - 1}'}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if not r.ok:
            return []
        return r.json() or []
    except Exception:
        return []


def update_embedding(chunk_id, embedding):
    """UPDATE single chunk"""
    url = f'{SUPABASE_URL}/rest/v1/leyes_chunks'
    payload = {'embedding': embedding}
    try:
        r = requests.patch(
            url,
            params={'id': f'eq.{chunk_id}'},
            headers=HEADERS,
            json=payload,
            timeout=12
        )
        return r.ok
    except Exception:
        return False


def main():
    processed = 0
    embedded = 0
    failed = 0
    skipped = 0
    offset = 0
    start_t = time.time()
    
    while True:
        chunks = fetch_chunks_batch(offset)
        if not chunks:
            print(f'  [batch {BATCH_IDX}] No more chunks. Done.', flush=True)
            break
        
        for c in chunks:
            chunk_id = c['id']
            # Skip si no es nuestro batch
            if chunk_id % TOTAL_BATCHES != BATCH_IDX:
                continue
            
            texto = c.get('texto', '')
            if not texto or len(texto.strip()) < 10:
                skipped += 1
                continue
            
            emb = get_embedding(texto)
            if not emb:
                failed += 1
                continue
            
            if update_embedding(chunk_id, emb):
                embedded += 1
            else:
                failed += 1
            
            processed += 1
            
            if processed % 100 == 0:
                elapsed = time.time() - start_t
                rate = processed / elapsed if elapsed > 0 else 0
                print(f'  [batch {BATCH_IDX}] processed={processed} embedded={embedded} failed={failed} skipped={skipped} rate={rate:.1f}/s', flush=True)
        
        offset += CHUNK_BATCH_SIZE
        
        # Avanzar más rápido si nada en este batch
        if processed == 0 and offset > 100000:
            print(f'  [batch {BATCH_IDX}] No matches found in first 100k. Probably done.', flush=True)
            break
    
    elapsed = time.time() - start_t
    print(f'\n═══ SUMMARY batch {BATCH_IDX} ═══', flush=True)
    print(f'  processed: {processed}', flush=True)
    print(f'  embedded:  {embedded}', flush=True)
    print(f'  failed:    {failed}', flush=True)
    print(f'  skipped:   {skipped}', flush=True)
    print(f'  elapsed:   {elapsed:.0f}s', flush=True)


if __name__ == '__main__':
    main()
