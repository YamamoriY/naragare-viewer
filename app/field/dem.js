/* 標高（圏外でも動く）
 *
 * 国土地理院の「標高タイル(PNG版)」は、標高をそのまま画素の色に埋め込んである。
 *   x = R*65536 + G*256 + B
 *   x <  2^23 なら 標高 = x * 0.01 [m]
 *   x == 2^23 なら 標高なし（海など）
 *   x >  2^23 なら 標高 = (x - 2^24) * 0.01 [m]（海面下）
 *
 * これを端末で読めば、
 *   ・いまいる場所の標高（GPSの高度より正確。GPSの高度は±20mずれる）
 *   ・森町が重点管理地域としている「標高200m以下」の塗り
 * のどちらも、電波が無くても出せる。
 *
 * テキスト版(.txt)は1枚350KBあるが、PNG版は30KB前後。
 * 事前ダウンロードに耐えるのはPNG版のほう。
 */
(function (root) {
  'use strict';

  var Z = 14;                    // DEM10B が配信されているズーム
  var SIZE = 256;
  var NA = null;

  var mem = new Map();           // '14/x/y' -> Float32Array | 読み込み中の Promise
  var bad = new Set();           // 取れなかったタイル（海など）。二度と取りに行かない
  var MAX_MEM = 120;             // だいたい 30MB。古いものから捨てる

  function url(z, x, y) {
    return '/api/tile/dem_png/' + z + '/' + x + '/' + y + '.png';
  }

  function lonLatToPixel(lon, lat, z) {
    var n = Math.pow(2, z);
    var x = (lon + 180) / 360 * n * SIZE;
    var r = lat * Math.PI / 180;
    var y = (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * n * SIZE;
    return [x, y];
  }

  function decode(bitmap) {
    var cv = document.createElement('canvas');
    cv.width = cv.height = SIZE;
    var cx = cv.getContext('2d', { willReadFrequently: true });
    cx.drawImage(bitmap, 0, 0);
    var d = cx.getImageData(0, 0, SIZE, SIZE).data;
    var out = new Float32Array(SIZE * SIZE);
    for (var i = 0, p = 0; i < out.length; i++, p += 4) {
      if (d[p + 3] === 0) { out[i] = NaN; continue; }
      var v = d[p] * 65536 + d[p + 1] * 256 + d[p + 2];
      if (v === 8388608) out[i] = NaN;
      else out[i] = (v < 8388608 ? v : v - 16777216) * 0.01;
    }
    return out;
  }

  function tile(tx, ty) {
    var key = Z + '/' + tx + '/' + ty;
    if (bad.has(key)) return Promise.resolve(null);
    var hit = mem.get(key);
    if (hit) return Promise.resolve(hit);

    var p = fetch(url(Z, tx, ty), { cache: 'force-cache' })
      .then(function (r) {
        if (!r.ok) throw new Error('no tile');
        return r.blob();
      })
      .then(function (b) { return createImageBitmap(b); })
      .then(function (bm) {
        var arr = decode(bm);
        if (bm.close) bm.close();
        mem.set(key, arr);
        if (mem.size > MAX_MEM) {
          var first = mem.keys().next().value;
          mem['delete'](first);
        }
        return arr;
      })
      ['catch'](function () { mem['delete'](key); bad.add(key); return null; });
    mem.set(key, p);
    return p;
  }

  /** 1点の標高[m]。取れなければ null。 */
  function elevation(lon, lat) {
    var px = lonLatToPixel(lon, lat, Z);
    var tx = Math.floor(px[0] / SIZE), ty = Math.floor(px[1] / SIZE);
    return Promise.resolve(tile(tx, ty)).then(function (arr) {
      if (!arr) return null;
      var i = Math.min(SIZE - 1, Math.max(0, Math.floor(px[0] - tx * SIZE)));
      var j = Math.min(SIZE - 1, Math.max(0, Math.floor(px[1] - ty * SIZE)));
      var v = arr[j * SIZE + i];
      return isNaN(v) ? null : v;
    });
  }

  /** すでに端末にあるぶんだけで答える（待たない）。画面の追従表示用。 */
  function elevationSync(lon, lat) {
    var px = lonLatToPixel(lon, lat, Z);
    var tx = Math.floor(px[0] / SIZE), ty = Math.floor(px[1] / SIZE);
    var k = Z + '/' + tx + '/' + ty;
    if (bad.has(k)) return null;
    var arr = mem.get(k);
    if (!arr || typeof arr.then === 'function') { tile(tx, ty); return undefined; }
    var i = Math.min(SIZE - 1, Math.max(0, Math.floor(px[0] - tx * SIZE)));
    var j = Math.min(SIZE - 1, Math.max(0, Math.floor(px[1] - ty * SIZE)));
    var v = arr[j * SIZE + i];
    return isNaN(v) ? null : v;
  }

  /* ---------------- 標高200m以下の塗り ----------------
     等高線ではなく面で塗る。
     現地で「いま高リスク区域の中にいるかどうか」が一目で分かるようにするため。
     森町は令和8年に標高200m以下を重点管理地域に設定している。            */
  var Elev200 = root.L && L.GridLayer.extend({
    options: {
      threshold: 200,
      minZoom: 12,
      maxZoom: 20,
      opacity: 0.42,
      color: [201, 42, 42],       // 高リスクの赤
      edge: 6                     // 上端 6m は濃く（境目を見せる）
    },

    createTile: function (coords, done) {
      var cv = document.createElement('canvas');
      cv.width = cv.height = SIZE;
      var self = this;
      var z = coords.z, x = coords.x, y = coords.y;
      var f = Math.pow(2, Z - z);          // 出力1画素 ＝ 標高タイルの何画素ぶんか

      // この出力タイルが必要とする標高タイルを列挙する
      var x0 = Math.floor(x * SIZE * f / SIZE), x1 = Math.floor(((x + 1) * SIZE * f - 1) / SIZE);
      var y0 = Math.floor(y * SIZE * f / SIZE), y1 = Math.floor(((y + 1) * SIZE * f - 1) / SIZE);
      var need = [];
      for (var tx = x0; tx <= x1; tx++) {
        for (var ty = y0; ty <= y1; ty++) need.push([tx, ty]);
      }
      if (need.length > 32) { setTimeout(function () { done(null, cv); }); return cv; }

      Promise.all(need.map(function (t) { return tile(t[0], t[1]); }))
        .then(function (arrs) {
          var map = {};
          need.forEach(function (t, i) { map[t[0] + ',' + t[1]] = arrs[i]; });
          var cx = cv.getContext('2d');
          var img = cx.createImageData(SIZE, SIZE);
          var d = img.data;
          var th = self.options.threshold, col = self.options.color;
          var edge = self.options.edge;
          var any = false;
          for (var j = 0; j < SIZE; j++) {
            var wy = (y * SIZE + j) * f;
            var sty = Math.floor(wy / SIZE), py = Math.floor(wy - sty * SIZE);
            for (var i2 = 0; i2 < SIZE; i2++) {
              var wx = (x * SIZE + i2) * f;
              var stx = Math.floor(wx / SIZE), px = Math.floor(wx - stx * SIZE);
              var arr = map[stx + ',' + sty];
              if (!arr) continue;
              var h = arr[py * SIZE + px];
              if (isNaN(h) || h > th) continue;
              var p = (j * SIZE + i2) * 4;
              d[p] = col[0]; d[p + 1] = col[1]; d[p + 2] = col[2];
              // 200m のすぐ下だけ濃くして、境目の線を見せる
              d[p + 3] = (h > th - edge) ? 190 : 90;
              any = true;
            }
          }
          if (any) cx.putImageData(img, 0, 0);
          done(null, cv);
        })['catch'](function (e) { done(null, cv); });
      return cv;
    }
  });

  /** メモリに読み込み済みの標高タイル数（画面に出す） */
  function cached() {
    var n = 0;
    mem.forEach(function (v) { if (v && typeof v.then !== 'function') n++; });
    return n;
  }

  root.Dem = {
    Z: Z, url: url, elevation: elevation, elevationSync: elevationSync,
    Elev200Layer: Elev200 ? function (o) { return new Elev200(o); } : null,
    cached: cached
  };
})(window);
