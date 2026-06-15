// ============================================================
// Tequio Service Worker · v2.0.0
// Fase 3.B — Caché offline con estrategias híbridas
// ============================================================
//
//  • HTML (navegación):    network-first  → caché fallback
//  • Imágenes/CSS/fuentes: cache-first    → red fallback
//  • /api/data?vista=*:    stale-while-revalidate (10 min)
//  • /api/ai-proxy:        network-only   (cada consulta es única)
//  • CDNs (Leaflet, fonts): cache-first   (TTL largo)
//
// ============================================================
const VERSION       = 'tequio-v2.0.3-onbrich-14jun';
const CACHE_SHELL   = `${VERSION}-shell`;
const CACHE_ASSETS  = `${VERSION}-assets`;
const CACHE_DATA    = `${VERSION}-data`;

// Lo mínimo para que la app cargue offline desde la primera visita
const PRECACHE_SHELL = ['/', '/manifest.json'];

const PRECACHE_ASSETS = [
  '/icon-192.png',
  '/icon-512.png',
  '/mask192.png',
  '/mask512.png',
  '/appletch.png',
  '/fav32.png',
];

// ──────────────────────────────────────────────────────────
// INSTALL — pre-cache best-effort
// ──────────────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  console.log('[Tequio SW]', VERSION, 'installing');
  event.waitUntil((async () => {
    const shell  = await caches.open(CACHE_SHELL);
    const assets = await caches.open(CACHE_ASSETS);
    // allSettled: si falla un archivo, no rompemos el SW
    await Promise.allSettled([
      ...PRECACHE_SHELL.map(url =>
        shell.add(url).catch(err => console.warn('[SW] precache shell falló:', url, err))
      ),
      ...PRECACHE_ASSETS.map(url =>
        assets.add(url).catch(err => console.warn('[SW] precache asset falló:', url, err))
      ),
    ]);
    await self.skipWaiting();
  })());
});

// ──────────────────────────────────────────────────────────
// ACTIVATE — limpieza de versiones viejas
// ──────────────────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  console.log('[Tequio SW]', VERSION, 'activating');
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys
        .filter(k => k.startsWith('tequio-') && !k.startsWith(VERSION))
        .map(k => {
          console.log('[Tequio SW] eliminando caché vieja:', k);
          return caches.delete(k);
        })
    );
    await self.clients.claim();
  })());
});

// ──────────────────────────────────────────────────────────
// FETCH — router de estrategias
// ──────────────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;                  // solo GET
  const url = new URL(req.url);

  // 1. AI proxy → nunca cachear
  if (url.pathname.startsWith('/api/ai-proxy')) {
    return;  // browser lo maneja directo
  }

  // 2. /api/data → stale-while-revalidate
  if (url.pathname.startsWith('/api/data')) {
    event.respondWith(staleWhileRevalidate(req, CACHE_DATA));
    return;
  }

  // 3. Same-origin
  if (url.origin === location.origin) {
    // 3a. Navegación HTML → network-first
    const acceptsHtml = req.mode === 'navigate' ||
                        (req.headers.get('accept') || '').includes('text/html');
    if (acceptsHtml) {
      event.respondWith(networkFirst(req, CACHE_SHELL));
      return;
    }
    // 3b. Otros assets → cache-first
    event.respondWith(cacheFirst(req, CACHE_ASSETS));
    return;
  }

  // 4. CDNs conocidos → cache-first
  const cdnHosts = [
    'cdnjs.cloudflare.com',
    'fonts.googleapis.com',
    'fonts.gstatic.com',
    'basemaps.cartocdn.com',
    'unpkg.com',
  ];
  if (cdnHosts.some(h => url.host.endsWith(h))) {
    event.respondWith(cacheFirst(req, CACHE_ASSETS));
    return;
  }

  // 5. Resto → network-only (sin interceptar)
});

// ──────────────────────────────────────────────────────────
// ESTRATEGIAS
// ──────────────────────────────────────────────────────────

// Cache-first: caché si existe, sino red
async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  if (cached) return cached;
  try {
    const fresh = await fetch(req);
    if (fresh && (fresh.ok || fresh.type === 'opaque')) {
      cache.put(req, fresh.clone()).catch(() => {});
    }
    return fresh;
  } catch (err) {
    return new Response('', { status: 503, statusText: 'Offline' });
  }
}

// Network-first: red primero, caché si red falla
async function networkFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const fresh = await fetch(req);
    if (fresh && fresh.ok) {
      cache.put(req, fresh.clone()).catch(() => {});
    }
    return fresh;
  } catch (err) {
    const cached = await cache.match(req);
    if (cached) return cached;
    // Último recurso: la home cacheada
    const home = await cache.match('/');
    if (home) return home;
    return new Response(
      '<h1 style="font-family:sans-serif;padding:40px;text-align:center">📡 Tequio sin conexión</h1>' +
      '<p style="text-align:center;color:#666">Esta sección no está cacheada. Vuelve a conectarte e intenta de nuevo.</p>',
      { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
  }
}

// Stale-while-revalidate: caché al instante, refresca en bg
async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  const networkPromise = fetch(req).then(fresh => {
    if (fresh && fresh.ok) {
      cache.put(req, fresh.clone()).catch(() => {});
    }
    return fresh;
  }).catch(() => null);
  if (cached) {
    networkPromise;  // fire-and-forget
    return cached;
  }
  const fresh = await networkPromise;
  return fresh || new Response(
    JSON.stringify({ error: 'offline', mensaje: 'Datos no disponibles sin conexión' }),
    { status: 503, headers: { 'Content-Type': 'application/json' } }
  );
}

// ──────────────────────────────────────────────────────────
// MESSAGING — comunicación con la página
// ──────────────────────────────────────────────────────────
self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (event.data?.type === 'CLEAR_DATA_CACHE') {
    caches.delete(CACHE_DATA).then(() => {
      event.source?.postMessage({ type: 'CACHE_CLEARED' });
    });
  }
});

// ──────────────────────────────────────────────────────────
// PUSH (placeholder · se activa en Fase 3.D)
// ──────────────────────────────────────────────────────────
self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: 'Tequio', body: event.data ? event.data.text() : 'Nueva actualización' };
  }
  const title = data.title || 'Tequio';
  const options = {
    body: data.body || 'Tienes una nueva alerta cívica',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    tag: data.tag || 'tequio-general',
    data: { url: data.url || '/' },
    requireInteraction: data.urgent === true,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(location.origin) && 'focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
