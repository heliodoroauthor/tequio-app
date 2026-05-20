// scripts/scrape_gasolina.js  (v1 — CRE places + prices XML)
//
// Descarga dos XMLs del Gobierno de México (CRE), los une por place_id, y upsertea
// en gasolina_estaciones.
//
//   places.xml → place_id, name, cre_id, lng (x), lat (y)
//   prices.xml → place_id, gas_price[type=regular|premium|diesel]
//
// Frecuencia: diaria. La fuente se actualiza varias veces al día.

const SUPABASE_URL = process.env.SUPABASE_URL;
const SERVICE_KEY  = process.env.SUPABASE_SERVICE_ROLE_KEY;
const RUN_ID       = process.env.GITHUB_RUN_ID || `local-${Date.now()}`;
const SCRAPER_SLUG = 'gasolina_cre';
const URL_PRICES   = 'https://publicacionexterna.azurewebsites.net/publicaciones/prices';
const URL_PLACES   = 'https://publicacionexterna.azurewebsites.net/publicaciones/places';
const BATCH_SIZE   = 500;
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
  try {
    await sbInsert('scraper_logs', [{
      scraper_slug: SCRAPER_SLUG, workflow_run_id: RUN_ID,
      started_at: p.started_at, finished_at: new Date().toISOString(),
      ...p
    }]);
  } catch (e) { console.warn('no log:', e.message); }
}

// Parser XML simple via regex. CRE no usa atributos complejos ni namespaces.
function parsePlacesXML(xml) {
  // <place place_id="2039">
  //   <name>ESTACION HIPODROMO SA DE CV</name>
  //   <cre_id>PL/658/EXP/ES/2015</cre_id>
  //   <location><x>-116.9214</x><y>32.47641</y></location>
  // </place>
  const places = new Map();
  const rx = /<place\s+place_id="(\d+)">([\s\S]*?)<\/place>/g;
  let m;
  while ((m = rx.exec(xml)) !== null) {
    const place_id = parseInt(m[1], 10);
    const inner = m[2];
    const nameMatch = inner.match(/<name>([\s\S]*?)<\/name>/);
    const creMatch  = inner.match(/<cre_id>([\s\S]*?)<\/cre_id>/);
    const xMatch    = inner.match(/<x>([-\d.]+)<\/x>/);
    const yMatch    = inner.match(/<y>([-\d.]+)<\/y>/);
    places.set(place_id, {
      place_id,
      nombre: nameMatch ? nameMatch[1].trim() : 'Sin nombre',
      cre_id: creMatch  ? creMatch[1].trim()  : null,
      lng:    xMatch    ? parseFloat(xMatch[1]) : null,
      lat:    yMatch    ? parseFloat(yMatch[1]) : null,
    });
  }
  return places;
}

function parsePricesXML(xml) {
  // <place place_id="11703">
  //   <gas_price type="regular">22.95</gas_price>
  //   <gas_price type="premium">27.9</gas_price>
  //   <gas_price type="diesel">27.99</gas_price>  (opcional)
  // </place>
  const prices = new Map();
  const rx = /<place\s+place_id="(\d+)">([\s\S]*?)<\/place>/g;
  let m;
  while ((m = rx.exec(xml)) !== null) {
    const place_id = parseInt(m[1], 10);
    const inner = m[2];
    const obj = { place_id, regular: null, premium: null, diesel: null };
    const gpRx = /<gas_price\s+type="(\w+)">([-\d.]+)<\/gas_price>/g;
    let g;
    while ((g = gpRx.exec(inner)) !== null) {
      const v = parseFloat(g[2]);
      if (Number.isFinite(v) && v > 0) obj[g[1]] = v;
    }
    prices.set(place_id, obj);
  }
  return prices;
}

async function fetchText(url) {
  console.log(`[gasolina] GET ${url}`);
  const res = await fetch(url, { headers: { 'User-Agent': UA } });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return await res.text();
}

const started_at = new Date().toISOString();
(async () => {
  console.log('[gasolina] inicio · run', RUN_ID, '· v1');
  let inserted = 0;
  try {
    const [placesXML, pricesXML] = await Promise.all([
      fetchText(URL_PLACES),
      fetchText(URL_PRICES),
    ]);
    console.log(`[gasolina] descargados · places=${placesXML.length}B · prices=${pricesXML.length}B`);

    const places = parsePlacesXML(placesXML);
    const prices = parsePricesXML(pricesXML);
    console.log(`[gasolina] parseados · ${places.size} places · ${prices.size} prices`);

    // Unir + filtrar (solo con lat/lng y al menos un precio válido)
    const fecha = new Date().toISOString();
    const rows = [];
    let sinPrecios = 0, sinUbicacion = 0;
    for (const [place_id, place] of places) {
      const p = prices.get(place_id);
      if (!p) { sinPrecios++; continue; }
      if (place.lat == null || place.lng == null) { sinUbicacion++; continue; }
      // Sanity check: México está entre lat 14-33 y lng -118 a -86
      if (place.lat < 14 || place.lat > 33 || place.lng < -118 || place.lng > -86) continue;
      rows.push({
        place_id,
        cre_id: place.cre_id,
        nombre: (place.nombre || 'Sin nombre').slice(0, 200),
        lat: place.lat,
        lng: place.lng,
        precio_regular: p.regular,
        precio_premium: p.premium,
        precio_diesel:  p.diesel,
        fecha_actualizacion: fecha,
      });
    }
    console.log(`[gasolina] válidos: ${rows.length} · sin_precios: ${sinPrecios} · sin_ubicación: ${sinUbicacion}`);

    // Insertar en lotes
    for (let i = 0; i < rows.length; i += BATCH_SIZE) {
      const batch = rows.slice(i, i + BATCH_SIZE);
      await sbInsert('gasolina_estaciones', batch, {
        onConflict: 'place_id', merge: true,
      });
      inserted += batch.length;
      if (inserted % 5000 === 0) console.log(`[gasolina] ${inserted} estaciones…`);
    }

    console.log(`[gasolina] OK · ${inserted} estaciones actualizadas`);
    await logRun({
      status: 'ok', rows_inserted: inserted, rows_skipped: sinPrecios + sinUbicacion,
      http_status: 200, fuente_url: URL_PRICES,
      notes: `v1 places=${places.size} prices=${prices.size} insertados=${inserted}`,
      started_at,
    });
    process.exit(0);
  } catch (e) {
    console.error('[gasolina] FATAL:', e.message);
    await logRun({
      status: 'fail', rows_inserted: inserted, fuente_url: URL_PRICES,
      error_msg: String(e.message || e).slice(0, 500), started_at,
    });
    process.exit(1);
  }
})();
