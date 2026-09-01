/* ナラ枯れビューアー ------------------------------------------------------
   ローカルのサーバー（server.py）とだけ通信する。外部への接続は
   背景地図をローカルに落としていない場合の地理院タイルだけ。          */
'use strict';

const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = s => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const pad0 = s => String(s || '').replace(/^0+/, '') || '0';

const S = {
  boot: null, site: null, trees: [], byId: new Map(), sel: null,
  filter: { status: new Set(), priority: new Set(), rinpan: '', q: '', nara: false },
  mode: null,            // null | 'add' | 'measure'
  map: null, layers: {}, markers: new Map(),
  naraLoaded: false, here: null, selMarker: null, picked: new Set(), cmp: null
};

const PRI_COLOR = { '高': '#ff6b6b', '中': '#ffa94d', '低': '#8b9bb0' };

/* ---------------------------------------------------------------- 通信 */
async function api(path, opt) {
  const r = await fetch('/api/' + path, opt);
  if (!r.ok) {
    let m = r.statusText;
    try { m = (await r.json()).error || m; } catch (e) { }
    throw new Error(m);
  }
  return r.json();
}
function toast(msg, err) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'on' + (err ? ' err' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = ''), err ? 4200 : 2200);
}

/* ------------------------------------------------------------ 座標の整形 */
const fmt = {
  deg: (lat, lon) => `${lat.toFixed(7)}, ${lon.toFixed(7)}`,
  dms(v, isLat) {
    const h = isLat ? (v < 0 ? 'S' : 'N') : (v < 0 ? 'W' : 'E');
    const a = Math.abs(v);
    const d = Math.floor(a);
    const m = Math.floor((a - d) * 60);
    const s = ((a - d) * 60 - m) * 60;
    return `${d}°${String(m).padStart(2, '0')}′${s.toFixed(2).padStart(5, '0')}″${h}`;
  },
  xy: (x, y) => `X ${x.toFixed(2)}  Y ${y.toFixed(2)}`,
};

function copyText(text, btn) {
  const done = () => {
    if (btn) { btn.classList.add('ok'); btn.textContent = '✓'; }
    setTimeout(() => { if (btn) { btn.classList.remove('ok'); btn.textContent = '⧉'; } }, 1100);
    toast('コピーしました: ' + text);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, () => fallback());
  } else fallback();
  function fallback() {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); }
    catch (e) { toast('コピーできませんでした', true); }
    document.body.removeChild(ta);
  }
}

/* ---------------------------------------------------------------- 起動 */
async function boot() {
  S.boot = await api('bootstrap');
  $('#who').value = localStorage.getItem('who') || '';
  $('#who').addEventListener('change', e => localStorage.setItem('who', e.target.value.trim()));

  const d = S.boot.deadline;
  const el = $('#deadline');
  el.textContent = `処理期限 ${d.date} まで あと ${d.days_left} 日`;
  el.classList.toggle('urgent', d.days_left <= 60);

  buildSites();
  buildFilters();
  buildLegend();
  initMap();
  bindUI();
  bindImport();
  bindBulk();
  bindPhotos();
  bindCompare();
  renderInfo();
  await reload();
}

/* ---------------------------------------------------------------- サイト */
function buildSites() {
  const box = $('#sites');
  const sites = S.boot.sites || [];
  let h = `<button class="site on" data-id="">
      <b>森町全域</b><span>登録 ${S.boot.tree_total} 件</span></button>`;
  for (const s of sites) {
    const hasTile = s.zmax != null;
    h += `<button class="site" data-id="${esc(s.id)}">
      <b>${esc(s.name || s.id)}</b>
      <span>${hasTile ? esc(s.flown_on || '撮影日不明') : '記録のみ'}・${s.tree_count} 件${
        s.rinpan ? '・' + pad0(s.rinpan) + '林班' : ''}</span></button>`;
  }
  if (!sites.some(s => s.zmax != null)) {
    h += `<p class="hint" style="padding:4px 9px 8px">
      オルソが未登録です。右上の「＋ オルソを取り込む」から追加できます。</p>`;
  }
  box.innerHTML = h;
  $$('.site', box).forEach(b => b.addEventListener('click', () => {
    $$('.site', box).forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    S.site = b.dataset.id || null;
    const s = sites.find(x => x.id === S.site);
    setOrtho(s);
    reload().then(() => { if (s) fitSite(s); });
  }));
}

function fitSite(s) {
  if (!s || s.minlat == null) return;
  S.map.fitBounds([[s.minlat, s.minlon], [s.maxlat, s.maxlon]], { padding: [26, 26] });
}

/* ---------------------------------------------------------------- 絞り込み */
function buildFilters() {
  $('#f-status').innerHTML = S.boot.status.map(s =>
    `<button class="chip" data-v="${s.code}"><i class="dot" style="background:${s.color}"></i>${esc(s.label)}<span class="n"></span></button>`).join('');
  $('#f-priority').innerHTML = S.boot.priority.map(p =>
    `<button class="chip" data-v="${p}"><i class="dot" style="background:${PRI_COLOR[p]}"></i>${p}<span class="n"></span></button>`).join('');

  const bind = (sel, key) => $$(sel + ' .chip').forEach(b => b.addEventListener('click', () => {
    const v = b.dataset.v;
    S.filter[key].has(v) ? S.filter[key].delete(v) : S.filter[key].add(v);
    b.classList.toggle('on');
    reload();
  }));
  bind('#f-status', 'status');
  bind('#f-priority', 'priority');

  let t;
  $('#q').addEventListener('input', e => {
    clearTimeout(t);
    t = setTimeout(() => { S.filter.q = e.target.value.trim(); reload(); }, 260);
  });
  $('#f-rinpan').addEventListener('change', e => { S.filter.rinpan = e.target.value; reload(); });
  $('#f-nara').addEventListener('change', e => { S.filter.nara = e.target.checked; reload(); });
  $('#reset-filter').addEventListener('click', () => {
    S.filter = { status: new Set(), priority: new Set(), rinpan: '', q: '', nara: false };
    $$('.chip').forEach(c => c.classList.remove('on'));
    $('#q').value = ''; $('#f-rinpan').value = ''; $('#f-nara').checked = false;
    reload();
  });
}

function qs() {
  const p = new URLSearchParams();
  if (S.site) p.set('site', S.site);
  if (S.filter.status.size) p.set('status', [...S.filter.status].join(','));
  if (S.filter.priority.size) p.set('priority', [...S.filter.priority].join(','));
  if (S.filter.rinpan) p.set('rinpan', S.filter.rinpan);
  if (S.filter.q) p.set('q', S.filter.q);
  if (S.filter.nara) p.set('nara', '1');
  return p;
}

function buildLegend() {
  const st = S.boot.status.map(s =>
    `<div class="row"><i class="sw" style="background:${s.color}"></i>${esc(s.label)}</div>`).join('');
  $('#legend').innerHTML =
    `<div class="cap">丸の色＝現地調査のステータス</div>${st}
     <div class="cap" style="margin-top:6px">丸の大きさ＝優先度（大きいほど高い）</div>
     <div class="row"><i class="sw" style="background:#3a4553;border-color:#ffd43b"></i>金の縁＝オルソ上で位置を調整済み</div>
     <div class="row"><i class="sw sq" style="background:#7048e8;opacity:.5"></i>ナラ類の小班（森林簿）</div>
     <div class="row"><i class="sw" style="background:#ffd43b;border-radius:2px;height:4px;border:none"></i>標高200m 等高線</div>
     <div class="cap">この線より海側（低い側）が森町の重点管理地域</div>`;
}

/* ---------------------------------------------------------------- 地図 */
function initMap() {
  const map = L.map('map', { zoomControl: true, preferCanvas: true, maxZoom: 22 })
    .setView([42.1085, 140.5747], 11);
  S.map = map;
  L.control.scale({ imperial: false }).addTo(map);

  const GSI = 'https://cyberjapandata.gsi.go.jp/xyz';
  const attr = '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">国土地理院</a>';
  const local = S.boot.basemap_local || {};

  // ローカルに取り込んだタイルを優先し、そこに無いものだけ地理院から取りに行く。
  const FallbackTile = L.TileLayer.extend({
    createTile(coords, done) {
      const img = document.createElement('img');
      img.alt = '';
      L.DomEvent.on(img, 'load', L.Util.bind(this._tileOnLoad, this, done, img));
      L.DomEvent.on(img, 'error', () => {
        if (!img._fellBack && this.options.remoteUrl) {
          img._fellBack = true;
          img.src = L.Util.template(this.options.remoteUrl, coords);
          return;
        }
        this._tileOnError(done, img, new Error('tile not available'));
      });
      img.src = this.getTileUrl(coords);
      return img;
    }
  });
  const BLANK = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
  const mk = (name, ext, remote) => local[name]
    ? new FallbackTile(`/data/basemap/${name}/{z}/{x}/{y}.${ext}`,
      { maxZoom: 22, maxNativeZoom: 18, attribution: attr, remoteUrl: remote, errorTileUrl: BLANK })
    : L.tileLayer(remote,
      { maxZoom: 22, maxNativeZoom: 18, attribution: attr, errorTileUrl: BLANK });

  S.layers.base = {
    photo: mk('photo', 'jpg', GSI + '/seamlessphoto/{z}/{x}/{y}.jpg'),
    pale: mk('pale', 'png', GSI + '/pale/{z}/{x}/{y}.png'),
    std: mk('std', 'png', GSI + '/std/{z}/{x}/{y}.png'),
    none: L.tileLayer('', { attribution: '' })
  };
  S.layers.base.photo.addTo(map);
  S.curBase = 'photo';

  S.layers.orthoGroup = L.layerGroup().addTo(map);
  S.layers.rinpan = L.layerGroup();
  S.layers.kosyoban = L.layerGroup();
  S.layers.nara = L.layerGroup();
  S.layers.risk = L.layerGroup();
  S.layers.contour = L.layerGroup();
  S.layers.trees = L.layerGroup().addTo(map);
  S.layers.tools = L.layerGroup().addTo(map);
  setOrtho(null);

  map.on('click', onMapClick);
  map.on('mousemove', e => showCursor(e.latlng));
  map.on('zoomend moveend', () => {
    $('#sb-zoom').textContent = 'ズーム ' + S.map.getZoom();
    if ($('#l-kosyoban').checked) loadKosyoban();
  });
  $('#sb-zoom').textContent = 'ズーム ' + map.getZoom();
}

