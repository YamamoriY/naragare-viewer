# -*- coding: utf-8 -*-
"""画像の座標系を自動で見分ける。

ドローンのオルソは平面直角座標系XI系（EPSG:6679）で揃っているが、
衛星画像はそうとは限らない。よくあるのは

  * UTM 54N（北海道の衛星画像でいちばん多い）
  * 緯度経度（EPSG:4326 / 6668）
  * Webメルカトル（EPSG:3857）
  * 平面直角座標系のどれか

判定の材料は次の順に見る。
  1. GeoTIFF の GeoKey（タグ 34735）に入っている EPSG コード
  2. 同じ名前の .prj ファイル（WKT）
  3. ワールドファイルの数値から推定（最後の手段）

GDAL も pyproj も使わない。
"""
from __future__ import annotations
import os, re, struct, math

import geo


class Geographic:
    """緯度経度そのもの。変換は不要。"""
    name = '緯度経度'

    def __init__(self, name='緯度経度'):
        self.name = name

    def to_lonlat(self, x, y):
        return x, y

    def from_lonlat(self, lon, lat):
        return lon, lat


class WebMercator:
    name = 'Webメルカトル'

    def to_lonlat(self, x, y):
        return geo.merc_to_lonlat(x, y)

    def from_lonlat(self, lon, lat):
        return geo.lonlat_to_merc(lon, lat)


# ---------------------------------------------------------------- EPSG
def from_epsg(code):
    """EPSG コードから座標系を作る。分からなければ None。"""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return None

    # 地理座標
    if code in (4326, 4612, 6668, 4019, 4030):
        return Geographic('緯度経度 (EPSG:%d)' % code)
    if code == 3857 or code == 900913 or code == 3785:
        return WebMercator()

    # 平面直角座標系
    #   JGD2011 : 6669..6687 = I..XIX
    #   JGD2000 : 2443..2461 = I..XIX
    #   Tokyo   : 30161..30179 = I..XIX
    for base, label in ((6669, 'JGD2011'), (2443, 'JGD2000'), (30161, 'Tokyo')):
        if base <= code <= base + 18:
            zone = code - base + 1
            c = geo.jp_plane(zone)
            c.name = '平面直角座標系%d系 (EPSG:%d / %s)' % (zone, code, label)
            if label == 'Tokyo':
                c.name += ' ※旧日本測地系。数百mずれます'
            return c

    # UTM
    if 32601 <= code <= 32660:                       # WGS84 北半球
        return geo.utm(code - 32600, south=False, wgs84=True)
    if 32701 <= code <= 32760:                       # WGS84 南半球
        return geo.utm(code - 32700, south=True, wgs84=True)
    if 6688 <= code <= 6695:                         # JGD2011 UTM 51N..58N
        return geo.utm(code - 6688 + 51, south=False, wgs84=False)
    if 3097 <= code <= 3101:                         # JGD2000 UTM 51N..55N
        return geo.utm(code - 3097 + 51, south=False, wgs84=False)
    return None


# ---------------------------------------------------------------- WKT
def from_wkt(wkt):
    """.prj の WKT から座標系を作る。"""
    if not wkt:
        return None
    t = wkt.strip()

    # 末尾の AUTHORITY["EPSG","xxxx"] があればそれを信じる
    auth = re.findall(r'AUTHORITY\s*\[\s*"EPSG"\s*,\s*"(\d+)"\s*\]', t, re.I)
    if auth:
        c = from_epsg(auth[-1])
        if c:
            return c

    if re.match(r'^\s*GEOGCS', t, re.I):
        return Geographic('緯度経度（WKT）')

    if not re.search(r'PROJCS', t, re.I):
        return None

    def param(name):
        m = re.search(r'PARAMETER\s*\[\s*"%s"\s*,\s*(-?[\d.eE+]+)\s*\]' % name, t, re.I)
        return float(m.group(1)) if m else None

    proj = re.search(r'PROJECTION\s*\[\s*"([^"]+)"', t, re.I)
    proj = (proj.group(1) if proj else '').lower().replace('_', '')

    if 'mercator' in proj and 'transverse' not in proj:
        return WebMercator()
    if 'transversemercator' not in proj:
        return None

    lat0 = param('latitude_of_origin')
    if lat0 is None:
        lat0 = param('Latitude_Of_Origin') or 0.0
    lon0 = param('central_meridian')
    k0 = param('scale_factor') or 0.9999
    fe = param('false_easting') or 0.0
    fn = param('false_northing') or 0.0
    if lon0 is None:
        return None

    # 楕円体（GRS80 と WGS84 は実用上ほぼ同じだが一応見る）
    a = geo.A_GRS80
    f = geo.F_GRS80
    sph = re.search(r'SPHEROID\s*\[\s*"([^"]+)"\s*,\s*([\d.]+)\s*,\s*([\d.]+)', t, re.I)
    if sph:
        a = float(sph.group(2))
        inv = float(sph.group(3))
        f = 1.0 / inv if inv else 0.0

    name = re.search(r'PROJCS\s*\[\s*"([^"]+)"', t, re.I)
    c = geo.TransverseMercator(lat0, lon0, k0, fe, fn, a=a, f=f,
                               name=(name.group(1) if name else '横メルカトル（WKT）'))
    return c


