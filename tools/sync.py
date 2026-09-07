# -*- coding: utf-8 -*-
"""端末（スマホ）と事務所（このPC）のあいだでデータを合わせる。

■ 何を解決しているか

  森の中は電波が無い。だから現地では端末の中だけに書き、
  電波の届くところに出たとき／事務所のWi-Fiに入ったときに、まとめて送る。
  複数人が別々の端末で、同じ日に、同じ林班を歩くことがある。

■ どう作ってあるか

  1) すべての記録は uuid（端末が自分で作る一意ID）を持つ。
     採番をサーバーに頼らないので、圏外でも記録を作れる。
     同じ記録を2回送っても増えない（uuid で上書きになる）。

  2) サーバー側の変更には seq（通し番号）が付く。
     端末は「前回 seq いくつまで受け取った」を覚えていて、その続きだけをもらう。
     全件を毎回もらわないので、電波が細くても終わる。

  3) 同じ記録を2人が別々に直したときは updated_at の新しいほうを採る
     （Last-Write-Wins）。ただし **採らなかったほうも conflicts に残す**。
     現場で入れた値を黙って消さないため。

     「どちらが正しいか」を機械が決められる場面ではないので、
     決めるのは人に任せ、機械は「消さない」ことだけを保証する。

■ やっていないこと

  ベクタークロックやCRDTのような厳密な収束は入れていない。
  1本の木を、同じ日に、2人が、別々に直す ―― という状況が
  そもそもほとんど起きない業務だからで、
  起きたときに人が気づける（conflicts に出る）ようにしてある。
"""
from __future__ import annotations
import json, time

import db as dbmod

# 端末から送られてきても採用しない列（サーバーが決めるもの）
SERVER_OWNED = {
    'id', 'seq', 'sseq', 'code', 'x', 'y', 'elev', 'rinpan', 'kosyoban', 'chiku',
    'sp_main', 'nara_rank', 'chiban', 'score', 'area_m2', 'canopy_h',
    'orig_lon', 'orig_lat', 'orig_note', 'created_at',
}

# 端末が書き換えてよい trees の列
TREE_WRITABLE = set(dbmod.SURVEY_FIELDS) | {
    'lon', 'lat', 'memo', 'priority', 'loc_accuracy', 'site',
    'gps_acc', 'heading', 'deleted', 'device',
}

SURVEY_WRITABLE = set(dbmod.VISIT_FIELDS) | {'seq_no', 'deleted', 'device'}

TRACK_WRITABLE = {
    'name', 'device', 'surveyor', 'started_at', 'ended_at', 'dist_m', 'dur_s',
    'up_m', 'pt_n', 'minlon', 'minlat', 'maxlon', 'maxlat', 'color', 'note',
    'points', 'deleted',
}

PHOTO_WRITABLE = {'caption', 'deleted', 'taken_at', 'lon', 'lat', 'heading', 'device'}

PULL_KINDS = ('trees', 'surveys', 'photos', 'tracks')


# ------------------------------------------------------------------ 受け取る
def pull(con, since=0, limit=800, want_points=True):
    """seq が since より大きい行を集めて返す。

    limit はテーブルごと。全部返しきれなかったら more=True を立て、
    端末は「次の seq から」もう一度呼ぶ。
    """
    out = {'since': since, 'seq': dbmod.current_seq(con), 'more': False}
    truncated = []          # 上限に当たった表と、その表を渡しきった位置
    for kind in PULL_KINDS:
        cols = '*'
        if kind == 'tracks' and not want_points:
            names = [r[1] for r in con.execute('PRAGMA table_info(tracks)')]
            cols = ','.join(c for c in names if c != 'points')
        rs = con.execute(
            'SELECT %s FROM %s WHERE sseq > ? ORDER BY sseq LIMIT ?' % (cols, kind),
            (since, limit + 1)).fetchall()
        if len(rs) > limit:
            rs = rs[:limit]
            out['more'] = True
            truncated.append(rs[-1]['sseq'] if rs else since)
        recs = [dict(r) for r in rs]
        if kind == 'photos':
            for r in recs:
                r['url'] = '/data/photos/' + (r.get('filename') or '')
        if kind == 'trees':
            # 木がどの調査回に紐づくかは surveys 側に持たせる
            pass
        out[kind] = recs
    # まだ続きがあるときは「どの表も確実に渡しきった位置」までしか進めない。
    # 表ごとに上限をかけているので、いちばん手前で切れた表に合わせないと、
    # そのあいだの写真やトラックが飛ばされてしまう。
    if out['more']:
        out['seq'] = min(int(t or since) for t in truncated)
    return out