let cursorTimer = null;
function showCursor(ll) {
  clearTimeout(cursorTimer);
  cursorTimer = setTimeout(async () => {
    $('#sb-latlon').textContent = `緯 ${ll.lat.toFixed(6)}  経 ${ll.lng.toFixed(6)}`;
    try {
      const r = await api(`locate?lon=${ll.lng.toFixed(7)}&lat=${ll.lat.toFixed(7)}`);
      $('#sb-xy').textContent = `XI系 ${fmt.xy(r.x, r.y)}`
        + (r.rinpan ? `　${pad0(r.rinpan)}-${pad0(r.kosyoban)} 小班` : '');
      const e = $('#sb-elev');
      if (r.ground_elev == null) { e.textContent = '標高 —'; e.className = 'sb-item'; }
      else {
        e.textContent = `標高 ${r.ground_elev.toFixed(1)} m`
          + (r.ground_elev <= 200 ? '（重点管理）' : '');
        e.className = 'sb-item' + (r.ground_elev <= 200 ? ' warn' : '');
      }
    } catch (e) { }
  }, 90);
}

function setOrtho(site) {
  const g = S.layers.orthoGroup;
  g.clearLayers();
  const list = (site ? [site] : (S.boot.sites || [])).filter(s => s.zmax != null);
  S.orthoLayers = [];
  const op = ($('#opacity') ? $('#opacity').value / 100 : 1);
  for (const s of list) {
    const l = L.tileLayer(s.tiles, {
      minZoom: s.zmin || 10, maxZoom: 22, maxNativeZoom: s.zmax,
      bounds: [[s.minlat, s.minlon], [s.maxlat, s.maxlon]],
      opacity: op,
      attribution: 'ドローンオルソ（' + esc(s.name || s.id) + '）'
    });
    S.orthoLayers.push(l);
    if (!$('#l-ortho') || $('#l-ortho').checked) l.addTo(g);
  }
}

/* ---------------------------------------------------------------- 読み込み */
async function reload() {
  const r = await api('trees?' + qs().toString());
  S.trees = r.trees;
  S.byId = new Map(S.trees.map(t => [t.id, t]));
  drawTrees();
  renderList();
  updateCounts();
  fillRinpan();
  updateBulkBar();
  const ex = qs();
  $('#ex-csv').href = '/api/export.csv?' + ex.toString();
  $('#ex-geo').href = '/api/export.geojson?' + ex.toString();
  if ($('#view-board').classList.contains('on')) renderBoard();
}

function fillRinpan() {
  const sel = $('#f-rinpan');
  const set = new Map();
  for (const t of S.trees) {
    if (!t.rinpan) continue;
    set.set(t.rinpan, (set.get(t.rinpan) || 0) + 1);
  }
  const cur = sel.value;
  sel.innerHTML = '<option value="">すべて</option>' +
    [...set.entries()].sort((a, b) => a[0].localeCompare(b[0]))
      .map(([k, v]) => `<option value="${esc(k)}">${esc(pad0(k))} 林班（${v}）</option>`).join('');
  sel.value = cur;
}

function updateCounts() {
  const n = S.trees.length;
  const hi = S.trees.filter(t => t.priority === '高').length;
  const un = S.trees.filter(t => t.status === 'unsurveyed').length;
  $('#count').innerHTML = `表示 <b>${n}</b> 件　優先度高 <b style="color:#ff8787">${hi}</b>　未調査 <b>${un}</b>`;
  $('#list-count').textContent = `${n} 件`;
  const cs = {}, cp = {};
  for (const t of S.trees) {
    cs[t.status] = (cs[t.status] || 0) + 1;
    cp[t.priority] = (cp[t.priority] || 0) + 1;
  }
  $$('#f-status .chip').forEach(c => $('.n', c).textContent = cs[c.dataset.v] || 0);
  $$('#f-priority .chip').forEach(c => $('.n', c).textContent = cp[c.dataset.v] || 0);
}

/* ---------------------------------------------------------------- マーカー */
function statusColor(code) {
  const s = S.boot.status.find(x => x.code === code);
  return s ? s.color : '#888';
}
function drawTrees() {
  const g = S.layers.trees;
  g.clearLayers();
  S.markers.clear();
  for (const t of S.trees) {
    if (t.lat == null) continue;
    const r = t.priority === '高' ? 9 : t.priority === '中' ? 7 : 5.5;
    const m = L.circleMarker([t.lat, t.lon], {
      radius: r,
      color: t.orig_lat != null ? '#ffd43b' : '#ffffffcc',   // 位置を直したものは金の縁
      weight: t.orig_lat != null ? 2.4 : 1.6,
      fillColor: statusColor(t.status), fillOpacity: .92
    });
    m.on('click', ev => { L.DomEvent.stop(ev); openDetail(t.id); });
    m.bindTooltip(
      `${esc(t.code)}／優先度${esc(t.priority)}<br>${
        t.rinpan ? esc(pad0(t.rinpan)) + '林班' : '林班不明'}${
        t.kosyoban ? '-' + esc(pad0(t.kosyoban)) + '小班' : ''}${
        t.orig_lat != null ? '<br><span style="color:#ffd43b">位置を調整済み</span>' : ''}`,
      { direction: 'top', opacity: .95 });
    m.addTo(g);
    S.markers.set(t.id, m);
  }
}

/* ---------------------------------------------------------------- レイヤー */
async function loadRinpan() {
  if (S.layers.rinpanLoaded) return;
  const url = (S.boot.layers || {}).rinpan;
  if (!url) { throw new Error('林班界データがありません（初期設定.bat を実行）'); }
  const gj = await (await fetch(url)).json();
  L.geoJSON(gj, {
    style: { color: '#74c0fc', weight: 1.1, fill: false, opacity: .75 },
    onEachFeature: (f, l) => l.bindTooltip(
      `${esc(f.properties['林班'])} 林班（${esc(f.properties['地区'])}）<br>${f.properties['面積ha']} ha`,
      { sticky: true })
  }).addTo(S.layers.rinpan);
  S.layers.rinpanLoaded = true;
}

async function loadContour() {
  if (S.contourLoaded) return;
  const url = (S.boot.layers || {}).contour200;
  if (!url) throw new Error('等高線データがありません（tools/build_contour.py を実行）');
  const gj = await (await fetch(url)).json();
  // 太い暗線の上に細い明線を重ねて、空中写真の上でも読めるようにする
  L.geoJSON(gj, { style: { color: '#000000', weight: 4.5, opacity: .35, fill: false } })
    .addTo(S.layers.contour);
  L.geoJSON(gj, {
    style: { color: '#ffd43b', weight: 1.8, opacity: .95, fill: false, dashArray: '9,5' },
    onEachFeature: (f, l) => l.bindTooltip('標高 200 m（この線より海側が重点管理地域）',
      { sticky: true })
  }).addTo(S.layers.contour);
  S.contourLoaded = true;
}

async function loadNara() {
  if (S.naraLoaded) return;
  const gj = await (await fetch('/api/kosyoban?nara=1&bbox=140.30,41.95,140.90,42.35')).json();
  L.geoJSON(gj, {
    style: f => {
      const risk = (f.properties.elev != null && f.properties.elev <= 200);
      return { color: risk ? '#ff922b' : '#9775fa', weight: risk ? 1.6 : .9,
               fillColor: risk ? '#e8590c' : '#7048e8', fillOpacity: .38, opacity: .85 };
    },
    filter: f => f.properties.nara_rank === 2,
    onEachFeature: (f, l) => l.bindTooltip(koTip(f.properties), { sticky: true })
  }).addTo(S.layers.nara);
  L.geoJSON(gj, {
    style: () => ({ color: '#7048e8', weight: .6, fillColor: '#7048e8', fillOpacity: .10, opacity: .5 }),
    filter: f => f.properties.nara_rank === 1 && f.properties.elev != null && f.properties.elev <= 200,
    onEachFeature: (f, l) => l.bindTooltip(koTip(f.properties), { sticky: true })
  }).addTo(S.layers.risk);
  S.naraLoaded = true;
}

function koTip(p) {
  const sp = (p.species || []).map(s => s.name + (s.ratio ? '(' + s.ratio + ')' : '')).join('・');
  return `<b>${esc(pad0(p.rinpan))}林班-${esc(pad0(p.kosyoban))}小班</b><br>
    ${esc(p.rinshu || '')} ${esc(sp || p.sp_main || '')}<br>
    ${p.age ? '林齢' + esc(p.age) + '年　' : ''}${p.height ? '樹高' + esc(p.height) + 'm　' : ''}${p.area_ha ? esc(p.area_ha) + 'ha' : ''}<br>
    ${p.elev != null ? '標高 ' + esc(p.elev) + ' m' + (p.elev <= 200 ? '（重点管理）' : '') : ''}`;
}

async function loadKosyoban() {
  if (S.map.getZoom() < 14) { S.layers.kosyoban.clearLayers(); return; }
  const b = S.map.getBounds();
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map(v => v.toFixed(5)).join(',');
  if (S.koLast === bbox) return;
  S.koLast = bbox;
  const gj = await (await fetch('/api/kosyoban?bbox=' + bbox)).json();
  S.layers.kosyoban.clearLayers();
  L.geoJSON(gj, {
    style: { color: '#adb5bd', weight: .7, fill: true, fillOpacity: .02, opacity: .55 },
    onEachFeature: (f, l) => l.bindTooltip(koTip(f.properties), { sticky: true })
  }).addTo(S.layers.kosyoban);
}

