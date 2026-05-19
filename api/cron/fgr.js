// Cron: Scrape FGR news weekly.
export default async function handler(req, res) {
  if (req.headers.authorization !== `Bearer ${process.env.CRON_SECRET}`) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  const SUPABASE_URL = process.env.SUPABASE_URL || 'https://mhsuihwjgtzxflesbnxv.supabase.co';
  const SERVICE_ROLE = process.env.SUPABASE_SERVICE_ROLE;
  if (!SERVICE_ROLE) return res.status(500).json({ error: 'Missing SUPABASE_SERVICE_ROLE' });

  const FGR_URL = 'https://www.fgr.org.mx/es/FGR/Comunicados';

  const MESES = {
    enero:'01',febrero:'02',marzo:'03',abril:'04',mayo:'05',junio:'06',
    julio:'07',agosto:'08',septiembre:'09',octubre:'10',noviembre:'11',diciembre:'12'
  };

  try {
    const html = await fetch(FGR_URL, {
      headers: { 'User-Agent': 'Mozilla/5.0 (Tequio/1.0 cron)' }
    }).then(r => r.text());

    // Buscar items: ámbito + fecha + Comunicado FGR NUM + título
    const items = [];
    const re = /(Nacional|Estatal)\s+(\d{1,2}) de ([a-záéíóú]+) de (\d{4})\s+Comunicado FGR (\d+)\s+([^\n<]{20,400})/gi;
    let m;
    while ((m = re.exec(html)) !== null) {
      const [_, ambito, dd, mesName, yyyy, num, titulo] = m;
      const mes = MESES[mesName.toLowerCase()];
      if (!mes) continue;
      const fecha = `${yyyy}-${mes}-${dd.padStart(2,'0')}`;
      items.push({
        titulo: `FGR DPE/${num}/${yyyy.slice(2)} · Nacional`,
        resumen: titulo.trim().slice(0, 500),
        url_oficial: FGR_URL,
        fuente: 'FGR',
        fuente_url: 'https://www.fgr.org.mx',
        ambito: ambito.toLowerCase() === 'nacional' ? 'nacional' : 'estatal',
        tema: 'seguridad',
        fecha_publicacion: fecha,
        hash_url: `fgr_${num}_${yyyy}`
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
