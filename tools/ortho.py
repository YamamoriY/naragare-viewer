# -*- coding: utf-8 -*-
"""オルソ画像の読み込みと XYZ タイル化（GDAL不要・Pillow + numpy のみ）。

対応する入力
  1. GeoTIFF  : ModelPixelScale / ModelTiepoint タグから位置決め。
                4バンド目（アルファ）があれば飛行範囲外の透過に使う。
  2. JPEG/PNG + ワールドファイル(.jgw/.pgw/.wld)
                D:\\ortho_out の <site>_ortho.jpg + .jgw はこの形式。

座標系は平面直角座標系XI系（EPSG:6679 / 2459）固定。出力は Web メルカトル
(EPSG:3857) の XYZ タイル。投影変換はタイルごとに 9x9 の制御格子で厳密計算し、
画素へは双一次補間で展開する（GDAL の近似変換器と同じ考え方。誤差は 0.01 画素未満）。
"""
from __future__ import annotations
import os, math, struct, json

import numpy as np
from PIL import Image

import geo
import crs as crsmod

Image.MAX_IMAGE_PIXELS = None      # 大きなオルソを開くための解除

TILE = 256
CTRL = 8                            # 制御格子の分割数（9x9点）


# ------------------------------------------------------------ GeoTIFF タグ
def _geotiff_transform(path):
    """GeoTIFF から (pixel_size_x, pixel_size_y, origin_x, origin_y) を読む。

    Pillow がタグを読めればそれを使い、駄目なら自前で IFD を走査する。
    """
    try:
        with Image.open(path) as im:
            t = getattr(im, 'tag_v2', None)
            if t:
                scale = t.get(33550)      # ModelPixelScaleTag
                tie = t.get(33922)        # ModelTiepointTag
                if scale and tie and len(tie) >= 6:
                    return float(scale[0]), float(scale[1]), float(tie[3]), float(tie[4])
                trans = t.get(34264)      # ModelTransformationTag
                if trans and len(trans) >= 16:
                    return float(trans[0]), float(-trans[5]), float(trans[3]), float(trans[7])
    except Exception:
        pass
    return _geotiff_transform_raw(path)


def _geotiff_transform_raw(path):
    with open(path, 'rb') as f:
        head = f.read(8)
        if head[:2] == b'II':
            en = '<'
        elif head[:2] == b'MM':
            en = '>'
        else:
            return None
        magic = struct.unpack(en + 'H', head[2:4])[0]
        if magic == 42:
            off = struct.unpack(en + 'I', head[4:8])[0]
            entry, cnt_fmt, off_fmt = 12, en + 'H', en + 'I'
        elif magic == 43:                      # BigTIFF
            f.seek(8)
            off = struct.unpack(en + 'Q', f.read(8))[0]
            entry, cnt_fmt, off_fmt = 20, en + 'H', en + 'Q'
        else:
            return None
        f.seek(off)
        if magic == 42:
            n = struct.unpack(en + 'H', f.read(2))[0]
        else:
            n = struct.unpack(en + 'Q', f.read(8))[0]
        want = {33550: None, 33922: None, 34264: None}
        for _ in range(n):
            raw = f.read(entry)
            if len(raw) < entry:
                break
            tag, typ = struct.unpack(cnt_fmt + 'H', raw[:4])
            if tag not in want:
                continue
            if magic == 42:
                count, voff = struct.unpack(en + 'II', raw[4:12])
            else:
                count, voff = struct.unpack(en + 'QQ', raw[4:20])
            if typ != 12:                     # DOUBLE 以外は扱わない
                continue
            cur = f.tell()
            f.seek(voff)
            want[tag] = struct.unpack(en + '%dd' % count, f.read(8 * count))
            f.seek(cur)
        if want[33550] and want[33922] and len(want[33922]) >= 6:
            s, t = want[33550], want[33922]
            return float(s[0]), float(s[1]), float(t[3]), float(t[4])
        if want[34264] and len(want[34264]) >= 16:
            t = want[34264]
            return float(t[0]), float(-t[5]), float(t[3]), float(t[7])
    return None


def _world_file(path):
    for ext in ('.jgw', '.pgw', '.tfw', '.wld', '.jpgw', '.pngw'):
        p = os.path.splitext(path)[0] + ext
        if os.path.exists(p):
            v = [float(x) for x in open(p).read().split()[:6]]
            # A, D, B, E, C, F : x = A*col + B*row + C
            # 回転なし前提（本業務のオルソは回転0）
            return abs(v[0]), abs(v[3]), v[4], v[5]
    return None


