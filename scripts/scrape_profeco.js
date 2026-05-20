// scripts/scrape_profeco.js  (v2: sin @supabase/supabase-js)
//
// TEQUIO · Scraper PROFECO Quien es Quien en los Precios
//
// Fuente:  https://datos.profeco.gob.mx/datos_abiertos/qqp.php
// Frecuencia: semanal
//
// Cambios v2:
//   - Removido @supabase/supabase-js (Node 20 sin WebSocket nativo no lo soporta)
//   - Usa fetch directo a PostgREST: POST + on_conflict + Prefer: resolution=merge-duplicates
//
// Env vars: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GITHUB_RUN_ID
// Deps:     csv-parse

import { parse } from 'csv-parse';
import { Readable } from 'node:stream';
import * as zlib from 'node:zlib';

const SUPABASE_URL = process.env.SUPABASE_URL;
const SERVICE_KEY  = process.env.SUPABASE_SERVICE_ROLE_KEY;
const RUN_ID       = process.env.GITHUB_RUN_ID || `local-${Date.now()}`;

const SCRAPER_SLUG = 'profeco_qqp';
const ANIO_OBJETIVO = new Date().getFullYear();
const ANIO_FALLBACK = ANIO_OBJETIVO - 1;
const CATALOGO_URL = 'https://datos.profeco.gob.mx/datos_abiertos/qqp.php';
const BATCH_SIZE   = 500;

if (!SUPABASE_URL || !SERVICE_KEY) {
  console.error('Faltan env vars SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY');
  process.exit(1);
}

// ─────────────────────────────────────────────────────────────
// Helpers REST a Supabase (PostgREST)
// ─────────────────────────────────────────────────────────────

const BASE = SUPABASE_URL.replace(/\/+$/, '') + '/rest/v1';
const AUTH_HEADERS = {
  'apikey':        SERVICE_KEY,
  'Authorization': `Bearer ${SERVICE_KEY}`,
  'Content-Type':  'application/json',
};

async function sbInsert(table, rows, opts = {}) {
  const url = new URL(`${BASE}/${table}`);
  if (opts.onConflict) url.searchParams.set('on_conflict', opts.onConflict);

  const headers = { ...AUTH_HEADERS };
  // resolution=merge-duplicates => upsert; return=minimal => no devuelve body para ahorrar bandwidth
  headers['Prefer'] = (opts.merge ? 'resolution=merge-duplicates,' : '') + 'return=minimal';

  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(rows),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Supabase ${res.status}: ${text.slice(0, 300)}`);
  }
  return res;
}

async function sbUpdate(table, filter, payload) {
  const url = new URL(`${BASE}/${table}`);
  for (const [k, v] of Object.entries(filter)) url.searchParams.set(k, v);
  const res = await fetch(url, {
    method: 'PATCH',
    headers: { ...AUTH_HEADERS, 'Prefer': 'return=minimal' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Supabase ${res.status}: ${text.slice(0, 300)}`);
  }
  return res;
}

// ─────────────────────────────────────────────────────────────
// Logging en scraper_logs (via REST)
// ─────────────────────────────────────────────────────────────

async function logStart(fuenteUrl) {
  const url = new URL(`${BASE}/scraper_logs`);
  url.searchParams.set('select', 'id');
  const res = await fetch(url, {
    method: 'POST',
    headers: { ...AUTH_HEADERS, 'Prefer': 'return=representation' },
    body: JSON.stringify([{
      scraper_slug:    SCRAPER_SLUG,
      workflow_run_id: RUN_ID,
      status:          'running',
      fuente_url:      fuenteUrl,
      started_at:      new Date().toISOString(),
    }]),
  });
  if (!res.ok) {
    console.warn('[profeco] no se pudo log_start:', res.status, await res.text());
    return null;
  }
  const data = await res.json();
  return data[0]?.id ?? null;
}

async function logFinish(logId, payload) {
  if (!logId) return;
  try {
    await sbUpdate('scraper_logs', { id: `eq.${logId}` }, {
      ...payload,
      finished_at: new Date().toISOString(),
    });
  } catch (e) {
    console.warn('[profeco] no se pudo log_finish:', e.message);
  }
}

// ─────────────────────────────────────────────────────────────
// Helpers de parsing
// ─────────────────────────────────────────────────────────────

function normHeader(h) {
  return String(h || '')
    .normalize('NFD').replace(/\p{Diacritic}/gu, '')
    .toUpperCase().trim();
}

const HEADER_MAP = {
  PRODUCTO:        'producto',
  PRESENTACION:    'presentacion',
  MARCA:           'marca',
  CATEGORIA:       'categoria',
  CATALOGO:        'catalogo',
  PRECIO:          'precio',
  FECHAREGISTRO:   'fecha_registro',
  CADENACOMERCIAL: 'cadena_comercial',
  GIRO:            'giro',
  NOMBRECOMERCIAL: 'nombre_comercial',
  DIRECCION:       'direccion',
  ESTADO:          'estado',
  MUNICIPIO:       'municipio',
  LATITUD:         'latitud',
  LONGITUD:        'longitud',
};

