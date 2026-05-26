 import nodeCrypto from 'node:crypto';

export const config = { api: { bodyParser: { sizeLimit: '5mb' } } };

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
        'Range-Unit': 'items',
        'Range': '0-49999',
      },
    });
    if (!r.ok) throw new Error(`Supabase ${r.status}`);
    return r.json();
  }

  async function sbCount(path) {
    // Devuelve el total exacto de filas que matchean (vía Content-Range).
    try {
      const r = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
        method: 'HEAD',
        headers: {
          'apikey': ANON_KEY,
          'Authorization': `Bearer ${ANON_KEY}`,
          'Prefer': 'count=exact',
          'Range-Unit': 'items',
          'Range': '0-0',
        },
      });
      const cr = r.headers.get('content-range') || '';
      const m = cr.match(/\/(\d+)$/);
      return m ? parseInt(m[1], 10) : null;
    } catch { return null; }
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

// -- Buscador global --
    if (vista === 'buscador_global') {
      const q = (req.query.q || '').trim();
      if (!q || q.length < 2) return res.status(200).json({ items: [] });
      const pattern = '*' + encodeURIComponent(q) + '*';
      const [leyes, dips, muns, iniciativas, debates] = await Promise.all([
        sb(`leyes?titulo=ilike.${pattern}&select=id,titulo,tipo&limit=5`).catch(() => []),
        sb(`politicos_diputados?nombre=ilike.${pattern}&select=nombre,partido,entidad&limit=5`).catch(() => []),
        sb(`municipios?nombre=ilike.${pattern}&select=clave_inegi,nombre,nombre_estado&limit=5`).catch(() => []),
        sb(`iniciativas?titulo=ilike.${pattern}&select=id,titulo,tipo&limit=3`).catch(() => []),
        sb(`debates?titulo=ilike.${pattern}&select=id,slug,titulo&limit=3`).catch(() => [])
      ]);
      const items = [
        ...(leyes || []).map(l => ({ tipo: 'ley', titulo: l.titulo, sub: l.tipo, link: '/panel/buscador-sat.html' })),
        ...(dips || []).map(d => ({ tipo: 'diputado', titulo: d.nombre, sub: (d.partido || '') + ' \u00b7 ' + (d.entidad || ''), link: '#' })),
        ...(muns || []).map(m => ({ tipo: 'municipio', titulo: m.nombre, sub: m.nombre_estado, link: '/panel/municipio.html?clave_inegi=' + m.clave_inegi + '&embedded=1' })),
        ...(iniciativas || []).map(i => ({ tipo: 'iniciativa', titulo: i.titulo, sub: i.tipo, link: i.tipo === 'voto_congreso' ? '/panel/votar.html?embedded=1' : '/panel/iniciativa-firmar.html?embedded=1' })),
        ...(debates || []).map(db => ({ tipo: 'debate', titulo: db.titulo, sub: 'Debate', link: '/panel/debates.html?id=' + db.id + '&embedded=1' }))
      ];
      return res.status(200).json({ items, total: items.length, query: q });
    }

        if (vista === 'dashboard') {
      const [dolar, inflacion, tasa, leyesCount] = await Promise.all([
        sb('econ_banxico?serie_id=eq.SF43718&order=fecha.desc&limit=2&select=valor,fecha'),
        sb('econ_banxico?serie_id=eq.SP1&order=fecha.desc&limit=13&select=valor,fecha'),
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
        inflacion: (inflacion && inflacion.length >= 13) ? { valor: ((inflacion[0].valor - inflacion[12].valor) / inflacion[12].valor) * 100, mes: fmtMes(inflacion[0].fecha), tipo: "anual_inpc_computado" } : null,
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

    if (vista === 'indicadores_fiscales') {
      const rows = await sb('indicadores_fiscales?order=orden.asc&select=slug,label,valor_display,valor_numerico,cambio_pct,cambio_direccion,cambio_label,color_var,fuente,fuente_url,periodo,fecha_dato,fecha_publicacion');
      return res.status(200).json({ indicadores: rows });
    }

    if (vista === 'sequia') {
      const rows = await sb('monitor_sequia?order=pct_sequia_extrema.desc.nullslast,pct_sequia_severa.desc.nullslast&select=fecha_corte,estado,nivel_sequia,pct_anomalo_seco,pct_sequia_moderada,pct_sequia_severa,pct_sequia_extrema,pct_sequia_excepcional');
      return res.status(200).json({ estados: rows });
    }

    if (vista === 'presas') {
      // SINA (98.6% data útil) > CONAGUA SIH (1.9% útil). Priorizamos SINA.
      // 1) Tomamos lo último de SINA por presa
      // 2) Para presas que SINA no reporta, fallback a CONAGUA SIH más reciente
      const sinaRows = await sb('presas_cuencas?fuente=eq.SINA&order=fecha_corte.desc&select=fecha_corte,presa,estado,capacidad_total_hm3,almacenamiento_hm3,pct_almacenamiento,fuente,region_hidrologica,latitud,longitud&limit=2000');
      const conaguaRows = await sb('presas_cuencas?fuente=eq.CONAGUA%20SIH&order=fecha_corte.desc&select=fecha_corte,presa,estado,capacidad_total_hm3,almacenamiento_hm3,pct_almacenamiento,fuente,region_hidrologica,latitud,longitud&limit=2000');
      const byNombre = {};
      for (const r of sinaRows) {
        if (!byNombre[r.presa]) byNombre[r.presa] = r;
      }
      for (const r of conaguaRows) {
        if (!byNombre[r.presa] && r.almacenamiento_hm3 != null) byNombre[r.presa] = r;
      }
      const presas = Object.values(byNombre)
        .filter(p => p.almacenamiento_hm3 != null && p.pct_almacenamiento != null)
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

    // ════════════ VOTACIÓN CIUDADANA — Fase 4.1.B ════════════

    if (vista === 'registrar_ciudadano') {
      if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
      let body = req.body;
      if (typeof body === 'string') { try { body = JSON.parse(body); } catch { body = {}; } }
      const { cookie_id, entidad, distrito, declara_mayor } = body || {};
      if (!cookie_id || !entidad || distrito === undefined) {
        return res.status(400).json({ error: 'cookie_id, entidad y distrito requeridos' });
      }
      if (!declara_mayor) {
        return res.status(400).json({ error: 'Debes declarar ser mayor de 18 años para participar.' });
      }
      const distritoNum = parseInt(distrito, 10);
      if (isNaN(distritoNum) || distritoNum < 1 || distritoNum > 50) {
        return res.status(400).json({ error: 'Distrito inválido (1-50).' });
      }
      // UUID v4 sanity check
      if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(cookie_id)) {
        return res.status(400).json({ error: 'cookie_id inválido (no es UUID).' });
      }
      const ip = (req.headers['x-forwarded-for'] || req.headers['x-real-ip'] || '').split(',')[0].trim();
      const ua = (req.headers['user-agent'] || '');
      // Hash IP + UA con crypto.subtle (Edge runtime) o crypto si está en Node
      const sha256 = (s) => nodeCrypto.createHash('sha256').update(s || 'unknown').digest('hex');
      const ip_hash = sha256(ip);
      const ua_hash = sha256(ua);
      // UPSERT directo — usar ANON_KEY (Vercel) con RLS permisivo para registro
      const KEY = SERVICE_KEY || ANON_KEY;
      const url = `${SUPABASE_URL}/rest/v1/usuarios_ciudadanos?on_conflict=cookie_id`;
      const r = await fetch(url, {
        method: 'POST',
        headers: {
          'apikey': KEY,
          'Authorization': `Bearer ${KEY}`,
          'Content-Type': 'application/json',
          'Prefer': 'resolution=merge-duplicates,return=representation',
        },
        body: JSON.stringify({
          cookie_id,
          entidad,
          distrito: distritoNum,
          ip_signup_hash: ip_hash,
          user_agent_hash: ua_hash,
          declara_mayor_edad: true,
          last_seen: new Date().toISOString(),
        }),
      });
      if (!r.ok) {
        const errText = await r.text();
        return res.status(500).json({ error: 'No se pudo registrar', detail: errText.slice(0, 200) });
      }
      const data = await r.json();
      return res.status(200).json({ ok: true, usuario_id: data?.[0]?.id, entidad, distrito: distritoNum });
    }

    if (vista === 'me') {
      // Devuelve datos del usuario logueado (por cookie_id)
      const cookie_id = req.query.cookie_id || '';
      if (!cookie_id || !/^[0-9a-f-]{36}$/i.test(cookie_id)) {
        return res.status(200).json({ logueado: false });
      }
      const rows = await sb(`usuarios_ciudadanos?cookie_id=eq.${cookie_id}&select=id,entidad,distrito,declara_mayor_edad,created_at`);
      if (!rows || !rows.length) return res.status(200).json({ logueado: false });
      const u = rows[0];
      // Cuenta cuántos votos ha emitido
      const votos = await sb(`votos_ciudadanos?usuario_id=eq.${u.id}&select=votacion_pendiente_id`);
      return res.status(200).json({
        logueado: true,
        usuario: u,
        num_votos: (votos || []).length,
      });
    }

    if (vista === 'votaciones_pendientes') {
      const limit = Math.min(parseInt(req.query.limit || '20', 10), 100);
      const estado = req.query.estado || 'abierta';
      const cookie_id = req.query.cookie_id || '';
      const rows = await sb(`votaciones_pendientes?estado=eq.${encodeURIComponent(estado)}&order=fecha_propuesta.desc&select=id,titulo,asunto_corto,descripcion,materia,fecha_propuesta,fecha_votacion,gaceta_url,votacion_id,estado&limit=${limit}`);
      const resultados = {};
      const mi_voto = {};
      if (rows && rows.length) {
        const ids = rows.map(r => r.id).join(',');
        const votosRows = await sb(`votos_ciudadanos?votacion_pendiente_id=in.(${ids})&select=votacion_pendiente_id,voto`);
        for (const v of votosRows || []) {
          if (!resultados[v.votacion_pendiente_id]) resultados[v.votacion_pendiente_id] = { si: 0, no: 0, abst: 0, total: 0 };
          resultados[v.votacion_pendiente_id][v.voto] = (resultados[v.votacion_pendiente_id][v.voto] || 0) + 1;
          resultados[v.votacion_pendiente_id].total++;
        }
        if (cookie_id && /^[0-9a-f-]{36}$/i.test(cookie_id)) {
          const userRows = await sb(`usuarios_ciudadanos?cookie_id=eq.${cookie_id}&select=id`);
          const uid = userRows?.[0]?.id;
          if (uid) {
            const misVotos = await sb(`votos_ciudadanos?usuario_id=eq.${uid}&votacion_pendiente_id=in.(${ids})&select=votacion_pendiente_id,voto`);
            for (const v of misVotos || []) mi_voto[v.votacion_pendiente_id] = v.voto;
          }
        }
      }
      return res.status(200).json({ votaciones: rows || [], resultados, mi_voto });
    }

    if (vista === 'quemones_ranking') {
      // Top diputados con más quemones
      const limit = Math.min(parseInt(req.query.limit || '50', 10), 200);
      const partido = req.query.partido || '';
      const entidad = req.query.entidad || '';
      let q = `quemones_ranking?order=num_quemones.desc,pct_quemones.desc&select=dipt_id,nombre,partido,entidad,distrito,foto_url,num_quemones,total_votaciones_evaluadas,pct_quemones&limit=${limit}`;
      if (partido) q += `&partido=eq.${encodeURIComponent(partido)}`;
      if (entidad) q += `&entidad=eq.${encodeURIComponent(entidad)}`;
      // Solo mostrar diputados con al menos 1 evaluación
      q += `&total_votaciones_evaluadas=gt.0`;
      const rows = await sb(q);
      // Stats globales
      const stats = await sb('quemones?select=es_quemon');
      const totalIncidentes = (stats || []).length;
      const totalQuemones = (stats || []).filter(r => r.es_quemon).length;
      return res.status(200).json({
        ranking: rows || [],
        total_diputados_evaluados: (rows || []).length,
        total_incidentes: totalIncidentes,
        total_quemones: totalQuemones,
      });
    }

    if (vista === 'quemon_detalle') {
      // Detalle de incidentes para un diputado
      const dipt_id = parseInt(req.query.dipt_id || '0', 10);
      if (!dipt_id) return res.status(400).json({ error: 'dipt_id requerido' });
      const rows = await sb(`quemones?dipt_id=eq.${dipt_id}&order=created_at.desc&select=*,votaciones_pendientes(asunto_corto,titulo,materia,fecha_propuesta),votaciones_diputados(asunto,fecha,resultado)`);
      // Info del diputado
      const dipRows = await sb(`politicos_diputados?dipt_id=eq.${dipt_id}&select=dipt_id,nombre,partido,entidad,distrito,foto_url`);
      return res.status(200).json({
        diputado: dipRows?.[0] || null,
        incidentes: rows || [],
      });
    }

    if (vista === 'mi_rep_vs_yo') {
      // Cruce: cómo voté yo (votos_ciudadanos) vs cómo votó mi diputado (votos_individuales)
      const cookie_id = req.query.cookie_id || '';
      if (!cookie_id || !/^[0-9a-f-]{36}$/i.test(cookie_id)) return res.status(400).json({ error: 'cookie_id requerido' });
      // 1) Usuario
      const userRows = await sb(`usuarios_ciudadanos?cookie_id=eq.${cookie_id}&select=id,entidad,distrito`);
      const u = userRows?.[0];
      if (!u) return res.status(200).json({ logueado: false });
      // 2) Mi diputado (por entidad+distrito)
      const dipRows = await sb(`politicos_diputados?entidad=eq.${encodeURIComponent(u.entidad)}&distrito=eq.${u.distrito}&select=dipt_id,nombre,partido,foto_url&limit=1`);
      const dip = dipRows?.[0];
      if (!dip) return res.status(200).json({ logueado: true, sin_diputado: true, usuario: u });
      // 3) Mis votos ciudadanos
      const misVotos = await sb(`votos_ciudadanos?usuario_id=eq.${u.id}&select=votacion_pendiente_id,voto`);
      if (!misVotos || !misVotos.length) {
        return res.status(200).json({ logueado: true, usuario: u, diputado: dip, comparaciones: [], stats: { total: 0, coincide: 0, contra: 0, pct: 0 } });
      }
      // 4) Para cada voto mío, busco la votación pendiente → votacion_id → voto de mi diputado
      const pendienteIds = misVotos.map(v => v.votacion_pendiente_id).join(',');
      const pendientes = await sb(`votaciones_pendientes?id=in.(${pendienteIds})&select=id,asunto_corto,titulo,materia,votacion_id`);
      const pendientesById = {};
      for (const p of pendientes || []) pendientesById[p.id] = p;
      const votacionesIds = (pendientes || []).map(p => p.votacion_id).filter(Boolean).join(',');
      const votosDip = votacionesIds ? await sb(`votos_individuales?dipt_id=eq.${dip.dipt_id}&votacion_id=in.(${votacionesIds})&select=votacion_id,voto`) : [];
      const votoDipPorVotId = {};
      for (const v of votosDip || []) votoDipPorVotId[v.votacion_id] = v.voto;
      // 5) Build comparaciones
      const comparaciones = [];
      let coincide = 0, contra = 0;
      for (const mv of misVotos) {
        const p = pendientesById[mv.votacion_pendiente_id];
        if (!p) continue;
        const voto_dip = p.votacion_id ? votoDipPorVotId[p.votacion_id] : null;
        const yo = mv.voto;
        let estado = 'pendiente';
        if (voto_dip === yo) { estado = 'coincide'; coincide++; }
        else if (voto_dip && voto_dip !== yo && voto_dip !== 'ausente') { estado = 'contra'; contra++; }
        else if (voto_dip === 'ausente') { estado = 'ausente'; }
        comparaciones.push({
          votacion_pendiente_id: p.id,
          asunto: p.asunto_corto || p.titulo,
          materia: p.materia,
          mi_voto: yo,
          voto_diputado: voto_dip,
          estado,
        });
      }
      const total = coincide + contra;
      return res.status(200).json({
        logueado: true,
        usuario: u,
        diputado: dip,
        comparaciones,
        stats: {
          total_votos_emitidos: misVotos.length,
          comparables: total,
          coincide,
          contra,
          pct_coincide: total ? Math.round(100 * coincide / total) : 0,
        },
      });
    }

    if (vista === 'emitir_voto') {
      if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
      let body = req.body;
      if (typeof body === 'string') { try { body = JSON.parse(body); } catch { body = {}; } }
      const { cookie_id, entidad, distrito, votacion_pendiente_id, voto, declara_mayor } = body || {};
      if (!cookie_id || !entidad || !distrito || !votacion_pendiente_id || !voto) {
        return res.status(400).json({ error: 'Faltan campos.' });
      }
      if (!/^[0-9a-f-]{36}$/i.test(cookie_id)) return res.status(400).json({ error: 'cookie_id inválido.' });
      if (!['si', 'no', 'abst'].includes(voto)) return res.status(400).json({ error: 'voto debe ser si/no/abst.' });

      const ip = (req.headers['x-forwarded-for'] || req.headers['x-real-ip'] || '').split(',')[0].trim();
      const ua = (req.headers['user-agent'] || '');
      const sha256 = (s) => nodeCrypto.createHash('sha256').update(s || 'unknown').digest('hex');
      const ip_hash = sha256(ip);
      const ua_hash = sha256(ua);

      const KEY_VOTO = SERVICE_KEY || ANON_KEY;
      const url = `${SUPABASE_URL}/rest/v1/rpc/emitir_voto_ciudadano`;
      const r = await fetch(url, {
        method: 'POST',
        headers: {
          'apikey': KEY_VOTO,
          'Authorization': `Bearer ${KEY_VOTO}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          p_cookie_id: cookie_id,
          p_entidad: entidad,
          p_distrito: parseInt(distrito, 10),
          p_votacion_pendiente_id: parseInt(votacion_pendiente_id, 10),
          p_voto: voto,
          p_ip_hash: ip_hash,
          p_ua_hash: ua_hash,
          p_declara_mayor: !!declara_mayor,
        }),
      });
      const result = await r.json();
      return res.status(r.ok ? 200 : 400).json(result);
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
      const offset = Math.max(parseInt(req.query.offset || '0', 10), 0);
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
      query += `&order=${orderMap[orden] || orderMap.monto}&limit=${limit}&offset=${offset}`;

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
      // Total exacto (PostgREST count=exact), independiente del cap del statsRows
      const countPath = `contratos_publicos?select=ocid${conditions.length ? '&' + conditions.join('&') : ''}`;
      const totalExacto = await sbCount(countPath);
      const monto_total = statsRows.reduce((s, r) => s + (parseFloat(r.monto_mxn) || 0), 0);
      const por_tipo = {};
      const por_dependencia = {};
      for (const r of statsRows) {
        if (r.tipo_procedimiento) por_tipo[r.tipo_procedimiento] = (por_tipo[r.tipo_procedimiento] || 0) + 1;
        if (r.dependencia) por_dependencia[r.dependencia] = (por_dependencia[r.dependencia] || 0) + 1;
      }

      return res.status(200).json({
        contratos: rows,
        total_resultados: (totalExacto != null ? totalExacto : statsRows.length),
        offset,
        limit,
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

    // ── Ombligo · INAH (Zonas Arqueológicas + Museos) ──
    if (vista === 'ombligo_zonas') {
      const estado = (req.query.estado || '').trim();
      let path = 'inah_zonas_arqueologicas?select=*&order=estado.asc,nombre.asc&limit=300';
      if (estado) path += `&estado=eq.${encodeURIComponent(estado)}`;
      const rows = await sb(path);
      const por_estado = rows.reduce((acc, z) => { acc[z.estado] = (acc[z.estado] || 0) + 1; return acc; }, {});
      return res.status(200).json({ zonas: rows, total: rows.length, por_estado });
    }
    if (vista === 'lenguas') {
      const rows = await sb('inali_lenguas?select=*&order=hablantes.desc.nullslast&limit=80');
      const por_familia = rows.reduce((acc, l) => { acc[l.familia] = (acc[l.familia] || 0) + 1; return acc; }, {});
      const por_riesgo = rows.reduce((acc, l) => { acc[l.riesgo] = (acc[l.riesgo] || 0) + 1; return acc; }, {});
      return res.status(200).json({ lenguas: rows, total: rows.length, por_familia, por_riesgo });
    }
    if (vista === 'unesco') {
      const rows = await sb('unesco_mexico?select=*&order=anio_inscripcion.asc.nullslast,nombre.asc&limit=100');
      const por_tipo = rows.reduce((acc, s) => { acc[s.tipo] = (acc[s.tipo] || 0) + 1; return acc; }, {});
      return res.status(200).json({ sitios: rows, total: rows.length, por_tipo });
    }
    if (vista === 'ombligo_museos') {
      const estado = (req.query.estado || '').trim();
      let path = 'inah_museos?select=*&order=estado.asc,nombre.asc&limit=300';
      if (estado) path += `&estado=eq.${encodeURIComponent(estado)}`;
      const rows = await sb(path);
      return res.status(200).json({ museos: rows, total: rows.length });
    }

    // ── Noticias Cívicas (DOF + SCJN + SEGOB + Presidencia + Diputados) ──
    if (vista === 'noticias') {
      const q = (req.query.q || '').trim();
      const fuente = (req.query.fuente || '').trim();
      const tema = (req.query.tema || '').trim();
      const ambito = (req.query.ambito || '').trim();
      let path = 'noticias_civicas?select=id,titulo,resumen,url_oficial,fuente,fuente_url,ambito,tema,fecha_publicacion&order=fecha_publicacion.desc.nullslast&limit=1000';
      if (fuente) path += `&fuente=eq.${encodeURIComponent(fuente)}`;
      if (tema) path += `&tema=eq.${encodeURIComponent(tema)}`;
      if (ambito) path += `&ambito=eq.${encodeURIComponent(ambito)}`;
      if (q && q.length > 2) path += `&or=(titulo.ilike.*${encodeURIComponent(q)}*,resumen.ilike.*${encodeURIComponent(q)}*)`;
      const rows = await sb(path);
      return res.status(200).json({
        noticias: rows,
        total: rows.length,
        por_fuente: rows.reduce((acc, n) => {
          acc[n.fuente] = (acc[n.fuente] || 0) + 1;
          return acc;
        }, {}),
      });
    }

    // ── Leyes (Federales + Estatales) ──
    if (vista === 'leyes') {
      const q = (req.query.q || '').trim();
      const ambito = (req.query.ambito || '').trim();
      const entidad = (req.query.entidad || '').trim();
      let path = 'leyes?select=id,nombre,fuente,url,fecha_publicacion,texto,ambito,entidad,tipo&order=ambito.asc,nombre.asc&limit=1000';
      if (ambito) path += `&ambito=eq.${encodeURIComponent(ambito)}`;
      if (entidad) path += `&entidad=eq.${encodeURIComponent(entidad)}`;
      if (q && q.length > 2) path += `&or=(nombre.ilike.*${encodeURIComponent(q)}*,texto.ilike.*${encodeURIComponent(q)}*)`;
      const rows = await sb(path);
      // Clasificación por materia (heurística)
      const clasMat = (nombre) => {
        const t = (nombre || '').toLowerCase();
        if (/\b(trabajo|laboral|empleo|salario|isr|imss)\b/.test(t)) return 'laboral';
        if (/\b(salud|imss|issste|sanitari|medicamento)\b/.test(t)) return 'salud';
        if (/\b(ambient|ecolog|agua|forestal|cambio climatic|biodiversidad)\b/.test(t)) return 'ambiente';
        if (/\b(fiscal|impuest|hacienda|iva|aduana)\b/.test(t)) return 'fiscal';
        if (/\b(seguridad|policial|guardia nacional|delincuencia)\b/.test(t)) return 'seguridad';
        if (/\b(educaci|sep|maestro|escolar|universidad)\b/.test(t)) return 'educacion';
        if (/\b(transparenc|acceso a la informaci|inai)\b/.test(t)) return 'transparencia';
        if (/\b(derechos humanos|discriminaci|indigen|igualdad)\b/.test(t)) return 'derechos';
        if (/\b(ni[nñ]ez|adolescent|menor)\b/.test(t)) return 'ninez';
        if (/\b(energ|electric|petrole|hidrocarbur|nuclear)\b/.test(t)) return 'energia';
        if (/\b(digital|datos personales|telecomunicaci|ciberseg)\b/.test(t)) return 'digital';
        if (/\b(constituci|amparo|cpeum)\b/.test(t)) return 'constitucional';
        return 'general';
      };
      const leyes = rows.map(r => ({
        id: r.id,
        clave: `LEY-${String(r.id).padStart(4,'0')}`,
        nombre: r.nombre,
        fuente: r.fuente || 'Cámara de Diputados',
        url: r.url || `https://www.diputados.gob.mx/LeyesBiblio/index.htm`,
        fecha_publicacion: r.fecha_publicacion,
        descripcion: (r.texto || '').slice(0, 400),
        materia: clasMat(r.nombre),
        ambito: r.ambito || 'federal',
        entidad: r.entidad || null,
        tipo: r.tipo || 'ley',
        estado: 'vigente',
      }));
      return res.status(200).json({
        leyes,
        total: leyes.length,
        por_ambito: leyes.reduce((acc, l) => {
          acc[l.ambito] = (acc[l.ambito] || 0) + 1;
          return acc;
        }, {}),
      });
    }

    if (vista === 'scrapers_health') {
      const rows = await sb('v_scrapers_health?order=last_finished_at.desc.nullslast&select=scraper_slug,last_status,last_rows_inserted,last_rows_updated,fuente_url,last_error,last_started_at,last_finished_at,workflow_run_id,seconds_since_last_run,failures_30d,runs_30d');
      return res.status(200).json({ scrapers: rows });
    }

    if (vista === 'ley_detalle') {
      const id = req.query.id;
      if (!id) return res.status(400).json({ error: 'falta id' });
      const rows = await sb(`leyes?id=eq.${id}&select=*`);
      if (!rows.length) return res.status(404).json({ error: 'no encontrada' });
      return res.status(200).json(rows[0]);
    }

    // ── Jurisprudencia SCJN — Tesis del Semanario Judicial ──
    if (vista === 'jurisprudencia') {
      const q = (req.query.q || '').trim();
      const materia = req.query.materia || '';
      const instancia = req.query.instancia || '';
      let path = 'jurisprudencia_scjn?select=id,registro_digital,tipo,rubro,materia,instancia,epoca,tesis_clave,fecha_publicacion,importancia,resumen_ciudadano,url_oficial&order=importancia.desc,fecha_publicacion.desc&limit=200';
      if (materia) path += `&materia=eq.${encodeURIComponent(materia)}`;
      if (instancia) path += `&instancia=eq.${encodeURIComponent(instancia)}`;
      if (q && q.length > 2) path += `&or=(rubro.ilike.*${encodeURIComponent(q)}*,resumen_ciudadano.ilike.*${encodeURIComponent(q)}*,tesis_clave.ilike.*${encodeURIComponent(q)}*)`;
      const tesis = await sb(path);
      return res.status(200).json({ tesis, total: tesis.length });
    }

    if (vista === 'jurisprudencia_detalle') {
      const id = req.query.id;
      if (!id) return res.status(400).json({ error: 'falta id' });
      const rows = await sb(`jurisprudencia_scjn?id=eq.${id}&select=*`);
      if (!rows.length) return res.status(404).json({ error: 'no encontrada' });
      return res.status(200).json(rows[0]);
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

    if (vista === 'econ_banxico') {
      // Histórico de Banxico: dólar, tasa, inflación, etc.
      // Filtro opcional: ?series=SF43718,SP30577&dias=365
      const dias = Math.min(parseInt(req.query.dias || '365', 10) || 365, 3650);
      const series = (req.query.series || '').trim();
      const desde = new Date(Date.now() - dias * 86400000).toISOString().slice(0,10);
      let filtro = `fecha=gte.${desde}`;
      if (series) {
        const ids = series.split(',').map(s => s.trim()).filter(Boolean).map(s => `"${s}"`).join(',');
        if (ids) filtro += `&serie_id=in.(${ids})`;
      }
      const rows = await sb(`econ_banxico?${filtro}&order=fecha.asc&select=serie_id,serie_slug,nombre,unidad,frecuencia,fecha,valor,fuente,fuente_url&limit=20000`);
      // Agrupar por serie_id para que el cliente solo itere
      const series_data = {};
      for (const r of rows) {
        const k = r.serie_id;
        if (!series_data[k]) {
          series_data[k] = {
            serie_id: r.serie_id,
            serie_slug: r.serie_slug,
            nombre: r.nombre,
            unidad: r.unidad,
            frecuencia: r.frecuencia,
            fuente: r.fuente,
            fuente_url: r.fuente_url,
            puntos: []
          };
        }
        series_data[k].puntos.push({ fecha: r.fecha, valor: r.valor });
      }
      return res.status(200).json({ desde, dias, series: Object.values(series_data) });
    }

    if (vista === 'municipios') {
      const estado = (req.query.estado || '').trim();
      const q = (req.query.q || '').trim();
      const limit = Math.min(parseInt(req.query.limit || '50', 10) || 50, 500);
      const offset = Math.max(parseInt(req.query.offset || '0', 10) || 0, 0);
      const orden = req.query.orden === 'nombre' ? 'nombre.asc' : 'poblacion_total.desc.nullslast';
      const params = [
        `select=clave_inegi,estado_slug,municipio_slug,nombre,nombre_estado,poblacion_total,viviendas_totales,latitud,longitud,cabecera_municipal`,
        `order=${orden}`,
        `limit=${limit}`,
        `offset=${offset}`,
      ];
      if (estado) params.push(`estado_slug=eq.${encodeURIComponent(estado)}`);
      if (q) params.push(`nombre=ilike.*${encodeURIComponent(q)}*`);
      const rows = await sb(`municipios?${params.join('&')}`);
      return res.status(200).json({ municipios: rows, total: rows.length, filtro_estado: estado || null, busqueda: q || null });
    }

    if (vista === 'municipio') {
      const clave = (req.query.clave_inegi || req.query.clave || '').trim();
      const slug = (req.query.slug || '').trim();
      const estado = (req.query.estado || '').trim();
      let url;
      if (clave) {
        url = `municipios?clave_inegi=eq.${encodeURIComponent(clave)}&select=*`;
      } else if (slug) {
        const filt = estado ? `&estado_slug=eq.${encodeURIComponent(estado)}` : '';
        url = `municipios?municipio_slug=eq.${encodeURIComponent(slug)}${filt}&select=*`;
      } else {
        return res.status(400).json({ error: 'Se requiere ?clave_inegi=XXXXX o ?slug=municipio[&estado=estado-slug]' });
      }
      const rows = await sb(url);
      if (!rows || !rows.length) return res.status(404).json({ error: 'Municipio no encontrado' });
      return res.status(200).json({ municipio: rows[0], matches: rows.length, otros: rows.length > 1 ? rows.slice(1) : [] });
    }

    if (vista === 'municipio_panel') {
      // Devuelve toda la info de un municipio para el panel: datos, presidente, delitos, finanzas
      const clave = (req.query.clave_inegi || req.query.clave || '').trim();
      const slug = (req.query.slug || '').trim();
      const estado = (req.query.estado || '').trim();

      // 1. Localizar municipio
      let muniUrl;
      if (clave) {
        muniUrl = `municipios?clave_inegi=eq.${encodeURIComponent(clave)}&select=*`;
      } else if (slug) {
        const filt = estado ? `&estado_slug=eq.${encodeURIComponent(estado)}` : '';
        muniUrl = `municipios?municipio_slug=eq.${encodeURIComponent(slug)}${filt}&select=*`;
      } else {
        return res.status(400).json({ error: 'Se requiere ?clave_inegi=XXXXX o ?slug=municipio[&estado=estado-slug]' });
      }
      const munis = await sb(muniUrl);
      if (!munis || !munis.length) return res.status(404).json({ error: 'Municipio no encontrado' });
      const m = munis[0];
      const cveInegi = m.clave_inegi;

      // 2. Llamadas paralelas a tablas relacionadas
      const [pres, delitosAnual, delitosMes, finanzas, transferEstado, vecinos] = await Promise.all([
        // Presidente actual (último periodo)
        sb(`presidentes_municipales?clave_inegi=eq.${cveInegi}&order=periodo_inicio.desc&limit=1&select=nombre_completo,nombre,apellido_paterno,apellido_materno,sexo,partido,coalicion,periodo_inicio,periodo_fin,periodo_label,direccion,pagina_web,telefono`),
        // Delitos por tipo último año disponible
        sb(`delitos_municipios?clave_inegi=eq.${cveInegi}&order=anio.desc,mes.desc&limit=2000&select=anio,mes,tipo_delito,subtipo_delito,modalidad,cantidad`),
        // Top delitos por tipo y total anual
        null,
        // Finanzas históricas
        sb(`finanzas_municipales_inegi?clave_inegi=eq.${cveInegi}&order=anio.asc&select=anio,flujo,capitulo,concepto,monto,unidad`),
        // Transferencias estatales último año del estado
        sb(`transferencias_estatales?clave_entidad=eq.${m.clave_entidad}&anio=eq.2025&tipo_dato=eq.pagado&order=monto.desc&limit=20&select=ramo,fondo,concepto,mes,monto`),
        // Otros municipios del mismo estado (para context)
        sb(`municipios?estado_slug=eq.${encodeURIComponent(m.estado_slug)}&order=poblacion_total.desc&limit=10&select=clave_inegi,nombre,municipio_slug,poblacion_total`),
      ]);

      // Agregar delitos: total anual + breakdown
      const delitosByAnio = {};
      const delitosByTipo = {};
      const delitosByMes = {};
      for (const d of (delitosAnual || [])) {
        delitosByAnio[d.anio] = (delitosByAnio[d.anio] || 0) + (d.cantidad || 0);
        const t = d.tipo_delito || 'Otro';
        delitosByTipo[t] = (delitosByTipo[t] || 0) + (d.cantidad || 0);
        const ymKey = `${d.anio}-${String(d.mes).padStart(2,'0')}`;
        delitosByMes[ymKey] = (delitosByMes[ymKey] || 0) + (d.cantidad || 0);
      }
      const tiposOrdenados = Object.entries(delitosByTipo).sort((a,b) => b[1]-a[1]).slice(0,10).map(([t,c]) => ({tipo:t, cantidad:c}));
      const mesesOrdenados = Object.entries(delitosByMes).sort().map(([ym,c]) => ({periodo:ym, cantidad:c}));

      return res.status(200).json({
        municipio: m,
        presidente: pres && pres[0] || null,
        delitos: {
          por_anio: delitosByAnio,
          por_tipo: tiposOrdenados,
          por_mes: mesesOrdenados,
          total_periodo: Object.values(delitosByAnio).reduce((a,b)=>a+b, 0),
        },
        finanzas: finanzas || [],
        transferencias_estado: transferEstado || [],
        vecinos: (vecinos || []).filter(v => v.clave_inegi !== cveInegi).slice(0, 5),
        timestamp: new Date().toISOString(),
      });
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

    // ─────────────── CHAT COMUNITARIO ───────────────
    // Helper: hash de IP para anti-flood (no se reversa, no identifica)
    function ipHash(req) {
      const ip = (req.headers['x-forwarded-for'] || req.headers['x-real-ip'] || '0.0.0.0')
        .split(',')[0].trim();
      return nodeCrypto.createHash('sha256').update(ip + '|tequio-salt').digest('hex').slice(0, 32);
    }

    // Lista de palabras prohibidas (datos personales, doxxing, insultos extremos)
    const PALABRAS_PROHIBIDAS = [
      'puta','perra','maricon','pendejo','mamada','culero','verga','chinga tu madre',
      'doxx','dirección de','teléfono de','rfc:','curp:','contraseña',
    ];
    function pasaFiltro(texto) {
      const t = (texto || '').toLowerCase();
      // Bloquea si contiene palabra prohibida
      for (const p of PALABRAS_PROHIBIDAS) if (t.includes(p)) return { ok:false, motivo:'lenguaje no permitido o información personal' };
      // Bloquea si parece teléfono mexicano (10 dígitos seguidos)
      if (/\b\d{10}\b/.test(t)) return { ok:false, motivo:'no compartas números de teléfono' };
      // Bloquea URLs sospechosas (solo permitir dominios .gob.mx o .org.mx)
      const urls = t.match(/https?:\/\/[^\s]+/g) || [];
      for (const u of urls) {
        if (!/\.(gob\.mx|org\.mx|edu\.mx|unam\.mx|cdmx\.gob\.mx)\b/.test(u)) {
          return { ok:false, motivo:'sólo se permiten enlaces a dominios oficiales (.gob.mx, .org.mx)' };
        }
      }
      return { ok:true };
    }

    // GET listar mensajes
    if (vista === 'chat') {
      const estado = (req.query.estado || 'Nacional').trim();
      const filtro = (req.query.filtro || '').trim();
      let path = `mensajes_comunidad?select=id,nick,tipo,texto,estado,votos,created_at&oculto=eq.false&order=created_at.desc&limit=80`;
      if (estado && estado !== 'todos') path += `&estado=eq.${encodeURIComponent(estado)}`;
      if (filtro && filtro !== 'todos') path += `&tipo=eq.${encodeURIComponent(filtro)}`;
      const rows = await sb(path);
      // Conteo global hoy
      const hoy = new Date(); hoy.setHours(0,0,0,0);
      let total_hoy = 0;
      try {
        const cnt = await sb(`mensajes_comunidad?select=id&oculto=eq.false&created_at=gte.${hoy.toISOString()}&limit=1000`);
        total_hoy = cnt.length;
      } catch {}
      return res.status(200).json({ mensajes: rows, total: rows.length, total_hoy });
    }

    // POST publicar
    if (req.method === 'POST' && vista === 'chat_publicar') {
      const body = req.body || {};
      const texto = (body.texto || '').trim();
      const tipo = (body.tipo || 'idea').trim();
      const nick = (body.nick || '').trim() || ('Ciudadano_' + Math.floor(Math.random()*9000+1000));
      const estado = (body.estado || 'Nacional').trim();
      if (texto.length < 5 || texto.length > 500) return res.status(400).json({ error:'texto debe tener entre 5 y 500 caracteres' });
      if (!['denuncia','idea','alerta','organizacion'].includes(tipo)) return res.status(400).json({ error:'tipo invalido' });
      const filtro = pasaFiltro(texto);
      if (!filtro.ok) return res.status(400).json({ error: filtro.motivo });
      const hash = ipHash(req);
      // Anti-flood: máximo 5 mensajes por hora desde misma IP
      try {
        const recientes = await sb(`mensajes_comunidad?select=id&ip_hash=eq.${hash}&created_at=gte.${new Date(Date.now()-3600000).toISOString()}&limit=10`);
        if (recientes.length >= 5) return res.status(429).json({ error:'has publicado demasiado en la última hora. Inténtalo de nuevo más tarde.' });
      } catch {}
      await sbWrite('mensajes_comunidad', {
        nick: nick.slice(0, 40),
        tipo, texto: texto.slice(0, 500),
        estado: estado.slice(0, 60),
        ip_hash: hash,
      });
      return res.status(200).json({ ok:true });
    }

    // POST votar
    if (req.method === 'POST' && vista === 'chat_votar') {
      const id = (req.body?.id || '').trim();
      if (!id) return res.status(400).json({ error:'id requerido' });
      const key = SERVICE_KEY || ANON_KEY;
      const r = await fetch(`${SUPABASE_URL}/rest/v1/rpc/sql`, { method:'POST' }).catch(()=>null);
      // PostgREST: incrementar votos vía PATCH
      const patch = await fetch(`${SUPABASE_URL}/rest/v1/mensajes_comunidad?id=eq.${id}`, {
        method:'PATCH',
        headers:{ 'Content-Type':'application/json', 'apikey':key, 'Authorization':`Bearer ${key}`, 'Prefer':'return=representation' },
        body: JSON.stringify({ votos: { increment: 1 } }),
      });
      // PostgREST no soporta increment nativo. Fallback: leer y escribir.
      if (!patch.ok) {
        const cur = await sb(`mensajes_comunidad?id=eq.${id}&select=votos&limit=1`);
        const v = (cur[0]?.votos || 0) + 1;
        const upd = await fetch(`${SUPABASE_URL}/rest/v1/mensajes_comunidad?id=eq.${id}`, {
          method:'PATCH',
          headers:{ 'Content-Type':'application/json', 'apikey':key, 'Authorization':`Bearer ${key}`, 'Prefer':'return=minimal' },
          body: JSON.stringify({ votos: v }),
        });
        if (!upd.ok) return res.status(500).json({ error:'no se pudo votar' });
      }
      return res.status(200).json({ ok:true });
    }

    // POST reportar
    if (req.method === 'POST' && vista === 'chat_reportar') {
      const id = (req.body?.id || '').trim();
      if (!id) return res.status(400).json({ error:'id requerido' });
      const key = SERVICE_KEY || ANON_KEY;
      const cur = await sb(`mensajes_comunidad?id=eq.${id}&select=reportes&limit=1`);
      const r = (cur[0]?.reportes || 0) + 1;
      const upd = await fetch(`${SUPABASE_URL}/rest/v1/mensajes_comunidad?id=eq.${id}`, {
        method:'PATCH',
        headers:{ 'Content-Type':'application/json', 'apikey':key, 'Authorization':`Bearer ${key}`, 'Prefer':'return=minimal' },
        body: JSON.stringify({ reportes: r }),
      });
      if (!upd.ok) return res.status(500).json({ error:'no se pudo reportar' });
      // El trigger SQL auto-oculta cuando reportes >= 3
      return res.status(200).json({ ok:true, oculto: r >= 3 });
    }
    // ─────────────── FIN CHAT COMUNITARIO ───────────────

    // ── SAT 69-B — Empresas factureras EFOS ──
    if (vista === 'sat_69b') {
      const q = (req.query.q || '').trim().toUpperCase();
      const situacion = (req.query.situacion || '').trim();
      if (!q || q.length < 3) {
        return res.status(400).json({ error: 'Pasa ?q=RFC o nombre (min 3 caracteres)' });
      }
      const RFC_RE = /^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$/i;
      const RFC_PREFIX = /^[A-ZÑ&]{3,4}[0-9]{0,6}$/i;
      let path;
      if (RFC_RE.test(q) || RFC_PREFIX.test(q)) {
        path = `sat_69b?rfc=ilike.${encodeURIComponent(q)}*&select=rfc,nombre,situacion,numero_publicacion,fecha_publicacion_sat,fecha_dof,pagina_dof,fuente_url&order=fecha_publicacion_sat.desc&limit=50`;
      } else {
        path = `sat_69b?nombre=ilike.*${encodeURIComponent(q)}*&select=rfc,nombre,situacion,numero_publicacion,fecha_publicacion_sat,fecha_dof,pagina_dof,fuente_url&order=fecha_publicacion_sat.desc&limit=50`;
      }
      if (situacion) path += `&situacion=eq.${encodeURIComponent(situacion)}`;
      const rows = await sb(path);
      return res.status(200).json({ items: rows, total: rows.length, query: q });
    }

    // ── SAT 69 — Deudores firmes del SAT ──
    if (vista === 'sat_69') {
      const q = (req.query.q || '').trim().toUpperCase();
      const supuesto = (req.query.supuesto || '').trim();
      if (!q || q.length < 3) {
        return res.status(400).json({ error: 'Pasa ?q=RFC o nombre (min 3 caracteres)' });
      }
      const RFC_RE = /^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$/i;
      const RFC_PREFIX = /^[A-ZÑ&]{3,4}[0-9]{0,6}$/i;
      let path;
      if (RFC_RE.test(q) || RFC_PREFIX.test(q)) {
        path = `sat_69?rfc=ilike.${encodeURIComponent(q)}*&select=rfc,nombre,supuesto,entidad_federativa,monto,ejercicio,tipo_persona,fecha_primera_publicacion,fuente_url&order=monto.desc.nullslast&limit=50`;
      } else {
        path = `sat_69?nombre=ilike.*${encodeURIComponent(q)}*&select=rfc,nombre,supuesto,entidad_federativa,monto,ejercicio,tipo_persona,fecha_primera_publicacion,fuente_url&order=monto.desc.nullslast&limit=50`;
      }
      if (supuesto) path += `&supuesto=eq.${encodeURIComponent(supuesto)}`;
      const rows = await sb(path);
      return res.status(200).json({ items: rows, total: rows.length, query: q });
    }

    // ── ASF — Auditoría Superior de la Federación ──
    if (vista === 'asf') {
      const cuenta = req.query.cuenta_publica;
      let path = 'asf_auditorias?select=*&order=cuenta_publica.desc,fecha_entrega.desc';
      if (cuenta) path += `&cuenta_publica=eq.${encodeURIComponent(cuenta)}`;
      const rows = await sb(path);
      return res.status(200).json({ items: rows, total: rows.length });
    }

    // -- SHCP PEF -- Presupuesto de Egresos de la Federacion --
    if (vista === 'pef_resumen') {
      const anio = parseInt(req.query.anio || '2026', 10);
      // Estrategia: paginar todos los rows con Range header (Supabase cap=1000 por chunk)
      // Hacer fetches paralelos en lotes para evitar timeout (Vercel 10s Hobby, 60s Pro).
      const CHUNK = 1000;
      const rfetch = async (range) => {
        const r = await fetch(`${SUPABASE_URL}/rest/v1/shcp_pef?ciclo=eq.${anio}&select=ramo,desc_ramo,monto&order=ramo.asc`, {
          headers: {
            'apikey': ANON_KEY,
            'Authorization': `Bearer ${ANON_KEY}`,
            'Accept': 'application/json',
            'Range-Unit': 'items',
            'Range': range,
            'Prefer': 'count=exact',
          },
        });
        if (!r.ok && r.status !== 206) throw new Error(`Supabase ${r.status}`);
        const cr = r.headers.get('content-range') || '';
        const data = await r.json();
        return { data, contentRange: cr };
      };
      const first = await rfetch(`0-${CHUNK-1}`);
      const m = (first.contentRange || '').match(/\/(\d+)$/);
      const totalRows = m ? parseInt(m[1], 10) : first.data.length;
      const all = first.data.slice();
      // Lanzar resto en lotes de 10 concurrentes para no saturar
      const ranges = [];
      for (let off = CHUNK; off < totalRows; off += CHUNK) {
        ranges.push(`${off}-${off + CHUNK - 1}`);
      }
      const BATCH = 10;
      for (let b = 0; b < ranges.length; b += BATCH) {
        const slice = ranges.slice(b, b + BATCH);
        const results = await Promise.all(slice.map(rg => rfetch(rg).then(x => x.data).catch(() => [])));
        for (const c of results) all.push(...c);
      }
      const byRamo = {};
      let total = 0;
      for (const r of all) {
        const k = r.ramo + '|' + (r.desc_ramo || '');
        byRamo[k] = (byRamo[k] || 0) + Number(r.monto || 0);
        total += Number(r.monto || 0);
      }
      const items = Object.entries(byRamo).map(([k, v]) => {
        const [ramo, desc_ramo] = k.split('|');
        return { ramo, desc_ramo, monto: v };
      }).sort((a, b) => b.monto - a.monto);
      res.setHeader('Cache-Control', 's-maxage=3600');
      return res.status(200).json({ anio, total, ramos: items, rowsFetched: all.length, totalRowsServer: totalRows });
    }

        if (vista === 'pef_ramo') {
      const anio = parseInt(req.query.anio || '2026', 10);
      const ramo = (req.query.ramo || '').trim();
      if (!ramo) return res.status(400).json({ error: 'Pasa ?ramo=N' });
      const rows = await sb(`shcp_pef?ciclo=eq.${anio}&ramo=eq.${encodeURIComponent(ramo)}&select=unidad_responsable,desc_ur,desc_pp,desc_og,monto&order=monto.desc.nullslast&limit=500`);
      const byUR = {};
      for (const r of rows) {
        const k = (r.unidad_responsable || '') + '|' + (r.desc_ur || '');
        if (!byUR[k]) byUR[k] = { unidad_responsable: r.unidad_responsable, desc_ur: r.desc_ur, monto: 0, programas: [] };
        byUR[k].monto += Number(r.monto || 0);
        if (r.desc_pp) byUR[k].programas.push({ desc_pp: r.desc_pp, desc_og: r.desc_og, monto: Number(r.monto || 0) });
      }
      const items = Object.values(byUR).sort((a, b) => b.monto - a.monto);
      res.setHeader('Cache-Control', 's-maxage=3600');
      return res.status(200).json({ anio, ramo, items });
    }

    // ── Panel Estatal — todo lo de un estado en una sola call ──
    if (vista === 'estado_panel') {
      const clave = (req.query.clave || '').trim().padStart(2, '0');
      if (!clave || clave === '00') {
        return res.status(400).json({ error: 'Pasa ?clave=NN (clave INEGI de la entidad, ej. 09 para CDMX)' });
      }
      // Paralelizar las queries
      const [transfAgg, municipios, presidentes, diputados, senadores] = await Promise.all([
        sb(`rpc/agg_transferencias_estado?p_clave=${encodeURIComponent(clave)}`),
        sb(`municipios?clave_entidad=eq.${clave}&select=clave_inegi,nombre,municipio_slug,poblacion_total,latitud,longitud&order=poblacion_total.desc.nullslast&limit=20`),
        sb(`presidentes_municipales?clave_entidad=eq.${clave}&select=nombre,apellido_paterno,apellido_materno,nombre_municipio,partido,periodo_label&limit=300`),
        sb(`politicos_diputados?select=nombre,partido,entidad,distrito&limit=500`).catch(_ => []),
        sb(`politicos_senadores?select=nombre,partido,entidad,principio&limit=200`).catch(_ => [])
      ]);

      // El RPC retorna [{ ... }] (PostgREST envuelve scalar functions en array)
      const aggRow = Array.isArray(transfAgg) ? transfAgg[0] : transfAgg;
      const aggData = aggRow && (aggRow.agg_transferencias_estado || aggRow);
      const totalRecibido = Number(aggData?.total || 0);
      const transferencias_por_anio = aggData?.transferencias_por_anio || [];
      const top_fondos = aggData?.top_fondos || [];
      const top_ramos = aggData?.top_ramos || [];
      const nombreEstado = aggData?.nombre_estado || municipios[0]?.nombre_estado || '';

      // Filtrar diputados por estado (entidad ILIKE) - normalizar acentos
      const norm = s => (s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
      const nEstado = norm(nombreEstado);
      const dipFiltrados = diputados.filter(d => {
        const e = norm(d.entidad);
        if (!e || !nEstado) return false;
        return e.includes(nEstado.slice(0, 8)) || nEstado.includes(e.slice(0, 8));
      });
      const senFiltrados = senadores.filter(s => {
        const e = norm(s.entidad_federativa);
        return e && nEstado && (e.includes(nEstado.slice(0, 8)) || nEstado.includes(e.slice(0, 8)));
      });

      // Distribucion partidista de presidentes municipales
      const porPartido = {};
      for (const p of presidentes) {
        const k = (p.partido || 'SIN_DATO').split(',')[0].trim();
        porPartido[k] = (porPartido[k] || 0) + 1;
      }

      return res.status(200).json({
        clave,
        nombre_estado: nombreEstado,
        total_transferencias_historicas: totalRecibido,
        ultimo_anio_con_datos: transferencias_por_anio.length ? transferencias_por_anio[transferencias_por_anio.length - 1].anio : null,
        transferencias_por_anio,
        top_fondos,
        top_ramos,
        municipios_top: municipios.slice(0, 10),
        num_municipios: municipios.length,
        presidentes_partido: Object.entries(porPartido).map(([partido, num]) => ({ partido, num })).sort((a, b) => b.num - a.num),
        diputados_federales: dipFiltrados.slice(0, 50),
        senadores: senFiltrados.slice(0, 5),
        num_diputados: dipFiltrados.length,
        num_senadores: senFiltrados.length
      });
    }

    // -- SRE -- Embajadas y Consulados de Mexico --
    if (vista === 'sre') {
      const tipo = (req.query.tipo || 'all').toLowerCase();
      const q = (req.query.q || '').trim();
      const out = { embajadas: [], consulados: [] };
      if (tipo === 'all' || tipo === 'embajadas') {
        let path = 'sre_embajadas?select=*&order=pais.asc.nullslast&limit=200';
        if (q && q.length > 2) path += `&or=(pais.ilike.*${encodeURIComponent(q)}*,titular.ilike.*${encodeURIComponent(q)}*)`;
        out.embajadas = await sb(path);
      }
      if (tipo === 'all' || tipo === 'consulados') {
        let path = 'sre_consulados?select=*&order=pais.asc.nullslast,ciudad.asc.nullslast&limit=200';
        if (q && q.length > 2) path += `&or=(pais.ilike.*${encodeURIComponent(q)}*,ciudad.ilike.*${encodeURIComponent(q)}*,titular.ilike.*${encodeURIComponent(q)}*)`;
        out.consulados = await sb(path);
      }
      return res.status(200).json(out);
    }

    // IP Hash - devuelve hash sha256 de IP del cliente con salt
    if (vista === 'ip_hash') {
      const ipRaw = (req.headers['x-forwarded-for'] || req.connection?.remoteAddress || '')
        .split(',')[0].trim() || 'unknown';
      const salt = process.env.SALT_IP || 'tequio-salt-v1';
      const ip_hash = nodeCrypto.createHash('sha256').update(ipRaw + salt).digest('hex').slice(0, 32);
      return res.status(200).json({ ip_hash });
    }

    // Chat enviar (Nivel 2) - 30s rate limit por device_hash
    if (vista === 'chat_enviar') {
      if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const { mensaje, device_hash, estado, honeypot } = body;
      if (honeypot) return res.status(400).json({ error: 'bot' });
      if (!mensaje || mensaje.length < 1 || mensaje.length > 2000) {
        return res.status(400).json({ error: 'mensaje 1-2000 chars' });
      }
      if (!device_hash || device_hash.length < 10) {
        return res.status(400).json({ error: 'device_hash requerido' });
      }
      const recent = await sb('chat_mensajes?device_hash=eq.' + encodeURIComponent(device_hash) + '&order=timestamp.desc&limit=1');
      if (recent.length > 0) {
        const sec = (Date.now() - new Date(recent[0].timestamp).getTime()) / 1000;
        if (sec < 30) return res.status(429).json({ error: 'Espera ' + Math.ceil(30 - sec) + 's' });
      }
      const ipRaw = (req.headers['x-forwarded-for'] || req.connection?.remoteAddress || '').split(',')[0].trim() || 'unknown';
      const ip_hash = nodeCrypto.createHash('sha256').update(ipRaw + (process.env.SALT_IP || 'tequio-salt-v1')).digest('hex').slice(0, 32);
      const key = SERVICE_KEY || ANON_KEY;
      const ins = await fetch(SUPABASE_URL + '/rest/v1/chat_mensajes', {
        method: 'POST',
        headers: {
          'apikey': key,
          'Authorization': 'Bearer ' + key,
          'Content-Type': 'application/json',
          'Prefer': 'return=minimal'
        },
        body: JSON.stringify({ mensaje: mensaje.trim(), device_hash, ip_hash, estado: estado || null })
      });
      if (!ins.ok) return res.status(500).json({ error: 'insert failed' });
      return res.status(201).json({ ok: true });
    }

    // Votar Nivel 3 - registrar voto sobre iniciativa con dedup
    if (vista === 'votar') {
      if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const { iniciativa_id, voto, device_hash, ine_hash, distrito, estado, honeypot, tiempo_en_pagina_ms, scroll_detectado } = body;
      if (honeypot) return res.status(400).json({ error: 'bot' });
      if (!iniciativa_id || !voto) return res.status(400).json({ error: 'iniciativa_id y voto requeridos' });
      if (!['a_favor', 'en_contra', 'abstencion'].includes(voto)) return res.status(400).json({ error: 'voto invalido' });
      if (!device_hash && !ine_hash) return res.status(400).json({ error: 'device_hash o ine_hash requerido' });
      if (typeof tiempo_en_pagina_ms === 'number' && tiempo_en_pagina_ms < 5000) return res.status(400).json({ error: 'demasiado rapido' });
      if (scroll_detectado === false) return res.status(400).json({ error: 'sin interaccion humana' });
      if (ine_hash) {
        const yaVoto = await sb('votos?ine_hash=eq.' + encodeURIComponent(ine_hash) + '&iniciativa_id=eq.' + encodeURIComponent(iniciativa_id) + '&limit=1');
        if (yaVoto.length > 0) return res.status(409).json({ error: 'Ya votaste con INE en esta iniciativa' });
      }
      if (device_hash) {
        const yaVoto = await sb('votos?device_hash=eq.' + encodeURIComponent(device_hash) + '&iniciativa_id=eq.' + encodeURIComponent(iniciativa_id) + '&limit=1');
        if (yaVoto.length > 0) return res.status(409).json({ error: 'Ya votaste desde este dispositivo' });
      }
      const ipRaw = (req.headers['x-forwarded-for'] || req.connection?.remoteAddress || '').split(',')[0].trim() || 'unknown';
      const ip_hash = nodeCrypto.createHash('sha256').update(ipRaw + (process.env.SALT_IP || 'tequio-salt-v1')).digest('hex').slice(0, 32);
      const key = SERVICE_KEY || ANON_KEY;
      const ins = await fetch(SUPABASE_URL + '/rest/v1/votos', {
        method: 'POST',
        headers: {
          'apikey': key,
          'Authorization': 'Bearer ' + key,
          'Content-Type': 'application/json',
          'Prefer': 'return=minimal'
        },
        body: JSON.stringify({ iniciativa_id, voto, device_hash: device_hash || null, ine_hash: ine_hash || null, ip_hash, distrito: distrito || null, estado: estado || null })
      });
      if (!ins.ok) return res.status(500).json({ error: 'insert failed' });
      return res.status(201).json({ ok: true });
    }

    // Firmar Iniciativa Ciudadana Nivel 3
    if (vista === 'firmar') {
      if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const { iniciativa_id, device_hash, ine_hash, distrito, estado, honeypot, tiempo_en_pagina_ms, scroll_detectado } = body;
      if (honeypot) return res.status(400).json({ error: 'bot' });
      if (!iniciativa_id) return res.status(400).json({ error: 'iniciativa_id requerido' });
      if (!device_hash && !ine_hash) return res.status(400).json({ error: 'device_hash o ine_hash requerido' });
      if (typeof tiempo_en_pagina_ms === 'number' && tiempo_en_pagina_ms < 5000) return res.status(400).json({ error: 'demasiado rapido' });
      if (scroll_detectado === false) return res.status(400).json({ error: 'sin interaccion humana' });
      if (ine_hash) {
        const ya = await sb('firmas_iniciativas?ine_hash=eq.' + encodeURIComponent(ine_hash) + '&iniciativa_id=eq.' + encodeURIComponent(iniciativa_id) + '&limit=1');
        if (ya.length > 0) return res.status(409).json({ error: 'Ya firmaste con INE esta iniciativa' });
      }
      if (device_hash) {
        const ya = await sb('firmas_iniciativas?device_hash=eq.' + encodeURIComponent(device_hash) + '&iniciativa_id=eq.' + encodeURIComponent(iniciativa_id) + '&limit=1');
        if (ya.length > 0) return res.status(409).json({ error: 'Ya firmaste desde este dispositivo' });
      }
      const ipRaw = (req.headers['x-forwarded-for'] || req.connection?.remoteAddress || '').split(',')[0].trim() || 'unknown';
      const ip_hash = nodeCrypto.createHash('sha256').update(ipRaw + (process.env.SALT_IP || 'tequio-salt-v1')).digest('hex').slice(0, 32);
      const key = SERVICE_KEY || ANON_KEY;
      const ins = await fetch(SUPABASE_URL + '/rest/v1/firmas_iniciativas', {
        method: 'POST',
        headers: {
          'apikey': key,
          'Authorization': 'Bearer ' + key,
          'Content-Type': 'application/json',
          'Prefer': 'return=minimal'
        },
        body: JSON.stringify({ iniciativa_id, device_hash: device_hash || null, ine_hash: ine_hash || null, ip_hash, distrito: distrito || null, estado: estado || null })
      });
      if (!ins.ok) return res.status(500).json({ error: 'insert failed' });
      return res.status(201).json({ ok: true });
    }

    // Listar iniciativas con conteos
    if (vista === 'iniciativas') {
      const tipo = (req.query.tipo || '').trim();
      let path = 'iniciativas?select=*&order=fecha_inicio.desc&limit=100';
      if (tipo) path += '&tipo=eq.' + encodeURIComponent(tipo);
      const items = await sb(path);
      const results = await Promise.all(items.map(async (it) => {
        if (it.tipo === 'voto_congreso') {
          const [aFavor, enContra, abst] = await Promise.all([
            sb('votos?iniciativa_id=eq.' + encodeURIComponent(it.id) + '&voto=eq.a_favor&select=count'),
            sb('votos?iniciativa_id=eq.' + encodeURIComponent(it.id) + '&voto=eq.en_contra&select=count'),
            sb('votos?iniciativa_id=eq.' + encodeURIComponent(it.id) + '&voto=eq.abstencion&select=count')
          ]);
          return { ...it, votos: { a_favor: aFavor[0]?.count || 0, en_contra: enContra[0]?.count || 0, abstencion: abst[0]?.count || 0 } };
        } else {
          const f = await sb('firmas_iniciativas?iniciativa_id=eq.' + encodeURIComponent(it.id) + '&select=count');
          return { ...it, firmas: f[0]?.count || 0 };
        }
      }));
      return res.status(200).json({ items: results });
    }
    // ── Directorio 32 Estados ──
    if (vista === 'directorio_estado') {
      const clave = (req.query.clave || '').trim().padStart(2, '0');
      if (!clave || clave === '00') return res.status(400).json({ error: 'Pasa ?clave=NN' });

      const muni = await sb(`municipios?clave_entidad=eq.${clave}&select=nombre_estado&limit=1`);
      const nombreEstado = muni[0]?.nombre_estado || '';

      const variantes = [nombreEstado];
      if (nombreEstado === 'Ciudad de Mexico') variantes.push('Ciudad de México', 'CDMX', 'Distrito Federal');
      if (nombreEstado === 'Estado de Mexico') variantes.push('México', 'Estado de México');
      if (nombreEstado === 'Nuevo Leon') variantes.push('Nuevo León');
      if (nombreEstado === 'Yucatan') variantes.push('Yucatán');
      if (nombreEstado === 'Queretaro') variantes.push('Querétaro');
      if (nombreEstado === 'San Luis Potosi') variantes.push('San Luis Potosí');
      if (nombreEstado === 'Michoacan') variantes.push('Michoacán');

      const dipFilter = variantes.map(v => `entidad.ilike.*${encodeURIComponent(v)}*`).join(',');
      const senFilter = variantes.map(v => `entidad_federativa.ilike.*${encodeURIComponent(v)}*`).join(',');

      const [presidentes, diputados, senadores] = await Promise.all([
        sb(`presidentes_municipales?clave_entidad=eq.${clave}&select=nombre,apellido_paterno,apellido_materno,nombre_municipio,partido,periodo_label,direccion,pagina_web,lada,telefono&order=nombre_municipio.asc&limit=500`),
        sb(`politicos_diputados?or=(${dipFilter})&select=nombre,partido,entidad,distrito,principio_eleccion,email,telefono,foto_url&order=nombre.asc&limit=200`).catch(_ => []),
        sb(`politicos_senadores?or=(${senFilter})&select=nombre_completo,partido,entidad_federativa,tipo_eleccion,telefono,direccion_oficina,email,foto_url&limit=20`).catch(_ => [])
      ]);

      return res.status(200).json({
        clave,
        nombre_estado: nombreEstado,
        num_municipios: presidentes.length,
        num_municipios_con_telefono: presidentes.filter(p => p.telefono && p.telefono.trim()).length,
        presidentes,
        diputados_federales: diputados,
        senadores,
        senadores_disponibles: senadores.length > 0
      });
    }

    // ── Servicios Públicos: federales + por estado ──
    if (vista === 'servicios') {
      const clave = (req.query.clave || '').trim();
      const categoria = (req.query.categoria || '').trim();
      let federalPath = 'servicios_publicos?nivel=eq.federal&select=*&order=categoria.asc,nombre.asc';
      if (categoria) federalPath += `&categoria=eq.${encodeURIComponent(categoria)}`;
      let estatalPath = null;
      if (clave) {
        const c = clave.padStart(2, '0');
        estatalPath = `servicios_publicos?nivel=eq.estatal&clave_entidad=eq.${c}&select=*&order=categoria.asc,nombre.asc`;
      }
      const [federales, estatales] = await Promise.all([
        sb(federalPath),
        estatalPath ? sb(estatalPath) : Promise.resolve([])
      ]);
      return res.status(200).json({
        federales,
        estatales,
        clave: clave || null,
        total_federales: federales.length,
        total_estatales: estatales.length
      });
    }

    // ── Testigo Civico — listar reportes ──
    if (vista === 'testigo_listar') {
      const categoria = (req.query.categoria || '').trim();
      const estado = (req.query.estado || '').trim();
      const limit = Math.min(parseInt(req.query.limit || '50', 10), 200);
      let path = `testigo_reportes?estatus=eq.publicado&select=*&order=created_at.desc&limit=${limit}`;
      if (categoria) path += `&categoria=eq.${encodeURIComponent(categoria)}`;
      if (estado) path += `&clave_entidad=eq.${encodeURIComponent(estado.padStart(2, '0'))}`;
      const items = await sb(path);
      return res.status(200).json({ items, total: items.length });
    }

    // ── Testigo Civico — crear reporte (rate limit 3/24h) ──
    if (vista === 'testigo_crear') {
      if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const { titulo, descripcion, categoria, foto_url, foto_thumb_url, lat, lng, nombre_estado, clave_entidad, nombre_municipio, device_hash, honeypot, tiempo_en_pagina_ms } = body;

      if (honeypot) return res.status(400).json({ error: 'bot' });
      if (typeof tiempo_en_pagina_ms === 'number' && tiempo_en_pagina_ms < 30000) {
        return res.status(400).json({ error: 'Espera al menos 30 segundos' });
      }

      if (!titulo || titulo.length < 5 || titulo.length > 200) return res.status(400).json({ error: 'titulo 5-200 chars' });
      if (!descripcion || descripcion.length < 10 || descripcion.length > 5000) return res.status(400).json({ error: 'descripcion 10-5000 chars' });
      const validCats = ['corrupcion','infraestructura','seguridad','salud','ambiental','servicios','otro'];
      if (!validCats.includes(categoria)) return res.status(400).json({ error: 'categoria invalida' });
      if (!device_hash || device_hash.length < 10) return res.status(400).json({ error: 'device_hash requerido' });

      const dia = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
      const recientes = await sb(`testigo_reportes?device_hash=eq.${encodeURIComponent(device_hash)}&created_at=gte.${encodeURIComponent(dia)}&select=id`);
      if (recientes.length >= 3) {
        return res.status(429).json({ error: 'Limite alcanzado: maximo 3 reportes cada 24 horas' });
      }

      const ipRaw = (req.headers['x-forwarded-for'] || req.connection?.remoteAddress || '').split(',')[0].trim() || 'unknown';
      const ip_hash = nodeCrypto.createHash('sha256').update(ipRaw + (process.env.SALT_IP || 'tequio-salt-v1')).digest('hex').slice(0, 32);

      const payload = {
        titulo: titulo.trim(),
        descripcion: descripcion.trim(),
        categoria,
        foto_url: foto_url || null,
        foto_thumb_url: foto_thumb_url || null,
        lat: typeof lat === 'number' ? Math.round(lat * 10000) / 10000 : null,
        lng: typeof lng === 'number' ? Math.round(lng * 10000) / 10000 : null,
        nombre_estado: nombre_estado || null,
        clave_entidad: clave_entidad || null,
        nombre_municipio: nombre_municipio || null,
        device_hash,
        ip_hash
      };

      const ins = await fetch(`${SUPABASE_URL}/rest/v1/testigo_reportes`, {
        method: 'POST',
        headers: {
          'apikey': SERVICE_KEY || ANON_KEY,
          'Authorization': `Bearer ${SERVICE_KEY || ANON_KEY}`,
          'Content-Type': 'application/json',
          'Prefer': 'return=representation'
        },
        body: JSON.stringify(payload)
      });

      if (!ins.ok) {
        const errText = await ins.text();
        return res.status(500).json({ error: 'insert failed', detail: errText.slice(0, 200) });
      }
      const data = await ins.json();
      return res.status(201).json({ ok: true, reporte: Array.isArray(data) ? data[0] : data });
    }

    // ── Testigo Civico — flag de reporte abusivo ──
    if (vista === 'testigo_flag') {
      if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const { reporte_id, device_hash } = body;
      if (!reporte_id || !device_hash) return res.status(400).json({ error: 'reporte_id y device_hash requeridos' });

      const upd = await fetch(`${SUPABASE_URL}/rest/v1/testigo_reportes?id=eq.${encodeURIComponent(reporte_id)}`, {
        method: 'GET',
        headers: { 'apikey': ANON_KEY, 'Authorization': `Bearer ${ANON_KEY}` }
      });
      const rows = await upd.json();
      if (!rows.length) return res.status(404).json({ error: 'no encontrado' });

      const nuevoCount = (rows[0].reportes_count || 0) + 1;
      const nuevoEstatus = nuevoCount >= 3 ? 'oculto' : 'publicado';

      const patch = await fetch(`${SUPABASE_URL}/rest/v1/testigo_reportes?id=eq.${encodeURIComponent(reporte_id)}`, {
        method: 'PATCH',
        headers: {
          'apikey': SERVICE_KEY || ANON_KEY,
          'Authorization': `Bearer ${SERVICE_KEY || ANON_KEY}`,
          'Content-Type': 'application/json',
          'Prefer': 'return=minimal'
        },
        body: JSON.stringify({ reportes_count: nuevoCount, estatus: nuevoEstatus })
      });

      if (!patch.ok) return res.status(500).json({ error: 'update failed' });
      return res.status(200).json({ ok: true, oculto: nuevoEstatus === 'oculto' });
    }

    // ── Testigo Civico — subir foto (proxy a Supabase Storage) ──
    if (vista === 'testigo_foto') {
      if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const { foto_base64, device_hash, ext } = body;
      if (!foto_base64 || !device_hash) return res.status(400).json({ error: 'foto_base64 y device_hash requeridos' });
      const validExt = ['jpg','jpeg','png','webp'];
      const safeExt = validExt.includes((ext || 'jpg').toLowerCase()) ? ext.toLowerCase() : 'jpg';
      const mime = safeExt === 'png' ? 'image/png' : safeExt === 'webp' ? 'image/webp' : 'image/jpeg';

      const b64 = foto_base64.replace(/^data:[^;]+;base64,/, '');
      const buf = Buffer.from(b64, 'base64');
      if (buf.length > 3145728) return res.status(400).json({ error: 'foto demasiado grande (max 3MB)' });

      const filename = `${device_hash.slice(0,12)}/${Date.now()}.${safeExt}`;
      const up = await fetch(`${SUPABASE_URL}/storage/v1/object/testigo-fotos/${filename}`, {
        method: 'POST',
        headers: {
          'apikey': SERVICE_KEY || ANON_KEY,
          'Authorization': `Bearer ${SERVICE_KEY || ANON_KEY}`,
          'Content-Type': mime,
          'x-upsert': 'true'
        },
        body: buf
      });
      if (!up.ok) {
        const errText = await up.text();
        return res.status(500).json({ error: 'upload failed', detail: errText.slice(0,200) });
      }
      const public_url = `${SUPABASE_URL}/storage/v1/object/public/testigo-fotos/${filename}`;
      return res.status(201).json({ ok: true, foto_url: public_url });
    }

    // ── Debates — listar ──
    if (vista === 'debates_listar') {
      const estatus = req.query.estatus || 'abierto';
      const items = await sb(`debates?estatus=eq.${encodeURIComponent(estatus)}&select=*&order=destacado.desc,argumentos_count.desc,fecha_inicio.desc&limit=100`);
      return res.status(200).json({ items, total: items.length });
    }

    // ── Debates — detalle con argumentos por posición ──
    if (vista === 'debates_detalle') {
      const id = req.query.id;
      const slug = (req.query.slug || '').trim();
      if (!id && !slug) return res.status(400).json({ error: 'id o slug requerido' });
      const debQuery = id 
        ? `debates?id=eq.${encodeURIComponent(id)}&select=*&limit=1`
        : `debates?slug=eq.${encodeURIComponent(slug)}&select=*&limit=1`;
      const debs = await sb(debQuery);
      if (!debs.length) return res.status(404).json({ error: 'debate no encontrado' });
      const debate = debs[0];
      const argumentos = await sb(`debate_argumentos?debate_id=eq.${debate.id}&estatus=eq.publicado&select=*&order=score.desc,created_at.desc&limit=200`);
      const porPosicion = { a_favor: [], en_contra: [], matiz: [] };
      for (const a of argumentos) porPosicion[a.posicion]?.push(a);
      return res.status(200).json({ debate, argumentos: porPosicion });
    }

    // ── Debates — agregar argumento (Nivel 2, rate limit 5min) ──
    if (vista === 'debate_argumentar') {
      if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const { debate_id, posicion, argumento, device_hash, honeypot, tiempo_en_pagina_ms } = body;
      
      if (honeypot) return res.status(400).json({ error: 'bot' });
      if (typeof tiempo_en_pagina_ms === 'number' && tiempo_en_pagina_ms < 10000) {
        return res.status(400).json({ error: 'Espera al menos 10 segundos' });
      }
      if (!debate_id || !posicion || !argumento) return res.status(400).json({ error: 'debate_id, posicion y argumento requeridos' });
      if (!['a_favor','en_contra','matiz'].includes(posicion)) return res.status(400).json({ error: 'posicion invalida' });
      if (argumento.length < 50 || argumento.length > 2000) return res.status(400).json({ error: 'argumento debe tener entre 50 y 2000 caracteres' });
      if (!device_hash || device_hash.length < 10) return res.status(400).json({ error: 'device_hash requerido' });
      
      // Rate limit: 1 cada 5 min por device
      const cincoMin = new Date(Date.now() - 5 * 60 * 1000).toISOString();
      const recientes = await sb(`debate_argumentos?device_hash=eq.${encodeURIComponent(device_hash)}&created_at=gte.${encodeURIComponent(cincoMin)}&select=id`);
      if (recientes.length > 0) {
        return res.status(429).json({ error: 'Espera 5 minutos entre argumentos' });
      }
      
      // IP hash
      const ipRaw = (req.headers['x-forwarded-for'] || req.connection?.remoteAddress || '').split(',')[0].trim() || 'unknown';
      const ip_hash = nodeCrypto.createHash('sha256').update(ipRaw + (process.env.SALT_IP || 'tequio-salt-v1')).digest('hex').slice(0, 32);
      
      const ins = await fetch(`${SUPABASE_URL}/rest/v1/debate_argumentos`, {
        method: 'POST',
        headers: {
          'apikey': SERVICE_KEY || ANON_KEY,
          'Authorization': `Bearer ${SERVICE_KEY || ANON_KEY}`,
          'Content-Type': 'application/json',
          'Prefer': 'return=representation'
        },
        body: JSON.stringify({
          debate_id: parseInt(debate_id, 10),
          posicion,
          argumento: argumento.trim(),
          device_hash,
          ip_hash
        })
      });
      
      if (!ins.ok) {
        const err = await ins.text();
        return res.status(500).json({ error: 'insert failed', detail: err.slice(0, 200) });
      }
      const data = await ins.json();
      return res.status(201).json({ ok: true, argumento: Array.isArray(data) ? data[0] : data });
    }

    // ── Debates — votar argumento ──
    if (vista === 'debate_votar_arg') {
      if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const { argumento_id, voto, device_hash } = body;
      
      if (!argumento_id || !voto || !device_hash) return res.status(400).json({ error: 'argumento_id, voto y device_hash requeridos' });
      if (!['arriba','abajo'].includes(voto)) return res.status(400).json({ error: 'voto invalido' });
      
      const ins = await fetch(`${SUPABASE_URL}/rest/v1/debate_votos`, {
        method: 'POST',
        headers: {
          'apikey': SERVICE_KEY || ANON_KEY,
          'Authorization': `Bearer ${SERVICE_KEY || ANON_KEY}`,
          'Content-Type': 'application/json',
          'Prefer': 'return=minimal'
        },
        body: JSON.stringify({
          argumento_id: parseInt(argumento_id, 10),
          device_hash,
          voto
        })
      });
      
      if (!ins.ok) {
        const errText = await ins.text();
        // 409 = ya votó (UNIQUE constraint violation)
        if (ins.status === 409 || errText.includes('duplicate')) {
          return res.status(409).json({ error: 'Ya votaste este argumento' });
        }
        return res.status(500).json({ error: 'vote failed', detail: errText.slice(0, 200) });
      }
      
      return res.status(201).json({ ok: true });
    }

    // ── Ombligo Timeline — Eras históricas de México ──
    if (vista === 'eras_historicas') {
      const items = await sb('historia_mexico_eras?select=*&order=orden.asc');
      return res.status(200).json({ items, total: items.length });
    }

    // ── Mapa de Poder ──
    if (vista === 'mapa_poder') {
      const [actores, vinculos] = await Promise.all([
        sb('actores_poder?activo=eq.true&select=*&order=influencia_score.desc&limit=100'),
        sb('vinculos_poder?vigente=eq.true&select=*&limit=300')
      ]);
      return res.status(200).json({ actores, vinculos, total_actores: actores.length, total_vinculos: vinculos.length });
    }


    // ── Diario Civico — resumen personalizado ──
    if (vista === 'diario') {
      const clave = (req.query.estado || '').padStart(2, '0');
      const today = new Date().toISOString().slice(0, 10);
      const queries = [
        sb('noticias_civicas?select=*&order=fecha_publicacion.desc&limit=10').catch(() => []),
        sb('votaciones_pendientes?select=*&order=fecha.desc&limit=5').catch(() => []),
        sb('iniciativas?estado=eq.abierta&select=*&order=fecha_inicio.desc&limit=5').catch(() => []),
        clave && clave !== '00' 
          ? sb(`municipios?clave_entidad=eq.${clave}&select=clave_inegi,nombre,poblacion_total&order=poblacion_total.desc.nullslast&limit=5`).catch(() => [])
          : Promise.resolve([]),
        sb('econ_banxico?select=*&order=fecha.desc&limit=3').catch(() => []),
        sb('presas_cuencas?select=*&order=fecha.desc&limit=3').catch(() => [])
      ];
      const [noticias, votaciones, iniciativas, municipios_estado, banxico, presas] = await Promise.all(queries);
      // Dedup defensivo: noticias con titulo duplicado (mismo encabezado DOF, etc.)
      const noticiasUnicas = [];
      const titulosVistos = new Set();
      for (const n of (noticias || [])) {
        const key = (n.titulo || '').toLowerCase().trim();
        if (key && !titulosVistos.has(key)) {
          titulosVistos.add(key);
          noticiasUnicas.push(n);
        }
      }
      return res.status(200).json({
        fecha: today, estado_usuario: clave || null,
        noticias: noticiasUnicas, votaciones, iniciativas, municipios_estado, banxico, presas
      });
    }

    // ── Deuda Pública — Serie SHRFSP histórica ──
    // ── Chat Comunitario — listar mensajes recientes ──
    if (vista === 'chat_listar') {
      const estado = (req.query.estado || '').trim();
      const limit = Math.min(parseInt(req.query.limit || '50', 10), 200);
      let path = `chat_mensajes?oculto=eq.false&select=*&order=timestamp.desc&limit=${limit}`;
      if (estado && estado !== 'todos') path += `&estado=eq.${encodeURIComponent(estado)}`;
      const items = await sb(path);
      return res.status(200).json({ items: items.reverse(), total: items.length });
    }

    // ── Chat Comunitario — flag abusivo ──
    if (vista === 'chat_flag') {
      if (req.method !== 'POST') return res.status(405).json({ error: 'POST only' });
      const body = typeof req.body === 'string' ? JSON.parse(req.body) : (req.body || {});
      const { mensaje_id, device_hash } = body;
      if (!mensaje_id || !device_hash) return res.status(400).json({ error: 'mensaje_id y device_hash requeridos' });
      const rows = await sb(`chat_mensajes?id=eq.${encodeURIComponent(mensaje_id)}&select=reportado`);
      if (!rows.length) return res.status(404).json({ error: 'no encontrado' });
      const nuevoCount = (rows[0].reportado || 0) + 1;
      const oculto = nuevoCount >= 3;
      const patch = await fetch(`${SUPABASE_URL}/rest/v1/chat_mensajes?id=eq.${encodeURIComponent(mensaje_id)}`, {
        method: 'PATCH',
        headers: { 'apikey': SERVICE_KEY || ANON_KEY, 'Authorization': `Bearer ${SERVICE_KEY || ANON_KEY}`, 'Content-Type': 'application/json', 'Prefer': 'return=minimal' },
        body: JSON.stringify({ reportado: nuevoCount, oculto })
      });
      if (!patch.ok) return res.status(500).json({ error: 'flag failed' });
      return res.status(200).json({ ok: true, oculto });
    }

    if (vista === 'deuda') {
      const items = await sb('deuda_publica_historico?select=*&order=anio.asc');
      const ultimo = items[items.length - 1];
      const total = items.length;
      return res.status(200).json({ items, ultimo, total });
    }

    // Mi Rep vs Yo - votaciones divisivas + diputados estado + sus votos
    if (vista === 'mi_rep_datos') {
      const claveEnt = (req.query.clave_entidad || '').trim().padStart(2, '0');
      const limit = Math.min(parseInt(req.query.limit || '20', 10), 50);

      const votaciones = await sb(`votaciones_diputados?total_no=gt.50&order=fecha.desc.nullslast&limit=${limit}&select=votacion_id,fecha,asunto,tipo,total_si,total_no,total_abst,total_ausente,resultado,url_oficial`);

      let dips = [];
      if (claveEnt && claveEnt !== '00') {
        const NOMBRES = {"01":"Aguascalientes","02":"Baja California","03":"Baja California Sur","04":"Campeche","05":"Coahuila","06":"Colima","07":"Chiapas","08":"Chihuahua","09":"Ciudad de Mexico","10":"Durango","11":"Guanajuato","12":"Guerrero","13":"Hidalgo","14":"Jalisco","15":"Estado de Mexico","16":"Michoacan","17":"Morelos","18":"Nayarit","19":"Nuevo Leon","20":"Oaxaca","21":"Puebla","22":"Queretaro","23":"Quintana Roo","24":"San Luis Potosi","25":"Sinaloa","26":"Sonora","27":"Tabasco","28":"Tamaulipas","29":"Tlaxcala","30":"Veracruz","31":"Yucatan","32":"Zacatecas"};
        const nombreEstado = NOMBRES[claveEnt];
        if (nombreEstado) {
          const variantes = [nombreEstado];
          if (claveEnt === '09') variantes.push('Ciudad de M\u00e9xico');
          if (claveEnt === '15') variantes.push('M\u00e9xico', 'Mexico', 'Estado de M\u00e9xico');
          if (claveEnt === '19') variantes.push('Nuevo Le\u00f3n');
          if (claveEnt === '16') variantes.push('Michoac\u00e1n');
          if (claveEnt === '22') variantes.push('Quer\u00e9taro');
          if (claveEnt === '24') variantes.push('San Luis Potos\u00ed');
          if (claveEnt === '31') variantes.push('Yucat\u00e1n');
          const filter = variantes.map(v => `entidad.ilike.*${encodeURIComponent(v)}*`).join(',');
          dips = await sb(`politicos_diputados?or=(${filter})&select=dipt_id,nombre,partido,partido_codigo,entidad,distrito,principio_eleccion,foto_url&limit=100`).catch(() => []);
        }
      }

      let votosInd = [];
      if (dips.length && votaciones.length) {
        const dipIds = dips.map(d => d.dipt_id).filter(Boolean);
        const votIds = votaciones.map(v => v.votacion_id);
        if (dipIds.length && votIds.length) {
          votosInd = await sb(`votos_individuales?dipt_id=in.(${dipIds.join(',')})&votacion_id=in.(${votIds.join(',')})&select=*&limit=10000`).catch(() => []);
        }
      }

      return res.status(200).json({
        votaciones,
        diputados: dips,
        votos_individuales: votosInd,
        clave_entidad: claveEnt || null
      });
    }

    // ── Notificaciones — agregador de eventos recientes ──
    if (vista === 'notificaciones') {
      const claveEnt = (req.query.estado || '').trim();
      const sinceStr = (req.query.since || '').trim();
      const since = sinceStr ? new Date(sinceStr) : new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);

      const [votaciones, iniciativas, leyesRec, banxico, noticias, asfRec] = await Promise.all([
        sb('votaciones_diputados?total_no=gt.20&order=fecha.desc.nullslast&limit=3&select=votacion_id,fecha,asunto').catch(() => []),
        sb('iniciativas?estado=eq.abierta&order=fecha_inicio.desc&limit=2&select=id,titulo,tipo,fecha_inicio').catch(() => []),
        sb('leyes?order=fecha_publicacion.desc.nullslast&limit=2&select=id,titulo,tipo,fecha_publicacion').catch(() => []),
        sb('econ_banxico?order=fecha.desc&limit=1&select=fecha,serie,titulo,valor').catch(() => []),
        sb('noticias_civicas?order=fecha_publicacion.desc&limit=3&select=titulo,fuente,fecha_publicacion,url').catch(() => []),
        sb('asf_auditorias?order=fecha_entrega.desc&limit=1&select=cuenta_publica,entrega,fecha_entrega,num_auditorias').catch(() => [])
      ]);

      const items = [];

      votaciones.forEach(v => items.push({
        tipo: 'votacion',
        icono: '🗳️',
        titulo: 'Votación divisiva en Diputados',
        desc: (v.asunto || '').slice(0, 130),
        fecha: v.fecha,
        link: '/panel/mi-rep-vs-yo.html?embedded=1',
        slug: 'mi-rep'
      }));

      iniciativas.forEach(i => items.push({
        tipo: 'iniciativa',
        icono: i.tipo === 'voto_congreso' ? '🗳️' : '✍️',
        titulo: i.tipo === 'voto_congreso' ? 'Vota antes que el Congreso' : 'Firma iniciativa ciudadana',
        desc: i.titulo,
        fecha: i.fecha_inicio,
        link: i.tipo === 'voto_congreso' ? '/panel/votar.html?embedded=1' : '/panel/iniciativa-firmar.html?embedded=1',
        slug: i.tipo === 'voto_congreso' ? 'votar' : 'iniciativa-firmar'
      }));

      leyesRec.forEach(l => l.fecha_publicacion && items.push({
        tipo: 'ley',
        icono: '📜',
        titulo: 'Nueva ley publicada',
        desc: l.titulo,
        fecha: l.fecha_publicacion,
        link: null,
        slug: null
      }));

      if (banxico[0] && banxico[0].fecha) {
        const b = banxico[0];
        items.push({
          tipo: 'banxico',
          icono: '💰',
          titulo: `${b.titulo || b.serie || 'Indicador económico'} actualizado`,
          desc: `Valor: ${b.valor != null ? Number(b.valor).toLocaleString('es-MX', { maximumFractionDigits: 4 }) : '—'}`,
          fecha: b.fecha,
          link: null,
          slug: null
        });
      }

      noticias.forEach(n => items.push({
        tipo: 'noticia',
        icono: '📰',
        titulo: 'Noticia cívica',
        desc: n.titulo,
        fecha: n.fecha_publicacion,
        link: n.url || null,
        slug: null,
        external: !!n.url
      }));

      if (asfRec[0]) {
        const a = asfRec[0];
        items.push({
          tipo: 'asf',
          icono: '🔍',
          titulo: `ASF — ${a.entrega} Cuenta Pública ${a.cuenta_publica}`,
          desc: `${a.num_auditorias} auditorías documentadas`,
          fecha: a.fecha_entrega,
          link: '/panel/asf.html?embedded=1',
          slug: 'asf'
        });
      }

      const sorted = items.filter(i => i.fecha).sort((a, b) => new Date(b.fecha) - new Date(a.fecha)).slice(0, 12);
      const unread = sorted.filter(i => new Date(i.fecha) > since).length;

      return res.status(200).json({ items: sorted, total: sorted.length, unread });
    }

    // ==================== APROBACION DEL PUEBLO ====================
    if (vista === 'aprobacion_actores') {
      const tipo = (req.query.tipo || '').toString();
      const entidad = (req.query.entidad || '').toString();
      const partido = (req.query.partido || '').toString();
      const device = (req.query.device || '').toString();
      let q = '?activo=eq.true&select=slug,nombre,tipo,cargo_actual,organizacion,entidad,partido,bio_corta,fuente_url';
      if (tipo) q += '&tipo=eq.' + encodeURIComponent(tipo);
      else q += '&tipo=in.(politico,gobernador,ministro_scjn,consejero_ine,magistrado_tepjf,secretario_federal,lider_partido,diputado_presidencia,fiscal)';
      if (entidad) q += '&entidad=eq.' + encodeURIComponent(entidad);
      if (partido) q += '&partido=eq.' + encodeURIComponent(partido);
      q += '&order=nombre.asc&limit=500';
      const actores = (await sb('actores_poder' + q)) || [];
      const allVotos = (await sb('aprobacion_actores?select=actor_slug,voto&limit=100000')) || [];
      const counts = {};
      for (const v of allVotos) {
        if (!counts[v.actor_slug]) counts[v.actor_slug] = { aprueba:0, desaprueba:0, neutral:0, total:0 };
        counts[v.actor_slug][v.voto] = (counts[v.actor_slug][v.voto]||0) + 1;
        counts[v.actor_slug].total++;
      }
      const miVoto = {};
      if (device) {
        const mis = (await sb('aprobacion_actores?device_hash=eq.' + encodeURIComponent(device) + '&select=actor_slug,voto&limit=5000')) || [];
        for (const v of mis) miVoto[v.actor_slug] = v.voto;
      }
      const out = actores.map(a => ({
        slug:a.slug, nombre:a.nombre, tipo:a.tipo, cargo_actual:a.cargo_actual, organizacion:a.organizacion, entidad:a.entidad, partido:a.partido, bio_corta:a.bio_corta, fuente_url:a.fuente_url,
        counts: counts[a.slug] || { aprueba:0, desaprueba:0, neutral:0, total:0 },
        mi_voto: miVoto[a.slug] || null
      }));
      return res.status(200).json({ actores: out, total: out.length });
    }

    if (vista === 'aprobacion_leyes') {
      const device = (req.query.device || '').toString();
      const cat = (req.query.categoria || '').toString();
      let q = '?destacada=eq.true&select=*';
      if (cat) q += '&categoria=eq.' + encodeURIComponent(cat);
      q += '&order=orden.asc&limit=200';
      const leyes = (await sb('leyes_destacadas' + q)) || [];
      const votos = (await sb('aprobacion_leyes?select=ley_id,voto&limit=100000')) || [];
      const counts = {};
      for (const v of votos) {
        if (!counts[v.ley_id]) counts[v.ley_id] = { aprueba:0, desaprueba:0, neutral:0, total:0 };
        counts[v.ley_id][v.voto] = (counts[v.ley_id][v.voto]||0) + 1;
        counts[v.ley_id].total++;
      }
      const miVoto = {};
      if (device) {
        const mis = (await sb('aprobacion_leyes?device_hash=eq.' + encodeURIComponent(device) + '&select=ley_id,voto&limit=5000')) || [];
        for (const v of mis) miVoto[v.ley_id] = v.voto;
      }
      const out = leyes.map(l => ({
        slug:l.slug, titulo_oficial:l.titulo_oficial, titulo_ciudadano:l.titulo_ciudadano, tipo:l.tipo, ambito:l.ambito, resumen_ciudadano:l.resumen_ciudadano, categoria:l.categoria, emoji:l.emoji, url_oficial:l.url_oficial,
        counts: counts[l.slug] || { aprueba:0, desaprueba:0, neutral:0, total:0 },
        mi_voto: miVoto[l.slug] || null
      }));
      return res.status(200).json({ leyes: out, total: out.length });
    }

    if (vista === 'aprobacion_votar_actor' || vista === 'aprobacion_votar_ley') {
      const isActor = vista === 'aprobacion_votar_actor';
      const target = isActor ? (req.query.slug || '').toString() : (req.query.ley_id || '').toString();
      const tipo = (req.query.actor_tipo || 'politico').toString();
      const titulo = (req.query.titulo || target).toString();
      const voto = (req.query.voto || '').toString();
      const device = (req.query.device || '').toString();
      const entidad = (req.query.entidad || '').toString();
      if (!target || !voto || !device) return res.status(400).json({ error: 'Faltan parametros: target/voto/device' });
      if (!['aprueba','desaprueba','neutral'].includes(voto)) return res.status(400).json({ error: 'voto invalido' });
      if (device.length < 8 || device.length > 128) return res.status(400).json({ error: 'device invalido' });
      const ip = (req.headers['x-forwarded-for'] || '').split(',')[0].trim();
      const ipHash = nodeCrypto.createHash('sha256').update(ip + '|tequio').digest('hex').substring(0, 16);
      const table = isActor ? 'aprobacion_actores' : 'aprobacion_leyes';
      const onConflict = isActor ? 'actor_slug,device_hash' : 'ley_id,device_hash';
      const body = isActor
        ? { actor_slug: target, actor_tipo: tipo, voto, device_hash: device, ip_hash: ipHash, clave_entidad: entidad || null }
        : { ley_id: target, ley_titulo: titulo, voto, device_hash: device, ip_hash: ipHash, clave_entidad: entidad || null };
      try {
        const key = SERVICE_KEY || ANON_KEY;
        const url = SUPABASE_URL + '/rest/v1/' + table + '?on_conflict=' + onConflict;
        const r = await fetch(url, {
          method: 'POST',
          headers: {
            apikey: key,
            Authorization: 'Bearer ' + key,
            'Content-Type': 'application/json',
            Prefer: 'resolution=merge-duplicates,return=representation'
          },
          body: JSON.stringify(body)
        });
        if (!r.ok) {
          const t = await r.text();
          return res.status(500).json({ error: 'Error voto', detail: t.substring(0,200) });
        }
        return res.status(200).json({ ok: true });
      } catch (e) {
        return res.status(500).json({ error: e.message });
      }
    }

    if (vista === 'aprobacion_scoreboard') {
      const votos = (await sb('aprobacion_actores?select=actor_slug,voto&limit=100000')) || [];
      const actores = (await sb('actores_poder?activo=eq.true&select=slug,nombre,cargo_actual,entidad,partido,tipo&limit=2000')) || [];
      const idx = {};
      for (const a of actores) idx[a.slug] = a;
      const agg = {};
      for (const v of votos) {
        if (!agg[v.actor_slug]) agg[v.actor_slug] = { aprueba:0, desaprueba:0, neutral:0, total:0 };
        agg[v.actor_slug][v.voto] = (agg[v.actor_slug][v.voto]||0) + 1;
        agg[v.actor_slug].total++;
      }
      const rows = [];
      for (const slug of Object.keys(agg)) {
        if (!idx[slug]) continue;
        const c = agg[slug];
        rows.push({ slug, nombre: idx[slug].nombre, cargo_actual: idx[slug].cargo_actual, entidad: idx[slug].entidad, partido: idx[slug].partido, tipo: idx[slug].tipo, counts: c, pct_aprobacion: c.total > 0 ? Math.round((c.aprueba / c.total) * 100) : 0, pct_desaprobacion: c.total > 0 ? Math.round((c.desaprueba / c.total) * 100) : 0 });
      }
      const minVotos = parseInt(req.query.min_votos || '5');
      const filtered = rows.filter(r => r.counts.total >= minVotos);
      const top = [...filtered].sort((a,b) => b.pct_aprobacion - a.pct_aprobacion).slice(0, 20);
      const bottom = [...filtered].sort((a,b) => a.pct_aprobacion - b.pct_aprobacion).slice(0, 20);
      return res.status(200).json({ top_aprobados: top, top_desaprobados: bottom, total_actores: rows.length, min_votos: minVotos });
    }

    // ==================== PROMESOMETRO ====================
    if (vista === 'promesas_listar') {
      const actor = (req.query.actor_slug || '').toString();
      const tipo = (req.query.tipo || '').toString();
      const categoria = (req.query.categoria || '').toString();
      const estado = (req.query.estado || '').toString();
      const desastre = (req.query.desastre || '').toString();
      const ambito = (req.query.ambito || '').toString();
      const entidad = (req.query.entidad || '').toString();
      const device = (req.query.device || '').toString();
      let q = '?destacada=eq.true&select=*';
      if (actor) q += '&actor_slug=eq.' + encodeURIComponent(actor);
      if (tipo) q += '&tipo_promesa=eq.' + encodeURIComponent(tipo);
      if (categoria) q += '&categoria=eq.' + encodeURIComponent(categoria);
      if (estado) q += '&estado_actual=eq.' + encodeURIComponent(estado);
      if (desastre) q += '&desastre_slug=eq.' + encodeURIComponent(desastre);
      if (ambito) q += '&ambito=eq.' + encodeURIComponent(ambito);
      if (entidad) q += '&entidad=eq.' + encodeURIComponent(entidad);
      q += '&order=fecha_promesa.desc&limit=500';
      const promesas = (await sb('promesas' + q)) || [];
      // Get actor names for display
      const slugs = [...new Set(promesas.map(p => p.actor_slug))];
      let actores = [];
      if (slugs.length) {
        const filter = '?slug=in.(' + slugs.map(s => encodeURIComponent(s)).join(',') + ')&select=slug,nombre,cargo_actual,partido,entidad,tipo';
        actores = (await sb('actores_poder' + filter)) || [];
      }
      const aidx = {};
      for (const a of actores) aidx[a.slug] = a;
      // Vote aggregates
      const votos = (await sb('promesas_votos?select=promesa_slug,evaluacion&limit=100000')) || [];
      const counts = {};
      for (const v of votos) {
        if (!counts[v.promesa_slug]) counts[v.promesa_slug] = { cumplida:0, parcial:0, incumplida:0, no_se:0, total:0 };
        counts[v.promesa_slug][v.evaluacion] = (counts[v.promesa_slug][v.evaluacion]||0) + 1;
        counts[v.promesa_slug].total++;
      }
      const miVoto = {};
      if (device) {
        const mis = (await sb('promesas_votos?device_hash=eq.' + encodeURIComponent(device) + '&select=promesa_slug,evaluacion&limit=10000')) || [];
        for (const v of mis) miVoto[v.promesa_slug] = v.evaluacion;
      }
      const out = promesas.map(p => ({
        ...p,
        actor: aidx[p.actor_slug] || { slug: p.actor_slug, nombre: p.actor_slug },
        counts: counts[p.slug] || { cumplida:0, parcial:0, incumplida:0, no_se:0, total:0 },
        mi_voto: miVoto[p.slug] || null
      }));
      return res.status(200).json({ promesas: out, total: out.length });
    }

    if (vista === 'desastres_listar') {
      const desastres = (await sb('desastres?destacado=eq.true&order=fecha_inicio.desc&limit=100')) || [];
      // Count promesas per desastre
      const promesas = (await sb('promesas?desastre_slug=not.is.null&select=desastre_slug,estado_actual&limit=5000')) || [];
      const counts = {};
      for (const p of promesas) {
        if (!counts[p.desastre_slug]) counts[p.desastre_slug] = { total:0, cumplida:0, parcial:0, en_curso:0, incumplida:0 };
        counts[p.desastre_slug].total++;
        if (counts[p.desastre_slug][p.estado_actual] !== undefined) counts[p.desastre_slug][p.estado_actual]++;
      }
      const out = desastres.map(d => ({ ...d, promesas_stats: counts[d.slug] || { total:0 } }));
      return res.status(200).json({ desastres: out, total: out.length });
    }

    if (vista === 'promesas_votar') {
      const slug = (req.query.slug || '').toString();
      const evaluacion = (req.query.evaluacion || '').toString();
      const device = (req.query.device || '').toString();
      const entidad = (req.query.entidad || '').toString();
      if (!slug || !evaluacion || !device) return res.status(400).json({ error:'Faltan parametros: slug/evaluacion/device' });
      if (!['cumplida','parcial','incumplida','no_se'].includes(evaluacion)) return res.status(400).json({ error:'evaluacion invalida' });
      if (device.length < 8 || device.length > 128) return res.status(400).json({ error:'device invalido' });
      const ip = (req.headers['x-forwarded-for'] || '').split(',')[0].trim();
      const ipHash = nodeCrypto.createHash('sha256').update(ip + '|tequio').digest('hex').substring(0,16);
      const body = { promesa_slug: slug, evaluacion, device_hash: device, ip_hash: ipHash, clave_entidad: entidad || null };
      try {
        const key = SERVICE_KEY || ANON_KEY;
        const url = SUPABASE_URL + '/rest/v1/promesas_votos?on_conflict=promesa_slug,device_hash';
        const r = await fetch(url, {
          method:'POST',
          headers: {
            apikey: key,
            Authorization: 'Bearer ' + key,
            'Content-Type': 'application/json',
            Prefer: 'resolution=merge-duplicates,return=representation'
          },
          body: JSON.stringify(body)
        });
        if (!r.ok) {
          const t = await r.text();
          return res.status(500).json({ error:'Error voto', detail: t.substring(0,200) });
        }
        return res.status(200).json({ ok:true });
      } catch (e) {
        return res.status(500).json({ error: e.message });
      }
    }

    if (vista === 'promesa_detalle') {
      const slug = (req.query.slug || '').toString();
      if (!slug) return res.status(400).json({ error: 'slug requerido' });
      const arr = (await sb('promesas?slug=eq.' + encodeURIComponent(slug) + '&select=*')) || [];
      if (!arr.length) return res.status(404).json({ error: 'No encontrada' });
      const p = arr[0];
      const actorArr = (await sb('actores_poder?slug=eq.' + encodeURIComponent(p.actor_slug) + '&select=slug,nombre,cargo_actual,partido,entidad,tipo,fuente_url')) || [];
      p.actor = actorArr[0] || null;
      const votos = (await sb('promesas_votos?promesa_slug=eq.' + encodeURIComponent(slug) + '&select=evaluacion&limit=50000')) || [];
      const c = { cumplida:0, parcial:0, incumplida:0, no_se:0, total:0 };
      for (const v of votos) { c[v.evaluacion]++; c.total++; }
      p.counts = c;
      return res.status(200).json({ promesa: p });
    }

    // ==================== ATLAS NACIONAL ====================
    if (vista === 'atlas_categorias') {
      const entidad = (req.query.entidad || '').toString();
      let q = '?select=*';
      if (entidad) q += '&entidad=eq.' + encodeURIComponent(entidad);
      q += '&order=categoria.asc,subcategoria.asc&limit=2000';
      const cats = (await sb('puntos_categorias' + q)) || [];
      const grouped = {};
      for (const c of cats) {
        if (!grouped[c.categoria]) grouped[c.categoria] = { categoria: c.categoria, categoria_slug: c.categoria_slug, color: c.color, total: 0, subcategorias: [] };
        grouped[c.categoria].subcategorias.push({ subcategoria: c.subcategoria, subcategoria_slug: c.subcategoria_slug, total: c.total });
        grouped[c.categoria].total += c.total;
      }
      return res.status(200).json({ categorias: Object.values(grouped), total: cats.length });
    }

    if (vista === 'atlas_puntos') {
      const entidad = (req.query.entidad || '').toString();
      const categoria = (req.query.categoria || '').toString();
      const subcategoria = (req.query.subcategoria || '').toString();
      const bbox = (req.query.bbox || '').toString();
      const limit = Math.min(parseInt(req.query.limit || '500'), 5000);
      let q = '?activo=eq.true&select=slug,nombre,categoria,subcategoria,categoria_slug,subcategoria_slug,lat,lng,entidad,municipio,color,icon,fuente_origen,fuente_url';
      if (entidad) q += '&entidad=eq.' + encodeURIComponent(entidad);
      if (categoria) q += '&categoria_slug=eq.' + encodeURIComponent(categoria);
      if (subcategoria) q += '&subcategoria_slug=eq.' + encodeURIComponent(subcategoria);
      if (bbox) {
        const parts = bbox.split(',').map(Number);
        if (parts.length === 4 && parts.every(n => !isNaN(n))) {
          q += '&lat=gte.' + parts[0] + '&lat=lte.' + parts[2] + '&lng=gte.' + parts[1] + '&lng=lte.' + parts[3];
        }
      }
      q += '&limit=' + limit;
      const puntos = (await sb('puntos_servicio' + q)) || [];
      return res.status(200).json({ puntos, total: puntos.length });
    }

    if (vista === 'atlas_cerca_de_mi') {
      const lat = parseFloat(req.query.lat);
      const lng = parseFloat(req.query.lng);
      const radio = parseFloat(req.query.radio_km || '5');
      const categoria = (req.query.categoria || '').toString();
      const subcategoria = (req.query.subcategoria || '').toString();
      if (isNaN(lat) || isNaN(lng)) return res.status(400).json({ error: 'lat/lng requeridos' });
      const dlat = radio / 111;
      const dlng = radio / (111 * Math.cos(lat * Math.PI / 180));
      let q = '?activo=eq.true&select=*' +
        '&lat=gte.' + (lat - dlat) + '&lat=lte.' + (lat + dlat) +
        '&lng=gte.' + (lng - dlng) + '&lng=lte.' + (lng + dlng);
      if (categoria) q += '&categoria_slug=eq.' + encodeURIComponent(categoria);
      if (subcategoria) q += '&subcategoria_slug=eq.' + encodeURIComponent(subcategoria);
      q += '&limit=2000';
      const puntos = (await sb('puntos_servicio' + q)) || [];
      function hav(lat1, lon1, lat2, lon2) {
        const R = 6371;
        const toRad = x => x * Math.PI / 180;
        const dLat = toRad(lat2 - lat1);
        const dLon = toRad(lon2 - lon1);
        const a = Math.sin(dLat/2)**2 + Math.cos(toRad(lat1))*Math.cos(toRad(lat2))*Math.sin(dLon/2)**2;
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
      }
      const enriched = puntos.map(p => ({ ...p, distancia_km: hav(lat, lng, p.lat, p.lng) })).filter(p => p.distancia_km <= radio);
      enriched.sort((a,b) => a.distancia_km - b.distancia_km);
      return res.status(200).json({ puntos: enriched.slice(0, 100), total: enriched.length, radio_km: radio });
    }

    if (vista === 'atlas_punto_detalle') {
      const slug = (req.query.slug || '').toString();
      if (!slug) return res.status(400).json({ error: 'slug requerido' });
      const arr = (await sb('puntos_servicio?slug=eq.' + encodeURIComponent(slug) + '&select=*')) || [];
      if (!arr.length) return res.status(404).json({ error: 'No encontrado' });
      return res.status(200).json({ punto: arr[0] });
    }

    // ==================== DENUE PROXY ====================
    if (vista === 'denue_proxy') {
      const path = (req.query.path || '').toString();
      const token = (req.query.token || '').toString();
      if (!path || !token) return res.status(400).json({ error: 'path y token requeridos' });
      if (!/^[A-Za-z]+\/[0-9A-Za-z,\-\.\/]+$/.test(path)) return res.status(400).json({ error: 'path invalido' });
      try {
        const url = 'https://www.inegi.org.mx/app/api/denue/v1/consulta/' + path + '/' + token;
        const r = await fetch(url, { headers: { 'Accept': 'application/json', 'User-Agent': 'Tequio/1.0' } });
        const text = await r.text();
        let j; try { j = JSON.parse(text); } catch(e) {}
        return res.status(200).json({
          ok: r.ok, status: r.status,
          data: j || null,
          raw: j ? null : text.substring(0, 500),
          count: Array.isArray(j) ? j.length : null
        });
      } catch (e) {
        return res.status(500).json({ error: 'fetch failed: ' + e.message });
      }
    }

    return res.status(400).json({ error: 'Vista desconocida', vistas_disponibles: [
      'dashboard','clima','alertas','sequia','presas','diputados','votaciones',
      'mi_representante','buscar_diputado','senadores','senador_detalle','senadores_busqueda',
      'votaciones_pendientes','emitir_voto','registrar_ciudadano','me',
      'quemones_ranking','quemon_detalle','mi_rep_vs_yo',
      'contratos','contrato_detalle','proveedores_top','proveedor_detalle','compranet_stats',
      'despachos','crear_lead',
      'banxico_historico','inegi_estado','inegi_comparador','leyes_lista','directorio_estado',
      'chat','chat_publicar','chat_votar','chat_reportar',
      'aprobacion_actores','aprobacion_leyes','aprobacion_votar_actor','aprobacion_votar_ley','aprobacion_scoreboard',
      'promesas_listar','promesas_votar','promesa_detalle','desastres_listar',
      'atlas_categorias','atlas_puntos','atlas_cerca_de_mi','atlas_punto_detalle','denue_proxy'
    ]});
  } catch (e) {
    return res.status(500).json({ error: e.message });
  }
        }