/* ================================================================ 道具 */
function setMode(mode) {
  S.mode = mode;
  $('#t-add').classList.toggle('on', mode === 'add');
  $('#t-measure').classList.toggle('on', mode === 'measure');
  $('#map').classList.toggle('picking', mode === 'add');
  $('#map').classList.toggle('measuring', mode === 'measure');
  const bar = $('#hint-bar');
  if (mode === 'add') {
    $('#hint-text').textContent = 'オルソの上で、木のある場所をタップしてください';
    bar.hidden = false;
  } else if (mode === 'measure') {
    $('#hint-text').textContent = '地図を順にタップすると距離が出ます。ダブルクリックで終了';
    bar.hidden = false;
    measureStart();
  } else {
    bar.hidden = true;
    measureClear();
  }
}

function onMapClick(e) {
  if (S.move && S.moveClick) { S.moveClick(e.latlng); return; }
  if (S.mode === 'add') { addTreeAt(e.latlng.lng, e.latlng.lat); return; }
  if (S.mode === 'measure') { measureAdd(e.latlng); return; }
}

/* ---------- 木の登録 ---------- */
async function addTreeAt(lon, lat) {
  try {
    const t = await api('tree', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lon, lat, site: (S.site && S.site !== 'genchi') ? S.site : 'genchi',
        status: 'damaged', priority: '高',
        loc_accuracy: 'オルソ上でタップして指定',
        _who: $('#who').value.trim()
      })
    });
    toast(`${t.code} を登録しました。ピンをドラッグして位置を直せます`);
    setMode(null);
    await reload();
    openDetail(t.id);
  } catch (e) { toast(e.message, true); }
}

/* ---------- 距離をはかる ---------- */
function measureStart() { S.measure = { pts: [], line: null, marks: [] }; }
function measureClear() {
  if (!S.measure) return;
  if (S.measure.line) S.layers.tools.removeLayer(S.measure.line);
  S.measure.marks.forEach(m => S.layers.tools.removeLayer(m));
  S.measure = null;
}
function measureAdd(ll) {
  const M = S.measure;
  M.pts.push(ll);
  const dot = L.circleMarker(ll, { radius: 4, color: '#4dabf7', fillColor: '#fff', fillOpacity: 1, weight: 2 })
    .addTo(S.layers.tools);
  M.marks.push(dot);
  if (M.line) S.layers.tools.removeLayer(M.line);
  M.line = L.polyline(M.pts, { color: '#4dabf7', weight: 2.5, dashArray: '6,5' }).addTo(S.layers.tools);
  let total = 0;
  for (let i = 1; i < M.pts.length; i++) total += M.pts[i - 1].distanceTo(M.pts[i]);
  if (M.pts.length > 1) {
    const last = M.pts[M.pts.length - 1];
    dot.bindTooltip(
      `${total < 1000 ? total.toFixed(1) + ' m' : (total / 1000).toFixed(3) + ' km'}`,
      { permanent: true, direction: 'right', className: 'measure-label' }).openTooltip();
  }
}

/* ---------- 現在地 ---------- */
function gps() {
  if (!navigator.geolocation) { toast('この端末では現在地を取得できません', true); return; }
  toast('現在地を取得しています…');
  navigator.geolocation.getCurrentPosition(pos => {
    const { latitude: lat, longitude: lon, accuracy: acc } = pos.coords;
    if (S.here) { S.layers.tools.removeLayer(S.here.m); S.layers.tools.removeLayer(S.here.c); }
    const m = L.marker([lat, lon], {
      icon: L.divIcon({ className: '', html: '<div class="here-dot"></div>', iconSize: [16, 16], iconAnchor: [8, 8] })
    }).addTo(S.layers.tools);
    const c = L.circle([lat, lon], { radius: acc, color: '#4dabf7', weight: 1, fillOpacity: .08 })
      .addTo(S.layers.tools);
    S.here = { m, c, lat, lon, acc };
    S.map.setView([lat, lon], Math.max(S.map.getZoom(), 18));
    toast(`現在地（誤差 約${Math.round(acc)}m）`);
    if (S.sel) openDetail(S.sel);
  }, err => toast('現在地を取得できませんでした: ' + err.message, true),
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 10000 });
}

function bearing(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180;
  const y = Math.sin((lon2 - lon1) * r) * Math.cos(lat2 * r);
  const x = Math.cos(lat1 * r) * Math.sin(lat2 * r)
    - Math.sin(lat1 * r) * Math.cos(lat2 * r) * Math.cos((lon2 - lon1) * r);
  return (Math.atan2(y, x) / r + 360) % 360;
}
const COMPASS = ['北', '北北東', '北東', '東北東', '東', '東南東', '南東', '南南東',
  '南', '南南西', '南西', '西南西', '西', '西北西', '北西', '北北西'];

