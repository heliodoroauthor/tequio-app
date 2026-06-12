#!/usr/bin/env python3
"""
backfill_embeddings_e5.py
==========================
Backfill multi-tabla de embeddings con intfloat/multilingual-e5-base (768d).

Reemplaza al pipeline Gemini quebrado por crédito agotado (~29-may-2026, issue #2).
Cero costo, cero dependencia externa, falla LOUD.

Tablas soportadas:
  - leyes_chunks       "Artículo {articulo_num} de {ley_nombre}: {texto}"
  - leyes              "{nombre} {texto}"
  - politicos_senadores "{nombre_completo} | {partido} | {entidad_federativa} | {cargo_especial}. Semblanza: {semblanza}"

Todos con prefijo "passage: " REQUERIDO por e5.

Selector:
  - mode=null: embedding_model IS NULL OR != 'e5-base'
  - mode=all: TODAS las filas

PATCH escribe {embedding: vec, embedding_model: 'e5-base'}.

Uso: python3 backfill_embeddings_e5.py <table> <batch_idx> <total_batches> [--mode=null|all]

🦎 Cero Invención · Tequio · Phase 2 · issue #2
"""
import os
import sys
import time
import requests
from typing import List, Dict

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')

if not all([SUPABASE_URL, SERVICE_KEY]):
    print('FATAL: faltan vars SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY', flush=True)
    sys.exit(1)

if len(sys.argv) < 4:
    print('FATAL: uso: python3 backfill_embeddings_e5.py <table> <batch_idx> <total_batches> [--mode=null|all]', flush=True)
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


def _passage_chunks(r: dict) -> str:
    art = (r.get('articulo_num') or '').strip()
    ley = (r.get('ley_nombre') or '').strip()
    texto = (r.get('texto') or '').strip()
    if art and ley:
        return f"Artículo {art} de {ley}: {texto}"
    if art:
        return f"Artículo {art}: {texto}"
    if ley:
        return f"{ley}: {texto}"
    return texto


def _passage_leyes(r: dict) -> str:
    nombre = (r.get('nombre') or '').strip()
    texto = (r.get('texto') or '').strip()
    if nombre and texto:
        return f"{nombre} {texto}"
    return nombre or texto


def _passage_senadores(r: dict) -> str:
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


TABLE_CONFIG: Dict[str, Dict] = {
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
    print(f'FATAL: tabla "{TABLE}" no soportada. Opciones: {list(TABLE_CONFIG.keys())}', flush=True)
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

print(f'🦎 Backfill E5 · tabla={TABLE} · batch {BATCH_IDX}/{TOTAL_BATCHES} · mode={MODE}', flush=True)
print(f'   Model: {MODEL_NAME} (768d normalized · passage prefix)', flush=True)

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


def fetch_rows() -> List[dict]:
    """Fetch desde el INICIO del selector (sin cursor). Diag prints para CI."""
    url = f'{SUPABASE_URL}/rest/v1/{TABLE}'
    params = {
        'select': CONFIG['select'],
        'order': 'id.asc',
        'limit': str(FETCH_BATCH),
    }
    if MODE == 'null':
        params['or'] = '(embedding_model.is.null,embedding_model.neq.e5-base)'
    print(f'[DIAG] fetch GET {url}', flush=True)
    print(f'[DIAG] fetch params={params}', flush=True)
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    except Exception as e:
        print(f'[DIAG] requests.get RAISED: {type(e).__name__}: {e}', flush=True)
        raise
    print(f'[DIAG] fetch resp status={r.status_code} bytes={len(r.text)}', flush=True)
    print(f'[DIAG] fetch preview={r.text[:300]!r}', flush=True)
    if not r.ok:
        raise RuntimeError(f'fetch fail {r.status_code}: {r.text[:200]}')
    try:
        j = r.json()
    except Exception as e:
        print(f'[DIAG] r.json() RAISED: {type(e).__name__}: {e}', flush=True)
        raise
    print(f'[DIAG] r.json() type={type(j).__name__} len={len(j) if hasattr(j,"__len__") else "n/a"}', flush=True)
    return j or []


def embed_texts(texts: List[str]) -> List[List[float]]:
    prepped = [f'passage: {t[:MAX_TEXT_CHARS]}' for t in texts]
    vecs = model.encode(
        prepped,
        batch_size=ENCODE_BATCH,
        show_progress_bar=False,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return vecs.tolist()


def patch_row(row_id: int, vec: List[float]) -> bool:
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
        print(f'⚠️  PATCH fail id={row_id}: {r.status_code} {r.text[:120]}', flush=True)
        return False
    return True


def main():
    print('[DIAG] main() ENTERED', flush=True)
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
        print(f'[DIAG] === main loop iter={iter_count} ===', flush=True)
        rows = fetch_rows()
        if not rows:
            print(f'  [batch {BATCH_IDX}/{TABLE}] Selector vacío. Done.', flush=True)
            break

        mine = [r for r in rows if r['id'] % TOTAL_BATCHES == BATCH_IDX]
        print(f'[DIAG] iter={iter_count} fetched={len(rows)} mine={len(mine)} no_progress={consecutive_no_progress}', flush=True)
        if not mine:
            consecutive_no_progress += 1
            if consecutive_no_progress >= MAX_NO_PROGRESS:
                print(f'  [batch {BATCH_IDX}/{TABLE}] {MAX_NO_PROGRESS} iter sin filas propias. Otros shards trabajan. Done.', flush=True)
                break
            time.sleep(2)
            continue

        consecutive_no_progress = 0

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
            continue

        try:
            vecs = embed_texts(valid_texts)
        except Exception as e:
            print(f'FATAL: embed_texts crashed: {e}', flush=True)
            sys.exit(3)

        for r, v in zip(valid_rows, vecs):
            if len(v) != 768:
                print(f'FATAL: vector dim != 768 (got {len(v)}) id={r["id"]} tabla={TABLE}', flush=True)
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
            print(
                f'  [batch {BATCH_IDX}/{TABLE}] processed={processed} '
                f'embedded={embedded} failed={failed} skipped={skipped} rate={rate:.1f}/s',
                flush=True,
            )
            last_log_at = now

    elapsed = time.time() - start_t
    print(f'\n═══ SUMMARY batch {BATCH_IDX} tabla={TABLE} ═══', flush=True)
    print(f'  processed: {processed}', flush=True)
    print(f'  embedded:  {embedded}', flush=True)
    print(f'  failed:    {failed}', flush=True)
    print(f'  skipped:   {skipped}', flush=True)
    print(f'  elapsed:   {elapsed:.0f}s', flush=True)
    print(f'  rate:      {processed/elapsed if elapsed > 0 else 0:.1f}/s', flush=True)

    if processed > 0 and embedded == 0:
        print('FATAL: 0 embeddings persistidos pese a procesar filas. Aborting.', flush=True)
        sys.exit(5)


if __name__ == '__main__':
    main()
