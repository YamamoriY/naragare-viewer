/* ナラ枯れ現地調査アプリ（スマホ／タブレット／PC 共通）
 *
 * 前提
 *   ・森の中は圏外。書いたものは端末に貯まり、電波が来たときに送る。
 *   ・手袋・片手・直射日光。押すところは大きく、文字は濃く。
 *   ・立ち止まっている時間は短い。1本の木を記録するのに要る操作を減らす。
 *
 * 画面の作り
 *   地図が主。下の3つのボタン（木を登録／歩いた跡／近くの木）が入口で、
 *   細かいことは全部「下から出るパネル」に入れてある。
 *   階層をこれ以上深くしない。
 */
(function () {
  'use strict';

  var $ = function (s) { return document.querySelector(s); };
  var S = window.Store, Y = window.Sync, G = window.Geo, D = window.Dem;

  /* ================================================================ 状態 */
  var F = {
    map: null,
    layer: {},                 // 地図のレイヤー
    marks: {},                 // uuid -> CircleMarker
    trees: [],                 // 端末が持っている木（消したものを除く）
    tracks: [],
    here: null,                // {lat, lon, acc, alt, at}
    heading: null,             // 端末が向いている方位[度]
    follow: true,
    mode: null,                // null | 'pick' | 'move'
    pickCb: null,
    sel: null,                 // 選んでいる木の uuid
    rec: null,                 // 記録中のトラック
    boot: null,                // /api/bootstrap の控え
    cat: null,                 // ステータス定義
    settings: {},
    watchId: null,
    alerted: {}                // 近づいたと知らせた木
  };

  var DEFAULT_CAT = {
    status: [
      { code: 'unsurveyed', label: '未調査', color: '#6b7684' },
      { code: 'damaged', label: '被害あり', color: '#c92a2a' },
      { code: 'clean', label: '被害なし', color: '#2b8a3e' },
      { code: 'pending', label: '判定保留', color: '#d9480f' },
      { code: 'unreachable', label: '到達できず', color: '#5f3dc4' }
    ],
    work: [
      { code: 'none', label: '対象外', color: '#adb5bd' },
      { code: 'waiting', label: '処理待ち', color: '#e8590c' },
      { code: 'ordered', label: '発注済', color: '#1971c2' },
      { code: 'done', label: '処理済', color: '#2f9e44' }
    ]
  };

  // 現地で記録する項目の選択肢
  var SPECIES = ['ミズナラ', 'コナラ', 'カシワ', 'ナラ類（種不明）', '広葉樹その他', '不明'];
  var FRASS = ['なし', '少', '中', '多'];
  var COND = ['葉の変色', '落葉', '衰弱', '枯死'];
  var BORING = ['なし', 'あり（少）', 'あり（多）'];
  var DBH_QUICK = [15, 20, 25, 30, 40, 50, 60, 80];

  var MORI = [42.106, 140.577];

  /* ================================================================ 小道具 */
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === 'class') n.className = attrs[k];
      else if (k === 'html') n.innerHTML = attrs[k];
      else if (k === 'text') n.textContent = attrs[k];
      else if (k.slice(0, 2) === 'on') n.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] != null && attrs[k] !== false) n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) {
      if (c == null) return;
      n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return n;
  }

  var toastTimer;
  function toast(msg, kind) {
    var t = $('#toast');
    t.textContent = msg;
    t.className = kind || '';
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, kind === 'err' ? 5200 : 2800);
  }

  function buzz(pattern) {
    if (navigator.vibrate) { try { navigator.vibrate(pattern); } catch (e) {} }
  }

  function fmtDist(m) {
    if (m == null || !isFinite(m)) return '―';
    if (m < 1000) return Math.round(m) + ' m';
    return (m / 1000).toFixed(m < 10000 ? 2 : 1) + ' km';
  }
  function fmtDur(s) {
    s = Math.max(0, Math.round(s));
    var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
    return (h ? h + ':' + (m < 10 ? '0' : '') : '') + (h ? m : m) + ':'
      + ('0' + (s % 60)).slice(-2);
  }
  function statusOf(code) {
    var l = (F.cat && F.cat.status) || DEFAULT_CAT.status;
    for (var i = 0; i < l.length; i++) if (l[i].code === code) return l[i];
    return l[0];
  }

  /* ================================================================ 起動 */
  function boot() {
    splash('地図を用意しています…');
    registerSW();

    Promise.resolve()
      .then(function () { return S.persist(); })
      .then(function () { return loadSettings(); })
      .then(function () { return initMap(); })
      .then(function () { return loadLocal(); })
      .then(function () { return loadBootstrap(); })
      .then(function () {
        drawTrees();
        drawTracks();
        startGps();
        startCompass();
        wire();
        Y.auto(10);
        Y.on(renderSync);
        Y.countPending();
        return Y.ping(4000).then(function (ok) {
          Y.state.reachable = ok; Y.emit();
          if (ok) return Y.syncNow({ quiet: true }).then(reloadAfterSync);
        });
      })
      .then(function () {
        splash(null);
        handleShortcut();
      })
      ['catch'](function (e) {
        console.error(e);
        splash(null);
        toast('起動でつまずきました: ' + (e.message || e), 'err');
      });
  }

  function splash(msg) {
    var s = $('#splash');
    if (msg == null) { s.classList.add('gone'); setTimeout(function () { s.hidden = true; }, 320); }
    else $('#splash-msg').textContent = msg;
  }

  function registerSW() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then(function (reg) {
        reg.addEventListener('updatefound', function () {
          var w = reg.installing;
          if (!w) return;
          w.addEventListener('statechange', function () {
            if (w.state === 'installed' && navigator.serviceWorker.controller) {
              toast('新しい版が入りました。次に開いたときから切り替わります。');
            }
          });
        });
      })['catch'](function (e) { console.warn('SW', e); });
  }

  function loadSettings() {
    return S.getKV('settings', {}).then(function (v) {
      F.settings = Object.assign({
        basemap: 'pale', ortho: true, elev200: true, rinpan: true,
        boundary: true, nara: false, tracks: true, labels: true,
        keepScreenOn: true, proximity: 25, orthoOpacity: 100
      }, v || {});
      return S.getKV('catalog').then(function (c) {
        F.cat = c || DEFAULT_CAT;
      });
    });
  }
  function saveSettings() { return S.setKV('settings', F.settings); }

  function loadLocal() {
    return Promise.all([S.all('trees'), S.all('tracks')]).then(function (v) {
      F.trees = v[0].filter(function (t) { return !t.deleted; });
      F.tracks = v[1].filter(function (t) { return !t.deleted; });
    });
  }

  function loadBootstrap() {
    return fetch('/api/bootstrap', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (b) { F.boot = b; S.setKV('bootstrap', b); return b; })
      ['catch'](function () {
        return S.getKV('bootstrap').then(function (b) {
          F.boot = b || { sites: [], layers: {} };
          return F.boot;
        });
      })
      .then(function (b) {
        buildOrthoLayers(b);
        return b;
      });
  }

  function reloadAfterSync() {
    return loadLocal().then(function () {
      drawTrees();
      drawTracks();
      // 開いているパネルは、受け取った新しい中身で描き直す
      if (F.sel && !$('#sheet').hidden && F.sheetKind === 'tree') openTree(F.sel, true);
    });
  }

  function handleShortcut() {
    var p = new URLSearchParams(location.search).get('do');
    if (p === 'add-here') addHere();
    if (p === 'track') toggleTrack();
    if (p === 'sync') openSync();
    if (p) history.replaceState({}, '', '/field/');
  }

  /* ================================================================ 地図 */
  function initMap() {
    var m = L.map('map', {
      center: MORI, zoom: 13, zoomControl: false, attributionControl: true,
      preferCanvas: true, tap: false, maxZoom: 20, minZoom: 8
    });
    F.map = m;
    m.attributionControl.setPrefix('');

    F.layer.base = L.tileLayer('/api/tile/pale/{z}/{x}/{y}.png', {
      maxNativeZoom: 18, maxZoom: 20, attribution: '地理院タイル', keepBuffer: 4
    }).addTo(m);

    F.layer.elev200 = D.Elev200Layer ? D.Elev200Layer({
      minZoom: 12, maxZoom: 20, pane: 'overlayPane', opacity: 0.5
    }) : null;

    F.layer.ortho = L.layerGroup().addTo(m);
    F.layer.vector = L.layerGroup().addTo(m);
    F.layer.tracks = L.layerGroup().addTo(m);
    F.layer.trees = L.layerGroup().addTo(m);
    F.layer.tools = L.layerGroup().addTo(m);

    applyBasemap();
    if (F.settings.elev200 && F.layer.elev200) F.layer.elev200.addTo(m);
    loadVectorLayers();

    m.on('movestart', function (e) {
      if (e.hard !== true && F.follow && !F.autoPan) setFollow(false);
    });
    m.on('move', function () { if (F.mode === 'pick') updatePickReadout(); });
    m.on('click', onMapClick);

    // 地図を触っている間はスリープさせない
    keepAwake();
    return Promise.resolve();
  }

  function applyBasemap() {
    var key = F.settings.basemap;
    var ext = key === 'photo' ? 'jpg' : 'png';
    var zmax = key === 'photo' ? 18 : 18;
    F.layer.base.options.maxNativeZoom = zmax;
    F.layer.base.setUrl('/api/tile/' + key + '/{z}/{x}/{y}.' + ext);
  }

  function buildOrthoLayers(b) {
    F.layer.ortho.clearLayers();
    F.orthoLayers = [];
    (b.sites || []).forEach(function (s) {
      if (!s.tile_ext || s.minlon == null) return;
      var url = '/data/tiles/' + s.id + '/{z}/{x}/{y}.' + s.tile_ext;
      var lay = L.tileLayer(url, {
        minNativeZoom: s.zmin || 12, maxNativeZoom: s.zmax || 20, maxZoom: 20,
        bounds: L.latLngBounds([s.minlat, s.minlon], [s.maxlat, s.maxlon]),
        opacity: (F.settings.orthoOpacity || 100) / 100,
        attribution: 'ドローンオルソ'
      });
      lay._site = s;
      F.orthoLayers.push(lay);
      if (F.settings.ortho && F.settings['ortho_' + s.id] !== false) {
        lay.addTo(F.layer.ortho);
      }
    });
  }

  function loadVectorLayers() {
    var b = F.boot || {};
    var L2 = (b.layers || {});
    if (F.settings.boundary && L2.boundary) {
      fetchJson(L2.boundary).then(function (g) {
        F.layer.boundaryG = L.geoJSON(g, {
          style: function (f) {
            return f.properties.main
              ? { color: '#0b5fce', weight: 3, fill: false, dashArray: null }
              : { color: '#7b8794', weight: 1.5, fill: false, dashArray: '5 5' };
          },
          interactive: false
        }).addTo(F.layer.vector);
      });
    }
    if (F.settings.rinpan && L2.rinpan) {
      fetchJson(L2.rinpan).then(function (g) {
        F.layer.rinpanG = L.geoJSON(g, {
          style: { color: '#2f6f4f', weight: 1.4, fillOpacity: 0, opacity: .8 },
          interactive: false
        }).addTo(F.layer.vector);
      });
    }
  }

  var jsonCache = {};
  function fetchJson(url) {
    if (jsonCache[url]) return Promise.resolve(jsonCache[url]);
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error('取れません');
      return r.json();
    }).then(function (j) { jsonCache[url] = j; return j; })
      ['catch'](function () { return { type: 'FeatureCollection', features: [] }; });
  }

  /* ================================================================ 木の表示 */
  function radiusFor(z) { return z >= 18 ? 9 : z >= 16 ? 7 : z >= 14 ? 5 : 4; }

  function drawTrees() {
    F.layer.trees.clearLayers();
    F.marks = {};
    var r = radiusFor(F.map.getZoom());
    F.trees.forEach(function (t) { addMark(t, r); });
    updateNearBadge();
  }

  function addMark(t, r) {
    if (t.lat == null || t.lon == null) return;
    var st = statusOf(t.status);
    var mk = L.circleMarker([t.lat, t.lon], {
      radius: r || radiusFor(F.map.getZoom()),
      color: '#ffffff', weight: 2, opacity: .95,
      fillColor: st.color, fillOpacity: .95,
      className: t.dirty ? 'dirty' : ''
    });
    mk.on('click', function (e) {
      L.DomEvent.stop(e);
      openTree(t.uuid);
    });
    mk.addTo(F.layer.trees);
    F.marks[t.uuid] = mk;
  }

  function refreshMark(t) {
    var mk = F.marks[t.uuid];
    if (!mk) { addMark(t); return; }
    var st = statusOf(t.status);
    mk.setStyle({ fillColor: st.color });
    mk.setLatLng([t.lat, t.lon]);
  }

  function drawTracks() {
    F.layer.tracks.clearLayers();
    if (!F.settings.tracks) return;
    F.tracks.forEach(function (tr) {
      var pts = tr.points || [];
      if (pts.length < 2) return;
      L.polyline(pts.map(function (p) { return [p[1], p[0]]; }), {
        color: tr.color || '#5f3dc4', weight: 4, opacity: .45, interactive: true
      }).addTo(F.layer.tracks).on('click', function () { openTrack(tr.uuid); });
    });
  }

  /* ================================================================ 現在地 */
  function startGps() {
    if (!navigator.geolocation) {
      toast('この端末では現在地を取れません', 'err');
      return;
    }
    F.watchId = navigator.geolocation.watchPosition(onPos, onPosErr, {
      enableHighAccuracy: true, maximumAge: 2000, timeout: 20000
    });
  }

  function onPos(pos) {
    var c = pos.coords;
    F.here = {
      lat: c.latitude, lon: c.longitude, acc: c.accuracy,
      alt: c.altitude, speed: c.speed,
      course: (c.heading != null && !isNaN(c.heading)) ? c.heading : null,
      at: pos.timestamp
    };
    if (F.here.course != null && F.headingSource !== 'sensor') {
      F.heading = F.here.course;
      setCompass(F.heading);
    }
    drawHere();
    if (F.follow) {
      F.autoPan = true;
      F.map.setView([F.here.lat, F.here.lon], F.map.getZoom(), { animate: true });
      setTimeout(function () { F.autoPan = false; }, 400);
    }
    renderGpsPill();
    if (F.rec) recordPoint();
    checkProximity();
  }

  function onPosErr(e) {
    var msg = e.code === 1
      ? '位置情報が許可されていません。設定から許可してください。'
      : '現在地を取れません（' + e.message + '）';
    $('#gps-text').textContent = 'GPS ✕';
    if (!F.gpsWarned) { toast(msg, 'err'); F.gpsWarned = true; }
  }

  function drawHere() {
    if (!F.here) return;
    var ll = [F.here.lat, F.here.lon];
    if (!F.hereMk) {
      F.hereCone = L.marker(ll, {
        icon: L.divIcon({ className: '', html: '<div class="here-cone"></div>',
                          iconSize: [26, 30], iconAnchor: [13, 30] }),
        interactive: false, zIndexOffset: 500
      }).addTo(F.layer.tools);
      F.hereAcc = L.circle(ll, { radius: F.here.acc || 10, color: '#0b5fce',
        weight: 1, fillOpacity: .08, interactive: false }).addTo(F.layer.tools);
      F.hereMk = L.marker(ll, {
        icon: L.divIcon({ className: '', html: '<div class="here-dot"></div>',
                          iconSize: [18, 18], iconAnchor: [9, 9] }),
        interactive: false, zIndexOffset: 600
      }).addTo(F.layer.tools);
    } else {
      F.hereMk.setLatLng(ll);
      F.hereCone.setLatLng(ll);
      F.hereAcc.setLatLng(ll).setRadius(F.here.acc || 10);
    }
    var cone = F.hereCone.getElement();
    if (cone && F.heading != null) {
      cone.firstChild.style.transform = 'rotate(' + (F.heading + 180) + 'deg)';
      cone.style.opacity = 1;
    } else if (cone) cone.style.opacity = 0;
  }

  function renderGpsPill() {
    var t = $('#gps-text');
    if (!F.here) { t.textContent = 'GPS ―'; return; }
    var e = D.elevationSync(F.here.lon, F.here.lat);
    var parts = ['±' + Math.round(F.here.acc) + 'm'];
    if (e != null && e !== undefined) parts.push(Math.round(e) + 'm');
    t.textContent = parts.join(' ・ ');
    // 標高200m以下にいるかを色で出す
    $('#pill-gps').style.borderColor =
      (e != null && e !== undefined && e <= 200) ? '#c92a2a' : '';
  }

  function setFollow(on) {
    F.follow = on;
    $('#btn-locate').classList.toggle('on', on);
  }

  /* --------------- 方位（コンパス） --------------- */
  function startCompass() {
    var handler = function (e) {
      var h = null;
      if (e.webkitCompassHeading != null) h = e.webkitCompassHeading;    // iOS
      else if (e.absolute && e.alpha != null) h = 360 - e.alpha;         // Android
      if (h == null || isNaN(h)) return;
      F.headingSource = 'sensor';
      F.heading = (h + 360) % 360;
      setCompass(F.heading);
      drawHere();
    };
    F.compassHandler = handler;
    if (typeof DeviceOrientationEvent !== 'undefined'
        && typeof DeviceOrientationEvent.requestPermission === 'function') {
      // iOS 13+ は利用者の操作からしか許可を求められない。ボタンで求める。
      F.needCompassPermission = true;
    } else {
      window.addEventListener('deviceorientationabsolute', handler, true);
      window.addEventListener('deviceorientation', handler, true);
    }
  }

  function askCompass() {
    if (!F.needCompassPermission) return Promise.resolve(true);
    return DeviceOrientationEvent.requestPermission().then(function (r) {
      if (r === 'granted') {
        window.addEventListener('deviceorientation', F.compassHandler, true);
        F.needCompassPermission = false;
        toast('方位を使えるようになりました');
        return true;
      }
      toast('方位が許可されませんでした', 'err');
      return false;
    })['catch'](function () { return false; });
  }

  function setCompass(deg) {
    var g = document.getElementById('compass-rot');
    if (g) g.style.transform = 'rotate(' + (-deg) + 'deg)';
  }

  /* --------------- 画面を消さない --------------- */
  function keepAwake() {
    if (!F.settings.keepScreenOn || !('wakeLock' in navigator)) return;
    var req = function () {
      navigator.wakeLock.request('screen').then(function (l) {
        F.wakeLock = l;
        l.addEventListener('release', function () { F.wakeLock = null; });
      })['catch'](function () {});
    };
    req();
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible' && !F.wakeLock
          && F.settings.keepScreenOn) req();
    });
  }

  /* ================================================================ 近接 */
  function checkProximity() {
    if (!F.here || !F.settings.proximity) return;
    var lim = F.settings.proximity;
    var near = null, nd = Infinity;
    F.trees.forEach(function (t) {
      if (t.lat == null) return;
      var d = G.distance(F.here.lon, F.here.lat, t.lon, t.lat);
      if (d < nd) { nd = d; near = t; }
    });
    updateNearBadge();
    if (!near || nd > lim) return;
    if (F.alerted[near.uuid]) return;
    F.alerted[near.uuid] = true;
    if (near.status === 'unsurveyed') {
      buzz([120, 80, 120]);
      toast(near.code + ' まで ' + Math.round(nd) + 'm（未調査）');
    }
  }

  function updateNearBadge() {
    if (!F.here) return;
    var n = 0;
    F.trees.forEach(function (t) {
      if (t.lat == null || t.status !== 'unsurveyed') return;
      if (G.distance(F.here.lon, F.here.lat, t.lon, t.lat) < 200) n++;
    });
    var b = $('#near-badge');
    b.hidden = n === 0;
    b.textContent = n;
  }

  /* ================================================================ パネル */
  function sheet(title, build, opts) {
    var body = $('#sheet-body');
    body.innerHTML = '';
    $('#sheet-title').textContent = title;
    $('#sheet').hidden = false;
    $('#scrim').hidden = false;
    body.scrollTop = 0;
    F.sheetOpts = opts || {};
    F.sheetKind = F.sheetOpts.kind || null;
    build(body);
  }
  function closeSheet() {
    $('#sheet').hidden = true;
    $('#scrim').hidden = true;
    if (F.sheetOpts && F.sheetOpts.onclose) F.sheetOpts.onclose();
  }

  /* ================================================================ 登録 */
  function openAdd() {
    sheet('木を登録する', function (b) {
      b.appendChild(el('p', { class: 'muted small mb',
        text: '登録のしかたを選びます。あとから位置を直せます。' }));

      b.appendChild(bigChoice('現在地に登録', 'いま立っている場所。GPSの誤差はそのまま記録されます。',
        function () { closeSheet(); addHere(); }, F.here
          ? ('GPS ±' + Math.round(F.here.acc) + 'm') : 'GPS待ち'));

      b.appendChild(bigChoice('地図で場所を選ぶ', '木の見えるところまで地図を動かして決めます。',
        function () { closeSheet(); startPick(); }));

      b.appendChild(bigChoice('緯度経度を入力', '無線で聞いた座標・GPS機の値・平面直角XI系。',
        function () { closeSheet(); openCoordEntry(); }));
    });
  }

  function bigChoice(title, desc, fn, tag) {
    return el('button', { class: 'item mb', onclick: fn }, [
      el('div', { class: 't' }, [
        el('b', { text: title }),
        el('span', { text: desc })
      ]),
      tag ? el('span', { class: 'd tiny muted', text: tag }) : null,
      el('span', { class: 'arrow', html:
        '<svg viewBox="0 0 24 24" fill="none" stroke="#46586b" stroke-width="2"><polyline points="9,5 16,12 9,19"/></svg>' })
    ]);
  }

  function addHere() {
    if (!F.here) { toast('まだ現在地が取れていません', 'err'); return; }
    if (F.here.acc > 40) {
      if (!confirm('GPSの誤差が ±' + Math.round(F.here.acc)
                   + 'm あります。このまま登録しますか？\n'
                   + '（空の見える場所で少し待つと精度が上がります）')) return;
    }
    createTree(F.here.lon, F.here.lat, {
      loc_accuracy: '現在地（GPS ±' + Math.round(F.here.acc) + 'm）',
      gps_acc: Math.round(F.here.acc * 10) / 10,
      heading: F.heading != null ? Math.round(F.heading) : null
    });
  }

  function startPick(onPick, label) {
    F.mode = 'pick';
    F.pickCb = onPick || null;
    $('#crosshair').hidden = false;
    setFollow(false);
    showPickBar(label || 'ここに登録');
    updatePickReadout();
  }

  function endPick() {
    F.mode = null; F.pickCb = null;
    $('#crosshair').hidden = true;
    if (F.pickBar) { F.pickBar.remove(); F.pickBar = null; }
  }

  function showPickBar(label) {
    if (F.pickBar) F.pickBar.remove();
    var read = el('span', { class: 'grow tiny', id: 'pick-read' });
    var bar = el('div', { class: 'bar', id: 'pickbar' }, [
      read,
      el('button', { class: 'mini', onclick: function () { endPick(); } }, ['やめる']),
      el('button', { class: 'mini stop', onclick: doPick }, [label])
    ]);
    bar.hidden = false;
    document.body.appendChild(bar);
    F.pickBar = bar;
  }

  function updatePickReadout() {
    var r = document.getElementById('pick-read');
    if (!r) return;
    var c = F.map.getCenter();
    var e = D.elevationSync(c.lng, c.lat);
    var d = F.here ? G.distance(F.here.lon, F.here.lat, c.lng, c.lat) : null;
    r.textContent = c.lat.toFixed(6) + ', ' + c.lng.toFixed(6)
      + (e != null && e !== undefined ? ' ・ ' + Math.round(e) + 'm' : '')
      + (d != null ? ' ・ 現在地から ' + fmtDist(d) : '');
  }

  function doPick() {
    var c = F.map.getCenter();
    var cb = F.pickCb;
    endPick();
    if (cb) cb(c.lng, c.lat);
    else createTree(c.lng, c.lat, { loc_accuracy: '地図上で指定' });
  }

  function onMapClick(e) {
    if (F.mode) return;
    // 近くの木があれば開く（指は太いので広めに拾う）
    var best = null, bd = Infinity;
    F.trees.forEach(function (t) {
      if (t.lat == null) return;
      var d = F.map.latLngToContainerPoint([t.lat, t.lon])
        .distanceTo(F.map.latLngToContainerPoint(e.latlng));
      if (d < bd) { bd = d; best = t; }
    });
    if (best && bd < 30) openTree(best.uuid);
  }

  /* --------------- 緯度経度を入力して登録 --------------- */
  function openCoordEntry(onOk) {
    sheet('緯度経度を入力', function (b) {
      var input = el('input', { type: 'text', inputmode: 'text',
        placeholder: '42.105986, 140.683474', autocomplete: 'off' });
      var prev = el('div', { class: 'card', style: 'display:none' });
      var warn = el('div', { class: 'card warn', style: 'display:none' });
      var okBtn = el('button', { class: 'btn pri block mt', disabled: true }, ['この場所に登録']);
      var found = null;

      b.appendChild(el('p', { class: 'muted small mb', html:
        'こう書けます<br>' +
        '<b>42.105986, 140.683474</b>（十進）<br>' +
        '<b>N42°06\'21.5" E140°41\'00.5"</b>（度分秒）<br>' +
        '<b>北緯42度6分21.5秒 東経140度41分0.5秒</b><br>' +
        '<b>X=-210167.4 Y=35644.23</b>（平面直角XI系）' }));
      b.appendChild(input);
      b.appendChild(warn);
      b.appendChild(prev);
      b.appendChild(okBtn);

      var upd = function () {
        var txt = input.value;
        var p = G.coordFrom(txt);
        found = p;
        okBtn.disabled = !p;
        if (!p) { prev.style.display = 'none'; warn.style.display = 'none'; return; }

        // 度分秒を10進として書き写す間違いを拾う
        var mis = G.dmsMisreading(txt);
        if (mis) {
          warn.style.display = '';
          warn.innerHTML = '';
          warn.appendChild(el('b', { text: '⚠ 書き写し間違いかもしれません' }));
          warn.appendChild(el('p', { class: 'small', text:
            '「' + txt.trim() + '」を度分秒（' + G.dms(mis.alt.lat, true) + ' '
            + G.dms(mis.alt.lon, false) + '）と読むと、'
            + fmtDist(mis.m) + '離れた別の場所になります。' }));
          warn.appendChild(el('button', { class: 'btn block',
            onclick: function () {
              input.value = mis.alt.lat.toFixed(6) + ', ' + mis.alt.lon.toFixed(6);
              upd();
            } }, ['度分秒として読み直す']));
        } else warn.style.display = 'none';

        prev.style.display = '';
        var xy = G.toXY(p.lon, p.lat);
        var d = F.here ? G.distance(F.here.lon, F.here.lat, p.lon, p.lat) : null;
        var br = F.here ? G.bearing(F.here.lon, F.here.lat, p.lon, p.lat) : null;
        prev.innerHTML = '';
        var dl = el('dl', { class: 'kv' });
        var add = function (k, v) {
          dl.appendChild(el('dt', { text: k }));
          dl.appendChild(el('dd', { text: v }));
        };
        add('緯度経度', p.lat.toFixed(6) + ', ' + p.lon.toFixed(6));
        add('度分秒', G.dms(p.lat, true) + ' ' + G.dms(p.lon, false));
        add('XI系', 'X=' + xy[1].toFixed(2) + ' Y=' + xy[0].toFixed(2));
        if (d != null) add('現在地から', fmtDist(d) + '（' + G.compassName(br) + '）');
        if (!G.nearMori(p)) add('⚠', '森町から外れています');
        prev.appendChild(dl);
        prev.appendChild(el('button', { class: 'btn block mt', onclick: function () {
          F.map.setView([p.lat, p.lon], 18);
          showGhost(p.lon, p.lat);
        } }, ['地図で確かめる']));
        D.elevation(p.lon, p.lat).then(function (e) {
          if (e != null) add('標高', Math.round(e) + ' m' + (e <= 200 ? '（200m以下）' : ''));
        });
      };

      input.addEventListener('input', upd);
      okBtn.addEventListener('click', function () {
        if (!found) return;
        closeSheet();
        clearGhost();
        if (onOk) onOk(found.lon, found.lat);
        else createTree(found.lon, found.lat, { loc_accuracy: '緯度経度を入力' });
      });
      setTimeout(function () { input.focus(); }, 200);
    }, { onclose: clearGhost });
  }

  function showGhost(lon, lat) {
    clearGhost();
    F.ghost = L.marker([lat, lon], {
      icon: L.divIcon({ className: '', html: '<div class="new-pin"></div>',
                        iconSize: [22, 22], iconAnchor: [11, 11] })
    }).addTo(F.layer.tools);
  }
  function clearGhost() {
    if (F.ghost) { F.layer.tools.removeLayer(F.ghost); F.ghost = null; }
  }

  /* --------------- 実際に作る --------------- */
  function createTree(lon, lat, extra) {
    var now = S.stamp();
    var t = Object.assign({
      uuid: S.uuid(),
      code: '仮' + now.slice(5, 10).replace('-', '') + '-'
            + String(F.trees.length + 1).padStart(3, '0'),
      site: 'field',
      lon: Math.round(lon * 1e7) / 1e7,
      lat: Math.round(lat * 1e7) / 1e7,
      status: 'unsurveyed',
      priority: '中',
      source: 'field',
      survey_date: S.today(),
      surveyor: F.settings.who || '',
      created_at: now, updated_at: now,
      dirty: 1
    }, extra || {});

    D.elevation(lon, lat).then(function (e) {
      if (e != null) { t.elev = Math.round(e * 10) / 10; save(t); }
    });

    return S.put('trees', t).then(function () {
      F.trees.push(t);
      addMark(t);
      buzz(40);
      toast('登録しました。内容を記録してください。');
      openTree(t.uuid);
      maybeSync();
      return t;
    });
  }

  function save(t) {
    t.updated_at = S.stamp();
    t.dirty = 1;
    return S.put('trees', t).then(function () {
      putLocal(t);
      refreshMark(t);
      maybeSync();
    });
  }

  /** 画面が持っている配列を、いま保存したものと同じ中身にそろえる。
   *
   *  同期のあと loadLocal() が配列を作り直すので、
   *  開きっぱなしのパネルが古いオブジェクトを掴んだままになることがある。
   *  そのまま直すと「押したのに変わらない」が起きるので、必ずここを通す。 */
  function putLocal(t) {
    for (var i = 0; i < F.trees.length; i++) {
      if (F.trees[i].uuid === t.uuid) { F.trees[i] = t; return; }
    }
    F.trees.push(t);
  }

  var syncTimer;
  function maybeSync() {
    Y.countPending();
    if (!navigator.onLine) return;
    clearTimeout(syncTimer);
    syncTimer = setTimeout(function () {
      Y.syncNow({ quiet: true }).then(function (r) {
        if (r && r.ok) reloadAfterSync();
      });
    }, 4000);
  }

  /* ================================================================ 木の詳細 */
  function openTree(uuid, keepScroll) {
    var t = F.trees.filter(function (x) { return x.uuid === uuid; })[0];
    if (!t) return;
    var top = keepScroll ? $('#sheet-body').scrollTop : 0;
    F.sel = uuid;
    if (!keepScroll) F.map.setView([t.lat, t.lon], Math.max(F.map.getZoom(), 17));
    sheet(t.code || '木の記録', function (b) { buildTreeSheet(b, t); },
          { kind: 'tree' });
    if (top) $('#sheet-body').scrollTop = top;
  }

  function buildTreeSheet(b, t) {
    /* --- 位置と距離 --- */
    var head = el('div', { class: 'row mb' });
    var d = F.here ? G.distance(F.here.lon, F.here.lat, t.lon, t.lat) : null;
    var br = F.here ? G.bearing(F.here.lon, F.here.lat, t.lon, t.lat) : null;
    head.appendChild(el('div', { class: 'grow' }, [
      el('div', { class: 'small muted', text:
        (t.rinpan ? t.rinpan + '林班 ' : '') + (t.kosyoban ? t.kosyoban + '小班' : '')
        + (t.elev != null ? '　標高 ' + Math.round(t.elev) + 'm' : '') }),
      el('div', { class: 'small', text: t.lat.toFixed(6) + ', ' + t.lon.toFixed(6) })
    ]));
    if (d != null) {
      head.appendChild(el('div', { style: 'text-align:right' }, [
        el('div', { style: 'font-size:20px;font-weight:800', text: fmtDist(d) }),
        el('div', { class: 'tiny muted', text: G.compassName(br) + 'へ' })
      ]));
    }
    b.appendChild(head);

    if (t.dirty) {
      b.appendChild(el('div', { class: 'card warn small',
        text: '未送信。電波のあるところで自動的に送られます。' }));
    }

    /* --- 調査ステータス --- */
    b.appendChild(el('p', { class: 'label', text: '現地調査ステータス' }));
    var chips = el('div', { class: 'chips mb' });
    (F.cat.status || DEFAULT_CAT.status).forEach(function (s) {
      var btn = el('button', { class: 'pick' + (t.status === s.code ? ' on' : ''),
        onclick: function () {
          t.status = s.code;
          if (!t.survey_date) t.survey_date = S.today();
          if (!t.surveyor) t.surveyor = F.settings.who || '';
          save(t);
          buzz(25);
          openTree(t.uuid, true);
        } }, [
        el('span', { class: 'sw', style: 'background:' + s.color }),
        s.label
      ]);
      chips.appendChild(btn);
    });
    b.appendChild(chips);

    /* --- 到達できずのときは理由 --- */
    if (t.status === 'unreachable') {
      b.appendChild(field('到達できなかった理由', textarea(t.access_note || '',
        function (v) { t.access_note = v; save(t); },
        '例）笹薮1.5km・高低差100mの急斜面・熊の足跡')));
    }

    /* --- 調査項目（被害あり・保留のときに開く） --- */
    var show = t.status !== 'unsurveyed';
    if (show) {
      b.appendChild(el('hr', { class: 'sep' }));

      b.appendChild(field('樹種', pickRow(SPECIES, t.species, function (v) {
        t.species = v; save(t);
      }, true)));

      b.appendChild(field('胸高直径', dbhRow(t)));

      b.appendChild(field('フラス量', pickRow(FRASS, t.frass, function (v) {
        t.frass = v; save(t);
      })));

      b.appendChild(field('状況（あてはまるもの全部）', multiRow(COND, t.dieback, function (v) {
        t.dieback = v;
        t.leaf_color = v.indexOf('葉の変色') >= 0 ? 'あり' : (v ? 'なし' : '');
        save(t);
      })));

      b.appendChild(field('穿孔（カシナガの入った穴）', pickRow(BORING, t.boring, function (v) {
        t.boring = v; save(t);
      })));

      b.appendChild(field('周辺の林相', textarea(t.stand || '', function (v) {
        t.stand = v; save(t);
      }, '例）周辺にミズナラあり・カシワ混交')));
    }

    /* --- 写真 --- */
    b.appendChild(el('hr', { class: 'sep' }));
    b.appendChild(el('p', { class: 'label', text: '写真' }));
    var ph = el('div', { class: 'thumbs mb', id: 'tree-photos' });
    b.appendChild(ph);
    renderPhotos(ph, t);
    var file = el('input', { type: 'file', accept: 'image/*', capture: 'environment',
      style: 'display:none', multiple: true });
    file.addEventListener('change', function () {
      var list = Array.prototype.slice.call(file.files || []);
      file.value = '';
      addPhotos(t, list).then(function () { renderPhotos(ph, t); });
    });
    b.appendChild(file);
    b.appendChild(el('button', { class: 'btn acc block',
      onclick: function () { file.click(); } }, ['📷　写真を撮る／選ぶ']));

    /* --- メモ --- */
    b.appendChild(el('hr', { class: 'sep' }));
    b.appendChild(field('メモ', textarea(t.memo || '', function (v) {
      t.memo = v; save(t);
    }, '気づいたこと')));

    b.appendChild(field('調査日 / 調査者', el('div', { class: 'row' }, [
      el('input', { type: 'date', value: t.survey_date || S.today(),
        oninput: function (e) { t.survey_date = e.target.value; save(t); } }),
      el('input', { type: 'text', value: t.surveyor || '', placeholder: '調査者',
        oninput: function (e) { t.surveyor = e.target.value; save(t); } })
    ])));

    /* --- 位置を直す --- */
    b.appendChild(el('hr', { class: 'sep' }));
    b.appendChild(el('p', { class: 'label', text: '位置を直す' }));
    b.appendChild(el('div', { class: 'row mb' }, [
      el('button', { class: 'btn grow', onclick: function () {
        closeSheet();
        toast('地図を動かして、正しい場所で「ここへ移す」');
        startPick(function (lon, lat) {
          var moved = G.distance(t.lon, t.lat, lon, lat);
          t.lon = Math.round(lon * 1e7) / 1e7;
          t.lat = Math.round(lat * 1e7) / 1e7;
          t.loc_accuracy = '現地で地図上から補正';
          save(t).then(function () {
            toast(fmtDist(moved) + ' 動かしました');
            openTree(t.uuid);
          });
        }, 'ここへ移す');
      } }, ['地図で']),
      el('button', { class: 'btn grow', onclick: function () {
        openCoordEntry(function (lon, lat) {
          t.lon = Math.round(lon * 1e7) / 1e7;
          t.lat = Math.round(lat * 1e7) / 1e7;
          t.loc_accuracy = '緯度経度を入力して補正';
          save(t).then(function () { openTree(t.uuid); });
        });
      } }, ['緯度経度で']),
      el('button', { class: 'btn grow', onclick: function () {
        if (!F.here) { toast('現在地が取れていません', 'err'); return; }
        if (!confirm('この木の位置を、いまいる場所に合わせますか？')) return;
        t.lon = Math.round(F.here.lon * 1e7) / 1e7;
        t.lat = Math.round(F.here.lat * 1e7) / 1e7;
        t.gps_acc = Math.round(F.here.acc * 10) / 10;
        t.loc_accuracy = '現在地に合わせた（GPS ±' + Math.round(F.here.acc) + 'm）';
        save(t).then(function () { openTree(t.uuid); });
      } }, ['現在地へ'])
    ]));

    /* --- そのほか --- */
    b.appendChild(el('div', { class: 'row mt' }, [
      el('button', { class: 'btn grow', onclick: function () {
        var txt = t.lat.toFixed(6) + ', ' + t.lon.toFixed(6);
        if (navigator.clipboard) navigator.clipboard.writeText(txt);
        toast('座標をコピーしました：' + txt);
      } }, ['座標をコピー']),
      el('button', { class: 'btn dan grow', onclick: function () {
        if (!confirm((t.code || 'この木') + ' を消しますか？\n'
                     + '（事務所側からも消えます）')) return;
        t.deleted = 1;
        save(t).then(function () {
          F.trees = F.trees.filter(function (x) { return x.uuid !== t.uuid; });
          if (F.marks[t.uuid]) { F.layer.trees.removeLayer(F.marks[t.uuid]); }
          closeSheet();
          toast('消しました');
        });
      } }, ['消す'])
    ]));
  }

  function field(label, node) {
    return el('div', { class: 'field' }, [el('p', { class: 'label', text: label }), node]);
  }

  function pickRow(options, value, onchange, allowFree) {
    var wrap = el('div');
    var chips = el('div', { class: 'chips' });
    options.forEach(function (o) {
      chips.appendChild(el('button', { class: 'pick' + (value === o ? ' on' : ''),
        onclick: function (e) {
          var on = e.currentTarget.classList.contains('on');
          Array.prototype.forEach.call(chips.children, function (c) {
            c.classList.remove('on');
          });
          if (!on) e.currentTarget.classList.add('on');
          onchange(on ? '' : o);
          buzz(15);
        } }, [o]));
    });
    wrap.appendChild(chips);
    if (allowFree && value && options.indexOf(value) < 0) {
      wrap.appendChild(el('input', { type: 'text', class: 'mt', value: value,
        oninput: function (e) { onchange(e.target.value); } }));
    }
    return wrap;
  }

  function multiRow(options, value, onchange) {
    var cur = (value || '').split('・').filter(Boolean);
    var chips = el('div', { class: 'chips' });
    options.forEach(function (o) {
      chips.appendChild(el('button', { class: 'pick' + (cur.indexOf(o) >= 0 ? ' on' : ''),
        onclick: function (e) {
          var i = cur.indexOf(o);
          if (i >= 0) { cur.splice(i, 1); e.currentTarget.classList.remove('on'); }
          else { cur.push(o); e.currentTarget.classList.add('on'); }
          onchange(cur.join('・'));
          buzz(15);
        } }, [o]));
    });
    return chips;
  }

  function dbhRow(t) {
    var wrap = el('div');
    var input = el('input', { type: 'number', inputmode: 'decimal', step: '1',
      min: '0', max: '300', value: t.dbh_cm != null ? t.dbh_cm : '',
      placeholder: 'cm' });
    input.addEventListener('input', function () {
      t.dbh_cm = input.value === '' ? null : parseFloat(input.value);
      save(t);
    });
    var chips = el('div', { class: 'chips mt' });
    DBH_QUICK.forEach(function (n) {
      chips.appendChild(el('button', { class: 'pick', onclick: function () {
        input.value = n; t.dbh_cm = n; save(t); buzz(15);
      } }, [n + '']));
    });
    wrap.appendChild(el('div', { class: 'row' }, [
      el('div', { class: 'grow' }, [input]),
      el('span', { class: 'muted', text: 'cm' })
    ]));
    wrap.appendChild(chips);
    wrap.appendChild(el('p', { class: 'tiny muted mt', text:
      '幹まわりを測ったときは ÷3.14 した値を入れてください。' }));
    return wrap;
  }

  function textarea(value, onchange, placeholder) {
    var n = el('textarea', { placeholder: placeholder || '' });
    n.value = value || '';
    var t;
    n.addEventListener('input', function () {
      clearTimeout(t);
      t = setTimeout(function () { onchange(n.value); }, 500);
    });
    n.addEventListener('blur', function () { onchange(n.value); });
    return n;
  }

  /* ================================================================ 写真 */
  function renderPhotos(box, t) {
    box.innerHTML = '';
    S.byIndex('photos', 'tree', t.uuid).then(function (list) {
      list = list.filter(function (p) { return !p.deleted; });
      if (!list.length) {
        box.appendChild(el('div', { class: 'ph', text: 'まだありません' }));
        return;
      }
      list.forEach(function (p) {
        var w = el('div', { class: 'thumb-wrap' });
        var img = el('img', { alt: '' });
        if (p.blob) img.src = URL.createObjectURL(p.blob);
        else if (p.server) img.src = p.server;
        img.addEventListener('click', function () { viewPhoto(p, t); });
        w.appendChild(img);
        if (p.state === 'pending') w.appendChild(el('span', { class: 'up', text: '未送信' }));
        box.appendChild(w);
      });
    });
  }

  function addPhotos(t, files) {
    if (!files.length) return Promise.resolve();
    toast(files.length + '枚を取り込んでいます…');
    return files.reduce(function (chain, f) {
      return chain.then(function () { return shrink(f); }).then(function (blob) {
        var p = {
          uuid: S.uuid(), tree_uuid: t.uuid, blob: blob,
          filename: (t.code || 'tree') + '.jpg',
          caption: '', taken_at: S.stamp(),
          lon: F.here ? F.here.lon : null, lat: F.here ? F.here.lat : null,
          heading: F.heading != null ? Math.round(F.heading) : null,
          state: 'pending', created_at: S.stamp()
        };
        return S.put('photos', p);
      });
    }, Promise.resolve()).then(function () {
      toast('写真を保存しました');
      maybeSync();
    });
  }

  /** 現場の写真は5MB前後ある。長辺1600pxに縮めて、電波の細いところでも送れるようにする。
      元の解像度は現地判定には要らない（幹の穴が写ればよい）。 */
  function shrink(file, max) {
    max = max || 1600;
    var load = (window.createImageBitmap)
      ? createImageBitmap(file, { imageOrientation: 'from-image' })
      : new Promise(function (res, rej) {
          var img = new Image();
          img.onload = function () { res(img); };
          img.onerror = rej;
          img.src = URL.createObjectURL(file);
        });
    return load.then(function (im) {
      var w = im.width, h = im.height;
      var s = Math.min(1, max / Math.max(w, h));
      var cv = document.createElement('canvas');
      cv.width = Math.round(w * s); cv.height = Math.round(h * s);
      cv.getContext('2d').drawImage(im, 0, 0, cv.width, cv.height);
      if (im.close) im.close();
      return new Promise(function (res) {
        cv.toBlob(function (b) { res(b || file); }, 'image/jpeg', 0.82);
      });
    })['catch'](function () { return file; });
  }

  function viewPhoto(p, t) {
    sheet('写真', function (b) {
      var img = el('img', { style: 'width:100%;border-radius:12px' });
      img.src = p.blob ? URL.createObjectURL(p.blob) : p.server;
      b.appendChild(img);
      b.appendChild(el('p', { class: 'small muted mt', text:
        (p.taken_at || '') + (p.state === 'pending' ? '　（未送信）' : '') }));
      b.appendChild(field('説明', textarea(p.caption || '', function (v) {
        p.caption = v; S.put('photos', p);
      }, '例）幹の穴とフラス')));
      b.appendChild(el('button', { class: 'btn dan block mt', onclick: function () {
        if (!confirm('この写真を消しますか？')) return;
        p.deleted = 1;
        S.put('photos', p).then(function () {
          closeSheet();
          openTree(t.uuid);
        });
      } }, ['写真を消す']));
    });
  }

  /* ================================================================ トラック */
  function toggleTrack() {
    if (F.rec) stopTrack(); else startTrack();
  }

  function startTrack() {
    if (!F.here) { toast('現在地が取れてから始めてください', 'err'); return; }
    F.rec = {
      uuid: S.uuid(),
      name: S.stamp().slice(0, 16).replace('T', ' ') + ' の記録',
      surveyor: F.settings.who || '',
      started_at: S.stamp(),
      points: [], dist: 0, up: 0, lastElev: null,
      color: pickColor()
    };
    F.recLine = L.polyline([], { color: F.rec.color, weight: 5, opacity: .9 })
      .addTo(F.layer.tracks);
    $('#trackbar').hidden = false;
    $('#btn-track').classList.add('on');
    recordPoint(true);
    tickTrack();
    buzz([60, 40, 60]);
    toast('歩いた跡の記録を始めました');
  }

  var COLORS = ['#5f3dc4', '#1971c2', '#0ca678', '#e8590c', '#c2255c', '#5c940d'];
  function pickColor() { return COLORS[F.tracks.length % COLORS.length]; }

  function recordPoint(force) {
    if (!F.rec || !F.here) return;
    var pts = F.rec.points;
    var last = pts[pts.length - 1];
    var now = Date.now();
    if (!force && last) {
      var d = G.distance(last[0], last[1], F.here.lon, F.here.lat);
      // 止まっているときのGPSの揺れを拾わない。
      // 3m 動くか、20秒たつまでは書かない。
      if (d < 3 && now - last[3] < 20000) return;
      if (F.here.acc > 60) return;                 // 精度が悪すぎる点は捨てる
      F.rec.dist += d;
    }
    var e = D.elevationSync(F.here.lon, F.here.lat);
    if (e != null && e !== undefined) {
      if (F.rec.lastElev != null && e > F.rec.lastElev) F.rec.up += e - F.rec.lastElev;
      F.rec.lastElev = e;
    }
    pts.push([Math.round(F.here.lon * 1e6) / 1e6, Math.round(F.here.lat * 1e6) / 1e6,
              e != null && e !== undefined ? Math.round(e) : null, now,
              Math.round(F.here.acc)]);
    F.recLine.addLatLng([F.here.lat, F.here.lon]);
    renderTrackBar();
  }

  function tickTrack() {
    if (!F.rec) return;
    renderTrackBar();
    setTimeout(tickTrack, 1000);
  }

  function renderTrackBar() {
    if (!F.rec) return;
    $('#track-dist').textContent = fmtDist(F.rec.dist);
    var s = (Date.now() - new Date(F.rec.started_at.replace(/-/g, '/')).getTime()) / 1000;
    $('#track-time').textContent = fmtDur(s);
    $('#track-up').textContent = F.rec.up > 3 ? '↑' + Math.round(F.rec.up) + 'm' : '';
  }

  function stopTrack() {
    if (!F.rec) return;
    var r = F.rec;
    sheet('歩いた跡を残しますか？', function (b) {
      b.appendChild(el('dl', { class: 'kv mb' }, [
        el('dt', { text: '距離' }), el('dd', { text: fmtDist(r.dist) }),
        el('dt', { text: '時間' }), el('dd', { text: fmtDur(
          (Date.now() - new Date(r.started_at.replace(/-/g, '/')).getTime()) / 1000) }),
        el('dt', { text: '登り' }), el('dd', { text: Math.round(r.up) + ' m' }),
        el('dt', { text: '点の数' }), el('dd', { text: r.points.length + '' })
      ]));
      var nameIn = el('input', { type: 'text', value: r.name });
      b.appendChild(field('名前', nameIn));
      b.appendChild(el('button', { class: 'btn pri block', onclick: function () {
        r.name = nameIn.value || r.name;
        saveTrack(r);
        closeSheet();
      } }, ['残す（地図に薄く出ます）']));
      b.appendChild(el('button', { class: 'btn dan block mt', onclick: function () {
        discardTrack();
        closeSheet();
      } }, ['残さない（消す）']));
      b.appendChild(el('p', { class: 'tiny muted mt', text:
        '残した跡は、次に同じ場所へ入るときの目印になります。' +
        'どこまで見たかが分かるので、二度手間と踏み残しが減ります。' }));
    }, { onclose: function () { if (F.rec) { /* 決めるまで記録は続く */ } } });
  }

  function saveTrack(r) {
    var pts = r.points;
    var lons = pts.map(function (p) { return p[0]; });
    var lats = pts.map(function (p) { return p[1]; });
    var tr = {
      uuid: r.uuid, name: r.name, surveyor: r.surveyor,
      started_at: r.started_at, ended_at: S.stamp(),
      dist_m: Math.round(r.dist), up_m: Math.round(r.up),
      dur_s: Math.round((Date.now()
        - new Date(r.started_at.replace(/-/g, '/')).getTime()) / 1000),
      pt_n: pts.length, points: pts, color: r.color,
      minlon: Math.min.apply(null, lons), maxlon: Math.max.apply(null, lons),
      minlat: Math.min.apply(null, lats), maxlat: Math.max.apply(null, lats),
      created_at: r.started_at, updated_at: S.stamp(), dirty: 1
    };
    S.put('tracks', tr).then(function () {
      F.tracks.push(tr);
      endRec();
      drawTracks();
      toast('残しました（' + fmtDist(tr.dist_m) + '）');
      maybeSync();
    });
  }

  function discardTrack() {
    endRec();
    toast('消しました');
  }

  function endRec() {
    if (F.recLine) { F.layer.tracks.removeLayer(F.recLine); F.recLine = null; }
    F.rec = null;
    $('#trackbar').hidden = true;
    $('#btn-track').classList.remove('on');
  }

  function backToStart() {
    var r = F.rec;
    if (!r || !r.points.length || !F.here) { toast('まだ道が記録されていません'); return; }
    var s = r.points[0];
    var d = G.distance(F.here.lon, F.here.lat, s[0], s[1]);
    var br = G.bearing(F.here.lon, F.here.lat, s[0], s[1]);
    toast('スタートまで ' + fmtDist(d) + '　' + G.compassName(br) + '（' + Math.round(br) + '°）');
    buzz(60);
    F.map.fitBounds(L.latLngBounds([[F.here.lat, F.here.lon], [s[1], s[0]]]),
                    { padding: [60, 120] });
  }

  function openTrack(uuid) {
    var tr = F.tracks.filter(function (x) { return x.uuid === uuid; })[0];
    if (!tr) return;
    sheet(tr.name || '歩いた跡', function (b) {
      b.appendChild(el('dl', { class: 'kv mb' }, [
        el('dt', { text: '距離' }), el('dd', { text: fmtDist(tr.dist_m) }),
        el('dt', { text: '時間' }), el('dd', { text: fmtDur(tr.dur_s) }),
        el('dt', { text: '登り' }), el('dd', { text: (tr.up_m || 0) + ' m' }),
        el('dt', { text: '歩いた人' }), el('dd', { text: tr.surveyor || '―' }),
        el('dt', { text: '日時' }), el('dd', { text: tr.started_at || '' })
      ]));
      b.appendChild(el('button', { class: 'btn block', onclick: function () {
        F.map.fitBounds(L.latLngBounds([[tr.minlat, tr.minlon], [tr.maxlat, tr.maxlon]]),
                        { padding: [40, 40] });
        closeSheet();
      } }, ['この跡を画面に収める']));
      b.appendChild(el('button', { class: 'btn dan block mt', onclick: function () {
        if (!confirm('この跡を消しますか？')) return;
        tr.deleted = 1; tr.dirty = 1; tr.updated_at = S.stamp();
        S.put('tracks', tr).then(function () {
          F.tracks = F.tracks.filter(function (x) { return x.uuid !== tr.uuid; });
          drawTracks(); closeSheet(); maybeSync();
        });
      } }, ['消す']));
    });
  }

  /* ================================================================ 近くの木 */
  function openNear() {
    sheet('近くの木', function (b) {
      if (!F.here) {
        b.appendChild(el('p', { class: 'muted', text: '現在地が取れていません。' }));
        return;
      }
      var list = F.trees.map(function (t) {
        return { t: t, d: G.distance(F.here.lon, F.here.lat, t.lon, t.lat) };
      }).sort(function (a, c) { return a.d - c.d; }).slice(0, 40);

      var filt = el('div', { class: 'chips mb' });
      var only = F.nearFilter || '';
      [['', 'すべて']].concat((F.cat.status || DEFAULT_CAT.status).map(function (s) {
        return [s.code, s.label];
      })).forEach(function (p) {
        filt.appendChild(el('button', { class: 'pick' + (only === p[0] ? ' on' : ''),
          onclick: function () { F.nearFilter = p[0]; openNear(); } }, [p[1]]));
      });
      b.appendChild(filt);

      var box = el('div', { class: 'list' });
      var n = 0;
      list.forEach(function (r) {
        if (only && r.t.status !== only) return;
        n++;
        var st = statusOf(r.t.status);
        var br = G.bearing(F.here.lon, F.here.lat, r.t.lon, r.t.lat);
        box.appendChild(el('button', { class: 'item', onclick: function () {
          closeSheet(); openTree(r.t.uuid);
        } }, [
          el('span', { class: 'sw', style: 'background:' + st.color }),
          el('span', { class: 't' }, [
            el('b', { text: r.t.code || '（未採番）' }),
            el('span', { text: st.label
              + (r.t.species ? '・' + r.t.species : '')
              + (r.t.rinpan ? '・' + r.t.rinpan + '林班' : '')
              + (r.t.dirty ? '・未送信' : '') })
          ]),
          el('span', { class: 'd' }, [
            fmtDist(r.d),
            el('div', { class: 'tiny muted', text: G.compassName(br) })
          ])
        ]));
      });
      if (!n) box.appendChild(el('p', { class: 'muted', text: '該当する木がありません。' }));
      b.appendChild(box);
    });
  }

  /* ================================================================ レイヤー */
  function openLayers() {
    sheet('重ねる情報', function (b) {
      b.appendChild(el('p', { class: 'label', text: '背景' }));
      var bm = el('div', { class: 'chips mb' });
      [['pale', '淡色地図'], ['std', '地形図'], ['photo', '空中写真'],
       ['relief', '陰影起伏'], ['slopemap', '傾斜量']].forEach(function (p) {
        bm.appendChild(el('button', { class: 'pick' + (F.settings.basemap === p[0] ? ' on' : ''),
          onclick: function () {
            F.settings.basemap = p[0]; saveSettings(); applyBasemap(); openLayers();
          } }, [p[1]]));
      });
      b.appendChild(bm);

      b.appendChild(sw('標高200m以下', '森町が重点管理地域としている範囲。赤く塗ります。',
        F.settings.elev200, function (on) {
          F.settings.elev200 = on; saveSettings();
          if (!F.layer.elev200) return;
          if (on) F.layer.elev200.addTo(F.map); else F.map.removeLayer(F.layer.elev200);
        }));

      b.appendChild(sw('町の境界', '森町とまわりの町の境目。',
        F.settings.boundary, function (on) {
          F.settings.boundary = on; saveSettings();
          if (F.layer.boundaryG) {
            on ? F.layer.vector.addLayer(F.layer.boundaryG)
               : F.layer.vector.removeLayer(F.layer.boundaryG);
          } else if (on) loadVectorLayers();
        }));

      b.appendChild(sw('林班界', '森林計画の区画。',
        F.settings.rinpan, function (on) {
          F.settings.rinpan = on; saveSettings();
          if (F.layer.rinpanG) {
            on ? F.layer.vector.addLayer(F.layer.rinpanG)
               : F.layer.vector.removeLayer(F.layer.rinpanG);
          } else if (on) loadVectorLayers();
        }));

      b.appendChild(sw('歩いた跡', '前に歩いた道を薄く出します。',
        F.settings.tracks, function (on) {
          F.settings.tracks = on; saveSettings(); drawTracks();
        }));

      /* オルソ */
      b.appendChild(el('hr', { class: 'sep' }));
      b.appendChild(el('p', { class: 'label', text: 'ドローンのオルソ' }));
      var sites = ((F.boot && F.boot.sites) || []).filter(function (s) {
        return s.tile_ext && s.minlon != null;
      });
      if (!sites.length) {
        b.appendChild(el('p', { class: 'muted small', text: 'まだありません。' }));
      }
      (F.orthoLayers || []).forEach(function (lay) {
        var s = lay._site;
        var on = F.settings['ortho_' + s.id] !== false && F.settings.ortho;
        b.appendChild(sw(s.name || s.id,
          (s.flown_on ? '撮影 ' + s.flown_on : '撮影日不明')
          + (s.px_size ? '　地上画素 ' + (s.px_size * 100).toFixed(0) + 'cm' : ''),
          on, function (v) {
            F.settings['ortho_' + s.id] = v;
            F.settings.ortho = true;
            saveSettings();
            if (v) lay.addTo(F.layer.ortho); else F.layer.ortho.removeLayer(lay);
          }, function () {
            F.map.fitBounds(L.latLngBounds([s.minlat, s.minlon], [s.maxlat, s.maxlon]));
            closeSheet();
          }));
      });
      if (sites.length) {
        var op = el('input', { type: 'range', min: '20', max: '100',
          value: F.settings.orthoOpacity || 100 });
        op.addEventListener('input', function () {
          F.settings.orthoOpacity = +op.value;
          (F.orthoLayers || []).forEach(function (l) { l.setOpacity(op.value / 100); });
        });
        op.addEventListener('change', saveSettings);
        b.appendChild(field('オルソの濃さ', op));
      }
    });
  }

  function sw(name, note, on, onchange, onJump) {
    var input = el('input', { type: 'checkbox' });
    input.checked = !!on;
    input.addEventListener('change', function () { onchange(input.checked); });
    var t = el('div', { class: 'grow' }, [
      el('div', { class: 'n', text: name }),
      note ? el('div', { class: 's', text: note }) : null
    ]);
    if (!onJump) return el('label', { class: 'switch' }, [t, input]);
    // 名前を押すとその場所へ飛ぶ。スイッチと押し分けたいので label にしない。
    t.style.cursor = 'pointer';
    t.addEventListener('click', onJump);
    return el('div', { class: 'switch' }, [t, input]);
  }

  /* ================================================================ 事前ダウンロード */
  function openDownload() {
    sheet('地図を先に入れておく', function (b) {
      b.appendChild(el('p', { class: 'muted small mb', text:
        'いま画面に映っている範囲を、端末の中に保存します。'
        + '保存した範囲は電波が無くても表示されます。' }));

      var zmaxIn = el('select');
      [[16, '16（だいたいの位置）'], [17, '17'], [18, '18（推奨・木が見える）'],
       [19, '19'], [20, '20（オルソを最大まで）']].forEach(function (p) {
        var o = el('option', { value: p[0], text: p[1] });
        if (p[0] === 18) o.selected = true;
        zmaxIn.appendChild(o);
      });
      b.appendChild(field('どこまで細かく', zmaxIn));

      var opts = {
        base: true, dem: true, ortho: true, layers: true
      };
      b.appendChild(sw('背景地図', '選んでいる背景（' + F.settings.basemap + '）',
        true, function (v) { opts.base = v; est(); }));
      b.appendChild(sw('標高データ', '標高200m以下の塗りと、現在地の標高に使います',
        true, function (v) { opts.dem = v; est(); }));
      b.appendChild(sw('ドローンのオルソ', 'いま出しているオルソ',
        true, function (v) { opts.ortho = v; est(); }));
      b.appendChild(sw('林班界・町界', 'GeoJSON（1回だけ）',
        true, function (v) { opts.layers = v; }));

      var info = el('div', { class: 'card mt' });
      b.appendChild(info);
      var bar = el('div', { class: 'prog mt', style: 'display:none' },
                   [el('i')]);
      b.appendChild(bar);
      var msg = el('p', { class: 'small mt' });
      b.appendChild(msg);

      var go = el('button', { class: 'btn pri block mt' }, ['この範囲を保存する']);
      b.appendChild(go);
      var stop = el('button', { class: 'btn dan block mt', style: 'display:none' },
                    ['中止']);
      b.appendChild(stop);

      b.appendChild(el('hr', { class: 'sep' }));
      var usage = el('p', { class: 'small muted' });
      b.appendChild(usage);
      S.usage().then(function (u) {
        if (!u) return;
        usage.textContent = '端末の中で使っている容量 '
          + (u.used / 1048576).toFixed(0) + ' MB'
          + (u.quota ? '（使える上限のめやす ' + (u.quota / 1048576).toFixed(0) + ' MB）' : '');
      });
      b.appendChild(el('button', { class: 'btn block mt', onclick: function () {
        if (!confirm('保存した地図をすべて消しますか？\n記録した木や写真は消えません。')) return;
        caches['delete']('naragare-tiles-v1').then(function () {
          toast('保存した地図を消しました');
        });
      } }, ['保存した地図を消す']));

      var list = [];
      function est() {
        var zmax = +zmaxIn.value;
        list = tileList(F.map.getBounds(), zmax, opts);
        var mb = list.length * 0.024;
        info.innerHTML = '';
        info.appendChild(el('b', { text: list.length.toLocaleString() + ' 枚' }));
        info.appendChild(el('span', { class: 'muted',
          text: '　だいたい ' + (mb < 1 ? '1未満' : mb.toFixed(0)) + ' MB' }));
        info.appendChild(el('p', { class: 'tiny muted', text:
          list.length > 20000
            ? '多すぎます。地図を広げすぎているか、細かくしすぎています。'
            : 'Wi-Fi のあるところで実行してください。' }));
      }
      zmaxIn.addEventListener('change', est);
      est();

      var cancelled = false;
      stop.addEventListener('click', function () { cancelled = true; });

      go.addEventListener('click', function () {
        if (!list.length) return;
        cancelled = false;
        go.style.display = 'none';
        stop.style.display = '';
        bar.style.display = '';
        var done = 0, fail = 0;
        var extra = opts.layers ? cacheLayers() : Promise.resolve();
        extra.then(function () {
          return runQueue(list, 6, function (url) {
            if (cancelled) return Promise.resolve();
            return cacheOne(url)['catch'](function () { fail++; })
              .then(function () {
                done++;
                if (done % 5 === 0 || done === list.length) {
                  bar.firstChild.style.width = (done / list.length * 100) + '%';
                  msg.textContent = done + ' / ' + list.length
                    + (fail ? '（取れなかったもの ' + fail + '）' : '');
                }
              });
          });
        }).then(function () {
          go.style.display = ''; stop.style.display = 'none';
          msg.textContent = cancelled
            ? '中止しました（' + done + '枚まで保存）'
            : '保存しました（' + (done - fail) + '枚）'
              + (fail ? '　' + fail + '枚は元が無いか届きませんでした' : '');
          toast(cancelled ? '中止しました' : '地図を保存しました', 'ok');
          S.usage().then(function (u) {
            if (u) usage.textContent = '端末の中で使っている容量 '
              + (u.used / 1048576).toFixed(0) + ' MB';
          });
        });
      });
    });
  }

  /** 保存するタイルの一覧を作る */
  function tileList(bounds, zmax, opts) {
    var out = [];
    var zmin = Math.max(10, Math.min(13, F.map.getZoom()));
    var ext = F.settings.basemap === 'photo' ? 'jpg' : 'png';

    var push = function (tpl, z0, z1, bnds) {
      for (var z = z0; z <= z1; z++) {
        var a = lonLatToTile(bnds.getWest(), bnds.getNorth(), z);
        var c = lonLatToTile(bnds.getEast(), bnds.getSouth(), z);
        for (var x = a[0]; x <= c[0]; x++) {
          for (var y = a[1]; y <= c[1]; y++) {
            out.push(tpl.replace('{z}', z).replace('{x}', x).replace('{y}', y));
          }
        }
      }
    };

    if (opts.base) {
      push('/api/tile/' + F.settings.basemap + '/{z}/{x}/{y}.' + ext,
           zmin, Math.min(zmax, 18), bounds);
    }
    if (opts.dem) {
      push('/api/tile/dem_png/{z}/{x}/{y}.png', 14, 14, bounds);
    }
    if (opts.ortho) {
      (F.orthoLayers || []).forEach(function (lay) {
        if (!F.map.hasLayer(lay) && !F.layer.ortho.hasLayer(lay)) return;
        var s = lay._site;
        var sb = L.latLngBounds([s.minlat, s.minlon], [s.maxlat, s.maxlon]);
        if (!bounds.intersects(sb)) return;
        var inter = L.latLngBounds(
          [Math.max(sb.getSouth(), bounds.getSouth()), Math.max(sb.getWest(), bounds.getWest())],
          [Math.min(sb.getNorth(), bounds.getNorth()), Math.min(sb.getEast(), bounds.getEast())]);
        push('/data/tiles/' + s.id + '/{z}/{x}/{y}.' + s.tile_ext,
             Math.max(s.zmin || 12, zmin), Math.min(s.zmax || 20, zmax), inter);
      });
    }
    return out;
  }

  function lonLatToTile(lon, lat, z) {
    var n = Math.pow(2, z);
    var x = Math.floor((lon + 180) / 360 * n);
    var r = lat * Math.PI / 180;
    var y = Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * n);
    return [Math.max(0, Math.min(n - 1, x)), Math.max(0, Math.min(n - 1, y))];
  }

  function cacheOne(url) {
    return caches.open('naragare-tiles-v1').then(function (c) {
      return c.match(url).then(function (hit) {
        if (hit) return null;
        return fetch(url, { cache: 'no-store' }).then(function (r) {
          if (!r.ok) throw new Error('miss');
          return c.put(url, r);
        });
      });
    });
  }

  function cacheLayers() {
    var urls = [];
    var l = (F.boot && F.boot.layers) || {};
    ['boundary', 'rinpan', 'contour200'].forEach(function (k) {
      if (l[k]) urls.push(l[k]);
    });
    return caches.open('naragare-data-v3').then(function (c) {
      return Promise.all(urls.map(function (u) {
        return fetch(u).then(function (r) { return r.ok ? c.put(u, r) : null; })
          ['catch'](function () {});
      }));
    })['catch'](function () {});
  }

  function runQueue(items, n, fn) {
    var i = 0;
    var worker = function () {
      if (i >= items.length) return Promise.resolve();
      var it = items[i++];
      return fn(it).then(worker);
    };
    var ws = [];
    for (var k = 0; k < n; k++) ws.push(worker());
    return Promise.all(ws);
  }

  /* ================================================================ 同期 */
  function renderSync(st) {
    var dot = $('#sync-dot'), txt = $('#sync-text');
    var n = st.pendingTotal || 0;
    dot.className = 'dot ' + (st.busy ? 'busy'
      : st.lastError ? 'err'
      : (st.reachable ? 'on' : 'off'));
    // 上の帯は狭い。細かい進み具合は同期パネルのほうに出す。
    var label = st.busy ? '同期中'
      : !navigator.onLine ? 'オフライン'
      : st.reachable === false ? '未接続'
      : n > 0 ? '未送信'
      : '同期済';
    txt.innerHTML = '';
    txt.appendChild(document.createTextNode(label));
    if (n > 0) {
      var b = el('span', { class: 'n', text: n + '' });
      txt.appendChild(b);
    }
  }

  function openSync() {
    sheet('同期', function (b) {
      var st = Y.state;
      var card = el('div', { class: 'card' });
      b.appendChild(card);
      var draw = function () {
        card.innerHTML = '';
        var p = st.pending || {};
        card.appendChild(el('b', { text: (st.pendingTotal || 0) + ' 件が未送信' }));
        card.appendChild(el('dl', { class: 'kv mt' }, [
          el('dt', { text: '木' }), el('dd', { text: (p.trees || 0) + '' }),
          el('dt', { text: '調査' }), el('dd', { text: (p.surveys || 0) + '' }),
          el('dt', { text: '歩いた跡' }), el('dd', { text: (p.tracks || 0) + '' }),
          el('dt', { text: '写真' }), el('dd', { text: (p.photos || 0) + '' })
        ]));
        if (st.lastError) {
          card.appendChild(el('p', { class: 'small', style: 'color:#c92a2a',
            text: st.lastError }));
        }
        if (st.phase) card.appendChild(el('p', { class: 'small', text: st.phase }));
      };
      draw();

      var btn = el('button', { class: 'btn pri block mt', onclick: function () {
        btn.disabled = true;
        Y.syncNow().then(function (r) {
          btn.disabled = false;
          draw();
          if (r.ok) { toast('同期しました', 'ok'); reloadAfterSync(); }
          else toast(r.error || '同期できませんでした', 'err');
        });
      } }, ['いま同期する']);
      b.appendChild(btn);
      Y.on(draw);

      S.getKV('last_sync').then(function (v) {
        b.appendChild(el('p', { class: 'small muted mt', text:
          v ? '最後に同期したのは ' + new Date(v).toLocaleString('ja-JP')
            : 'まだ一度も同期していません' }));
      });

      b.appendChild(el('hr', { class: 'sep' }));
      b.appendChild(el('p', { class: 'small muted', text:
        '圏外では、書いたものは端末の中にたまります。'
        + '電波かWi-Fiが来ると自動で送られます。'
        + '端末を替える前には必ず同期してください。' }));
    });
  }

  /* ================================================================ メニュー */
  function openMenu() {
    sheet('メニュー', function (b) {
      b.appendChild(field('担当者名', (function () {
        var i = el('input', { type: 'text', value: F.settings.who || '',
          placeholder: '氏名（記録に残ります）' });
        i.addEventListener('change', function () {
          F.settings.who = i.value.trim();
          saveSettings();
          S.setKV('who', F.settings.who);
        });
        return i;
      })()));

      b.appendChild(el('button', { class: 'btn block mb', onclick: function () {
        closeSheet(); openDownload();
      } }, ['🗺　地図を先に入れておく']));

      b.appendChild(el('button', { class: 'btn block mb', onclick: function () {
        closeSheet(); openSync();
      } }, ['🔄　同期']));

      b.appendChild(el('button', { class: 'btn block mb', onclick: function () {
        closeSheet(); openTracks();
      } }, ['👣　歩いた跡の一覧']));

      b.appendChild(el('button', { class: 'btn block mb', onclick: function () {
        closeSheet(); openStats();
      } }, ['📊　いまの状況']));

      if (F.needCompassPermission) {
        b.appendChild(el('button', { class: 'btn block mb', onclick: function () {
          askCompass();
        } }, ['🧭　方位を使えるようにする']));
      }

      b.appendChild(el('hr', { class: 'sep' }));
      b.appendChild(sw('画面を消さない', '調査中に暗くならないようにします（電池を使います）',
        F.settings.keepScreenOn, function (v) {
          F.settings.keepScreenOn = v; saveSettings();
          if (v) keepAwake();
          else if (F.wakeLock) { F.wakeLock.release(); F.wakeLock = null; }
        }));
      b.appendChild(sw('近づいたら知らせる', '未調査の木に25m以内へ入ると振動します',
        !!F.settings.proximity, function (v) {
          F.settings.proximity = v ? 25 : 0; saveSettings();
        }));

      b.appendChild(el('hr', { class: 'sep' }));
      b.appendChild(el('p', { class: 'small muted', html:
        '出典：国土地理院タイル／北海道オープンデータ（森林計画関係資料）／'
        + '歴史的行政区域データセットβ版（CODH）<br>'
        + 'オルソの絶対位置には水平±3〜5mのずれがあります。'
        + '境界の確定・図面の作成には使えません。' }));
      b.appendChild(el('p', { class: 'tiny muted mt', id: 'build-info' }));
      if (F.boot && F.boot.build) {
        $('#build-info').textContent = '版 ' + F.boot.build;
      }
    });
  }

  function openTracks() {
    sheet('歩いた跡', function (b) {
      if (!F.tracks.length) {
        b.appendChild(el('p', { class: 'muted', text: 'まだありません。' }));
        return;
      }
      var box = el('div', { class: 'list' });
      F.tracks.slice().sort(function (a, c) {
        return (c.started_at || '').localeCompare(a.started_at || '');
      }).forEach(function (tr) {
        box.appendChild(el('button', { class: 'item', onclick: function () {
          closeSheet(); openTrack(tr.uuid);
        } }, [
          el('span', { class: 'sw', style: 'background:' + (tr.color || '#5f3dc4') }),
          el('span', { class: 't' }, [
            el('b', { text: tr.name || '（無題）' }),
            el('span', { text: (tr.started_at || '').slice(0, 16)
              + (tr.surveyor ? '・' + tr.surveyor : '')
              + (tr.dirty ? '・未送信' : '') })
          ]),
          el('span', { class: 'd', text: fmtDist(tr.dist_m) })
        ]));
      });
      b.appendChild(box);
    });
  }

  function openStats() {
    sheet('いまの状況', function (b) {
      var by = {};
      F.trees.forEach(function (t) {
        by[t.status] = (by[t.status] || 0) + 1;
      });
      var box = el('div', { class: 'list mb' });
      (F.cat.status || DEFAULT_CAT.status).forEach(function (s) {
        box.appendChild(el('div', { class: 'item' }, [
          el('span', { class: 'sw', style: 'background:' + s.color }),
          el('span', { class: 't' }, [el('b', { text: s.label })]),
          el('span', { class: 'd', text: (by[s.code] || 0) + '' })
        ]));
      });
      b.appendChild(box);
      b.appendChild(el('p', { class: 'small muted', text:
        '端末が持っている ' + F.trees.length + ' 本の内訳です。'
        + '事務所側の全体は、同期したあとの数になります。' }));

      var dl = el('dl', { class: 'kv mt' });
      dl.appendChild(el('dt', { text: '歩いた跡' }));
      dl.appendChild(el('dd', { text: F.tracks.length + ' 本' }));
      var tot = F.tracks.reduce(function (a, c) { return a + (c.dist_m || 0); }, 0);
      dl.appendChild(el('dt', { text: '歩いた距離' }));
      dl.appendChild(el('dd', { text: fmtDist(tot) }));
      dl.appendChild(el('dt', { text: '標高タイル' }));
      dl.appendChild(el('dd', { text: D.cached() + ' 枚を保持' }));
      b.appendChild(dl);
    });
  }

  /* ================================================================ 配線 */
  function wire() {
    $('#btn-menu').addEventListener('click', openMenu);
    $('#pill-sync').addEventListener('click', openSync);
    $('#pill-gps').addEventListener('click', function () {
      if (!F.here) { toast('現在地を探しています…'); return; }
      openHere();
    });
    $('#btn-locate').addEventListener('click', function () {
      if (!F.here) { toast('現在地を探しています…'); return; }
      setFollow(true);
      F.autoPan = true;
      F.map.setView([F.here.lat, F.here.lon], Math.max(F.map.getZoom(), 17));
      setTimeout(function () { F.autoPan = false; }, 400);
    });
    $('#btn-layers').addEventListener('click', openLayers);
    $('#btn-compass').addEventListener('click', function () {
      if (F.needCompassPermission) { askCompass(); return; }
      if (F.heading == null) { toast('方位が取れていません'); return; }
      toast('いま向いているのは ' + G.compassName(F.heading)
            + '（' + Math.round(F.heading) + '°）');
    });
    $('#btn-add').addEventListener('click', openAdd);
    $('#btn-track').addEventListener('click', toggleTrack);
    $('#btn-near').addEventListener('click', openNear);
    $('#track-stop').addEventListener('click', stopTrack);
    $('#track-back').addEventListener('click', backToStart);
    $('#sheet-close').addEventListener('click', closeSheet);
    $('#scrim').addEventListener('click', closeSheet);
    F.map.on('zoomend', function () {
      var r = radiusFor(F.map.getZoom());
      Object.keys(F.marks).forEach(function (k) { F.marks[k].setRadius(r); });
    });
    setFollow(true);

    // 画面に戻ってきたら、たまっている分を送りに行く
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState !== 'visible') return;
      Y.countPending().then(function (n) {
        if (n > 0 && navigator.onLine) {
          Y.syncNow({ quiet: true }).then(function (r) {
            if (r && r.ok) reloadAfterSync();
          });
        }
      });
    });

    // 未送信のまま閉じようとしたら止める
    window.addEventListener('beforeunload', function (e) {
      if ((Y.state.pendingTotal || 0) > 0 && navigator.onLine) {
        e.preventDefault();
        e.returnValue = '';
      }
    });
  }

  function openHere() {
    sheet('現在地', function (b) {
      var h = F.here;
      var xy = G.toXY(h.lon, h.lat);
      var dl = el('dl', { class: 'kv' });
      var add = function (k, v) {
        dl.appendChild(el('dt', { text: k }));
        dl.appendChild(el('dd', { text: v }));
      };
      add('緯度経度', h.lat.toFixed(6) + ', ' + h.lon.toFixed(6));
      add('度分秒', G.dms(h.lat, true) + ' ' + G.dms(h.lon, false));
      add('XI系', 'X=' + xy[1].toFixed(1) + ' Y=' + xy[0].toFixed(1));
      add('GPS誤差', '±' + Math.round(h.acc) + ' m');
      if (F.heading != null) {
        add('向き', G.compassName(F.heading) + '（' + Math.round(F.heading) + '°）');
      }
      b.appendChild(dl);
      var elevLine = el('p', { class: 'small mt', text: '標高を調べています…' });
      b.appendChild(elevLine);
      D.elevation(h.lon, h.lat).then(function (e) {
        if (e == null) { elevLine.textContent = '標高データがありません（未ダウンロード）'; return; }
        elevLine.textContent = '標高 ' + Math.round(e) + ' m'
          + (e <= 200 ? '　― 重点管理地域（200m以下）の中です' : '　― 200mより上です');
        elevLine.style.color = e <= 200 ? '#c92a2a' : '';
      });
      b.appendChild(el('button', { class: 'btn block mt', onclick: function () {
        var t = h.lat.toFixed(6) + ', ' + h.lon.toFixed(6);
        if (navigator.clipboard) navigator.clipboard.writeText(t);
        toast('コピーしました：' + t);
      } }, ['座標をコピー']));
      b.appendChild(el('button', { class: 'btn pri block mt', onclick: function () {
        closeSheet(); addHere();
      } }, ['ここに木を登録']));
    });
  }

  /* ================================================================ */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else boot();

  window.Field = F;   // 動作確認用
})();
