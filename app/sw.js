/* サービスワーカー ―― 圏外でもアプリが立ち上がるようにする係
 *
 * これが無いと、電波の無い森の中でホーム画面のアイコンを押しても白い画面になる。
 *
 * 3つの箱に分けて貯める。
 *   shell  … 画面そのもの（HTML/JS/CSS/Leaflet）。更新したら入れ替える。
 *   tiles  … 地図のタイル。いちど入れたら変わらないので、あるものを必ず使う。
 *   data   … 林班や町界の GeoJSON。あれば先に出し、裏で新しくする。
 *
 * タイルの箱は「事前ダウンロード」画面からも同じ名前で使う。
 * ページ側で caches.put() して貯め、ここが取り出す。
 */
var VERSION = 'v3';
var SHELL = 'naragare-shell-' + VERSION;
var TILES = 'naragare-tiles-v1';     // 中身は版が変わっても捨てない
var DATA = 'naragare-data-' + VERSION;

var SHELL_FILES = [
  '/field/',
  '/field/index.html',
  '/field/field.css',
  '/field/field.js',
  '/field/store.js',
  '/field/sync.js',
  '/field/dem.js',
  '/app/geo.js',
  '/app/vendor/leaflet.js',
  '/app/vendor/leaflet.css',
  '/app/vendor/images/marker-icon.png',
  '/app/vendor/images/marker-icon-2x.png',
  '/app/vendor/images/marker-shadow.png',
  '/manifest.webmanifest',
  '/app/icons/icon-192.png',
  '/app/icons/icon-512.png'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(SHELL).then(function (c) {
      // 1つ失敗しても全部が失敗しないように、1件ずつ入れる
      return Promise.all(SHELL_FILES.map(function (u) {
        return c.add(new Request(u, { cache: 'reload' }))['catch'](function () {});
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        // タイルの箱だけは版が上がっても消さない（現地で消えたら致命的）
        if (k === TILES) return null;
        if (k === SHELL || k === DATA) return null;
        if (k.indexOf('naragare-') === 0) return caches['delete'](k);
        return null;
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

function isTile(url) {
  return /^\/api\/tile\//.test(url.pathname)
    || /^\/data\/(tiles|basemap)\//.test(url.pathname);
}
function isData(url) {
  return /^\/data\/layers\//.test(url.pathname)
    || /^\/data\/photos\//.test(url.pathname);
}
function isApi(url) {
  return /^\/api\//.test(url.pathname) && !isTile(url);
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // 画面の読み込み。圏外なら貯めてある画面を返す。
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req)['catch'](function () {
        return caches.match('/field/index.html')
          .then(function (r) { return r || caches.match('/field/'); });
      })
    );
    return;
  }

  // 地図タイル：あるものを必ず使う。無ければ取りに行き、取れたら貯める。
  if (isTile(url)) {
    e.respondWith(
      caches.open(TILES).then(function (c) {
        return c.match(req).then(function (hit) {
          if (hit) return hit;
          return fetch(req).then(function (res) {
            if (res && res.ok) c.put(req, res.clone());
            return res;
          })['catch'](function () {
            return new Response('', { status: 504, statusText: 'offline' });
          });
        });
      })
    );
    return;
  }

  // 重ねる情報・写真：あれば先に出し、裏で新しくしておく
  if (isData(url)) {
    e.respondWith(
      caches.open(DATA).then(function (c) {
        return c.match(req).then(function (hit) {
          var net = fetch(req).then(function (res) {
            if (res && res.ok) c.put(req, res.clone());
            return res;
          })['catch'](function () { return hit || new Response('', { status: 504 }); });
          return hit || net;
        });
      })
    );
    return;
  }

  // API：必ず新しいものを取りに行く（同期は鮮度がすべて）
  if (isApi(url)) return;

  // 画面のファイル：あるものを出しつつ、裏で新しくする
  e.respondWith(
    caches.open(SHELL).then(function (c) {
      return c.match(req).then(function (hit) {
        var net = fetch(req).then(function (res) {
          if (res && res.ok) c.put(req, res.clone());
          return res;
        })['catch'](function () { return hit; });
        return hit || net;
      });
    })
  );
});

/* ページからの頼みごと */
self.addEventListener('message', function (e) {
  var d = e.data || {};
  if (d.type === 'SKIP_WAITING') self.skipWaiting();
  if (d.type === 'TILE_COUNT') {
    caches.open(TILES).then(function (c) { return c.keys(); })
      .then(function (keys) {
        e.source.postMessage({ type: 'TILE_COUNT', n: keys.length });
      });
  }
  if (d.type === 'CLEAR_TILES') {
    caches['delete'](TILES).then(function () {
      e.source.postMessage({ type: 'TILES_CLEARED' });
    });
  }
});
