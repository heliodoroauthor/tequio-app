// tequio-kpis.js · Hydrata elementos data-kpi="X" desde RPC dashboard_kpis_globales
// Uso: <script src="/js/tequio-kpis.js" defer></script>
// El IIFE busca elementos [data-kpi="X"] en el DOM y actualiza textContent
// con el valor en vivo de Supabase. Si la RPC falla, queda el valor hardcoded.
// 🦎 Cero Invención · Tequio · 2026
(function hydrateKpisGlobales(){
  var SUPABASE_URL = 'https://mhsuihwjgtzxflesbnxv.supabase.co';
  var ANON = 'sb_publishable_grJAWIMoSr5b8YfaB7HVlw_OesNipBz';
  function fmtNum(n){ if (n == null) return '—'; return Number(n).toLocaleString('es-MX'); }
  function applyKpis(k){
    if (!k) return;
    var map = {
      'leyes':              function(){ return fmtNum(k.leyes); },
      'leyes-estatal':      function(){ return fmtNum(k.leyes_estatal); },
      'leyes-municipal':    function(){ return fmtNum(k.leyes_municipal); },
      'leyes-federal':      function(){ return fmtNum(k.leyes_federal); },
      'leyes-calidad':      function(){ return fmtNum(k.leyes_con_contenido_calidad); },
      'contratos':          function(){ return fmtNum(k.contratos_publicos); },
      'inah-zonas':         function(){ return fmtNum(k.inah_zonas); },
      'inah-museos':        function(){ return fmtNum(k.inah_museos); },
      'inah-patrimonio':    function(){ return (k.inah_zonas||0) + '+' + (k.inah_museos||0); },
      'sre-embajadas':      function(){ return fmtNum(k.sre_embajadas); },
      'sre-consulados':     function(){ return fmtNum(k.sre_consulados); },
      'inali-lenguas':      function(){ return fmtNum(k.inali_lenguas); },
      'unesco-patrimonio':  function(){ return fmtNum(k.unesco_patrimonio); },
      'diputados':          function(){ return fmtNum(k.politicos_diputados); },
      'senadores':          function(){ return fmtNum(k.politicos_senadores); },
      'actores-poder':      function(){ return fmtNum(k.actores_poder); },
      'vinculos-poder':     function(){ return fmtNum(k.vinculos_poder); },
      'sat-69':             function(){ return fmtNum(k.sat_69); },
      'sat-69b':            function(){ return fmtNum(k.sat_69b); },
      'sat-total':          function(){ return fmtNum((k.sat_69||0) + (k.sat_69b||0)); },
      'gasolina':           function(){ return fmtNum(k.gasolina_estaciones); },
      'profeco-precios':    function(){ return fmtNum(k.profeco_precios); },
      'municipios':         function(){ return fmtNum(k.municipios); },
      'votos-individuales': function(){ return fmtNum(k.votos_individuales); },
      'contratos-ultimo-corte': function(){ return k.contratos_ultimo_corte || '—'; },
      'pef-total': function(){
        if (!k.pef || !k.pef.monto_total_mxn) return '—';
        return '$' + (k.pef.monto_total_mxn / 1e12).toFixed(2) + ' bn';
      },
      // Nuevos KPIs post-mega-sweep (jun 2026)
      'leyes-federal-estatal':       function(){ return fmtNum(k.leyes_federal_estatal); },
      'leyes-municipal-reglamento':  function(){ return fmtNum(k.leyes_municipal_reglamento); },
      'leyes-municipal-gaceta':      function(){ return fmtNum(k.leyes_municipal_gaceta); },
      'chunks-total':                function(){ return fmtNum(k.chunks_total); },
      'chunks-embedded':             function(){ return fmtNum(k.chunks_embedded); },
      'chunks-federal':              function(){ return fmtNum(k.chunks_federal); },
      'chunks-estatal':              function(){ return fmtNum(k.chunks_estatal); },
      'chunks-municipal':            function(){ return fmtNum(k.chunks_municipal); }
    };
    Object.keys(map).forEach(function(key){
      var els = document.querySelectorAll('[data-kpi="' + key + '"]');
      if (!els.length) return;
      var v = map[key]();
      els.forEach(function(el){ if (v && v !== '—') el.textContent = v; });
    });
    document.querySelectorAll('[data-kpi]').forEach(function(el){
      if (!el.title || el.title.indexOf('Tequio') < 0) {
        el.title = '🦎 Cero Invención · count en vivo · ' + new Date(k.actualizado).toLocaleString('es-MX');
      }
    });
  }
  function tryFetch(){
    fetch(SUPABASE_URL + '/rest/v1/rpc/dashboard_kpis_globales', {
      method: 'POST',
      headers: { 'Content-Type':'application/json', 'apikey':ANON, 'Authorization':'Bearer '+ANON },
      body: '{}'
    })
    .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
    .then(function(k){
      applyKpis(k);
      window.__tequioKpis = k;
    })
    .catch(function(){ /* silencioso · queda valor hardcoded como fallback */ });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', tryFetch);
  else setTimeout(tryFetch, 0);
})();
