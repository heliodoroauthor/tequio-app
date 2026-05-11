// /api/ai-proxy.js
// Vercel Serverless Function — proxy entre la app Tequio (formato Anthropic)
// y la API de Gemini (Google AI Studio). Permite mantener TODO el código
// de la app sin cambios, sólo redirigiendo la URL.
//
// Variables de entorno requeridas:
//   GEMINI_API_KEY   – clave creada en https://aistudio.google.com/apikey
//
// Body esperado (estilo Anthropic):
//   { model, max_tokens, system, messages: [{role:'user'|'assistant', content:string|array}] }
//
// Body devuelto (estilo Anthropic):
//   { content: [{ type:'text', text:'...' }] }

const GEMINI_MODEL = process.env.GEMINI_MODEL || 'gemini-2.5-flash';
const GEMINI_ENDPOINT = `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`;

// Convierte el contenido de un mensaje Anthropic a texto plano.
// Anthropic puede mandar content como string o como [{type:'text', text:'...'}]
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

// Convierte el body estilo Anthropic a formato Gemini
function anthropicToGemini(body) {
  const { system, messages = [], max_tokens, temperature } = body || {};

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

  // system prompt
  if (system && typeof system === 'string' && system.trim()) {
    geminiBody.system_instruction = { parts: [{ text: system }] };
  }

  // Safety settings — permitir contenido cívico/legal sin bloqueos excesivos
  geminiBody.safetySettings = [
    { category: 'HARM_CATEGORY_HARASSMENT', threshold: 'BLOCK_ONLY_HIGH' },
    { category: 'HARM_CATEGORY_HATE_SPEECH', threshold: 'BLOCK_ONLY_HIGH' },
    { category: 'HARM_CATEGORY_SEXUALLY_EXPLICIT', threshold: 'BLOCK_ONLY_HIGH' },
    { category: 'HARM_CATEGORY_DANGEROUS_CONTENT', threshold: 'BLOCK_ONLY_HIGH' },
  ];

  return geminiBody;
}

// Convierte la respuesta de Gemini al formato Anthropic
function geminiToAnthropic(data, originalModel) {
  let text = '';

  const cand = data?.candidates?.[0];
  if (cand?.content?.parts?.length) {
    text = cand.content.parts.map(p => p.text || '').join('');
  }

  // Si Gemini bloqueó por seguridad, dar un mensaje claro
  if (!text && cand?.finishReason && cand.finishReason !== 'STOP') {
    text = `[Respuesta no disponible. Razón: ${cand.finishReason}. Reformula tu pregunta o consulta directamente a un profesional.]`;
  }

  if (!text && data?.promptFeedback?.blockReason) {
    text = `[Tu pregunta fue bloqueada por filtros de seguridad: ${data.promptFeedback.blockReason}. Reformúlala con otras palabras.]`;
  }

  if (!text) {
    text = 'Lo siento, no pude generar una respuesta. Intenta reformular la pregunta.';
  }

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
  };
}

module.exports = async function handler(req, res) {
  // CORS — permitir desde cualquier origen (la app puede correr en preview de Vercel también)
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, x-api-key, anthropic-version');

  if (req.method === 'OPTIONS') {
    res.status(200).end();
    return;
  }

  if (req.method !== 'POST') {
    res.status(405).json({ error: { type: 'method_not_allowed', message: 'Only POST allowed' } });
    return;
  }

  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    res.status(500).json({
      error: { type: 'config_error', message: 'GEMINI_API_KEY no configurada en variables de entorno' },
    });
    return;
  }

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch { body = {}; }
  }

  try {
    const geminiBody = anthropicToGemini(body);

    const upstream = await fetch(`${GEMINI_ENDPOINT}?key=${apiKey}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(geminiBody),
    });

    const data = await upstream.json();

    if (!upstream.ok) {
      // Errores de Gemini — mapeados a formato Anthropic
      res.status(upstream.status).json({
        type: 'error',
        error: {
          type: 'api_error',
          message: data?.error?.message || `Gemini API error (status ${upstream.status})`,
        },
      });
      return;
    }

    const anthropicResp = geminiToAnthropic(data, body?.model);
    res.status(200).json(anthropicResp);
  } catch (err) {
    res.status(500).json({
      type: 'error',
      error: { type: 'internal_error', message: err?.message || 'Error desconocido' },
    });
  }
}
