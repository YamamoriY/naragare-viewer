# -*- coding: utf-8 -*-
"""オルソ画像を取り込み、地図タイルとナラ枯れ候補木を作る。

これ1本で「オルソを入れれば成立する」を担保する。
  1. オルソを読む（GeoTIFF または JPEG+ワールドファイル）
  2. Web メルカトルの XYZ タイルへ変換して data/tiles/<サイト>/ に書く
  3. 赤褐色の樹冠を候補木として抽出する
  4. 各候補木に 林班・小班・樹種・標高 を付ける（forest.db と 国土地理院DEM）
  5. data/survey.db に登録する。既存の現地調査記録は絶対に壊さない

使い方
  python tools/ingest_ortho.py                    オルソ投入フォルダを全部処理
  python tools/ingest_ortho.py <ファイル|フォルダ>
  python tools/ingest_ortho.py X.tif --site siteD --name "○○地区" --date 2026-08-29
  python tools/ingest_ortho.py X.tif --tiles-only  タイルだけ作る（検出しない）
  python tools/ingest_ortho.py X.tif --dry-run     DBに書かずに結果だけ見る
"""
from __future__ import annotations
import os, sys, re, json, math, argparse, time, sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
INBOX = os.path.join(ROOT, 'オルソ投入')

IMG_EXT = ('.tif', '.tiff', '.jpg', '.jpeg', '.png')


def log(*a):
    print(*a)
    sys.stdout.flush()


def need(mod, hint):
    try:
        __import__(mod)
    except ImportError:
        raise SystemExit(
            '\n[!] %s が入っていません。次のコマンドで入れてください:\n    pip install %s\n' % (mod, hint))


def discover(target):
    """処理対象のオルソファイルを集める。"""
    out = []
    if target and os.path.isfile(target):
        return [target]
    root = target or INBOX
    if not os.path.isdir(root):
        return []
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            low = fn.lower()
            if not low.endswith(IMG_EXT):
                continue
            if '_dsm' in low or '_dtm' in low or low.endswith('_mask.tif'):
                continue
            out.append(os.path.join(dirpath, fn))
    return out


def site_id_from(path, override=None):
    if override:
        return override
    base = os.path.splitext(os.path.basename(path))[0]
    base = re.sub(r'[_-]?ortho.*$', '', base, flags=re.I)
    base = re.sub(r'[^0-9A-Za-z぀-ヿ一-鿿]+', '', base)
    return base or 'site'


def find_dsm(ortho_path):
    d = os.path.dirname(ortho_path)
    stem = re.sub(r'[_-]?ortho.*$', '', os.path.splitext(os.path.basename(ortho_path))[0], flags=re.I)
    for cand in (stem + '_dsm.tif', stem + '_dsm.tiff', stem + 'dsm.tif'):
        p = os.path.join(d, cand)
        if os.path.exists(p):
            return p
    return None


# --------------------------------------------------------------- 林班/小班
class ForestIndex:
    """forest.db の小班を使って、点がどの林班・小班に入るかを引く。"""

    def __init__(self, path):
        self.ok = os.path.exists(path)
        self.rows = []
        if not self.ok:
            return
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row

    def load_area(self, x0, y0, x1, y1, margin=300.0):
        if not self.ok:
            return 0
        q = ('SELECT id,rinpan,kosyoban,chiku,sp_main,nara_rank,chiban,elev,geom,'
             'minx,miny,maxx,maxy FROM kosyoban '
             'WHERE maxx>=? AND minx<=? AND maxy>=? AND miny<=?')
        self.rows = []
        for r in self.con.execute(q, (x0 - margin, x1 + margin, y0 - margin, y1 + margin)):
            try:
                g = json.loads(r['geom'])
            except Exception:
                continue
            self.rows.append((dict(r), g))
        return len(self.rows)

    @staticmethod
    def _in_ring(lon, lat, ring):
        inside = False
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if (yi > lat) != (yj > lat):
                if lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                    inside = not inside
            j = i
        return inside

    def lookup(self, lon, lat, x, y):
        for rec, g in self.rows:
            if not (rec['minx'] - 1 <= x <= rec['maxx'] + 1 and
                    rec['miny'] - 1 <= y <= rec['maxy'] + 1):
                continue
            polys = g['coordinates'] if g['type'] == 'MultiPolygon' else [g['coordinates']]
            for poly in polys:
                if not poly:
                    continue
                if self._in_ring(lon, lat, poly[0]):
                    if any(self._in_ring(lon, lat, h) for h in poly[1:]):
                        continue
                    return rec
        return None


