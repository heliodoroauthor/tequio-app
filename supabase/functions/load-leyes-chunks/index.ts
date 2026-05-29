// Edge Function: load-leyes-chunks
// Fix del bug documentado en docs/bugs/BUG-load-leyes-chunks-chunk_idx.md (2026-05-28)
//
// CAMBIOS RESPECTO A LA VERSIÓN ANTERIOR:
//   1. Respeta chunk_idx del payload si viene; si no, lo asigna secuencial
//   2. Respeta articulo_num del payload (antes lo mapeaba a NULL)
//   3. Acepta start_idx opcional para append en batches
//   4. Mejor manejo de errores con detalles del INSERT
//
// DEPLOY:
//   supabase functions deploy load-leyes-chunks --project-ref mhsuihwjgtzxflesbnxv
//
// VERIFICACIÓN POST-DEPLOY:
//   curl -X POST https://mhsuihwjgtzxflesbnxv.supabase.co/functions/v1/load-leyes-chunks \
//     -H "Content-Type: application/json" \
//     -H "apikey: <anon>" \
//     -d \'{"ley_id":3200,"ley_nombre":"TEST","chunks":[{"chunk_idx":0,"articulo_num":"1","texto":"prueba"}]}\'
//   Esperado: {"ok":true,"inserted":1}

import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

serve(async (req: Request) => {
  // CORS preflight
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CORS });
  }

  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "POST only" }), {
      status: 405,
      headers: { ...CORS, "Content-Type": "application/json" },
    });
  }

  try {
    const body = await req.json();
    const { ley_id, ley_nombre, chunks, start_idx = 0 } = body;

    // Validación
    if (!ley_nombre) {
      return jsonError("ley_nombre requerido", 400);
    }
    if (!Array.isArray(chunks) || chunks.length === 0) {
      return jsonError("chunks vacio", 400);
    }

    // Cliente con service role (bypassa RLS)
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
    );

    // Mapear chunks respetando chunk_idx y articulo_num del payload.
    // FIX bug 2026-05-28: anteriormente esto era el problema — chunk_idx y articulo_num
    // se perdían en el mapeo y quedaban como NULL.
    const rows = chunks.map((c: any, i: number) => ({
      ley_id: ley_id ?? null,
      ley_nombre,
      chunk_idx: c.chunk_idx ?? (start_idx + i),
      articulo_num: c.articulo_num ?? null,
      titulo: c.titulo ?? null,
      capitulo: c.capitulo ?? null,
      texto: c.texto,
      caracteres: c.texto ? c.texto.length : 0,
    }));

    // Validar que todos tengan texto
    const sin_texto = rows.filter((r: any) => !r.texto || r.texto.length === 0).length;
    if (sin_texto > 0) {
      return jsonError(`${sin_texto} chunks sin texto`, 400);
    }

    const { data, error } = await supabase
      .from("leyes_chunks")
      .insert(rows)
      .select("id");

    if (error) {
      console.error("INSERT error:", error);
      return new Response(
        JSON.stringify({ error: "postgrest failed", detail: error }),
        { status: 500, headers: { ...CORS, "Content-Type": "application/json" } }
      );
    }

    return new Response(
      JSON.stringify({ ok: true, inserted: data?.length ?? rows.length }),
      { status: 200, headers: { ...CORS, "Content-Type": "application/json" } }
    );
  } catch (e: any) {
    return jsonError(`exception: ${e.message ?? String(e)}`, 500);
  }
});

function jsonError(message: string, status: number) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}
