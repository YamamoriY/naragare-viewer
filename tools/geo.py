# -*- coding: utf-8 -*-
"""座標変換ユーティリティ（外部ライブラリ不要）

扱う座標系
  * 平面直角座標系 I〜XIX 系
      - EPSG:6669-6687 (JGD2011) … 本業務のオルソ／DSM／点群は XI 系 = EPSG:6679
      - EPSG:2443-2461 (JGD2000) … 北海道オープンデータの林班・小班shp（XI系 = 2459）
      北海道では JGD2000 と JGD2011 の差は数ミリ〜数センチで、投影パラメータは同一。
  * UTM 51N〜56N … 衛星画像でよく使われる（北海道は 54N が中心）
  * 緯度経度 … EPSG:6668(JGD2011地理座標)。WGS84との差は数cmのため実用上同一。
  * Web メルカトル EPSG:3857 … 地図タイル用。

投影計算は Krüger 級数（6次）。ゾーン内での誤差はミリメートル級で、
本データの絶対位置誤差（GPS単独測位で水平±3〜5m）に比べて無視できる。

  py -3 tools/geo.py     引き継ぎ資料の実測値との突き合わせが走る
"""
from __future__ import annotations
import math

try:
    import numpy as _np
except ImportError:      # numpy が無くてもスカラー計算はできるようにする
    _np = None

# --- GRS80 / WGS84 ---
A_GRS80 = 6378137.0
F_GRS80 = 1.0 / 298.257222101
A_WGS84 = 6378137.0
F_WGS84 = 1.0 / 298.257223563

# --- 平面直角座標系 XI 系（本業務の既定）---
LAT0 = 44.0
LON0 = 140.25
K0 = 0.9999


