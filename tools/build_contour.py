# -*- coding: utf-8 -*-
"""国土地理院の標高タイルから等高線をつくる。

森町は「標高200m以下の高リスク区域」を重点管理地域としているので、
その境目である 200m の等高線を地図に重ねられるようにする。

塗りつぶしではなく線にしているのは、
  * 下に敷いたオルソを隠さない（オルソを見るのが主目的なので）
  * どのズームでも滲まない（ベクタなので）
  * 軽い（タイルを何千枚も作らずに済む）
ため。

  python tools/build_contour.py                 200m の等高線をつくる
  python tools/build_contour.py --threshold 300
  python tools/build_contour.py --offline       すでに取得済みのDEMだけで作る

出力: data/layers/contour<標高>_mori.geojson
"""
from __future__ import annotations
import os, sys, json, math, argparse, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np

from dem import DEM, NODATA, Z, TILE, _tile_xy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')

# 森町のおおよその範囲（林班界の外接矩形に少し余裕を持たせたもの）
MORI_BBOX = (140.40, 41.99, 140.79, 42.29)


def log(*a):
    print(*a)
    sys.stdout.flush()


def assemble(dem, bbox, quiet=False):
    """DEMタイルを1枚の配列につなぐ。戻り値 (grid, tx0, ty0)"""
    lo0, la0, lo1, la1 = bbox
    ax, ay = _tile_xy(lo0, la1)      # 左上
    bx, by = _tile_xy(lo1, la0)      # 右下
    tx0, tx1 = int(min(ax, bx)), int(max(ax, bx))
    ty0, ty1 = int(min(ay, by)), int(max(ay, by))
    nx, ny = tx1 - tx0 + 1, ty1 - ty0 + 1
    log('   タイル %d x %d = %d 枚 / グリッド %d x %d 画素'
        % (nx, ny, nx * ny, nx * TILE, ny * TILE))

    grid = np.full((ny * TILE, nx * TILE), NODATA, dtype=np.int16)
    done = 0
    t0 = time.time()
    for j in range(ny):
        for i in range(nx):
            arr = dem._load(tx0 + i, ty0 + j)
            grid[j * TILE:(j + 1) * TILE, i * TILE:(i + 1) * TILE] = arr
            done += 1
            if not quiet and (done % 20 == 0 or done == nx * ny):
                sys.stdout.write('\r      %d / %d 枚（新規取得 %d）' % (done, nx * ny, dem.fetched))
                sys.stdout.flush()
    if not quiet:
        sys.stdout.write('\n')
    log('   組み立て %.0f 秒 / 有効画素 %.1f %%'
        % (time.time() - t0, float((grid != NODATA).mean()) * 100))
    return grid, tx0, ty0


def marching_squares(grid, thr_dm):
    """しきい値の等高線を線分の集まりとして返す。

    grid は int16（デシメートル）。NODATA を含むセルは飛ばす。
    戻り値は [( (c0,r0), (c1,r1) ), ...] のグリッド座標（画素・小数）。
    """
    v = grid.astype(np.float32)
    valid = grid != NODATA

    tl, tr = v[:-1, :-1], v[:-1, 1:]
    bl, br = v[1:, :-1], v[1:, 1:]
    ok = valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, :-1] & valid[1:, 1:]

    # 「内側」＝しきい値以下（＝重点管理側）
    a = (tl <= thr_dm)
    b = (tr <= thr_dm)
    c = (br <= thr_dm)
    d = (bl <= thr_dm)
    case = (a.astype(np.uint8) | (b.astype(np.uint8) << 1)
            | (c.astype(np.uint8) << 2) | (d.astype(np.uint8) << 3))

    active = ok & (case > 0) & (case < 15)
    rr, cc = np.nonzero(active)
    log('   等高線が通るセル: %s' % '{:,}'.format(len(rr)))

    T, TRv, B, L = {}, {}, {}, {}

    def interp(v0, v1):
        dv = v1 - v0
        return np.where(np.abs(dv) < 1e-9, 0.5, (thr_dm - v0) / np.where(dv == 0, 1, dv))

    tlv, trv = tl[rr, cc], tr[rr, cc]
    blv, brv = bl[rr, cc], br[rr, cc]
    ct = case[rr, cc]

    top = np.stack([cc + interp(tlv, trv), rr + 0.0], axis=1)
    rgt = np.stack([cc + 1.0, rr + interp(trv, brv)], axis=1)
    bot = np.stack([cc + interp(blv, brv), rr + 1.0], axis=1)
    lft = np.stack([cc + 0.0, rr + interp(tlv, blv)], axis=1)

    # case ごとに、どの辺とどの辺を結ぶか
    PAIRS = {
        1: [('L', 'T')], 2: [('T', 'R')], 3: [('L', 'R')],
        4: [('R', 'B')], 6: [('T', 'B')], 7: [('L', 'B')],
        8: [('B', 'L')], 9: [('B', 'T')], 11: [('B', 'R')],
        12: [('R', 'L')], 13: [('R', 'T')], 14: [('T', 'L')],
        5: [('L', 'T'), ('R', 'B')],      # 鞍点
        10: [('T', 'R'), ('B', 'L')],
    }
    E = {'T': top, 'R': rgt, 'B': bot, 'L': lft}
    segs = []
    for cv, pairs in PAIRS.items():
        m = (ct == cv)
        if not m.any():
            continue
        idx = np.nonzero(m)[0]
        for e0, e1 in pairs:
            p0, p1 = E[e0][idx], E[e1][idx]
            segs.append(np.concatenate([p0, p1], axis=1))
    if not segs:
        return np.zeros((0, 4), dtype=np.float32)
    return np.concatenate(segs, axis=0)


