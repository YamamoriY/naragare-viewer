# -*- coding: utf-8 -*-
"""国土地理院 標高タイル（DEM10B, 10mメッシュ）の取得とキャッシュ。

森町は「標高200m以下の高リスク区域」を重点管理地域として設定しているため、
地表面の標高が要る。ドローンDSMは林冠の高さなので地面の高さではない。

出典: 国土地理院 標高タイル https://maps.gsi.go.jp/development/ichiran.html
      （国土地理院コンテンツ利用規約に基づき利用）
一度取得したタイルは data/dem_cache/ に int16(デシメートル) で保存し、
以後はオフラインで参照できる。
"""
from __future__ import annotations
import os, math, urllib.request, urllib.error

import numpy as np

Z = 14                       # DEM10B が提供されているズーム
TILE = 256
URL = 'https://cyberjapandata.gsi.go.jp/xyz/dem/{z}/{x}/{y}.txt'
NODATA = -32768


def _tile_xy(lon, lat, z=Z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lr = math.radians(lat)
    y = (1.0 - math.log(math.tan(lr) + 1.0 / math.cos(lr)) / math.pi) / 2.0 * n
    return x, y


class DEM:
    def __init__(self, cache_dir, offline=False, quiet=False):
        self.dir = cache_dir
        self.offline = offline
        self.quiet = quiet
        os.makedirs(cache_dir, exist_ok=True)
        self._mem = {}
        self.fetched = 0
        self.missing = 0

    def _load(self, tx, ty):
        key = (tx, ty)
        if key in self._mem:
            return self._mem[key]
        path = os.path.join(self.dir, '%d_%d_%d.npy' % (Z, tx, ty))
        arr = None
        if os.path.exists(path):
            try:
                arr = np.load(path)
            except Exception:
                arr = None
        if arr is None and not self.offline:
            arr = self._download(tx, ty)
            if arr is not None:
                np.save(path, arr)
        if arr is None:
            arr = np.full((TILE, TILE), NODATA, dtype=np.int16)
        self._mem[key] = arr
        return arr

    def _download(self, tx, ty):
        url = URL.format(z=Z, x=tx, y=ty)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'naragare-viewer/1.0'})
            raw = urllib.request.urlopen(req, timeout=60).read().decode('utf-8')
        except urllib.error.HTTPError as e:
            if e.code == 404:            # 海域など標高データが無い区画
                self.missing += 1
                return np.full((TILE, TILE), NODATA, dtype=np.int16)
            if not self.quiet:
                print('  [DEM] 取得失敗 %s: %s' % (url, e))
            return None
        except Exception as e:
            if not self.quiet:
                print('  [DEM] 取得失敗 %s: %s' % (url, e))
            return None
        out = np.full((TILE, TILE), NODATA, dtype=np.int16)
        for r, line in enumerate(raw.strip().split('\n')):
            if r >= TILE:
                break
            for c, tok in enumerate(line.split(',')):
                if c >= TILE:
                    break
                tok = tok.strip()
                if tok and tok != 'e':
                    try:
                        out[r, c] = int(round(float(tok) * 10.0))
                    except ValueError:
                        pass
        self.fetched += 1
        return out

    def elevation(self, lon, lat):
        """1点の標高[m]。取得できなければ None。"""
        fx, fy = _tile_xy(lon, lat)
        tx, ty = int(fx), int(fy)
        px = (fx - tx) * TILE
        py = (fy - ty) * TILE
        arr = self._load(tx, ty)
        c = min(TILE - 1, max(0, int(px)))
        r = min(TILE - 1, max(0, int(py)))
        v = arr[r, c]
        return None if v == NODATA else v / 10.0

    def elevation_grid(self, lons, lats):
        """numpy配列でまとめて標高[m]を引く。欠測は NaN。"""
        lons = np.asarray(lons, dtype=float)
        lats = np.asarray(lats, dtype=float)
        out = np.full(lons.shape, np.nan, dtype=np.float32)
        n = 2 ** Z
        fx = (lons + 180.0) / 360.0 * n
        lr = np.radians(np.clip(lats, -85.05, 85.05))
        fy = (1.0 - np.log(np.tan(lr) + 1.0 / np.cos(lr)) / np.pi) / 2.0 * n
        tx = fx.astype(np.int64)
        ty = fy.astype(np.int64)
        for key in set(zip(tx.ravel().tolist(), ty.ravel().tolist())):
            m = (tx == key[0]) & (ty == key[1])
            arr = self._load(key[0], key[1])
            c = np.clip(((fx[m] - key[0]) * TILE).astype(np.int64), 0, TILE - 1)
            r = np.clip(((fy[m] - key[1]) * TILE).astype(np.int64), 0, TILE - 1)
            v = arr[r, c].astype(np.float32)
            v[v == NODATA] = np.nan
            out[m] = v / 10.0
        return out

    def prefetch_bbox(self, lon0, lat0, lon1, lat1):
        x0, y1 = _tile_xy(lon0, lat0)
        x1, y0 = _tile_xy(lon1, lat1)
        txs = range(int(min(x0, x1)), int(max(x0, x1)) + 1)
        tys = range(int(min(y0, y1)), int(max(y0, y1)) + 1)
        tiles = [(a, b) for a in txs for b in tys]
        for i, (a, b) in enumerate(tiles):
            self._load(a, b)
        return len(tiles)
