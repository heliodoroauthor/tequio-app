// Tequio Service Worker — Cache crítico para uso sin internet
const CACHE_NAME = 'tequio-v1';
const CRITICAL = [
  '/',
  '/panel/offline.html',
  '/panel/emergencia.html',
  '/panel/servicios.html',
  '/panel/generador.html',
  '/js/anti-bot.js',
  '/manifest.json'
];

// Install: pre-cache critical pages
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(CRITICAL.map(u => new Request(u, { cache: 'reload' }))))
      .catch(err => console.warn('SW cache fail:', err))
  );
  self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(names => Promise.all(
      names.filter(n => n !== CACHE_NAME).map(n => caches.delete(n))
    ))
  );
  self.clients.claim();
});

// Fetch: network-first for API, cache-first for static, offline fallback
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  
  // Skip non-GET
  if (event.request.method !== 'GET') return;
  
  // API calls: network only, fall back to nothing
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() => new Response(JSON.stringify({ error: 'offline', items: [] }), {
        headers: { 'Content-Type': 'application/json' }
      }))
    );
    return;
  }
  
  // Same-origin: cache-first with network update
  if (url.origin === location.origin) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        const network = fetch(event.request).then(response => {
          if (response.ok && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          }
          return response;
        }).catch(() => null);
        return cached || network || caches.match('/panel/offline.html');
      })
    );
  }
});