def catalog(con):
    """端末が最初に一度だけ受け取る、変わらないもの一式。"""
    return {
        'status': [{'code': c, 'label': l, 'color': k} for c, l, k in dbmod.STATUS],
        'work': [{'code': c, 'label': l, 'color': k} for c, l, k in dbmod.WORK],
        'owner': [{'code': c, 'label': l, 'color': k} for c, l, k in dbmod.OWNER],
        'priority': dbmod.PRIORITY,
        'land_class': dbmod.LAND_CLASS,
    }


# -------------------------------------------------------------------- 送る
def push(con, body, locate=None, who=''):
    """端末から届いた変更をデータベースに入れる。

    locate は lon,lat から林班・小班・標高などを引く関数（server.py が渡す）。
    圏外で作られた記録は、この瞬間に初めて林班が付く。
    """
    dev = body.get('device') or {}
    device_id = (dev.get('id') or '').strip()[:64]
    who = (dev.get('who') or who or '').strip()[:60]
    if device_id:
        _touch_device(con, device_id, dev)

    res = {'ok': True, 'applied': {}, 'ids': {}, 'codes': {},
           'conflicts': [], 'errors': []}

    n = _push_trees(con, body.get('trees') or [], device_id, who, locate, res)
    res['applied']['trees'] = n
    res['applied']['surveys'] = _push_surveys(con, body.get('surveys') or [],
                                              device_id, who, res)
    res['applied']['tracks'] = _push_tracks(con, body.get('tracks') or [],
                                            device_id, who, res)
    res['applied']['photos'] = _push_photo_meta(con, body.get('photos') or [],
                                                device_id, who, res)
    con.commit()
    res['seq'] = dbmod.current_seq(con)
    if device_id:
        total = sum(res['applied'].values())
        con.execute('UPDATE devices SET pushed=COALESCE(pushed,0)+? WHERE id=?',
                    (total, device_id))
        con.commit()
    return res


def _touch_device(con, device_id, dev):
    now = dbmod.now()
    r = con.execute('SELECT id FROM devices WHERE id=?', (device_id,)).fetchone()
    if r:
        con.execute('UPDATE devices SET name=?, who=?, ua=?, last_seen=? WHERE id=?',
                    ((dev.get('name') or '')[:60], (dev.get('who') or '')[:60],
                     (dev.get('ua') or '')[:200], now, device_id))
    else:
        con.execute('INSERT INTO devices(id,name,who,ua,first_seen,last_seen) '
                    'VALUES (?,?,?,?,?,?)',
                    (device_id, (dev.get('name') or '')[:60],
                     (dev.get('who') or '')[:60], (dev.get('ua') or '')[:200], now, now))


def _newer(a, b):
    """a のほうが新しいか。空文字は「とても古い」として扱う。"""
    return (a or '') >= (b or '')


