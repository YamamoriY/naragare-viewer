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
  filter: { status: new Set(), work: new Set(), rinpan: '', q: '' },
  mode: null,            // null / 'add' / 'measure'
  measure: null,
  map: null, layers: {}, markers: new Map(),
  naraLoaded: false, here: null, selMarker: null, picked: new Set(), cmp: null
};

const fmtDate = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

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
  // 日本の平面直角座標系は X が北向き、Y が東向き。
  // 内部では GIS 式に x=東, y=北 で持っているので、表示のときに入れ替える。
  xy: (east, north) => `X ${north.toFixed(2)}  Y ${east.toFixed(2)}`,
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

  buildPanels();
  buildSites();
  buildFilters();
  buildLegend();
  initMap();
  bindUI();
  bindImport();
  bindBulk();
  bindPhotos();
  bindCompare();
  bindDropzone();
  bindCoord();
  renderInfo();
  await reload();
}

/* ------------------------------------------------------- 折り畳みパネル */
function buildPanels() {
  const saved = JSON.parse(localStorage.getItem('panels') || '{}');
  $$('#side .panel').forEach(p => {
    const key = p.dataset.key;
    // オルソ・凡例・書き出しは既定で閉じておく（普段は開かなくてよい）
    const def = (key === 'sites' || key === 'legend' || key === 'export');
    if (key in saved ? saved[key] : def) p.classList.add('closed');
    $('.panel-h', p).addEventListener('click', () => {
      p.classList.toggle('closed');
      const st = JSON.parse(localStorage.getItem('panels') || '{}');
      st[key] = p.classList.contains('closed');
      localStorage.setItem('panels', JSON.stringify(st));
    });
  });
}

/* ---------------------------------------------------------------- サイト */
function orthoSites() { return (S.boot.sites || []).filter(s => s.zmax != null); }

function buildSites() {
  const box = $('#sites');
  const list = orthoSites();
  $('#site-n').textContent = list.length + ' 枚';
  let h = `<button class="site on" data-id="">
      <b>森町全体</b><span>すべてのオルソを重ねて表示</span></button>`;
  for (const s of list) {
    h += `<button class="site" data-id="${esc(s.id)}">
      <b>${esc(s.name || s.id)}</b>
      <span>${esc(s.flown_on || '撮影日不明')}${
        s.rinpan ? '・' + pad0(s.rinpan) + '林班' : ''}</span></button>`;
  }
  if (!list.length) {
    h += `<p class="hint" style="padding:4px 9px 8px">
      オルソが未登録です。画像をこの画面にドロップするか、
      右の「画像取込」から追加してください。</p>`;
  }
  box.innerHTML = h;
  // サイトを選ぶのは「地図をそこへ動かす」だけ。
  // 記録の一覧は絞り込まない（どの木もいつでも選べるようにするため）。
  $$('.site', box).forEach(b => b.addEventListener('click', () => {
    $$('.site', box).forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    S.site = b.dataset.id || null;
    const s = list.find(x => x.id === S.site);
    setOrtho(s);
    if (s) fitSite(s);
  }));
}

function fitSite(s) {
  if (!s || s.minlat == null) return;
  S.map.fitBounds([[s.minlat, s.minlon], [s.maxlat, s.maxlon]], { padding: [26, 26] });
}

/* ---------------------------------------------------------------- 絞り込み */
function buildFilters() {
  const chips = (list, box, set) => {
    $(box).innerHTML = list.map(s =>
      `<button class="chip" data-v="${s.code}"><i class="dot" style="background:${s.color}"></i>${esc(s.label)}<span class="n"></span></button>`).join('');
    $$(box + ' .chip').forEach(b => b.addEventListener('click', () => {
      const v = b.dataset.v;
      set.has(v) ? set.delete(v) : set.add(v);
      b.classList.toggle('on');
      reload();
    }));
  };
  chips(S.boot.status, '#f-status', S.filter.status);
  chips(S.boot.work, '#f-work', S.filter.work);

  let t;
  $('#q').addEventListener('input', e => {
    clearTimeout(t);
    t = setTimeout(() => { S.filter.q = e.target.value.trim(); reload(); }, 260);
  });
  $('#f-rinpan').addEventListener('change', e => { S.filter.rinpan = e.target.value; reload(); });
  $('#reset-filter').addEventListener('click', () => {
    S.filter = { status: new Set(), work: new Set(), rinpan: '', q: '' };
    $$('#f-status .chip, #f-work .chip').forEach(c => c.classList.remove('on'));
    $('#q').value = ''; $('#f-rinpan').value = '';
    reload();
  });
}

function qs() {
  const p = new URLSearchParams();
  if (S.filter.status.size) p.set('status', [...S.filter.status].join(','));
  if (S.filter.work.size) p.set('work', [...S.filter.work].join(','));
  if (S.filter.rinpan) p.set('rinpan', S.filter.rinpan);
  if (S.filter.q) p.set('q', S.filter.q);
  return p;
}

function buildLegend() {
  $('#legend').innerHTML =
    `<div class="cap">丸の色＝現地調査の判定</div>`
    + S.boot.status.map(s =>
      `<div class="row"><i class="sw" style="background:${s.color}"></i>${esc(s.label)}</div>`).join('')
    + `<div class="row" style="margin-top:4px"><i class="sw" style="background:#fff;box-shadow:0 0 0 3px #e8a300"></i>金の縁＝位置を調整済み</div>
       <div class="row"><i class="sw sq" style="background:#7048e8"></i>ナラ類の小班（森林簿）</div>
       <div class="cap">標高200m 等高線より低い側が森町の重点管理地域</div>`;
}

/* ---------------------------------------------------------------- 地図 */
function initMap() {
  const map = L.map('map', { zoomControl: true, preferCanvas: true, maxZoom: 22 })
    .setView([42.09, 140.62], 11);
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
  };
  S.layers.base.photo.addTo(map);
  S.curBase = 'photo';

  S.layers.orthoGroup = L.layerGroup().addTo(map);
  S.layers.rinpan = L.layerGroup();
  S.layers.kosyoban = L.layerGroup();
  S.layers.nara = L.layerGroup();
  S.layers.contour = L.layerGroup();
  S.layers.trees = L.layerGroup().addTo(map);
  S.layers.tools = L.layerGroup().addTo(map);
  setOrtho(null);

  map.on('click', onMapClick);
  map.on('dblclick', () => { if (S.mode === 'measure') setMode(null); });
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
  const list = site ? [site] : orthoSites();
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
  const st = {};
  const wc = {};
  for (const t of S.trees) {
    st[t.status] = (st[t.status] || 0) + 1;
    const w = t.work_status || 'none';
    wc[w] = (wc[w] || 0) + 1;
  }
  $('#count').innerHTML = `記録 <b>${n}</b> 件`
    + (st.damaged ? `　被害あり <b style="color:#c92a2a">${st.damaged}</b>` : '')
    + (st.unreachable ? `　到達できず <b style="color:#5f3dc4">${st.unreachable}</b>` : '')
    + (st.unsurveyed ? `　未調査 <b>${st.unsurveyed}</b>` : '');
  $('#list-count').textContent = `${n} 件`;
  const nf = S.filter.status.size + S.filter.work.size
    + (S.filter.rinpan ? 1 : 0) + (S.filter.q ? 1 : 0);
  $('#filter-n').textContent = nf ? nf + ' 件適用' : '';
  $$('#f-status .chip').forEach(c => $('.n', c).textContent = st[c.dataset.v] || 0);
  $$('#f-work .chip').forEach(c => $('.n', c).textContent = wc[c.dataset.v] || 0);
}

/* ---------------------------------------------------------------- マーカー */
/** 一覧に出す処理ステータスの札。被害ありの木だけ意味があるので、
    それ以外は薄く出す。 */
