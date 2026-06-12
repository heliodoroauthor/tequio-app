// v2: search por nombre canal
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
const SUPABASE_URL = Deno.env.get("SUPABASE_URL") || "";
const SERVICE_ROLE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";

Deno.serve(async () => {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/rpc/get_youtube_key`, {
    method: "POST",
    headers: { apikey: SERVICE_ROLE, Authorization: `Bearer ${SERVICE_ROLE}`, "Content-Type": "application/json" },
    body: "{}"
  });
  const t = await r.text();
  let key = "";
  try { const j = JSON.parse(t); key = typeof j === "string" ? j : String(j); }
  catch { key = t.replace(/^"|"$/g, "").trim(); }

  const queries = [
    { label: "Cámara de Diputados México", q: "Canal del Congreso México" },
    { label: "Senado México",                q: "Senado de la República México oficial" },
    { label: "SCJN México",                  q: "Suprema Corte de Justicia México SCJN canal oficial" },
    { label: "INE México",                   q: "INE México Instituto Nacional Electoral oficial" }
  ];

  const results: Array<any> = [];
  for (const { label, q } of queries) {
    const url = `https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q=${encodeURIComponent(q)}&maxResults=3&key=${key}`;
    const cr = await fetch(url);
    const data = await cr.json();
    if (data?.error) {
      results.push({ label, error: data.error.message });
      continue;
    }
    const top = (data?.items || []).map((i: any) => ({
      channel_id: i.snippet?.channelId || i.id?.channelId,
      title: i.snippet?.title,
      description: (i.snippet?.description || "").slice(0, 120)
    }));
    results.push({ label, top });
  }

  return new Response(JSON.stringify({ results }, null, 2), { headers: { "Content-Type": "application/json" }});
});
