// /api/sitemap-leyes — Sitemap dinámico de las leyes top.
// Devuelve XML con las leyes (federal/estatal/municipal) más relevantes.
// Cache: 12 horas. Soporta hasta 50,000 URLs por sitemap (límite Google).

export default async function handler(req, res) {
  const SUPABASE_URL = process.env.SUPABASE_URL?.replace(/\/$/, '');
  const ANON_KEY = process.env.SUPABASE_ANON_KEY;
  if (!SUPABASE_URL || !ANON_KEY) {
    res.status(500).send('config error');
    return;
  }

  try {
    // Top leyes: federales primero, luego estatales y municipales (con texto>0)
    const r = await fetch(`${SUPABASE_URL}/rest/v1/leyes?select=id,nombre,ambito,entidad&order=ambito.asc,id.asc&limit=10000`, {
      headers: {
        'apikey': ANON_KEY,
        'Authorization': `Bearer ${ANON_KEY}`,
        'Range-Unit': 'items',
        'Range': '0-9999',
      },
    });
    if (!r.ok) throw new Error('Supabase ' + r.status);
    const leyes = await r.json();

    // Generate XML
    const escape = (s) => String(s || '').replace(/[<>&'"]/g, c => ({
      '<':'&lt;','>':'&gt;','&':'&amp;',"'":'&apos;','"':'&quot;'
    })[c]);

    const urls = leyes.map(l => {
      // Use a deep link: /panel/buscador.html?ley=<id>
      // The buscador panel handles deep-link routing to specific leyes.
      const loc = `https://tequio.app/panel/buscador.html?ley=${l.id}`;
      const freq = l.ambito === 'federal' ? 'weekly' : 'monthly';
      const priority = l.ambito === 'federal' ? '0.85' : (l.ambito === 'estatal' ? '0.75' : '0.65');
      return `  <url>
    <loc>${escape(loc)}</loc>
    <changefreq>${freq}</changefreq>
    <priority>${priority}</priority>
  </url>`;
    });

    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.join('\n')}
</urlset>`;

    res.setHeader('Content-Type', 'application/xml; charset=utf-8');
    res.setHeader('Cache-Control', 'public, max-age=43200, s-maxage=43200'); // 12h
    res.status(200).send(xml);
  } catch (e) {
    res.status(500).send('error: ' + e.message);
  }
}
