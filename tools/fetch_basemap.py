# -*- coding: utf-8 -*-
"""背景地図（国土地理院タイル）をローカルに取り込み、オフラインでも使えるようにする。

現地では電波が届かないことがあるので、必要な範囲だけ先に落としておく。
出典: 国土地理院タイル https://maps.gsi.go.jp/development/ichiran.html
      （国土地理院コンテンツ利用規約に従って利用すること）

  python tools/fetch_basemap.py                 森町全域(z10-14) + 各サイト(z15-18)
  python tools/fetch_basemap.py --layer std     地理院地図（地形図）も取る
  python tools/fetch_basemap.py --zmax 19       サイト周辺をもっと細かく
"""
from __future__ import annotations
import os, sys, math, json, time, argparse, urllib.request, urllib.error, sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')

LAYERS = {
    # 名前: (URLテンプレート, 拡張子, 最大ズーム)
    'photo': ('https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg', 'jpg', 18),
    'std':   ('https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png', 'png', 18),
    'pale':  ('https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png', 'png', 18),
}

# 森町のおおよその範囲（森林計画データの林班界から得た外接矩形）
MORI_BBOX = (140.40, 41.99, 140.79, 42.29)


def tiles_for(bbox, z):
    lo0, la0, lo1, la1 = bbox
    x0, y0 = geo.lonlat_to_tile(lo0, la1, z)
    x1, y1 = geo.lonlat_to_tile(lo1, la0, z)
    for tx in range(min(x0, x1), max(x0, x1) + 1):
        for ty in range(min(y0, y1), max(y0, y1) + 1):
            yield tx, ty


def fetch(layer, bbox, zmin, zmax, out_root, sleep=0.05, quiet=False):
    url_t, ext, cap = LAYERS[layer]
    zmax = min(zmax, cap)
    got = skip = miss = 0
    for z in range(zmin, zmax + 1):
        lst = list(tiles_for(bbox, z))
        for i, (tx, ty) in enumerate(lst):
            d = os.path.join(out_root, layer, str(z), str(tx))
            p = os.path.join(d, '%d.%s' % (ty, ext))
            if os.path.exists(p) and os.path.getsize(p) > 0:
                skip += 1
                continue
            url = url_t.format(z=z, x=tx, y=ty)
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'naragare-viewer/1.0'})
                b = urllib.request.urlopen(req, timeout=60).read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    miss += 1
                    continue
                if not quiet:
                    print('  取得失敗 %s: %s' % (url, e))
                continue
            except Exception as e:
                if not quiet:
                    print('  取得失敗 %s: %s' % (url, e))
                continue
            os.makedirs(d, exist_ok=True)
            with open(p, 'wb') as f:
                f.write(b)
            got += 1
            time.sleep(sleep)
            if not quiet and got % 25 == 0:
                sys.stdout.write('\r    %s z%d  取得 %d / 既存 %d / 無し %d'
                                 % (layer, z, got, skip, miss))
                sys.stdout.flush()
    if not quiet:
        sys.stdout.write('\r    %s  取得 %d / 既存 %d / 無し %d            \n'
                         % (layer, got, skip, miss))
    return got, skip, miss


def site_bboxes():
    p = os.path.join(DATA, 'survey.db')
    out = []
    if os.path.exists(p):
        con = sqlite3.connect(p)
        try:
            for r in con.execute('SELECT id,minlon,minlat,maxlon,maxlat FROM sites'):
                if None in r[1:]:
                    continue
                pad = 0.004
                out.append((r[0], (r[1] - pad, r[2] - pad, r[3] + pad, r[4] + pad)))
        except sqlite3.Error:
            pass
        con.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--layer', action='append', choices=list(LAYERS),
                    help='取得するレイヤー（既定 photo, pale）')
    ap.add_argument('--zmin', type=int, default=10, help='町全域の最小ズーム')
    ap.add_argument('--town-zmax', type=int, default=14, help='町全域の最大ズーム')
    ap.add_argument('--zmax', type=int, default=18, help='サイト周辺の最大ズーム')
    ap.add_argument('--bbox', help='lon0,lat0,lon1,lat1 を直接指定')
    ap.add_argument('--sleep', type=float, default=0.05)
    args = ap.parse_args()

    layers = args.layer or ['photo', 'pale']
    out_root = os.path.join(DATA, 'basemap')
    os.makedirs(out_root, exist_ok=True)

    if args.bbox:
        bb = tuple(float(v) for v in args.bbox.split(','))
        for lay in layers:
            print('■ %s  指定範囲 z%d-%d' % (lay, args.zmin, args.zmax))
            fetch(lay, bb, args.zmin, args.zmax, out_root, args.sleep)
    else:
        for lay in layers:
            print('■ %s  森町全域 z%d-%d' % (lay, args.zmin, args.town_zmax))
            fetch(lay, MORI_BBOX, args.zmin, args.town_zmax, out_root, args.sleep)
        sb = site_bboxes()
        if not sb:
            print('  （サイト未登録のため、サイト周辺の詳細タイルは取得しません）')
        for sid, bb in sb:
            for lay in layers:
                print('■ %s  %s 周辺 z%d-%d' % (lay, sid, args.town_zmax + 1, args.zmax))
                fetch(lay, bb, args.town_zmax + 1, args.zmax, out_root, args.sleep)

    total = 0
    size = 0
    for dirpath, dirnames, filenames in os.walk(out_root):
        for fn in filenames:
            total += 1
            size += os.path.getsize(os.path.join(dirpath, fn))
    print('')
    print('背景地図タイル: %d 枚 / %.1f MB  -> %s' % (total, size / 1e6, out_root))
    print('出典: 国土地理院タイル（https://maps.gsi.go.jp/development/ichiran.html）')


if __name__ == '__main__':
    main()
