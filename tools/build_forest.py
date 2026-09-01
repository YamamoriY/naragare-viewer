# -*- coding: utf-8 -*-
"""森町の林班・小班・森林調査簿を参照データベース（data/forest.db）に取り込む。

入力（すべて実データ）
  北海道オープンデータ「森林計画関係資料（一般民有林）令和6年」渡島
    https://www.fics.pref.hokkaido.lg.jp/FILE/2024/GIS/01oshima.zip
    - 01_渡島林班.shp   林班界ポリゴン
    - 01_渡島小班.shp   小班界ポリゴン
    - 01_渡島_調査簿データ.xlsx  森林調査簿（樹種・林齢・蓄積・樹高・代表地番）
    座標系: 平面直角座標系 XI(11)系 JGD2000（オルソの EPSG:6679 と同一グリッド）
    利用条件: 北海道オープンデータ利用規約（CC BY 相当）
  国土地理院 標高タイル DEM10B（10mメッシュ・地表面標高）

出力
  data/forest.db                     参照データ（再生成可能）
  data/layers/rinpan_mori.geojson    森町全域の林班界
  data/layers/nara_mori.geojson      ナラ類を含む小班（カシノナガキクイムシの寄主）

使い方
  python tools/build_forest.py                 # zipを自動取得して構築
  python tools/build_forest.py --zip <path>    # 手元のzipを使う
  python tools/build_forest.py --no-dem        # 標高取得を省く（オフライン時）
"""
from __future__ import annotations
import os, sys, json, sqlite3, argparse, urllib.request, tempfile, shutil, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geo
from shapelib import (DBF, SHP, unzip_cp932, xlsx_rows, bbox_of, simplify,
                      point_in_polygon, ring_area)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
ZIP_URL = 'https://www.fics.pref.hokkaido.lg.jp/FILE/2024/GIS/01oshima.zip'

SHINKOKYOKU = '01'      # 渡島
MORI = '15'             # 森町（旧森町=林班1〜／旧砂原町=林班1000〜）

# 森林調査簿 樹種コード（カシノナガキクイムシの寄主となるナラ類）
NARA_CODES = {'62': 'コナラ', '63': 'カシワ', '64': 'ミズナラ'}
# ナラ類が混じりうる区分
NARA_MAYBE = {'98': '天然林広葉樹', '99': '針広混交林'}

SPECIES = {
    '01': 'イチイ', '02': 'スギ', '03': 'アカマツ', '04': 'クロマツ',
    '05': 'ヨーロッパアカマツ', '06': 'ヨーロッパクロマツ', '07': 'キタゴヨウ・ゴヨウマツ',
    '08': 'チョウセンゴヨウ', '09': 'リキダマツ', '10': 'ストローブマツ',
    '11': 'バンクスマツ', '12': 'レジノーザマツ', '16': 'ヒノキアスナロ', '17': 'カラマツ',
    '18': 'グイマツ', '19': 'チョウセンカラマツ', '21': 'クリーンラーチ',
    '22': 'グイマツ雑種F1', '23': 'トドマツ', '24': 'アカトドマツ', '25': 'エゾマツ',
    '26': 'アカエゾマツ', '27': 'ヨーロッパトウヒ', '28': 'イチョウ',
    '37': 'その他人工林針葉樹', '39': '天然林針葉樹', '41': 'ヤナギ',
    '42': 'セイヨウハコヤナギ', '43': 'エウロアメリカポプラ', '44': 'ドロノキ',
    '45': 'ギンドロ', '46': 'ヤマナラシ', '47': 'オニグルミ・サワグルミ',
    '48': 'ウダイカンバ', '49': 'シラカンバ', '50': 'ダケカンバ', '51': 'その他カンバ',
    '52': 'ハンノキ・ヤチハンノキ', '53': 'ヤマハンノキ', '54': 'コバノヤマハンノキ',
    '55': 'グルチノーザハンノキ', '56': 'ケヤマハンノキ', '57': 'アカシデ・クマシデ',
    '58': 'サワシバ', '59': 'アサダ', '60': 'ブナ', '61': 'クリ', '62': 'コナラ',
    '63': 'カシワ', '64': 'ミズナラ', '65': 'ハルニレ', '66': 'オヒョウ', '67': 'ケヤキ',
    '68': 'ヤマグワ', '69': 'カツラ', '70': 'キタコブシ', '71': 'ホオノキ',
    '97': 'その他人工林広葉樹', '98': '天然林広葉樹', '99': '針広混交林',
}
RINSHU = {'1': '人工林', '2': '天然林', '3': '天然生ぼう芽林',
          '4': '天然林伐採跡地', '5': '人工林伐採跡地', '6': '未立木地'}


