# DIA-SERVIDOR — Checklist 5-pasos para revivir el asistente IA

**Pre-requisito**: el servidor propio con e5-base local + LLM (probablemente Llama 3 o similar)
ya esta corriendo y expone dos endpoints HTTP internos:
- `POST /embed` → `{texts: string[]}` → `{vectors: number[][]}` (768d normalized e5)
- `POST /chat` → `{messages: ...}` → respuesta en formato Anthropic-compatible

Si el servidor todavia no esta, este doc no aplica. Volver cuando este montado.

---

## Paso 1 — Variables de entorno en Vercel

```
SERVER_PROPIO_URL=https://servidor-tequio.example/...  (URL interna del servidor)
SERVER_PROPIO_TOKEN=<bearer token compartido>
ASISTENTE_PAUSADO=false                                (CRITICO: cambia 503 a flujo normal)
```

Mantener tambien (legacy fallback opcional):
```
GEMINI_API_KEY=  (vacio o ausente cuando el servidor propio funciona)
SUPABASE_URL, SUPABASE_ANON_KEY  (sin cambios)
```

Despues de `vercel env add` redeploy obligatorio.

---

## Paso 2 — Reemplazar el embed call en api/ai-proxy.js

Buscar (linea ~280-310):
```js
const EMBED_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent';
...
const embedR = await fetch(EMBED_ENDPOINT + '?key=' + apiKey, {...gemini stuff...});
```

Reemplazar por:
```js
// CRITICAL: prefijo "query:" para e5 (asimetria pasaje/query)
const queryText = `query: ${lastUserText.slice(0, 4000)}`;
const embedR = await fetch(process.env.SERVER_PROPIO_URL + '/embed', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${process.env.SERVER_PROPIO_TOKEN}`,
  },
  body: JSON.stringify({ texts: [queryText] }),
});
if (!embedR.ok) throw new Error(`embed ${embedR.status}`);
const embedJson = await embedR.json();
const queryVec = embedJson.vectors[0];  // 768d normalized
```

**No olvidar `query:` prefix**. Sin el, recall degrada en silencio (no falla).

---

## Paso 3 — Verificar que el RPC `match_legal_documents` espera 768d e5

Ejecutar en Supabase SQL Editor:
```sql
SELECT
  proname,
  pg_catalog.pg_get_function_arguments(oid) AS args
FROM pg_proc
WHERE proname = 'match_legal_documents';
```

El primer argumento debe ser `vector(768)`. Si esta como `vector(1536)` o
distinto, hay que migrarlo. Probablemente ya esta en 768 porque el backfill
e5-base reescribio todo a 768d.

Sanity check rapido:
```sql
SELECT
  COUNT(*) FILTER (WHERE embedding_model = 'e5-base') AS e5,
  COUNT(*) FILTER (WHERE embedding_model = 'gemini-mrl-768') AS gemini_viejo,
  COUNT(*) FILTER (WHERE embedding_model IS NULL OR embedding IS NULL) AS sin_embedding
FROM leyes_chunks;
```

Si quedan rows con `gemini-mrl-768` o NULL, correr el backfill MASSIVE primero.

---

## Paso 4 — Reemplazar el chat call (gemini generateContent → server propio)

Buscar (linea ~400-450):
```js
const geminiR = await fetch(GEMINI_ENDPOINT + '?key=' + apiKey, {...});
```

Reemplazar por:
```js
const chatR = await fetch(process.env.SERVER_PROPIO_URL + '/chat', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${process.env.SERVER_PROPIO_TOKEN}`,
  },
  body: JSON.stringify({
    messages: [
      { role: 'system', content: systemPrompt },  // ya incluye contexto RAG
      ...userMessages,
    ],
    max_tokens: 2048,
  }),
});
if (!chatR.ok) throw new Error(`chat ${chatR.status}`);
const chatJson = await chatR.json();
```

Mantener formato Anthropic-compatible en la respuesta para no romper el frontend.

---

## Paso 5 — Smoke test del asistente revivido

Antes de quitar el gate, probar con `ASISTENTE_PAUSADO=true` aun activo, usando
un override de header en testing:

```bash
# 1) Verificar gate aun activo en publico
curl -X POST https://tequio.app/api/ai-proxy \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hola"}]}'
# Debe responder 503

# 2) Setear ASISTENTE_PAUSADO=false y redeploy
# (en Vercel dashboard)

# 3) Probar el flujo end-to-end
curl -X POST https://tequio.app/api/ai-proxy \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"que dice el articulo 123 constitucional sobre jornada laboral"}]}'
# Debe devolver una respuesta con citas a leyes/articulos especificos

# 4) Frontend (en navegador): abrir /panel/asistente-ia.html
#    - Quitar el banner "Pausado" del HTML si tiene gate hardcoded.
#    - Probar la query desde el chat UI.

# 5) Network tab: verificar que /api/ai-proxy responde 200 (no 503)
#    y el body tiene `content` con la respuesta real.
```

---

## Rollback de emergencia

Si algo se rompe en prod despues de Paso 5:

```
vercel env add ASISTENTE_PAUSADO production
# valor: true
vercel --prod
```

Esto restaura el gate 503 sin necesidad de revertir codigo. Documenta en
`docs/bugs/postmortem_*.md` que paso.

---

## Mantenimiento continuo

- **Goteo diario embeddings**: workflow `embeddings-trickle-e5.yml` ya corre cron
  diario a las 07:00 UTC. Embebe rows nuevas con e5-base. Sin tocar.
- **Servidor propio caido**: monitorear `/embed` y `/chat` con healthcheck. Si
  baja, el frontend del asistente debe degradar a "Servicio temporalmente
  no disponible, reintenta en X minutos" en lugar de 500.
- **Prefijo asimetrico e5**: documentado en `scripts/README.md`. Pasaje = `passage:`,
  query = `query:`. Sin el prefijo correcto, recall baja 10-20%.

Refs: issue #2, Phase 2.6 (honestidad asistente), task #66.
