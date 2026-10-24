// Lotto Lab service worker: network-first with offline fallback.
const SHELL = "lottolab-shell-v1";
const FILES = ["./", "index.html", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];
self.addEventListener("install", e => e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).then(() => self.skipWaiting())));
self.addEventListener("activate", e => e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== SHELL && k !== "lottolab-data").map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  // Network-first so new draws and app updates show up; cache keeps it working offline.
  const bucket = url.pathname.includes("/data/") ? "lottolab-data" : SHELL;
  e.respondWith(fetch(e.request).then(r => { if (r.ok && url.origin === location.origin) { const copy = r.clone(); caches.open(bucket).then(c => c.put(e.request, copy)); } return r; })
    .catch(() => caches.match(e.request)));
});