def log(*a):
    print(*a)
    sys.stdout.flush()


def find(files, needle):
    for p in files:
        if needle in os.path.basename(p):
            return p
    return None


def download(url, dest):
    log('  ダウンロード: %s' % url)
    req = urllib.request.Request(url, headers={'User-Agent': 'naragare-viewer/1.0'})
    with urllib.request.urlopen(req, timeout=1800) as r, open(dest, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                sys.stdout.write('\r    %.1f / %.1f MB' % (got / 1e6, total / 1e6))
                sys.stdout.flush()
    if total:
        sys.stdout.write('\n')
    return dest


def parse_key(k):
    """KEYCODE: 振興局(2)+市町村(2)+図郭(2)+林班(4)+小班(4)"""
    k = (k or '').strip()
    return {
        'shinko': k[0:2], 'shicho': k[2:4], 'zukaku': k[4:6],
        'rinpan': k[6:10], 'kosyoban': k[10:14],
    }


def rings_to_geojson(rings, tol_m):
    """shapefile のリング群 -> GeoJSON Polygon/MultiPolygon（WGS84）

    shapefile では外環が時計回り(符号付き面積<0)、穴が反時計回り。
    外環ごとにポリゴンを立て、以後の穴をその中に入れる。
    """
    polys = []
    for r in rings:
        if len(r) < 4:
            continue
        rs = simplify(r, tol_m) if tol_m else r
        if len(rs) < 4:
            rs = r
        if rs[0] != rs[-1]:
            rs = rs + [rs[0]]
        ll = [list(geo.xy_to_lonlat(x, y)) for x, y in rs]
        ll = [[round(a, 7), round(b, 7)] for a, b in ll]
        if ring_area(r) < 0:
            polys.append([ll])
        elif polys:
            polys[-1].append(ll)
        else:
            polys.append([ll])
    if not polys:
        return None
    if len(polys) == 1:
        return {'type': 'Polygon', 'coordinates': polys[0]}
    return {'type': 'MultiPolygon', 'coordinates': [[p] if False else p for p in polys]}


def centroid_xy(rings):
    """最大外環の重心（平面直角座標）"""
    best, ba = None, 0.0
    for r in rings:
        if len(r) < 4:
            continue
        a = abs(ring_area(r))
        if a > ba:
            ba, best = a, r
    if best is None:
        return None
    a = 0.0
    cx = cy = 0.0
    n = len(best)
    for i in range(n):
        x1, y1 = best[i]
        x2, y2 = best[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        a += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    if abs(a) < 1e-9:
        xs = [p[0] for p in best]
        ys = [p[1] for p in best]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    a *= 0.5
    return cx / (6 * a), cy / (6 * a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--zip', help='01oshima.zip のパス（省略時はダウンロード）')
    ap.add_argument('--work', help='展開先（省略時は一時ディレクトリ）')
    ap.add_argument('--no-dem', action='store_true', help='標高の取得を省略する')
    ap.add_argument('--simplify', type=float, default=1.0,
                    help='GeoJSON の座標間引き許容誤差[m]（既定1.0）')
    args = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    os.makedirs(os.path.join(DATA, 'layers'), exist_ok=True)

    tmp = args.work or os.path.join(DATA, '_src')
    os.makedirs(tmp, exist_ok=True)

    zpath = args.zip
    if not zpath:
        zpath = os.path.join(tmp, '01oshima.zip')
        if not os.path.exists(zpath) or os.path.getsize(zpath) < 1_000_000:
            log('■ 北海道オープンデータ（渡島・令和6年）を取得')
            download(ZIP_URL, zpath)
        else:
            log('■ 既存のzipを使用: %s' % zpath)

    log('■ 展開')
    ex = os.path.join(tmp, 'oshima')
    files = unzip_cp932(zpath, ex)
    shp_rin = find(files, '林班.shp')
    shp_ko = find(files, '小班.shp')
    xls_bo = find(files, '調査簿データ')
    if not (shp_rin and shp_ko):
        raise SystemExit('林班/小班 shapefile が見つかりません: %s' % ex)

    # ---------------- 森林調査簿 ----------------
    boc = {}
    if xls_bo:
        log('■ 森林調査簿を読み込み（%s）' % os.path.basename(xls_bo))
        header = None
        n = 0
        for row in xlsx_rows(xls_bo, sheet=1):
            if header is None:
                header = [c.strip() for c in row]
                idx = {}
                for want, key in (('KEYCODE', 'key'), ('林班', 'rinpan'), ('小班', 'kosyoban'),
                                  ('面積', 'area'), ('林種コード', 'rinshu'),
                                  ('樹種1コード', 'sp1'), ('樹種1比率', 'sp1r'),
                                  ('樹種2コード', 'sp2'), ('樹種2比率', 'sp2r'),
                                  ('樹種3コード', 'sp3'), ('樹種3比率', 'sp3r'),
                                  ('林齢', 'age'), ('樹高', 'height'),
                                  ('HA当蓄積N', 'volN'), ('HA当蓄積L', 'volL'),
                                  ('代表地番', 'chiban'), ('傾斜', 'slope'),
                                  ('複層区分コード', 'layer')):
                    if want in header:
                        idx[key] = header.index(want)
                continue
            n += 1
            g = lambda k: (row[idx[k]].strip() if k in idx and idx[k] < len(row) else '')
            key = g('key')
            if not key or key[2:4] != MORI or key[0:2] != SHINKOKYOKU:
                continue
            rec = {k: g(k) for k in idx if k != 'key'}
            # 複層林は同じKEYCODEで複数行。上層(空欄or1)を優先して残す。
            cur = boc.get(key)
            if cur is None or (cur.get('layer') not in ('', '1') and rec.get('layer') in ('', '1')):
                boc[key] = rec
        log('   調査簿 %d 行を走査 / 森町 %d 小班' % (n, len(boc)))

    def species_of(rec):
        out = []
        for a, b in (('sp1', 'sp1r'), ('sp2', 'sp2r'), ('sp3', 'sp3r')):
            c = (rec.get(a) or '').strip()
            if not c:
                continue
            c = c.zfill(2)
            if c in ('', '00'):
                continue
            ratio = (rec.get(b) or '').strip()
            out.append((c, SPECIES.get(c, 'コード%s' % c), ratio))
        return out

    # ---------------- DEM ----------------
    dem = None
    if not args.no_dem:
        try:
            from dem import DEM
            dem = DEM(os.path.join(DATA, 'dem_cache'))
            log('■ 国土地理院 標高タイルを使用（初回はダウンロードあり）')
        except Exception as e:
            log('   標高は使用しません（%s）' % e)

    # ---------------- DB ----------------
    dbp = os.path.join(DATA, 'forest.db')
    if os.path.exists(dbp):
        os.remove(dbp)
    con = sqlite3.connect(dbp)
    con.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE rinpan(
      rinpan TEXT PRIMARY KEY, chiku TEXT, area_ha REAL,
      lon REAL, lat REAL, x REAL, y REAL,
      minlon REAL, minlat REAL, maxlon REAL, maxlat REAL,
      geom TEXT);
    CREATE TABLE kosyoban(
      id TEXT PRIMARY KEY, rinpan TEXT, kosyoban TEXT, chiku TEXT,
      area_ha REAL, rinshu TEXT, species TEXT, sp_main TEXT,
      age TEXT, height TEXT, vol TEXT, chiban TEXT, slope TEXT,
      nara_rank INTEGER, elev REAL,
      lon REAL, lat REAL, x REAL, y REAL,
      minx REAL, miny REAL, maxx REAL, maxy REAL,
      minlon REAL, minlat REAL, maxlon REAL, maxlat REAL,
      geom TEXT);
    CREATE INDEX ix_ko_rinpan ON kosyoban(rinpan);
    CREATE INDEX ix_ko_bbox ON kosyoban(minx,maxx,miny,maxy);
    CREATE INDEX ix_ko_nara ON kosyoban(nara_rank);
    """)

    # ---------------- 林班 ----------------
    log('■ 林班界を読み込み')
    s = SHP(shp_rin)
    d = DBF(shp_rin[:-4] + '.dbf')
    keyf = d.names[0]
    # 1つの林班が複数レコード（飛び地）に分かれていることがあるのでまとめる
    grouped = {}
    for i, rec in d.records():
        k = parse_key(str(rec.get(keyf, '')))
        if k['shinko'] != SHINKOKYOKU or k['shicho'] != MORI:
            continue
        r = s.rings(i)
        if r:
            grouped.setdefault(k['rinpan'], []).extend(r)
    rin_rows = []
    for rn, rings in sorted(grouped.items()):
        chiku = '旧砂原町' if int(rn or 0) >= 1000 else '旧森町'
        bb = bbox_of(rings)
        c = centroid_xy(rings)
        lon, lat = geo.xy_to_lonlat(*c)
        lo0, la0 = geo.xy_to_lonlat(bb[0], bb[1])
        lo1, la1 = geo.xy_to_lonlat(bb[2], bb[3])
        gj = rings_to_geojson(rings, args.simplify)
        area = sum(abs(ring_area(r)) for r in rings if ring_area(r) < 0) / 10000.0
        rin_rows.append((rn, chiku, round(area, 2), round(lon, 7), round(lat, 7),
                         round(c[0], 2), round(c[1], 2),
                         round(lo0, 7), round(la0, 7), round(lo1, 7), round(la1, 7),
                         json.dumps(gj, ensure_ascii=False)))
    con.executemany('INSERT OR REPLACE INTO rinpan VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', rin_rows)
    con.commit()
    log('   森町の林班 %d 件' % len(rin_rows))
    d.close()

    # ---------------- 小班 ----------------
    log('■ 小班界を読み込み（渡島全体 %s 件から森町を抽出）' % '{:,}'.format(SHP(shp_ko).count))
    s = SHP(shp_ko)
    d = DBF(shp_ko[:-4] + '.dbf')
    keyf = d.names[0]
    rows = []
    t0 = time.time()
    # 同一小班が複数レコードに分かれている場合（飛び地）はまとめる
    kgroup = {}
    for i, rec in d.records():
        raw = str(rec.get(keyf, '')).strip()
        k = parse_key(raw)
        if k['shinko'] != SHINKOKYOKU or k['shicho'] != MORI:
            continue
        r = s.rings(i)
        if r:
            kgroup.setdefault(raw, []).extend(r)
    nparts = sum(1 for v in kgroup.values() if len(v) > 1)
    for raw, rings in sorted(kgroup.items()):
        k = parse_key(raw)
        rn, kn = k['rinpan'], k['kosyoban']
        chiku = '旧砂原町' if int(rn or 0) >= 1000 else '旧森町'
        bb = bbox_of(rings)
        c = centroid_xy(rings)
        lon, lat = geo.xy_to_lonlat(*c)
        lo0, la0 = geo.xy_to_lonlat(bb[0], bb[1])
        lo1, la1 = geo.xy_to_lonlat(bb[2], bb[3])
        bo = boc.get(raw, {})
        sp = species_of(bo)
        codes = {c0 for c0, _, _ in sp}
        if codes & set(NARA_CODES):
            rank = 2
        elif codes & set(NARA_MAYBE):
            rank = 1
        else:
            rank = 0
        area = bo.get('area') or ''
        try:
            area_ha = float(area)
        except (TypeError, ValueError):
            area_ha = round(sum(abs(ring_area(r)) for r in rings if ring_area(r) < 0) / 10000.0, 2)
        rows.append([
            '%s-%s' % (rn.lstrip('0') or '0', kn.lstrip('0') or '0'), rn, kn, chiku,
            area_ha, RINSHU.get((bo.get('rinshu') or '').strip(), ''),
            json.dumps([{'code': a, 'name': b, 'ratio': r} for a, b, r in sp], ensure_ascii=False),
            (sp[0][1] if sp else ''),
            (bo.get('age') or ''), (bo.get('height') or ''),
            (bo.get('volN') or bo.get('volL') or ''),
            (bo.get('chiban') or ''), (bo.get('slope') or ''),
            rank, None,
            round(lon, 7), round(lat, 7), round(c[0], 2), round(c[1], 2),
            round(bb[0], 2), round(bb[1], 2), round(bb[2], 2), round(bb[3], 2),
            round(lo0, 7), round(la0, 7), round(lo1, 7), round(la1, 7),
            json.dumps(rings_to_geojson(rings, args.simplify), ensure_ascii=False),
        ])
    d.close()
    log('   森町の小班 %d 件（%.0f秒）' % (len(rows), time.time() - t0))

    # 標高（小班重心）
    if dem is not None and rows:
        log('■ 小班重心の標高を取得（国土地理院DEM10B）')
        try:
            import numpy as np
            lons = np.array([r[15] for r in rows])
            lats = np.array([r[16] for r in rows])
            ev = dem.elevation_grid(lons, lats)
            for r, e in zip(rows, ev):
                r[14] = None if (e != e) else round(float(e), 1)
            log('   タイル取得 %d / データ無し %d' % (dem.fetched, dem.missing))
        except Exception as e:
            log('   標高取得を中止: %s' % e)

    con.executemany('INSERT OR REPLACE INTO kosyoban VALUES (%s)' % ','.join('?' * 28), rows)
    con.commit()

    # ---------------- GeoJSON レイヤー ----------------
    log('■ GeoJSON レイヤーを書き出し')
    lay = os.path.join(DATA, 'layers')

    feats = []
    for r in con.execute('SELECT rinpan,chiku,area_ha,lon,lat,geom FROM rinpan ORDER BY rinpan'):
        feats.append({'type': 'Feature',
                      'properties': {'林班': r[0].lstrip('0') or '0', '地区': r[1],
                                     '面積ha': r[2], 'lon': r[3], 'lat': r[4]},
                      'geometry': json.loads(r[5])})
    fc = {'type': 'FeatureCollection',
          'name': '森町 林班界',
          'attribution': '北海道オープンデータ 森林計画関係資料（一般民有林）令和6年 渡島',
          'features': feats}
    with open(os.path.join(lay, 'rinpan_mori.geojson'), 'w', encoding='utf-8') as f:
        json.dump(fc, f, ensure_ascii=False)
    log('   rinpan_mori.geojson  %d件  %.1f MB' % (
        len(feats), os.path.getsize(os.path.join(lay, 'rinpan_mori.geojson')) / 1e6))

    feats = []
    q = ('SELECT id,rinpan,kosyoban,sp_main,species,age,height,area_ha,elev,nara_rank,rinshu,geom '
         'FROM kosyoban WHERE nara_rank>0 ORDER BY nara_rank DESC, rinpan, kosyoban')
    for r in con.execute(q):
        feats.append({'type': 'Feature',
                      'properties': {'id': r[0], '林班': r[1].lstrip('0') or '0',
                                     '小班': r[2].lstrip('0') or '0', '主樹種': r[3],
                                     '樹種': json.loads(r[4] or '[]'), '林齢': r[5],
                                     '樹高': r[6], '面積ha': r[7], '標高m': r[8],
                                     'ナラ区分': r[9], '林種': r[10]},
                      'geometry': json.loads(r[11])})
    fc = {'type': 'FeatureCollection',
          'name': '森町 ナラ類を含む小班',
          'attribution': '北海道オープンデータ 森林調査簿（令和6年）＋国土地理院 標高タイル',
          'features': feats}
    p = os.path.join(lay, 'nara_mori.geojson')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(fc, f, ensure_ascii=False)
    log('   nara_mori.geojson    %d件  %.1f MB' % (len(feats), os.path.getsize(p) / 1e6))

    # ---------------- 統計 ----------------
    st = {}
    st['林班数'] = con.execute('SELECT COUNT(*) FROM rinpan').fetchone()[0]
    st['小班数'] = con.execute('SELECT COUNT(*) FROM kosyoban').fetchone()[0]
    st['ナラ類確実_小班数'] = con.execute('SELECT COUNT(*) FROM kosyoban WHERE nara_rank=2').fetchone()[0]
    st['ナラ類確実_面積ha'] = round(con.execute(
        'SELECT COALESCE(SUM(area_ha),0) FROM kosyoban WHERE nara_rank=2').fetchone()[0], 1)
    st['ナラ類可能性_小班数'] = con.execute('SELECT COUNT(*) FROM kosyoban WHERE nara_rank=1').fetchone()[0]
    st['標高200m以下のナラ類小班数'] = con.execute(
        'SELECT COUNT(*) FROM kosyoban WHERE nara_rank=2 AND elev IS NOT NULL AND elev<=200').fetchone()[0]
    st['標高200m以下のナラ類面積ha'] = round((con.execute(
        'SELECT COALESCE(SUM(area_ha),0) FROM kosyoban WHERE nara_rank=2 AND elev IS NOT NULL AND elev<=200'
    ).fetchone()[0]), 1)

    for k, v in [('出典_林小班', '北海道オープンデータ 森林計画関係資料（一般民有林）令和6年 渡島'),
                 ('出典_URL', ZIP_URL),
                 ('出典_標高', '国土地理院 標高タイル DEM10B（10mメッシュ）'),
                 ('座標系_原典', '平面直角座標系XI系 JGD2000 (EPSG:2459)'),
                 ('座標系_出力', 'WGS84相当 (EPSG:6668/4326)'),
                 ('市町村', '北海道茅部郡森町（森林計画上の市町村コード 01-15）'),
                 ('作成日時', time.strftime('%Y-%m-%d %H:%M:%S')),
                 ('統計', json.dumps(st, ensure_ascii=False))]:
        con.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (k, v))
    con.commit()
    con.execute('VACUUM')
    con.close()

    log('')
    log('■ 完了: %s (%.1f MB)' % (dbp, os.path.getsize(dbp) / 1e6))
    for k, v in st.items():
        log('   %-28s %s' % (k, v))


if __name__ == '__main__':
    main()
