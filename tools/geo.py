# -*- coding: utf-8 -*-
"""座標変換ユーティリティ（外部ライブラリ不要）

扱う座標系
  * 平面直角座標系 XI(11)系
      - EPSG:6679 (JGD2011) … 本業務のオルソ／DSM／点群
      - EPSG:2459 (JGD2000) … 北海道オープンデータの林班・小班shp
      北海道では JGD2000 と JGD2011 の差は数ミリ〜数センチで、投影パラメータは同一
      （lat_0=44, lon_0=140.25, k=0.9999, GRS80）。本ツールでは同一グリッドとして扱う。
  * 緯度経度 … EPSG:6668(JGD2011地理座標)。WGS84との差は数cmのため実用上同一。
  * Web メルカトル EPSG:3857 … 地図タイル用。

投影計算は Krüger 級数（6次）。ゾーン内での誤差はミリメートル級で、
本データの絶対位置誤差（GPS単独測位で水平±3〜5m）に比べて無視できる。
"""
from __future__ import annotations
import math

try:
    import numpy as _np
except ImportError:  # numpy が無くてもスカラー計算はできるようにする
    _np = None

# --- GRS80 ---
A_GRS80 = 6378137.0
F_GRS80 = 1.0 / 298.257222101

# --- 平面直角座標系 XI 系 ---
LAT0 = 44.0
LON0 = 140.25
K0 = 0.9999


def _series():
    n = F_GRS80 / (2.0 - F_GRS80)
    n2, n3, n4, n5 = n * n, n ** 3, n ** 4, n ** 5
    A = (A_GRS80 / (1.0 + n)) * (1.0 + n2 / 4.0 + n4 / 64.0)
    alpha = [
        n / 2.0 - 2.0 * n2 / 3.0 + 5.0 * n3 / 16.0 + 41.0 * n4 / 180.0 - 127.0 * n5 / 288.0,
        13.0 * n2 / 48.0 - 3.0 * n3 / 5.0 + 557.0 * n4 / 1440.0 + 281.0 * n5 / 630.0,
        61.0 * n3 / 240.0 - 103.0 * n4 / 140.0 + 15061.0 * n5 / 26880.0,
        49561.0 * n4 / 161280.0 - 179.0 * n5 / 168.0,
        34729.0 * n5 / 80640.0,
    ]
    beta = [
        n / 2.0 - 2.0 * n2 / 3.0 + 37.0 * n3 / 96.0 - n4 / 360.0 - 81.0 * n5 / 512.0,
        n2 / 48.0 + n3 / 15.0 - 437.0 * n4 / 1440.0 + 46.0 * n5 / 105.0,
        17.0 * n3 / 480.0 - 37.0 * n4 / 840.0 - 209.0 * n5 / 4480.0,
        4397.0 * n4 / 161280.0 - 11.0 * n5 / 504.0,
        4583.0 * n5 / 161280.0,
    ]
    delta = [
        2.0 * n - 2.0 * n2 / 3.0 - 2.0 * n3 + 116.0 * n4 / 45.0 + 26.0 * n5 / 45.0,
        7.0 * n2 / 3.0 - 8.0 * n3 / 5.0 - 227.0 * n4 / 45.0 + 2704.0 * n5 / 315.0,
        56.0 * n3 / 15.0 - 136.0 * n4 / 35.0 - 1262.0 * n5 / 105.0,
        4279.0 * n4 / 630.0 - 332.0 * n5 / 35.0,
        4174.0 * n5 / 315.0,
    ]
    return n, A, alpha, beta, delta


_N, _A, _ALPHA, _BETA, _DELTA = _series()
_E2N = 2.0 * math.sqrt(_N) / (1.0 + _N)


def _xi_at(lat_rad):
    """Δλ=0 における ξ'（子午線弧長パラメータ）"""
    s = math.sin(lat_rad)
    t = math.sinh(math.atanh(s) - _E2N * math.atanh(_E2N * s))
    xi = math.atan(t)
    return xi + sum(a * math.sin(2 * (j + 1) * xi) for j, a in enumerate(_ALPHA))