# --------------------------------------------------------------- DSM
def read_dsm(path):
    """DSM(float32 GeoTIFF)を読む。読めなければ None。"""
    try:
        import numpy as np
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        import ortho as orthomod
        tr = orthomod._geotiff_transform(path)
        if tr is None:
            return None
        im = Image.open(path)
        arr = np.asarray(im, dtype=np.float32)
        im.close()
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        arr = np.where(arr <= -9990, np.nan, arr)
        return dict(a=arr, px=tr[0], py=tr[1], ox=tr[2], oy=tr[3])
    except Exception as e:
        log('   DSM を読めませんでした（樹高は算出しません）: %s' % e)
        return None


def dsm_value(d, x, y):
    import numpy as np
    c = int((x - d['ox']) / d['px'])
    r = int((d['oy'] - y) / d['py'])
    a = d['a']
    if 0 <= r < a.shape[0] and 0 <= c < a.shape[1]:
        v = a[r, c]
        return None if (v != v) else float(v)
    return None


# --------------------------------------------------------------- 本体
def ingest(path, args):
    import numpy as np
    import ortho as orthomod
    import detect as detectmod
    import geo
    import db as dbmod

    site = site_id_from(path, args.site)
    log('')
    log('=' * 68)
    log('■ %s' % os.path.basename(path))
    log('   サイトID: %s' % site)

    forced = None
    if args.epsg:
        import crs as crsmod
        forced = crsmod.from_epsg(args.epsg)
        if forced is None:
            raise SystemExit('EPSG:%d は未対応です' % args.epsg)
    o = orthomod.Ortho(path, max_pixels=args.max_pixels, crs=forced)
    x0, y0, x1, y1 = o.bounds_xy
    lo0, la0, lo1, la1 = o.bounds_lonlat
    log('   平面直角XI系: %.1f, %.1f 〜 %.1f, %.1f  (%.0f m × %.0f m)'
        % (x0, y0, x1, y1, x1 - x0, y1 - y0))
    log('   緯度経度    : %.6f, %.6f 〜 %.6f, %.6f' % (lo0, la0, lo1, la1))
    log('   有効画素(アルファ>0): %.1f %%' % (o.coverage() * 100))

    # ---- タイル ----
    tile_dir = os.path.join(DATA, 'tiles', site)
    zmin = zmax = None
    ext = 'webp'
    ntiles = 0
    if not args.no_tiles:
        zmax = args.zmax or orthomod.pick_max_zoom(o)
        zmin = args.zmin if args.zmin is not None else max(10, zmax - 9)
        log('   タイル生成 z%d〜z%d -> data/tiles/%s' % (zmin, zmax, site))

        state = {'t': time.time()}

        def prog(z, done, total):
            if time.time() - state['t'] > 2.0 or done >= total:
                state['t'] = time.time()
                sys.stdout.write('\r      z%-3d %6d / %-6d' % (z, done, total))
                sys.stdout.flush()

        zmin, zmax, ext, ntiles = orthomod.write_tiles(
            o, tile_dir, zmin=zmin, zmax=zmax, fmt=args.tile_format,
            quality=args.quality, progress=prog)
        sys.stdout.write('\r      タイル %d 枚 (%s)            \n' % (ntiles, ext))

    # 候補木の自動抽出は既定では行わない。
    # 抽出は別のアルゴリズムで行い、後からマージする運用のため。
    # 同梱の色ベース抽出を使うときは --detect を付ける。
    if args.tiles_only or not args.detect:
        cands = []
        if not args.tiles_only:
            log('   候補木の自動抽出はしません（--detect を付けると同梱の抽出を使います）')
    else:
        log('   ナラ枯れ候補を抽出中…')
        t0 = time.time()
        cands = detectmod.detect_candidates(o, params=dict(
            vari_max=args.vari_max, exr_min=args.exr_min, sat_min=args.sat_min,
            val_min=args.val_min, min_area_m2=args.min_area, max_area_m2=args.max_area,
            work_res_m=args.work_res))
        log('   候補 %d 件 (%.0f 秒)' % (len(cands), time.time() - t0))
        if args.limit and len(cands) > args.limit:
            log('   スコア上位 %d 件に絞ります（--limit）' % args.limit)
            cands = cands[:args.limit]

    # ---- 属性付け ----
    fi = ForestIndex(dbmod.FOREST_DB)
    if fi.ok and cands:
        n = fi.load_area(x0, y0, x1, y1)
        log('   周辺の小班 %d 件を読み込み（林班・樹種の判定用）' % n)
    elif not fi.ok:
        log('   [!] data/forest.db がありません。先に tools/build_forest.py を実行すると'
            ' 林班・小班・樹種が付きます。')

    dem = None
    try:
        from dem import DEM
        dem = DEM(os.path.join(DATA, 'dem_cache'), quiet=True)
        dem.prefetch_bbox(lo0, la0, lo1, la1)
    except Exception as e:
        log('   標高は取得しません: %s' % e)

    dsm = None
    ground_bias = 0.0
    dsm_path = args.dsm or find_dsm(path)
    if dsm_path and cands:
        log('   DSM: %s' % os.path.basename(dsm_path))
        dsm = read_dsm(dsm_path)

    log('   候補木に林班・小班・標高を付与中…')
    enriched = []
    for c in cands:
        lon, lat = o.crs.to_lonlat(c['x'], c['y'])
        rec = fi.lookup(lon, lat, c['x'], c['y']) if fi.rows else None
        elev = dem.elevation(lon, lat) if dem else None
        if elev is None and rec:
            elev = rec['elev']
        ch = None
        if dsm and elev is not None:
            v = dsm_value(dsm, c['x'], c['y'])
            if v is not None:
                ch = v - elev
        e = dict(c)
        e.update(lon=lon, lat=lat, elev=elev, canopy_h=ch,
                 rinpan=(rec['rinpan'] if rec else None),
                 kosyoban=(rec['kosyoban'] if rec else None),
                 chiku=(rec['chiku'] if rec else None),
                 sp_main=(rec['sp_main'] if rec else None),
                 nara_rank=(rec['nara_rank'] if rec else None),
                 chiban=(rec['chiban'] if rec else None))
        enriched.append(e)

    # DSM の系統的な高さのずれを、下位5%を地面とみなして補正する
    if dsm is not None and enriched:
        hs = sorted(e['canopy_h'] for e in enriched if e['canopy_h'] is not None)
        if len(hs) >= 20:
            ground_bias = hs[int(len(hs) * 0.05)]
            log('   樹高の基準ずれ補正: %.1f m を差し引き（参考値）' % ground_bias)
            for e in enriched:
                if e['canopy_h'] is not None:
                    e['canopy_h'] = round(e['canopy_h'] - ground_bias, 1)

    for e in enriched:
        e['rank'] = detectmod.rank_score(e['score'], e['elev'], e['nara_rank'])
    detectmod.assign_priority(enriched, high_n=args.high_count, mid_n=args.mid_count)

    # ---- 集計表示 ----
    if enriched:
        from collections import Counter
        pr = Counter(e['priority'] for e in enriched)
        rp = Counter((e['rinpan'] or '不明').lstrip('0') or '0' for e in enriched)
        log('   優先度: 高 %d / 中 %d / 低 %d' % (pr['高'], pr['中'], pr['低']))
        log('   林班別: ' + ', '.join('%s林班 %d件' % (k, v) for k, v in rp.most_common(8)))

    if args.dry_run:
        log('   --dry-run のため DB には書きません。')
        for e in enriched[:15]:
            log('      %s 優先度%s score%.2f  %.6f,%.6f  林班%s-%s %s'
                % ('候補', e['priority'], e['score'], e['lon'], e['lat'],
                   (e['rinpan'] or '?').lstrip('0'), (e['kosyoban'] or '?').lstrip('0'),
                   e['sp_main'] or ''))
        return

    # ---- DB 登録 ----
    con = dbmod.connect()
    now = dbmod.now()

    rinpan_main = None
    if enriched:
        from collections import Counter
        rinpan_main = Counter(e['rinpan'] for e in enriched if e['rinpan']).most_common(1)
        rinpan_main = rinpan_main[0][0] if rinpan_main else None

    row = con.execute('SELECT id FROM sites WHERE id=?', (site,)).fetchone()
    vals = dict(id=site, name=args.name, flown_on=args.date,
                source=os.path.basename(path), tile_ext=ext, zmin=zmin, zmax=zmax,
                minlon=lo0, minlat=la0, maxlon=lo1, maxlat=la1,
                px_size=o.px, coverage=round(o.coverage(), 4),
                rinpan=rinpan_main, note=args.note, detected_at=now, updated_at=now)
    crs_name = getattr(o.crs, 'name', '')
    if crs_name and args.note is None:
        vals['note'] = '座標系: %s' % crs_name
    if row:
        # --name / --date / --note を付けずに取り込み直したときに、
        # すでに付けてある表示名や撮影日を消さないようにする
        keys = [k for k in vals if k != 'id' and vals[k] is not None]
        con.execute('UPDATE sites SET %s WHERE id=?' % ','.join('%s=?' % k for k in keys),
                    [vals[k] for k in keys] + [site])
    else:
        vals['name'] = args.name or site
        vals['created_at'] = now
        con.execute('INSERT INTO sites(%s) VALUES (%s)'
                    % (','.join(vals), ','.join('?' * len(vals))), list(vals.values()))

    # 既存の候補木（人の入力があるかもしれない）と突き合わせる
    existing = con.execute('SELECT id, code, x, y, status FROM trees WHERE site=?',
                           (site,)).fetchall()
    used = set()
    added = updated = 0
    tol = args.match_dist

    def nearest(x, y):
        best, bd = None, tol * tol
        for r in existing:
            if r['id'] in used or r['x'] is None:
                continue
            d = (r['x'] - x) ** 2 + (r['y'] - y) ** 2
            if d <= bd:
                best, bd = r, d
        return best

    for e in enriched:
        m = nearest(e['x'], e['y'])
        det = dict(lon=round(e['lon'], 7), lat=round(e['lat'], 7),
                   x=round(e['x'], 2), y=round(e['y'], 2),
                   elev=(round(e['elev'], 1) if e['elev'] is not None else None),
                   canopy_h=e['canopy_h'],
                   rinpan=e['rinpan'], kosyoban=e['kosyoban'], chiku=e['chiku'],
                   sp_main=e['sp_main'], nara_rank=e['nara_rank'], chiban=e['chiban'],
                   area_m2=round(e['area_m2'], 1), score=e['score'],
                   priority=e['priority'], updated_at=now)
        if m:
            used.add(m['id'])
            # 人が入力した項目（status など）には触れない
            con.execute('UPDATE trees SET %s WHERE id=?' % ','.join('%s=?' % k for k in det),
                        list(det.values()) + [m['id']])
            updated += 1
        else:
            det.update(code=dbmod.next_code(con, site), site=site,
                       status='unsurveyed', source='ai',
                       loc_accuracy='オルソ由来（水平±3〜5m）', created_at=now)
            con.execute('INSERT INTO trees(%s) VALUES (%s)'
                        % (','.join(det), ','.join('?' * len(det))), list(det.values()))
            added += 1

    con.commit()
    stale = len(existing) - len(used)
    log('   DB: 新規 %d 件 / 既存更新 %d 件 / 今回検出されなかった既存 %d 件（残置）'
        % (added, updated, stale))
    con.close()
    write_sites_json()


