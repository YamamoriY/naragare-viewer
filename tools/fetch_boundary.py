# -*- coding: utf-8 -*-
"""森町の行政界（町の境界線）を取ってきて、地図に重ねられる形にする。

    py -3 tools/fetch_boundary.py

なぜ要るか
----------
林班界は「森林計画の区画」であって、町の境界ではない。
現地で「ここはもう鹿部町だ」「ここから森町」が分かるかどうかは、
所有者確認・予算の出どころ・報告先が変わるという意味で実務に効く。

出典
----
「歴史的行政区域データセットβ版」（CODH / ROIS-DS 人文学オープンデータ共同利用センター）
  https://geoshape.ex.nii.ac.jp/city/
  国土数値情報「行政区域データ」（国土交通省）を加工したもの。CC BY 4.0。

点が多いままだとスマホで重いので、Douglas-Peucker で間引いてから保存する。
間引きの許容誤差は既定 3m。境界の確定には使えない図なので、この程度で足りる。
"""
from __future__ import annotations
import json, math, os, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'layers', 'boundary_mori.geojson')

# 森町（北海道茅部郡）。01345 は市町村コード、A1968 はデータセット内の版。
CITY = '01345'
CANDIDATES = [
    'https://geoshape.ex.nii.ac.jp/city/geojson/20230101/01/01345A1968.geojson',
    'https://geoshape.ex.nii.ac.jp/city/geojson/20220101/01/01345A1968.geojson',
    'https://geoshape.ex.nii.ac.jp/city/geojson/20200101/01/01345A1968.geojson',
]
# 隣接町村も入れておくと「どちらの町か」が分かる
NEIGHBORS = {
    '01337': ('鹿部町', 'https://geoshape.ex.nii.ac.jp/city/geojson/20230101/01/01337A1968.geojson'),
    '01346': ('八雲町', 'https://geoshape.ex.nii.ac.jp/city/geojson/20230101/01/01346A1968.geojson'),
    '01202': ('函館市', 'https://geoshape.ex.nii.ac.jp/city/geojson/20230101/01/01202A1968.geojson'),
}

TOL_M = 3.0


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent': 'naragare-viewer/2.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


# ------------------------------------------------------------------ 間引き
def _perp(p, a, b):
    """線分 a-b から点 p までの距離（緯度経度をおおよそメートルに直して測る）。"""
    k = math.cos(math.radians(a[1])) * 111320.0
    ax, ay = a[0] * k, a[1] * 110540.0
    bx, by = b[0] * k, b[1] * 110540.0
    px, py = p[0] * k, p[1] * 110540.0
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplify(pts, tol=TOL_M):
    """Douglas-Peucker。再帰ではなくスタックで回す（点が多いので）。"""
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        worst, wi = -1.0, i
        for k in range(i + 1, j):
            d = _perp(pts[k], pts[i], pts[j])
            if d > worst:
                worst, wi = d, k
        if worst > tol:
            keep[wi] = True
            stack.append((i, wi))
            stack.append((wi, j))
    return [p for p, k in zip(pts, keep) if k]


def simplify_geom(g, tol=TOL_M):
    def ring(r):
        out = simplify([[round(c[0], 6), round(c[1], 6)] for c in r], tol)
        if out[0] != out[-1]:
            out.append(out[0])
        return out if len(out) >= 4 else None

    if g['type'] == 'Polygon':
        rs = [x for x in (ring(r) for r in g['coordinates']) if x]
        return {'type': 'Polygon', 'coordinates': rs} if rs else None
    if g['type'] == 'MultiPolygon':
        ps = []
        for poly in g['coordinates']:
            rs = [x for x in (ring(r) for r in poly) if x]
            if rs:
                ps.append(rs)
        return {'type': 'MultiPolygon', 'coordinates': ps} if ps else None
    return g


def count_pts(g):
    if g['type'] == 'Polygon':
        return sum(len(r) for r in g['coordinates'])
    return sum(len(r) for p in g['coordinates'] for r in p)


def grab(url, name, code, main):
    try:
        d = fetch(url)
    except Exception as e:
        print('  [!] 取得できませんでした %s: %s' % (name, e))
        return None
    feats = d.get('features') or []
    if not feats:
        return None
    # 同じ市町村が複数フィーチャに分かれていることがあるのでまとめる
    polys = []
    for f in feats:
        g = f.get('geometry') or {}
        if g.get('type') == 'Polygon':
            polys.append(g['coordinates'])
        elif g.get('type') == 'MultiPolygon':
            polys += g['coordinates']
    if not polys:
        return None
    g = {'type': 'MultiPolygon', 'coordinates': polys}
    before = count_pts(g)
    g = simplify_geom(g)
    after = count_pts(g)
    print('  %s: %d点 → %d点' % (name, before, after))
    return {'type': 'Feature',
            'properties': {'name': name, 'code': code, 'main': bool(main)},
            'geometry': g}


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    feats = []
    print('森町の境界を取ってきます…')
    for url in CANDIDATES:
        f = grab(url, '森町', CITY, True)
        if f:
            feats.append(f)
            break
    if not feats:
        print('')
        print('森町の境界が取れませんでした。インターネットに繋がっているか確かめてください。')
        print('（この機能が無くても、他の画面はそのまま使えます）')
        return 1

    print('となりの町も取ってきます（境目が分かるように）…')
    for code, (name, url) in NEIGHBORS.items():
        f = grab(url, name, code, False)
        if f:
            feats.append(f)

    fc = {'type': 'FeatureCollection',
          'properties': {
              'source': '歴史的行政区域データセットβ版（CODH）／'
                        '国土数値情報 行政区域データ（国土交通省）',
              'license': 'CC BY 4.0',
              'note': '表示用に %.0fm 相当で間引いてあります。境界の確定には使えません。' % TOL_M,
          },
          'features': feats}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(fc, f, ensure_ascii=False, separators=(',', ':'))
    print('')
    print('できました: %s（%.0f KB）' % (OUT, os.path.getsize(OUT) / 1024.0))
    print('画面の「重ねる情報」に「町の境界」が出ます。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
