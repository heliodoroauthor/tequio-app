// /api/sitemap-tesis — Sitemap dinámico de tesis SCJN.
// Solo las tesis más recientes (11a Época) o con importancia 'alta' para no rebasar
// el límite de 50,000 URLs por sitemap. Total ~10k URLs (suficiente para SEO inicial).

export default async function handler(req, res) {
  const SUPABASE_URL = process.env.SUPABASE_URL?.replace(/\/$/, '');
  const ANON_KEY = process.env.SUPABASE_ANON_KEY;
  if (!SUPABASE_URL || !ANON_KEY) { res.status(500).send('config error'); return; }

  try {
    // Tesis de la Undécima Época (las más recientes y vigentes) - 8k tesis
    const r = await fetch(`${SUPABASE_URL}/rest/v1/jurisprudencia_scjn?select=id,registro_digital,rubro&epoca=eq.11a.+%C3%89poca&order=fecha_publicacion.desc.nullslast&limit=10000`, {
      headers: {
        'apikey': ANON_KEY,
        'Authorization': `Bearer ${ANON_KEY}`,
        'Range-Unit': 'items',
        'Range': '0-9999',
      },
    });
    if (!r.ok) throw new Error('Supabase ' + r.status);
    const tesis = await r.json();

    const escape = (s) => String(s || '').replace(/[<>&'"]/g, c => ({
      '<':'&lt;','>':'&gt;','&':'&amp;',"'":'&apos;','"':'&quot;'
    })[c]);

    const urls = tesis.map(t => {
      // Deep-link al SJF2 oficial — Google indexa esta URL y el contenido viene del rubro
      // Alternativa: /panel/jurisprudencia.html?id=<id> para landing en Tequio.
      const loc = `https://tequio.app/panel/jurisprudencia.html?id=${t.id}`;
      return `  <url>
    <loc>${escape(loc)}</loc>
    <changefreq>monthly</changefreq>
    <priority>0.6</priority>
  </url>`;
    });

    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.join('\n')}
</urlset>`;

    res.setHeader('Content-Type', 'application/xml; charset=utf-8');
    res.setHeader('Cache-Control', 'public, max-age=86400, s-maxage=86400'); // 24h
    res.status(200).send(xml);
  } catch (e) {
    res.status(500).send('error: ' + e.message);
  }
}
