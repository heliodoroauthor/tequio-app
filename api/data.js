// /api/data.js
// Endpoint Vercel para que el Dashboard y otras páginas obtengan datos vivos
// de Supabase sin exponer la service_role key al navegador.
//
// Uso desde el frontend:
//   fetch('/api/data?vista=dashboard')
//   fetch('/api/data?vista=banxico_historico&serie_id=SF43718')
//   fetch('/api/data?vista=inegi_estado&estado=09')

export default async function handler(req, res) {
  const SUPABASE_URL = process.env.SUPABASE_URL?.replace(/\/$/, '');
  const ANON_KEY = process.env.SUPABASE_ANON_KEY;
  if (!SUPABASE_URL || !ANON_KEY) {
    return res.status(500).json({ error: 'Supabase env vars missing' });
  }

  const vista = (req.query.vista || 'dashboard').toLowerCase();

  // Helper para llamar PostgREST
  async function sb(path) {
    const r = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
      headers: {
        'apikey': ANON_KEY,
        'Authorization': `Bearer ${ANON_KEY}`,
        'Accept': 'application/json',
      },
    });
    if (!r.ok) throw new Error(`Supabase ${r.status}: ${await r.text().catch(()=>'')}`);
    return r.json();
  }

  try {
    if (vista === 'dashboard') {
      // Tipo de cambio: últimos 2 puntos para calcular variación
      const [dolar, inflacion, tasa, leyesCount] = await Promise.all([
        sb('econ_banxico?serie_id=eq.SF43718&order=fecha.desc&limit=2&select=valor,fecha'),
        sb('econ_banxico?serie_id=eq.SP30577&order=fecha.desc&limit=1&select=valor,fecha'),
        sb('econ_banxico?serie_id=eq.SF61745&order=fecha.desc&limit=1&select=valor,fecha'),
        sb('leyes?select=count'),
      ]);

      const dolarHoy = dolar?.[0];
      const dolarAyer = dolar?.[1];
      const variacion = (dolarHoy && dolarAyer)
        ? ((dolarHoy.valor - dolarAyer.valor) / dolarAyer.valor) * 100
        : 0;

      // Formato mes legible: "abril de 2026"
      const mesesEs = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'];
      const fmtMes = (iso) => {
        if (!iso) return '';
        const [y,m] = iso.split('-');
        return `${mesesEs[parseInt(m,10)-1]} de ${y}`;
      };

      return res.status(200).json({
        dolar: dolarHoy ? { valor: dolarHoy.valor, fecha: dolarHoy.fecha, variacion_pct: variacion } : null,
        inflacion: inflacion?.[0] ? { valor: inflacion[0].valor, mes: fmtMes(inflacion[0].fecha) } : null,
        tasa_banxico: tasa?.[0] ? { valor: tasa[0].valor, fecha: tasa[0].fecha } : null,
        leyes_count: leyesCount?.[0]?.count || 0,
      });
    }

    if (vista === 'banxico_historico') {
      const serieId = req.query.serie_id || 'SF43718';
      const dias = Math.min(parseInt(req.query.dias || '365', 10), 730);
      const desde = new Date(Date.now() - dias * 86400000).toISOString().slice(0, 10);
      const rows = await sb(
        `econ_banxico?serie_id=eq.${serieId}&fecha=gte.${desde}&order=fecha.asc&select=fecha,valor,nombre,unidad`
      );
      return res.status(200).json({ serie_id: serieId, puntos: rows });
    }

    if (vista === 'inegi_estado') {
      const estado = req.query.estado || '0700'; // nacional default
      const rows = await sb(
        `demograficos_inegi?area_geografica=eq.${estado}&order=indicador_id.asc&select=indicador_id,nombre,valor,unidad,fecha,ubicacion`
      );
      return res.status(200).json({ estado, indicadores: rows });
    }

    if (vista === 'inegi_comparador') {
      // Para gráfica de barras: un indicador, todos los estados
      const indicador = req.query.indicador || '1002000001';
      const rows = await sb(
        `demograficos_inegi?indicador_id=eq.${indicador}&nivel=eq.estado&order=valor.desc&select=area_geografica,ubicacion,valor,unidad`
      );
      return res.status(200).json({ indicador, estados: rows });
    }

    if (vista === 'leyes_lista') {
      const rows = await sb('leyes?order=nombre.asc&select=id,nombre,fuente,url&limit=100');
      return res.status(200).json({ leyes: rows });
    }

    return res.status(400).json({ error: 'Vista desconocida', vistas_disponibles: [
      'dashboard', 'banxico_historico', 'inegi_estado', 'inegi_comparador', 'leyes_lista'
    ]});
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
