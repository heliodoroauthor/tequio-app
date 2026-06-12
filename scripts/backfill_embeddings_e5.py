#!/usr/bin/env python3
"""
backfill_embeddings_e5.py
==========================
Backfill multi-tabla de embeddings con intfloat/multilingual-e5-base (768d, sentence-transformers).

Reemplaza al pipeline Gemini que se rompió por crédito agotado (~29-may-2026).
Cero costo, cero dependencia externa, falla LOUD.

Tablas soportadas (column mapping):
  - leyes_chunks       SELECT id,texto,articulo_num,ley_nombre
                       passage: "Artículo {articulo_num} de {ley_nombre}: {texto}"
                       (preserva comportamiento del Edge Function Gemini original)
  - leyes              SELECT id,nombre,texto
                       passage: "{nombre} {texto}"
                       (preserva comportamiento de backfill_embeddings.py Gemini original)
  - politicos_senadores SELECT id,nombre_completo,partido,entidad_federativa,cargo_especial,semblanza
                       passage: "{nombre_completo} | {partido} | {entidad_federativa} | {cargo_especial}. Semblanza: {semblanza}"
                       (diseño nuevo; no había backfill previo)

Todos los pasajes llevan el prefijo "passage: " REQUERIDO por e5
(en queries se usa "query: " — debe respetarse en el embedder de queries del servidor propio).

Selector basado en embedding_model:
  - mode=null: filas con embedding_model IS NULL OR != 'e5-base' (re-embed Gemini + huérfanas)
  - mode=all:  TODAS las filas (incluido lo que ya es e5-base — para re-embed total)

PATCH escribe {embedding: vec, embedding_model: 'e5-base'} — la nueva columna se vuelve
cursor de resume gratis (shard muerto = re-correr salta lo hecho).

Uso: python3 backfill_embeddings_e5.py <table> <batch_idx> <total_batches> [--mode=null|all]
Ejemplos:
  python3 backfill_embeddings_e5.py leyes_chunks 0 10
  python3 backfill_embeddings_e5.py politicos_senadores 0 1 --mode=all

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

# ------------------------------------------------------------------
# Per-table config
# ------------------------------------------------------------------
def _passage_chunks(r: dict) -> str:
    """Preserva semántica del Gemini Edge Function original."""
    art = r.get('articulo_num')
    if art:
        return f"Artículo {art} de {r.get('ley_nombre') or ''}: {r.get('texto') or ''}"
    return r.get('texto') or ''


def _passage_leyes(r: dict) -> str:
    """Preserva semántica de backfill_embeddings.py v4 Gemini original."""
    return f"{r.get('nombre') or ''} {r.get('texto') or ''}".strip()


def _passage_senadores(r: dict) -> str:
    """Diseño nuevo: campos searchables + semblanza."""
    return (
        f"{r.get('nombre_completo') or ''} | "
        f"{r.get('partido') or ''} | "
        f"{r.get('entidad_federativa') or ''} | "
        f"{r.get('cargo_especial') or ''}. "
        f"Semblanza: {r.get('semblanza') or ''}"
    )


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

# Carga del modelo
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


def fetch_rows(last_id: int) -> List[dict]:
    """Lee próximas N filas con id > last_id, filtrando por embedding_model si mode=null."""
    url = f'{SUPABASE_URL}/rest/v1/{TABLE}'
    params = {
        'select': CONFIG['select'],
        'order': 'id.asc',
        'limit': str(FETCH_BATCH),
        'id': f'gt.{last_id}',
    }
    if MODE == 'null':
        # IS DISTINCT FROM 'e5-base' = (IS NULL) OR (!= 'e5-base')
        params['or'] = '(embedding_model.is.null,embedding_model.neq.e5-base)'
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    if not r.ok:
        raise RuntimeError(f'fetch fail {r.status_code}: {r.text[:200]}')
    return r.json() or []


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Codifica con prefix 'passage:' (REQUERIDO por e5). Normalize=True → norma 1.0."""
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
    """PATCH individual — escribe embedding + embedding_model='e5-base' (cursor de resume)."""
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
    last_id = 0
    processed = 0
    embedded = 0
    failed = 0
    skipped = 0
    start_t = time.time()
    last_log_at = start_t

    while True:
        rows = fetch_rows(last_id)
        if not rows:
            print(f'  [batch {BATCH_IDX}/{TABLE}] No more rows. Done.', flush=True)
            break

        # Cursor SIEMPRE avanza
        last_id = rows[-1]['id']

        # Shard filter (id % TOTAL_BATCHES == BATCH_IDX)
        mine = [r for r in rows if r['id'] % TOTAL_BATCHES == BATCH_IDX]
        if not mine:
            continue

        # Construir pasajes por tabla
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
                f'  [batch {BATCH_IDX}/{TABLE}] last_id={last_id} processed={processed} '
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