class TransverseMercator:
    """横メルカトル図法。平面直角座標系もUTMもこれで表せる。

    lat0, lon0 : 原点の緯度・経度[度]
    k0         : 縮尺係数（平面直角=0.9999 / UTM=0.9996）
    fe, fn     : 加える定数[m]（平面直角=0,0 / UTM=500000,0）
    """

    def __init__(self, lat0, lon0, k0, fe=0.0, fn=0.0, a=A_GRS80, f=F_GRS80, name=''):
        self.lat0, self.lon0, self.k0 = lat0, lon0, k0
        self.fe, self.fn = fe, fn
        self.a, self.f = a, f
        self.name = name

        n = f / (2.0 - f)
        n2, n3, n4, n5 = n * n, n ** 3, n ** 4, n ** 5
        self.n = n
        self.A = (a / (1.0 + n)) * (1.0 + n2 / 4.0 + n4 / 64.0)
        self.alpha = [
            n / 2 - 2 * n2 / 3 + 5 * n3 / 16 + 41 * n4 / 180 - 127 * n5 / 288,
            13 * n2 / 48 - 3 * n3 / 5 + 557 * n4 / 1440 + 281 * n5 / 630,
            61 * n3 / 240 - 103 * n4 / 140 + 15061 * n5 / 26880,
            49561 * n4 / 161280 - 179 * n5 / 168,
            34729 * n5 / 80640,
        ]
        self.beta = [
            n / 2 - 2 * n2 / 3 + 37 * n3 / 96 - n4 / 360 - 81 * n5 / 512,
            n2 / 48 + n3 / 15 - 437 * n4 / 1440 + 46 * n5 / 105,
            17 * n3 / 480 - 37 * n4 / 840 - 209 * n5 / 4480,
            4397 * n4 / 161280 - 11 * n5 / 504,
            4583 * n5 / 161280,
        ]
        self.delta = [
            2 * n - 2 * n2 / 3 - 2 * n3 + 116 * n4 / 45 + 26 * n5 / 45,
            7 * n2 / 3 - 8 * n3 / 5 - 227 * n4 / 45 + 2704 * n5 / 315,
            56 * n3 / 15 - 136 * n4 / 35 - 1262 * n5 / 105,
            4279 * n4 / 630 - 332 * n5 / 35,
            4174 * n5 / 315,
        ]
        self.e2n = 2.0 * math.sqrt(n) / (1.0 + n)
        self.S0 = k0 * self.A * self._xi_at(math.radians(lat0))

    def _xi_at(self, lat_rad):
        s = math.sin(lat_rad)
        t = math.sinh(math.atanh(s) - self.e2n * math.atanh(self.e2n * s))
        xi = math.atan(t)
        return xi + sum(a * math.sin(2 * (j + 1) * xi) for j, a in enumerate(self.alpha))

    # ---------- 緯度経度 -> 平面 ----------
    def from_lonlat(self, lon, lat):
        if _np is not None and (isinstance(lon, _np.ndarray) or isinstance(lat, _np.ndarray)):
            return self._from_lonlat_np(lon, lat)
        lam = math.radians(lon - self.lon0)
        phi = math.radians(lat)
        s = math.sin(phi)
        t = math.sinh(math.atanh(s) - self.e2n * math.atanh(self.e2n * s))
        xi = math.atan2(t, math.cos(lam))
        eta = math.atanh(math.sin(lam) / math.sqrt(1.0 + t * t))
        x, y = eta, xi
        for j, a in enumerate(self.alpha, start=1):
            x += a * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
            y += a * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        return self.k0 * self.A * x + self.fe, self.k0 * self.A * y - self.S0 + self.fn

    def _from_lonlat_np(self, lon, lat):
        np = _np
        lam = np.radians(np.asarray(lon, dtype=float) - self.lon0)
        phi = np.radians(np.asarray(lat, dtype=float))
        s = np.sin(phi)
        t = np.sinh(np.arctanh(np.clip(s, -1 + 1e-15, 1 - 1e-15))
                    - self.e2n * np.arctanh(np.clip(self.e2n * s, -1 + 1e-15, 1 - 1e-15)))
        xi = np.arctan2(t, np.cos(lam))
        eta = np.arctanh(np.clip(np.sin(lam) / np.sqrt(1.0 + t * t), -1 + 1e-15, 1 - 1e-15))
        x, y = eta.copy(), xi.copy()
        for j, a in enumerate(self.alpha, start=1):
            x += a * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
            y += a * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
        return self.k0 * self.A * x + self.fe, self.k0 * self.A * y - self.S0 + self.fn

    # ---------- 平面 -> 緯度経度 ----------
    def to_lonlat(self, x, y):
        if _np is not None and (isinstance(x, _np.ndarray) or isinstance(y, _np.ndarray)):
            return self._to_lonlat_np(x, y)
        xi = (y - self.fn + self.S0) / (self.k0 * self.A)
        eta = (x - self.fe) / (self.k0 * self.A)
        xi_p, eta_p = xi, eta
        for j, b in enumerate(self.beta, start=1):
            xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
            eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
        chi = math.asin(max(-1.0, min(1.0, math.sin(xi_p) / math.cosh(eta_p))))
        lat = chi + sum(d * math.sin(2 * (j + 1) * chi) for j, d in enumerate(self.delta))
        lon = math.radians(self.lon0) + math.atan2(math.sinh(eta_p), math.cos(xi_p))
        return math.degrees(lon), math.degrees(lat)

    def _to_lonlat_np(self, x, y):
        np = _np
        xi = (np.asarray(y, dtype=float) - self.fn + self.S0) / (self.k0 * self.A)
        eta = (np.asarray(x, dtype=float) - self.fe) / (self.k0 * self.A)
        xi_p = np.array(xi, dtype=float, copy=True)
        eta_p = np.array(eta, dtype=float, copy=True)
        for j, b in enumerate(self.beta, start=1):
            xi_p -= b * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
            eta_p -= b * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
        chi = np.arcsin(np.clip(np.sin(xi_p) / np.cosh(eta_p), -1.0, 1.0))
        lat = chi.copy()
        for j, d in enumerate(self.delta, start=1):
            lat += d * np.sin(2 * j * chi)
        lon = math.radians(self.lon0) + np.arctan2(np.sinh(eta_p), np.cos(xi_p))
        return np.degrees(lon), np.degrees(lat)


# ---------- 平面直角座標系（19系）----------
# (系番号): (原点緯度, 原点経度)  すべて k0=0.9999, 加算定数0
JP_ZONES = {
    1: (33.0, 129.5), 2: (33.0, 131.0), 3: (36.0, 132.1666666666667),
    4: (33.0, 133.5), 5: (36.0, 134.3333333333333), 6: (36.0, 136.0),
    7: (36.0, 137.1666666666667), 8: (36.0, 138.5), 9: (36.0, 139.8333333333333),
    10: (40.0, 140.8333333333333), 11: (44.0, 140.25), 12: (44.0, 142.25),
    13: (44.0, 144.25), 14: (26.0, 142.0), 15: (26.0, 127.5),
    16: (26.0, 124.0), 17: (26.0, 131.0), 18: (20.0, 136.0), 19: (26.0, 154.0),
}


def jp_plane(zone):
    lat0, lon0 = JP_ZONES[zone]
    return TransverseMercator(lat0, lon0, 0.9999, 0.0, 0.0,
                              name='平面直角座標系%d系' % zone)


def utm(zone, south=False, wgs84=True):
    a, f = (A_WGS84, F_WGS84) if wgs84 else (A_GRS80, F_GRS80)
    return TransverseMercator(0.0, zone * 6 - 183, 0.9996, 500000.0,
                              10000000.0 if south else 0.0, a=a, f=f,
                              name='UTM %d%s' % (zone, 'S' if south else 'N'))