_S0 = K0 * _A * _xi_at(math.radians(LAT0))


def lonlat_to_xy(lon, lat):
    """緯度経度 -> 平面直角座標XI系 (x=東距[m], y=北距[m])"""
    lam = math.radians(lon - LON0)
    phi = math.radians(lat)
    s = math.sin(phi)
    t = math.sinh(math.atanh(s) - _E2N * math.atanh(_E2N * s))
    xi = math.atan2(t, math.cos(lam))
    eta = math.atanh(math.sin(lam) / math.sqrt(1.0 + t * t))
    x = eta
    y = xi
    for j, a in enumerate(_ALPHA, start=1):
        x += a * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
        y += a * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
    return K0 * _A * x, K0 * _A * y - _S0


def lonlat_to_xy_np(lon, lat):
    """lonlat_to_xy の numpy 版（タイル生成で画素ごとに使う）"""
    np = _np
    lam = np.radians(np.asarray(lon, dtype=float) - LON0)
    phi = np.radians(np.asarray(lat, dtype=float))
    s = np.sin(phi)
    t = np.sinh(np.arctanh(np.clip(s, -1 + 1e-15, 1 - 1e-15))
                - _E2N * np.arctanh(np.clip(_E2N * s, -1 + 1e-15, 1 - 1e-15)))
    xi = np.arctan2(t, np.cos(lam))
    eta = np.arctanh(np.clip(np.sin(lam) / np.sqrt(1.0 + t * t), -1 + 1e-15, 1 - 1e-15))
    x = eta.copy()
    y = xi.copy()
    for j, a in enumerate(_ALPHA, start=1):
        x += a * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
        y += a * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
    return K0 * _A * x, K0 * _A * y - _S0


def xy_to_lonlat(x, y):
    """平面直角座標XI系 (x=東距, y=北距) -> (lon, lat)。スカラー / numpy配列 両対応。"""
    if _np is not None and (isinstance(x, _np.ndarray) or isinstance(y, _np.ndarray)):
        return _xy_to_lonlat_np(x, y)
    xi = (y + _S0) / (K0 * _A)
    eta = x / (K0 * _A)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(_BETA, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    chi = math.asin(max(-1.0, min(1.0, math.sin(xi_p) / math.cosh(eta_p))))
    lat = chi + sum(d * math.sin(2 * (j + 1) * chi) for j, d in enumerate(_DELTA))
    lon = math.radians(LON0) + math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(lon), math.degrees(lat)


def _xy_to_lonlat_np(x, y):
    np = _np
    xi = (y + _S0) / (K0 * _A)
    eta = x / (K0 * _A)
    xi_p = np.array(xi, dtype=float, copy=True)
    eta_p = np.array(eta, dtype=float, copy=True)
    for j, b in enumerate(_BETA, start=1):
        xi_p -= b * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
        eta_p -= b * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
    chi = np.arcsin(np.clip(np.sin(xi_p) / np.cosh(eta_p), -1.0, 1.0))
    lat = chi.copy()
    for j, d in enumerate(_DELTA, start=1):
        lat += d * np.sin(2 * j * chi)
    lon = math.radians(LON0) + np.arctan2(np.sinh(eta_p), np.cos(xi_p))
    return np.degrees(lon), np.degrees(lat)


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
    print('%-10s %12s %11s %10s %10s' % ('', 'lon', 'lat', 'dlon"', 'dlat"'))
    worst = 0.0
    for name, x, y, elon, elat in cases:
        lon, lat = xy_to_lonlat(x, y)
        d = haversine_m(lon, lat, elon, elat)
        worst = max(worst, d)
        print('%-10s %12.6f %11.6f  ずれ %.3f m' % (name, lon, lat, d))
        bx, by = lonlat_to_xy(lon, lat)
        assert abs(bx - x) < 1e-3 and abs(by - y) < 1e-3, (bx - x, by - y)
    print('最大ずれ: %.3f m （資料の記載桁数 1e-6 度 ≒ 0.1m 相当）' % worst)
