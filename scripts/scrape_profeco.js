// scripts/scrape_profeco.js  (v6 — diagnóstico)
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
const BATCH_SIZE = 500;
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 TequioBot/1.0';

if (!SUPABASE_URL || !SERVICE_KEY) { console.error('Faltan env vars'); process.exit(1); }

const BASE = SUPABASE_URL.replace(/\/+$/, '') + '/rest/v1';
const AUTH = { 'apikey': SERVICE_KEY, 'Authorization': `Bearer ${SERVICE_KEY}`, 'Content-Type': 'application/json' };

async function sbInsert(table, rows, opts = {}) {
  const url = new URL(`${BASE}/${table}`);
  if (opts.onConflict) url.searchParams.set('on_conflict', opts.onConflict);
  const headers = { ...AUTH, 'Prefer': (opts.merge ? 'resolution=merge-duplicates,' : '') + 'return=minimal' };
  const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(rows) });
  if (!res.ok) throw new Error(`Supabase ${res.status}: ${(await res.text()).slice(0,300)}`);
}

async function logRun(p) {
  try { await sbInsert('scraper_logs', [{ scraper_slug: SCRAPER_SLUG, workflow_run_id: RUN_ID, started_at: p.started_at, finished_at: new Date().toISOString(), ...p }]); }
  catch (e) { console.warn('no log:', e.message); }
}

function normH(h) { return String(h||'').normalize('NFD').replace(/\p{Diacritic}/gu,'').toUpperCase().trim(); }
const HM = { PRODUCTO:'producto', PRESENTACION:'presentacion', MARCA:'marca', CATEGORIA:'categoria', CATALOGO:'catalogo', PRECIO:'precio', FECHAREGISTRO:'fecha_registro', CADENACOMERCIAL:'cadena_comercial', GIRO:'giro', NOMBRECOMERCIAL:'nombre_comercial', DIRECCION:'direccion', ESTADO:'estado', MUNICIPIO:'municipio', LATITUD:'latitud', LONGITUD:'longitud' };

function parseF(r) { if (!r) return null; const s = String(r).trim(); const m1=s.match(/^(\d{4})-(\d{2})-(\d{2})/); if(m1)return `${m1[1]}-${m1[2]}-${m1[3]}`; const m2=s.match(/^(\d{2})\/(\d{2})\/(\d{4})/); if(m2)return `${m2[3]}-${m2[2]}-${m2[1]}`; return null; }
function parseN(r) { if (r==null||r==='') return null; const n=Number(String(r).replace(/,/g,'').trim()); return Number.isFinite(n)?n:null; }
function cleanT(r, max=240) { if (r==null) return null; const s=String(r).trim(); return s.length===0?null:s.slice(0,max); }
function normalizeRow(raw) {
  const r = {};
  for (const [k,c] of Object.entries(HM)) {
    const v = raw[k];
    if (c==='precio'||c==='latitud'||c==='longitud') r[c] = parseN(v);
    else if (c==='fecha_registro') r[c] = parseF(v);
    else r[c] = cleanT(v);
  }
  return r.producto ? r : null;
}