def write_sites_json():
    """フロントエンドが読むサイト一覧を書き出す。"""
    import db as dbmod
    con = dbmod.connect()
    sites = []
    for r in con.execute('SELECT * FROM sites ORDER BY id'):
        d = dict(r)
        d['tiles'] = 'data/tiles/%s/{z}/{x}/{y}.%s' % (d['id'], d['tile_ext'] or 'png')
        n = con.execute('SELECT COUNT(*) c FROM trees WHERE site=?', (d['id'],)).fetchone()['c']
        d['tree_count'] = n
        sites.append(d)
    con.close()
    p = os.path.join(DATA, 'sites.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump({'sites': sites}, f, ensure_ascii=False, indent=1)
    return p


def main():
    ap = argparse.ArgumentParser(description='オルソを取り込んでタイルと候補木を作る')
    ap.add_argument('target', nargs='?', help='オルソのファイルかフォルダ（省略時は オルソ投入/）')
    ap.add_argument('--site', help='サイトID（省略時はファイル名から）')
    ap.add_argument('--name', help='画面に表示する名前')
    ap.add_argument('--date', help='撮影日 YYYY-MM-DD')
    ap.add_argument('--note', help='備考')
    ap.add_argument('--dsm', help='DSMのパス（省略時は同名_dsm.tifを探す）')
    ap.add_argument('--epsg', type=int,
                    help='座標系を明示する（例 32654 = UTM54N）。自動判定に失敗するとき')

    ap.add_argument('--zmin', type=int)
    ap.add_argument('--zmax', type=int)
    ap.add_argument('--tile-format', choices=['webp', 'png'])
    ap.add_argument('--quality', type=int, default=82)
    ap.add_argument('--no-tiles', action='store_true', help='タイルを作らない')
    ap.add_argument('--tiles-only', action='store_true', help='タイルだけ作る')
    ap.add_argument('--detect', action='store_true',
                    help='同梱の色ベース抽出で候補木を作る（既定では作らない）')
    ap.add_argument('--max-pixels', type=int, default=260_000_000,
                    help='この画素数を超えるオルソは縮小して処理する')

    ap.add_argument('--work-res', type=float, default=None, help='検出の解像度[m]')
    ap.add_argument('--vari-max', type=float, default=None)
    ap.add_argument('--exr-min', type=float, default=None)
    ap.add_argument('--sat-min', type=float, default=None)
    ap.add_argument('--val-min', type=float, default=None)
    ap.add_argument('--min-area', type=float, default=None, help='候補の最小面積[m2]')
    ap.add_argument('--max-area', type=float, default=None, help='候補の最大面積[m2]')
    ap.add_argument('--limit', type=int, default=3000, help='登録する候補の上限')
    ap.add_argument('--high-count', type=int, default=40,
                    help='優先度「高」にする件数（サイト内の上位から）')
    ap.add_argument('--mid-count', type=int, default=120,
                    help='優先度「中」にする件数')
    ap.add_argument('--match-dist', type=float, default=3.0,
                    help='既存の候補木と同一とみなす距離[m]')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    need('numpy', 'numpy')
    need('PIL', 'pillow')

    targets = discover(args.target)
    if not targets:
        log('処理するオルソが見つかりません。')
        log('  「オルソ投入」フォルダに次のどちらかを置いてください:')
        log('    ・GeoTIFF     例) siteA_ortho.tif')
        log('    ・JPEG + .jgw 例) siteA_ortho.jpg と siteA_ortho.jgw')
        log('  （DSM を一緒に置くと樹高の参考値も出ます: siteA_dsm.tif）')
        return
    log('対象 %d 件' % len(targets))
    for p in targets:
        try:
            ingest(p, args)
        except SystemExit as e:
            log('  [中断] %s' % e)
        except Exception as e:
            import traceback
            log('  [エラー] %s: %s' % (os.path.basename(p), e))
            traceback.print_exc()
    log('')
    log('完了。「起動.bat」でビューアーを開いてください。')


if __name__ == '__main__':
    main()
