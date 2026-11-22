// Caça Obra: guarda o app no celular para abrir rápido e funcionar com sinal fraco.
// - Telas e lista de obras: tenta a internet primeiro (dados novos); sem sinal, usa a cópia guardada.
// - Bibliotecas do mapa (cdnjs): usa a cópia guardada; só baixa na primeira vez.
const C = "cacaobra-v3";
const BASICOS = ["./", "index.html", "manifest.webmanifest", "icone.svg", "icone-192.png", "dados/obras.json"];

self.addEventListener("install", e => e.waitUntil(
  caches.open(C).then(c => c.addAll(BASICOS)).then(() => self.skipWaiting())));

self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== C).map(k => caches.delete(k)))).then(() => self.clients.claim())));

self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  const u = new URL(e.request.url);
  if (u.hostname === "cdnjs.cloudflare.com") {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request).then(resp => {
      const cp = resp.clone(); caches.open(C).then(c => c.put(e.request, cp)); return resp;
    })));
    return;
  }
  if (u.origin !== location.origin) return;
  e.respondWith(fetch(e.request).then(resp => {
    if (resp.ok) { const cp = resp.clone(); caches.open(C).then(c => c.put(e.request, cp)); }
    return resp;
  }).catch(() => caches.match(e.request, { ignoreSearch: true })));
});
