#!/usr/bin/env node
/**
 * Tequio · Scraper Banxico SIE
 *
 * Pull diario de tipo de cambio, tasa Banxico, TIIE 28d, remesas mensuales e IED trimestral.
 * Inserta en tabla `econ_banxico` (histórico) y refresca `indicadores_fiscales` cuando aplica.
 * Cada ejecución registra status en `scraper_logs`.
 *
 * Variables de entorno requeridas:
 *   BANXICO_TOKEN              — Token gratuito de https://www.banxico.org.mx/SieAPIRest/service/v1/token
 *   SUPABASE_URL               — URL Supabase del proyecto
 *   SUPABASE_SERVICE_ROLE_KEY  — Service role key (NO anon)
 *
 * Uso local:
 *   BANXICO_TOKEN=xxx SUPABASE_URL=xxx SUPABASE_SERVICE_ROLE_KEY=xxx node scripts/scrape_banxico.js
 *
 * Promesa "cero invención": cada fila guarda fuente_url exacto al endpoint Banxico consultado.
 */

const BANXICO_TOKEN = process.env.BANXICO_TOKEN;
const SB_URL = (process.env.SUPABASE_URL || '').replace(/\/$/, '');
const SB_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;
const SCRAPER_SLUG = 'banxico_sie';
const FUENTE_LABEL = 'Banxico SIE';

if (!BANXICO_TOKEN || !SB_URL || !SB_KEY) {
  console.error('[banxico] Faltan variables de entorno: BANXICO_TOKEN, SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY');
  process.exit(1);
}

const SERIES = {
  tipo_cambio_fix: { id: 'SF43718', freq: 'diaria',     nombre: 'Tipo de cambio FIX (MXN/USD)',       unidad: 'MXN por USD' },
  tasa_referencia: { id: 'SF61745', freq: 'diaria',     nombre: 'Tasa Objetivo Banxico',              unidad: '% anual' },
  tiie_28:         { id: 'SF43783', freq: 'diaria',     nombre: 'TIIE 28 días',                       unidad: '% anual' },
  remesas:         { id: 'SE27803', freq: 'mensual',    nombre: 'Remesas familiares',                 unidad: 'USD millones' },
  ied:             { id: 'SE45712', freq: 'trimestral', nombre: 'Inversión Extranjera Directa total', unidad: 'USD millones' },
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchSerie(serieId, intentos = 3) {
  const url = `https://www.banxico.org.mx/SieAPIRest/service/v1/series/${serieId}/datos/oportuno?token=${BANXICO_TOKEN}`;
  for (let i = 0; i < intentos; i++) {
    try {
      const r = await fetch(url, { headers: { Accept: 'application/json' } });
      if (r.status === 429) {
        await sleep(2000 * (i + 1));
        continue;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      const datos = j?.bmx?.series?.[0]?.datos;
      if (!datos || !datos.length) throw new Error('Respuesta sin datos');
      // Banxico devuelve fecha 'dd/mm/yyyy' → normalizar a yyyy-mm-dd
      return datos
        .filter((d) => d.dato && d.dato !== 'N/E')
        .map((d) => {
          const [dd, mm, yyyy] = d.fecha.split('/');
          return {
            fecha: `${yyyy}-${mm.padStart(2, '0')}-${dd.padStart(2, '0')}`,
            valor: parseFloat(String(d.dato).replace(/,/g, '')),
          };
        });
    } catch (e) {
      if (i === intentos - 1) throw e;
      await sleep(1000 * (i + 1));
    }
  }
}

async function sbUpsert(table, rows, onConflict) {
  if (!rows.length) return { ok: true, inserted: 0 };
  const url = `${SB_URL}/rest/v1/${table}?on_conflict=${onConflict}`;
  const r = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      apikey: SB_KEY,
      Authorization: `Bearer ${SB_KEY}`,
      Prefer: 'resolution=merge-duplicates,return=minimal',
    },
    body: JSON.stringify(rows),
  });
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`Supabase upsert ${r.status}: ${txt.slice(0, 200)}`);
  }
  return { ok: true, inserted: rows.length };
}

