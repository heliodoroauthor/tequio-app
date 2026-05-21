// scripts/scrape_senado_perfiles.js
// Tequio · Scraper de perfiles individuales LXVI
// Lee politicos_senadores, fetcha cada perfil del Senado, parsea semblanza + comisiones,
// hace UPDATE via Supabase REST.
//
// Requiere env vars:
//   SUPABASE_URL
//   SUPABASE_SERVICE_KEY
//
// 🦎 Cero invención: cada campo proviene de senado.gob.mx/66/senador/{id}

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_KEY;
const USER_AGENT = 'Tequio Civic Bot (+https://tequio.app)';
const PACING_MS = 2000;   // 2s entre fetches
const FETCH_TIMEOUT_MS = 25000;
const RETRY_503_DELAY = 5000;
const MAX_RETRIES = 3;

if (!SUPABASE_URL || !SUPABASE_KEY) {
  console.error('Missing SUPABASE_URL or SUPABASE_SERVICE_KEY');
  process.exit(1);
}

function stripHtml(s) {
  return s.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').replace(/&nbsp;/g, ' ').trim();
}

function parseProfile(html) {
  // SEMBLANZA
  let semblanza = null;
  const sembMatch = html.match(/<div class="item-content[^"]*" id="semblanza">([\s\S]*?)<div class="item-content/);
  if (sembMatch) {
    let s = sembMatch[1];
    s = s.replace(/<\/(p|li|ul|ol)>/gi, '\n').replace(/<li[^>]*>/gi, '• ');
    s = s.replace(/<strong[^>]*>([^<]+)<\/strong>/gi, '$1');
    s = stripHtml(s).replace(/\n+/g, '\n').trim();
    semblanza = s || null;
  }

  // COMISIONES: array of { rol, nombre, url }
  const comisiones_secretario = [];
  const comisiones_integrante = [];
  const comisiones_presidente = [];
  const comH = html.search(/<h4[^>]*>COMISIONES<\/h4>/i);
  if (comH > 0) {
    const block = html.slice(comH, comH + 8000);
    const blocks = [...block.matchAll(/<tr><th>(Presidente\(a\)|Secretario\(a\)|Integrante)[^<]*<\/th><td>([\s\S]*?)<\/td><\/tr>/gi)];
    for (const b of blocks) {
      const rol = b[1];
      const items = [...b[2].matchAll(/<a\s+href=['"]([^'"]+)['"][^>]*>([^<]+)<\/a>/gi)];
      const arr = rol.startsWith('Pres') ? comisiones_presidente
                : rol.startsWith('Secr') ? comisiones_secretario
                : comisiones_integrante;
      for (const it of items) {
        arr.push({ nombre: it[2].trim(), url: 'https://www.senado.gob.mx' + it[1] });
      }
    }
  }

  // Cargo especial (presidencias en Mesa Directiva o coordinaciones — buscar en sidebar)
  let cargo_especial = null;
  if (comisiones_presidente.length > 0) {
    cargo_especial = 'Presidente(a) Comisión: ' + comisiones_presidente.map(c => c.nombre).join(', ');
  }

  return { semblanza, comisiones_secretario, comisiones_integrante, comisiones_presidente, cargo_especial };
}

async function fetchSenator(senId) {
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
      const r = await fetch(`https://www.senado.gob.mx/66/senador/${senId}`, {
        headers: { 'User-Agent': USER_AGENT },
        signal: controller.signal
      });
      clearTimeout(timeout);
      if (r.ok) {
        const html = await r.text();
        return parseProfile(html);
      }
      if (r.status === 503 || r.status === 429) {
        console.log(`  [${senId}] HTTP ${r.status}, retry ${attempt}/${MAX_RETRIES} in ${RETRY_503_DELAY * attempt}ms`);
        await new Promise(res => setTimeout(res, RETRY_503_DELAY * attempt));
        continue;
      }
      console.log(`  [${senId}] HTTP ${r.status} (no retry)`);
      return null;
    } catch (e) {
      console.log(`  [${senId}] error: ${e.message}, retry ${attempt}/${MAX_RETRIES}`);
      if (attempt === MAX_RETRIES) return null;
      await new Promise(res => setTimeout(res, RETRY_503_DELAY));
    }
  }
  return null;
}

async function listSenadores() {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/politicos_senadores?select=id&order=id.asc`, {
    headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` }
  });
  if (!r.ok) throw new Error(`List failed: ${r.status} ${await r.text()}`);
  return await r.json();
}

async function updateSenator(senId, data) {
  const payload = {
    semblanza: data.semblanza,
    comisiones_secretario: data.comisiones_secretario,
    comisiones_integrante: data.comisiones_integrante,
    cargo_especial: data.cargo_especial,
    scraped_at: new Date().toISOString()
  };
  const r = await fetch(`${SUPABASE_URL}/rest/v1/politicos_senadores?id=eq.${senId}`, {
    method: 'PATCH',
    headers: {
      apikey: SUPABASE_KEY,
      Authorization: `Bearer ${SUPABASE_KEY}`,
      'Content-Type': 'application/json',
      Prefer: 'return=minimal'
    },
    body: JSON.stringify(payload)
  });
  if (!r.ok) console.log(`  [${senId}] UPDATE failed: ${r.status} ${await r.text()}`);
  return r.ok;
}

async function main() {
  console.log('Tequio · Senado LXVI perfiles scraper');
  const senadores = await listSenadores();
  console.log(`${senadores.length} senadores a procesar`);

  let ok = 0, fail = 0, com_total = 0;
  for (let i = 0; i < senadores.length; i++) {
    const s = senadores[i];
    const t0 = Date.now();
    const data = await fetchSenator(s.id);
    if (data) {
      await updateSenator(s.id, data);
      ok++;
      const ncom = (data.comisiones_secretario?.length || 0) + (data.comisiones_integrante?.length || 0) + (data.comisiones_presidente?.length || 0);
      com_total += ncom;
      console.log(`[${i + 1}/${senadores.length}] sen_id=${s.id} ✓ sem=${data.semblanza ? data.semblanza.length : 0}b com=${ncom} (${Date.now() - t0}ms)`);
    } else {
      fail++;
      console.log(`[${i + 1}/${senadores.length}] sen_id=${s.id} ✗ FAILED`);
    }
    if (i < senadores.length - 1) await new Promise(res => setTimeout(res, PACING_MS));
  }

  console.log(`\nResumen: ${ok} OK, ${fail} fallidos, ${com_total} comisiones totales scrapeadas`);
  if (fail > senadores.length * 0.2) {
    console.error('More than 20% failed — exiting con error');
    process.exit(1);
  }
}

main().catch(e => { console.error(e); process.exit(1); });
