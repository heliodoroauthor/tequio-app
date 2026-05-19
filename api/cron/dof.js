// Cron: Scrape DOF RSS daily, insert new entries to noticias_civicas.
// Trigger: Vercel cron at /api/cron/dof
// Auth: validates CRON_SECRET header

export default async function handler(req, res) {
  // Vercel cron sends Authorization: Bearer ${CRON_SECRET}
  if (req.headers.authorization !== `Bearer ${process.env.CRON_SECRET}`) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  const SUPABASE_URL = process.env.SUPABASE_URL || 'https://mhsuihwjgtzxflesbnxv.supabase.co';
  const SERVICE_ROLE = process.env.SUPABASE_SERVICE_ROLE;
  if (!SERVICE_ROLE) return res.status(500).json({ error: 'Missing SUPABASE_SERVICE_ROLE' });

  const DOF_RSS = 'https://dof.gob.mx/rss/sumario.xml';

  try {
    const rss = await fetch(DOF_RSS, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'es-MX,es;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache'
      }
    }).then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); });

    // Parse RSS items
    const items = [];
    const re = /<item>([\s\S]*?)<\/item>/g;
    let m;
    while ((m = re.exec(rss)) !== null) {
      const block = m[1];
      const title = (block.match(/<title>([\s\S]*?)<\/title>/) || [])[1] || '';
      const link  = (block.match(/<link>([\s\S]*?)<\/link>/) || [])[1] || '';
      const desc  = (block.match(/<description>([\s\S]*?)<\/description>/) || [])[1] || '';
      const pubDate = (block.match(/<pubDate>([\s\S]*?)<\/pubDate>/) || [])[1] || '';

      const cleanTitle = title.replace(/<!\[CDATA\[/g,'').replace(/\]\]>/g,'').trim();
      const cleanDesc  = desc.replace(/<!\[CDATA\[/g,'').replace(/\]\]>/g,'').trim();
      const cleanLink  = link.replace(/<!\[CDATA\[/g,'').replace(/\]\]>/g,'').trim();

      // Extract fecha from URL: ?codigo=NNN&fecha=DD/MM/YYYY
      const fechaMatch = cleanLink.match(/fecha=(\d{2})\/(\d{2})\/(\d{4})/);
      const fecha = fechaMatch ? `${fechaMatch[3]}-${fechaMatch[2]}-${fechaMatch[1]}` : null;

      const codigoMatch = cleanLink.match(/codigo=(\d+)/);
      const codigo = codigoMatch ? codigoMatch[1] : cleanLink.slice(-20);

      if (!cleanTitle || !cleanLink) continue;

      items.push({
        titulo: cleanTitle.slice(0, 300),
        resumen: cleanDesc.slice(0, 500),
        url_oficial: cleanLink,
        fuente: 'DOF',
        fuente_url: 'https://dof.gob.mx',
        ambito: 'nacional',
        tema: 'politica',
        fecha_publicacion: fecha,
        hash_url: `dof_${codigo}`
      });
    }

    if (items.length === 0) {
      return res.status(200).json({ ok: true, parsed: 0, inserted: 0 });
    }

    // Upsert via PostgREST
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
      return res.status(500).json({ error: 'Supabase insert failed', detail: errText.slice(0, 300) });
    }

    return res.status(200).json({ ok: true, parsed: items.length, inserted: items.length });
  } catch (err) {
    return res.status(500).json({ error: 'cron failed', detail: String(err).slice(0, 300) });
  }
}