async function logScraper(status, summary, errorMsg, startedAt) {
  try {
    await fetch(`${SB_URL}/rest/v1/scraper_logs`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        apikey: SB_KEY,
        Authorization: `Bearer ${SB_KEY}`,
        Prefer: 'return=minimal',
      },
      body: JSON.stringify([{
        scraper_slug: SCRAPER_SLUG,
        workflow_run_id: process.env.GITHUB_RUN_ID || null,
        status,
        rows_inserted: summary.inserted || 0,
        rows_updated: 0,
        rows_skipped: summary.skipped || 0,
        fuente_url: 'https://www.banxico.org.mx/SieAPIRest/',
        http_status: 200,
        error_msg: errorMsg || null,
        notes: JSON.stringify(summary.series || {}).slice(0, 1000),
        started_at: startedAt,
        finished_at: new Date().toISOString(),
      }]),
    });
  } catch (e) {
    console.error('[banxico] No se pudo escribir scraper_logs:', e.message);
  }
}

// Refresca indicadores_fiscales con el dato más reciente de tipo de cambio (slug 'dolar_hoy')
// Si no existe el indicador, lo crea. Si existe, actualiza valor_display y fecha_dato.
async function refrescarIndicador(slug, label, valor, fecha, fuenteUrl, orden) {
  const valor_display = `$${valor.toFixed(2)}`;
  const row = {
    slug,
    label,
    valor_display,
    valor_numerico: valor,
    cambio_direccion: 'flat',
    cambio_label: `MXN/USD · ${fecha}`,
    color_var: 'var(--accent2)',
    fuente: 'Banxico SIE',
    fuente_url: fuenteUrl,
    periodo: fecha,
    fecha_dato: fecha,
    fecha_publicacion: new Date().toISOString().slice(0, 10),
    orden,
  };
  await sbUpsert('indicadores_fiscales', [row], 'slug');
}

async function main() {
  const startedAt = new Date().toISOString();
  const seriesProcesadas = {};
  let totalInsertado = 0;
  let huboError = false;
  let primerError = null;
  let dolarMasReciente = null;

  for (const [slug, cfg] of Object.entries(SERIES)) {
    try {
      const datos = await fetchSerie(cfg.id);
      const rows = datos.map((d) => ({
        serie_id: cfg.id,
        serie_slug: slug,
        nombre: cfg.nombre,
        unidad: cfg.unidad,
        fecha: d.fecha,
        valor: d.valor,
        frecuencia: cfg.freq,
        fuente: FUENTE_LABEL,
        fuente_url: `https://www.banxico.org.mx/SieAPIRest/service/v1/series/${cfg.id}/datos/oportuno`,
      }));
      const upRes = await sbUpsert('econ_banxico', rows, 'serie_id,fecha');
      totalInsertado += upRes.inserted;
      seriesProcesadas[slug] = upRes.inserted;
      console.log(`[banxico] ${slug}: ${upRes.inserted} filas`);

      // Guardar el último dato de tipo de cambio para refrescar el KPI del dashboard
      if (slug === 'tipo_cambio_fix' && datos.length) {
        const ultimo = datos[datos.length - 1];
        dolarMasReciente = { valor: ultimo.valor, fecha: ultimo.fecha, fuenteUrl: rows[0].fuente_url };
      }

      await sleep(250); // rate limit Banxico
    } catch (e) {
      huboError = true;
      primerError = primerError || `${slug}: ${e.message}`;
      console.error(`[banxico] FAIL ${slug}:`, e.message);
    }
  }

  // Refrescar KPI del dashboard con el último FIX
  if (dolarMasReciente) {
    try {
      await refrescarIndicador(
        'dolar_hoy',
        'DÓLAR HOY (FIX Banxico)',
        dolarMasReciente.valor,
        dolarMasReciente.fecha,
        dolarMasReciente.fuenteUrl,
        0,
      );
      console.log(`[banxico] indicadores_fiscales actualizado: dolar_hoy=$${dolarMasReciente.valor}`);
    } catch (e) {
      console.error('[banxico] No se pudo refrescar indicadores_fiscales:', e.message);
    }
  }

  const status = huboError ? 'partial' : 'ok';
  await logScraper(status, { inserted: totalInsertado, series: seriesProcesadas }, primerError, startedAt);
  console.log(`[banxico] DONE status=${status} total=${totalInsertado}`);
  process.exit(huboError ? 1 : 0);
}

main().catch(async (e) => {
  console.error('[banxico] FATAL', e);
  await logScraper('fail', {}, e.message, new Date().toISOString());
  process.exit(1);
});
