// Service worker. IMPORTANT: it must never cache HTML navigations or API
// responses — those have to be fetched fresh every time or the app shows stale
// scores. We only cache-first for immutable, content-hashed build assets and
// the app icon so the shell loads instantly when installed to the home screen.
const STATIC_CACHE = "stockmon-v1";
const PRECACHE = ["/manifest.webmanifest", "/icons/icon.svg"];
const KEEP = [STATIC_CACHE];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((c) => c.addAll(PRECACHE))
      .catch(() => {}),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => !KEEP.includes(k)).map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never cache navigations or API calls — always hit the network for fresh data.
  if (request.mode === "navigate" || url.pathname.startsWith("/api/")) return;

  // Cache-first for hashed build assets and static icons.
  const cacheable =
    url.pathname.startsWith("/_next/static/") ||
    url.pathname.startsWith("/icons/") ||
    url.pathname === "/manifest.webmanifest";
  if (!cacheable) return;

  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ||
        fetch(request).then((res) => {
          const copy = res.clone();
          caches
            .open(STATIC_CACHE)
            .then((c) => c.put(request, copy))
            .catch(() => {});
          return res;
        }),
    ),
  );
});
