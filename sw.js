// Forecast Lagoon — service worker v1.0 (sito v3.17): riceve le allerte vento (Web Push) e le mostra.
// Nessuna cache: il sito continua a caricarsi sempre dalla rete come prima.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = { corpo: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.titolo || 'Forecast Lagoon', {
    body: d.corpo || '', tag: d.tag || 'forecast-lagoon', renotify: true,
    icon: 'assets/icona_180.png', badge: 'assets/icona_180.png', data: { url: d.url || './' }
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || './';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
    for (const c of cs) if ('focus' in c) return c.focus();
    return self.clients.openWindow(url);
  }));
});
