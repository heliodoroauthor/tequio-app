# Edge Functions de Tequio

**Importante:** las Edge Functions deployadas en Supabase NO estaban en git hasta 2026-05-28.
Esta carpeta es el inicio de su versionado. Cada vez que se modifique una función,
actualizar aquí y redeployar.

## Funciones inventariadas (deployadas en Supabase)

| Función | Status repo | Función |
|---|---|---|
| `gh-replace-string` | ❌ no en repo | Hace `replace` literal en un archivo del repo + commit. Activamente usada. |
| `gh-proxy` | ❌ no en repo | Proxy autenticado a GitHub API (usado por `public.gh()`). |
| `load-leyes-chunks` | ✅ FIX (este PR) | Inserta chunks de leyes. Fix de chunk_idx ignorado. |
| `embed-chunks-cycle` | ❌ no en repo | Genera embeddings para chunks pendientes. Llamado por cron. |
| `push-send` | ❌ no en repo | Envía push notifications. |

**TODO operacional:** versionar las otras 4 funciones (descargar desde Dashboard).

## Cómo deployar una función

Requiere [Supabase CLI](https://supabase.com/docs/guides/cli) y un access token personal.

```bash
# Login (una vez)
supabase login

# Link al proyecto
supabase link --project-ref mhsuihwjgtzxflesbnxv

# Deploy una función específica
supabase functions deploy load-leyes-chunks

# Deploy todas
supabase functions deploy
```

## Cómo probar `load-leyes-chunks` después del deploy

```bash
curl -X POST https://mhsuihwjgtzxflesbnxv.supabase.co/functions/v1/load-leyes-chunks \
  -H "Content-Type: application/json" \
  -H "apikey: <anon_key>" \
  -d \'{"ley_id":3200,"ley_nombre":"TEST","chunks":[{"chunk_idx":0,"articulo_num":"1","texto":"prueba"}]}\'
```

**Resultado esperado tras el fix:**
```json
{"ok":true,"inserted":1}
```

**Síntoma del bug actual (pre-fix):**
```json
{"error":"postgrest failed","detail":{"code":"23502","message":"null value in column \"chunk_idx\""}}
```

## Rollback

Si el fix rompe algo, el workaround SQL sigue disponible:

```sql
SELECT public.leyes_chunks_bulk_insert(
  p_ley_id => 3200,
  p_ley_nombre => \'Test\',
  p_start_idx => 0,
  p_chunks => \'[{"articulo_num":"1","texto":"abc"}]\'::jsonb
);
```

Esa RPC y `leyes_chunks_replace_all` están definidas en Postgres y son llamables
desde `anon` vía REST sin elevar privilegios.
