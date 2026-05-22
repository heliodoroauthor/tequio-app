#!/usr/bin/env python3
"""
backfill_embeddings.py v4 (FIX-36) — gemini-embedding-001 con MRL 768d
========================================================================
Llena embeddings NULL en leyes/jurisprudencia_scjn. Usa Gemini con outputDimensionality=768.

v4 (FIX-36 2026-05-22): Loguea TODOS los smoke-test fails y resumen final
a scraper_logs (scraper_slug='backfill_embeddings') asi se puede leer
el motivo del fallo via SQL sin necesidad de acceder GitHub Actions logs.
"""
import os, sys, time, json, requests
from datetime import datetime, timezone

print("Tequio Backfill Embeddings v4 — gemini-embedding-001 (MRL 768d)")
print(f"  Python: {sys.version.split()[0]}")

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SERVICE_KEY  = os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
GEMINI_KEY   = os.environ.get('GEMINI_API_KEY', '')
GH_RUN_ID    = os.environ.get('GITHUB_RUN_ID', '')

print(f"  SUPABASE_URL: {'OK' if SUPABASE_URL else 'MISSING'} ({SUPABASE_URL[:30] if SUPABASE_URL else 'n/a'}...)")
print(f"  SERVICE_KEY: {'OK ('+str(len(SERVICE_KEY))+' chars)' if SERVICE_KEY else 'MISSING'}")
print(f"  GEMINI_KEY: {'OK ('+str(len(GEMINI_KEY))+' chars)' if GEMINI_KEY else 'MISSING'}")

if not (SUPABASE_URL and SERVICE_KEY):
    print("ERROR: SUPABASE_URL o SERVICE_KEY missing. Cannot even log error. Abortando.")
    sys.exit(1)

EMBED_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={GEMINI_KEY}"

HEADERS_SB = {
    'apikey': SERVICE_KEY,
    'Authorization': f'Bearer {SERVICE_KEY}',
    'Content-Type': 'application/json',
}

STARTED_AT = datetime.now(timezone.utc).isoformat()


def log_run(status, error_msg=None, notes_obj=None, rows_inserted=0, rows_updated=0):
    """Loguea resultado a scraper_logs (visible via SQL)."""
    try:
        payload = [{
            'scraper_slug': 'backfill_embeddings',
            'workflow_run_id': GH_RUN_ID or None,
            'status': status,
            'rows_inserted': rows_inserted,
            'rows_updated': rows_updated,
            'rows_skipped': 0,
            'fuente_url': 'gemini-embedding-001',
            'http_status': 200,
            'error_msg': (error_msg or '')[:1000] if error_msg else None,
            'notes': (json.dumps(notes_obj) if notes_obj else '')[:1000],
            'started_at': STARTED_AT,
            'finished_at': datetime.now(timezone.utc).isoformat(),
        }]
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/scraper_logs",
            headers={**HEADERS_SB, 'Prefer': 'return=minimal'},
            json=payload,
            timeout=15
        )
        if not r.ok:
            print(f"  [log_run WARN] Supabase {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  [log_run EXC] {e}")


def test_gemini():
    """Smoke test: la API key Gemini funciona?"""
    print("\n[TEST] Probando Gemini gemini-embedding-001 (768d)...")
    if not GEMINI_KEY:
        return False, "GEMINI_API_KEY missing/empty"
    try:
        r = requests.post(EMBED_URL, json={
            'model': 'models/gemini-embedding-001',
            'content': {'parts': [{'text': 'hola mundo'}]},
            'taskType': 'RETRIEVAL_DOCUMENT',
            'outputDimensionality': 768,
        }, timeout=20)
        print(f"  Status: {r.status_code}")
        body = r.text[:600]
        if not r.ok:
            print(f"  Body: {body}")
            return False, f"HTTP {r.status_code}: {body}"
        emb = r.json().get('embedding', {}).get('values', [])
        print(f"  Embedding OK: {len(emb)} dimensiones")
        if len(emb) != 768:
            return False, f"Wrong dim: got {len(emb)}, expected 768"
        return True, None
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"  EXCEPCION: {msg}")
        return False, msg


