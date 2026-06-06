// Tequio · Freshness + Modules Widget · "Cero Invención"
// Hace visible al ciudadano: (1) freshness real de cada fuente, (2) estado honesto
// de cada módulo (operativo / en desarrollo / roadmap).
// Componente independiente: NO toca el monolito index.html.

(function () {
  var SUPABASE_URL = 'https://mhsuihwjgtzxflesbnxv.supabase.co';
  var ANON = 'sb_publishable_grJAWIMoSr5b8YfaB7HVlw_OesNipBz';
  var RPC_FRESH   = SUPABASE_URL + '/rest/v1/rpc/tequio_data_freshness';
  var RPC_MODULES = SUPABASE_URL + '/rest/v1/rpc/tequio_modules_status';
  var STORAGE_KEY = 'tequio_freshness_cache_v2';
  var CACHE_TTL_MS = 10 * 60 * 1000;

  var LABELS = {
    leyes:       { icon: '📜', titulo: 'Leyes mexicanas' },
    tesis_scjn:  { icon: '⚖️', titulo: 'Tesis SCJN' },
    votaciones:  { icon: '🗳️', titulo: 'Votaciones del Congreso' },
    diputados:   { icon: '🏛️', titulo: 'Cámara de Diputados' },
    noticias:    { icon: '📰', titulo: 'Noticias cívicas' },
    banxico:     { icon: '💱', titulo: 'Indicadores Banxico' },
    profeco:     { icon: '🛒', titulo: 'Precios PROFECO' },
    gasolina:    { icon: '⛽', titulo: 'Precios gasolina (CRE)' },
    presas:      { icon: '💧', titulo: 'Presas CONAGUA' },
    contratos:   { icon: '📑', titulo: 'Contratos federales OCDS' },
    sequia:      { icon: '🌵', titulo: 'Monitor de sequía SMN' },
    embajadas:   { icon: '🌎', titulo: 'Embajadas y consulados' },
    municipios:  { icon: '🏘️', titulo: 'Municipios INEGI' },
    delitos:     { icon: '⚠️', titulo: 'Delitos municipales SESNSP' }
  };

  var MODULE_LABELS = {
    iniciativas_ciudadanas:    { icon: '🗳️', titulo: 'Iniciativas ciudadanas' },
    verificacion_promesas:     { icon: '✅', titulo: 'Verificación de promesas' },
    voto_simulado:             { icon: '🗳️', titulo: 'Voto simulado del Congreso' },
    despachos_legales:         { icon: '⚖️', titulo: 'Despachos verificados' },
    leads_legales:             { icon: '📨', titulo: 'Canalización legal' },
    chat_comunidad:            { icon: '💬', titulo: 'Chat comunitario' },
    testigo_civico:            { icon: '📸', titulo: 'Testigo cívico' },
    asf_auditorias:            { icon: '🔍', titulo: 'Auditorías ASF' },
    cfe_tarifas:               { icon: '💡', titulo: 'Tarifas CFE' },
    deuda_publica_historico:   { icon: '💰', titulo: 'Deuda pública (histórico)' },
    historia_mexico_eras:      { icon: '📚', titulo: 'Historia de México' },
    inah_zonas_arqueologicas:  { icon: '🏛️', titulo: 'Zonas arqueológicas INAH' },
    inah_museos:               { icon: '🏛️', titulo: 'Museos INAH' },
    unesco_mexico:             { icon: '🌍', titulo: 'Patrimonio UNESCO' },
    inali_lenguas:             { icon: '🗣️', titulo: 'Lenguas indígenas INALI' },
    indicadores_fiscales:      { icon: '💹', titulo: 'Indicadores fiscales' },
    usuarios_ciudadanos:       { icon: '👤', titulo: 'Cuentas de usuario' },
    colaboradores_stats:       { icon: '🤝', titulo: 'Colaboradores externos' }
  };

  var COLORS = {
    fresca:    { bg: 'rgba(34,197,94,0.12)',   border: 'rgba(34,197,94,0.45)',   txt: '#4ade80', label: 'FRESCA' },
    reciente:  { bg: 'rgba(59,130,246,0.10)',  border: 'rgba(59,130,246,0.40)',  txt: '#60a5fa', label: 'RECIENTE' },
    stale:     { bg: 'rgba(251,191,36,0.10)',  border: 'rgba(251,191,36,0.40)',  txt: '#fbbf24', label: 'STALE' },
    vieja:     { bg: 'rgba(239,68,68,0.10)',   border: 'rgba(239,68,68,0.40)',   txt: '#f87171', label: 'VIEJA' },
    sin_datos: { bg: 'rgba(100,116,139,0.10)', border: 'rgba(100,116,139,0.40)', txt: '#94a3b8', label: 'SIN DATOS' },
    operativo:    { bg: 'rgba(34,197,94,0.12)',  border: 'rgba(34,197,94,0.45)',  txt: '#4ade80', label: 'OPERATIVO' },
    inicial:      { bg: 'rgba(59,130,246,0.10)', border: 'rgba(59,130,246,0.40)', txt: '#60a5fa', label: 'INICIAL' },
    proximamente: { bg: 'rgba(251,191,36,0.10)', border: 'rgba(251,191,36,0.40)', txt: '#fbbf24', label: 'PRÓXIMAMENTE' },
    roadmap:      { bg: 'rgba(100,116,139,0.10)',border: 'rgba(100,116,139,0.40)',txt: '#94a3b8', label: 'ROADMAP' }
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
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ data: data, cached_at: Date.now() })); } catch (e) {}
  }

  async function fetchAll() {
    var cached = loadCache();
    if (cached) return cached;
    try {
      var headers = { 'apikey': ANON, 'Authorization': 'Bearer ' + ANON, 'Content-Type': 'application/json' };
      var [fresh, modules] = await Promise.all([
        fetch(RPC_FRESH,   { method: 'POST', headers: headers, body: '{}' }).then(function (r) { return r.json(); }),
        fetch(RPC_MODULES, { method: 'POST', headers: headers, body: '{}' }).then(function (r) { return r.json(); })
      ]);
      var data = { freshness: fresh, modules: modules };
      saveCache(data);
      return data;
    } catch (e) {
      console.warn('[tequio-cero-invencion] error:', e);
      return null;
    }
  }

  function renderFreshness(data) {
    if (!data || !data.fuentes) return '<div style="padding:24px;color:#94a3b8">No se pudo cargar.</div>';
    var fuentes = data.fuentes;
    var counts = { fresca: 0, reciente: 0, stale: 0, vieja: 0, sin_datos: 0 };
    Object.keys(fuentes).forEach(function (k) { var s = fuentes[k].status; if (counts[s] !== undefined) counts[s]++; });
    var total = Object.keys(fuentes).length;
    var saludables = counts.fresca + counts.reciente;
    var porcentaje = Math.round(saludables * 100 / total);

    var html = '<div style="padding:18px 20px;border-bottom:1px solid rgba(255,255,255,0.08);">';
    html += '<div style="font-size:22px;font-weight:800;color:#e8edf5;margin-bottom:8px">' + porcentaje + '% de las fuentes están frescas o recientes</div>';
    html += '<div style="font-size:12px;color:#94a3b8;line-height:1.6">' + counts.fresca + ' fresca' + (counts.fresca!==1?'s':'') + ' · ' + counts.reciente + ' reciente' + (counts.reciente!==1?'s':'');
    if (counts.stale > 0) html += ' · <strong style="color:#fbbf24">' + counts.stale + ' stale</strong>';
    if (counts.vieja > 0) html += ' · <strong style="color:#f87171">' + counts.vieja + ' vieja' + (counts.vieja!==1?'s':'') + '</strong>';
    html += '</div></div>';

    var keys = Object.keys(fuentes).sort(function (a, b) { return (fuentes[a].dias_atras || 0) - (fuentes[b].dias_atras || 0); });
    html += '<div style="padding:8px;">';
    keys.forEach(function (k) {
      var f = fuentes[k];
      var meta = LABELS[k] || { icon: '📊', titulo: k };
      var col = COLORS[f.status] || COLORS.sin_datos;
      html += '<div style="display:grid;grid-template-columns:40px 1fr auto;gap:12px;padding:12px 14px;border-radius:10px;background:' + col.bg + ';border:1px solid ' + col.border + ';margin:6px 4px;align-items:center">';
      html += '<div style="font-size:22px;text-align:center">' + meta.icon + '</div>';
      html += '<div><div style="font-weight:700;font-size:13px;color:#e8edf5;line-height:1.3">' + meta.titulo + '</div>';
      html += '<div style="font-size:11px;color:#94a3b8;margin-top:3px">' + (f.fuente_oficial||'') + '</div>';
      html += '<div style="font-size:11px;color:#64748b;margin-top:3px">📅 ' + (f.fecha||'sin dato') + ' · ' + (f.total!=null ? f.total.toLocaleString('es-MX') + ' registros' : '') + '</div></div>';
      html += '<div style="text-align:right"><div style="font-family:monospace;font-weight:800;font-size:10px;letter-spacing:0.5px;color:' + col.txt + '">' + col.label + '</div>';
      html += '<div style="font-size:10px;color:#64748b;margin-top:2px">' + fmtDias(f.dias_atras) + '</div></div></div>';
    });
    html += '</div>';
    return html;
  }

  function renderModules(data) {
    if (!data || !data.modulos) return '<div style="padding:24px;color:#94a3b8">No se pudo cargar.</div>';
    var modulos = data.modulos;
    var counts = { operativo: 0, inicial: 0, proximamente: 0, roadmap: 0 };
    modulos.forEach(function (m) { if (counts[m.estado] !== undefined) counts[m.estado]++; });

    var html = '<div style="padding:18px 20px;border-bottom:1px solid rgba(255,255,255,0.08);">';
    html += '<div style="font-size:18px;font-weight:800;color:#e8edf5;margin-bottom:8px">Honestidad sobre cada módulo</div>';
    html += '<div style="font-size:12px;color:#94a3b8;line-height:1.6">';
    html += '<span style="color:#4ade80">' + counts.operativo + ' operativos</span> · ';
    html += '<span style="color:#60a5fa">' + counts.inicial + ' iniciales</span> · ';
    html += '<span style="color:#fbbf24">' + counts.proximamente + ' próximamente</span> · ';
    html += '<span style="color:#94a3b8">' + counts.roadmap + ' en roadmap</span>';
    html += '</div></div>';

    var order = { operativo: 0, inicial: 1, proximamente: 2, roadmap: 3 };
    modulos = modulos.slice().sort(function (a, b) {
      return (order[a.estado]||9) - (order[b.estado]||9) || (b.filas||0) - (a.filas||0);
    });

    html += '<div style="padding:8px;">';
    modulos.forEach(function (m) {
      var meta = MODULE_LABELS[m.modulo] || { icon: '📦', titulo: m.modulo };
      var col = COLORS[m.estado] || COLORS.sin_datos;
      html += '<div style="display:grid;grid-template-columns:40px 1fr auto;gap:12px;padding:12px 14px;border-radius:10px;background:' + col.bg + ';border:1px solid ' + col.border + ';margin:6px 4px;align-items:center">';
      html += '<div style="font-size:22px;text-align:center">' + meta.icon + '</div>';
      html += '<div><div style="font-weight:700;font-size:13px;color:#e8edf5;line-height:1.3">' + meta.titulo + '</div>';
      html += '<div style="font-size:11px;color:#94a3b8;margin-top:3px;line-height:1.4">' + (m.descripcion_real||'') + '</div>';
      html += '<div style="font-size:11px;color:#64748b;margin-top:3px">📊 ' + (m.filas||0).toLocaleString('es-MX') + ' registros</div></div>';
      html += '<div style="text-align:right"><div style="font-family:monospace;font-weight:800;font-size:10px;letter-spacing:0.5px;color:' + col.txt + '">' + col.label + '</div></div></div>';
    });
    html += '</div>';
    return html;
  }

  function renderModal(data, activeTab) {
    var tabs = '<div style="display:flex;gap:6px;padding:14px 20px 0;background:rgba(255,255,255,0.02)">';
    tabs += '<button data-tab="fuentes" class="tcb-tab' + (activeTab==='fuentes' ? ' active' : '') + '">📊 Fuentes (' + (data && data.freshness && data.freshness.fuentes ? Object.keys(data.freshness.fuentes).length : 0) + ')</button>';
    tabs += '<button data-tab="modulos" class="tcb-tab' + (activeTab==='modulos' ? ' active' : '') + '">⚙️ Módulos (' + (data && data.modules && data.modules.modulos ? data.modules.modulos.length : 0) + ')</button>';
    tabs += '</div>';

    var body;
    if (activeTab === 'modulos') body = renderModules(data ? data.modules : null);
    else body = renderFreshness(data ? data.freshness : null);

    var footer = '<div style="padding:14px 20px;border-top:1px solid rgba(255,255,255,0.08);font-size:10px;color:#64748b;line-height:1.6">';
    footer += '<strong>🦎 Cero Invención:</strong> cada dato proviene de fuente oficial verificable. ';
    footer += 'Cuando una fuente tarda o un módulo aún no tiene datos, lo mostramos sin maquillaje. ';
    footer += 'No completamos lagunas con datos inventados.';
    footer += '</div>';

    return tabs + '<div style="max-height:60vh;overflow-y:auto">' + body + '</div>' + footer;
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
      '#tequio-freshness-modal{background:linear-gradient(135deg,#0e1525,#1a243d);border:1px solid rgba(255,255,255,0.12);border-radius:16px;max-width:580px;width:100%;max-height:92vh;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,0.6);position:relative}',
      '#tequio-freshness-close{position:absolute;top:14px;right:14px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);color:#94a3b8;width:32px;height:32px;border-radius:50%;cursor:pointer;font-size:16px;font-family:inherit;display:flex;align-items:center;justify-content:center;z-index:10}',
      '#tequio-freshness-close:hover{color:#e8edf5;background:rgba(255,255,255,0.12)}',
      '.tcb-tab{background:transparent;color:#94a3b8;border:1px solid rgba(255,255,255,0.08);border-radius:8px 8px 0 0;padding:8px 14px;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit;border-bottom:none;transition:color .12s}',
      '.tcb-tab:hover{color:#e8edf5}',
      '.tcb-tab.active{background:rgba(74,222,128,0.10);color:#4ade80;border-color:rgba(74,222,128,0.40)}',
      '@media (max-width:520px){#tequio-freshness-btn{bottom:auto;top:80px;left:auto;right:18px;padding:7px 11px;font-size:11px}}'
    ].join('\n');
    document.head.appendChild(s);
  }

  var _currentTab = 'fuentes';
  var _cachedData = null;

  function refreshModalBody() {
    var body = document.getElementById('tequio-freshness-body');
    if (!body) return;
    body.innerHTML = renderModal(_cachedData, _currentTab);
    // Re-bind tabs
    body.querySelectorAll('.tcb-tab').forEach(function (b) {
      b.addEventListener('click', function () {
        _currentTab = b.dataset.tab;
        refreshModalBody();
      });
    });
  }

  async function openModal() {
    ensureStyles();
    var overlay = document.getElementById('tequio-freshness-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'tequio-freshness-overlay';
      overlay.innerHTML = '<div id="tequio-freshness-modal"><button id="tequio-freshness-close" aria-label="Cerrar">×</button><div id="tequio-freshness-body"><div style="padding:40px;text-align:center;color:#94a3b8">Cargando…</div></div></div>';
      document.body.appendChild(overlay);
      overlay.addEventListener('click', function (e) { if (e.target === overlay) closeModal(); });
      document.getElementById('tequio-freshness-close').addEventListener('click', closeModal);
    }
    overlay.classList.add('show');
    _cachedData = await fetchAll();
    refreshModalBody();
  }

  function closeModal() {
    var o = document.getElementById('tequio-freshness-overlay');
    if (o) o.classList.remove('show');
  }

  function createBtn() {
    if (document.getElementById('tequio-freshness-btn')) return;
    var btn = document.createElement('button');
    btn.id = 'tequio-freshness-btn';
    btn.innerHTML = '<span class="dot"></span> Cero Invención · Estado';
    btn.title = 'Ver estado real de cada fuente y cada módulo';
    btn.addEventListener('click', openModal);
    document.body.appendChild(btn);
  }

  function init() { ensureStyles(); createBtn(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