/* ---------- 座標へ飛ぶ ---------- */
function parseCoord(s) {
  s = (s || '').trim();
  // 度分秒
  const dms = s.match(/(\d+)[°\s]+(\d+)[′'\s]+([\d.]+)[″"\s]*([NS])[,\s]+(\d+)[°\s]+(\d+)[′'\s]+([\d.]+)[″"\s]*([EW])/i);
  if (dms) {
    const lat = (+dms[1] + dms[2] / 60 + dms[3] / 3600) * (/s/i.test(dms[4]) ? -1 : 1);
    const lon = (+dms[5] + dms[6] / 60 + dms[7] / 3600) * (/w/i.test(dms[8]) ? -1 : 1);
    return { lat, lon };
  }
  const m = s.match(/(-?[\d.]+)\s*[,\s]\s*(-?[\d.]+)/);
  if (!m) return null;
  const a = parseFloat(m[1]), b = parseFloat(m[2]);
  // 緯度経度らしいか、平面直角座標らしいか
  if (Math.abs(a) <= 90 && Math.abs(b) <= 180 && Math.abs(b) > 100) return { lat: a, lon: b };
  if (Math.abs(b) <= 90 && Math.abs(a) <= 180 && Math.abs(a) > 100) return { lat: b, lon: a };
  return { xy: [a, b] };
}

async function gotoCoord() {
  const v = parseCoord($('#goto-input').value);
  if (!v) { toast('座標を読み取れませんでした', true); return; }
  let lat = v.lat, lon = v.lon;
  if (v.xy) {
    // 平面直角座標 -> 緯度経度 はサーバーに逆変換が無いので二分探索は避け、
    // locate は緯度経度入力なので、近似の初期値から数回だけ補正する
    const [x, y] = v.xy;
    lat = 44 + y / 111000;
    lon = 140.25 + x / (111320 * Math.cos(lat * Math.PI / 180));
    for (let i = 0; i < 6; i++) {
      const r = await api(`locate?lon=${lon.toFixed(7)}&lat=${lat.toFixed(7)}`);
      const dx = x - r.x, dy = y - r.y;
      if (Math.abs(dx) < 0.05 && Math.abs(dy) < 0.05) break;
      lat += dy / 111000;
      lon += dx / (111320 * Math.cos(lat * Math.PI / 180));
    }
  }
  closeModal('#modal-goto');
  S.map.setView([lat, lon], Math.max(S.map.getZoom(), 18));
  const ping = L.circleMarker([lat, lon], { radius: 14, color: '#ffd43b', weight: 3, fill: false })
    .addTo(S.layers.tools);
  setTimeout(() => S.layers.tools.removeLayer(ping), 2600);
  toast(`移動しました: ${lat.toFixed(6)}, ${lon.toFixed(6)}`);
}

/* ---------------------------------------------------------------- UI */
function openModal(sel) { $(sel).hidden = false; }
function closeModal(sel) { $(sel).hidden = true; }

function bindUI() {
  ['#l-contour', '#l-rinpan', '#l-kosyoban', '#l-nara', '#l-risk', '#f-nara']
    .forEach(s => $(s).checked = false);
  $('#l-ortho').checked = true;
  $('#opacity').value = 100;
  $('#basemap').value = 'photo';

  $$('#tabs button').forEach(b => b.addEventListener('click', () => showView(b.dataset.view)));

  const toggle = (id, layer, loader) => $(id).addEventListener('change', async e => {
    if (e.target.checked) {
      if (loader) {
        try { await loader(); }
        catch (err) { toast(err.message, true); e.target.checked = false; return; }
      }
      S.map.addLayer(layer);
    } else S.map.removeLayer(layer);
  });
  toggle('#l-contour', S.layers.contour, loadContour);
  toggle('#l-rinpan', S.layers.rinpan, loadRinpan);
  toggle('#l-kosyoban', S.layers.kosyoban, loadKosyoban);
  toggle('#l-nara', S.layers.nara, loadNara);
  toggle('#l-risk', S.layers.risk, loadNara);

  $('#l-ortho').addEventListener('change', e => {
    const g = S.layers.orthoGroup;
    g.clearLayers();
    if (e.target.checked) S.orthoLayers.forEach(l => l.addTo(g));
  });
  $('#opacity').addEventListener('input', e => {
    const v = e.target.value / 100;
    $('#opacity-v').textContent = e.target.value;
    (S.orthoLayers || []).forEach(l => l.setOpacity(v));
  });
  $('#basemap').addEventListener('change', e => {
    S.map.removeLayer(S.layers.base[S.curBase]);
    S.curBase = e.target.value;
    S.layers.base[S.curBase].addTo(S.map);
    S.layers.base[S.curBase].bringToBack();
  });

  $('#t-add').addEventListener('click', () => setMode(S.mode === 'add' ? null : 'add'));
  $('#t-measure').addEventListener('click', () => setMode(S.mode === 'measure' ? null : 'measure'));
  $('#t-gps').addEventListener('click', gps);
  $('#t-goto').addEventListener('click', () => { openModal('#modal-goto'); $('#goto-input').focus(); });
  $('#t-full').addEventListener('click', toggleFull);
  $('#hint-cancel').addEventListener('click', () => {
    if (S.move) { const id = S.move.id; endMove(id); return; }
    setMode(null);
  });
  $('#goto-go').addEventListener('click', gotoCoord);
  $('#goto-input').addEventListener('keydown', e => { if (e.key === 'Enter') gotoCoord(); });

  $('#side-toggle').addEventListener('click', () => {
    $('#app').classList.toggle('side-off');
    setTimeout(() => S.map.invalidateSize(), 220);
  });
  $('#btn-import').addEventListener('click', () => openModal('#modal-import'));
  $('#btn-photos').addEventListener('click', () => openModal('#modal-photos'));
  $('#btn-help').addEventListener('click', () => openModal('#modal-help'));
  $$('[data-close]').forEach(b => b.addEventListener('click', e => {
    e.target.closest('.modal').hidden = true;
  }));
  $$('.modal').forEach(m => m.addEventListener('click', e => {
    if (e.target === m) m.hidden = true;
  }));

  $('#sort').addEventListener('change', renderList);
  S.map.on('dblclick', () => { if (S.mode === 'measure') setMode(null); });

  document.addEventListener('keydown', e => {
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) {
      if (e.key === 'Escape') e.target.blur();
      return;
    }
    const k = e.key.toLowerCase();
    if (e.key === 'Escape') {
      const open = $$('.modal').find(m => !m.hidden);
      if (open) { open.hidden = true; return; }
      if (S.move) { const id = S.move.id; endMove(id); return; }
      if (S.mode) { setMode(null); return; }
      if (S.cmp) { stopCompare(); return; }
      closeDetail();
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const map = {
      a: () => setMode(S.mode === 'add' ? null : 'add'),
      m: () => setMode(S.mode === 'measure' ? null : 'measure'),
      g: gps,
      j: () => { openModal('#modal-goto'); $('#goto-input').focus(); },
      i: () => openModal('#modal-import'),
      p: () => openModal('#modal-photos'),
      c: () => S.cmp ? stopCompare() : startCompare(),
      f: toggleFull,
      o: () => { $('#l-ortho').checked = !$('#l-ortho').checked; $('#l-ortho').dispatchEvent(new Event('change')); },
      '[': () => $('#side-toggle').click(),
      '?': () => openModal('#modal-help'),
      '1': () => showView('map'), '2': () => showView('list'),
      '3': () => showView('board'), '4': () => showView('info'),
    };
    if (map[k]) { e.preventDefault(); map[k](); }
  });
}

function showView(v) {
  $$('#tabs button').forEach(x => x.classList.toggle('on', x.dataset.view === v));
  $$('.view').forEach(s => s.classList.remove('on'));
  $('#view-' + v).classList.add('on');
  if (v === 'map') setTimeout(() => S.map.invalidateSize(), 60);
  if (v === 'board') renderBoard();
}

function toggleFull() {
  if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
  else document.exitFullscreen?.();
  setTimeout(() => S.map.invalidateSize(), 350);
}

/* ================================================ オルソの取り込み */
function bindImport() {
  const pick = $('#imp-picker');
  $('#imp-browse').addEventListener('click', () => {
    pick.hidden = !pick.hidden;
    if (!pick.hidden) browseTo(lastDir());
  });
  $('#pick-up').addEventListener('click', () => {
    if (S.browse && S.browse.parent) browseTo(S.browse.parent);
  });
  $('#imp-start').addEventListener('click', startImport);
  $('#imp-cancel').addEventListener('click', async () => {
    if (!confirm('取り込みを中止しますか？')) return;
    await api('import/cancel', { method: 'POST' });
  });
  $('#imp-done').addEventListener('click', () => location.reload());
  $('#imp-path').addEventListener('change', e => guessSite(e.target.value));
  pollImport();
}

function lastDir() { return localStorage.getItem('lastdir') || ''; }

async function browseTo(path) {
  let r;
  try { r = await api('browse?path=' + encodeURIComponent(path || '')); }
  catch (e) { toast(e.message, true); return; }
  S.browse = r;
  if (path) localStorage.setItem('lastdir', path);
  $('#pick-path').textContent = r.path || 'ドライブを選んでください';
  $('#pick-drives').innerHTML = (r.drives || [])
    .map(d => `<button data-p="${esc(d)}">${esc(d)}</button>`).join('');
  $$('#pick-drives button').forEach(b =>
    b.addEventListener('click', () => browseTo(b.dataset.p)));

  const sep = (r.path || '').includes('\\') ? '\\' : '/';
  const dirs = (r.dirs || []).map(d =>
    `<div data-dir="${esc((r.path ? r.path.replace(/[\\/]$/, '') + sep : '') + d)}">
       <span class="fi">📁</span>${esc(d)}</div>`).join('');
  const files = (r.files || []).map(f => {
    const ok = f.geotiff || f.world;
    return `<div data-file="${esc((r.path ? r.path.replace(/[\\/]$/, '') + sep : '') + f.name)}">
      <span class="fi">🗺</span>${esc(f.name)}
      ${ok ? '<span class="tag2">位置情報あり</span>'
           : '<span class="tag2 no">ワールドファイルなし</span>'}
      ${f.dsm ? '<span class="tag2">DSMあり</span>' : ''}
      <span class="sz">${(f.size / 1e6).toFixed(1)} MB</span></div>`;
  }).join('');
  $('#pick-list').innerHTML = (dirs + files) || '<div style="color:var(--fg3)">（オルソはありません）</div>';
  $$('#pick-list [data-dir]').forEach(d =>
    d.addEventListener('click', () => browseTo(d.dataset.dir)));
  $$('#pick-list [data-file]').forEach(d =>
    d.addEventListener('click', () => {
      $('#imp-path').value = d.dataset.file;
      guessSite(d.dataset.file);
      $('#imp-picker').hidden = true;
    }));
}

function guessSite(path) {
  const base = (path || '').split(/[\\/]/).pop().replace(/\.[^.]+$/, '')
    .replace(/[_-]?ortho.*$/i, '');
  if (base && !$('#imp-site').value) $('#imp-site').value = base;
  if (base && !$('#imp-name').value) $('#imp-name').value = base;
}

async function startImport() {
  const opts = {
    path: $('#imp-path').value.trim(),
    site: $('#imp-site').value.trim(),
    name: $('#imp-name').value.trim(),
    date: $('#imp-date').value,
    note: $('#imp-note').value.trim(),
    detect: $('#imp-detect').checked,
  };
  if (!opts.path) { toast('オルソのファイルを選んでください', true); return; }
  try {
    await api('import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts)
    });
    $('#imp-form').hidden = true;
    $('#imp-progress').hidden = false;
    $('#imp-done').hidden = true;
    $('#imp-cancel').hidden = false;
  } catch (e) { toast(e.message, true); }
}

async function pollImport() {
  try {
    const s = await api('import/status');
    if (s.running || s.rc !== null) {
      if ($('#modal-import').hidden === false) {
        $('#imp-form').hidden = true;
        $('#imp-progress').hidden = false;
      }
      $('#imp-phase').textContent = s.phase || '';
      $('#imp-elapsed').textContent = s.elapsed ? `${Math.floor(s.elapsed / 60)}分${s.elapsed % 60}秒` : '';
      $('#imp-bar').style.width = (s.percent || 0) + '%';
      const log = $('#imp-log');
      const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
      log.textContent = (s.lines || []).join('\n');
      if (atBottom) log.scrollTop = log.scrollHeight;
      if (!s.running && s.rc !== null) {
        $('#imp-cancel').hidden = true;
        $('#imp-done').hidden = false;
        $('#imp-done').textContent = s.rc === 0 ? '閉じて反映する' : '閉じる';
      }
    }
  } catch (e) { }
  setTimeout(pollImport, 1200);
}

/* ---------------------------------------------------------------- 一覧 */
function renderList() {
  const cols = [
    ['pick', ''], ['code', '記録ID'], ['priority', '優先度'], ['status', 'ステータス'],
    ['rinpan', '林班-小班'], ['sp_main', '森林簿樹種'], ['elev', '標高m'],
    ['latlon', '緯度経度'], ['survey_date', '調査日'], ['surveyor', '調査者'],
    ['access_note', '到達状況'], ['memo', 'メモ']
  ];
  $('#list thead').innerHTML = '<tr>' + cols.map(c =>
    c[0] === 'pick' ? '<th class="pick"></th>' : `<th>${c[1]}</th>`).join('') + '</tr>';

  const key = $('#sort').value;
  const arr = S.trees.slice();
  const ord = { '高': 0, '中': 1, '低': 2 };
  arr.sort((a, b) => {
    if (key === 'rinpan') return String(a.rinpan || '').localeCompare(String(b.rinpan || '')) ||
      String(a.kosyoban || '').localeCompare(String(b.kosyoban || ''));
    if (key === 'updated') return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
    if (key === 'status') return String(a.status).localeCompare(String(b.status));
    return (ord[a.priority] ?? 3) - (ord[b.priority] ?? 3);
  });

  const lab = c => (S.boot.status.find(s => s.code === c) || {}).label || c;
  $('#list tbody').innerHTML = arr.map(t => `<tr data-id="${t.id}" class="${
      S.sel === t.id ? 'sel ' : ''}${S.picked.has(t.id) ? 'picked' : ''}">
    <td class="pick"><input type="checkbox" ${S.picked.has(t.id) ? 'checked' : ''}></td>
    <td class="mono">${esc(t.code)}</td>
    <td class="pri pri-${esc(t.priority)}">${esc(t.priority)}</td>
    <td><span class="tag" style="background:${statusColor(t.status)}22;color:${statusColor(t.status)}">${esc(lab(t.status))}</span></td>
    <td>${t.rinpan ? esc(pad0(t.rinpan)) : '—'}${t.kosyoban ? '-' + esc(pad0(t.kosyoban)) : ''}</td>
    <td>${esc(t.sp_main || '—')}</td>
    <td>${t.elev != null ? esc(t.elev) : '—'}</td>
    <td class="mono">${t.lat != null ? t.lat.toFixed(6) + ', ' + t.lon.toFixed(6) : '—'}</td>
    <td>${esc(t.survey_date || '')}</td>
    <td>${esc(t.surveyor || '')}</td>
    <td class="wrap">${esc((t.access_note || '').slice(0, 40))}</td>
    <td class="wrap">${esc((t.memo || '').split('\n')[0].slice(0, 50))}</td></tr>`).join('');

  $$('#list tbody .pick input').forEach(cb => cb.addEventListener('click', e => {
    e.stopPropagation();
    const id = Number(cb.closest('tr').dataset.id);
    cb.checked ? S.picked.add(id) : S.picked.delete(id);
    cb.closest('tr').classList.toggle('picked', cb.checked);
    updateBulkBar();
  }));
  $$('#list tbody tr').forEach(tr => tr.addEventListener('click', () => {
    const id = Number(tr.dataset.id);
    openDetail(id);
    const t = S.byId.get(id);
    if (t && t.lat != null) {
      showView('map');
      setTimeout(() => { S.map.invalidateSize(); S.map.setView([t.lat, t.lon], Math.max(S.map.getZoom(), 18)); }, 70);
    }
  }));
}

/* ---------------------------------------------------------- 一括編集 */
function updateBulkBar() {
  const n = S.picked.size;
  const bar = $('#bulk-bar');
  if (!bar) return;
  bar.hidden = n === 0;
  $('#bulk-n').textContent = `${n} 件を選択中`;
  const all = $('#sel-all');
  const ids = S.trees.map(t => t.id);
  all.checked = n > 0 && ids.length > 0 && ids.every(i => S.picked.has(i));
  all.indeterminate = n > 0 && !all.checked;
}

function bindBulk() {
  $('#bulk-status').innerHTML = '<option value="">ステータスを変えない</option>' +
    S.boot.status.map(s => `<option value="${s.code}">${esc(s.label)}</option>`).join('');

  $('#sel-all').addEventListener('change', e => {
    S.picked = e.target.checked ? new Set(S.trees.map(t => t.id)) : new Set();
    renderList();
    updateBulkBar();
  });
  $('#bulk-clear').addEventListener('click', () => {
    S.picked = new Set();
    renderList();
    updateBulkBar();
  });
  $('#bulk-apply').addEventListener('click', async () => {
    const f = {};
    const v = id => ($(id).value || '').trim();
    if (v('#bulk-status')) f.status = v('#bulk-status');
    if (v('#bulk-priority')) f.priority = v('#bulk-priority');
    if (v('#bulk-date')) f.survey_date = v('#bulk-date');
    if (v('#bulk-surveyor')) f.surveyor = v('#bulk-surveyor');
    if (v('#bulk-access')) f.access_note = v('#bulk-access');
    const keys = Object.keys(f);
    if (!keys.length) { toast('変更する項目を入れてください', true); return; }
    if (!confirm(`${S.picked.size} 件の記録を、${keys.length} 項目まとめて変更します。よろしいですか？`)) return;
    try {
      const r = await api('trees/bulk', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: [...S.picked], fields: f, _who: $('#who').value.trim() })
      });
      toast(`${r.changed} 件を更新しました`);
      ['#bulk-status', '#bulk-priority', '#bulk-date', '#bulk-surveyor', '#bulk-access']
        .forEach(id => $(id).value = '');
      S.picked = new Set();
      await reload();
    } catch (e) { toast(e.message, true); }
  });
}