function workTag(t) {
  const w = S.boot.work.find(x => x.code === (t.work_status || 'none'));
  if (!w) return '—';
  if (t.status !== 'damaged' && w.code === 'none') return '<span style="color:var(--fg3)">—</span>';
  return `<span class="tag" style="background:${w.color}1f;color:${w.color}">${esc(w.label)}</span>`;
}

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
    // 指でも押せるように、見えない大きな当たり判定を下に敷く
    L.circleMarker([t.lat, t.lon], {
      radius: 22, stroke: false, fillColor: '#000', fillOpacity: 0.001,
      bubblingMouseEvents: false
    }).on('click', ev => hitTree(ev, t.id)).addTo(g);

    const m = L.circleMarker([t.lat, t.lon], {
      radius: 10,
      color: t.orig_lat != null ? '#e8a300' : '#ffffff',
      weight: t.orig_lat != null ? 3.5 : 3,
      fillColor: statusColor(t.status), fillOpacity: 1,
      bubblingMouseEvents: false
    });
    m.on('click', ev => hitTree(ev, t.id));
    m.bindTooltip(
      `<b>${esc(t.code)}</b><br>${
        t.rinpan ? esc(pad0(t.rinpan)) + '林班' : '林班不明'}${
        t.kosyoban ? '-' + esc(pad0(t.kosyoban)) + '小班' : ''}${
        t.orig_lat != null ? '<br>位置を調整済み' : ''}`,
      { direction: 'top', opacity: 1 });
    m.addTo(g);
    S.markers.set(t.id, m);
  }
}

/* ---------------------------------------------------------------- レイヤー */
async function loadRinpan() {
  if (S.rinpanLoaded) return;
  const url = (S.boot.layers || {}).rinpan;
  if (!url) throw new Error('林班界データがありません');
  const gj = await (await fetch(url)).json();
  L.geoJSON(gj, {
    style: { color: '#0b5fce', weight: 1.6, fill: false, opacity: .85 },
    onEachFeature: (f, l) => l.bindTooltip(
      `${esc(f.properties['林班'])} 林班（${esc(f.properties['地区'])}）<br>${f.properties['面積ha']} ha`,
      { sticky: true })
  }).addTo(S.layers.rinpan);
  S.rinpanLoaded = true;
}

async function loadContour() {
  if (S.contourLoaded) return;
  const url = (S.boot.layers || {}).contour200;
  if (!url) throw new Error('等高線データがありません（tools/build_contour.py を実行）');
  const gj = await (await fetch(url)).json();
  // 太い暗線の上に細い明線を重ねて、空中写真の上でも読めるようにする
  L.geoJSON(gj, { style: { color: '#ffffff', weight: 5, opacity: .75, fill: false } })
    .addTo(S.layers.contour);
  L.geoJSON(gj, {
    style: { color: '#e8a300', weight: 2.4, opacity: 1, fill: false, dashArray: '10,6' },
    onEachFeature: (f, l) => l.bindTooltip('標高 200 m（この線より低い側が重点管理地域）',
      { sticky: true })
  }).addTo(S.layers.contour);
  S.contourLoaded = true;
}

async function loadNara() {
  if (S.naraLoaded) return;
  const gj = await (await fetch('/api/kosyoban?nara=2&bbox=140.30,41.95,140.90,42.35')).json();
  L.geoJSON(gj, {
    style: f => {
      const risk = (f.properties.elev != null && f.properties.elev <= 200);
      return { color: risk ? '#d9480f' : '#5f3dc4', weight: 2,
               fillColor: risk ? '#e8590c' : '#7048e8', fillOpacity: .35, opacity: .95 };
    },
    onEachFeature: (f, l) => l.bindTooltip(koTip(f.properties), { sticky: true })
  }).addTo(S.layers.nara);
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
    style: { color: '#5c6b7a', weight: 1.1, fill: true, fillOpacity: .03, opacity: .8 },
    onEachFeature: (f, l) => l.bindTooltip(koTip(f.properties), { sticky: true })
  }).addTo(S.layers.kosyoban);
}

/* ================================================================ 道具 */
function setMode(mode) {
  S.mode = mode;
  $('#t-add').classList.toggle('on', mode === 'add');
  $('#t-measure').classList.toggle('on', mode === 'measure');
  $('#map').classList.toggle('picking', mode === 'add' || mode === 'measure');
  const bar = $('#hint-bar');
  if (mode === 'measure') measureStart(); else measureClear();
  if (S.map) S.map.doubleClickZoom[mode === 'measure' ? 'disable' : 'enable']();
  if (mode === 'add') {
    $('#hint-text').textContent = 'オルソの上で、木のある場所をタップしてください';
    bar.hidden = false;
  } else if (mode === 'measure') {
    $('#hint-text').textContent =
      '地図を順にタップすると距離が出ます。ダブルクリックか Esc で終わり';
    bar.hidden = false;
  } else if (!S.move) {
    bar.hidden = true;
  }
}

/* ---------- 距離をはかる -------------------------------------------------
   林道からの到達距離や、笹薮を何m漕ぐかの見積りに使う。          */
function measureStart() {
  if (S.measure) return;
  S.measure = { pts: [], line: null, marks: [] };
}
function measureClear() {
  if (!S.measure) return;
  if (S.measure.line) S.layers.tools.removeLayer(S.measure.line);
  S.measure.marks.forEach(m => {
    if (m.getTooltip()) { m.closeTooltip(); m.unbindTooltip(); }
    S.layers.tools.removeLayer(m);
  });
  S.measure = null;
}
const fmtLen = m => m < 1000 ? `${m.toFixed(1)} m` : `${(m / 1000).toFixed(3)} km`;

function measureAdd(ll) {
  const M = S.measure;
  M.pts.push(ll);
  const dot = L.circleMarker(ll,
    { radius: 5, color: '#0b5fce', fillColor: '#fff', fillOpacity: 1, weight: 2.5 })
    .addTo(S.layers.tools);
  M.marks.push(dot);
  if (M.line) S.layers.tools.removeLayer(M.line);
  M.line = L.polyline(M.pts,
    { color: '#0b5fce', weight: 3, dashArray: '7,5' }).addTo(S.layers.tools);

  let total = 0;
  for (let i = 1; i < M.pts.length; i++) total += M.pts[i - 1].distanceTo(M.pts[i]);
  if (M.pts.length > 1) {
    const a = M.pts[M.pts.length - 2], b = M.pts[M.pts.length - 1];
    const seg = a.distanceTo(b);
    const br = bearing(a.lat, a.lng, b.lat, b.lng);
    dot.bindTooltip(
      `${fmtLen(total)}${M.pts.length > 2 ? `　区間 ${fmtLen(seg)}` : ''}` +
      `　${COMPASS[Math.round(br / 22.5) % 16]}`,
      { permanent: true, direction: 'right', className: 'measure-label' }).openTooltip();
    $('#hint-text').textContent =
      `合計 ${fmtLen(total)}（${M.pts.length} 点）　ダブルクリックか Esc で終わり`;
  }
}

/** 木を押したとき。計測中・登録中・位置直し中は地図の操作にゆずる。 */
function hitTree(ev, id) {
  if (S.mode || S.move) { onMapClick(ev); return; }
  L.DomEvent.stop(ev);
  openDetail(id);
}

/** 画面上で pxTol 以内にある一番近い木を返す。指のずれを吸収するため。 */
function nearestTree(latlng, pxTol) {
  const p0 = S.map.latLngToContainerPoint(latlng);
  let best = null, bd = pxTol;
  for (const t of S.trees) {
    if (t.lat == null) continue;
    const p = S.map.latLngToContainerPoint([t.lat, t.lon]);
    const d = Math.hypot(p.x - p0.x, p.y - p0.y);
    if (d <= bd) { best = t; bd = d; }
  }
  return best;
}

function onMapClick(e) {
  if (S.move && S.moveClick) { S.moveClick(e.latlng); return; }
  if (S.mode === 'measure') { measureAdd(e.latlng); return; }
  if (S.mode === 'add') { addTreeAt(e.latlng.lng, e.latlng.lat); return; }
  // マーカーそのものを外しても、近くの木なら開く
  const hit = nearestTree(e.latlng, 34);
  if (hit) openDetail(hit.id);
}

/* ---------- 木の登録 ---------- */
async function addTreeAt(lon, lat, opts) {
  try {
    const t = await api('tree', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({
        lon, lat, site: 'genchi', status: 'unsurveyed', priority: '中',
        loc_accuracy: 'オルソ上でタップして指定',
        _who: $('#who').value.trim()
      }, opts || {}))
    });
    toast(`${t.code} を登録しました`);
    setMode(null);
    await reload();
    openDetail(t.id);
    S.map.setView([t.lat, t.lon], Math.max(S.map.getZoom(), 19));
  } catch (e) { toast(e.message, true); }
}