# 本業務の既定（XI系）
XI = jp_plane(11)


# ---------- 既定系のモジュール関数（従来の呼び出しをそのまま残す）----------
def lonlat_to_xy(lon, lat):
    """緯度経度 -> 平面直角座標XI系 (x=東距[m], y=北距[m])"""
    return XI.from_lonlat(lon, lat)


def lonlat_to_xy_np(lon, lat):
    return XI._from_lonlat_np(lon, lat)


def xy_to_lonlat(x, y):
    """平面直角座標XI系 -> (lon, lat)。スカラー / numpy配列 両対応。"""
    return XI.to_lonlat(x, y)


# ---------- Web メルカトル ----------
R_MERC = 6378137.0


def lonlat_to_merc(lon, lat):
    if _np is not None and isinstance(lat, _np.ndarray):
        np = _np
        return (np.radians(lon) * R_MERC,
                R_MERC * np.log(np.tan(np.pi / 4.0 + np.radians(lat) / 2.0)))
    return (math.radians(lon) * R_MERC,
            R_MERC * math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)))


def merc_to_lonlat(mx, my):
    if _np is not None and (isinstance(mx, _np.ndarray) or isinstance(my, _np.ndarray)):
        np = _np
        return (np.degrees(mx / R_MERC),
                np.degrees(2.0 * np.arctan(np.exp(my / R_MERC)) - np.pi / 2.0))
    return (math.degrees(mx / R_MERC),
            math.degrees(2.0 * math.atan(math.exp(my / R_MERC)) - math.pi / 2.0))


def tile_bounds_merc(z, tx, ty):
    """XYZ タイル (Googleスキーム: y は北が0) のメルカトル座標範囲"""
    span = 2.0 * math.pi * R_MERC
    size = span / (2 ** z)
    x0 = -span / 2.0 + tx * size
    y1 = span / 2.0 - ty * size
    return x0, y1 - size, x0 + size, y1


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    tx = int((lon + 180.0) / 360.0 * n)
    lr = math.radians(lat)
    ty = int((1.0 - math.log(math.tan(lr) + 1.0 / math.cos(lr)) / math.pi) / 2.0 * n)
    return tx, ty


def merc_resolution(z):
    """タイル1画素あたりのメルカトル座標長さ [m]"""
    return 2.0 * math.pi * R_MERC / (256.0 * 2 ** z)


# ---------- 距離 ----------
def haversine_m(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


if __name__ == '__main__':
    # 引き継ぎ資料に記載された実測値との突き合わせ
    cases = [
        ('SiteA min', 15040.0, -204967.5, 140.431991, 42.154684),
        ('SiteA max', 15725.6, -204534.0, 140.440298, 42.158573),
        ('SiteB min', 36543.7, -210016.2, 140.691874, 42.108520),
        ('SiteB max', 37628.0, -209013.7, 140.705049, 42.117495),
        ('SiteC min', 34831.9, -220063.2, 140.670579, 42.018138),
        ('SiteC max', 35869.1, -219192.5, 140.683155, 42.025932),
    ]
    print('■ 平面直角座標系XI系（EPSG:6679）')
    worst = 0.0
    for name, x, y, elon, elat in cases:
        lon, lat = xy_to_lonlat(x, y)
        d = haversine_m(lon, lat, elon, elat)
        worst = max(worst, d)
        print('  %-10s %12.6f %11.6f  ずれ %.3f m' % (name, lon, lat, d))
        bx, by = lonlat_to_xy(lon, lat)
        assert abs(bx - x) < 1e-3 and abs(by - y) < 1e-3, (bx - x, by - y)
    print('  最大ずれ: %.3f m （資料の記載桁数 1e-6 度 ≒ 0.1m 相当）' % worst)

    print('')
    print('■ 往復の検算（各系で 1e-6 m 以内に戻るか）')
    for label, crs, pt in (
        ('平面直角XI系', jp_plane(11), (37167.87, -209542.46)),
        ('平面直角XII系', jp_plane(12), (10000.0, -50000.0)),
        ('UTM 54N', utm(54), (450000.0, 4663000.0)),
        ('UTM 53N', utm(53), (600000.0, 4663000.0)),
    ):
        lon, lat = crs.to_lonlat(*pt)
        bx, by = crs.from_lonlat(lon, lat)
        err = math.hypot(bx - pt[0], by - pt[1])
        print('  %-14s %10.6f, %9.6f  往復誤差 %.2e m' % (label, lon, lat, err))
        assert err < 1e-6, err
    print('  すべて往復一致')