function parseFecha(raw) {
  if (!raw) return null;
  const s = String(raw).trim();
  const m1 = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m1) return `${m1[1]}-${m1[2]}-${m1[3]}`;
  const m2 = s.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
  if (m2) return `${m2[3]}-${m2[2]}-${m2[1]}`;
  return null;
}

function parseNum(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  const n = Number(String(raw).replace(/,/g, '').trim());
  return Number.isFinite(n) ? n : null;
}

function cleanText(raw, max = 240) {
  if (raw === null || raw === undefined) return null;
  const s = String(raw).trim();
  return s.length === 0 ? null : s.slice(0, max);
}

function normalizeRow(rawRow) {
  const row = {};
  for (const [csvKey, dbCol] of Object.entries(HEADER_MAP)) {
    const v = rawRow[csvKey];
    if (dbCol === 'precio' || dbCol === 'latitud' || dbCol === 'longitud') {
      row[dbCol] = parseNum(v);
    } else if (dbCol === 'fecha_registro') {
      row[dbCol] = parseFecha(v);
    } else {
      row[dbCol] = cleanText(v);
    }
  }
  if (!row.producto) return null;
  return row;
}

// ─────────────────────────────────────────────────────────────
// Descubrir URL del archivo del año actual
// ─────────────────────────────────────────────────────────────

async function descubrirURL(anio) {
  const html = await fetch(CATALOGO_URL, {
    headers: { 'User-Agent': 'TequioApp/1.0 (+tequio.app)' },
  }).then(r => r.text());
  const regex = new RegExp(
    `href="([^"]+file\\.php\\?t=[^"]+)"[^>]*>[^<]*Quien es Quien en los Precios\\s*${anio}`,
    'i'
  );
  const m = html.match(regex);
  if (!m) return null;
  return m[1].startsWith('http') ? m[1] : new URL(m[1], CATALOGO_URL).href;
}

// ─────────────────────────────────────────────────────────────
// Descargar + parsear stream CSV
// ─────────────────────────────────────────────────────────────

async function descargarYParsear(url, onBatch) {
  const res = await fetch(url, {
    headers: {
      'User-Agent': 'TequioApp/1.0 (scraper-profeco; +tequio.app)',
      'Accept': 'text/csv,application/csv,application/octet-stream,*/*',
    },
    redirect: 'follow',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} al descargar ${url}`);

  const buf = Buffer.from(await res.arrayBuffer());
  let textBuf = buf;
  if (buf[0] === 0x1f && buf[1] === 0x8b) {
    console.log('[profeco] archivo gzip, descomprimiendo...');
    textBuf = zlib.gunzipSync(buf);
  }
  if (textBuf[0] === 0xef && textBuf[1] === 0xbb && textBuf[2] === 0xbf) {
    textBuf = textBuf.slice(3);
  }

  const head = textBuf.slice(0, 4096).toString('utf8');
  const delimiter = head.includes('|') && !head.includes(',') ? '|' : ',';
  console.log('[profeco] delimitador:', JSON.stringify(delimiter), '· tamaño:', textBuf.length, 'bytes');

  const stream = Readable.from(textBuf);
  const parser = stream.pipe(parse({
    columns: header => header.map(normHeader),
    delimiter,
    skip_empty_lines: true,
    relax_quotes: true,
    relax_column_count: true,
    bom: true,
    trim: true,
  }));

  let batch = [];
  let total = 0;
  for await (const raw of parser) {
    const norm = normalizeRow(raw);
    if (!norm) continue;
    batch.push(norm);
    if (batch.length >= BATCH_SIZE) {
      await onBatch(batch);
      total += batch.length;
      batch = [];
      if (total % 10000 === 0) console.log(`[profeco] procesadas ${total} filas…`);
    }
  }
  if (batch.length) {
    await onBatch(batch);
    total += batch.length;
  }
  return total;
}

// ─────────────────────────────────────────────────────────────
// MAIN
// ─────────────────────────────────────────────────────────────

(async () => {
  console.log('[profeco] inicio · run', RUN_ID);
  const logId = await logStart(CATALOGO_URL);

  try {
    let url = await descubrirURL(ANIO_OBJETIVO);
    if (!url) {
      console.warn(`[profeco] no encontré link para ${ANIO_OBJETIVO}, intentando ${ANIO_FALLBACK}…`);
      url = await descubrirURL(ANIO_FALLBACK);
    }
    if (!url) throw new Error('No se encontró URL del archivo PROFECO en el catálogo.');

    console.log('[profeco] URL del dataset:', url);

    let inserted = 0;
    const total = await descargarYParsear(url, async (batch) => {
      await sbInsert('profeco_precios', batch, {
        onConflict: 'producto,marca,presentacion,nombre_comercial,fecha_registro',
        merge: true,
      });
      inserted += batch.length;
    });

    console.log(`[profeco] OK · ${total} filas procesadas`);

    await logFinish(logId, {
      status:        'success',
      rows_inserted: inserted,
      rows_updated:  0,
      rows_skipped:  0,
      http_status:   200,
      fuente_url:    url,
      notes:         `PROFECO QQP año ${ANIO_OBJETIVO}`,
    });
    process.exit(0);

  } catch (err) {
    console.error('[profeco] ERROR:', err.message);
    await logFinish(logId, {
      status:    'error',
      error_msg: String(err.message || err).slice(0, 500),
    });
    process.exit(1);
  }
})();