class Ortho:
    """オルソ1枚。平面直角座標XI系での位置と画素を保持する。"""

    def __init__(self, path, max_pixels=None, verbose=True, crs=None):
        self.path = path
        self.verbose = verbose
        # 座標系。既定は平面直角座標系XI系（ドローンオルソ）。
        # 衛星画像などは GeoTIFF の GeoKey や .prj から自動で判定する。
        self.crs = crs or crsmod.detect(path, verbose=verbose) or geo.XI
        if verbose and self.crs is geo.XI:
            print('   座標系: 平面直角座標系11系（既定）')
        tr = None
        if path.lower().endswith(('.tif', '.tiff')):
            tr = _geotiff_transform(path)
        if tr is None:
            tr = _world_file(path)
        if tr is None:
            raise SystemExit(
                'ジオリファレンス情報が見つかりません: %s\n'
                '  GeoTIFF なら ModelPixelScale/ModelTiepoint タグ、\n'
                '  JPEG/PNG ならワールドファイル(.jgw など)が必要です。' % path)
        self.px, self.py, self.ox, self.oy = tr

        im = Image.open(path)
        self.full_w, self.full_h = im.size
        scale = 1
        if max_pixels and self.full_w * self.full_h > max_pixels:
            scale = math.ceil(math.sqrt(self.full_w * self.full_h / max_pixels))
            if im.format == 'JPEG':
                im.draft('RGB', (self.full_w // scale, self.full_h // scale))
        if verbose:
            print('   画素数 %d x %d / 画素寸法 %.4f m' % (self.full_w, self.full_h, self.px))

        bands = im.getbands()
        has_alpha = 'A' in bands
        im = im.convert('RGBA' if has_alpha else 'RGB')
        if scale > 1:
            im = im.resize((max(1, self.full_w // scale), max(1, self.full_h // scale)),
                           Image.LANCZOS)
            if verbose:
                print('   1/%d に縮小して処理: %d x %d' % (scale, im.size[0], im.size[1]))
        arr = np.asarray(im)
        im.close()

        self.w, self.h = arr.shape[1], arr.shape[0]
        self.sx = self.px * (self.full_w / self.w)     # 実際に保持している画素の寸法
        self.sy = self.py * (self.full_h / self.h)
        if arr.shape[2] == 4:
            self.rgb = np.ascontiguousarray(arr[:, :, :3])
            self.alpha = np.ascontiguousarray(arr[:, :, 3])
        else:
            self.rgb = np.ascontiguousarray(arr)
            # アルファが無い場合、真っ黒な外周を範囲外とみなす
            self.alpha = np.where(self.rgb.max(axis=2) <= 2, 0, 255).astype(np.uint8)
        del arr

    # ----- 座標 -----
    @property
    def bounds_xy(self):
        return (self.ox, self.oy - self.sy * self.h, self.ox + self.sx * self.w, self.oy)

    @property
    def bounds_lonlat(self):
        x0, y0, x1, y1 = self.bounds_xy
        lo = []
        la = []
        for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            a, b = self.crs.to_lonlat(x, y)
            lo.append(a); la.append(b)
        return (min(lo), min(la), max(lo), max(la))

    def xy_to_pixel(self, x, y):
        return (x - self.ox) / self.sx, (self.oy - y) / self.sy

    def pixel_to_xy(self, col, row):
        return self.ox + (col + 0.5) * self.sx, self.oy - (row + 0.5) * self.sy

    def coverage(self):
        return float((self.alpha > 0).mean())


# ------------------------------------------------------------ タイル化
def _control_map(z, tx, ty, ortho):
    """タイル(z,tx,ty)の (CTRL+1)^2 制御点における元画像の画素座標を厳密に求める。"""
    mx0, my0, mx1, my1 = geo.tile_bounds_merc(z, tx, ty)
    g = np.linspace(0.0, 1.0, CTRL + 1)
    gx = mx0 + (mx1 - mx0) * g
    gy = my1 - (my1 - my0) * g            # 上から下
    MX, MY = np.meshgrid(gx, gy)
    lon, lat = geo.merc_to_lonlat(MX, MY)
    X, Y = ortho.crs.from_lonlat(lon, lat)
    col = (X - ortho.ox) / ortho.sx
    row = (ortho.oy - Y) / ortho.sy
    return col, row


def _upsample(grid, n):
    """(CTRL+1)x(CTRL+1) の制御格子を n x n へ双一次補間で展開。"""
    k = grid.shape[0] - 1
    t = np.linspace(0.0, k, n)
    i0 = np.floor(t).astype(int)
    i0 = np.clip(i0, 0, k - 1)
    f = (t - i0)
    a = grid[i0, :] * (1 - f)[:, None] + grid[i0 + 1, :] * f[:, None]
    b = a[:, i0] * (1 - f)[None, :] + a[:, i0 + 1] * f[None, :]
    return b


def render_tile(ortho, z, tx, ty):
    """1タイルを (256,256,4) uint8 で返す。全て範囲外なら None。"""
    c_ctrl, r_ctrl = _control_map(z, tx, ty, ortho)
    C = _upsample(c_ctrl, TILE)
    R = _upsample(r_ctrl, TILE)

    h, w = ortho.h, ortho.w
    inside = (C >= 0) & (C <= w - 1) & (R >= 0) & (R <= h - 1)
    if not inside.any():
        return None

    C = np.clip(C, 0, w - 1.001)
    R = np.clip(R, 0, h - 1.001)
    c0 = C.astype(np.int32)
    r0 = R.astype(np.int32)
    fc = (C - c0)[..., None]
    fr = (R - r0)[..., None]
    c1 = np.minimum(c0 + 1, w - 1)
    r1 = np.minimum(r0 + 1, h - 1)

    src = ortho.rgb
    p00 = src[r0, c0].astype(np.float32)
    p01 = src[r0, c1].astype(np.float32)
    p10 = src[r1, c0].astype(np.float32)
    p11 = src[r1, c1].astype(np.float32)
    top = p00 * (1 - fc) + p01 * fc
    bot = p10 * (1 - fc) + p11 * fc
    rgb = (top * (1 - fr) + bot * fr)

    a = ortho.alpha
    a00 = a[r0, c0].astype(np.float32)
    a01 = a[r0, c1].astype(np.float32)
    a10 = a[r1, c0].astype(np.float32)
    a11 = a[r1, c1].astype(np.float32)
    at = a00 * (1 - fc[..., 0]) + a01 * fc[..., 0]
    ab = a10 * (1 - fc[..., 0]) + a11 * fc[..., 0]
    alpha = at * (1 - fr[..., 0]) + ab * fr[..., 0]
    # 端で色がにじまないよう、少しでも欠けている画素は透過にする
    alpha = np.where(alpha < 250, 0, 255)
    alpha = np.where(inside, alpha, 0)

    if not alpha.any():
        return None
    out = np.empty((TILE, TILE, 4), dtype=np.uint8)
    out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[:, :, 3] = alpha.astype(np.uint8)
    return out


def pick_max_zoom(ortho, cap=22, tol=1.25):
    """元画像の解像度に見合う最大ズームを選ぶ。

    タイルの地上解像度が元画像より tol 倍以上粗くならない範囲で、
    いちばん粗い（＝タイル枚数が少ない）ズームを選ぶ。
    GSD 0.10m なら z20、0.08m なら z21 になり、引き継ぎ資料の推奨と一致する。
    """
    lon0, lat0, lon1, lat1 = ortho.bounds_lonlat
    lat = (lat0 + lat1) / 2.0
    for z in range(1, cap + 1):
        ground = geo.merc_resolution(z) * math.cos(math.radians(lat))
        if ground <= ortho.sx * tol:
            return z
    return cap


def webp_ok():
    try:
        from PIL import features
        return bool(features.check('webp'))
    except Exception:
        return False


def write_tiles(ortho, out_dir, zmin=None, zmax=None, fmt=None, quality=82,
                progress=None):
    """XYZ タイルを書き出し、(zmin, zmax, ext, タイル数) を返す。"""
    if fmt is None:
        fmt = 'webp' if webp_ok() else 'png'
    ext = fmt
    lon0, lat0, lon1, lat1 = ortho.bounds_lonlat
    if zmax is None:
        zmax = pick_max_zoom(ortho)
    if zmin is None:
        zmin = max(1, zmax - 8)

    made = {}
    # --- 最大ズーム: 元画像から直接描画 ---
    x0, y0 = geo.lonlat_to_tile(lon0, lat1, zmax)
    x1, y1 = geo.lonlat_to_tile(lon1, lat0, zmax)
    total = (x1 - x0 + 1) * (y1 - y0 + 1)
    done = 0
    count = 0
    level = {}
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            done += 1
            t = render_tile(ortho, zmax, tx, ty)
            if progress and done % 50 == 0:
                progress(zmax, done, total)
            if t is None:
                continue
            _save(t, out_dir, zmax, tx, ty, ext, quality)
            level[(tx, ty)] = True
            count += 1
    if progress:
        progress(zmax, total, total)
    made[zmax] = count

    # --- 下位ズーム: 上位4枚を縮小して合成 ---
    for z in range(zmax - 1, zmin - 1, -1):
        parents = {}
        for (tx, ty) in level:
            parents.setdefault((tx // 2, ty // 2), []).append((tx, ty))
        cnt = 0
        for (px, py), kids in parents.items():
            canvas = Image.new('RGBA', (TILE * 2, TILE * 2), (0, 0, 0, 0))
            for (tx, ty) in kids:
                p = os.path.join(out_dir, str(z + 1), str(tx), '%d.%s' % (ty, ext))
                if not os.path.exists(p):
                    continue
                with Image.open(p) as im:
                    canvas.paste(im.convert('RGBA'), ((tx - px * 2) * TILE, (ty - py * 2) * TILE))
            small = canvas.resize((TILE, TILE), Image.LANCZOS)
            arr = np.asarray(small)
            if not arr[:, :, 3].any():
                continue
            _save(arr, out_dir, z, px, py, ext, quality)
            cnt += 1
        made[z] = cnt
        level = {k: True for k in parents}
        if progress:
            progress(z, cnt, cnt)
    return zmin, zmax, ext, sum(made.values())


def _save(arr, out_dir, z, x, y, ext, quality):
    d = os.path.join(out_dir, str(z), str(x))
    os.makedirs(d, exist_ok=True)
    im = Image.fromarray(arr, 'RGBA')
    p = os.path.join(d, '%d.%s' % (y, ext))
    if ext == 'webp':
        im.save(p, 'WEBP', quality=quality, method=4)
    else:
        im.save(p, 'PNG', optimize=True)
