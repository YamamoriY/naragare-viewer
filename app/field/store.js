/* 端末の中のデータ置き場（IndexedDB）
 *
 * 現地では、書いたものは全部いったんここに入る。サーバーには行かない。
 * 電波が来たときに sync.js がここから取り出してまとめて送る。
 *
 * 設計の要点
 *   - 主キーは uuid。端末が自分で作るので、圏外でも記録を作れる。
 *   - dirty=1 が「まだ送っていない」印。送れたら 0 に戻す。
 *   - 写真は Blob のまま置く。数十MBになるので、ここが唯一の置き場。
 *
 * 保存領域について
 *   ブラウザは空き容量が減ると勝手に消すことがある。
 *   navigator.storage.persist() で「消さないでほしい」と申請している。
 *   iOS は 7日間まったく開かないと消えることがあるので、
 *   「未送信があるまま長く放置しない」ことを画面で促している。
 */
(function (root) {
  'use strict';

  var NAME = 'naragare-field';
  var VER = 1;
  var db = null;

  var STORES = {
    trees:   { key: 'uuid', idx: [['dirty', 'dirty'], ['code', 'code']] },
    surveys: { key: 'uuid', idx: [['dirty', 'dirty'], ['tree', 'tree_uuid']] },
    photos:  { key: 'uuid', idx: [['state', 'state'], ['tree', 'tree_uuid']] },
    tracks:  { key: 'uuid', idx: [['dirty', 'dirty']] },
    kv:      { key: 'k', idx: [] }
  };

  function open() {
    if (db) return Promise.resolve(db);
    return new Promise(function (res, rej) {
      var req = indexedDB.open(NAME, VER);
      req.onupgradeneeded = function (e) {
        var d = e.target.result;
        Object.keys(STORES).forEach(function (name) {
          var spec = STORES[name];
          var os = d.objectStoreNames.contains(name)
            ? e.target.transaction.objectStore(name)
            : d.createObjectStore(name, { keyPath: spec.key });
          spec.idx.forEach(function (ix) {
            if (!os.indexNames.contains(ix[0])) os.createIndex(ix[0], ix[1]);
          });
        });
      };
      req.onsuccess = function () { db = req.result; res(db); };
      req.onerror = function () { rej(req.error); };
    });
  }

  function tx(names, mode) {
    return open().then(function (d) { return d.transaction(names, mode); });
  }

  function wrap(req) {
    return new Promise(function (res, rej) {
      req.onsuccess = function () { res(req.result); };
      req.onerror = function () { rej(req.error); };
    });
  }

  function put(store, obj) {
    return tx([store], 'readwrite').then(function (t) {
      return wrap(t.objectStore(store).put(obj));
    });
  }

  function putMany(store, list) {
    if (!list.length) return Promise.resolve(0);
    return tx([store], 'readwrite').then(function (t) {
      var os = t.objectStore(store);
      list.forEach(function (o) { os.put(o); });
      return new Promise(function (res, rej) {
        t.oncomplete = function () { res(list.length); };
        t.onerror = function () { rej(t.error); };
      });
    });
  }

  function get(store, key) {
    return tx([store], 'readonly').then(function (t) {
      return wrap(t.objectStore(store).get(key));
    });
  }

  function all(store) {
    return tx([store], 'readonly').then(function (t) {
      return wrap(t.objectStore(store).getAll());
    });
  }

  function byIndex(store, index, value) {
    return tx([store], 'readonly').then(function (t) {
      return wrap(t.objectStore(store).index(index).getAll(value));
    });
  }

  function del(store, key) {
    return tx([store], 'readwrite').then(function (t) {
      return wrap(t.objectStore(store)['delete'](key));
    });
  }

  function count(store) {
    return tx([store], 'readonly').then(function (t) {
      return wrap(t.objectStore(store).count());
    });
  }

  /* ---------------- 設定と覚え書き ---------------- */
  function getKV(k, dflt) {
    return get('kv', k).then(function (r) {
      return r === undefined ? dflt : r.v;
    });
  }
  function setKV(k, v) { return put('kv', { k: k, v: v }); }

  /* ---------------- uuid ---------------- */
  function uuid() {
    if (root.crypto && root.crypto.randomUUID) return root.crypto.randomUUID();
    var b = new Uint8Array(16);
    (root.crypto || { getRandomValues: function (a) {
      for (var i = 0; i < a.length; i++) a[i] = Math.floor(Math.random() * 256);
    } }).getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    var h = [];
    for (var i = 0; i < 16; i++) h.push((b[i] + 0x100).toString(16).slice(1));
    return h.slice(0, 4).join('') + '-' + h.slice(4, 6).join('') + '-'
      + h.slice(6, 8).join('') + '-' + h.slice(8, 10).join('') + '-'
      + h.slice(10).join('');
  }

  /** 'YYYY-MM-DD HH:MM:SS'。サーバー側の updated_at と同じ書き方にそろえる。 */
  function stamp(d) {
    d = d || new Date();
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
      + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }
  function today() { return stamp().slice(0, 10); }

  /* ---------------- 使用量 ---------------- */
  function usage() {
    if (!navigator.storage || !navigator.storage.estimate) {
      return Promise.resolve(null);
    }
    return navigator.storage.estimate().then(function (e) {
      return { used: e.usage || 0, quota: e.quota || 0 };
    })['catch'](function () { return null; });
  }

  function persist() {
    if (!navigator.storage || !navigator.storage.persist) {
      return Promise.resolve(false);
    }
    return navigator.storage.persisted().then(function (p) {
      return p ? true : navigator.storage.persist();
    })['catch'](function () { return false; });
  }

  /** データを全部消す（引き継ぎ・端末を返すとき） */
  function wipe() {
    return open().then(function (d) {
      d.close(); db = null;
      return new Promise(function (res, rej) {
        var r = indexedDB.deleteDatabase(NAME);
        r.onsuccess = function () { res(true); };
        r.onerror = function () { rej(r.error); };
        r.onblocked = function () { res(false); };
      });
    });
  }

  root.Store = {
    open: open, put: put, putMany: putMany, get: get, all: all,
    byIndex: byIndex, del: del, count: count,
    getKV: getKV, setKV: setKV,
    uuid: uuid, stamp: stamp, today: today,
    usage: usage, persist: persist, wipe: wipe
  };
})(window);
