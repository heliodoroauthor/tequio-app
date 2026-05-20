// ============================================================
// Tequio Service Worker · v1.0.0
// Fase 3.A — Solo registro (sin caché agresivo todavía)
// La caché completa viene en Fase 3.B
// ============================================================
const VERSION = 'tequio-v1.0.0';
const PRECACHE = [
  '/',
  '/manifest.json',
  '/icon-192.png',
  '/icon-512.png',
];
// Install: pre-cachea solo lo esencial
self.addEventListener('install', (event) => {
  console.log('[Tequio SW] Installing', VERSION);
  event.waitUntil(
    caches.open(VERSION)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
      .catch((err) => console.warn('[Tequio SW] Precache failed:', err))
  );
});
// Activate: limpia versiones viejas
self.addEventListener('activate', (event) => {
  console.log('[Tequio SW] Activating', VERSION);
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== VERSION && k.startsWith('tequio-')).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});
// Fetch: estrategia "network-first" con fallback a caché
// En 3.A no cacheamos requests dinámicos — eso viene en 3.B
self.addEventListener('fetch', (event) => {
  const req = event.request;
  // Solo GET, solo same-origin, sin /api/
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  event.respondWith(
    fetch(req)
      .catch(() => caches.match(req).then((cached) => cached || caches.match('/')))
  );
});
// Mensajería con la página (para 3.C — push subscribe)
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
// Push handler — placeholder, se activa en Fase 3.D
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