async function descubrirURL(anio) {
  const html = await (await fetch(CATALOGO_URL, { headers: { 'User-Agent': UA } })).text();
  const rx = /<a\b[^>]*\bhref\s*=\s*["']([^"']*file\.php\?t=[^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
  const links = [...html.matchAll(rx)].map(m => ({ href: m[1], text: m[2].replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim() }));
  console.log(`[profeco] anchors: ${links.length}`);
  const found = links.find(l => l.text.includes(String(anio)));
  if (!found) { console.log('[profeco] anchors:', JSON.stringify(links.slice(0,10))); return null; }
  console.log(`[profeco] match: ${found.text} · ${found.href}`);
  return found.href.startsWith('http') ? found.href : new URL(found.href, CATALOGO_URL).href;
}

async function descargarYParsear(url, onBatch) {
  const res = await fetch(url, { headers: { 'User-Agent': UA, 'Accept': '*/*' }, redirect: 'follow' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  console.log(`[profeco] descargado: ${buf.length} bytes`);

  // ═══ DIAGNÓSTICO DE FORMATO ═══
  console.log(`[profeco] HEX(32): ${buf.slice(0,32).toString('hex')}`);
  const ascii64 = buf.slice(0,64).toString('utf8').replace(/[\x00-\x1f]/g, ch => '<'+ch.charCodeAt(0).toString(16)+'>');
  console.log(`[profeco] ASCII(64): ${ascii64}`);

  let textBuf = buf;
  if (buf[0]===0x1f && buf[1]===0x8b) { console.log('[profeco] GZIP'); textBuf = zlib.gunzipSync(buf); console.log(`[profeco] descomprimido: ${textBuf.length} bytes`); }
  else if (buf[0]===0x50 && buf[1]===0x4b) throw new Error('Archivo es ZIP (necesita unzip)');
  else if (buf[0]===0xd0 && buf[1]===0xcf) throw new Error('Archivo es XLS antiguo (CFB)');
  if (textBuf[0]===0xef && textBuf[1]===0xbb && textBuf[2]===0xbf) { textBuf = textBuf.slice(3); console.log('[profeco] BOM removido'); }

  const head = textBuf.slice(0,4096).toString('utf8');
  const nl = head.indexOf('\n');
  const firstLine = head.slice(0, nl >= 0 ? nl : 500);
  console.log(`[profeco] primera línea (${firstLine.length} chars): ${firstLine.slice(0,300)}`);
  const counts = { ',':(firstLine.match(/,/g)||[]).length, ';':(firstLine.match(/;/g)||[]).length, '|':(firstLine.match(/\|/g)||[]).length, '\t':(firstLine.match(/\t/g)||[]).length };
  console.log(`[profeco] delims:`, JSON.stringify(counts));
  const delimiter = Object.entries(counts).sort((a,b)=>b[1]-a[1])[0][0];
  console.log(`[profeco] delim elegido: ${JSON.stringify(delimiter)}`);

  const stream = Readable.from(textBuf);
  const parser = stream.pipe(parse({
    columns: h => { console.log('[profeco] HEADER:', JSON.stringify(h)); return h.map(normH); },
    delimiter, skip_empty_lines: true, relax_column_count: true, bom: true, trim: true, quote: false, skip_records_with_error: true,
  }));

  let batch = [], total = 0, skipped = 0, firstLogged = false;
  parser.on('skip', e => { skipped++; if (skipped<=3) console.warn(`[profeco] skip: ${(e?.message||'').slice(0,120)}`); });
  parser.on('error', e => console.error('[profeco] PARSER ERR:', e.message));

  for await (const raw of parser) {
    if (!firstLogged) { console.log('[profeco] PRIMER RECORD:', JSON.stringify(raw).slice(0,400)); firstLogged = true; }
    const n = normalizeRow(raw);
    if (!n) continue;
    batch.push(n);
    if (batch.length >= BATCH_SIZE) { await onBatch(batch); total += batch.length; batch = []; if (total % 10000 === 0) console.log(`[profeco] ${total} filas…`); }
  }
  if (batch.length) { await onBatch(batch); total += batch.length; }
  console.log(`[profeco] resumen · ok=${total} · skipped=${skipped}`);
  return { total, skipped };
}

const started_at = new Date().toISOString();
(async () => {
  console.log('[profeco] inicio · run', RUN_ID);
  let inserted = 0, sourceUrl = CATALOGO_URL;
  try {
    let url = await descubrirURL(ANIO_OBJETIVO);
    if (!url) url = await descubrirURL(ANIO_FALLBACK);
    if (!url) throw new Error('No URL');
    sourceUrl = url;
    const { total, skipped } = await descargarYParsear(url, async batch => {
      await sbInsert('profeco_precios', batch, { onConflict: 'producto,marca,presentacion,nombre_comercial,fecha_registro', merge: true });
      inserted += batch.length;
    });
    await logRun({ status: skipped > 0 ? 'partial' : 'ok', rows_inserted: inserted, rows_skipped: skipped, http_status: 200, fuente_url: sourceUrl, notes: `año ${ANIO_OBJETIVO} skip=${skipped}`, started_at });
    process.exit(0);
  } catch (e) {
    console.error('[profeco] ERROR:', e.message);
    await logRun({ status: 'fail', rows_inserted: inserted, fuente_url: sourceUrl, error_msg: String(e.message||e).slice(0,500), started_at });
    process.exit(1);
  }
})();