/* ------------------------------------------------ 現地写真のまとめ取り込み */
function bindPhotos() {
  const drop = $('#ph-drop'), file = $('#ph-file');
  drop.addEventListener('click', () => file.click());
  file.addEventListener('change', () => importPhotos(file.files));
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', e => importPhotos(e.dataTransfer.files));
}

async function importPhotos(files) {
  if (!files || !files.length) return;
  const box = $('#ph-result');
  box.hidden = false;
  box.innerHTML = `<div class="pr-sum">${files.length} 枚を読み込んでいます…</div>`;
  const fd = new FormData();
  fd.append('radius', $('#ph-radius').value);
  fd.append('create', $('#ph-create').value);
  fd.append('_who', $('#who').value.trim());
  fd.append('site', (S.site && S.site !== 'genchi') ? S.site : 'genchi');
  let i = 0;
  for (const f of files) fd.append('p' + (i++), f, f.name);
  let r;
  try {
    const res = await fetch('/api/photos/import', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(((await res.json()) || {}).error || res.statusText);
    r = await res.json();
  } catch (e) {
    box.innerHTML = `<div class="pr-sum" style="border-left-color:var(--hi)">取り込めませんでした: ${esc(e.message)}</div>`;
    return;
  }
  const row = (x, extra) => `<div class="pr"><span class="f">${esc(x.file)}</span>${extra}</div>`;
  let h = `<div class="pr-sum">${esc(r.summary)}</div>`;
  if (r.attached.length) h += '<h4>既存の木の写真として追加</h4>' + r.attached.map(x =>
    row(x, `<span class="c">${esc(x.code)}</span><span class="d">${x.dist} m先</span>`)).join('');
  if (r.created.length) h += '<h4>新しく登録した木</h4>' + r.created.map(x =>
    row(x, `<span class="c">${esc(x.code)}</span><span class="d">${
      x.rinpan ? pad0(x.rinpan) + '-' + pad0(x.kosyoban) + ' 小班' : '林班外'}</span>`)).join('');
  if (r.nogps.length) h += '<h4>位置情報が無く、紐づけられなかった写真</h4>' + r.nogps.map(x =>
    row(x, '<span class="d">木を選んでから個別に貼れます</span>')).join('');
  if (r.skipped.length) h += '<h4>見送り</h4>' + r.skipped.map(x =>
    row(x, `<span class="d">${esc(x.why)}</span>`)).join('');
  box.innerHTML = h;
  await reload();
}

/* ------------------------------------------------ 2枚の画像を見比べる */
function cmpSites() {
  return (S.boot.sites || []).filter(s => s.zmax != null);
}

function startCompare() {
  const list = cmpSites();
  if (list.length < 2) { toast('見比べるには画像が2つ以上必要です', true); return; }
  const opts = list.map(s => `<option value="${esc(s.id)}">${esc(s.name || s.id)}</option>`).join('');
  $('#cmp-left').innerHTML = opts;
  $('#cmp-right').innerHTML = opts;
  $('#cmp-left').value = list[0].id;
  $('#cmp-right').value = list[1].id;

  if (!S.map.getPane('cmpL')) {
    ['cmpL', 'cmpR'].forEach((p, i) => {
      const pane = S.map.createPane(p);
      pane.style.zIndex = 250 + i;
    });
  }
  S.cmp = { layers: {}, x: 0.5 };
  $('#cmp-bar').hidden = false;
  $('#cmp-handle').hidden = false;
  $('#t-compare').classList.add('on');
  $('#l-ortho').checked = false;
  $('#l-ortho').dispatchEvent(new Event('change'));
  drawCompare();
  const b = list.find(s => s.id === $('#cmp-left').value);
  if (b) fitSite(b);
  moveHandle(0.5);
  toast('中央の線を左右にドラッグして見比べます');
}

function drawCompare() {
  const C = S.cmp;
  if (!C) return;
  for (const k of ['L', 'R']) {
    if (C.layers[k]) S.map.removeLayer(C.layers[k]);
    const id = $(k === 'L' ? '#cmp-left' : '#cmp-right').value;
    const s = cmpSites().find(x => x.id === id);
    if (!s) continue;
    C.layers[k] = L.tileLayer(s.tiles, {
      minZoom: s.zmin || 10, maxZoom: 22, maxNativeZoom: s.zmax,
      bounds: [[s.minlat, s.minlon], [s.maxlat, s.maxlon]],
      pane: 'cmp' + k, attribution: esc(s.name || s.id)
    }).addTo(S.map);
  }
  clipCompare();
}

function clipCompare() {
  if (!S.cmp) return;
  const w = S.map.getSize().x;
  const px = Math.round(S.cmp.x * w);
  const pl = S.map.getPane('cmpL'), pr = S.map.getPane('cmpR');
  if (pl) pl.style.clipPath = `inset(0 ${Math.max(0, w - px)}px 0 0)`;
  if (pr) pr.style.clipPath = `inset(0 0 0 ${Math.max(0, px)}px)`;
}

function moveHandle(frac) {
  if (!S.cmp) return;
  S.cmp.x = Math.max(0.02, Math.min(0.98, frac));
  const rect = $('#map').getBoundingClientRect();
  $('#cmp-handle').style.left = (S.cmp.x * rect.width) + 'px';
  clipCompare();
}

function stopCompare() {
  if (!S.cmp) return;
  for (const k of ['L', 'R']) if (S.cmp.layers[k]) S.map.removeLayer(S.cmp.layers[k]);
  ['cmpL', 'cmpR'].forEach(p => { const q = S.map.getPane(p); if (q) q.style.clipPath = ''; });
  S.cmp = null;
  $('#cmp-bar').hidden = true;
  $('#cmp-handle').hidden = true;
  $('#t-compare').classList.remove('on');
  $('#l-ortho').checked = true;
  $('#l-ortho').dispatchEvent(new Event('change'));
}

function bindCompare() {
  $('#t-compare').addEventListener('click', () => S.cmp ? stopCompare() : startCompare());
  $('#cmp-close').addEventListener('click', stopCompare);
  $('#cmp-left').addEventListener('change', drawCompare);
  $('#cmp-right').addEventListener('change', drawCompare);
  const h = $('#cmp-handle');
  let dragging = false;
  const onMove = e => {
    if (!dragging || !S.cmp) return;
    const rect = $('#map').getBoundingClientRect();
    const cx = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
    moveHandle(cx / rect.width);
    e.preventDefault();
  };
  h.addEventListener('mousedown', () => { dragging = true; });
  h.addEventListener('touchstart', () => { dragging = true; }, { passive: true });
  document.addEventListener('mousemove', onMove);
  document.addEventListener('touchmove', onMove, { passive: false });
  document.addEventListener('mouseup', () => { dragging = false; });
  document.addEventListener('touchend', () => { dragging = false; });
  S.map.on('resize', () => { if (S.cmp) moveHandle(S.cmp.x); });
}

/* ---------------------------------------------------------------- 詳細 */
function closeDetail() {
  endMove(null);
  $('#detail').classList.add('closed');
  S.sel = null;
  if (S.selMarker) { S.layers.tools.removeLayer(S.selMarker); S.selMarker = null; }
}

async function openDetail(id) {
  S.sel = id;
  const t = await api('tree/' + id);
  const lab = c => (S.boot.status.find(s => s.code === c) || {}).label || c;
  const rp = t.rinpan ? pad0(t.rinpan) : null;
  const ks = t.kosyoban ? pad0(t.kosyoban) : null;
  const naraTxt = { 2: 'ナラ類（森林簿）', 1: 'ナラ類の可能性あり', 0: 'ナラ類でない' }[t.nara_rank] ?? '民有林の小班外';
  const opt = (v, list) => list.map(o =>
    `<option ${String(v || '') === o ? 'selected' : ''}>${o}</option>`).join('');

  const hasPos = t.lat != null;
  const deg = hasPos ? fmt.deg(t.lat, t.lon) : '';
  const dms = hasPos ? `${fmt.dms(t.lat, true)} ${fmt.dms(t.lon, false)}` : '';
  const xy = (t.x != null) ? fmt.xy(t.x, t.y) : '';

  let moved = null;
  if (hasPos && t.orig_lat != null) {
    moved = L.latLng(t.orig_lat, t.orig_lon).distanceTo(L.latLng(t.lat, t.lon));
  }

  let nav = '';
  if (hasPos && S.here) {
    const dist = L.latLng(S.here.lat, S.here.lon).distanceTo(L.latLng(t.lat, t.lon));
    const b = bearing(S.here.lat, S.here.lon, t.lat, t.lon);
    nav = `<div class="navinfo">現在地から <b>${dist < 1000 ? dist.toFixed(0) + ' m' : (dist / 1000).toFixed(2) + ' km'}</b>
      　方角 <b>${COMPASS[Math.round(b / 22.5) % 16]}</b>（${b.toFixed(0)}°）</div>`;
  }

  $('#detail-body').innerHTML = `
  <div class="d-head">
    <div class="row1">
      <h2 class="mono">${esc(t.code)}</h2>
      <span class="pri pri-${esc(t.priority)}">優先度 ${esc(t.priority)}</span>
      <button class="d-close" title="閉じる">×</button>
    </div>
    <div class="d-sub">
      ${rp ? `<b>${esc(rp)} 林班${ks ? ' - ' + esc(ks) + ' 小班' : ''}</b>` : '<b>林班不明（民有林の小班外）</b>'}
      ${t.chiku ? '／' + esc(t.chiku) : ''}
      ${t.chiban ? '<br>' + esc(t.chiban) : ''}
    </div>
  </div>
  <div class="d-body">

    <div class="sec">
      <h3>位置</h3>
      <div class="coordbox">
        <div class="coordrow"><span class="lab">緯度経度</span>
          <span class="val" id="c-deg">${esc(deg || '—')}</span>
          ${hasPos ? '<button class="copy" data-c="deg" title="コピー">⧉</button>' : '<span></span>'}</div>
        <div class="coordrow"><span class="lab">度分秒</span>
          <span class="val" id="c-dms">${esc(dms || '—')}</span>
          ${hasPos ? '<button class="copy" data-c="dms" title="コピー">⧉</button>' : '<span></span>'}</div>
        <div class="coordrow"><span class="lab">平面直角<br>XI系 [m]</span>
          <span class="val" id="c-xy">${esc(xy || '—')}</span>
          ${hasPos ? '<button class="copy" data-c="xy" title="コピー">⧉</button>' : '<span></span>'}</div>
        <div class="coordrow"><span class="lab">標高</span>
          <span class="val">${t.elev != null ? esc(t.elev) + ' m' : '—'}${
            t.elev != null && t.elev <= 200 ? '　<span style="color:#ffc078;font-size:11px">重点管理</span>' : ''}</span>
          <span></span></div>
        ${t.canopy_h != null ? `<div class="coordrow"><span class="lab">樹高</span>
          <span class="val">${esc(t.canopy_h)} m　<span style="color:var(--fg3);font-size:10px">参考値</span></span>
          <span></span></div>` : ''}
        ${moved != null ? `<div class="coordrow"><span class="lab">位置の調整</span>
          <span class="val" style="color:#8ce99a">元の位置から ${moved.toFixed(1)} m 動かした</span>
          <span></span></div>` : ''}
      </div>
      ${hasPos ? `<div class="maplinks">
        <a href="https://maps.gsi.go.jp/#18/${t.lat}/${t.lon}/" target="_blank" rel="noopener">地理院地図</a>
        <a href="https://www.google.com/maps/search/?api=1&query=${t.lat},${t.lon}" target="_blank" rel="noopener">Googleマップ</a>
        <a href="https://www.google.com/maps/dir/?api=1&destination=${t.lat},${t.lon}" target="_blank" rel="noopener">経路</a>
      </div>` : ''}
      ${nav}
      <p class="hint">位置の確からしさ：${esc(t.loc_accuracy || '—')}<br>
        オルソの絶対位置には水平±3〜5mの誤差があります。</p>
      ${hasPos ? `<div class="actions">
        <button class="btn primary" id="btn-move">オルソ上で位置を直す</button>
        ${moved != null ? '<button class="btn" id="btn-reset-pos">元に戻す</button>' : ''}
      </div>` : ''}
      ${t.source === 'survey' && moved == null ? `<p class="hint">
        この記録の位置は<b>小班の代表点</b>で、木そのものの座標ではありません。
        オルソで枯れている木が見つかったら、上のボタンでその位置に直せます。</p>` : ''}
    </div>

    <div class="sec">
      <h3>現地調査ステータス</h3>
      <div class="statusgrid" id="sbtns">
        ${S.boot.status.map(s => `<button class="sbtn ${t.status === s.code ? 'on' : ''}"
           data-v="${s.code}" style="${t.status === s.code ? 'border-color:' + s.color + ';background:' + s.color + '33' : ''}">
           <i class="dot" style="background:${s.color}"></i>${esc(s.label)}</button>`).join('')}
      </div>
      <div class="f2" style="margin-top:10px">
        <div class="f"><label>調査日</label><input type="date" id="e-survey_date" value="${esc(t.survey_date || '')}"></div>
        <div class="f"><label>調査者</label><input id="e-surveyor" value="${esc(t.surveyor || '')}"></div>
      </div>
      <div class="f"><label>到達状況・アクセスの記録（到達できなかった場合はここに理由を）</label>
        <input id="e-access_note" value="${esc(t.access_note || '')}" placeholder="例）沢を渡れず接近不可。対岸から目視。"></div>
    </div>

    <div class="sec">
      <h3>現地で確認したこと</h3>
      <div class="f2">
        <div class="f"><label>樹種</label>
          <input id="e-species" list="dl-sp" value="${esc(t.species || '')}" placeholder="ミズナラ / コナラ / カシワ">
          <datalist id="dl-sp"><option>ミズナラ</option><option>コナラ</option><option>カシワ</option>
            <option>シラカンバ</option><option>トドマツ</option>
            <option>その他広葉樹</option><option>針葉樹</option></datalist></div>
        <div class="f"><label>胸高直径 cm</label><input type="number" id="e-dbh_cm" value="${t.dbh_cm ?? ''}" step="1"></div>
      </div>
      <div class="f2">
        <div class="f"><label>葉の変色</label><select id="e-leaf_color">${opt(t.leaf_color, ['', 'なし', '一部変色', '全体変色', '落葉'])}</select></div>
        <div class="f"><label>枯死状況</label><select id="e-dieback">${opt(t.dieback, ['', '健全', '衰弱', '枯死'])}</select></div>
      </div>
      <div class="f2">
        <div class="f"><label>穿孔（カシナガ）</label><select id="e-boring">${opt(t.boring, ['', 'なし', '少', '中', '多'])}</select></div>
        <div class="f"><label>フラス量</label><select id="e-frass">${opt(t.frass, ['', 'なし', '少', '中', '多'])}</select></div>
      </div>
      <div class="f"><label>周辺林相</label><input id="e-stand" value="${esc(t.stand || '')}" placeholder="例）ミズナラ・トドマツ混交、立木密度中"></div>
      <div class="f"><label>ナラ枯れでなかった場合の要因</label>
        <select id="e-misjudge_reason">${opt(t.misjudge_reason,
          ['', '根むくれによる枯れ', 'つる植物の枯れ', '自然枯れ', 'ナラ類以外の広葉樹',
           '針葉樹', '影・地形', '道路・裸地', '樹冠の重なり', '撮影時期の差',
           '解像度不足', '原因不明', 'その他'])}</select>
        <p class="hint">森町では「根むくれによる枯れ」「シラカンバに巻き付いたツルの枯れ」が
          実際に確認されています。</p></div>
    </div>

    <div class="sec">
      <h3>被害木の処理（カシナガ脱出前・5月末まで）</h3>
      <div class="f2">
        <div class="f"><label>処理方法</label>
          <select id="e-treatment">${opt(t.treatment, ['', '伐倒後焼却', '伐倒後チップ化・焼却', '伐倒くん蒸', '立木くん蒸', '未定'])}</select></div>
        <div class="f"><label>処理日</label><input type="date" id="e-treatment_date" value="${esc(t.treatment_date || '')}"></div>
      </div>
    </div>

    <div class="sec">
      <h3>メモ</h3>
      <div class="f"><textarea id="e-memo" placeholder="現地で気づいたこと">${esc(t.memo || '')}</textarea></div>
      <div class="actions">
        <button class="btn primary" id="save">保存</button>
      </div>
      <p class="hint">「保存」で調査記録が data/survey.db に書き込まれます。</p>
    </div>

    <div class="sec">
      <h3>写真</h3>
      <div class="photos" id="ph">${(t.photos || []).map(p =>
        `<figure><img src="/data/photos/${encodeURIComponent(p.filename)}" alt="${esc(p.caption || '')}"
          onclick="window.open(this.src,'_blank')"><button data-pid="${p.id}" title="削除">×</button></figure>`).join('')}</div>
      <div class="drop" id="drop" style="margin-top:8px">
        写真をここにドロップ／クリックして選択<br><span style="font-size:11px">樹冠・葉・幹・周辺林相</span>
        <input type="file" id="file" accept="image/*" multiple hidden>
      </div>
    </div>

    <div class="sec">
      <h3>この記録について</h3>
      <dl class="kv">
        <dt>登録元</dt><dd>${t.source === 'ai' ? 'オルソからの自動抽出'
          : t.source === 'survey' ? '振興局・町の現地確認記録' : '手入力（地図でタップ）'}</dd>
        <dt>森林簿樹種</dt><dd>${esc(t.sp_main || '—')}（${esc(naraTxt)}）</dd>
        <dt>更新</dt><dd>${esc(t.updated_at || '')}</dd>
      </dl>
    </div>

    <div class="sec">
      <h3>変更履歴</h3>
      <div class="hist">${(t.history || []).length
        ? t.history.map(h => `<div>${esc(h.at)}　<b>${esc(h.who || '不明')}</b>　${esc(h.field)}：
            ${esc(h.old || '（空）')} → ${esc(h.new || '（空）')}</div>`).join('')
        : '<div>まだ変更はありません</div>'}</div>
      ${t.source === 'manual' ? '<div class="actions"><button class="btn danger" id="del">この記録を削除</button></div>' : ''}
    </div>
  </div>`;

  $('#detail').classList.remove('closed');
  wireDetail(t, { deg, dms, xy });
  $$('#list tbody tr').forEach(tr => tr.classList.toggle('sel', Number(tr.dataset.id) === id));

  // 選んだ木を目立たせる
  if (S.selMarker) S.layers.tools.removeLayer(S.selMarker);
  S.selMarker = null;
  if (hasPos) {
    S.selMarker = L.circleMarker([t.lat, t.lon],
      { radius: 15, color: '#ffd43b', weight: 3, fill: false, interactive: false })
      .addTo(S.layers.tools);
  }
}

function wireDetail(t, coords) {
  $('.d-close').addEventListener('click', closeDetail);

  $$('.copy').forEach(b => b.addEventListener('click', () => copyText(coords[b.dataset.c], b)));

  $$('#sbtns .sbtn').forEach(b => b.addEventListener('click', async () => {
    try {
      await save({ status: b.dataset.v });
      toast('ステータスを更新しました');
      await reload();
      openDetail(t.id);
    } catch (e) { toast(e.message, true); }
  }));

  $('#save').addEventListener('click', async () => {
    const d = {};
    for (const k of ['survey_date', 'surveyor', 'access_note', 'species', 'dbh_cm',
      'leaf_color', 'dieback', 'boring', 'frass', 'stand', 'misjudge_reason',
      'treatment', 'treatment_date', 'memo']) {
      const el = $('#e-' + k);
      if (el) d[k] = el.value;
    }
    try { await save(d); toast('保存しました'); await reload(); }
    catch (e) { toast(e.message, true); }
  });

  const mv = $('#btn-move');
  if (mv) mv.addEventListener('click', () => startMove(t));

  const rs = $('#btn-reset-pos');
  if (rs) rs.addEventListener('click', async () => {
    if (!confirm('登録されたときの位置に戻します。よろしいですか？')) return;
    try {
      await api('tree/' + t.id + '/reset_position', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ _who: $('#who').value.trim() })
      });
      toast('元の位置に戻しました');
      await reload();
      openDetail(t.id);
    } catch (e) { toast(e.message, true); }
  });

  const del = $('#del');
  if (del) del.addEventListener('click', async () => {
    if (!confirm(t.code + ' を削除します。よろしいですか？')) return;
    const r = await fetch('/api/tree/' + t.id, { method: 'DELETE' });
    if (!r.ok) { toast((await r.json()).error, true); return; }
    toast('削除しました'); closeDetail(); reload();
  });

  $$('#ph button').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('この写真を削除しますか？')) return;
    await api('photo/' + b.dataset.pid + '/delete', { method: 'POST' });
    openDetail(t.id);
  }));

  const drop = $('#drop'), file = $('#file');
  drop.addEventListener('click', () => file.click());
  file.addEventListener('change', () => upload(t.id, file.files));
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', e => upload(t.id, e.dataTransfer.files));
}

