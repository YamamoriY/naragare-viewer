# -*- coding: utf-8 -*-
"""Shapefile(.shp/.shx/.dbf) と xlsx を標準ライブラリだけで読む。

GDAL / pyshp / openpyxl / pandas を一切必要としない。
北海道オープンデータの林班・小班shp（ポリゴン, Shift_JIS属性）と
森林調査簿xlsxを読むのに必要な範囲だけを実装している。
"""
from __future__ import annotations
import struct, os, zipfile
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------- DBF
class DBF:
    """dBASE III+ 形式の属性テーブル。文字コードは Shift_JIS(cp932) 固定。"""

    def __init__(self, path, encoding='cp932'):
        self.path = path
        self.encoding = encoding
        self.f = open(path, 'rb')
        head = self.f.read(32)
        (self.version, yy, mm, dd, self.nrec,
         self.header_len, self.rec_len) = struct.unpack('<BBBBIHH', head[:12])
        self.fields = []          # (name, type, length, decimals)
        self.f.seek(32)
        while True:
            d = self.f.read(32)
            if not d or len(d) < 32 or d[0] == 0x0d:
                break
            name = d[:11].split(b'\x00')[0].decode(encoding, 'replace').strip()
            self.fields.append((name, chr(d[11]), d[16], d[17]))
        self.names = [f[0] for f in self.fields]

    def record(self, i):
        self.f.seek(self.header_len + i * self.rec_len)
        raw = self.f.read(self.rec_len)
        if len(raw) < self.rec_len:
            return None
        out, off = {}, 1                      # 先頭1バイトは削除フラグ
        for name, typ, ln, dec in self.fields:
            v = raw[off:off + ln].decode(self.encoding, 'replace').strip()
            off += ln
            if typ in 'NF' and v:
                try:
                    v = float(v) if ('.' in v or dec) else int(v)
                except ValueError:
                    pass
            out[name] = v
        return out

    def records(self):
        for i in range(self.nrec):
            r = self.record(i)
            if r is not None:
                yield i, r

    def close(self):
        self.f.close()


# ---------------------------------------------------------------- SHP
class SHP:
    """ポリゴン(type 5)/ポイント(1)/ポリライン(3) を読む。

    ファイル全体をメモリに載せる。渡島の小班shpで36MB程度なので問題ない。
    """

    NULL, POINT, POLYLINE, POLYGON = 0, 1, 3, 5

    def __init__(self, path):
        self.path = path
        self.buf = open(path, 'rb').read()
        if struct.unpack('>I', self.buf[:4])[0] != 9994:
            raise ValueError('shapefile ではありません: %s' % path)
        self.shape_type = struct.unpack('<I', self.buf[32:36])[0]
        self.bbox = struct.unpack('<4d', self.buf[36:68])
        self._index = []          # (body_offset, content_len_bytes)
        pos, n = 100, len(self.buf)
        while pos + 8 <= n:
            num, ln = struct.unpack('>II', self.buf[pos:pos + 8])
            self._index.append((pos + 8, ln * 2))
            pos += 8 + ln * 2
        self.count = len(self._index)

    def shape_bbox(self, i):
        off, _ = self._index[i]
        st = struct.unpack('<I', self.buf[off:off + 4])[0]
        if st == self.NULL:
            return None
        if st == self.POINT:
            x, y = struct.unpack('<2d', self.buf[off + 4:off + 20])
            return (x, y, x, y)
        return struct.unpack('<4d', self.buf[off + 4:off + 36])

    def rings(self, i):
        """ポリゴン/ポリラインの環（リング）を [[(x,y),...], ...] で返す。"""
        off, _ = self._index[i]
        st = struct.unpack('<I', self.buf[off:off + 4])[0]
        if st == self.NULL:
            return []
        if st == self.POINT:
            return [[struct.unpack('<2d', self.buf[off + 4:off + 20])]]
        p = off + 36
        nparts, npoints = struct.unpack('<II', self.buf[p:p + 8])
        p += 8
        parts = struct.unpack('<%dI' % nparts, self.buf[p:p + 4 * nparts])
        p += 4 * nparts
        xy = struct.unpack('<%dd' % (2 * npoints), self.buf[p:p + 16 * npoints])
        out = []
        for k in range(nparts):
            a = parts[k]
            b = parts[k + 1] if k + 1 < nparts else npoints
            out.append([(xy[2 * j], xy[2 * j + 1]) for j in range(a, b)])
        return out