/* ---------- 現在地 ---------- */
function gps() {
  if (!navigator.geolocation) { toast('この端末では現在地を取得できません', true); return; }
  toast('現在地を取得しています…');
  navigator.geolocation.getCurrentPosition(pos => {
    const { latitude: lat, longitude: lon, accuracy: acc } = pos.coords;
    if (S.here) { S.layers.tools.removeLayer(S.here.m); S.layers.tools.removeLayer(S.here.c); }
    const m = L.marker([lat, lon], {
      icon: L.divIcon({ className: '', html: '<div class="here-dot"></div>', iconSize: [18, 18], iconAnchor: [9, 9] })
    }).addTo(S.layers.tools);
    const c = L.circle([lat, lon], { radius: acc, color: '#0b5fce', weight: 1.5, fillOpacity: .1 })
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

/* ---------- 座標を読み取る ----------------------------------------------
   ジオグラフィカ・地理院地図・GPS機など、現場で使う道具が出す書き方を
   ひととおり受け付ける。

     42.105986, 140.683474          十進の度
     N42D06'21.5" E140D41'00.5"     度分秒（記号が前）
     42D06'21.5"N, 140D41'00.5"E    度分秒（記号が後ろ）
     N42D06.358' E140D41.008'       度分
     北緯42度6分21.5秒 東経140度41分0.5秒
     X=-210123.45 Y=35876.21        平面直角座標XI系（X=北, Y=東）
                                                                        */
function parseCoord(str) {
  let s = String(str == null ? '' : str)
    // 全角を半角に
    .replace(/[０-９Ａ-Ｚａ-ｚ．，＋－]/g,
      c => String.fromCharCode(c.charCodeAt(0) - 0xFEE0))
    .replace(/北緯/g, 'N').replace(/南緯/g, 'S')
    .replace(/東経/g, 'E').replace(/西経/g, 'W')
    .replace(/[°度]/g, ' ')
    .replace(/[\u2032\u2019\u2018'\u201B分]/g, ' ')
    .replace(/[\u2033\u201D\u201C"\u3003秒]/g, ' ')
    .trim();
  if (!s) return null;

  // 平面直角座標。X= / Y= と書いてあるときは、書いてあるとおりに読む。
  const mx = s.match(/X\s*[=:]?\s*(-?[\d.]+)/i);
  const my = s.match(/Y\s*[=:]?\s*(-?[\d.]+)/i);
  if (mx && my) return { xy: [parseFloat(my[1]), parseFloat(mx[1])] };  // [東, 北]

  // 数字と半球記号を順番どおりに拾う
  const items = [];
  const re = /([NSEW])|(-?\d+(?:\.\d+)?)/gi;
  let m;
  while ((m = re.exec(s))) {
    if (m[1]) items.push({ h: m[1].toUpperCase() });
    else items.push({ n: parseFloat(m[2]) });
  }
  if (!items.length) return null;

  // ふたつの値に切り分ける
  const hAt = items.map((t, k) => (t.h ? k : -1)).filter(k => k >= 0);
  const nAt = items.findIndex(t => t.n !== undefined);
  let groups;
  if (hAt.length === 2) {
    groups = hAt[0] < nAt
      ? [items.slice(hAt[0], hAt[1]), items.slice(hAt[1])]        // N42… E140…
      : [items.slice(0, hAt[0] + 1), items.slice(hAt[0] + 1)];    // 42…N 140…E
  } else if (hAt.length === 0) {
    const ns = items.filter(t => t.n !== undefined);
    if (ns.length < 2 || ns.length % 2) return null;
    groups = [ns.slice(0, ns.length / 2), ns.slice(ns.length / 2)];
  } else {
    return null;
  }

  const vals = groups.map(g => {
    const h = (g.find(t => t.h) || {}).h;
    const n = g.filter(t => t.n !== undefined).map(t => t.n);
    if (!n.length || n.length > 3) return null;
    if (n.length > 1 && (n[1] < 0 || n[1] >= 60)) return null;
    if (n.length > 2 && (n[2] < 0 || n[2] >= 60)) return null;
    const sign = n[0] < 0 ? -1 : 1;
    const v = sign * (Math.abs(n[0]) + (n[1] || 0) / 60 + (n[2] || 0) / 3600);
    return { v: (h === 'S' || h === 'W') ? -Math.abs(v) : v, h, split: n.length > 1 };
  });
  if (vals.some(v => !v)) return null;
  const [a, b] = vals;

  // どちらが緯度か
  if (a.h && b.h) {
    const lat = 'NS'.indexOf(a.h) >= 0 ? a : b;
    const lon = lat === a ? b : a;
    if ('EW'.indexOf(lon.h) < 0) return null;
    return { lat: lat.v, lon: lon.v };
  }
  // 記号が無い場合。度分・度分秒で書かれているなら必ず緯度経度。
  const degLike = (a.split || b.split)
    || (Math.abs(a.v) <= 180 && Math.abs(b.v) <= 180);
  if (degLike) {
    if (Math.abs(a.v) > 90 && Math.abs(b.v) <= 90) return { lat: b.v, lon: a.v };
    return { lat: a.v, lon: b.v };
  }
  // それ以外は平面直角座標。日本式（X=北, Y=東）で仮に読み、
  // 日本の外に出たら resolveCoord で順番を入れ替えてためす。
  return { xy: [b.v, a.v], xyGuess: true };
}

/** 平面直角XI系 -> 緯度経度。逆変換を持たないので /api/locate で数回寄せる。 */
async function xyToLatLon(east, north) {
  let lat = 44 + north / 111000;
  let lon = 140.25 + east / (111320 * Math.cos(lat * Math.PI / 180));
  for (let i = 0; i < 8; i++) {
    const r = await api(`locate?lon=${lon.toFixed(7)}&lat=${lat.toFixed(7)}`);
    const dx = east - r.x, dy = north - r.y;
    if (Math.abs(dx) < 0.02 && Math.abs(dy) < 0.02) break;
    lat += dy / 111000;
    lon += dx / (111320 * Math.cos(lat * Math.PI / 180));
  }
  return { lat, lon };
}

/** 「42.062733」を「42°06'27.33"」と読み替える。読めなければ null。
    小数以下を MM SS.ss と見なす。分・秒が60以上なら度分秒ではありえないので null。 */
function decimalAsDms(numText) {
  const m = /^(-?)(\d+)\.(\d{4,})$/.exec(String(numText).trim());
  if (!m) return null;
  const f = m[3];
  const mm = +f.slice(0, 2);
  const ss = +(f.slice(2, 4) + '.' + (f.slice(4) || '0'));
  if (!(mm < 60) || !(ss < 60)) return null;
  const v = +m[2] + mm / 60 + ss / 3600;
  return m[1] === '-' ? -v : v;
}

/** よくある書き写し間違いを拾う。
    ジオグラフィカやGPS機の表示  42°06'27.33"  を、記号を落として
    「42.062733」と10進のつもりで書いてしまうと、まったく別の場所になる。
    この例では 23km ずれる。しかも両方とも森町の中に落ちるので気づけない。
    そこで、10進として読んだ値の小数部が「分・秒」としても読めて、
    そちらも森町に入るときは、どちらの意味かを画面で聞く。 */
function dmsMisreading(text) {
  const s = String(text == null ? '' : text)
    .replace(/[０-９．，＋－]/g, c => String.fromCharCode(c.charCodeAt(0) - 0xFEE0));
  // 記号が書いてあれば度分秒だと分かるので、この間違いは起きない
  if (/[°'"′″’”度分秒NSEWnsew]/.test(s)) return null;
  const m = s.match(/(-?\d+\.\d{4,})[^\d.-]+(-?\d+\.\d{4,})/);
  if (!m) return null;
  const a = decimalAsDms(m[1]), b = decimalAsDms(m[2]);
  if (a == null || b == null) return null;
  let lat = a, lon = b;
  if (Math.abs(a) > 90 && Math.abs(b) <= 90) { lat = b; lon = a; }
  const alt = { lat, lon };
  if (!NEAR_MORI(alt)) return null;              // 森町の外なら黙っている
  const lit = parseCoord(text);
  if (!lit || lit.xy || lit.lat == null) return null;
  const d = L.latLng(lit.lat, lit.lon).distanceTo(L.latLng(alt.lat, alt.lon));
  if (d < 500) return null;                      // ほぼ同じ場所なら言う必要がない
  return { alt, m: d, text: `${fmt.dms(lat, true)} ${fmt.dms(lon, false)}` };
}

/** その緯度経度にいちばん近い登録記録と、その距離[m] */
function nearestRecord(lat, lon) {
  let best = null, bd = Infinity;
  for (const t of S.trees) {
    if (t.lat == null) continue;
    const m = L.latLng(lat, lon).distanceTo(L.latLng(t.lat, t.lon));
    if (m < bd) { bd = m; best = t; }
  }
  return best ? { t: best, m: bd } : null;
}

/** 森町のあたりに収まっているか（読み違いに気づくため） */
const NEAR_MORI = p => !!p && p.lat > 41.6 && p.lat < 42.6 && p.lon > 140.0 && p.lon < 141.2;

async function resolveCoord() {
  return coordFrom($('#coord-input').value);
}

/** 文字列から緯度経度を求める。読めなければ null。 */
async function coordFrom(text) {
  const v = parseCoord(text);
  if (!v) return null;
  if (v.xy) {
    const [east, north] = v.xy;
    let p = await xyToLatLon(east, north);
    if (v.xyGuess && !NEAR_MORI(p)) {
      const q = await xyToLatLon(north, east);
      if (NEAR_MORI(q)) p = q;
    }
    return isFinite(p.lat) && isFinite(p.lon) ? p : null;
  }
  if (!isFinite(v.lat) || !isFinite(v.lon)) return null;
  if (Math.abs(v.lat) > 90 || Math.abs(v.lon) > 180) return null;
  return { lat: v.lat, lon: v.lon };
}

function bindCoord() {
  const prev = $('#coord-preview');
  let t;
  $('#coord-input').addEventListener('input', () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      const p = await resolveCoord();
      if (!p) { prev.hidden = true; return; }
      try {
        const r = await api(`locate?lon=${p.lon.toFixed(7)}&lat=${p.lat.toFixed(7)}`);
        prev.hidden = false;
        const near = nearestRecord(p.lat, p.lon);
        prev.innerHTML = `${NEAR_MORI(p) ? '' :
            '<b style="color:#c92a2a">森町から離れた場所です。書き方を確かめてください。</b><br>'
          }緯度 <b>${p.lat.toFixed(7)}</b>　経度 <b>${p.lon.toFixed(7)}</b><br>
          平面直角XI系 <b>${fmt.xy(r.x, r.y)}</b><br>
          ${r.rinpan ? `<b>${pad0(r.rinpan)} 林班 - ${pad0(r.kosyoban)} 小班</b>${
            r.sp_main ? '（' + esc(r.sp_main) + '）' : ''}` : '民有林の小班の外'}
          ${r.ground_elev != null ? `<br>標高 <b>${r.ground_elev} m</b>${
            r.ground_elev <= 200 ? '（重点管理）' : ''}` : ''}
          ${near ? `<br>いちばん近い記録 <b>${esc(near.t.code)}</b>（${fmtLen(near.m)}）`
                 : '<br>近くに登録された記録はありません'}
          ${(() => {
            const w = dmsMisreading($('#coord-input').value);
            if (!w) return '';
            const n2 = nearestRecord(w.alt.lat, w.alt.lon);
            return `<div class="askbox">
              <b>もしかして度分秒（${esc(w.text)}）ですか？</b><br>
              そちらは <b>${w.alt.lat.toFixed(7)}, ${w.alt.lon.toFixed(7)}</b>
              ${n2 ? `（${esc(n2.t.code)} から ${fmtLen(n2.m)}）` : ''} で、
              いま読んでいる場所から <b>${fmtLen(w.m)}</b> 離れています。<br>
              GPS機の <code>42°06'27.33"</code> を記号だけ外して
              <code>42.062733</code> と書くと、まったく別の場所になります。
              <button class="mini" id="use-dms">度分秒として読み直す</button>
            </div>`;
          })()}`;
        const ud = $('#use-dms');
        if (ud) ud.addEventListener('click', () => {
          const w = dmsMisreading($('#coord-input').value);
          if (!w) return;
          $('#coord-input').value = w.text;
          $('#coord-input').dispatchEvent(new Event('input', { bubbles: true }));
        });
      } catch (e) { prev.hidden = true; }
    }, 320);
  });
  $('#coord-input').addEventListener('keydown',
    e => { if (e.key === 'Enter') $('#coord-go').click(); });

  $('#coord-go').addEventListener('click', async () => {
    const p = await resolveCoord();
    if (!p) { toast('座標を読み取れませんでした', true); return; }
    closeModal('#modal-coord');
    S.map.setView([p.lat, p.lon], Math.max(S.map.getZoom(), 18));
    const ping = L.circleMarker([p.lat, p.lon],
      { radius: 16, color: '#e8a300', weight: 4, fill: false }).addTo(S.layers.tools);
    setTimeout(() => S.layers.tools.removeLayer(ping), 2600);
    toast(`移動しました: ${p.lat.toFixed(6)}, ${p.lon.toFixed(6)}`);
  });

  $('#coord-add').addEventListener('click', async () => {
    const p = await resolveCoord();
    if (!p) { toast('座標を読み取れませんでした', true); return; }
    closeModal('#modal-coord');
    await addTreeAt(p.lon, p.lat, { loc_accuracy: '緯度経度を入力して指定' });
  });
}

/* ---------------------------------------------------------------- UI */
function openModal(sel) {
  $(sel).hidden = false;
  const i = $(sel).querySelector('input:not([type=hidden]):not([type=file])');
  if (i) setTimeout(() => i.focus(), 60);
}
function closeModal(sel) { $(sel).hidden = true; }

function bindUI() {
  ['#l-contour', '#l-rinpan', '#l-kosyoban', '#l-nara'].forEach(s => $(s).checked = false);
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
  $('#t-gps').addEventListener('click', gps);
  $('#t-coord').addEventListener('click', () => openModal('#modal-coord'));
  $('#t-photos').addEventListener('click', () => openModal('#modal-photos'));
  $('#t-import').addEventListener('click', () => openModal('#modal-import'));
  $('#t-measure').addEventListener('click',
    () => setMode(S.mode === 'measure' ? null : 'measure'));
  $('#t-full').addEventListener('click', toggleFull);
  $('#hint-cancel').addEventListener('click', () => {
    if (S.move) { const id = S.move.id; endMove(id); return; }
    setMode(null);
  });

  $('#side-toggle').addEventListener('click', () => {
    $('#app').classList.toggle('side-off');
    setTimeout(() => S.map.invalidateSize(), 220);
  });

  $('#btn-help').addEventListener('click', () => openModal('#modal-help'));
  if (S.boot && S.boot.build) {
    const el = $('#build');
    if (el) el.textContent = '画面の版: ' + S.boot.build;
    console.log('ナラ枯れビューアー 画面の版:', S.boot.build);
  }
  $$('[data-close]').forEach(b => b.addEventListener('click', e => {
    e.target.closest('.modal').hidden = true;
  }));
  $$('.modal').forEach(m => m.addEventListener('click', e => {
    if (e.target === m) m.hidden = true;
  }));

  $('#sort').addEventListener('change', renderList);

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
      j: () => openModal('#modal-coord'),
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


/* ============================================ 画面全体へのドロップ */
const IMG_RE = /\.(tif|tiff|jpe?g|png)$/i;
const WORLD_RE = /\.(jgw|pgw|tfw|wld|prj)$/i;

function bindDropzone() {
  const dz = $('#dropzone');
  let depth = 0;
  window.addEventListener('dragenter', e => {
    if (!e.dataTransfer || ![...e.dataTransfer.types].includes('Files')) return;
    depth++;
    dz.hidden = false;
    $('#dz-title').textContent = 'ここにドロップ';
    $('#dz-sub').innerHTML =
      '写真（JPEG）なら位置情報から木に自動で紐づけます<br>'
      + 'オルソ・衛星画像なら地図タイルを作ります';
  });
  window.addEventListener('dragover', e => { e.preventDefault(); });
  window.addEventListener('dragleave', () => {
    depth = Math.max(0, depth - 1);
    if (!depth) dz.hidden = true;
  });
  window.addEventListener('drop', async e => {
    e.preventDefault();
    depth = 0; dz.hidden = true;
    const files = [...(e.dataTransfer.files || [])];
    if (files.length) await routeFiles(files);
  });
}

async function routeFiles(files) {
  const imgs = files.filter(f => IMG_RE.test(f.name));
  const world = files.filter(f => WORLD_RE.test(f.name));
  // ワールドファイルが一緒、GeoTIFF、または大きい画像 → オルソとして扱う
  const asOrtho = world.length > 0
    || imgs.some(f => /\.(tif|tiff)$/i.test(f.name))
    || imgs.some(f => f.size > 12 * 1024 * 1024);
  if (asOrtho) {
    if (!imgs.length) { toast('画像が含まれていません', true); return; }
    openModal('#modal-import');
    await uploadOrtho(imgs, world);
  } else {
    if (!imgs.length) { toast('扱える画像がありません', true); return; }
    openModal('#modal-photos');
    await importPhotos(imgs);
  }
}

function putFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', '/api/upload?name=' + encodeURIComponent(file.name));
    xhr.upload.onprogress = ev => {
      if (ev.lengthComputable && onProgress) onProgress(ev.loaded / ev.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); }
        catch (e) { reject(new Error('応答を読めませんでした')); }
      } else {
        let m = xhr.statusText;
        try { m = JSON.parse(xhr.responseText).error || m; } catch (e) { }
        reject(new Error(m));
      }
    };
    xhr.onerror = () => reject(new Error('転送に失敗しました'));
    xhr.send(file);
  });
}

async function uploadOrtho(imgs, world) {
  $('#imp-form').hidden = false;
  $('#imp-progress').hidden = true;
  const main = imgs.slice().sort((a, b) => b.size - a.size)[0];
  const all = [...world, ...imgs];
  const total = all.reduce((a, f) => a + f.size, 0) || 1;
  const drop = $('#imp-drop');
  let sent = 0, saved = null;
  const show = (name, pct) => {
    drop.innerHTML = `<b>${esc(name)}</b> を転送中… ${pct}%<br>
      <span style="font-size:12px;font-weight:500">全体 ${(total / 1e6).toFixed(0)} MB</span>`;
  };
  try {
    for (const f of all) {
      show(f.name, ((sent / total) * 100).toFixed(0));
      const res = await putFile(f, p => show(f.name, ((sent + p * f.size) / total * 100).toFixed(0)));
      if (f === main) saved = res;
      sent += f.size;
    }
  } catch (e) {
    drop.innerHTML = '画像をここにドロップ';
    toast('転送できませんでした: ' + e.message, true);
    return;
  }
  drop.innerHTML = `<b>${esc(main.name)}</b> を受け取りました<br>
    <span style="font-size:12px;font-weight:500">下の内容を確認して「取り込みを始める」</span>`;
  $('#imp-path').value = (saved && saved.path) || '';
  guessSite(main.name);
  toast(`${all.length} ファイル（${(total / 1e6).toFixed(0)} MB）を受け取りました`);
}

/* ================================================ 画像の取り込み */
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

  const drop = $('#imp-drop'), file = $('#imp-file');
  drop.addEventListener('click', () => file.click());
  file.addEventListener('change', () => {
    const fs = [...file.files];
    uploadOrtho(fs.filter(f => IMG_RE.test(f.name)), fs.filter(f => WORLD_RE.test(f.name)));
  });
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
  };
  if (!opts.path) { toast('画像を選んでください', true); return; }
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
    ['pick', ''], ['code', '記録ID'], ['status', '調査'], ['work_status', '処理'],
    ['rinpan', '林班-小班'], ['sp_main', '森林簿樹種'], ['elev', '標高m'],
    ['latlon', '緯度経度'], ['survey_date', '調査日'], ['surveyor', '調査者'],
    ['access_note', '到達状況'], ['memo', 'メモ']
  ];
  $('#list thead').innerHTML = '<tr>' + cols.map(c =>
    c[0] === 'pick' ? '<th class="pick"></th>' : `<th>${c[1]}</th>`).join('') + '</tr>';

  const key = $('#sort').value;
  const order = S.boot.status.map(s => s.code);
  const arr = S.trees.slice();
  arr.sort((a, b) => {
    if (key === 'rinpan') return String(a.rinpan || '').localeCompare(String(b.rinpan || '')) ||
      String(a.kosyoban || '').localeCompare(String(b.kosyoban || ''));
    if (key === 'updated') return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
    return order.indexOf(a.status) - order.indexOf(b.status) ||
      String(a.code).localeCompare(String(b.code));
  });

  const lab = c => (S.boot.status.find(s => s.code === c) || {}).label || c;
  $('#list tbody').innerHTML = arr.map(t => `<tr data-id="${t.id}" class="${
      S.sel === t.id ? 'sel ' : ''}${S.picked.has(t.id) ? 'picked' : ''}">
    <td class="pick"><input type="checkbox" ${S.picked.has(t.id) ? 'checked' : ''}></td>
    <td class="mono">${esc(t.code)}</td>
    <td><span class="tag" style="background:${statusColor(t.status)}1f;color:${statusColor(t.status)}">${esc(lab(t.status))}</span></td>
    <td>${workTag(t)}</td>
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
    if (v('#bulk-date')) f.survey_date = v('#bulk-date');
    if (v('#bulk-surveyor')) f.surveyor = v('#bulk-surveyor');
    if (!Object.keys(f).length) { toast('変更する項目を入れてください', true); return; }
    if (!confirm(`${S.picked.size} 件をまとめて変更します。よろしいですか？`)) return;
    try {
      const r = await api('trees/bulk', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: [...S.picked], fields: f, _who: $('#who').value.trim() })
      });
      toast(`${r.changed} 件を更新しました`);
      ['#bulk-status', '#bulk-date', '#bulk-surveyor'].forEach(id => $(id).value = '');
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
  fd.append('site', 'genchi');
  let i = 0;
  for (const f of files) fd.append('p' + (i++), f, f.name);
  let r;
  try {
    const res = await fetch('/api/photos/import', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(((await res.json()) || {}).error || res.statusText);
    r = await res.json();
  } catch (e) {
    box.innerHTML = `<div class="pr-sum" style="background:#ffecec;border-color:#e8a0a0;border-left-color:#c92a2a;color:#a51111">取り込めませんでした: ${esc(e.message)}</div>`;
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
function startCompare() {
  const list = orthoSites();
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
    const s = orthoSites().find(x => x.id === id);
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
  if (S.ksbLayer) { S.layers.tools.removeLayer(S.ksbLayer); S.ksbLayer = null; }
  endMove(null);
  $('#detail').classList.add('closed');
  S.sel = null;
  if (S.selMarker) { S.layers.tools.removeLayer(S.selMarker); S.selMarker = null; }
}

/** 伐倒の関門にひっかかっているか（被害あり・未処理・民有林・同意なし） */
function needOwner(t) {
  return t.status === 'damaged'
    && (t.work_status || 'none') !== 'done'
    && t.land_class !== '国有林'
    && !['agreed', 'na'].includes(t.owner_status || '');
}

/** 現地調査1回ぶんのカード。折りたたんだ中が入力欄になっている。 */
function visitCard(v) {
  const lab = c => (S.boot.status.find(s => s.code === c) || {}).label || '（判定なし）';
  const col = c => (S.boot.status.find(s => s.code === c) || {}).color || '#adb5bd';
  const opt = (val, list) => list.map(o =>
    `<option ${String(val || '') === o ? 'selected' : ''}>${o}</option>`).join('');
  const id = v.id || 'new';
  return `<details class="visit" data-sid="${v.id || ''}" ${v.id ? '' : 'open'}>
    <summary>
      <b>${v.seq ? v.seq + '回目' : '新しい調査'}</b>
      <span class="tag" style="background:${col(v.result)}1f;color:${col(v.result)}">${esc(lab(v.result))}</span>
      <span class="vmeta">${esc(v.survey_date || '日付なし')}${v.surveyor ? '　' + esc(v.surveyor) : ''}${
        v.witness ? '　立会 ' + esc(v.witness) : ''}</span>
    </summary>
    <div class="vbody">
      <div class="f2">
        <div class="f"><label>調査日</label><input type="date" data-k="survey_date" value="${esc(v.survey_date || '')}"></div>
        <div class="f"><label>この調査の判定</label>
          <select data-k="result">
            <option value="">（判定なし）</option>
            ${S.boot.status.map(s => `<option value="${s.code}" ${v.result === s.code ? 'selected' : ''}>${esc(s.label)}</option>`).join('')}
          </select></div>
      </div>
      <div class="f2">
        <div class="f"><label>調査者</label><input data-k="surveyor" value="${esc(v.surveyor || '')}" placeholder="森町農林課 ○○"></div>
        <div class="f"><label>立会者</label><input data-k="witness" value="${esc(v.witness || '')}" placeholder="渡島総合振興局 ○○"></div>
      </div>
      <div class="f"><label>到達状況・アクセスの記録（到達できなかった場合はここに理由を）</label>
        <input data-k="access_note" value="${esc(v.access_note || '')}" placeholder="例）沢を渡れず接近不可。対岸から目視。"></div>
      <div class="f2">
        <div class="f"><label>樹種</label>
          <input data-k="species" list="dl-sp" value="${esc(v.species || '')}" placeholder="ミズナラ / コナラ / カシワ"></div>
        <div class="f"><label>胸高直径 cm</label><input type="number" data-k="dbh_cm" value="${v.dbh_cm ?? ''}" step="1"></div>
      </div>
      <div class="f2">
        <div class="f"><label>葉の変色</label><select data-k="leaf_color">${opt(v.leaf_color, ['', 'なし', '一部変色', '全体変色', '落葉'])}</select></div>
        <div class="f"><label>枯死状況</label><select data-k="dieback">${opt(v.dieback, ['', '健全', '衰弱', '枯死'])}</select></div>
      </div>
      <div class="f2">
        <div class="f"><label>穿孔（カシナガ）</label><select data-k="boring">${opt(v.boring, ['', 'なし', '少', '中', '多'])}</select></div>
        <div class="f"><label>フラス量</label><select data-k="frass">${opt(v.frass, ['', 'なし', '少', '中', '多'])}</select></div>
      </div>
      <div class="f"><label>周辺林相</label><input data-k="stand" value="${esc(v.stand || '')}" placeholder="例）ミズナラ・トドマツ混交、立木密度中"></div>
      <div class="f"><label>ナラ枯れでなかった場合の要因</label>
        <select data-k="misjudge_reason">${opt(v.misjudge_reason,
          ['', '根むくれによる枯れ', 'つる植物の枯れ', '自然枯れ', 'ナラ類以外の広葉樹',
           '針葉樹', '影・地形', '道路・裸地', '樹冠の重なり', '撮影時期の差',
           '解像度不足', '原因不明', 'その他'])}</select>
        <p class="hint">森町では「根むくれによる枯れ」「シラカンバに巻き付いたツルの枯れ」が
          実際に確認されています。</p></div>
      <div class="f"><label>採取したもの（枝・フラス・虫体・採取番号）</label>
        <input data-k="sample" value="${esc(v.sample || '')}" placeholder="例）フラス採取 No.3、診断依頼 9/12"></div>
      <div class="f"><label>この調査のメモ</label>
        <input data-k="note" value="${esc(v.note || '')}"></div>
      <div class="actions">
        <button class="btn primary vsave">この調査を保存</button>
        ${v.id ? '<button class="btn danger vdel">削除</button>' : '<button class="btn vcancel">やめる</button>'}
      </div>
    </div>
  </details>`;
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
      <span class="tag" style="background:${statusColor(t.status)}1f;color:${statusColor(t.status)}">${esc(lab(t.status))}</span>
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
            t.elev != null && t.elev <= 200 ? '　<span style="color:#a35200;font-size:12px">重点管理</span>' : ''}</span>
          <span></span></div>
        <!-- 樹高（DSM−DEM）はドローンDSMの誤差が大きく、材積などに使われると
             実害が出るため画面には出さない。データは残してある。
             航空レーザ測量のDTMが手に入ったら復活させること。
             docs/位置合わせと精度.md 参照。 -->
        ${moved != null ? `<div class="coordrow"><span class="lab">位置の調整</span>
          <span class="val" style="color:#1f6f33">元の位置から ${moved.toFixed(1)} m</span>
          <span></span></div>` : ''}
      </div>
      ${hasPos ? `<div class="maplinks">
        <a href="https://maps.gsi.go.jp/#18/${t.lat}/${t.lon}/" target="_blank" rel="noopener">地理院地図</a>
        <a href="https://www.google.com/maps/search/?api=1&query=${t.lat},${t.lon}" target="_blank" rel="noopener">Googleマップ</a>
        <a href="https://www.google.com/maps/dir/?api=1&destination=${t.lat},${t.lon}" target="_blank" rel="noopener">経路</a>
      </div>` : ''}
      ${nav}
      ${(() => {
        const ki = t.kosyoban_info;
        const rep = /代表点/.test(t.loc_accuracy || '');
        if (rep && ki) return `<div class="warnbox">
          <b>この位置は木の座標ではありません。</b>
          ${esc(pad0(t.rinpan))}林班-${esc(pad0(t.kosyoban))}小班（${ki.area_ha} ha）の
          <b>代表点（重心）</b>です。振興局から公表されているのが林班-小班までで、
          木そのものの座標が整理されていないためです。<br>
          <b>木は、この点から最大 ${ki.radius_m} m 離れている可能性があります。</b><br>
          オルソで枯れている木が見つかったら、下のボタンで位置を直してください。
          <button class="mini" id="btn-show-ksb">小班の範囲を地図に出す</button>
        </div>`;
        return `<p class="hint">位置の確からしさ：${esc(t.loc_accuracy || '—')}</p>`;
      })()}
      <div class="actions">
        ${hasPos ? '<button class="btn primary" id="btn-move">オルソ上で位置を直す</button>' : ''}
        ${moved != null ? '<button class="btn" id="btn-reset-pos">元に戻す</button>' : ''}
      </div>
      <div class="f" style="margin-top:4px">
        <label>${hasPos ? '緯度経度を入れて直す' : '緯度経度を入れて位置を決める'}</label>
        <div class="ll-edit">
          <input id="e-latlon" value="${esc(deg)}" placeholder="42.105986, 140.683474">
          <button class="btn" id="btn-setll">${hasPos ? 'この座標にする' : 'この座標にする'}</button>
        </div>
        <p class="hint">度分秒（<code>N42&deg;06'21.5" E140&deg;41'00.5"</code>）や
          平面直角XI系（<code>X=-209542.46 Y=37167.87</code>）でも貼り付けられます。
          GPS機の度分秒表示は0.1秒（約3m）までなので、
          正確に合わせたいときは10進の度を使ってください。</p>
      </div>
      ${t.source === 'survey' && moved == null ? `<p class="hint">
        この記録の位置は<b>小班の代表点</b>で、木そのものの座標ではありません。
        オルソで枯れている木が見つかったら、上のボタンでその位置に直せます。</p>` : ''}
    </div>
    <div class="sec">
      <h3>現地調査</h3>
      <p class="hint" style="margin-top:-2px">いま現地で何と判定されているか</p>
      <div class="statusgrid" id="sbtns">
        ${S.boot.status.map(s => `<button class="sbtn ${t.status === s.code ? 'on' : ''}"
           data-v="${s.code}" style="${t.status === s.code ? 'border-color:' + s.color + ';background:' + s.color + '33' : ''}">
           <i class="dot" style="background:${s.color}"></i>${esc(s.label)}</button>`).join('')}
      </div>

      <div class="visits" id="visits">
        ${(t.surveys || []).length === 0
          ? '<p class="hint">まだ調査の記録がありません。</p>'
          : (t.surveys || []).map(v => visitCard(v)).join('')}
      </div>
      <div class="actions"><button class="btn" id="add-visit">調査を追加する</button></div>
      <p class="hint">立ち合い調査で判定が変わった経緯は、ここに1回ずつ積んでください。
        いちばん新しい調査の内容が、上のステータスと一覧に反映されます。</p>
    </div>

    <div class="sec ${needOwner(t) ? 'sec-alert' : ''}">
      <h3>所有者確認</h3>
      ${needOwner(t) ? `<div class="warnbox" style="margin-top:0">
        <b>伐倒に進めません。</b>
        民有林の立木を伐るには所有者の同意が要ります。
        地番は <b>${esc(t.chiban || '不明')}</b>。ここから林地台帳をあたってください。
      </div>` : ''}
      <div class="f2">
        <div class="f"><label>所管区分</label>
          <select id="e-land_class">${opt(t.land_class, S.boot.land_class)}</select></div>
        <div class="f"><label>所有者確認</label>
          <select id="e-owner_status">${S.boot.owner.map(o =>
            `<option value="${o.code}" ${(t.owner_status || '') === o.code ? 'selected' : ''}
             >${esc(o.label)}</option>`).join('')}</select></div>
      </div>
      <div class="f"><label>確認のメモ（連絡先・経緯・林地台帳の確認結果）</label>
        <input id="e-owner_note" value="${esc(t.owner_note || '')}"
          placeholder="例）林地台帳で所有者を確認。9/10に電話、同意済み。"></div>
      ${t.land_class === '国有林' ? `<p class="hint">
        国有林は森林管理署の所管です。町の防除対象になるかを先に確かめてください。</p>` : ''}
    </div>

    <div class="sec">
      <h3>伐倒・くん蒸</h3>
      <p class="hint" style="margin-top:-2px">
        カシナガが脱出する<b>翌年5月末まで</b>に終える必要があります</p>
      <div class="statusgrid" id="wbtns">
        ${S.boot.work.map(w => `<button class="sbtn ${(t.work_status || 'none') === w.code ? 'on' : ''}"
           data-v="${w.code}" style="${(t.work_status || 'none') === w.code ? 'border-color:' + w.color + ';background:' + w.color + '33' : ''}">
           <i class="dot" style="background:${w.color}"></i>${esc(w.label)}</button>`).join('')}
      </div>
      <div class="f2" style="margin-top:10px">
        <div class="f"><label>処理方法</label>
          <select id="e-treatment">${opt(t.treatment, ['', '伐倒後焼却', '伐倒後チップ化・焼却', '伐倒くん蒸', '立木くん蒸', '未定'])}</select></div>
        <div class="f"><label>処理日</label><input type="date" id="e-treatment_date" value="${esc(t.treatment_date || '')}"></div>
      </div>
      <div class="f2">
        <div class="f"><label>施工者</label><input id="e-contractor" value="${esc(t.contractor || '')}" placeholder="請け負った業者名"></div>
        <div class="f"><label>年度</label><input id="e-fiscal_year" value="${esc(t.fiscal_year || '')}" placeholder="例）令和8年度"></div>
      </div>
      <div class="f2">
        <div class="f"><label>くん蒸剤</label><input id="e-fumigant" value="${esc(t.fumigant || '')}" placeholder="薬剤名"></div>
        <div class="f"><label>使用量</label><input id="e-fumigant_amount" value="${esc(t.fumigant_amount || '')}" placeholder="例）1.5 kg"></div>
      </div>
      <div class="f"><label>材積 m³<span class="sub">（補助金・積算に使います）</span></label>
        <div class="ll-edit">
          <input type="number" id="e-volume_m3" value="${t.volume_m3 ?? ''}" step="0.01" placeholder="伐倒した材の体積">
          <button class="btn" id="calc-vol" title="胸高直径から概算します">直径から概算</button>
        </div></div>
      <div class="f2">
        <div class="f"><label>処理後の確認者</label><input id="e-checked_by" value="${esc(t.checked_by || '')}"></div>
        <div class="f"><label>確認日</label><input type="date" id="e-checked_date" value="${esc(t.checked_date || '')}"></div>
      </div>
    </div>

    <div class="sec">
      <h3>メモ</h3>
      <div class="f"><textarea id="e-memo" placeholder="現地で気づいたこと">${esc(t.memo || '')}</textarea></div>
      <div class="actions"><button class="btn primary" id="save">保存</button></div>
    </div>

    <div class="sec">
      <h3>写真</h3>
      <div class="photos" id="ph">${(t.photos || []).map(p =>
        `<figure><img src="/data/photos/${encodeURIComponent(p.filename)}" alt="${esc(p.caption || '')}"
          onclick="window.open(this.src,'_blank')"><button data-pid="${p.id}" title="削除">×</button></figure>`).join('')}</div>
      <div class="drop" id="drop" style="margin-top:9px">
        写真をここにドロップ／クリックして選択
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
      { radius: 17, color: '#0b5fce', weight: 3.5, fill: false, interactive: false })
      .addTo(S.layers.tools);
  }
}

/** 選んだ記録の小班の輪郭を地図に出す。「木はこの中のどこか」を目で見せる。 */
function showKosyoban(t) {
  if (S.ksbLayer) { S.layers.tools.removeLayer(S.ksbLayer); S.ksbLayer = null; }
  const ki = t.kosyoban_info;
  if (!ki || !ki.geom) { toast('小班の形が取れませんでした', true); return; }
  S.ksbLayer = L.geoJSON(ki.geom, {
    style: { color: '#d9480f', weight: 3, fillColor: '#d9480f', fillOpacity: 0.10 }
  }).addTo(S.layers.tools);
  S.map.fitBounds(S.ksbLayer.getBounds(), { padding: [40, 40] });
  toast(`${pad0(t.rinpan)}林班-${pad0(t.kosyoban)}小班（${ki.area_ha} ha）。`
      + `木はこの中のどこかにあります`);
}

function wireDetail(t, coords) {
  $('.d-close').addEventListener('click', closeDetail);

  $$('.copy').forEach(b => b.addEventListener('click', () => copyText(coords[b.dataset.c], b)));

  // --- 処理ステータス ---
  $$('#wbtns .sbtn').forEach(b => b.addEventListener('click', async () => {
    try {
      await save({ work_status: b.dataset.v });
      toast('処理ステータスを更新しました');
      await reload();
      openDetail(t.id);
    } catch (e) { toast(e.message, true); }
  }));

  // --- 現地調査の記録 ---
  const wireVisits = () => {
    $$('#visits .visit').forEach(box => {
      const sid = box.dataset.sid;
      const grab = () => {
        const d = {};
        $$('[data-k]', box).forEach(el => { d[el.dataset.k] = el.value; });
        if (sid) d.id = Number(sid);
        return d;
      };
      const sv = $('.vsave', box);
      if (sv) sv.addEventListener('click', async () => {
        try {
          await api('tree/' + t.id + '/survey', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Object.assign(grab(), { _who: $('#who').value.trim() }))
          });
          toast('調査を保存しました');
          await reload();
          openDetail(t.id);
        } catch (e) { toast(e.message, true); }
      });
      const dl = $('.vdel', box);
      if (dl) dl.addEventListener('click', async () => {
        if (!confirm('この調査の記録を削除します。よろしいですか？')) return;
        try {
          await api('tree/' + t.id + '/survey/' + sid + '/delete', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ _who: $('#who').value.trim() })
          });
          toast('削除しました');
          await reload();
          openDetail(t.id);
        } catch (e) { toast(e.message, true); }
      });
      const cx = $('.vcancel', box);
      if (cx) cx.addEventListener('click', () => box.remove());
    });
  };
  wireVisits();

  $('#add-visit').addEventListener('click', () => {
    if ($('#visits .visit[data-sid=""]')) return;
    const n = (t.surveys || []).length + 1;
    $('#visits').insertAdjacentHTML('beforeend', visitCard({
      seq: n, survey_date: fmtDate(new Date()),
      surveyor: $('#who').value.trim(), result: t.status
    }));
    const hint = $('#visits .hint');
    if (hint) hint.remove();
    wireVisits();
    $('#visits .visit:last-child').scrollIntoView({ block: 'center' });
  });

  // --- 材積の概算 ---
  const cv = $('#calc-vol');
  if (cv) cv.addEventListener('click', () => {
    const d = Number(t.dbh_cm);
    if (!d) { toast('胸高直径が入っていません。調査の記録に入れてください', true); return; }
    // 立木材積の目安。胸高断面積 × 樹高 × 形状比。
    // 樹高が無いので広葉樹の目安 20m、形数 0.45 を使う。あくまで概算。
    const v = Math.PI * Math.pow(d / 200, 2) * 20 * 0.45;
    $('#e-volume_m3').value = v.toFixed(2);
    toast(`胸高直径 ${d} cm から概算しました（樹高20m・形数0.45の目安）。実測値に直してください`);
  });

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
    // 現地調査の項目は「調査を追加する」側で持つので、ここでは扱わない
    for (const k of ['land_class', 'owner_status', 'owner_note',
      'treatment', 'treatment_date', 'contractor', 'fiscal_year',
      'fumigant', 'fumigant_amount', 'volume_m3',
      'checked_by', 'checked_date', 'memo']) {
      const el = $('#e-' + k);
      if (el) d[k] = el.value;
    }
    try { await save(d); toast('保存しました'); await reload(); }
    catch (e) { toast(e.message, true); }
  });

  const ksb = $('#btn-show-ksb');
  if (ksb) ksb.addEventListener('click', () => showKosyoban(t));

  const mv = $('#btn-move');
  if (mv) mv.addEventListener('click', () => startMove(t));

  $('#btn-setll').addEventListener('click', async () => {
    const p = await coordFrom($('#e-latlon').value);
    if (!p) { toast('座標を読み取れませんでした', true); return; }
    const d = (t.lat != null)
      ? L.latLng(t.lat, t.lon).distanceTo(L.latLng(p.lat, p.lon)) : null;
    let msg = `【${t.code} の位置を変えます】\n\n`
            + `変更前  ${t.lat != null ? t.lat.toFixed(7) + ', ' + t.lon.toFixed(7) : '（未設定）'}\n`
            + `変更後  ${p.lat.toFixed(7)}, ${p.lon.toFixed(7)}\n`;
    if (d != null) {
      msg += `\nこの記録が ${fmtLen(d)} 動きます。\n`;
      if (d > 300) {
        const near = nearestRecord(p.lat, p.lon);
        msg += `\n★ ${fmtLen(d)} は離れすぎているかもしれません。\n`
             + `  ${t.code} ではなく、別の記録を開いていませんか？\n`;
        if (near && near.t.id !== t.id) {
          msg += `  入れた座標にいちばん近い記録は ${near.t.code}（${fmtLen(near.m)}）です。\n`;
        }
        msg += `  新しい木なら、この画面ではなく「座標」から\n`
             + `  「ここに木を登録」を使ってください。\n`;
      }
    }
    // 動かした先が別の小班になるなら知らせる。
    // 振興局の記録は「林班-小班」で来ているので、そこから外れたら
    // 座標の入れ間違いか、小班界ぎわのどちらかを疑う必要がある。
    try {
      const lc = await api(`locate?lon=${p.lon.toFixed(7)}&lat=${p.lat.toFixed(7)}`);
      const was = `${pad0(t.rinpan || '')}-${pad0(t.kosyoban || '')}`;
      const now = `${pad0(lc.rinpan || '')}-${pad0(lc.kosyoban || '')}`;
      if (t.rinpan && was !== now) {
        msg += `
★ 林班-小班が変わります： ${was} → ${lc.rinpan ? now : '民有林の小班の外'}
`
             + `  この記録は ${was} 小班のものとして登録されています。
`
             + `  座標の入れ間違いか、小班界のすぐ際でないか確かめてください。
`;
      }
    } catch (e) { /* 引けなくても位置の変更は妨げない */ }

    const mis = dmsMisreading($('#e-latlon').value);
    if (mis) {
      msg += `\n★ もしかして度分秒（${mis.text}）ですか？\n`
           + `  そちらは ${mis.alt.lat.toFixed(7)}, ${mis.alt.lon.toFixed(7)} で、\n`
           + `  いま読んでいる場所から ${fmtLen(mis.m)} 離れています。\n`
           + `  GPS機の 42°06'27.33" を記号だけ外して 42.062733 と書くと、\n`
           + `  まったく別の場所になります。\n`;
    }
    if (!NEAR_MORI(p)) msg += '\n※森町から離れた場所です。書き方を確かめてください。';
    if (!confirm(msg + '\nよろしいですか？')) return;
    try {
      await save({ lon: p.lon, lat: p.lat, loc_accuracy: '緯度経度を入力して指定' });
      toast('位置を変えました');
      await reload();
      S.map.setView([p.lat, p.lon], Math.max(S.map.getZoom(), 18));
      openDetail(t.id);
    } catch (e) { toast(e.message, true); }
  });

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
  drop.addEventListener('dragleave', e => {
    e.preventDefault(); drop.classList.remove('over');
  });
  drop.addEventListener('drop', e => {
    e.preventDefault(); e.stopPropagation();
    drop.classList.remove('over');
    upload(t.id, e.dataTransfer.files);
  });
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
    radius: 7, color: '#e8a300', weight: 3, fillColor: '#fff', fillOpacity: .6,
    interactive: false, dashArray: '3,3'
  }).addTo(S.layers.tools);
  const line = L.polyline([start, start], {
    color: '#e8a300', weight: 2.4, dashArray: '6,5', interactive: false
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
  const st = await api('stats');
  const n = st.total || 0;
  const pct = v => n ? Math.round(v / n * 1000) / 10 : 0;
  const seg = S.boot.status.map(s =>
    `<i style="width:${pct(st.status[s.code] || 0)}%;background:${s.color}" title="${s.label}"></i>`).join('');

  const dw = st.damaged_work || {};
  const cards = [
    ['登録の総数', n, '森町全体'],
    ['未調査', st.status.unsurveyed || 0, '現地確認がまだ'],
    ['被害あり', st.status.damaged || 0, 'ナラ枯れと確定'],
    ['到達できず', st.status.unreachable || 0, '再訪の検討'],
    ['処理済', dw.done || 0, '被害木のうち片づいた分'],
    ['処理が残り', st.to_treat || 0, '5月末までに要処理'],
  ].map(([k, v, s]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');

  const rows = st.by_rinpan.filter(r => r.rinpan).map(r => `<tr>
    <td>${esc(pad0(r.rinpan))} 林班</td><td>${esc(r.chiku || '')}</td>
    <td>${r.n}</td><td>${r.unsurveyed}</td>
    <td style="color:#c92a2a">${r.damaged}</td>
    <td style="color:#2f9e44">${r.treated}</td>
    <td style="color:#e8590c;font-weight:700">${r.to_treat || 0}</td></tr>`).join('');

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

    ${renderWork(st)}
    ${renderUnreachable(st)}

    <h2>林班ごとの内訳</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>林班</th><th>地区</th><th>登録</th><th>未調査</th>
        <th>被害あり</th><th>うち処理済</th><th>要処理</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6">データがありません</td></tr>'}</tbody></table></div>`;


  $$('#board button.go-tree').forEach(b => b.addEventListener('click', () => {
    showView('map');
    openDetail(Number(b.dataset.id));
    const t = S.byId.get(Number(b.dataset.id));
    if (t && t.lat != null) setTimeout(() => {
      S.map.invalidateSize();
      S.map.setView([t.lat, t.lon], 19);
    }, 80);
  }));

  $$('#board button.go').forEach(b => b.addEventListener('click', () => {
    showView('map');
    $('#l-ortho').checked = true;
    setTimeout(() => {
      S.map.invalidateSize();
      S.map.setView([Number(b.dataset.lat), Number(b.dataset.lon)], 19);
    }, 80);
  }));
}

/** 被害木が伐倒・くん蒸まで進んでいるか。止まっている木を名指しで出す。 */
function renderWork(st) {
  const dmg = st.status.damaged || 0;
  if (!dmg) return '';
  const dw = st.damaged_work || {};
  const ow = st.owner || {};
  const lab = (list, c) => (list.find(x => x.code === c) || {}).label || c;
  const seg = S.boot.work.map(w =>
    `<i style="width:${dmg ? (dw[w.code] || 0) / dmg * 100 : 0}%;background:${w.color}"
        title="${w.label} ${dw[w.code] || 0}"></i>`).join('');

  const blocked = st.blocked || [];
  const bl = blocked.length ? `
    <div class="note warn" style="margin-top:12px">
      <b>所有者の同意が取れていない被害木が ${blocked.length} 件あります。</b><br>
      民有林の立木を伐るには所有者の同意が要ります。ここが済まないと伐倒に進めません。
      <div class="blocked">${blocked.map(b => `
        <button class="mini go-tree" data-id="${b.id}">${esc(b.code)}</button>
        <span>${esc(pad0(b.rinpan || ''))}林班-${esc(pad0(b.kosyoban || ''))}小班
          ／${esc(b.land_class || '所管不明')}
          ／${esc(lab(S.boot.owner, b.owner_status))}</span>`).join('')}</div>
    </div>` : `
    <div class="note" style="margin-top:12px">
      所有者の同意が取れていない被害木はありません。</div>`;

  return `
    <h2>被害木の処理はどこまで進んだか</h2>
    <p class="hint">「被害あり」と確定した <b>${dmg}</b> 件の内訳です。
      調査の判定（被害あり）はそのまま残るので、処理済にしても
      「被害ありだった」ことは消えません。</p>
    <div class="bar" style="height:14px">${seg}</div>
    <div style="font-size:11.5px;color:var(--fg3);margin-top:5px">
      ${S.boot.work.map(w => `<span style="margin-right:14px">
        <i style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${w.color}"></i>
        ${w.label} ${dw[w.code] || 0}</span>`).join('')}
    </div>
    <div style="font-size:11.5px;color:var(--fg3);margin-top:7px">
      所有者確認：${S.boot.owner.map(o =>
        `<span style="margin-right:12px">${o.label} ${ow[o.code] || 0}</span>`).join('')}
    </div>
    ${bl}`;
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
        ? `<span style="color:#1f6f33;font-weight:700">あり</span><br><span style="font-size:12px;color:var(--fg3)">${esc(u.covered_by.name)}</span>`
        : '<span style="color:var(--fg3)">なし</span>'}</td>
      <td>${u.covered_by
        ? `<button class="mini go" data-lon="${u.lon}" data-lat="${u.lat}">オルソで見る</button>` : ''}</td></tr>`).join('');

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
    `<div style="padding:20px;color:#c92a2a">起動できませんでした: ${esc(e.message)}</div>`);
});
