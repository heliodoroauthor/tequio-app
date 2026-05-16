/* Tequio Anti-Bot — Nivel 2 (5 capas) + Nivel 3 (11 capas)
 * 100% client-side. Cero datos personales — solo hashes locales.
 */
(function () {
  'use strict';

  // ============================================================
  // CAPA: Device Fingerprint (FingerprintJS-style, sin libreria)
  // ============================================================
  async function deviceFingerprint() {
    const parts = [
      navigator.userAgent,
      navigator.language,
      navigator.languages?.join(',') || '',
      navigator.platform || '',
      navigator.hardwareConcurrency || '',
      navigator.deviceMemory || '',
      screen.width + 'x' + screen.height,
      screen.colorDepth,
      new Date().getTimezoneOffset(),
      navigator.maxTouchPoints || '',
      canvasFingerprint(),
      webglFingerprint()
    ].join('|');
    return await sha256(parts);
  }

  function canvasFingerprint() {
    try {
      const c = document.createElement('canvas');
      c.width = 220; c.height = 30;
      const ctx = c.getContext('2d');
      ctx.textBaseline = 'top';
      ctx.font = '14px Arial';
      ctx.fillStyle = '#f60';
      ctx.fillRect(125, 1, 62, 20);
      ctx.fillStyle = '#069';
      ctx.fillText('Tequio.app MX', 2, 15);
      ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
      ctx.fillText('Tequio.app MX', 4, 17);
      return c.toDataURL();
    } catch (_) { return 'nocanvas'; }
  }

  function webglFingerprint() {
    try {
      const c = document.createElement('canvas');
      const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
      if (!gl) return 'nowebgl';
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      return (ext && gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) || 'webgl';
    } catch (_) { return 'nowebgl'; }
  }

  async function sha256(s) {
    const buf = new TextEncoder().encode(s);
    const hash = await crypto.subtle.digest('SHA-256', buf);
    return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('');
  }

  // ============================================================
  // CAPA: Tiempo en pagina
  // ============================================================
  const pageLoadTime = Date.now();
  function tiempoEnPagina() { return Date.now() - pageLoadTime; }

  // ============================================================
  // CAPA: Scroll detection
  // ============================================================
  let scrollDetectado = false;
  window.addEventListener('scroll', () => { scrollDetectado = true; }, { passive: true, once: true });
  let pointerDetectado = false;
  ['mousemove', 'touchstart', 'pointerdown'].forEach(ev => {
    window.addEventListener(ev, () => { pointerDetectado = true; }, { passive: true, once: true });
  });

  // ============================================================
  // CAPA: Honeypot - inyectar campo invisible
  // ============================================================
  function inyectarHoneypot(formOrContainer) {
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.name = 'website_url';
    inp.tabIndex = -1;
    inp.autocomplete = 'off';
    inp.setAttribute('aria-hidden', 'true');
    inp.style.cssText = 'position:absolute;left:-9999px;top:-9999px;opacity:0;height:0;width:0;';
    formOrContainer.appendChild(inp);
    return () => inp.value;
  }

  // ============================================================
  // CAPA: IP hash (server-side)
  // ============================================================
  let _ipHash = null;
  async function ipHash() {
    if (_ipHash) return _ipHash;
    try {
      const r = await fetch('/api/data?vista=ip_hash');
      const j = await r.json();
      _ipHash = j.ip_hash;
    } catch (_) { _ipHash = null; }
    return _ipHash;
  }

  // ============================================================
  // CAPA: Geolocalizacion (opcional, no bloquea)
  // ============================================================
  async function geoMexico() {
    if (!navigator.geolocation) return { ok: false, motivo: 'no_disponible' };
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve({ ok: false, motivo: 'timeout' }), 5000);
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          clearTimeout(timer);
          const { latitude, longitude } = pos.coords;
          const enMexico = latitude > 14 && latitude < 33 && longitude > -118 && longitude < -86;
          resolve({ ok: true, en_mexico: enMexico, lat: latitude.toFixed(2), lng: longitude.toFixed(2) });
        },
        () => { clearTimeout(timer); resolve({ ok: false, motivo: 'denegado' }); },
        { timeout: 5000, maximumAge: 60000 }
      );
    });
  }

  // ============================================================
  // CAPA: Timezone + idioma
  // ============================================================
  function timezoneInfo() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) { return ''; }
  }
  function idiomaInfo() { return (navigator.language || '').toLowerCase(); }

  // ============================================================
  // CAPA: Battery (opcional)
  // ============================================================
  async function batteryInfo() {
    if (!navigator.getBattery) return null;
    try {
      const b = await navigator.getBattery();
      return { level: b.level, charging: b.charging };
    } catch (_) { return null; }
  }

  // ============================================================
  // API: contexto completo Nivel 2
  // ============================================================
  async function contextoNivel2(opts) {
    opts = opts || {};
    const [deviceHash, ipH] = await Promise.all([deviceFingerprint(), ipHash()]);
    return {
      device_hash: deviceHash,
      ip_hash: ipH,
      tiempo_en_pagina_ms: tiempoEnPagina(),
      scroll_detectado: scrollDetectado,
      pointer_detectado: pointerDetectado,
      honeypot: opts.honeypot ? opts.honeypot() : '',
      timezone: timezoneInfo(),
      idioma: idiomaInfo()
    };
  }

  // API: contexto completo Nivel 3 (incluye geo + battery)
  async function contextoNivel3(opts) {
    opts = opts || {};
    const base = await contextoNivel2(opts);
    const [geo, batt] = await Promise.all([
      opts.skipGeo ? Promise.resolve({ ok: false, motivo: 'skipped' }) : geoMexico(),
      batteryInfo()
    ]);
    return { ...base, geo, battery: batt };
  }

  // ============================================================
  // Validacion minima Nivel 3
  // ============================================================
  function validarNivel3(ctx, opts) {
    opts = opts || {};
    const minTiempo = opts.minTiempoMs || 30000;
    if (ctx.honeypot) return { ok: false, motivo: 'honeypot' };
    if (ctx.tiempo_en_pagina_ms < minTiempo) return { ok: false, motivo: 'tiempo', falta_seg: Math.ceil((minTiempo - ctx.tiempo_en_pagina_ms) / 1000) };
    if (!ctx.scroll_detectado) return { ok: false, motivo: 'sin_scroll' };
    if (!ctx.pointer_detectado) return { ok: false, motivo: 'sin_pointer' };
    if (!ctx.device_hash) return { ok: false, motivo: 'sin_device' };
    if (ctx.idioma && !ctx.idioma.startsWith('es')) return { ok: false, motivo: 'idioma_no_es' };
    if (ctx.timezone && !/America\/|Mexico|Tijuana|Monterrey|Cancun/.test(ctx.timezone)) {
      return { ok: false, motivo: 'timezone_no_mx' };
    }
    return { ok: true };
  }

  function validarNivel2(ctx, opts) {
    opts = opts || {};
    const minTiempo = opts.minTiempoMs || 5000;
    if (ctx.honeypot) return { ok: false, motivo: 'honeypot' };
    if (ctx.tiempo_en_pagina_ms < minTiempo) return { ok: false, motivo: 'tiempo', falta_seg: Math.ceil((minTiempo - ctx.tiempo_en_pagina_ms) / 1000) };
    if (!ctx.device_hash) return { ok: false, motivo: 'sin_device' };
    return { ok: true };
  }

  // Exponer
  window.AntiBot = {
    deviceFingerprint,
    ipHash,
    geoMexico,
    inyectarHoneypot,
    tiempoEnPagina,
    contextoNivel2,
    contextoNivel3,
    validarNivel2,
    validarNivel3,
    sha256
  };
})();
