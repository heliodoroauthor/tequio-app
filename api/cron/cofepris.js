// Cron: Scrape COFEPRIS articulos weekly.
export default async function handler(req, res) {
  if (req.headers.authorization !== `Bearer ${process.env.CRON_SECRET}`) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  const SUPABASE_URL = process.env.SUPABASE_URL || 'https://mhsuihwjgtzxflesbnxv.supabase.co';
  const SERVICE_ROLE = process.env.SUPABASE_SERVICE_ROLE;
  if (!SERVICE_ROLE) return res.status(500).json({ error: 'Missing SUPABASE_SERVICE_ROLE' });

  const URL_COFEPRIS = 'https://www.gob.mx/cofepris';
  const MESES = {
    enero:'01',febrero:'02',marzo:'03',abril:'04',mayo:'05',junio:'06',
    julio:'07',agosto:'08',septiembre:'09',octubre:'10',noviembre:'11',diciembre:'12'
  };

  try {
    const html = await fetch(URL_COFEPRIS, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'es-MX,es;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache'
      }
    }).then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); });

    // Extraer enlaces a articulos con su contexto
    const items = [];
    const linkRe = /<a[^>]+href="(\/cofepris\/(?:es\/)?articulos\/[^"]+)"[^>]*>([\s\S]{0,600}?)<\/a>/g;
    let m;
    const seen = new Set();
    while ((m = linkRe.exec(html)) !== null) {
      const href = 'https://www.gob.mx' + m[1].split('?')[0];
      if (seen.has(href)) continue;
      seen.add(href);

      // Texto cercano: tomar 600 chars alrededor
      const ctxStart = Math.max(0, m.index - 300);
      const ctxEnd = Math.min(html.length, m.index + 600);
      const ctx = html.slice(ctxStart, ctxEnd).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

      const fechaMatch = ctx.match(/(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})/i);
      if (!fechaMatch) continue;
      const mes = MESES[fechaMatch[2].toLowerCase()];
      if (!mes) continue;
      const fecha = `${fechaMatch[3]}-${mes}-${fechaMatch[1].padStart(2,'0')}`;

      const titulo = ctx.slice(fechaMatch.index + fechaMatch[0].length).trim().slice(0, 400);
      if (!titulo || titulo.length < 15) continue;

      const slug = m[1].split('/').pop().slice(0, 60);
      const isAlerta = /alerta|riesgo|decomis|retir|falsific|contamin|brote|intoxic/i.test(titulo);

      items.push({
        titulo: (isAlerta ? '🚨 Alerta sanitaria' : '🏥 Comunicado COFEPRIS'),
        resumen: titulo.slice(0, 500),
        url_oficial: href,
        fuente: 'COFEPRIS',
        fuente_url: URL_COFEPRIS,
        ambito: 'nacional',
        tema: 'salud',
        fecha_publicacion: fecha,
        hash_url: `cofepris_${slug.replace(/\W+/g,'_')}`
      });
    }

    if (items.length === 0) return res.status(200).json({ ok: true, parsed: 0 });

    const upsertUrl = `${SUPABASE_URL}/rest/v1/noticias_civicas?on_conflict=hash_url`;
    const upsert = await fetch(upsertUrl, {
      method: 'POST',
      headers: {
        'apikey': SERVICE_ROLE,
        'Authorization': `Bearer ${SERVICE_ROLE}`,
        'Content-Type': 'application/json',
        'Prefer': 'resolution=ignore-duplicates,return=minimal'
      },
      body: JSON.stringify(items)
    });
    if (!upsert.ok) {
      const errText = await upsert.text();
      return res.status(500).json({ error: 'insert failed', detail: errText.slice(0, 300) });
    }
    return res.status(200).json({ ok: true, parsed: items.length });
  } catch (err) {
    return res.status(500).json({ error: 'cron failed', detail: String(err).slice(0, 300) });
  }
}
