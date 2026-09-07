/* 座標の計算（ブラウザ用）
 *
 * tools/geo.py と同じ Krüger 級数6次の実装をそのまま JavaScript に写したもの。
 * 現地は圏外なので、平面直角座標XI系 ⇄ 緯度経度 をサーバーに聞きに行けない。
 * この1ファイルがあれば、機内モードでも座標を出せる。
 *
 * 検算： py -3 tools/geo.py と同じ値になることを
 *        tools/verify_coords.py で突き合わせている（誤差 7cm 以内）。
 */
(function (root) {
  'use strict';

  var A_GRS80 = 6378137.0, F_GRS80 = 1 / 298.257222101;

  function TM(lat0, lon0, k0, fe, fn, a, f) {
    this.lat0 = lat0; this.lon0 = lon0; this.k0 = k0;
    this.fe = fe || 0; this.fn = fn || 0;
    this.a = a || A_GRS80; this.f = f || F_GRS80;

    var n = this.f / (2 - this.f);
    var n2 = n * n, n3 = n2 * n, n4 = n3 * n, n5 = n4 * n;
    this.n = n;
    this.A = (this.a / (1 + n)) * (1 + n2 / 4 + n4 / 64);
    this.alpha = [
      n / 2 - 2 * n2 / 3 + 5 * n3 / 16 + 41 * n4 / 180 - 127 * n5 / 288,
      13 * n2 / 48 - 3 * n3 / 5 + 557 * n4 / 1440 + 281 * n5 / 630,
      61 * n3 / 240 - 103 * n4 / 140 + 15061 * n5 / 26880,
      49561 * n4 / 161280 - 179 * n5 / 168,
      34729 * n5 / 80640
    ];
    this.beta = [
      n / 2 - 2 * n2 / 3 + 37 * n3 / 96 - n4 / 360 - 81 * n5 / 512,
      n2 / 48 + n3 / 15 - 437 * n4 / 1440 + 46 * n5 / 105,
      17 * n3 / 480 - 37 * n4 / 840 - 209 * n5 / 4480,
      4397 * n4 / 161280 - 11 * n5 / 504,
      4583 * n5 / 161280
    ];
    this.delta = [
      2 * n - 2 * n2 / 3 - 2 * n3 + 116 * n4 / 45 + 26 * n5 / 45,
      7 * n2 / 3 - 8 * n3 / 5 - 227 * n4 / 45 + 2704 * n5 / 315,
      56 * n3 / 15 - 136 * n4 / 35 - 1262 * n5 / 105,
      4279 * n4 / 630 - 332 * n5 / 35,
      4174 * n5 / 315
    ];
    this.e2n = 2 * Math.sqrt(n) / (1 + n);
    this.S0 = this.k0 * this.A * this._xiAt(lat0 * Math.PI / 180);
  }

  var atanh = Math.atanh || function (x) { return Math.log((1 + x) / (1 - x)) / 2; };
  var sinh = Math.sinh || function (x) { return (Math.exp(x) - Math.exp(-x)) / 2; };
  var cosh = Math.cosh || function (x) { return (Math.exp(x) + Math.exp(-x)) / 2; };

  TM.prototype._xiAt = function (latRad) {
    var s = Math.sin(latRad);
    var t = sinh(atanh(s) - this.e2n * atanh(this.e2n * s));
    var xi = Math.atan(t), out = xi;
    for (var j = 0; j < this.alpha.length; j++) {
      out += this.alpha[j] * Math.sin(2 * (j + 1) * xi);
    }
    return out;
  };

  /** 緯度経度 → 平面 [東距 x, 北距 y]（単位 m） */
  TM.prototype.fromLonLat = function (lon, lat) {
    var lam = (lon - this.lon0) * Math.PI / 180;
    var phi = lat * Math.PI / 180;
    var s = Math.sin(phi);
    var t = sinh(atanh(s) - this.e2n * atanh(this.e2n * s));
    var xi = Math.atan2(t, Math.cos(lam));
    var eta = atanh(Math.sin(lam) / Math.sqrt(1 + t * t));
    var x = eta, y = xi;
    for (var j = 1; j <= this.alpha.length; j++) {
      var a = this.alpha[j - 1];
      x += a * Math.cos(2 * j * xi) * sinh(2 * j * eta);
      y += a * Math.sin(2 * j * xi) * cosh(2 * j * eta);
    }
    return [this.k0 * this.A * x + this.fe,
            this.k0 * this.A * y - this.S0 + this.fn];
  };

  /** 平面 → 緯度経度 [lon, lat] */
  TM.prototype.toLonLat = function (x, y) {
    var xi = (y - this.fn + this.S0) / (this.k0 * this.A);
    var eta = (x - this.fe) / (this.k0 * this.A);
    var xiP = xi, etaP = eta;
    for (var j = 1; j <= this.beta.length; j++) {
      var b = this.beta[j - 1];
      xiP -= b * Math.sin(2 * j * xi) * cosh(2 * j * eta);
      etaP -= b * Math.cos(2 * j * xi) * sinh(2 * j * eta);
    }
    var chi = Math.asin(Math.max(-1, Math.min(1, Math.sin(xiP) / cosh(etaP))));
    var lat = chi;
    for (var k = 1; k <= this.delta.length; k++) {
      lat += this.delta[k - 1] * Math.sin(2 * k * chi);
    }
    var lon = this.lon0 * Math.PI / 180 + Math.atan2(sinh(etaP), Math.cos(xiP));
    return [lon * 180 / Math.PI, lat * 180 / Math.PI];
  };

  // 平面直角座標系（19系）。k0=0.9999、加算定数なし。
  var ZONES = {
    1: [33, 129.5], 2: [33, 131], 3: [36, 132.1666666666667],
    4: [33, 133.5], 5: [36, 134.3333333333333], 6: [36, 136],
    7: [36, 137.1666666666667], 8: [36, 138.5], 9: [36, 139.8333333333333],
    10: [40, 140.8333333333333], 11: [44, 140.25], 12: [44, 142.25],
    13: [44, 144.25], 14: [26, 142], 15: [26, 127.5],
    16: [26, 124], 17: [26, 131], 18: [20, 136], 19: [26, 154]
  };

  function jpPlane(zone) {
    var z = ZONES[zone];
    return new TM(z[0], z[1], 0.9999, 0, 0);
  }

  var XI = jpPlane(11);   // 北海道南部（森町）はXI系

  /** 2点間の距離[m]（球面近似。数百mの範囲では誤差は無視できる） */
  function distance(lon1, lat1, lon2, lat2) {
    var R = 6371008.8, r = Math.PI / 180;
    var dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
    var a = Math.sin(dLat / 2) * Math.sin(dLat / 2)
      + Math.cos(lat1 * r) * Math.cos(lat2 * r)
      * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
  }

  /** 方位角[度]（真北基準・時計回り） */
  function bearing(lon1, lat1, lon2, lat2) {
    var r = Math.PI / 180;
    var y = Math.sin((lon2 - lon1) * r) * Math.cos(lat2 * r);
    var x = Math.cos(lat1 * r) * Math.sin(lat2 * r)
      - Math.sin(lat1 * r) * Math.cos(lat2 * r) * Math.cos((lon2 - lon1) * r);
    return (Math.atan2(y, x) / r + 360) % 360;
  }

  var COMPASS16 = ['北', '北北東', '北東', '東北東', '東', '東南東', '南東', '南南東',
                   '南', '南南西', '南西', '西南西', '西', '西北西', '北西', '北北西'];
  function compassName(deg) {
    return COMPASS16[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16];
  }

  /* ---------------- 座標の書き方を読み取る ----------------
     現場の道具（ジオグラフィカ・地理院地図・GPS機・無線のメモ）が出す
     書き方をひととおり受け付ける。app/app.js の parseCoord と同じ規則。

       42.105986, 140.683474          十進の度
       N42°06'21.5" E140°41'00.5"     度分秒
       42°06'21.5"N 140°41'00.5"E     度分秒（記号があと）
       N42°06.358' E140°41.008'       度分
       北緯42度6分21.5秒 東経140度41分0.5秒
       X=-210123.45 Y=35876.21        平面直角XI系（X=北距, Y=東距）
  */
  function parse(str) {
    var s = String(str == null ? '' : str)
      .replace(/[０-９Ａ-Ｚａ-ｚ．，＋－]/g,
        function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); })
      .replace(/北緯/g, 'N').replace(/南緯/g, 'S')
      .replace(/東経/g, 'E').replace(/西経/g, 'W')
      .replace(/[°度]/g, ' ')
      .replace(/[′’‘'‛分]/g, ' ')
      .replace(/[″”“"〃秒]/g, ' ')
      .trim();
    if (!s) return null;

    var mx = s.match(/X\s*[=:]?\s*(-?[\d.]+)/i);
    var my = s.match(/Y\s*[=:]?\s*(-?[\d.]+)/i);
    if (mx && my) return { xy: [parseFloat(my[1]), parseFloat(mx[1])] };

    var items = [], re = /([NSEW])|(-?\d+(?:\.\d+)?)/gi, m;
    while ((m = re.exec(s))) {
      if (m[1]) items.push({ h: m[1].toUpperCase() });
      else items.push({ n: parseFloat(m[2]) });
    }
    if (!items.length) return null;

    var hAt = [], nAt = -1, i;
    for (i = 0; i < items.length; i++) {
      if (items[i].h) hAt.push(i);
      else if (nAt < 0) nAt = i;
    }
    var groups;
    if (hAt.length === 2) {
      groups = hAt[0] < nAt
        ? [items.slice(hAt[0], hAt[1]), items.slice(hAt[1])]
        : [items.slice(0, hAt[0] + 1), items.slice(hAt[0] + 1)];
    } else if (hAt.length === 0) {
      var ns = items.filter(function (t) { return t.n !== undefined; });
      if (ns.length < 2 || ns.length % 2) return null;
      groups = [ns.slice(0, ns.length / 2), ns.slice(ns.length / 2)];
    } else return null;

    var vals = groups.map(function (g) {
      var hh = (g.filter(function (t) { return t.h; })[0] || {}).h;
      var n = g.filter(function (t) { return t.n !== undefined; })
              .map(function (t) { return t.n; });
      if (!n.length || n.length > 3) return null;
      if (n.length > 1 && (n[1] < 0 || n[1] >= 60)) return null;
      if (n.length > 2 && (n[2] < 0 || n[2] >= 60)) return null;
      var sign = n[0] < 0 ? -1 : 1;
      var v = sign * (Math.abs(n[0]) + (n[1] || 0) / 60 + (n[2] || 0) / 3600);
      return { v: (hh === 'S' || hh === 'W') ? -Math.abs(v) : v,
               h: hh, split: n.length > 1 };
    });
    if (vals.some(function (v) { return !v; })) return null;
    var a = vals[0], b = vals[1];

    if (a.h && b.h) {
      var lat = 'NS'.indexOf(a.h) >= 0 ? a : b;
      var lon = lat === a ? b : a;
      if ('EW'.indexOf(lon.h) < 0) return null;
      return { lat: lat.v, lon: lon.v };
    }
    var degLike = (a.split || b.split)
      || (Math.abs(a.v) <= 180 && Math.abs(b.v) <= 180);
    if (degLike) {
      if (Math.abs(a.v) > 90 && Math.abs(b.v) <= 90) return { lat: b.v, lon: a.v };
      return { lat: a.v, lon: b.v };
    }
    return { xy: [b.v, a.v], xyGuess: true };
  }

  var NEAR_MORI = function (p) {
    return !!p && p.lat > 41.6 && p.lat < 42.6 && p.lon > 140.0 && p.lon < 141.2;
  };

  /** 文字列 → {lat, lon}。読めなければ null。オフラインでも動く。 */
  function coordFrom(text) {
    var v = parse(text);
    if (!v) return null;
    if (v.xy) {
      var p = XI.toLonLat(v.xy[0], v.xy[1]);
      var out = { lon: p[0], lat: p[1] };
      if (v.xyGuess && !NEAR_MORI(out)) {
        var q = XI.toLonLat(v.xy[1], v.xy[0]);
        if (NEAR_MORI({ lon: q[0], lat: q[1] })) out = { lon: q[0], lat: q[1] };
      }
      return isFinite(out.lat) && isFinite(out.lon) ? out : null;
    }
    if (!isFinite(v.lat) || !isFinite(v.lon)) return null;
    if (Math.abs(v.lat) > 90 || Math.abs(v.lon) > 180) return null;
    return { lat: v.lat, lon: v.lon };
  }

  /** 「42.062733」を「42°06'27.33"」と読み替える。読めなければ null。 */
  function decimalAsDms(numText) {
    var m = /^(-?)(\d+)\.(\d{4,})$/.exec(String(numText).trim());
    if (!m) return null;
    var f = m[3];
    var mm = +f.slice(0, 2);
    var ss = +(f.slice(2, 4) + '.' + (f.slice(4) || '0'));
    if (!(mm < 60) || !(ss < 60)) return null;
    var v = +m[2] + mm / 60 + ss / 3600;
    return m[1] === '-' ? -v : v;
  }

  /** 度分秒を10進として書き写した間違いを拾う（23km ずれる） */
  function dmsMisreading(text) {
    var s = String(text == null ? '' : text)
      .replace(/[０-９．，＋－]/g,
        function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); });
    if (/[°'"′″’”度分秒NSEWnsew]/.test(s)) return null;
    var m = s.match(/(-?\d+\.\d{4,})[^\d.-]+(-?\d+\.\d{4,})/);
    if (!m) return null;
    var a = decimalAsDms(m[1]), b = decimalAsDms(m[2]);
    if (a == null || b == null) return null;
    var lat = a, lon = b;
    if (Math.abs(a) > 90 && Math.abs(b) <= 90) { lat = b; lon = a; }
    var alt = { lat: lat, lon: lon };
    if (!NEAR_MORI(alt)) return null;
    var lit = parse(text);
    if (!lit || lit.xy || lit.lat == null) return null;
    var d = distance(lit.lon, lit.lat, alt.lon, alt.lat);
    if (d < 500) return null;
    return { alt: alt, m: d };
  }

  /** 度 → 度分秒の文字列 */
  function dms(v, isLat) {
    if (v == null || !isFinite(v)) return '';
    var h = isLat ? (v < 0 ? 'S' : 'N') : (v < 0 ? 'W' : 'E');
    var a = Math.abs(v);
    var d = Math.floor(a);
    var mf = (a - d) * 60;
    var mi = Math.floor(mf);
    var se = (mf - mi) * 60;
    return h + d + '°' + String(mi).padStart(2, '0') + '′'
      + (se < 10 ? '0' : '') + se.toFixed(2) + '″';
  }

  root.Geo = {
    TM: TM, jpPlane: jpPlane, XI: XI,
    toXY: function (lon, lat) { return XI.fromLonLat(lon, lat); },
    toLonLat: function (x, y) { return XI.toLonLat(x, y); },
    distance: distance, bearing: bearing, compassName: compassName,
    parse: parse, coordFrom: coordFrom, dms: dms,
    dmsMisreading: dmsMisreading, nearMori: NEAR_MORI
  };
})(typeof self !== 'undefined' ? self : this);