/* ---------- オルソ上で位置を直す ---------- */
function endMove(commitTo) {
  const M = S.move;
  if (!M) return;
  [M.marker, M.startDot, M.line].forEach(l => { if (l) S.layers.tools.removeLayer(l); });
  S.move = null;
  $('#hint-bar').hidden = true;
  $('#hint-bar').classList.remove('move');
  $('#hint-text').innerHTML = '';
  $('#hint-cancel').textContent = 'やめる（Esc）';
  const ok = $('#hint-ok');
  if (ok) ok.remove();
  if (commitTo != null) openDetail(commitTo);
}

function startMove(t) {
  endMove(null);
  // オルソを見ながら直せるように、表示を整える
  if (!$('#l-ortho').checked) {
    $('#l-ortho').checked = true;
    $('#l-ortho').dispatchEvent(new Event('change'));
  }
  S.map.setView([t.lat, t.lon], Math.max(S.map.getZoom(), 20));

  const start = L.latLng(t.lat, t.lon);
  const startDot = L.circleMarker(start, {
    radius: 6, color: '#ffd43b', weight: 2, fillColor: '#000', fillOpacity: .4,
    interactive: false, dashArray: '3,3'
  }).addTo(S.layers.tools);
  const line = L.polyline([start, start], {
    color: '#ffd43b', weight: 1.6, dashArray: '5,5', interactive: false
  }).addTo(S.layers.tools);
  const marker = L.marker(start, { draggable: true, autoPan: true, zIndexOffset: 1000 })
    .addTo(S.layers.tools);

  S.move = { id: t.id, marker, startDot, line, start, pos: start };

  const bar = $('#hint-bar');
  bar.hidden = false;
  bar.classList.add('move');
  if (!$('#hint-ok')) {
    const b = document.createElement('button');
    b.id = 'hint-ok';
    b.className = 'mini ok';
    b.textContent = 'この位置で確定';
    bar.insertBefore(b, $('#hint-cancel'));
    b.addEventListener('click', commitMove);
  }
  $('#hint-cancel').textContent = 'やめる（Esc）';

  const show = () => {
    const p = S.move.pos;
    const d = start.distanceTo(p);
    $('#hint-text').innerHTML =
      `ピンをドラッグ、または地図をタップして木の位置へ　`
      + `<b class="mono">${p.lat.toFixed(6)}, ${p.lng.toFixed(6)}</b>`
      + `　<span style="opacity:.85">元の位置から ${d.toFixed(1)} m</span>`;
    S.move.line.setLatLngs([start, p]);
  };
  show();
  marker.on('drag', () => { S.move.pos = marker.getLatLng(); show(); });
  marker.on('dragend', () => { S.move.pos = marker.getLatLng(); show(); });
  S.moveClick = ll => { S.move.pos = ll; marker.setLatLng(ll); show(); };
}

