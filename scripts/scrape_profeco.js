// scripts/scrape_profeco.js  (v3)
//
// TEQUIO · Scraper PROFECO Quien es Quien en los Precios
//
// Cambios v3:
//   - status válidos en scraper_logs: 'ok' | 'fail' | 'partial' (NO 'running')
//   - Solo se crea UN row en scraper_logs al final (no al inicio)
//   - descubrirURL ahora extrae TODOS los anchors file.php?t=… y elige por año
//     en el texto. Mucho más tolerante a variaciones del HTML.
//   - Log de diagnóstico si no encuentra link (primeros 10 anchors)

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

// User-Agent más realista (algunos sitios bloquean UAs evidentemente bot)
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 TequioBot/1.0';

if (!SUPABASE_URL || !SERVICE_KEY) {
  console.error('Faltan env vars SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY');
  process.exit(1);
}

// ─────────────────────────────────────────────────────────────
// Helpers REST a Supabase
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
  headers['Prefer'] = (opts.merge ? 'resolution=merge-duplicates,' : '') + 'return=minimal';
  const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(rows) });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Supabase ${res.status}: ${text.slice(0, 300)}`);
  }
  return res;
}

// Log final único — status válidos: 'ok' | 'fail' | 'partial'
async function logRun(payload) {
  try {
    await sbInsert('scraper_logs', [{
      scraper_slug:    SCRAPER_SLUG,
      workflow_run_id: RUN_ID,
      started_at:      payload.started_at,
      finished_at:     new Date().toISOString(),
      ...payload,
    }]);
  } catch (e) {
    console.warn('[profeco] no se pudo log final:', e.message);
  }
}

// ─────────────────────────────────────────────────────────────
// Helpers de parsing CSV
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
// Descubrir URL del archivo del año actual (flexible)
// ─────────────────────────────────────────────────────────────

async function descubrirURL(anio) {
  const res = await fetch(CATALOGO_URL, {
    headers: { 'User-Agent': UA, 'Accept': 'text/html,*/*' },
  });
  const html = await res.text();
  console.log(`[profeco] catálogo HTML: ${html.length} bytes, status ${res.status}`);

  // Extraer TODOS los <a href="...file.php?t=...">TEXT</a>
  const linkRegex = /<a\b[^>]*\bhref\s*=\s*["']([^"']*file\.php\?t=[^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
  const allLinks = [...html.matchAll(linkRegex)].map(m => ({
    href: m[1],
    text: m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim(),
  }));
  console.log(`[profeco] anchors file.php?t= encontrados: ${allLinks.length}`);

  if (allLinks.length === 0) {
    console.log('[profeco] DEBUG · primeros 800 chars del HTML:\n', html.slice(0, 800));
    return null;
  }

  // Buscar el que tenga el año en su texto (Quien es Quien en los Precios <ANIO>)
  const yearStr = String(anio);
  const found = allLinks.find(l => l.text.includes(yearStr));
  if (!found) {
    console.log(`[profeco] no se encontró link con texto que incluya "${yearStr}". Anchors disponibles:`);
    allLinks.slice(0, 10).forEach((l, i) => {
      console.log(`  [${i}] text="${l.text.slice(0, 80)}" · href=${l.href.slice(0, 60)}`);
    });
    return null;
  }

  console.log(`[profeco] match · text="${found.text}" · href=${found.href}`);
  return found.href.startsWith('http') ? found.href : new URL(found.href, CATALOGO_URL).href;
}

// ─────────────────────────────────────────────────────────────
// Descargar + parsear stream CSV
// ─────────────────────────────────────────────────────────────

async function descargarYParsear(url, onBatch) {
  const res = await fetch(url, {
    headers: {
      'User-Agent': UA,
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

const started_at = new Date().toISOString();
(async () => {
  console.log('[profeco] inicio · run', RUN_ID);
  let inserted = 0;
  let sourceUrl = CATALOGO_URL;

  try {
    let url = await descubrirURL(ANIO_OBJETIVO);
    if (!url) {
      console.warn(`[profeco] no encontré link para ${ANIO_OBJETIVO}, intentando ${ANIO_FALLBACK}…`);
      url = await descubrirURL(ANIO_FALLBACK);
    }
    if (!url) throw new Error('No se encontró URL del archivo PROFECO en el catálogo.');
    sourceUrl = url;
    console.log('[profeco] URL del dataset:', url);

    const total = await descargarYParsear(url, async (batch) => {
      await sbInsert('profeco_precios', batch, {
        onConflict: 'producto,marca,presentacion,nombre_comercial,fecha_registro',
        merge: true,
      });
      inserted += batch.length;
    });

    console.log(`[profeco] OK · ${total} filas procesadas`);

    await logRun({
      status:        'ok',
      rows_inserted: inserted,
      rows_updated:  0,
      rows_skipped:  0,
      http_status:   200,
      fuente_url:    sourceUrl,
      notes:         `PROFECO QQP año ${ANIO_OBJETIVO}`,
      started_at,
    });
    process.exit(0);
  } catch (err) {
    console.error('[profeco] ERROR:', err.message);
    await logRun({
      status:        'fail',
      rows_inserted: inserted,
      fuente_url:    sourceUrl,
      error_msg:     String(err.message || err).slice(0, 500),
      started_at,
    });
    process.exit(1);
  }
})();
