/* Only public static UI assets enter CacheStorage. No private/offline lessons. */
'use strict';
const CACHE_PREFIX = 'youth-public-shell-';
const CACHE = CACHE_PREFIX + 'v5';
const OFFLINE = '/static/pwa/offline.html';
const CORE = [OFFLINE, '/static/pwa/icon-clear-192.png', '/static/pwa/icon-clear-512.png'];
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

self.addEventListener('message', event => {
  if (event.data?.type === 'push-capability') event.ports[0]?.postMessage({push:1});
});

self.addEventListener('push', event => {
  let notice = {};
  try { notice = event.data?.json() || {}; } catch (_) { /* Keep an actionable fallback. */ }
  const title = typeof notice.title === 'string' && notice.title.trim() ? notice.title.slice(0,150) : 'Youth Learn';
  const body = typeof notice.body === 'string' && notice.body.trim() ? notice.body.slice(0,500) : 'Open Youth Learn to view your updates.';
  event.waitUntil(self.registration.showNotification(title, {
    body, icon:'/static/pwa/icon-clear-192.png',
    badge:'/static/pwa/icon-clear-192.png', tag:'youth-centre-updates',
    data:{url:'/notifications'}, renotify:false
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({type:'window',includeUncontrolled:true});
    const existing = windows.find(client => {
      const url = new URL(client.url);
      return url.origin === self.location.origin && url.pathname === '/notifications';
    });
    if (existing) return existing.focus();
    // Leave another window's unsaved quiz/form intact.
    return self.clients.openWindow('/notifications');
  })());
});

self.addEventListener('pushsubscriptionchange', event => {
  event.waitUntil((async () => {
    const response = await fetch('/device-notifications', {credentials:'same-origin',cache:'no-store'});
    if (!response.ok) return;
    const config = await response.json();
    if (!config.enabled || Notification.permission !== 'granted') return;
    const key = config.publicKey.replace(/-/g,'+').replace(/_/g,'/');
    const bytes = Uint8Array.from(atob(key + '='.repeat((4-key.length%4)%4)), c => c.charCodeAt(0));
    const subscription = event.newSubscription || await self.registration.pushManager.subscribe({
      userVisibleOnly:true, applicationServerKey:bytes
    });
    await fetch('/device-notifications', {method:'POST',credentials:'same-origin',cache:'no-store',
      headers:{'Content-Type':'application/json','X-CSRFToken':config.csrfToken},
      body:JSON.stringify(subscription.toJSON())});
  })().catch(() => {}));
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