# ---------------------------------------------------------------- GeoTIFF
def _read_tiff_tags(path, wanted):
    """TIFF の最初の IFD から指定タグを読む。{tag: (type, values)}"""
    out = {}
    with open(path, 'rb') as f:
        head = f.read(8)
        if head[:2] == b'II':
            en = '<'
        elif head[:2] == b'MM':
            en = '>'
        else:
            return out
        magic = struct.unpack(en + 'H', head[2:4])[0]
        if magic == 42:
            off = struct.unpack(en + 'I', head[4:8])[0]
            entry, big = 12, False
        elif magic == 43:
            f.seek(8)
            off = struct.unpack(en + 'Q', f.read(8))[0]
            entry, big = 20, True
        else:
            return out
        f.seek(off)
        n = struct.unpack(en + ('Q' if big else 'H'), f.read(8 if big else 2))[0]
        TYPE = {1: ('B', 1), 2: ('c', 1), 3: ('H', 2), 4: ('I', 4),
                5: ('II', 8), 11: ('f', 4), 12: ('d', 8), 16: ('Q', 8)}
        for _ in range(n):
            raw = f.read(entry)
            if len(raw) < entry:
                break
            tag, typ = struct.unpack(en + 'HH', raw[:4])
            if tag not in wanted:
                continue
            if big:
                count, voff = struct.unpack(en + 'QQ', raw[4:20])
            else:
                count, voff = struct.unpack(en + 'II', raw[4:12])
            if typ not in TYPE:
                continue
            code, size = TYPE[typ]
            total = count * size
            cur = f.tell()
            if total <= (8 if big else 4):
                data = raw[(12 if big else 8):(12 if big else 8) + total]
            else:
                f.seek(voff)
                data = f.read(total)
                f.seek(cur)
            if typ == 2:
                out[tag] = (typ, data.split(b'\x00')[0].decode('ascii', 'replace'))
            elif typ == 5:
                vals = struct.unpack(en + '%dI' % (count * 2), data)
                out[tag] = (typ, [vals[i] / (vals[i + 1] or 1) for i in range(0, len(vals), 2)])
            else:
                out[tag] = (typ, list(struct.unpack(en + '%d%s' % (count, code), data)))
            f.seek(cur)
    return out


def from_geotiff(path):
    """GeoTIFF の GeoKey から座標系を読む。"""
    try:
        tags = _read_tiff_tags(path, {34735, 34737})
    except OSError:
        return None
    if 34735 not in tags:
        return None
    keys = tags[34735][1]
    if len(keys) < 4:
        return None
    nkeys = keys[3]
    model = None
    proj_epsg = None
    geog_epsg = None
    for i in range(nkeys):
        b = 4 + i * 4
        if b + 3 >= len(keys):
            break
        kid, loc, cnt, val = keys[b], keys[b + 1], keys[b + 2], keys[b + 3]
        if loc != 0:
            continue                      # 値が別タグにある場合は今は見ない
        if kid == 1024:
            model = val
        elif kid == 3072:
            proj_epsg = val
        elif kid == 2048:
            geog_epsg = val
    if proj_epsg:
        c = from_epsg(proj_epsg)
        if c:
            return c
    if model == 2 or geog_epsg:
        return Geographic('緯度経度 (EPSG:%s)' % (geog_epsg or '4326'))
    return None


# ---------------------------------------------------------------- 総合
def detect(path, verbose=True):
    """画像の座標系を見分ける。分からなければ None。"""
    c = None
    src = ''
    if path.lower().endswith(('.tif', '.tiff')):
        c = from_geotiff(path)
        if c:
            src = 'GeoTIFFのGeoKey'
    if c is None:
        prj = os.path.splitext(path)[0] + '.prj'
        if os.path.exists(prj):
            try:
                c = from_wkt(open(prj, encoding='utf-8', errors='replace').read())
                if c:
                    src = '.prjファイル'
            except OSError:
                pass
    if c is not None and verbose:
        print('   座標系: %s（%s から判定）' % (getattr(c, 'name', '?'), src))
    return c


def guess_from_bounds(x0, y0, x1, y1):
    """座標系の情報が無いとき、数値の範囲から見当をつける。"""
    if -180 <= x0 <= 180 and -90 <= y0 <= 90 and abs(x1 - x0) < 10:
        return Geographic('緯度経度（数値の範囲から推定）')
    if abs(x0) < 1e6 and abs(y0) < 1e6:
        return None            # 平面直角座標系のどれか。系までは決められない
    if 1e5 <= x0 <= 1e6 and 1e6 <= y0 <= 1e7:
        return None            # UTM らしいが帯までは決められない
    return None
