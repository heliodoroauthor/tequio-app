// /api/embed-batch.js
// Endpoint serverless interno: recibe array de textos, devuelve embeddings Gemini 768-d.
// Protegido con shared secret simple para evitar abuse público.
// Uso interno: ingest de leyes_chunks · NO expone PII ni datos sensibles.

const GEMINI_EMBED_MODEL = 'text-embedding-004';
const BATCH_LIMIT = 100; // Gemini batchEmbedContents acepta hasta 100
const SHARED_SECRET = process.env.EMBED_BATCH_SECRET || 'tequio-embed-2026';

module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, x-embed-secret');

  if (req.method === 'OPTIONS') { res.status(200).end(); return; }
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Only POST' });
    return;
  }

  // Auth simple por shared secret en header
  const provided = req.headers['x-embed-secret'] || '';
  if (provided !== SHARED_SECRET) {
    res.status(401).json({ error: 'Unauthorized · missing or invalid x-embed-secret' });
    return;
  }

  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    res.status(500).json({ error: 'GEMINI_API_KEY no configurada' });
    return;
  }

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch { body = {}; }
  }

  const texts = Array.isArray(body?.texts) ? body.texts : [];
  if (!texts.length) {
    res.status(400).json({ error: 'texts requerido (array no vacío)' });
    return;
  }
  if (texts.length > BATCH_LIMIT) {
    res.status(400).json({ error: `máximo ${BATCH_LIMIT} textos por request` });
    return;
  }

  // Truncar cada texto a 2000 chars (Gemini tokens ~ 4000)
  const safeTexts = texts.map(t => String(t || '').slice(0, 2000));

  try {
    // Gemini batchEmbedContents
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_EMBED_MODEL}:batchEmbedContents?key=${apiKey}`;
    const requestBody = {
      requests: safeTexts.map(text => ({
        model: `models/${GEMINI_EMBED_MODEL}`,
        content: { parts: [{ text }] },
        taskType: 'RETRIEVAL_DOCUMENT',
        outputDimensionality: 768
      }))
    };

    const upstream = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody)
    });

    if (!upstream.ok) {
      const err = await upstream.text();
      res.status(upstream.status).json({
        error: 'Gemini API error',
        status: upstream.status,
        detail: err.slice(0, 500)
      });
      return;
    }

    const data = await upstream.json();
    const embeddings = (data?.embeddings || []).map(e => e.values || []);

    if (embeddings.length !== safeTexts.length) {
      res.status(500).json({
        error: 'Gemini devolvió cantidad inesperada de embeddings',
        expected: safeTexts.length,
        received: embeddings.length
      });
      return;
    }

    res.status(200).json({
      ok: true,
      count: embeddings.length,
      dim: embeddings[0]?.length || 0,
      embeddings
    });
  } catch (err) {
    res.status(500).json({ error: err?.message || 'Error desconocido' });
  }
};
