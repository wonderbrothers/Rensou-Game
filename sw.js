/* 連想ゲーム Service Worker
   - アプリ本体（HTML/CSS/JS/画像）をキャッシュしてオフラインでも起動できるようにする
   - api/ は「ネットワーク優先・失敗時キャッシュ」で常に最新を見せつつ、オフラインでも既読を閲覧可能にする
   - 新しい版が出たら自動で切り替えず、アプリ側のトーストからユーザーが更新を選ぶ
   CACHE_VERSION は build_static.py がビルドごとに自動で書き換える */
const CACHE_VERSION = "21ba443798e7";
const SHELL_CACHE = `rensou-shell-${CACHE_VERSION}`;
const DATA_CACHE = `rensou-data-${CACHE_VERSION}`;

const SHELL_ASSETS = [
  "./",
  "./index.html",
  "./app.js",
  "./style.css",
  "./manifest.json",
  "./favicon.ico",
  "./images/logo.svg",
  "./images/logo-dark.svg",
  "./images/hero.webp",
  "./images/analyst.png",
  "./images/icon-192.png",
  "./images/icon-512.png",
  "./fonts/local.css",
];

self.addEventListener("install", ev => {
  // waitUntil 内で失敗すると更新が止まるため、個別に失敗を許容する
  ev.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    await Promise.all(SHELL_ASSETS.map(u => cache.add(u).catch(() => null)));
    // skipWaiting はしない（ユーザーがトーストで更新を選んだときだけ切り替える）
  })());
});

self.addEventListener("activate", ev => {
  ev.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys
      .filter(k => k !== SHELL_CACHE && k !== DATA_CACHE)
      .map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", ev => {
  const req = ev.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // api/ … ネットワーク優先（最新の記事・株価を見せる）／オフライン時はキャッシュ
  if (url.pathname.includes("/api/")) {
    ev.respondWith((async () => {
      try {
        const res = await fetch(req);
        if (res && res.ok) (await caches.open(DATA_CACHE)).put(req, res.clone());
        return res;
      } catch (e) {
        const hit = await caches.match(req);
        if (hit) return hit;
        throw e;
      }
    })());
    return;
  }

  // アプリ本体 … キャッシュ優先（起動を速く・オフラインでも動く）
  ev.respondWith((async () => {
    const hit = await caches.match(req);
    if (hit) return hit;
    try {
      const res = await fetch(req);
      if (res && res.ok && res.type === "basic") {
        (await caches.open(SHELL_CACHE)).put(req, res.clone());
      }
      return res;
    } catch (e) {
      // ページ遷移の失敗時はトップを返す（PWAの白画面を防ぐ）
      if (req.mode === "navigate") {
        const shell = await caches.match("./index.html");
        if (shell) return shell;
      }
      throw e;
    }
  })());
});

// アプリ側から「更新する」が押されたときだけ新しい版へ切り替える
self.addEventListener("message", ev => {
  if (ev.data && ev.data.type === "SKIP_WAITING") self.skipWaiting();
});
