// Tequio · Hidratación YouTube En Vivo (extraído de index.html · Fase 2)
// ═════ Hidratación YouTube En Vivo · v2 con filtros + modal player ═════
(function hydrateYouTubeV2(){
  var SUPABASE_URL = 'https://mhsuihwjgtzxflesbnxv.supabase.co';
  var ANON = 'sb_publishable_grJAWIMoSr5b8YfaB7HVlw_OesNipBz';
  
  var state = {
    data: null,
    canal: '',
    busqueda: '',
    timer: null,
    autoRefresh: null
  };
  
  function fmtDate(iso){
    if (!iso) return '';
    var d = new Date(iso);
    var diff = Date.now() - d.getTime();
    var mins = Math.floor(diff / 60000);
    if (mins < 60) return 'hace ' + mins + ' min';
    var hrs = Math.floor(mins / 60);
    if (hrs < 24) return 'hace ' + hrs + 'h';
    var days = Math.floor(hrs / 24);
    if (days < 30) return 'hace ' + days + 'd';
    return d.toLocaleDateString('es-MX', {day:'numeric',month:'short'});
  }
  function esc(s){
    return String(s||'').replace(/[&<>"']/g, function(c){
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }
  function fmt(n){return Number(n||0).toLocaleString('es-MX');}
  
  // ── MODAL PLAYER ──
  window.ytAbrirPlayer = function(videoId, titulo, canal, isLive){
    var existing = document.getElementById('yt-player-modal');
    if(existing) existing.remove();
    var html = '<div id="yt-player-modal" onclick="ytCerrarPlayer(event)" style="position:fixed;inset:0;background:rgba(0,0,0,0.9);z-index:9999;display:flex;align-items:center;justify-content:center;padding:14px;backdrop-filter:blur(8px)">' +
      '<div onclick="event.stopPropagation()" style="max-width:1000px;width:100%;display:flex;flex-direction:column;gap:10px">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;color:#fff">' +
          '<div style="font-size:14px;line-height:1.4">' +
            '<div style="font-weight:700;margin-bottom:2px">' + esc(titulo) + '</div>' +
            '<div style="font-size:11px;opacity:0.7">' + esc(canal) + (isLive?' · <span style="color:#fb7185">🔴 EN VIVO</span>':'') + '</div>' +
          '</div>' +
          '<button onclick="ytCerrarPlayer()" style="background:rgba(255,255,255,0.15);border:none;border-radius:50%;width:40px;height:40px;color:#fff;font-size:20px;cursor:pointer;flex-shrink:0">✕</button>' +
        '</div>' +
        '<div style="position:relative;width:100%;aspect-ratio:16/9;background:#000;border-radius:14px;overflow:hidden">' +
          '<iframe src="https://www.youtube.com/embed/' + videoId + '?autoplay=1&rel=0" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen style="width:100%;height:100%;border:0"></iframe>' +
        '</div>' +
        '<div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">' +
          '<a href="https://www.youtube.com/watch?v=' + videoId + '" target="_blank" rel="noopener" style="background:rgba(255,255,255,0.10);color:#fff;text-decoration:none;padding:10px 16px;border-radius:8px;font-size:13px">📺 Ver en YouTube</a>' +
        '</div>' +
      '</div>' +
    '</div>';
    document.body.insertAdjacentHTML('beforeend', html);
    document.body.style.overflow = 'hidden';
  };
  window.ytCerrarPlayer = function(e){
    if(e && e.target.id !== 'yt-player-modal' && e.type === 'click') return;
    var m = document.getElementById('yt-player-modal');
    if(m) m.remove();
    document.body.style.overflow = '';
  };
  
  function renderCard(v, isLive){
    var thumb = v.thumbnail_url || '';
    var badge = isLive ? '<span class="live-badge">🔴 EN VIVO</span>' : '';
    var meta = isLive 
      ? (v.viewers ? fmt(v.viewers) + ' viendo ahora' : 'En transmisión')
      : fmtDate(v.published_at);
    var titEsc = esc(v.titulo||'').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    var canEsc = esc(v.channel_nombre||'').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    return '<div class="yt-card" onclick="ytAbrirPlayer(\'' + v.video_id + '\',\'' + titEsc + '\',\'' + canEsc + '\',' + (isLive?'true':'false') + ')">' +
      '<div class="thumb">' +
        (thumb ? '<img src="' + esc(thumb) + '" alt="" loading="lazy">' : '') +
        badge +
        '<span class="channel-chip">' + (v.channel_emoji||'') + ' ' + esc(v.channel_nombre) + '</span>' +
      '</div>' +
      '<div class="body">' +
        '<div class="title">' + esc(v.titulo) + '</div>' +
        '<div class="meta">' + meta + '</div>' +
      '</div>' +
    '</div>';
  }
  
  function filtrar(arr){
    return arr.filter(function(v){
      if(state.canal && v.channel_nombre !== state.canal) return false;
      if(state.busqueda){
        var q = state.busqueda.toLowerCase();
        return (v.titulo||'').toLowerCase().indexOf(q) >= 0;
      }
      return true;
    });
  }
  
  function aplicarFiltros(){
    if(!state.data) return;
    var live = filtrar(state.data.en_vivo || []);
    var ult = filtrar(state.data.ultimos_por_canal || []);
    var rec = filtrar(state.data.recientes || []);
    
    // LIVE
    var liveSec = document.getElementById('yt-live-section');
    var liveGrid = document.getElementById('yt-live-grid');
    var liveCount = document.getElementById('yt-live-count');
    if(live.length){
      if(liveSec) liveSec.style.display = '';
      if(liveGrid) liveGrid.innerHTML = live.map(function(v){return renderCard(v, true);}).join('');
      if(liveCount) liveCount.textContent = live.length + ' transmisión' + (live.length !== 1 ? 'es' : '');
    } else {
      if(liveSec) liveSec.style.display = 'none';
    }
    
    // Por canal
    var canSec = document.getElementById('yt-canales-section');
    var canGrid = document.getElementById('yt-canales-grid');
    if(ult.length){
      if(canSec) canSec.style.display = '';
      if(canGrid) canGrid.innerHTML = ult.map(function(v){return renderCard(v, false);}).join('');
    } else {
      if(canSec) canSec.style.display = 'none';
    }
    
    // Recientes
    var recSec = document.getElementById('yt-recientes-section');
    var recGrid = document.getElementById('yt-recientes-grid');
    if(rec.length){
      if(recSec) recSec.style.display = '';
      if(recGrid) recGrid.innerHTML = rec.map(function(v){return renderCard(v, false);}).join('');
    } else {
      if(recSec) recSec.style.display = 'none';
    }
    
    // Si todo vacío
    if(!live.length && !ult.length && !rec.length){
      if(canSec) canSec.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text3)">📭 Sin resultados con los filtros actuales.</div>';
      if(canSec) canSec.style.display = '';
    }
  }
  
  function setStats(d){
    var setText = function(id, v){var el=document.getElementById(id); if(el) el.textContent=v;};
    setText('yt-stat-live', fmt((d.en_vivo||[]).length));
    setText('yt-stat-canales', fmt((d.ultimos_por_canal||[]).length));
    setText('yt-stat-recientes', fmt((d.recientes||[]).length));
    if(d.last_sync){
      var dt = new Date(d.last_sync);
      var diff = Math.floor((Date.now() - dt.getTime()) / 60000);
      setText('yt-stat-sync', diff < 60 ? 'hace ' + diff + ' min' : dt.toLocaleTimeString('es-MX',{hour:'2-digit',minute:'2-digit'}));
    }
    // Badge sidebar
    var navBadge = document.getElementById('nav-live-badge');
    if(navBadge) navBadge.style.display = (d.en_vivo||[]).length ? '' : 'none';
  }
  
  window.ytSetCanal = function(el, c){
    state.canal = c || '';
    el.parentElement.querySelectorAll('.estado-chip').forEach(function(x){x.classList.remove('active');});
    el.classList.add('active');
    aplicarFiltros();
  };
  
  window.ytFiltrarDeb = function(){
    clearTimeout(state.timer);
    state.timer = setTimeout(function(){
      state.busqueda = document.getElementById('yt-search').value.trim();
      aplicarFiltros();
    }, 250);
  };
  
  function tryFetch(){
    fetch(SUPABASE_URL + '/rest/v1/rpc/dashboard_youtube_live', {
      method: 'POST',
      headers: {'Content-Type':'application/json', 'apikey':ANON, 'Authorization':'Bearer '+ANON},
      body: '{}'
    })
    .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
    .then(function(d){
      state.data = d;
      setStats(d);
      aplicarFiltros();
    })
    .catch(function(){});
  }
  
  window.ytRefresh = function(){
    var btn = document.getElementById('yt-refresh-btn');
    if(btn){ btn.innerHTML = '<span>⏳</span> Cargando...'; btn.disabled = true; }
    tryFetch();
    setTimeout(function(){
      if(btn){ btn.innerHTML = '<span>🔄</span> Actualizar'; btn.disabled = false; }
    }, 1500);
  };
  
  // Auto-refresh cada 60s
  function startAutoRefresh(){
    if(state.autoRefresh) clearInterval(state.autoRefresh);
    state.autoRefresh = setInterval(tryFetch, 60000);
  }
  
  // Boot
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function(){ tryFetch(); startAutoRefresh(); });
  else { tryFetch(); startAutoRefresh(); }
})();
