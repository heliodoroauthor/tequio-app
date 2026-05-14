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

    if (vista === 'presas') {
      // Trae histórico ordenado por fecha_corte DESC y deduplica al último por presa
      const rows = await sb('presas_cuencas?order=fecha_corte.desc&select=fecha_corte,presa,estado,capacidad_total_hm3,almacenamiento_hm3,pct_almacenamiento&limit=2000');
      const ultimasPorPresa = {};
      for (const r of rows) {
        if (!ultimasPorPresa[r.presa]) ultimasPorPresa[r.presa] = r;
      }
      const presas = Object.values(ultimasPorPresa)
        .filter(p => p.almacenamiento_hm3 != null)
        .sort((a, b) => (b.pct_almacenamiento ?? -1) - (a.pct_almacenamiento ?? -1));
      return res.status(200).json({ presas });
    }

    if (vista === 'diputados') {
      // Lista de los 500 diputados con filtros opcionales por estado y partido
      const filtroEntidad = req.query.entidad || '';
      const filtroPartido = req.query.partido || '';
      let query = 'politicos_diputados?order=entidad.asc,distrito.asc&select=dipt_id,nombre,partido,partido_codigo,entidad,distrito,principio_eleccion,email,foto_url,curricula_url,curul,reelecto,suplente,comisiones&limit=600';
      if (filtroEntidad) query += `&entidad=eq.${encodeURIComponent(filtroEntidad)}`;
      if (filtroPartido) query += `&partido=eq.${encodeURIComponent(filtroPartido)}`;
      const rows = await sb(query);
      // Calcular agregados rápidos
      const porPartido = {};
      const porEntidad = {};
      for (const d of rows) {
        if (d.partido) porPartido[d.partido] = (porPartido[d.partido] || 0) + 1;
        if (d.entidad) porEntidad[d.entidad] = (porEntidad[d.entidad] || 0) + 1;
      }
      return res.status(200).json({
        diputados: rows,
        total: rows.length,
        por_partido: porPartido,
        por_entidad: porEntidad,
      });
    }

    if (vista === 'votaciones') {
      // Votaciones nominales del pleno, más recientes primero
      const limit = Math.min(parseInt(req.query.limit || '50', 10), 200);
      const rows = await sb(`votaciones_diputados?order=fecha.desc,votacion_id.desc&select=votacion_id,fecha,periodo,asunto,tipo,total_si,total_no,total_abst,total_ausente,resultado,url_oficial&limit=${limit}`);
      return res.status(200).json({ votaciones: rows });
    }

    if (vista === 'mi_representante') {
      // Dado un dipt_id, devuelve el diputado + cómo votó en las últimas N votaciones
      const dipt_id = parseInt(req.query.dipt_id || '0', 10);
      if (!dipt_id) return res.status(400).json({ error: 'dipt_id requerido' });
      const limit = Math.min(parseInt(req.query.limit || '40', 10), 100);

      // 1) Info del diputado
      const dipRows = await sb(`politicos_diputados?dipt_id=eq.${dipt_id}&select=dipt_id,nombre,partido,partido_codigo,entidad,distrito,principio_eleccion,curul,email,foto_url,curricula_url,comisiones,reelecto`);
      const diputado = dipRows?.[0];
      if (!diputado) return res.status(404).json({ error: 'Diputado no encontrado' });

      // 2) Sus votos (últimas N votaciones)
      const votos = await sb(`votos_individuales?dipt_id=eq.${dipt_id}&order=votacion_id.desc&select=votacion_id,voto&limit=${limit * 3}`);

      // 3) Detalle de cada votación
      const votIds = (votos || []).map(v => v.votacion_id).slice(0, limit);
      const votacionesById = {};
      if (votIds.length) {
        const idsParam = votIds.join(',');
        const detalles = await sb(`votaciones_diputados?votacion_id=in.(${idsParam})&select=votacion_id,fecha,asunto,tipo,total_si,total_no,total_abst,resultado,url_oficial`);
        for (const d of (detalles || [])) votacionesById[d.votacion_id] = d;
      }

      // 4) Combinar: voto + detalles
      const historial = votos
        .filter(v => votacionesById[v.votacion_id])
        .slice(0, limit)
        .map(v => ({
          ...votacionesById[v.votacion_id],
          voto: v.voto,
        }))
        .sort((a, b) => (b.fecha || '').localeCompare(a.fecha || ''));

      // 5) Stats agregados
      const stats = { si: 0, no: 0, abst: 0, ausente: 0 };
      for (const v of (votos || [])) {
        if (stats[v.voto] !== undefined) stats[v.voto]++;
      }
      const total = stats.si + stats.no + stats.abst + stats.ausente;
      const asistencia_pct = total ? Math.round(((stats.si + stats.no + stats.abst) / total) * 100) : 0;

      return res.status(200).json({
        diputado,
        historial,
        stats: { ...stats, total, asistencia_pct },
      });
    }

    // ────────── SENADORES — Fase 3.1.B ──────────
    if (vista === 'senadores') {
      // Lista de los 128 senadores con filtros
      const filtroPartido = req.query.partido || '';
      const filtroEntidad = req.query.entidad || '';
      let query = 'politicos_senadores?order=nombre_completo.asc&select=id,nombre_completo,url,partido,entidad_federativa,tipo_eleccion,nombre_suplente,foto_url,email,telefono,direccion_oficina,cargo_especial,comisiones_secretario,comisiones_integrante&limit=200';
      if (filtroPartido) query += `&partido=eq.${encodeURIComponent(filtroPartido)}`;
      if (filtroEntidad) query += `&entidad_federativa=eq.${encodeURIComponent(filtroEntidad)}`;
      const rows = await sb(query);
      const porPartido = {};
      const porEntidad = {};
      let totalComisiones = 0;
      for (const s of (rows || [])) {
        if (s.partido) porPartido[s.partido] = (porPartido[s.partido] || 0) + 1;
        if (s.entidad_federativa) porEntidad[s.entidad_federativa] = (porEntidad[s.entidad_federativa] || 0) + 1;
        if (Array.isArray(s.comisiones_integrante)) totalComisiones += s.comisiones_integrante.length;
        if (Array.isArray(s.comisiones_secretario)) totalComisiones += s.comisiones_secretario.length;
      }
      return res.status(200).json({
        senadores: rows || [],
        total: (rows || []).length,
        por_partido: porPartido,
        por_entidad: porEntidad,
        total_comisiones: totalComisiones,
      });
    }

    if (vista === 'senador_detalle') {
      const id = parseInt(req.query.id || '0', 10);
      if (!id) return res.status(400).json({ error: 'id requerido' });
      const rows = await sb(`politicos_senadores?id=eq.${id}&select=*`);
      const senador = rows?.[0];
      if (!senador) return res.status(404).json({ error: 'Senador no encontrado' });
      // No mandar el embedding al cliente (768 floats = ~3KB)
      delete senador.embedding;
      return res.status(200).json({ senador });
    }

    if (vista === 'senadores_busqueda') {
      // Búsqueda semántica con embeddings vía Gemini
      const q = (req.query.q || '').trim();
      if (!q) return res.status(400).json({ error: 'query requerida' });
      const limit = Math.min(parseInt(req.query.limit || '20', 10), 50);
      // 1) Generar embedding del query con Gemini
      const GEMINI_KEY = process.env.GEMINI_API_KEY;
      if (!GEMINI_KEY) {
        // Fallback: búsqueda texto trigram
        const safeQ = encodeURIComponent(q);
        const rows = await sb(`politicos_senadores?or=(nombre_completo.ilike.*${safeQ}*,cargo_especial.ilike.*${safeQ}*)&select=id,nombre_completo,foto_url,partido,entidad_federativa,cargo_especial,comisiones_integrante&limit=${limit}`);
        return res.status(200).json({ senadores: rows || [], modo: 'texto', query: q });
      }
      try {
        const er = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key=${GEMINI_KEY}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model: 'models/gemini-embedding-001',
            content: { parts: [{ text: q }] },
            outputDimensionality: 768,
          }),
        });
        if (!er.ok) throw new Error('embed failed');
        const ej = await er.json();
        const emb = ej?.embedding?.values;
        if (!emb || emb.length !== 768) throw new Error('embed bad shape');
        // 2) RPC contra Supabase (vector search). Si no existe la RPC, fallback a texto.
        const url = `${process.env.SUPABASE_URL}/rest/v1/rpc/match_senadores`;
        const rr = await fetch(url, {
          method: 'POST',
          headers: {
            'apikey': process.env.SUPABASE_SERVICE_ROLE_KEY,
            'Authorization': `Bearer ${process.env.SUPABASE_SERVICE_ROLE_KEY}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ query_embedding: emb, match_count: limit }),
        });
        if (rr.ok) {
          const rows = await rr.json();
          return res.status(200).json({ senadores: rows || [], modo: 'semantica', query: q });
        }
        // RPC no existe → fallback texto
        const safeQ = encodeURIComponent(q);
        const rows = await sb(`politicos_senadores?or=(nombre_completo.ilike.*${safeQ}*,cargo_especial.ilike.*${safeQ}*)&select=id,nombre_completo,foto_url,partido,entidad_federativa,cargo_especial,comisiones_integrante&limit=${limit}`);
        return res.status(200).json({ senadores: rows || [], modo: 'texto_fallback', query: q });
      } catch (e) {
        const safeQ = encodeURIComponent(q);
        const rows = await sb(`politicos_senadores?or=(nombre_completo.ilike.*${safeQ}*,cargo_especial.ilike.*${safeQ}*)&select=id,nombre_completo,foto_url,partido,entidad_federativa,cargo_especial,comisiones_integrante&limit=${limit}`);
        return res.status(200).json({ senadores: rows || [], modo: 'texto_err', query: q, err: String(e).slice(0, 100) });
      }
    }

    if (vista === 'buscar_diputado') {
      // Busca diputados por estado y opcionalmente distrito
      const entidad = req.query.entidad || '';
      const distrito = req.query.distrito || '';
      if (!entidad) return res.status(400).json({ error: 'entidad requerida' });
      let query = `politicos_diputados?entidad=eq.${encodeURIComponent(entidad)}&order=distrito.asc&select=dipt_id,nombre,partido,distrito,principio_eleccion,foto_url`;
      if (distrito) query += `&distrito=eq.${encodeURIComponent(distrito)}`;
      const rows = await sb(query);
      return res.status(200).json({ diputados: rows });
    }

    // ────────── COMPRANET / CONTRATOS PÚBLICOS ──────────
    if (vista === 'contratos') {
      // Filtros: q, dependencia, proveedor, entidad, tipo_proc, monto_min, monto_max, limit
      const q = (req.query.q || '').trim();
      const dependencia = req.query.dependencia || '';
      const proveedor = req.query.proveedor || '';
      const entidad = req.query.entidad || '';
      const tipoProc = req.query.tipo_proc || '';
      const montoMin = req.query.monto_min ? parseFloat(req.query.monto_min) : null;
      const montoMax = req.query.monto_max ? parseFloat(req.query.monto_max) : null;
      const limit = Math.min(parseInt(req.query.limit || '50', 10), 200);
      const orden = req.query.orden || 'monto'; // monto | fecha | dependencia

      let query = 'contratos_publicos?select=ocid,dependencia,unidad_compradora,proveedor_rfc,proveedor_nombre,titulo,monto_mxn,fecha_firma,tipo_procedimiento,tipo_contrato,entidad_federativa,fuente_url,flag_adjudicacion_directa_alta';

      const conditions = [];
      if (q) conditions.push(`or=(titulo.ilike.*${encodeURIComponent(q)}*,descripcion.ilike.*${encodeURIComponent(q)}*,proveedor_nombre.ilike.*${encodeURIComponent(q)}*,dependencia.ilike.*${encodeURIComponent(q)}*)`);
      if (dependencia) conditions.push(`dependencia=ilike.*${encodeURIComponent(dependencia)}*`);
      if (proveedor) conditions.push(`or=(proveedor_nombre.ilike.*${encodeURIComponent(proveedor)}*,proveedor_rfc.ilike.*${encodeURIComponent(proveedor)}*)`);
      if (entidad) conditions.push(`entidad_federativa=eq.${encodeURIComponent(entidad)}`);
      if (tipoProc) conditions.push(`tipo_procedimiento=eq.${encodeURIComponent(tipoProc)}`);
      if (montoMin !== null) conditions.push(`monto_mxn=gte.${montoMin}`);
      if (montoMax !== null) conditions.push(`monto_mxn=lte.${montoMax}`);

      if (conditions.length) query += '&' + conditions.join('&');

      const orderMap = {
        monto: 'monto_mxn.desc.nullslast',
        fecha: 'fecha_firma.desc.nullslast',
        dependencia: 'dependencia.asc',
      };
      query += `&order=${orderMap[orden] || orderMap.monto}&limit=${limit}`;

      const rows = await sb(query);

      // Stats agregados separados (count + sum)
      let statsQuery = 'contratos_publicos?select=monto_mxn,tipo_procedimiento,dependencia';
      if (conditions.length) {
        // mismo conditions pero sin select de columnas pesadas
        statsQuery = 'contratos_publicos?select=monto_mxn,tipo_procedimiento,dependencia&' + conditions.join('&') + '&limit=10000';
      } else {
        statsQuery += '&limit=10000';
      }
      const statsRows = await sb(statsQuery);
      const monto_total = statsRows.reduce((s, r) => s + (parseFloat(r.monto_mxn) || 0), 0);
      const por_tipo = {};
      const por_dependencia = {};
      for (const r of statsRows) {
        if (r.tipo_procedimiento) por_tipo[r.tipo_procedimiento] = (por_tipo[r.tipo_procedimiento] || 0) + 1;
        if (r.dependencia) por_dependencia[r.dependencia] = (por_dependencia[r.dependencia] || 0) + 1;
      }

      return res.status(200).json({
        contratos: rows,
        total_resultados: statsRows.length,
        monto_total,
        por_tipo,
        top_dependencias: Object.entries(por_dependencia).sort((a,b)=>b[1]-a[1]).slice(0,10),
      });
    }

    if (vista === 'contrato_detalle') {
      const ocid = req.query.ocid;
      if (!ocid) return res.status(400).json({ error: 'ocid requerido' });
      const rows = await sb(`contratos_publicos?ocid=eq.${encodeURIComponent(ocid)}`);
      const contrato = rows?.[0];
      if (!contrato) return res.status(404).json({ error: 'No encontrado' });

      // Otros contratos del mismo proveedor
      let relacionados = [];
      if (contrato.proveedor_rfc) {
        relacionados = await sb(`contratos_publicos?proveedor_rfc=eq.${encodeURIComponent(contrato.proveedor_rfc)}&ocid=neq.${encodeURIComponent(ocid)}&order=monto_mxn.desc.nullslast&limit=10&select=ocid,dependencia,titulo,monto_mxn,fecha_firma,tipo_procedimiento`);
      }
      return res.status(200).json({ contrato, relacionados });
    }

    if (vista === 'proveedores_top') {
      // Lupita Diconsa: top proveedores agregados
      const limit = Math.min(parseInt(req.query.limit || '30', 10), 100);
      const dependencia = req.query.dependencia || '';
      const orden = req.query.orden || 'monto'; // monto | contratos | dependencias

      let query = 'proveedores_agregados?select=proveedor_rfc,proveedor_nombre,num_contratos,monto_total,num_dependencias,monto_promedio,contratos_ad_directa,flags_ad_alta,dependencias';
      const orderMap = {
        monto: 'monto_total.desc.nullslast',
        contratos: 'num_contratos.desc',
        dependencias: 'num_dependencias.desc',
      };
      query += `&order=${orderMap[orden] || orderMap.monto}&limit=${limit}`;
      // Filtro por dependencia (string array contains)
      if (dependencia) query += `&dependencias=cs.{${encodeURIComponent(dependencia)}}`;

      const rows = await sb(query);
      return res.status(200).json({ proveedores: rows });
    }

    if (vista === 'proveedor_detalle') {
      const rfc = req.query.rfc || '';
      const nombre = req.query.nombre || '';
      if (!rfc && !nombre) return res.status(400).json({ error: 'rfc o nombre requerido' });

      let condicion = '';
      if (rfc) condicion = `proveedor_rfc=eq.${encodeURIComponent(rfc)}`;
      else condicion = `proveedor_nombre=ilike.*${encodeURIComponent(nombre)}*`;

      const contratos = await sb(`contratos_publicos?${condicion}&order=monto_mxn.desc.nullslast&limit=50&select=ocid,dependencia,titulo,monto_mxn,fecha_firma,tipo_procedimiento,tipo_contrato`);
      const agg = await sb(`proveedores_agregados?${condicion}&limit=1`);

      return res.status(200).json({
        agregado: agg?.[0] || null,
        contratos,
      });
    }

    if (vista === 'compranet_stats') {
      // Resumen global para hero
      const rows = await sb('contratos_publicos?select=monto_mxn,tipo_procedimiento,flag_adjudicacion_directa_alta&limit=300000');
      const total = rows.length;
      const monto_total = rows.reduce((s, r) => s + (parseFloat(r.monto_mxn) || 0), 0);
      const adjudicacion_directa = rows.filter(r => r.tipo_procedimiento === 'adjudicacion_directa').length;
      const flags_rojos = rows.filter(r => r.flag_adjudicacion_directa_alta).length;
      return res.status(200).json({ total, monto_total, adjudicacion_directa, flags_rojos });
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
      'dashboard','clima','alertas','sequia','presas','diputados','votaciones',
      'mi_representante','buscar_diputado','senadores','senador_detalle','senadores_busqueda',
      'contratos','contrato_detalle','proveedores_top','proveedor_detalle','compranet_stats',
      'despachos','crear_lead',
      'banxico_historico','inegi_estado','inegi_comparador','leyes_lista'
    ]});
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
}
