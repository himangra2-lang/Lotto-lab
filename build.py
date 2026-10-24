"""Build docs/ (the GitHub Pages site) from src/app.html.

Wraps the app in a full HTML document, adds the PWA manifest, service worker
registration and icons. Run after editing src/app.html:  python build.py
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
app = (ROOT / "src" / "app.html").read_text()

head = """<!doctype html>
<html lang="en-CA">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0E6B66">
<meta name="description" content="Randomness tests, expected value and smart picks for Canadian lotteries.">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icons/icon-192.png">
<link rel="apple-touch-icon" href="icons/icon-192.png">
</head>
<body>
"""
tail = """
<script>
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
</script>
</body>
</html>
"""
DOCS.mkdir(exist_ok=True)
(DOCS / "index.html").write_text(head + app + tail)

manifest = {
    "name": "Lotto Lab",
    "short_name": "Lotto Lab",
    "start_url": "./",
    "scope": "./",
    "display": "standalone",
    "background_color": "#EEF2F1",
    "theme_color": "#0E6B66",
    "icons": [
        {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}
(DOCS / "manifest.webmanifest").write_text(json.dumps(manifest, indent=2))

# Icon: three draw balls on teal.
(DOCS / "icons").mkdir(exist_ok=True)
for size in (192, 512):
    s = size / 512
    img = Image.new("RGB", (size, size), "#0E6B66")
    d = ImageDraw.Draw(img)
    for cx, cy, r, col in [(190, 210, 95, "#F2B233"), (340, 175, 62, "#F2B233"), (300, 345, 82, "#FFFFFF")]:
        d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill=col)
    img.save(DOCS / "icons" / f"icon-{size}.png")

sw = """// Lotto Lab service worker: network-first with offline fallback.
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
"""
(DOCS / "sw.js").write_text(sw)
(DOCS / ".nojekyll").write_text("")
print("built docs/")
