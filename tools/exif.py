# -*- coding: utf-8 -*-
"""JPEG の EXIF から撮影位置と撮影日時を読む（標準ライブラリのみ）。

現地で撮ったスマホ写真をまとめて放り込むと、写真が自分で持っている
GPS情報から「どの木の写真か」を割り出せる。事務所での入力作業が要らなくなる。

Pillow を使わないのは、ビューアー本体を標準ライブラリだけで動かすため。

JPEG の構造:
  FFD8 (SOI) のあと、FFEx のマーカーが並ぶ。
  APP1 (FFE1) の中身が "Exif\\0\\0" で始まっていれば、その後ろは TIFF 構造。
  TIFF の IFD0 に GPS IFD へのポインタ(34853)があり、その中に緯度経度が入っている。
"""
from __future__ import annotations
import struct

# TIFF のデータ型 -> (struct書式, バイト数)
_TYPE = {1: ('B', 1), 2: ('c', 1), 3: ('H', 2), 4: ('I', 4), 5: ('II', 8),
         7: ('B', 1), 9: ('i', 4), 10: ('ii', 8), 11: ('f', 4), 12: ('d', 8)}


def _find_exif(data):
    """JPEG から EXIF の TIFF ブロックを取り出す。"""
    if len(data) < 4 or data[0:2] != b'\xff\xd8':
        return None
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xDA:            # 画像データに入ったら終わり
            break
        seglen = struct.unpack('>H', data[i + 2:i + 4])[0]
        if marker == 0xE1 and data[i + 4:i + 10] == b'Exif\x00\x00':
            return data[i + 10:i + 2 + seglen]
        i += 2 + seglen
    return None


def _read_ifd(buf, off, en):
    """IFD を {tag: 値} で返す。"""
    out = {}
    if off + 2 > len(buf):
        return out
    count = struct.unpack(en + 'H', buf[off:off + 2])[0]
    p = off + 2
    for _ in range(count):
        if p + 12 > len(buf):
            break
        tag, typ, cnt = struct.unpack(en + 'HHI', buf[p:p + 8])
        if typ not in _TYPE:
            p += 12
            continue
        code, size = _TYPE[typ]
        total = cnt * size
        if total <= 4:
            raw = buf[p + 8:p + 8 + total]
        else:
            voff = struct.unpack(en + 'I', buf[p + 8:p + 12])[0]
            raw = buf[voff:voff + total]
        try:
            if typ == 2:
                out[tag] = raw.split(b'\x00')[0].decode('ascii', 'replace')
            elif typ in (5, 10):
                vals = struct.unpack(en + ('%d' % (cnt * 2)) + code[0], raw)
                out[tag] = [(vals[k], vals[k + 1]) for k in range(0, len(vals), 2)]
            else:
                out[tag] = list(struct.unpack(en + '%d%s' % (cnt, code), raw))
        except (struct.error, UnicodeDecodeError):
            pass
        p += 12
    return out


def _ratio(v):
    try:
        num, den = v
        return num / den if den else 0.0
    except (TypeError, ValueError):
        return 0.0


def _dms(vals, ref):
    if not vals or len(vals) < 3:
        return None
    d = _ratio(vals[0]) + _ratio(vals[1]) / 60.0 + _ratio(vals[2]) / 3600.0
    if ref and str(ref).upper().startswith(('S', 'W')):
        d = -d
    return d


def read(data):
    """JPEGのバイト列から {'lat','lon','alt','taken','make','model'} を返す。

    位置情報が無ければ lat/lon は None。
    """
    out = {'lat': None, 'lon': None, 'alt': None, 'taken': None,
           'make': None, 'model': None}
    tiff = _find_exif(data)
    if not tiff or len(tiff) < 8:
        return out
    if tiff[0:2] == b'II':
        en = '<'
    elif tiff[0:2] == b'MM':
        en = '>'
    else:
        return out
    try:
        ifd0_off = struct.unpack(en + 'I', tiff[4:8])[0]
        ifd0 = _read_ifd(tiff, ifd0_off, en)
    except struct.error:
        return out

    if 271 in ifd0:
        out['make'] = str(ifd0[271]).strip()
    if 272 in ifd0:
        out['model'] = str(ifd0[272]).strip()

    # 撮影日時（ExifIFD の DateTimeOriginal を優先）
    taken = None
    if 34665 in ifd0:
        try:
            ex = _read_ifd(tiff, int(ifd0[34665][0]), en)
            taken = ex.get(36867) or ex.get(36868)
        except (struct.error, TypeError, IndexError):
            pass
    taken = taken or ifd0.get(306)
    if taken:
        t = str(taken).strip()
        if len(t) >= 19 and t[4] == ':' and t[7] == ':':
            out['taken'] = '%s-%s-%s %s' % (t[0:4], t[5:7], t[8:10], t[11:19])
        else:
            out['taken'] = t

    # GPS
    if 34853 not in ifd0:
        return out
    try:
        gps = _read_ifd(tiff, int(ifd0[34853][0]), en)
    except (struct.error, TypeError, IndexError):
        return out

    lat = _dms(gps.get(2), gps.get(1))
    lon = _dms(gps.get(4), gps.get(3))
    if lat is not None and lon is not None and (lat or lon):
        out['lat'] = round(lat, 8)
        out['lon'] = round(lon, 8)
    if 6 in gps:
        try:
            a = _ratio(gps[6][0])
            if gps.get(5) and gps[5][0] == 1:
                a = -a
            out['alt'] = round(a, 1)
        except (TypeError, IndexError):
            pass
    return out


def read_file(path):
    with open(path, 'rb') as f:
        return read(f.read(256 * 1024))     # EXIF は先頭にあるので全部は読まない


if __name__ == '__main__':
    import sys, io, json
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    for p in sys.argv[1:]:
        print(p)
        print('  ', json.dumps(read_file(p), ensure_ascii=False))
