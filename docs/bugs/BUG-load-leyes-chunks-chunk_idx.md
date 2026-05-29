# 🐛 BUG: Edge Function `load-leyes-chunks` ignora `chunk_idx` y viola NOT NULL

**Fecha detección:** 2026-05-28
**Severidad:** Alta — pipeline oficial de carga roto
**Status:** Workaround SQL aplicado en producción · fix pendiente en Edge Function

---

## Resumen

La Edge Function `load-leyes-chunks` inserta filas en `public.leyes_chunks` con `chunk_idx = NULL`, violando la restricción `NOT NULL`. Todos los intentos fallan con PostgREST código `23502`.

## Reproducción

```bash
curl -X POST https://mhsuihwjgtzxflesbnxv.supabase.co/functions/v1/load-leyes-chunks \
  -H "Content-Type: application/json" -H "apikey: <anon-key>" \
  -d '{"ley_nombre":"TEST","ley_id":"TEST_X","chunks":[{"chunk_idx":0,"texto":"prueba","articulo_num":1,"titulo":"X","capitulo":"Y"}]}'
```

Respuesta:
```json
{"error":"postgrest failed","detail":{"code":"23502","message":"null value in column \"chunk_idx\" of relation \"leyes_chunks\" violates not-null constraint"}}
```

## Análisis

La columna `leyes_chunks.chunk_idx` es `INTEGER NOT NULL` con `UNIQUE(ley_id, chunk_idx)`. La Edge Function:

1. Recibe `chunks: [{...}]` en el payload
2. **No respeta el `chunk_idx` enviado en cada chunk**
3. **No asigna `chunk_idx` automáticamente** (debería usar `row_number()` o contador)
4. `articulo_num` también se mapea a `NULL` aunque el payload lo incluya

## Workaround en producción

Dos RPCs `SECURITY DEFINER` creadas (callables por anon vía REST):

```sql
public.leyes_chunks_bulk_insert(p_ley_id BIGINT, p_ley_nombre TEXT, p_start_idx INT, p_chunks JSONB) RETURNS INT
public.leyes_chunks_replace_all(p_ley_id BIGINT, p_ley_nombre TEXT, p_chunks JSONB) RETURNS INT
```

Asignan `chunk_idx` con `row_number() OVER ()`. Permitieron cargar 29 leyes NL (1,654 chunks) en sesión 2026-05-28.

## Fix propuesto

```typescript
const { ley_id, ley_nombre, chunks, start_idx = 0 } = await req.json();
const rows = chunks.map((c, i) => ({
  ley_id, ley_nombre,
  chunk_idx: c.chunk_idx ?? (start_idx + i),  // FIX 1
  articulo_num: c.articulo_num,                // FIX 2
  titulo: c.titulo, capitulo: c.capitulo,
  texto: c.texto, caracteres: c.texto.length,
}));
const { error } = await supabase.from('leyes_chunks').insert(rows);
```

## Cómo aplicar

Las Edge Functions no están en git (`supabase/functions/` no existe). Opciones:

1. Editar en Supabase Dashboard → Edge Functions → `load-leyes-chunks`
2. **Recomendado:** agregar `supabase/functions/load-leyes-chunks/index.ts` al repo + `supabase functions deploy` → permite versionar y fixar vía gh-replace-string en el futuro

## Impacto

- Pipeline oficial de carga roto
- Workaround permitió cargar 1,654 chunks NL hoy
- Sin fix, las cargas batch futuras (~248 shells NL + ~224 leyes nacional pendientes) están bloqueadas
