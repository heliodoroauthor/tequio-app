// /api/ai-proxy.js
// Vercel Serverless Function — proxy entre la app Tequio (formato Anthropic)
// y la API de Gemini con RAG sobre leyes mexicanas en Supabase.
//
// Variables de entorno requeridas:
//   GEMINI_API_KEY       – clave Gemini bound a service account
//   SUPABASE_URL         – ej. https://<id>.supabase.co
//   SUPABASE_ANON_KEY    – clave anon pública (con RLS read-only)
//
// Flujo:
//   1) Genera embedding de la última pregunta del usuario (gemini-embedding-001 MRL 768d)
//   2) Llama al RPC match_legal_documents en Supabase para top-5 leyes/jurisprudencias
//   3) Inyecta esas fuentes como contexto adicional al system prompt
//   4) Llama a Gemini generateContent
//   5) Devuelve respuesta en formato Anthropic

const GEMINI_MODEL = process.env.GEMINI_MODEL || 'gemini-2.5-flash';
const GEMINI_ENDPOINT = `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`;
const EMBED_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent';
const RAG_TOP_K = 5;
const RAG_MAX_CONTEXT_CHARS = 4000;

function contentToText(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .filter(b => b && (b.type === 'text' || typeof b === 'string'))
      .map(b => (typeof b === 'string' ? b : b.text || ''))
      .join('\n');
  }
  return String(content || '');
}