def _push_trees(con, items, device, who, locate, res):
    applied = 0
    for it in items:
        uu = (it.get('uuid') or '').strip()
        if not uu:
            res['errors'].append('uuid の無い木が届きました')
            continue
        ts = it.get('updated_at') or dbmod.now()
        cur = con.execute('SELECT * FROM trees WHERE uuid=?', (uu,)).fetchone()

        # ---- 新規（現地で追加した木） ----
        if cur is None:
            try:
                lon = float(it['lon'])
                lat = float(it['lat'])
            except (KeyError, TypeError, ValueError):
                res['errors'].append('緯度経度の無い木が届きました: %s' % uu[:8])
                continue
            loc = locate(lon, lat) if locate else {'lon': lon, 'lat': lat}
            site = it.get('site') or 'field'
            if not con.execute('SELECT 1 FROM sites WHERE id=?', (site,)).fetchone():
                con.execute('INSERT INTO sites(id,name,created_at,updated_at) '
                            'VALUES (?,?,?,?)',
                            (site, '現地で登録', dbmod.now(), dbmod.now()))
            f = {'uuid': uu, 'code': dbmod.next_code(con, site), 'site': site,
                 'source': 'field', 'device': device,
                 'created_at': it.get('created_at') or dbmod.now(),
                 'updated_at': ts, 'sseq': dbmod.next_seq(con)}
            for k in ('lon', 'lat', 'x', 'y', 'elev', 'rinpan', 'kosyoban',
                      'chiku', 'sp_main', 'nara_rank', 'chiban'):
                if k in loc:
                    f[k] = loc[k]
            for k, v in it.items():
                if k in TREE_WRITABLE and k not in ('lon', 'lat'):
                    f[k] = v
            f.setdefault('status', 'unsurveyed')
            f.setdefault('priority', '中')
            f.setdefault('loc_accuracy', '現地GPS')
            f = {k: v for k, v in f.items() if k in _cols(con, 'trees')}
            con.execute('INSERT INTO trees(%s) VALUES (%s)'
                        % (','.join(f), ','.join('?' * len(f))), list(f.values()))
            tid = con.execute('SELECT id FROM trees WHERE uuid=?', (uu,)).fetchone()['id']
            dbmod.log_change(con, tid, who, 'created', '', f['code'] + '（現地端末）')
            res['ids'][uu] = tid
            res['codes'][uu] = f['code']
            applied += 1
            continue

        # ---- 既存（現地で直した） ----
        res['ids'][uu] = cur['id']
        res['codes'][uu] = cur['code']
        if not _newer(ts, cur['updated_at']):
            # サーバー側のほうが新しい。捨てずに conflicts へ。
            for k, v in it.items():
                if k in TREE_WRITABLE and str(v or '') != str(
                        (cur[k] if k in cur.keys() else '') or ''):
                    dbmod.log_conflict(con, 'tree', uu, device, who, k,
                                       cur[k] if k in cur.keys() else '', v)
                    res['conflicts'].append({'uuid': uu, 'field': k})
            continue

        moved = False
        sets, args = [], []
        for k, v in it.items():
            if k not in TREE_WRITABLE or k in SERVER_OWNED:
                continue
            old = cur[k] if k in cur.keys() else None
            if k in ('lon', 'lat'):
                try:
                    if old is not None and abs(float(v) - float(old)) < 1e-9:
                        continue
                except (TypeError, ValueError):
                    continue
                moved = True
                continue
            if str(old or '') == str(v or ''):
                continue
            sets.append('%s=?' % k)
            args.append(v)
            dbmod.log_change(con, cur['id'], who, k, old, v)

        if moved and locate:
            try:
                loc = locate(float(it['lon']), float(it['lat']))
            except (TypeError, ValueError, KeyError):
                loc = None
            if loc:
                import geo
                d = (geo.haversine_m(cur['lon'], cur['lat'], loc['lon'], loc['lat'])
                     if cur['lon'] is not None else 0.0)
                der = {k: loc[k] for k in ('lon', 'lat', 'x', 'y', 'rinpan', 'kosyoban',
                                           'chiku', 'sp_main', 'nara_rank', 'chiban',
                                           'elev') if k in loc}
                if cur['orig_lon'] is None and cur['lon'] is not None:
                    der['orig_lon'] = cur['lon']
                    der['orig_lat'] = cur['lat']
                    der['orig_note'] = cur['loc_accuracy'] or '登録時の位置'
                for k, v in der.items():
                    sets.append('%s=?' % k)
                    args.append(v)
                dbmod.log_change(con, cur['id'], who, '位置',
                                 '%s, %s' % (cur['lat'], cur['lon']),
                                 '%s, %s（現地で %.1f m 補正）'
                                 % (loc['lat'], loc['lon'], d))

        if sets:
            sets += ['updated_at=?', 'sseq=?', 'device=?']
            args += [ts, dbmod.next_seq(con), device]
            con.execute('UPDATE trees SET %s WHERE id=?' % ','.join(sets),
                        args + [cur['id']])
            applied += 1
    return applied


