/* 同期エンジン
 *
 *   現地（圏外）             電波のあるところ
 *   ────────────            ─────────────────
 *   端末の中に書く    →      ①送る ②写真を送る ③もらう
 *
 * 順番に意味がある。
 *   ① 先に自分の分を送る … 電池が切れる・端末を落とす前に、現場の記録を逃がす。
 *   ② 写真はそのあと     … 1枚ずつ送るので途中で切れても続きからやれる。
 *   ③ 最後にもらう       … 事務所側の変更（処理済になった等）を受け取る。
 *
 * 途中で電波が切れても壊れないように、どの段階も「やり直せば済む」形にしてある。
 * 送信は uuid で冪等（同じものを2回送っても増えない）。
 * 受信は seq（通し番号）の続きからなので、切れたところから再開できる。
 */
(function (root) {
  'use strict';

  var S = root.Store;
  var listeners = [];
  var running = false;
  var state = {
    online: navigator.onLine,
    reachable: null,      // サーバーに届くか（null=未確認）
    busy: false,
    phase: '',
    progress: null,       // {done, total}
    lastSync: null,
    lastError: null,
    seq: 0,
    pending: { trees: 0, surveys: 0, tracks: 0, photos: 0 }
  };

  function emit() { listeners.forEach(function (f) { try { f(state); } catch (e) {} }); }
  function on(f) { listeners.push(f); f(state); }

  /* ---------------- 端末の名前 ---------------- */
  function device() {
    return Promise.all([
      S.getKV('device_id'), S.getKV('device_name'), S.getKV('who')
    ]).then(function (v) {
      var id = v[0];
      if (!id) { id = S.uuid(); S.setKV('device_id', id); }
      return { id: id, name: v[1] || guessName(), who: v[2] || '',
               ua: navigator.userAgent.slice(0, 180) };
    });
  }

  function guessName() {
    var ua = navigator.userAgent;
    if (/iPhone/.test(ua)) return 'iPhone';
    if (/iPad/.test(ua)) return 'iPad';
    if (/Android/.test(ua)) return 'Android';
    return 'パソコン';
  }

  /* ---------------- 届くかどうか ---------------- */
  function ping(ms) {
    var ctl = new AbortController();
    var t = setTimeout(function () { ctl.abort(); }, ms || 6000);
    return fetch('/api/sync/state', { cache: 'no-store', signal: ctl.signal })
      .then(function (r) { clearTimeout(t); return r.ok; })
      ['catch'](function () { clearTimeout(t); return false; });
  }

  /* ---------------- 未送信の数 ---------------- */
  function countPending() {
    return Promise.all([
      S.byIndex('trees', 'dirty', 1),
      S.byIndex('surveys', 'dirty', 1),
      S.byIndex('tracks', 'dirty', 1),
      S.byIndex('photos', 'state', 'pending')
    ]).then(function (v) {
      state.pending = { trees: v[0].length, surveys: v[1].length,
                        tracks: v[2].length, photos: v[3].length };
      state.pendingTotal = v[0].length + v[1].length + v[2].length + v[3].length;
      emit();
      return state.pendingTotal;
    });
  }

  /* ---------------- ①送る ---------------- */
  function pushAll(dev) {
    return Promise.all([
      S.byIndex('trees', 'dirty', 1),
      S.byIndex('surveys', 'dirty', 1),
      S.byIndex('tracks', 'dirty', 1)
    ]).then(function (v) {
      var trees = v[0], surveys = v[1], tracks = v[2];
      if (!trees.length && !surveys.length && !tracks.length) {
        return { applied: {}, codes: {}, skipped: true };
      }
      state.phase = '記録を送っています（' +
        (trees.length + surveys.length + tracks.length) + '件）';
      emit();
      var body = {
        device: dev,
        trees: trees.map(clean),
        surveys: surveys.map(clean),
        tracks: tracks.map(cleanTrack)
      };
      return fetch('/api/sync/push', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(readJson).then(function (res) {
        // 送れたものだけ印を外す。返事が来なかったものは次回もう一度送る。
        //
        // 送っているあいだにも現場は入力を続けている。
        // 送った時点の updated_at と、いま端末にあるものの updated_at を見比べ、
        // 変わっていたら「まだ未送信」のままにする。
        // ここを雑にすると、送信中に入れた1本ぶんが黙って消える。
        var mark = function (store, list) {
          return Promise.all(list.map(function (o) {
            return S.get(store, o.uuid).then(function (now) {
              if (!now) return null;
              if (now.updated_at !== o.updated_at) {
                // 送ったあとに直されている。コードだけ引き継いで、印は残す。
                if (store === 'trees' && res.codes && res.codes[o.uuid]) {
                  now.code = res.codes[o.uuid];
                  now.id = res.ids ? res.ids[o.uuid] : now.id;
                  return now;
                }
                return null;
              }
              now.dirty = 0;
              if (store === 'trees' && res.codes && res.codes[o.uuid]) {
                now.code = res.codes[o.uuid];
                now.id = res.ids ? res.ids[o.uuid] : now.id;
              }
              return now;
            });
          })).then(function (recs) {
            return S.putMany(store, recs.filter(Boolean));
          });
        };
        return Promise.all([
          mark('trees', trees), mark('surveys', surveys), mark('tracks', tracks)
        ]).then(function () { return res; });
      });
    });
  }

  // 端末の中だけで使う印は送らない
  function clean(o) {
    var c = {};
    Object.keys(o).forEach(function (k) {
      if (k.charAt(0) === '_' || k === 'dirty' || k === 'photos') return;
      c[k] = o[k];
    });
    return c;
  }
  function cleanTrack(o) {
    var c = clean(o);
    // 点列はそのまま送る（サーバー側で JSON 文字列にして持つ）
    return c;
  }

  /* ---------------- ②写真を送る ---------------- */
  function pushPhotos(dev) {
    return S.byIndex('photos', 'state', 'pending').then(function (list) {
      if (!list.length) return 0;
      var done = 0;
      var next = function () {
        if (!list.length) { state.progress = null; return done; }
        var p = list.shift();
        state.phase = '写真を送っています';
        state.progress = { done: done, total: done + list.length + 1 };
        emit();
        var q = new URLSearchParams({
          uuid: p.uuid, tree: p.tree_uuid, name: p.filename || 'photo.jpg',
          taken: p.taken_at || '', device: dev.id, caption: p.caption || ''
        });
        if (p.lon != null) q.set('lon', p.lon);
        if (p.lat != null) q.set('lat', p.lat);
        if (p.heading != null) q.set('heading', p.heading);
        return fetch('/api/sync/photo?' + q.toString(), {
          method: 'PUT',
          headers: { 'Content-Type': p.blob.type || 'image/jpeg' },
          body: p.blob
        }).then(readJson).then(function (r) {
          p.state = 'sent';
          p.server = r.url || null;
          done++;
          return S.put('photos', p);
        })['catch'](function (e) {
          // 木がまだサーバーに届いていないだけなら、次の同期で通る
          p.lastError = String(e.message || e);
          return S.put('photos', p);
        }).then(next);
      };
      return Promise.resolve(next());
    });
  }

  /* ---------------- ③もらう ---------------- */
  function pullAll(dev) {
    return S.getKV('last_seq', 0).then(function (since) {
      var loops = 0;
      var step = function (from) {
        state.phase = '事務所の更新を受け取っています';
        emit();
        return fetch('/api/sync/pull?since=' + from + '&device='
                     + encodeURIComponent(dev.id), { cache: 'no-store' })
          .then(readJson).then(function (res) {
            return merge(res).then(function () {
              return S.setKV('last_seq', res.seq).then(function () {
                state.seq = res.seq;
                if (res.catalog) S.setKV('catalog', res.catalog);
                if (res.more && loops++ < 60) return step(res.seq);
                return res;
              });
            });
          });
      };
      return step(since);
    });
  }

  /** 受け取ったものを端末の中に入れる。
   *  まだ送っていない（dirty）記録は上書きしない ―― 現場の入力が勝つ。 */
  function merge(res) {
    var jobs = [];
    ['trees', 'surveys', 'tracks'].forEach(function (kind) {
      var incoming = res[kind] || [];
      if (!incoming.length) return;
      jobs.push(Promise.all(incoming.map(function (r) {
        return S.get(kind, r.uuid).then(function (local) {
          if (local && local.dirty) return null;       // 現場の入力を守る
          var rec = Object.assign({}, r, { dirty: 0 });
          if (kind === 'tracks' && typeof rec.points === 'string') {
            try { rec.points = JSON.parse(rec.points); } catch (e) { rec.points = []; }
          }
          return rec;
        });
      })).then(function (recs) {
        return S.putMany(kind, recs.filter(Boolean));
      }));
    });

    // 写真は「事務所にあるが端末に無い」ものを記録だけ入れる（画像は見るとき取りに行く）
    var ph = res.photos || [];
    if (ph.length) {
      jobs.push(Promise.all(ph.map(function (r) {
        return S.get('photos', r.uuid).then(function (local) {
          if (local && local.state === 'pending') return null;
          return Object.assign({}, local || {}, {
            uuid: r.uuid, tree_uuid: r.tree_uuid || (local && local.tree_uuid) || null,
            tree_id: r.tree_id, filename: r.filename, caption: r.caption,
            taken_at: r.taken_at, lon: r.lon, lat: r.lat,
            deleted: r.deleted, server: r.url, state: 'sent'
          });
        });
      })).then(function (recs) {
        return S.putMany('photos', recs.filter(Boolean));
      }));
    }

    // 木と写真のひもづけ（サーバーは tree_id で持っている）
    jobs.push(Promise.resolve());
    return Promise.all(jobs);
  }

  function readJson(r) {
    if (!r.ok) {
      return r.json()['catch'](function () { return {}; }).then(function (j) {
        throw new Error(j.error || ('通信に失敗しました（' + r.status + '）'));
      });
    }
    return r.json();
  }

  /* ---------------- まとめて実行 ---------------- */
  function syncNow(opts) {
    opts = opts || {};
    if (running) return Promise.resolve({ busy: true });
    running = true;
    state.busy = true;
    state.lastError = null;
    state.phase = 'つながるか確かめています';
    emit();

    var dev;
    return device().then(function (d) {
      dev = d;
      return ping(opts.timeout || 8000);
    }).then(function (ok) {
      state.reachable = ok;
      if (!ok) throw new Error('サーバーに届きません。電波かWi-Fiを確かめてください。');
      return pushAll(dev);
    }).then(function () {
      return pushPhotos(dev);
    }).then(function () {
      return pullAll(dev);
    }).then(function (res) {
      state.lastSync = new Date().toISOString();
      S.setKV('last_sync', state.lastSync);
      state.phase = '';
      return countPending().then(function () { return { ok: true, res: res }; });
    })['catch'](function (e) {
      state.lastError = String(e.message || e);
      state.phase = '';
      return countPending().then(function () {
        return { ok: false, error: state.lastError };
      });
    }).then(function (out) {
      running = false;
      state.busy = false;
      state.progress = null;
      emit();
      return out;
    });
  }

  /* ---------------- 自動で試す ---------------- */
  var timer = null;
  function auto(minutes) {
    if (timer) clearInterval(timer);
    var ms = (minutes || 10) * 60000;
    timer = setInterval(function () {
      if (!navigator.onLine || state.busy) return;
      countPending().then(function (n) { if (n > 0) syncNow({ quiet: true }); });
    }, ms);

    // 電波が戻った瞬間に、たまっている分を送りに行く
    root.addEventListener('online', function () {
      state.online = true; emit();
      setTimeout(function () {
        countPending().then(function (n) { if (n > 0) syncNow({ quiet: true }); });
      }, 1500);
    });
    root.addEventListener('offline', function () {
      state.online = false; state.reachable = false; emit();
    });
  }

  root.Sync = {
    state: state, on: on, emit: emit, device: device, ping: ping,
    syncNow: syncNow, countPending: countPending, auto: auto
  };
})(window);
