// embed-chunks-cycle v4 · BATCH_SIZE 50 directo a Gemini
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
const SUPABASE_URL = Deno.env.get("SUPABASE_URL") || "";
const SERVICE_ROLE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
const MODEL = "gemini-embedding-001";
const BATCH_SIZE = 50;
function json(s: number, d: unknown): Response { return new Response(JSON.stringify(d), { status: s, headers: { "Content-Type": "application/json" }}); }
async function loadKey(): Promise<string> {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/rpc/get_gemini_key`, {
    method: "POST",
    headers: { apikey: SERVICE_ROLE, Authorization: `Bearer ${SERVICE_ROLE}`, "Content-Type": "application/json" },
    body: "{}"
  });
  if (!r.ok) return "";
  const t = await r.text();
  try { const j = JSON.parse(t); return typeof j === "string" ? j : String(j); }
  catch { return t.replace(/^"|"$/g, "").trim(); }
}
Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204 });
  const apiKey = await loadKey();
  if (!apiKey || apiKey.length < 20) return json(500, { error: "key" });
  const headers = { apikey: SERVICE_ROLE, Authorization: `Bearer ${SERVICE_ROLE}`, "Content-Type": "application/json" };
  const selRes = await fetch(`${SUPABASE_URL}/rest/v1/leyes_chunks?select=id,texto,articulo_num,ley_nombre&embedding=is.null&order=id.asc&limit=${BATCH_SIZE}`, { headers });
  if (!selRes.ok) return json(selRes.status, { error: "select failed" });
  const rows = await selRes.json() as Array<any>;
  if (!rows.length) return json(200, { processed: 0, remaining: 0, done: true });
  const texts = rows.map(r => {
    const prefix = r.articulo_num ? `Artículo ${r.articulo_num} de ${r.ley_nombre || ""}: ` : "";
    return (prefix + (r.texto || "")).slice(0, 2000);
  });
  const embedBody = { requests: texts.map(text => ({ model: `models/${MODEL}`, content: { parts: [{ text }] }, taskType: "RETRIEVAL_DOCUMENT", outputDimensionality: 768 })) };
  let embeddings: number[][] = [];
  for (let attempt = 0; attempt < 3; attempt++) {
    const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:batchEmbedContents?key=${apiKey}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(embedBody)
    });
    if (r.ok) { const d = await r.json(); embeddings = (d?.embeddings || []).map((e: any) => e.values || []); break; }
    if (r.status !== 429 || attempt === 2) { const t = await r.text(); return json(r.status, { error: "gemini", detail: t.slice(0, 200), status: r.status }); }
    await new Promise(rs => setTimeout(rs, 15000));
  }
  if (embeddings.length !== rows.length) return json(500, { error: "count mismatch" });
  let updated = 0, errs = 0;
  for (let i = 0; i < rows.length; i++) {
    const vec = embeddings[i];
    if (!Array.isArray(vec) || vec.length !== 768) { errs++; continue; }
    const vecStr = "[" + vec.join(",") + "]";
    const upRes = await fetch(`${SUPABASE_URL}/rest/v1/leyes_chunks?id=eq.${rows[i].id}`, {
      method: "PATCH", headers: { ...headers, Prefer: "return=minimal" }, body: JSON.stringify({ embedding: vecStr })
    });
    if (upRes.ok) updated++; else errs++;
  }
  const remRes = await fetch(`${SUPABASE_URL}/rest/v1/leyes_chunks?select=id&embedding=is.null&limit=1`, { headers: { ...headers, Prefer: "count=exact" }});
  const cr = remRes.headers.get("content-range") || "";
  const remaining = parseInt(cr.split("/")[1] || "0", 10) || 0;
  return json(200, { processed: updated, errors: errs, remaining, done: remaining === 0, batch_size: BATCH_SIZE });
});
