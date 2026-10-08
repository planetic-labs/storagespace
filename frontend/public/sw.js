const CACHE_NAME = 'storagespace-offline-v1'

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.add('/offline.html')))
  self.skipWaiting()
})

self.addEventListener('activate', event => {
  event.waitUntil(Promise.all([
    caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('storagespace-offline-') && key !== CACHE_NAME).map(key => caches.delete(key)))),
    self.clients.claim(),
  ]))
})

self.addEventListener('fetch', event => {
  if (event.request.mode !== 'navigate') return
  event.respondWith(fetch(event.request).catch(async () => {
    const cached = await caches.match('/offline.html')
    return cached || Response.error()
  }))
})