def test_supabase():
    """Smoke test: podemos leer la tabla leyes?"""
    print("\n[TEST] Probando Supabase select leyes...")
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/leyes?select=id,nombre&limit=3",
                         headers=HEADERS_SB, timeout=15)
        print(f"  Status: {r.status_code}")
        if not r.ok:
            return False, f"HTTP {r.status_code}: {r.text[:400]}"
        data = r.json()
        print(f"  Leyes traidas: {len(data)} (sample: {data[0]['nombre'][:60] if data else 'vacio'})")
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def get_embedding(text):
    """Genera embedding 768d via Gemini."""
    try:
        r = requests.post(EMBED_URL, json={
            'model': 'models/gemini-embedding-001',
            'content': {'parts': [{'text': text[:2000]}]},
            'taskType': 'RETRIEVAL_DOCUMENT',
            'outputDimensionality': 768,
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
    select_cols = id_col + ',' + ','.join(texto_cols) + ',embedding'
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{tabla}?select={select_cols}",
            headers=HEADERS_SB, timeout=30
        )
        if not r.ok:
            print(f"  [Supabase select ERR {r.status_code}] {r.text[:400]}")
            return {'actualizadas': 0, 'fallidas': 0, 'total_null': 0}
        todas = r.json()
        print(f"  Total filas en {tabla}: {len(todas)}")
        filas = [f for f in todas if f.get('embedding') is None]
        print(f"  Filas con embedding NULL: {len(filas)}")
    except Exception as e:
        print(f"  [Supabase EXC] {e}")
        return {'actualizadas': 0, 'fallidas': 0, 'total_null': 0}

    if not filas:
        print("  Nada que hacer.")
        return {'actualizadas': 0, 'fallidas': 0, 'total_null': 0}

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
        try:
            u = requests.patch(
                f"{SUPABASE_URL}/rest/v1/{tabla}?{id_col}=eq.{fila[id_col]}",
                headers={**HEADERS_SB, 'Prefer': 'return=minimal'},
                json={'embedding': emb},
                timeout=30
            )
            if u.ok:
                actualizadas += 1
                if actualizadas % 25 == 0:
                    label = str(fila.get(texto_cols[0]) or fila[id_col])[:50]
                    print(f"  [{actualizadas}] id={fila[id_col]} {label}")
            else:
                print(f"  [Update ERR {u.status_code}] {u.text[:200]}")
                fallidas += 1
        except Exception as e:
            print(f"  [Update EXC] {e}")
            fallidas += 1
        time.sleep(0.3)

    print(f"  RESUMEN {tabla}: {actualizadas} actualizadas, {fallidas} fallidas")
    return {'actualizadas': actualizadas, 'fallidas': fallidas, 'total_null': len(filas)}


def main():
    ok_sb, err_sb = test_supabase()
    if not ok_sb:
        log_run('fail', error_msg=f"test_supabase: {err_sb}", notes_obj={'phase': 'smoke_supabase'})
        print(f"ERROR: Supabase no responde: {err_sb}. Abortando.")
        sys.exit(1)

    ok_gm, err_gm = test_gemini()
    if not ok_gm:
        log_run('fail', error_msg=f"test_gemini: {err_gm}", notes_obj={'phase': 'smoke_gemini'})
        print(f"ERROR: Gemini no responde: {err_gm}. Abortando.")
        sys.exit(1)

    res_leyes = backfill_table('leyes', 'id', ['nombre', 'texto'])
    res_juris = backfill_table('jurisprudencia_scjn', 'id', ['rubro', 'texto'])

    total_actualizadas = res_leyes['actualizadas'] + res_juris['actualizadas']
    total_fallidas = res_leyes['fallidas'] + res_juris['fallidas']

    log_run(
        'ok' if total_fallidas == 0 else 'partial',
        error_msg=f"{total_fallidas} fallos" if total_fallidas else None,
        notes_obj={'leyes': res_leyes, 'jurisprudencia_scjn': res_juris},
        rows_updated=total_actualizadas
    )
    print(f"\nBackfill completo. Total actualizadas: {total_actualizadas}, fallidas: {total_fallidas}")
    print(f"rows_updated={total_actualizadas}")


if __name__ == '__main__':
    main()