// ── RAG: get top-K legal documents matching the user's query ──
async function searchLegalContext(apiKey, queryText) {
  const supaUrl = process.env.SUPABASE_URL;
  const supaKey = process.env.SUPABASE_ANON_KEY;
  if (!supaUrl || !supaKey || !queryText) return [];

  try {
    // 1) Generate embedding via Gemini gemini-embedding-001 (text-embedding-004 deprecado)
    const embedRes = await fetch(`${EMBED_ENDPOINT}?key=${apiKey}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'models/gemini-embedding-001',
        content: { parts: [{ text: queryText.substring(0, 2000) }] },
        taskType: 'RETRIEVAL_QUERY',
        outputDimensionality: 768,
      }),
    });
    if (!embedRes.ok) return [];
    const embedData = await embedRes.json();
    const embedding = embedData?.embedding?.values;
    if (!Array.isArray(embedding)) return [];

    // 2) Call Supabase RPC match_legal_chunks_hybrid (chunks FTS + leyes vector)
    const rpcRes = await fetch(`${supaUrl}/rest/v1/rpc/match_legal_chunks_hybrid`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        apikey: supaKey,
        Authorization: `Bearer ${supaKey}`,
      },
      body: JSON.stringify({ query_embedding: embedding, query_text: queryText.substring(0, 2000), match_count: RAG_TOP_K }),
    });
    if (!rpcRes.ok) return [];
    const docs = await rpcRes.json();
    return Array.isArray(docs) ? docs : [];
  } catch (err) {
    // RAG failure should NOT break the AI call — degrade gracefully
    console.error('RAG error:', err.message);
    return [];
  }
}

function buildRagSystem(originalSystem, docs) {
  if (!docs || docs.length === 0) return originalSystem;

  let context = '\n\n📚 FUENTES LEGALES MEXICANAS (texto literal del PDF oficial · Cero Invención):\n';
  let usedChars = 0;
  for (let i = 0; i < docs.length; i++) {
    const d = docs[i];
    let header;
    if (d.tipo === 'articulo' && d.articulo) {
      header = `── Artículo ${d.articulo} de ${d.titulo || ''} ──`;
    } else if (d.tipo === 'ley') {
      header = `── LEY ${i + 1}: ${d.titulo || ''} (referencia, sin texto completo) ──`;
    } else {
      header = `── ${(d.tipo || 'FUENTE').toUpperCase()} ${i + 1}: ${d.titulo || ''} ──`;
    }
    const block = `\n${header}\nFuente: ${d.fuente || ''}${d.fecha ? ' · ' + d.fecha : ''}\n${(d.texto || '').substring(0, 1200)}\n`;
    if (usedChars + block.length > RAG_MAX_CONTEXT_CHARS) break;
    context += block;
    usedChars += block.length;
  }
  context += `\n\nINSTRUCCIONES ESTRICTAS:\n1. Cuando cites un artículo, usa formato exacto: "Artículo X de [Nombre de la Ley]".\n2. Si la respuesta requiere un artículo NO incluido arriba, di literalmente: "No tengo el texto del Artículo X cargado completo" y NO inventes el contenido.\n3. Si las fuentes NO cubren la pregunta del usuario, di honestamente que no encontraste el artículo aplicable.\n4. Nunca confundas tu conocimiento general con los textos provistos. Marca claramente qué viene de las fuentes y qué es orientación general.\n`;

  return (originalSystem || '') + context;
}

function anthropicToGemini(body, systemOverride) {
  const { system, messages = [], max_tokens, temperature } = body || {};
  const finalSystem = systemOverride !== undefined ? systemOverride : system;

  const contents = [];
  for (const m of messages) {
    if (!m) continue;
    const role = m.role === 'assistant' ? 'model' : 'user';
    const text = contentToText(m.content);
    if (!text) continue;
    contents.push({ role, parts: [{ text }] });
  }

  const geminiBody = {
    contents,
    generationConfig: {
      maxOutputTokens: typeof max_tokens === 'number' ? max_tokens : 1024,
      temperature: typeof temperature === 'number' ? temperature : 0.7,
    },
  };

  if (finalSystem && typeof finalSystem === 'string' && finalSystem.trim()) {
    geminiBody.system_instruction = { parts: [{ text: finalSystem }] };
  }

  geminiBody.safetySettings = [
    { category: 'HARM_CATEGORY_HARASSMENT', threshold: 'BLOCK_ONLY_HIGH' },
    { category: 'HARM_CATEGORY_HATE_SPEECH', threshold: 'BLOCK_ONLY_HIGH' },
    { category: 'HARM_CATEGORY_SEXUALLY_EXPLICIT', threshold: 'BLOCK_ONLY_HIGH' },
    { category: 'HARM_CATEGORY_DANGEROUS_CONTENT', threshold: 'BLOCK_ONLY_HIGH' },
  ];

  return geminiBody;
}

function geminiToAnthropic(data, originalModel, docs) {
  let text = '';
  const cand = data?.candidates?.[0];
  if (cand?.content?.parts?.length) {
    text = cand.content.parts.map(p => p.text || '').join('');
  }
  if (!text && cand?.finishReason && cand.finishReason !== 'STOP') {
    text = `[Respuesta no disponible. Razón: ${cand.finishReason}. Reformula tu pregunta.]`;
  }
  if (!text && data?.promptFeedback?.blockReason) {
    text = `[Tu pregunta fue bloqueada por filtros de seguridad: ${data.promptFeedback.blockReason}.]`;
  }
  if (!text) text = 'Lo siento, no pude generar una respuesta. Intenta reformular.';

  return {
    id: 'msg_' + Date.now().toString(36),
    type: 'message',
    role: 'assistant',
    model: originalModel || GEMINI_MODEL,
    content: [{ type: 'text', text }],
    stop_reason: cand?.finishReason === 'STOP' ? 'end_turn' : 'stop_sequence',
    usage: {
      input_tokens: data?.usageMetadata?.promptTokenCount || 0,
      output_tokens: data?.usageMetadata?.candidatesTokenCount || 0,
    },
    // Custom Tequio metadata: which laws were retrieved for this answer
    tequio_sources: (docs || []).map(d => ({
      tipo: d.tipo,
      titulo: d.titulo,
      articulo: d.articulo || null,
      fuente: d.fuente,
      fecha: d.fecha,
      similarity: d.similarity,
    })),
  };
}

// ── Rate limit por IP usando Supabase RPC ──
const crypto = require('crypto');
const RATE_LIMIT_PER_MIN = parseInt(process.env.AI_RATE_LIMIT_PER_MIN || '10', 10);

function getClientIp(req) {
  return (
    (req.headers['x-forwarded-for'] || '').split(',')[0].trim() ||
    req.headers['x-real-ip'] ||
    (req.socket && req.socket.remoteAddress) ||
    'unknown'
  );
}

function hashIp(ip) {
  return crypto.createHash('sha256').update(String(ip)).digest('hex').substring(0, 32);
}

async function checkRateLimit(ip) {
  const supaUrl = process.env.SUPABASE_URL;
  const supaKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_ANON_KEY;
  if (!supaUrl || !supaKey) return { ok: true };
  try {
    const r = await fetch(`${supaUrl}/rest/v1/rpc/ai_proxy_check_rate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        apikey: supaKey,
        Authorization: `Bearer ${supaKey}`,
      },
      body: JSON.stringify({
        p_ip_hash: hashIp(ip),
        p_limit: RATE_LIMIT_PER_MIN,
        p_window_sec: 60,
      }),
    });
    if (!r.ok) return { ok: true };
    return await r.json();
  } catch (e) {
    console.warn('[rate-limit] error:', e.message);
    return { ok: true };
  }
}

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, x-api-key, anthropic-version');

  if (req.method === 'OPTIONS') { res.status(200).end(); return; }
  if (req.method !== 'POST') {
    res.status(405).json({ error: { type: 'method_not_allowed', message: 'Only POST allowed' } });
    return;
  }

  // Rate limit por IP
  const clientIp = getClientIp(req);
  const rateCheck = await checkRateLimit(clientIp);
  if (!rateCheck.ok) {
    res.setHeader('Retry-After', String(rateCheck.retry_after_sec || 60));
    res.setHeader('X-RateLimit-Limit', String(rateCheck.limit || RATE_LIMIT_PER_MIN));
    res.setHeader('X-RateLimit-Remaining', '0');
    res.status(429).json({
      error: {
        type: 'rate_limited',
        message: `Demasiadas consultas. Espera ${rateCheck.retry_after_sec || 60}s y reintenta. Límite: ${rateCheck.limit}/min.`,
        retry_after_sec: rateCheck.retry_after_sec || 60,
      },
    });
    return;
  }

  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    res.status(500).json({
      error: { type: 'config_error', message: 'GEMINI_API_KEY no configurada' },
    });
    return;
  }

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch { body = {}; }
  }

  try {
    // 1) Extract the user's last question for RAG retrieval
    let lastUserText = '';
    const msgs = Array.isArray(body?.messages) ? body.messages : [];
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i]?.role === 'user') {
        lastUserText = contentToText(msgs[i].content);
        break;
      }
    }

    // 2) Pull relevant legal docs (degrades silently if RAG unavailable)
    const docs = await searchLegalContext(apiKey, lastUserText);

    // 3) Inject docs into the system prompt
    const enrichedSystem = buildRagSystem(body?.system, docs);

    // 4) Build Gemini request
    const geminiBody = anthropicToGemini(body, enrichedSystem);

    // 5) Call Gemini
    const upstream = await fetch(`${GEMINI_ENDPOINT}?key=${apiKey}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(geminiBody),
    });
    const data = await upstream.json();

    if (!upstream.ok) {
      res.status(upstream.status).json({
        type: 'error',
        error: {
          type: 'api_error',
          message: data?.error?.message || `Gemini API error (status ${upstream.status})`,
        },
      });
      return;
    }

    res.status(200).json(geminiToAnthropic(data, body?.model, docs));
  } catch (err) {
    res.status(500).json({
      type: 'error',
      error: { type: 'internal_error', message: err?.message || 'Error desconocido' },
    });
  }
};
