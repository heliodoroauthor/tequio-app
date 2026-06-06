// Tequio · Freshness Widget · "Estado de los datos"
// Hace visible el principio "Cero Invención" — el ciudadano puede verificar
// la frescura real de cada fuente en cualquier momento.
//
// Carga independiente: NO toca el monolito index.html.

(function () {
  var SUPABASE_URL = 'https://mhsuihwjgtzxflesbnxv.supabase.co';
  var ANON = 'sb_publishable_grJAWIMoSr5b8YfaB7HVlw_OesNipBz';
  var RPC = SUPABASE_URL + '/rest/v1/rpc/tequio_data_freshness';
  var STORAGE_KEY = 'tequio_freshness_cache_v1';
  var CACHE_TTL_MS = 10 * 60 * 1000; // 10 minutos

  // Etiquetas legibles por fuente
  var LABELS = {
    leyes:       { icon: '📜', titulo: 'Leyes mexicanas',           panel: 'leyes' },
    tesis_scjn:  { icon: '⚖️', titulo: 'Tesis SCJN',                 panel: 'jurisprudencia' },
    votaciones:  { icon: '🗳️', titulo: 'Votaciones del Congreso',   panel: 'votaciones' },
    diputados:   { icon: '🏛️', titulo: 'Cámara de Diputados',       panel: 'diputados' },
    noticias:    { icon: '📰', titulo: 'Noticias cívicas',           panel: 'noticias' },
    banxico:     { icon: '💱', titulo: 'Indicadores Banxico',        panel: 'dashboard' },
    profeco:     { icon: '🛒', titulo: 'Precios PROFECO',            panel: 'precios' },
    gasolina:    { icon: '⛽', titulo: 'Precios gasolina (CRE)',     panel: 'precios' },
    presas:      { icon: '💧', titulo: 'Presas CONAGUA',             panel: 'presas' },
    contratos:   { icon: '📑', titulo: 'Contratos federales OCDS',   panel: 'contratos' },
    sequia:      { icon: '🌵', titulo: 'Monitor de sequía SMN',      panel: 'sequia' },
    embajadas:   { icon: '🌎', titulo: 'Embajadas y consulados',     panel: 'sre' },
    municipios:  { icon: '🏘️', titulo: 'Municipios INEGI',          panel: 'municipios' },
    delitos:     { icon: '⚠️', titulo: 'Delitos municipales SESNSP', panel: 'estado' }
  };

  // Status → color
  var COLORS = {
    fresca:    { bg: 'rgba(34,197,94,0.12)',   border: 'rgba(34,197,94,0.45)',   txt: '#4ade80', label: 'FRESCA' },
    reciente:  { bg: 'rgba(59,130,246,0.10)',  border: 'rgba(59,130,246,0.40)',  txt: '#60a5fa', label: 'RECIENTE' },
    stale:     { bg: 'rgba(251,191,36,0.10)',  border: 'rgba(251,191,36,0.40)',  txt: '#fbbf24', label: 'STALE' },
    vieja:     { bg: 'rgba(239,68,68,0.10)',   border: 'rgba(239,68,68,0.40)',   txt: '#f87171', label: 'VIEJA' },
    sin_datos: { bg: 'rgba(100,116,139,0.10)', border: 'rgba(100,116,139,0.40)', txt: '#94a3b8', label: 'SIN DATOS' }
  };

  function fmtDias(d) {
    if (d == null) return '—';
    if (d === 0) return 'hoy';
    if (d === 1) return 'ayer';
    if (d < 30) return 'hace ' + d + ' días';
    if (d < 365) return 'hace ' + Math.round(d / 30) + ' meses';
    return 'hace ' + Math.round(d / 365) + ' años';
  }

  function loadCache() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var obj = JSON.parse(raw);
      if (Date.now() - obj.cached_at > CACHE_TTL_MS) return null;
      return obj.data;
    } catch (e) { return null; }
  }

  function saveCache(data) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ data: data, cached_at: Date.now() }));
    } catch (e) {}
  }

  async function fetchFreshness() {
    var cached = loadCache();
    if (cached) return cached;
    try {
      var res = await fetch(RPC, {
        method: 'POST',
        headers: { 'apikey': ANON, 'Authorization': 'Bearer ' + ANON, 'Content-Type': 'application/json' },
        body: '{}'
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      var data = await res.json();
      saveCache(data);
      return data;
    } catch (e) {
      console.warn('[tequio-freshness] error:', e);
      return null;
    }
  }

  function renderModal(data) {
    if (!data || !data.fuentes) return '<div style="padding:24px;color:#94a3b8">No se pudo cargar el estado de datos. Reintenta.</div>';

    var fuentes = data.fuentes;
    var html = '';

    // Resumen rápido
    var counts = { fresca: 0, reciente: 0, stale: 0, vieja: 0, sin_datos: 0 };
    Object.keys(fuentes).forEach(function (k) {
      var status = fuentes[k].status;
      if (counts[status] !== undefined) counts[status]++;
    });
    var total = Object.keys(fuentes).length;
    var saludables = counts.fresca + counts.reciente;
    var porcentaje = Math.round(saludables * 100 / total);

    html += '<div style="padding:18px 20px;border-bottom:1px solid rgba(255,255,255,0.08);">';
    html += '  <div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:#64748b;font-weight:700;margin-bottom:6px">🦎 CERO INVENCIÓN · ESTADO DE DATOS</div>';
    html += '  <div style="font-size:22px;font-weight:800;color:#e8edf5;margin-bottom:8px">' + porcentaje + '% de las fuentes están frescas o recientes</div>';
    html += '  <div style="font-size:12px;color:#94a3b8;line-height:1.6">';
    html += '    ' + counts.fresca + ' fresca' + (counts.fresca !== 1 ? 's' : '') + ' · ';
    html += '    ' + counts.reciente + ' reciente' + (counts.reciente !== 1 ? 's' : '');
    if (counts.stale > 0) html += ' · <strong style="color:#fbbf24">' + counts.stale + ' stale</strong>';
    if (counts.vieja > 0) html += ' · <strong style="color:#f87171">' + counts.vieja + ' vieja' + (counts.vieja !== 1 ? 's' : '') + '</strong>';
    html += '  </div>';
    html += '</div>';

    // Lista detallada — ordenar por dias_atras ASC (frescas primero)
    var keys = Object.keys(fuentes).sort(function (a, b) {
      return (fuentes[a].dias_atras || 0) - (fuentes[b].dias_atras || 0);
    });

    html += '<div style="max-height:60vh;overflow-y:auto;padding:8px;">';
    keys.forEach(function (k) {
      var f = fuentes[k];
      var meta = LABELS[k] || { icon: '📊', titulo: k, panel: null };
      var col = COLORS[f.status] || COLORS.sin_datos;

      html += '<div style="display:grid;grid-template-columns:40px 1fr auto;gap:12px;padding:12px 14px;border-radius:10px;background:' + col.bg + ';border:1px solid ' + col.border + ';margin:6px 4px;align-items:center">';
      html += '  <div style="font-size:22px;text-align:center">' + meta.icon + '</div>';
      html += '  <div>';
      html += '    <div style="font-weight:700;font-size:13px;color:#e8edf5;line-height:1.3">' + meta.titulo + '</div>';
      html += '    <div style="font-size:11px;color:#94a3b8;margin-top:3px">' + f.fuente_oficial + '</div>';
      html += '    <div style="font-size:11px;color:#64748b;margin-top:3px">📅 ' + (f.fecha || 'sin dato') + ' · ' + (f.total != null ? f.total.toLocaleString('es-MX') + ' registros' : '') + '</div>';
      html += '  </div>';
      html += '  <div style="text-align:right">';
      html += '    <div style="font-family:monospace;font-weight:800;font-size:10px;letter-spacing:0.5px;color:' + col.txt + '">' + col.label + '</div>';
      html += '    <div style="font-size:10px;color:#64748b;margin-top:2px">' + fmtDias(f.dias_atras) + '</div>';
      html += '  </div>';
      html += '</div>';
    });
    html += '</div>';

    html += '<div style="padding:14px 20px;border-top:1px solid rgba(255,255,255,0.08);font-size:10px;color:#64748b;line-height:1.6">';
    html += '  <strong>🦎 Cero Invención:</strong> cada dato en Tequio proviene de una fuente oficial verificable.<br>';
    html += '  Cuando una fuente tarda en actualizarse, lo mostramos. No inventamos datos para "completar" lagunas.<br>';
    html += '  Criterios: <strong style="color:#4ade80">fresca</strong> ≤ 7 días · <strong style="color:#60a5fa">reciente</strong> ≤ 30 días · <strong style="color:#fbbf24">stale</strong> ≤ 90 días · <strong style="color:#f87171">vieja</strong> > 90 días.';
    html += '</div>';

    return html;
  }

  function ensureStyles() {
    if (document.getElementById('tequio-freshness-styles')) return;
    var s = document.createElement('style');
    s.id = 'tequio-freshness-styles';
    s.textContent = [
      '#tequio-freshness-btn{position:fixed;bottom:18px;left:18px;z-index:9998;background:linear-gradient(135deg,#1a243d,#1e2a42);color:#e8edf5;border:1px solid rgba(255,255,255,0.12);border-radius:999px;padding:9px 14px;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit;display:flex;align-items:center;gap:6px;box-shadow:0 4px 18px rgba(0,0,0,0.4);transition:transform .15s,border-color .15s}',
      '#tequio-freshness-btn:hover{transform:translateY(-1px);border-color:rgba(74,222,128,0.5)}',
      '#tequio-freshness-btn .dot{width:8px;height:8px;border-radius:50%;background:#4ade80;box-shadow:0 0 8px rgba(74,222,128,0.7);animation:tfp 2s ease-in-out infinite}',
      '@keyframes tfp{0%,100%{opacity:1}50%{opacity:.4}}',
      '#tequio-freshness-overlay{position:fixed;inset:0;background:rgba(10,15,26,0.85);backdrop-filter:blur(6px);z-index:9999;display:none;align-items:center;justify-content:center;padding:18px;font-family:"DM Sans",system-ui,sans-serif}',
      '#tequio-freshness-overlay.show{display:flex}',
      '#tequio-freshness-modal{background:linear-gradient(135deg,#0e1525,#1a243d);border:1px solid rgba(255,255,255,0.12);border-radius:16px;max-width:560px;width:100%;max-height:90vh;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.6)}',
      '#tequio-freshness-close{position:absolute;top:14px;right:14px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);color:#94a3b8;width:32px;height:32px;border-radius:50%;cursor:pointer;font-size:16px;font-family:inherit;display:flex;align-items:center;justify-content:center}',
      '#tequio-freshness-close:hover{color:#e8edf5;background:rgba(255,255,255,0.12)}',
      // Esconder en mobile cuando hay teclado / scroll en sticky bottom
      '@media (max-width:520px){#tequio-freshness-btn{bottom:auto;top:80px;left:auto;right:18px;padding:7px 11px;font-size:11px}}'
    ].join('\n');
    document.head.appendChild(s);
  }

  function createBtn() {
    if (document.getElementById('tequio-freshness-btn')) return;
    var btn = document.createElement('button');
    btn.id = 'tequio-freshness-btn';
    btn.innerHTML = '<span class="dot"></span> Estado de datos';
    btn.title = 'Tequio · Cero Invención · ver estado real de cada fuente';
    btn.addEventListener('click', openModal);
    document.body.appendChild(btn);
  }

  async function openModal() {
    ensureStyles();
    var overlay = document.getElementById('tequio-freshness-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'tequio-freshness-overlay';
      overlay.innerHTML = '<div id="tequio-freshness-modal"><button id="tequio-freshness-close" aria-label="Cerrar">×</button><div id="tequio-freshness-body" style="position:relative"><div style="padding:40px;text-align:center;color:#94a3b8">Cargando…</div></div></div>';
      document.body.appendChild(overlay);
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) closeModal();
      });
      document.getElementById('tequio-freshness-close').addEventListener('click', closeModal);
    }
    overlay.classList.add('show');
    var data = await fetchFreshness();
    document.getElementById('tequio-freshness-body').innerHTML = renderModal(data);
  }

  function closeModal() {
    var o = document.getElementById('tequio-freshness-overlay');
    if (o) o.classList.remove('show');
  }

  function init() {
    ensureStyles();
    createBtn();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
