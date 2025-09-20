const CACHE_NAME = 'ai-camera-cache-v1';
const FILES_TO_CACHE = [
  '/',
  '/index.html',
  '/history.html',
  '/manifest.webmanifest'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(FILES_TO_CACHE))
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
    ))
  );
});

self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request).then(response => response || fetch(event.request))
  );
});

self.addEventListener('push', event => {
  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data = {};
    }
  }
  const title = data.title || 'Notification';
  const body = data.body || '';
  event.waitUntil(
    self.registration.showNotification(title, { body })
  );
});

