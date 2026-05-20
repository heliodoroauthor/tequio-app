// scripts/scrape_profeco.js
//
// TEQUIO · Scraper PROFECO Quién es Quién en los Precios
//
// Fuente:  https://datos.profeco.gob.mx/datos_abiertos/qqp.php
// Frecuencia: semanal (la fuente se actualiza ~1 vez/semana)
//
// Estrategia:
//   1. Scrapear la página del catálogo de PROFECO para obtener el link más reciente
//      al archivo del año actual. Esto evita hardcodear tokens que rotan.
//   2. Descargar el archivo (CSV / TXT / ZIP — autodetectado).
//   3. Parsearlo en streaming y upsertear en lotes a Supabase.
//   4. Loggear inicio + fin en scraper_logs.
//
// Env vars requeridos:
//   SUPABASE_URL
//   SUPABASE_SERVICE_ROLE_KEY
//   GITHUB_RUN_ID  (opcional, lo provee GitHub Actions)
//
// Dependencias: @supabase/supabase-js, csv-parse, node-fetch (opcional, Node 20 ya tiene fetch global)

import { createClient } from '@supabase/supabase-js';
import { parse } from 'csv-parse';
import { Readable } from 'node:stream';
import * as zlib from 'node:zlib';

const SUPABASE_URL = process.env.SUPABASE_URL;
const SERVICE_KEY  = process.env.SUPABASE_SERVICE_ROLE_KEY;
const RUN_ID       = process.env.GITHUB_RUN_ID || `local-${Date.now()}`;

const SCRAPER_SLUG = 'profeco_qqp';
const ANIO_OBJETIVO = new Date().getFullYear();          // 2026
const ANIO_FALLBACK = ANIO_OBJETIVO - 1;                  // si el del año en curso falla, cae al anterior
const CATALOGO_URL = 'https://datos.profeco.gob.mx/datos_abiertos/qqp.php';
const BATCH_SIZE   = 500;

if (!SUPABASE_URL || !SERVICE_KEY) {
  console.error('Faltan env vars SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY');
  process.exit(1);
}

const sb = createClient(SUPABASE_URL, SERVICE_KEY, {
  auth: { persistSession: false },
});

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────

function normHeader(h) {
  return String(h || '')
    .normalize('NFD').replace(/\p{Diacritic}/gu, '') // sin acentos
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
  // Formatos comunes: "YYYY-MM-DD", "YYYY-MM-DD HH:MM:SS", "DD/MM/YYYY"
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
  // Validación mínima
  if (!row.producto) return null;
  return row;
}

// ─────────────────────────────────────────────────────────────
// 1) Encontrar URL del archivo del año actual desde el catálogo
// ─────────────────────────────────────────────────────────────

async function descubrirURL(anio) {
  const html = await fetch(CATALOGO_URL).then(r => r.text());
  // Buscamos un href cuya etiqueta diga "Quien es Quien en los Precios <ANIO>"
  const regex = new RegExp(
    `href="([^"]+file\\.php\\?t=[^"]+)"[^>]*>[^<]*Quien es Quien en los Precios\\s*${anio}`,
    'i'
  );
  const m = html.match(regex);
  if (!m) return null;
  let url = m[1].startsWith('http') ? m[1] : new URL(m[1], CATALOGO_URL).href;
  return url;
}

// ─────────────────────────────────────────────────────────────
// 2) Descargar + parsear stream CSV
// ─────────────────────────────────────────────────────────────

async function descargarYParsear(url, onBatch) {
  const res = await fetch(url, {
    headers: {
      'User-Agent': 'TequioApp/1.0 (scraper-profeco; bot+tequio@gob.mx)',
      'Accept': 'text/csv,application/csv,application/octet-stream,*/*',
    },
    redirect: 'follow',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} al descargar ${url}`);

  const buf = Buffer.from(await res.arrayBuffer());

  // Autodetectar gzip
  let textBuf = buf;
  if (buf[0] === 0x1f && buf[1] === 0x8b) {
    console.log('[profeco] archivo detectado como gzip, descomprimiendo...');
    textBuf = zlib.gunzipSync(buf);
  }
  // Si es UTF-8 con BOM, removerlo
  if (textBuf[0] === 0xef && textBuf[1] === 0xbb && textBuf[2] === 0xbf) {
    textBuf = textBuf.slice(3);
  }

  // Detectar delimitador (PROFECO usa coma o pipe a veces)
  const head = textBuf.slice(0, 4096).toString('utf8');
  const delimiter = head.includes('|') && !head.includes(',') ? '|' : ',';
  console.log('[profeco] delimitador detectado:', JSON.stringify(delimiter), '· tamaño:', textBuf.length, 'bytes');

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
// 3) Upsert por lotes
// ─────────────────────────────────────────────────────────────

async function upsertBatch(rows) {
  const { error } = await sb
    .from('profeco_precios')
    .upsert(rows, {
      onConflict: 'producto,marca,presentacion,nombre_comercial,fecha_registro',
      ignoreDuplicates: false,
    });
  if (error) {
    console.error('[profeco] error upsert:', error.message);
    throw error;
  }
}

// ─────────────────────────────────────────────────────────────
// 4) Logging en scraper_logs
// ─────────────────────────────────────────────────────────────

async function logStart(fuenteUrl) {
  const { data, error } = await sb.from('scraper_logs').insert({
    scraper_slug:    SCRAPER_SLUG,
    workflow_run_id: RUN_ID,
    status:          'running',
    fuente_url:      fuenteUrl,
    started_at:      new Date().toISOString(),
  }).select('id').single();
  if (error) console.warn('[profeco] no se pudo log_start:', error.message);
  return data?.id;
}

async function logFinish(logId, payload) {
  if (!logId) return;
  const { error } = await sb.from('scraper_logs')
    .update({
      ...payload,
      finished_at: new Date().toISOString(),
    })
    .eq('id', logId);
  if (error) console.warn('[profeco] no se pudo log_finish:', error.message);
}

// ─────────────────────────────────────────────────────────────
// MAIN
// ─────────────────────────────────────────────────────────────

(async () => {
  console.log('[profeco] inicio · run', RUN_ID);
  const logId = await logStart(CATALOGO_URL);

  try {
    // 1. Descubrir URL
    let url = await descubrirURL(ANIO_OBJETIVO);
    if (!url) {
      console.warn(`[profeco] no encontré link para ${ANIO_OBJETIVO}, intentando ${ANIO_FALLBACK}…`);
      url = await descubrirURL(ANIO_FALLBACK);
    }
    if (!url) throw new Error('No se encontró URL del archivo PROFECO en el catálogo.');

    console.log('[profeco] URL del dataset:', url);

    // 2. Descargar + parsear + upsertear
    let inserted = 0;
    const total = await descargarYParsear(url, async (batch) => {
      await upsertBatch(batch);
      inserted += batch.length;
    });

    console.log(`[profeco] OK · ${total} filas procesadas`);

    await logFinish(logId, {
      status:         'success',
      rows_inserted:  inserted,
      rows_updated:   0,
      rows_skipped:   0,
      http_status:    200,
      fuente_url:     url,
      notes:          `PROFECO QQP año ${ANIO_OBJETIVO}`,
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