# ------------------------------------------------------- 幾何ユーティリティ
def ring_area(ring):
    """符号付き面積。shapefile では外環が時計回り(負)、穴が反時計回り(正)。"""
    s = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def point_in_polygon(x, y, rings):
    """shapefile のポリゴン（外環＋穴）に対する内外判定。"""
    inside = False
    for r in rings:
        if len(r) < 4:
            continue
        if point_in_ring(x, y, r):
            # 外環なら入る、穴なら抜ける。奇偶で判定すれば穴も自然に処理できる。
            inside = not inside
    return inside


def bbox_of(rings):
    x0 = y0 = 1e30
    x1 = y1 = -1e30
    for r in rings:
        for x, y in r:
            if x < x0: x0 = x
            if x > x1: x1 = x
            if y < y0: y0 = y
            if y > y1: y1 = y
    return (x0, y0, x1, y1)


def bbox_intersects(a, b, margin=0.0):
    return not (a[0] > b[2] + margin or a[2] < b[0] - margin or
                a[1] > b[3] + margin or a[3] < b[1] - margin)


def simplify(points, tol):
    """Douglas-Peucker。GeoJSON を軽くするために使う。"""
    if len(points) < 3 or tol <= 0:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            if den == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / den
                t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best:
                best, bi = d, i
        if best > tol * tol:
            keep[bi] = True
            stack.append((a, bi))
            stack.append((bi, b))
    return [p for p, k in zip(points, keep) if k]


# ---------------------------------------------------------------- XLSX
_XNS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def _col_index(ref):
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return n - 1


def xlsx_sheet_names(path):
    import re
    z = zipfile.ZipFile(path)
    wb = z.read('xl/workbook.xml').decode('utf-8')
    return re.findall(r'<sheet[^>]*name="([^"]+)"', wb)


def xlsx_rows(path, sheet=1):
    """xlsx の1シートを行のリストとして順に返す（列位置を正しく解釈する）。"""
    z = zipfile.ZipFile(path)
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        with z.open('xl/sharedStrings.xml') as f:
            for ev, el in ET.iterparse(f, events=('end',)):
                if el.tag == _XNS + 'si':
                    shared.append(''.join(t.text or '' for t in el.iter(_XNS + 't')))
                    el.clear()
    name = 'xl/worksheets/sheet%d.xml' % sheet
    with z.open(name) as f:
        for ev, el in ET.iterparse(f, events=('end',)):
            if el.tag != _XNS + 'row':
                continue
            cells = {}
            for c in el.findall(_XNS + 'c'):
                v = c.find(_XNS + 'v')
                txt = v.text if v is not None else None
                if txt is None:
                    ise = c.find(_XNS + 'is')
                    if ise is not None:
                        txt = ''.join(t.text or '' for t in ise.iter(_XNS + 't'))
                if txt is None:
                    continue
                if c.get('t') == 's':
                    txt = shared[int(txt)]
                cells[_col_index(c.get('r') or 'A')] = txt
            el.clear()
            if cells:
                yield [cells.get(i, '') for i in range(max(cells) + 1)]


def unzip_cp932(zip_path, dest):
    """日本語ファイル名(Shift_JIS)のzipを正しく展開する。"""
    os.makedirs(dest, exist_ok=True)
    out = []
    try:
        z = zipfile.ZipFile(zip_path, metadata_encoding='cp932')
    except TypeError:            # Python 3.10 以前
        z = zipfile.ZipFile(zip_path)
    for info in z.infolist():
        if info.is_dir():
            continue
        target = os.path.join(dest, os.path.basename(info.filename))
        with open(target, 'wb') as fh:
            fh.write(z.read(info))
        out.append(target)
    return out