def _push_surveys(con, items, device, who, res):
    applied = 0
    for it in items:
        uu = (it.get('uuid') or '').strip()
        if not uu:
            continue
        tid = None
        tu = it.get('tree_uuid')
        if tu:
            r = con.execute('SELECT id FROM trees WHERE uuid=?', (tu,)).fetchone()
            tid = r['id'] if r else None
        if tid is None and it.get('tree_id'):
            tid = int(it['tree_id'])
        if tid is None:
            res['errors'].append('木の分からない調査記録が届きました')
            continue
        ts = it.get('updated_at') or dbmod.now()
        cur = con.execute('SELECT * FROM surveys WHERE uuid=?', (uu,)).fetchone()
        vals = {k: v for k, v in it.items()
                if k in SURVEY_WRITABLE and k not in ('seq_no',)}
        if cur is None:
            seq_no = con.execute('SELECT COALESCE(MAX(seq),0)+1 s FROM surveys '
                                 'WHERE tree_id=?', (tid,)).fetchone()['s']
            f = dict(vals)
            f.update({'uuid': uu, 'tree_id': tid, 'seq': seq_no,
                      'created_at': it.get('created_at') or dbmod.now(),
                      'updated_at': ts})
            f = {k: v for k, v in f.items() if k in _cols(con, 'surveys')}
            con.execute('INSERT INTO surveys(%s) VALUES (%s)'
                        % (','.join(f), ','.join('?' * len(f))), list(f.values()))
            sid = con.execute('SELECT id FROM surveys WHERE uuid=?', (uu,)).fetchone()['id']
            con.execute('UPDATE surveys SET sseq=? WHERE id=?', (dbmod.next_seq(con), sid))
            applied += 1
        elif _newer(ts, cur['updated_at']):
            sets = ['%s=?' % k for k in vals]
            con.execute('UPDATE surveys SET %s, updated_at=?, sseq=? WHERE id=?'
                        % ','.join(sets),
                        list(vals.values()) + [ts, dbmod.next_seq(con), cur['id']])
            applied += 1
        else:
            for k, v in vals.items():
                if str(v or '') != str((cur[k] if k in cur.keys() else '') or ''):
                    dbmod.log_conflict(con, 'survey', uu, device, who, k,
                                       cur[k] if k in cur.keys() else '', v)
    return applied


def _push_tracks(con, items, device, who, res):
    applied = 0
    for it in items:
        uu = (it.get('uuid') or '').strip()
        if not uu:
            continue
        ts = it.get('updated_at') or dbmod.now()
        pts = it.get('points')
        if isinstance(pts, (list, tuple)):
            it = dict(it, points=json.dumps(pts, separators=(',', ':')))
        cur = con.execute('SELECT id,updated_at FROM tracks WHERE uuid=?', (uu,)).fetchone()
        vals = {k: v for k, v in it.items() if k in TRACK_WRITABLE}
        vals['device'] = vals.get('device') or device
        if cur is None:
            f = dict(vals)
            f.update({'uuid': uu, 'created_at': it.get('created_at') or dbmod.now(),
                      'updated_at': ts, 'sseq': dbmod.next_seq(con)})
            f = {k: v for k, v in f.items() if k in _cols(con, 'tracks')}
            con.execute('INSERT INTO tracks(%s) VALUES (%s)'
                        % (','.join(f), ','.join('?' * len(f))), list(f.values()))
            applied += 1
        elif _newer(ts, cur['updated_at']):
            sets = ['%s=?' % k for k in vals]
            con.execute('UPDATE tracks SET %s, updated_at=?, sseq=? WHERE id=?'
                        % ','.join(sets),
                        list(vals.values()) + [ts, dbmod.next_seq(con), cur['id']])
            applied += 1
    return applied


def _push_photo_meta(con, items, device, who, res):
    """写真の本体は別の口（/api/sync/photo）で送る。ここは説明文と削除だけ。"""
    applied = 0
    for it in items:
        uu = (it.get('uuid') or '').strip()
        if not uu:
            continue
        cur = con.execute('SELECT * FROM photos WHERE uuid=?', (uu,)).fetchone()
        if cur is None:
            continue
        ts = it.get('updated_at') or dbmod.now()
        vals = {k: v for k, v in it.items() if k in PHOTO_WRITABLE}
        if not vals:
            continue
        sets = ['%s=?' % k for k in vals]
        con.execute('UPDATE photos SET %s, sseq=? WHERE id=?' % ','.join(sets),
                    list(vals.values()) + [dbmod.next_seq(con), cur['id']])
        applied += 1
    return applied


_COLS = {}


def _cols(con, table):
    if table not in _COLS:
        _COLS[table] = {r[1] for r in con.execute('PRAGMA table_info(%s)' % table)}
    return _COLS[table]
