/* Offline shell for the installed application.

   Only the static shell is cached. Requests to /v1 are never cached and never
   served from a cache: household state must always come from the gateway, and a
   stale reading shown as current is exactly what the state model forbids;
   see docs/architecture/overview.md. */

const CACHE = "homeflow-shell-v4";
const SHELL = [
  ".",
  "index.html",
  "app.css",
  "manifest.webmanifest",
  "js/app.js",
  "js/api.js",
  "js/live.js",
  "js/render.js",
  "js/strings.js",
  "icons/icon-192.png",
  "icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/v1") || url.pathname === "/healthz") return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((hit) => hit ?? caches.match("index.html"))),
  );
});