def link(segs, round_to=4):
    """線分をつないで折れ線にする。"""
    from collections import defaultdict
    key = lambda p: (round(float(p[0]), round_to), round(float(p[1]), round_to))
    adj = defaultdict(list)
    for i, s in enumerate(segs):
        k0, k1 = key(s[0:2]), key(s[2:4])
        adj[k0].append((k1, i))
        adj[k1].append((k0, i))
    used = set()
    lines = []
    starts = [k for k, v in adj.items() if len(v) == 1] + list(adj.keys())
    for s0 in starts:
        for nxt, si in adj[s0]:
            if si in used:
                continue
            path = [s0]
            cur, seg = s0, si
            while True:
                used.add(seg)
                nk = None
                for k2, i2 in adj[cur]:
                    if i2 == seg:
                        nk = k2
                        break
                if nk is None:
                    break
                path.append(nk)
                cur = nk
                nseg = None
                for k2, i2 in adj[cur]:
                    if i2 not in used:
                        nseg = i2
                        break
                if nseg is None:
                    break
                seg = nseg
            if len(path) > 1:
                lines.append(path)
    return lines


def simplify(pts, tol):
    if len(pts) < 3 or tol <= 0:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if den == 0:
                dd = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / den
                t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                dd = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if dd > best:
                best, bi = dd, i
        if best > tol * tol:
            keep[bi] = True
            stack.append((a, bi))
            stack.append((bi, b))
    return [p for p, k in zip(pts, keep) if k]


def to_lonlat(px, py, tx0, ty0):
    n = 2 ** Z
    fx = tx0 + (px + 0.5) / TILE
    fy = ty0 + (py + 0.5) / TILE
    lon = fx / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * fy / n))))
    return lon, lat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--threshold', type=float, default=200.0, help='標高[m]（既定 200）')
    ap.add_argument('--bbox', help='lon0,lat0,lon1,lat1')
    ap.add_argument('--offline', action='store_true', help='取得済みのDEMだけで作る')
    ap.add_argument('--simplify', type=float, default=0.35,
                    help='線の間引き（画素単位。0.35 ≒ 2.5m）')
    ap.add_argument('--min-points', type=int, default=6, help='これ未満の短い線は捨てる')
    args = ap.parse_args()

    bbox = tuple(float(v) for v in args.bbox.split(',')) if args.bbox else MORI_BBOX
    thr_dm = args.threshold * 10.0

    log('■ 標高 %g m の等高線をつくる' % args.threshold)
    log('   範囲 %.3f, %.3f 〜 %.3f, %.3f' % bbox)
    dem = DEM(os.path.join(DATA, 'dem_cache'), offline=args.offline, quiet=True)
    log('   国土地理院 標高タイル DEM10B（10mメッシュ）を読み込み')
    grid, tx0, ty0 = assemble(dem, bbox)
    if dem.fetched:
        log('   新規に取得したタイル %d 枚' % dem.fetched)

    segs = marching_squares(grid, thr_dm)
    if len(segs) == 0:
        raise SystemExit('等高線が見つかりませんでした（しきい値を確認してください）')
    log('   線分 %s 本' % '{:,}'.format(len(segs)))

    lines = link(segs)
    log('   つないだ折れ線 %s 本' % '{:,}'.format(len(lines)))

    feats = []
    total_m = 0.0
    kept = 0
    for pts in lines:
        if len(pts) < args.min_points:
            continue
        sp = simplify(pts, args.simplify)
        if len(sp) < 2:
            continue
        coords = []
        for px, py in sp:
            lon, lat = to_lonlat(px, py, tx0, ty0)
            coords.append([round(lon, 6), round(lat, 6)])
        # 長さ（おおよそ）
        for i in range(1, len(coords)):
            dx = (coords[i][0] - coords[i - 1][0]) * 111320 * math.cos(math.radians(coords[i][1]))
            dy = (coords[i][1] - coords[i - 1][1]) * 110570
            total_m += math.hypot(dx, dy)
        feats.append(coords)
        kept += 1

    gj = {
        'type': 'FeatureCollection',
        'name': '森町 標高%gm 等高線' % args.threshold,
        'attribution': '国土地理院 標高タイル DEM10B（10mメッシュ）',
        'note': ('森町が重点管理地域としている標高%gm の境。'
                 'DEMは10mメッシュのため、線の位置には数m〜十数mの誤差がある。' % args.threshold),
        'features': [{
            'type': 'Feature',
            'properties': {'標高m': args.threshold},
            'geometry': {'type': 'MultiLineString', 'coordinates': feats},
        }],
    }
    os.makedirs(os.path.join(DATA, 'layers'), exist_ok=True)
    out = os.path.join(DATA, 'layers', 'contour%g_mori.geojson' % args.threshold)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(gj, f, ensure_ascii=False)

    log('')
    log('■ 完了: %s' % out)
    log('   線 %s 本 / 頂点 %s 点 / 総延長 %.1f km / %.2f MB'
        % ('{:,}'.format(kept), '{:,}'.format(sum(len(c) for c in feats)),
           total_m / 1000.0, os.path.getsize(out) / 1e6))

    below = ((grid != NODATA) & (grid <= thr_dm)).sum()
    valid = (grid != NODATA).sum()
    log('   範囲内の陸地のうち 標高%gm以下: %.1f %%（%.0f km²）'
        % (args.threshold, below / max(valid, 1) * 100, below * 100 / 1e6))


if __name__ == '__main__':
    main()
