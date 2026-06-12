#!/usr/bin/env python3
"""
backfill_embeddings_e5.py - multi-table embeddings backfill with e5-base.
Refs issue #2 - replaces broken Gemini pipeline.
"""
import os
import sys
import time
import requests
from typing import List, Dict

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

if not all([SUPABASE_URL, SERVICE_KEY]):
    print('FATAL: faltan SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY', flush=True)
    sys.exit(1)

if len(sys.argv) < 4:
    print('FATAL: uso: backfill_embeddings_e5.py <table> <batch_idx> <total_batches> [--mode=null|all]', flush=True)
    sys.exit(1)

TABLE = sys.argv[1]
BATCH_IDX = int(sys.argv[2])
TOTAL_BATCHES = int(sys.argv[3])
MODE = 'null'
for arg in sys.argv[4:]:
    if arg.startswith('--mode='):
        MODE = arg.split('=', 1)[1].strip().lower()
        if MODE not in ('null', 'all'):
            print(f'FATAL: --mode debe ser null|all, got "{MODE}"', flush=True)
            sys.exit(1)


def _passage_chunks(r):
    art = (r.get('articulo_num') or '').strip()
    ley = (r.get('ley_nombre') or '').strip()
    texto = (r.get('texto') or '').strip()
    if art and ley:
        return f"Articulo {art} de {ley}: {texto}"
    if art:
        return f"Articulo {art}: {texto}"
    if ley:
        return f"{ley}: {texto}"
    return texto


def _passage_leyes(r):
    nombre = (r.get('nombre') or '').strip()
    texto = (r.get('texto') or '').strip()
    if nombre and texto:
        return f"{nombre} {texto}"
    return nombre or texto


def _passage_senadores(r):
    parts = []
    for col in ('nombre_completo', 'partido', 'entidad_federativa', 'cargo_especial'):
        v = (r.get(col) or '').strip()
        if v:
            parts.append(v)
    head = ' | '.join(parts)
    sem = (r.get('semblanza') or '').strip()
    if head and sem:
        return f"{head}. Semblanza: {sem}"
    if sem:
        return f"Semblanza: {sem}"
    return head


TABLE_CONFIG = {
    'leyes_chunks': {
        'select': 'id,texto,articulo_num,ley_nombre',
        'passage_builder': _passage_chunks,
        'min_text_len': 10,
    },
    'leyes': {
        'select': 'id,nombre,texto',
        'passage_builder': _passage_leyes,
        'min_text_len': 10,
    },
    'politicos_senadores': {
        'select': 'id,nombre_completo,partido,entidad_federativa,cargo_especial,semblanza',
        'passage_builder': _passage_senadores,
        'min_text_len': 5,
    },
}

if TABLE not in TABLE_CONFIG:
    print(f'FATAL: tabla "{TABLE}" no soportada', flush=True)
    sys.exit(1)

CONFIG = TABLE_CONFIG[TABLE]

HEADERS = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

MODEL_NAME = 'intfloat/multilingual-e5-base'
ENCODE_BATCH = 32
FETCH_BATCH = 200
MAX_TEXT_CHARS = 4000

print(f'Backfill E5 tabla={TABLE} batch {BATCH_IDX}/{TOTAL_BATCHES} mode={MODE}', flush=True)
print(f'  Model: {MODEL_NAME} (768d normalized passage prefix)', flush=True)

print('Cargando sentence-transformers...', flush=True)
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
print(f'Modelo listo en {time.time()-t_load:.1f}s device={device}', flush=True)


def fetch_rows():
    url = f'{SUPABASE_URL}/rest/v1/{TABLE}'
    params = {
        'select': CONFIG['select'],
        'order': 'id.asc',
        'limit': str(FETCH_BATCH),
    }
    if MODE == 'null':
        params['or'] = '(embedding_model.is.null,embedding_model.neq.e5-base)'
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    if not r.ok:
        raise RuntimeError(f'fetch fail {r.status_code}: {r.text[:200]}')
    return r.json() or []