async function commitMove() {
  const M = S.move;
  if (!M) return;
  try {
    await api('tree/' + M.id, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lon: M.pos.lng, lat: M.pos.lat,
                             loc_accuracy: 'オルソ上で目視により特定',
                             _who: $('#who').value.trim() })
    });
    const d = M.start.distanceTo(M.pos);
    toast(`位置を ${d.toFixed(1)} m 動かしました`);
    const id = M.id;
    endMove(null);
    await reload();
    openDetail(id);
  } catch (e) { toast(e.message, true); }
}

async function save(d) {
  d._who = $('#who').value.trim();
  return api('tree/' + S.sel, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(d)
  });
}

async function upload(id, files) {
  if (!files || !files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append('photo_' + f.name, f, f.name);
  const r = await fetch('/api/tree/' + id + '/photo', { method: 'POST', body: fd });
  if (!r.ok) { toast('写真を保存できませんでした', true); return; }
  toast(files.length + ' 枚を保存しました');
  openDetail(id);
}

/* ---------------------------------------------------------------- 進捗 */
async function renderBoard() {
  const p = new URLSearchParams();
  if (S.site) p.set('site', S.site);
  const st = await api('stats?' + p.toString());
  const n = st.total || 0;
  const pct = v => n ? Math.round(v / n * 1000) / 10 : 0;
  const seg = S.boot.status.map(s =>
    `<i style="width:${pct(st.status[s.code] || 0)}%;background:${s.color}" title="${s.label}"></i>`).join('');

  const cards = [
    ['登録の総数', n, S.site ? 'このサイト' : '森町全体'],
    ['未調査', st.status.unsurveyed || 0, '現地確認がまだ'],
    ['被害あり', st.status.damaged || 0, '処理が必要'],
    ['処理済', st.status.treated || 0, '伐倒・くん蒸など'],
    ['到達できず', st.status.unreachable || 0, '再訪の検討'],
    ['被害なし', st.status.clean || 0, 'ナラ枯れではなかった'],
  ].map(([k, v, s]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');

  const rows = st.by_rinpan.filter(r => r.rinpan).map(r => `<tr>
    <td>${esc(pad0(r.rinpan))} 林班</td><td>${esc(r.chiku || '')}</td>
    <td>${r.n}</td><td style="color:#ff8787">${r.high}</td><td>${r.unsurveyed}</td>
    <td style="color:#ff8787">${r.damaged}</td><td style="color:#74c0fc">${r.treated}</td></tr>`).join('');

  $('#board').innerHTML = `
    <h2>いまの状況</h2>
    <div class="cards">${cards}</div>
    <div class="bar" style="height:14px;margin-top:14px">${seg}</div>
    <div style="font-size:11.5px;color:var(--fg3)">
      ${S.boot.status.map(s => `<span style="margin-right:14px">
        <i style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${s.color}"></i>
        ${s.label} ${st.status[s.code] || 0}</span>`).join('')}
    </div>

    <h2>処理の期限</h2>
    <div class="note ${st.deadline.days_left <= 60 ? 'warn' : ''}">
      被害が確認された木は、<b>カシノナガキクイムシが脱出する翌年5月末まで</b>に
      伐倒焼却・伐倒くん蒸・立木くん蒸などの処理を終える必要があります。<br>
      <b>${st.deadline.date} まで あと ${st.deadline.days_left} 日</b>。
      いま「被害あり」で未処理は <b>${st.to_treat}</b> 件です。
    </div>

    ${renderUnreachable(st)}

    <h2>林班ごとの内訳</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>林班</th><th>地区</th><th>登録</th><th>優先度高</th><th>未調査</th>
        <th>被害あり</th><th>処理済</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7">データがありません</td></tr>'}</tbody></table></div>

    <h2>次にやること</h2>
    <div class="note">
      ${st.status.unsurveyed ? `① 未調査の <b>${st.status.unsurveyed}</b> 件を、優先度「高」から現地確認する。<br>` : ''}
      ${st.to_treat ? `② 被害ありの <b>${st.to_treat}</b> 件を、5月末までに処理する。<br>` : ''}
      ${st.status.unreachable ? `③ 到達できなかった <b>${st.status.unreachable}</b> 件を、オルソで確認する。<br>` : ''}
      ④ 現地調査の結果は、翌年のAIモデルの再学習用データになります（CSV／GeoJSONで書き出せます）。
    </div>`;

  $$('#board button.go').forEach(b => b.addEventListener('click', () => {
    showView('map');
    $('#l-ortho').checked = true;
    setTimeout(() => {
      S.map.invalidateSize();
      S.map.setView([Number(b.dataset.lat), Number(b.dataset.lon)], 19);
    }, 80);
  }));
}

function renderUnreachable(st) {
  const list = st.unreachable || [];
  if (!list.length) return '';
  const covered = list.filter(u => u.covered_by);
  const rows = list.map(u => `<tr>
      <td class="mono">${esc(u.code)}</td>
      <td>${u.rinpan ? esc(pad0(u.rinpan)) + (u.kosyoban ? '-' + esc(pad0(u.kosyoban)) : '') : '位置未特定'}</td>
      <td class="wrap">${esc(u.access_note || '')}</td>
      <td>${u.covered_by
        ? `<span style="color:#69db7c">あり</span><br><span style="font-size:11px;color:var(--fg3)">${esc(u.covered_by.name)}</span>`
        : '<span style="color:var(--fg3)">なし</span>'}</td>
      <td>${u.covered_by
        ? `<button class="mini go" data-lon="${u.lon}" data-lat="${u.lat}" style="margin:0">オルソで見る</button>` : ''}</td></tr>`).join('');

  return `
    <h2>現地に到達できなかった地点</h2>
    <div class="note">
      現地調査で到達できなかった地点が <b>${list.length}</b> 件あります。
      笹薮・急斜面・熊の出没など、人が近づけない理由は現場では動かせません。<br>
      このうち <b>${covered.length}</b> 件は<b>ドローンのオルソの範囲に入っており、上空から確認できます</b>。
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th>記録ID</th><th>林班-小班</th><th>到達できなかった理由</th>
        <th>オルソ</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

/* ---------------------------------------------------------------- 説明 */
function renderInfo() {
  const f = S.boot.forest || {};
  let stat = {};
  try { stat = JSON.parse(f['統計'] || '{}'); } catch (e) { }
  $('#info').innerHTML = `
  <h2>このビューアーは何をするものか</h2>
  <p>ドローンで撮ったオルソ画像の上で、ナラ枯れが疑われる木について
  「どこにあるか」「現地を見たか」「結果はどうだったか」「処理は済んだか」を
  管理するための道具です。ヘリコプターによる上空からの目視確認を置き換え、
  <b>人が現地に入れない場所を上空から確認できるようにする</b>ことを狙っています。</p>

  <h2>なぜナラ枯れが問題なのか（森町の状況）</h2>
  <p>ナラ枯れは、<b>カシノナガキクイムシ</b>が運ぶ<b>ナラ菌</b>によってミズナラ・コナラ・カシワなどが
  集団的に枯れる被害です。北海道では<b>令和5年（2023年）10月に道南で初めて確認</b>され、
  渡島半島南端部から北へ広がってきました。令和7年度には<b>道内11市町で2,001本</b>の被害木が
  確認されています。</p>
  <p>森町では、<b>令和8年（2026年）5月13日、森町砂原2丁目の町有林 1020林班</b>で被害木が確認され、
  伐倒・玉切りのうえ薬剤注入とビニール被覆による処理が行われました。町は
  <b>標高200m以下の高リスク区域</b>を重点管理地域として設定しています。
  ナラ類の伐採・移動はカシナガを広げるおそれがあるため、自己判断で行わず町（農林課）へ相談することとされています。</p>

  <h2>行政の年間の流れと、この道具が効く場所</h2>
  <table>
    <thead><tr><th>時期</th><th>やること</th><th>このビューアー</th></tr></thead>
    <tbody>
    <tr><td>7月頃</td><td>カシナガの生息調査</td><td>—</td></tr>
    <tr><td>8月・11月</td><td>被害監視区域の設定</td><td>ナラ類の分布と標高200m以下の区域を地図で確認できる</td></tr>
    <tr><td>9月頃</td><td>ヘリコプター等による上空調査</td><td><b>ドローンのオルソで置き換える</b></td></tr>
    <tr><td>9月・10月</td><td>現地調査で被害判定</td><td><b>結果をその場で記録する</b></td></tr>
    <tr><td>翌年5月末まで</td><td>伐倒焼却・くん蒸などの処理</td><td><b>未処理の件数と残り日数を表示する</b></td></tr>
    </tbody>
  </table>

  <h2>現地に到達できない、という問題</h2>
  <p>渡島総合振興局が整理した森町の現地確認結果では、確認しようとした11地点のうち
  <b>5地点が到達不能</b>でした。理由は笹薮1.5km以上、高低差100mの急斜面、
  熊の足跡で安全を確保できない、などで、いずれも人員を増やしても解決しません。</p>
  <p>このうち<b>4地点が今回のオルソの範囲に入っており</b>、上空から確認できる状態になりました。
  「進捗」タブに一覧があり、その場所へ地図で飛べます。</p>
  <p>また、確認できた6地点のうち実際にナラ枯れだったのは<b>1地点だけ</b>で、
  残りは根むくれ・つる植物の枯れ・原因不明でした。
  上空から見て赤茶色に見えるものが、そのままナラ枯れとは限りません。</p>

  <h2>使っているデータ</h2>
  <h3>林班・小班・樹種（森林調査簿）</h3>
  <p class="src">北海道オープンデータ「森林計画関係資料（一般民有林）令和6年・渡島」<br>
  ${esc(f['出典_URL'] || '')}<br>
  座標系 ${esc(f['座標系_原典'] || '')}／北海道オープンデータ利用規約</p>
  <p>森町の <b>林班 ${stat['林班数'] ?? '—'} 件</b>、<b>小班 ${stat['小班数'] ?? '—'} 件</b>を取り込んでいます。
  このうち森林調査簿で樹種がナラ類とされているのは
  <b>${stat['ナラ類確実_小班数'] ?? '—'} 小班・${stat['ナラ類確実_面積ha'] ?? '—'} ha</b>、
  さらに天然林広葉樹・針広混交林としてナラ類が混じりうる小班が
  <b>${stat['ナラ類可能性_小班数'] ?? '—'} 件</b>あります。
  ナラ類のうち<b>標高200m以下（重点管理地域）は ${stat['標高200m以下のナラ類小班数'] ?? '—'} 小班・
  ${stat['標高200m以下のナラ類面積ha'] ?? '—'} ha</b>です。</p>

  <h3>標高</h3>
  <p class="src">国土地理院 標高タイル DEM10B（10mメッシュ・地表面標高）</p>

  <h3>背景の空中写真・地形図</h3>
  <p class="src">国土地理院タイル（https://maps.gsi.go.jp/development/ichiran.html）<br>
  ${S.boot.basemap_local && S.boot.basemap_local.photo
      ? 'ローカルに取り込み済みのため、インターネットに繋がらない場所でも表示できます。'
      : '未取り込みです。<code>tools/fetch_basemap.py</code> を実行するとオフラインでも使えます。'}</p>

  <h3>ドローンオルソ</h3>
  <p>座標系は平面直角座標系XI系（EPSG:6679）。森林調査簿（EPSG:2459）と投影法・原点が同一のため、
  そのまま重ねられます。</p>

  <h2>位置の精度について（重要）</h2>
  <div class="note warn">
    オルソは<b>対空標識（GCP）もRTKも使っていないGPS単独測位</b>で作られています。<br>
    ・オルソの中での形や距離（木と木の間隔、林分の面積）は正確です。<br>
    ・しかし<b>絶対位置には水平±3〜5mの系統的なずれ</b>が残ります。高さは気圧高度由来で、さらに大きな誤差を含みます。<br>
    ・そのため森林簿や地番図と重ねると、<b>全体が数メートル平行移動して見えます</b>。<br>
    <b>この画面の座標をそのまま境界確定や図面作成に使うことはできません。</b>
    林分の現況把握・変化の把握・作業計画の検討に使ってください。<br>
    現地でGNSS等で測った位置が分かったら、詳細画面の「ピンを動かして直す」で修正できます。
  </div>

  <h2>データはどこにあるか</h2>
  <table>
    <tbody>
    <tr><td><code>data/survey.db</code></td><td><b>現地調査の記録。人が入力したものはここにしかありません。
      これだけは必ずバックアップしてください。</b>オルソを入れ直しても消えません。</td></tr>
    <tr><td><code>data/forest.db</code></td><td>林班・小班・森林調査簿。<code>tools/build_forest.py</code> で作り直せます。</td></tr>
    <tr><td><code>data/tiles/</code></td><td>オルソの地図タイル。オルソから作り直せます。</td></tr>
    <tr><td><code>data/photos/</code></td><td>現地写真。<b>バックアップ対象です。</b></td></tr>
    </tbody>
  </table>

  <h2>候補木の自動抽出について</h2>
  <p>オルソの取り込みでは、<b>候補木の自動抽出は既定では行いません</b>。
  抽出は別のアルゴリズムで行い、後からこのデータベースにマージする運用を想定しています。
  同梱の色ベース抽出を試したい場合は、取り込み画面の
  「同梱の色ベース抽出で候補木も作る」にチェックを入れてください。</p>`;
}

boot().catch(e => {
  document.body.insertAdjacentHTML('afterbegin',
    `<div style="padding:20px;color:#ffa8a8">起動できませんでした: ${esc(e.message)}</div>`);
});
