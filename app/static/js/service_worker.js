/* Only public static UI assets enter CacheStorage. No private/offline lessons. */
'use strict';
const CACHE_PREFIX = 'youth-public-shell-';
const CACHE = CACHE_PREFIX + 'v1';
const OFFLINE = '/static/pwa/offline.html';
const CORE = [OFFLINE, '/static/pwa/icon-192.png', '/static/pwa/icon-512.png'];
const MAX_ASSETS = 80;

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(CORE)));
  // Let an existing worker finish its open sessions. Never force a quiz reload.
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys
    .filter(key => key.startsWith(CACHE_PREFIX) && key !== CACHE)
    .map(key => caches.delete(key)))));
});

function publicAsset(request, url) {
  return url.origin === self.location.origin && url.pathname.startsWith('/static/') &&
    /\.(?:css|js|woff2?|ttf|png|jpe?g|svg|webp|ico)$/i.test(url.pathname);
}

async function trim(cache) {
  const keys = await cache.keys();
  const optional = keys.filter(request => !CORE.includes(new URL(request.url).pathname));
  const excess = Math.max(0, keys.length - MAX_ASSETS);
  await Promise.all(optional.slice(0, excess).map(request => cache.delete(request)));
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request, {cache:'no-store'}).catch(async () => {
      const fallback = await caches.match(OFFLINE);
      return fallback || Response.error();
    }));
    return;
  }
  if (!publicAsset(request, url) || request.headers.has('range')) return;
  const network = fetch(request);
  event.waitUntil(network.then(async response => {
    if (!response.ok || response.type !== 'basic' || response.redirected ||
        /private|no-store/i.test(response.headers.get('cache-control') || '')) return;
    const copy = response.clone();
    const cache = await caches.open(CACHE);
    await cache.put(request, copy);
    await trim(cache);
  }).catch(() => {}));
  event.respondWith(network.catch(async () => {
    const cached = await caches.match(request);
    return cached || Response.error();
  }));
});
