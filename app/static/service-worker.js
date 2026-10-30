// HVAccRM Service Worker
// 策略：
//   - 静态资源（/static/*）→ cache-first（应用壳：CSS / HTMX / 图标）
//   - 页面 / HTMX 片段 / 图片 → network-first（业务数据实时、多角色权限，绝不缓存整页 HTML）
//   - 离线时导航请求回退到离线提示页
const CACHE = "hvaccrm-shell-v55";
const SHELL = [
  "/static/app.css",
  "/static/htmx.min.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/offline.html"
];

self.addEventListener("install", function (e) {
  e.waitUntil(
    caches.open(CACHE)
      .then(function (c) { return c.addAll(SHELL); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (e) {
  const req = e.request;
  if (req.method !== "GET") return;              // 不拦截 POST
  const url = new URL(req.url);

  // 静态资源：cache-first（命中即返回，否则请求并缓存）
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(
      caches.match(req).then(function (hit) {
        if (hit) return hit;
        return fetch(req).then(function (res) {
          if (res && res.status === 200) {
            const copy = res.clone();
            caches.open(CACHE).then(function (c) { c.put(req, copy); });
          }
          return res;
        });
      })
    );
    return;
  }

  // 业务页面/片段/图片：network-first
  e.respondWith(
    fetch(req).then(function (res) { return res; }).catch(function () {
      if (req.mode === "navigate") {
        return caches.match("/offline.html");
      }
      return new Response("", { status: 504, statusText: "offline" });
    })
  );
});