def embed_texts(texts):
    prepped = [f'passage: {t[:MAX_TEXT_CHARS]}' for t in texts]
    vecs = model.encode(
        prepped,
        batch_size=ENCODE_BATCH,
        show_progress_bar=False,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return vecs.tolist()


def patch_row(row_id, vec):
    url = f'{SUPABASE_URL}/rest/v1/{TABLE}'
    payload = {'embedding': vec, 'embedding_model': 'e5-base'}
    r = requests.patch(
        url,
        params={'id': f'eq.{row_id}'},
        headers={**HEADERS, 'Prefer': 'return=minimal'},
        json=payload,
        timeout=20,
    )
    if not r.ok:
        print(f'PATCH fail id={row_id}: {r.status_code} {r.text[:120]}', flush=True)
        return False
    return True


def main():
    processed = 0
    embedded = 0
    failed = 0
    skipped = 0
    consecutive_no_progress = 0
    MAX_NO_PROGRESS = 20
    start_t = time.time()
    last_log_at = start_t
    iter_count = 0

    while True:
        iter_count += 1
        rows = fetch_rows()
        if not rows:
            print(f'  [batch {BATCH_IDX}/{TABLE}] Selector vacio. Done.', flush=True)
            break

        mine = [r for r in rows if r['id'] % TOTAL_BATCHES == BATCH_IDX]
        if not mine:
            consecutive_no_progress += 1
            if consecutive_no_progress >= MAX_NO_PROGRESS:
                print(f'  [batch {BATCH_IDX}/{TABLE}] {MAX_NO_PROGRESS} iter sin filas propias. Done.', flush=True)
                break
            time.sleep(2)
            continue

        valid_rows = []
        valid_texts = []
        for r in mine:
            passage = CONFIG['passage_builder'](r).strip()
            if len(passage) < CONFIG['min_text_len']:
                skipped += 1
                continue
            valid_rows.append(r)
            valid_texts.append(passage)

        if not valid_texts:
            consecutive_no_progress += 1
            if consecutive_no_progress >= MAX_NO_PROGRESS:
                print(f'  [batch {BATCH_IDX}/{TABLE}] {MAX_NO_PROGRESS} iter solo-skipped. Done.', flush=True)
                break
            time.sleep(1)
            continue

        consecutive_no_progress = 0

        try:
            vecs = embed_texts(valid_texts)
        except Exception as e:
            print(f'FATAL: embed_texts crashed: {e}', flush=True)
            sys.exit(3)

        for r, v in zip(valid_rows, vecs):
            if len(v) != 768:
                print(f'FATAL: dim != 768 id={r["id"]}', flush=True)
                sys.exit(4)
            ok = patch_row(r['id'], v)
            if ok:
                embedded += 1
            else:
                failed += 1
            processed += 1

        now = time.time()
        if now - last_log_at >= 10:
            elapsed = now - start_t
            rate = processed / elapsed if elapsed > 0 else 0
            print(f'  [batch {BATCH_IDX}/{TABLE}] processed={processed} embedded={embedded} failed={failed} skipped={skipped} rate={rate:.1f}/s', flush=True)
            last_log_at = now

    elapsed = time.time() - start_t
    print(f'\n=== SUMMARY batch {BATCH_IDX} tabla={TABLE} ===', flush=True)
    print(f'  processed: {processed}', flush=True)
    print(f'  embedded:  {embedded}', flush=True)
    print(f'  failed:    {failed}', flush=True)
    print(f'  skipped:   {skipped}', flush=True)
    print(f'  elapsed:   {elapsed:.0f}s', flush=True)

    if processed > 0 and embedded == 0:
        print('FATAL: 0 embeddings persistidos. Aborting.', flush=True)
        sys.exit(5)


if __name__ == '__main__':
    main()
