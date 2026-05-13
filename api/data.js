// /api/data.js — endpoint Vercel para exponer Supabase al frontend
//
// Vistas soportadas:
//   GET  ?vista=dashboard          → tipo cambio, inflación, tasa, count leyes
//   GET  ?vista=banxico_historico&serie_id=SF43718[&dias=365]
//   GET  ?vista=inegi_estado&estado=09
//   GET  ?vista=inegi_comparador&indicador=1002000001
//   GET  ?vista=leyes_lista
//   GET  ?vista=clima              → 28 capitales con pronóstico
//   GET  ?vista=alertas            → alertas meteo vigentes
//   GET  ?vista=sequia             → 32 estados con % D0-D4
//   GET  ?vista=despachos[&area=X] → despachos verificados activos
//   POST ?vista=crear_lead         → inserta en leads_legales

export default async function handler(req, res) {
  const SUPABASE_URL = process.env.SUPABASE_URL?.replace(/\/$/, '');
  const ANON_KEY = process.env.SUPABASE_ANON_KEY;
  const SERVICE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!SUPABASE_URL || !ANON_KEY) {
    return res.status(500).json({ error: 'Supabase env vars missing' });
  }

  const vista = (req.query.vista || 'dashboard').toLowerCase();

  async function sb(path) {
    const r = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
      headers: {
        'apikey': ANON_KEY,
        'Authorization': `Bearer ${ANON_KEY}`,
        'Accept': 'application/json',
      },
    });
    if (!r.ok) throw new Error(`Supabase ${r.status}`);
    return r.json();
  }

  async function sbWrite(path, body) {
    const key = SERVICE_KEY || ANON_KEY;
    const r = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'apikey': key,
        'Authorization': `Bearer ${key}`,
        'Prefer': 'return=minimal',
      },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`Supabase write ${r.status}: ${await r.text().catch(()=>'')}`);
    return true;
  }

  try {
    if (req.method === 'POST' && vista === 'crear_lead') {
      const body = req.body || {};
      if (!body.descripcion || body.descripcion.length < 10) {
        return res.status(400).json({ error: 'descripcion requerida' });
      }
      await sbWrite('leads_legales', {
        descripcion: String(body.descripcion).slice(0, 5000),
        area_legal: String(body.area_legal || '').slice(0, 100),
        tipo_caso: String(body.tipo_caso || '').slice(0, 50),
        estado_usuario: body.estado_usuario || null,
        nombre_usuario: body.nombre_usuario || null,
        telefono_usuario: body.telefono_usuario || null,
        email_usuario: body.email_usuario || null,
        despacho_id: body.despacho_id || null,
        canal: body.canal || 'web',
      });
      return res.status(201).json({ ok: true });
    }

    if (vista === 'dashboard') {
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
      const mesesEs = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'];
      const fmtMes = (iso) => {
        if (!iso) return '';
        const [y, m] = iso.split('-');
        return `${mesesEs[parseInt(m,10)-1]} de ${y}`;
      };
      return res.status(200).json({
        dolar: dolarHoy ? { valor: dolarHoy.valor, fecha: dolarHoy.fecha, variacion_pct: variacion } : null,
        inflacion: inflacion?.[0] ? { valor: inflacion[0].valor, mes: fmtMes(inflacion[0].fecha) } : null,
        tasa_banxico: tasa?.[0] ? { valor: tasa[0].valor, fecha: tasa[0].fecha } : null,
        leyes_count: leyesCount?.[0]?.count || 0,
      });
    }

    if (vista === 'clima') {
      const rows = await sb('clima_municipal?order=temp_max.desc.nullslast&select=estado,municipio,temp_max,temp_min,prob_lluvia,desc_cielo,velocidad_viento,fecha_pronostico&limit=32');
      return res.status(200).json({ capitales: rows });
    }

    if (vista === 'alertas') {
      const rows = await sb('alertas_meteo?order=vigente_desde.desc&select=tipo,nombre,nivel,zona_afectada,descripcion,vigente_desde,vigente_hasta,fuente,url_oficial&limit=20');
      return res.status(200).json({ alertas: rows });
    }

    if (vista === 'sequia') {
      const rows = await sb('monitor_sequia?order=pct_sequia_extrema.desc.nullslast,pct_sequia_severa.desc.nullslast&select=fecha_corte,estado,nivel_sequia,pct_anomalo_seco,pct_sequia_moderada,pct_sequia_severa,pct_sequia_extrema,pct_sequia_excepcional');
      return res.status(200).json({ estados: rows });
    }

    if (vista === 'despachos') {
      const area = (req.query.area || '').toLowerCase();
      const rows = await sb('despachos_verificados?activo=eq.true&verificado=eq.true&order=rating.desc.nullslast&select=id,nombre,responsable,especialidades,estados,telefono,whatsapp,email,sitio_web,rating,num_resenas,primera_consulta_gratis,plan');
      let filtered = rows;
      if (area && area.length > 3) {
        const areaTokens = area.split(/[\s\/]+/).filter(t => t.length > 3);
        filtered = rows.filter(d => {
          const espec = (d.especialidades || []).join(' ').toLowerCase();
          return areaTokens.some(t => espec.includes(t));
        });
        if (!filtered.length) filtered = rows;
      }
      return res.status(200).json({ despachos: filtered });
    }

    if (vista === 'banxico_historico') {
      const serieId = req.query.serie_id || 'SF43718';
      const dias = Math.min(parseInt(req.query.dias || '365', 10), 730);
      const desde = new Date(Date.now() - dias * 86400000).toISOString().slice(0, 10);
      const rows = await sb(`econ_banxico?serie_id=eq.${serieId}&fecha=gte.${desde}&order=fecha.asc&select=fecha,valor,nombre,unidad`);
      return res.status(200).json({ serie_id: serieId, puntos: rows });
    }

    if (vista === 'inegi_estado') {
      const estado = req.query.estado || '0700';
      const rows = await sb(`demograficos_inegi?area_geografica=eq.${estado}&order=indicador_id.asc&select=indicador_id,nombre,valor,unidad,fecha,ubicacion`);
      return res.status(200).json({ estado, indicadores: rows });
    }

    if (vista === 'inegi_comparador') {
      const indicador = req.query.indicador || '1002000001';
      const rows = await sb(`demograficos_inegi?indicador_id=eq.${indicador}&nivel=eq.estado&order=valor.desc&select=area_geografica,ubicacion,valor,unidad`);
      return res.status(200).json({ indicador, estados: rows });
    }

    if (vista === 'leyes_lista') {
      const rows = await sb('leyes?order=nombre.asc&select=id,nombre,fuente,url&limit=100');
      return res.status(200).json({ leyes: rows });
    }

    return res.status(400).json({ error: 'Vista desconocida', vistas_disponibles: [
      'dashboard','clima','alertas','sequia','despachos','crear_lead',
      'banxico_historico','inegi_estado','inegi_comparador','leyes_lista'
    ]});
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
