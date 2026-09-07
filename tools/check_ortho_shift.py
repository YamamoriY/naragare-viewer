# -*- coding: utf-8 -*-
"""オルソの絶対位置が何メートルずれているかを測る。

ドローンのオルソは、形と距離は正確でも「地球上のどこにあるか」が
±数メートルずれる（詳しくは docs/位置合わせと精度.md）。
このずれを数字で確かめるための道具。

やっていること
  1. 取り込み済みのオルソタイル（z18）を数枚つないで、約450m四方の画像にする
  2. 同じ範囲の国土地理院「シームレス空中写真」を取ってきて同じ大きさにする
  3. 二つの模様が一番よく重なる位置を位相相関で求める
  4. そのずれを、東西・南北のメートルに直す

比べる相手（シームレス空中写真）自体にも数メートルの誤差と撮影時期の差が
あるので、出てくる数字にも ±2m 程度の幅がある。一面の森林で照合できる模様が
無い場所は「測れなかった」と出る。ずれが無いという意味ではない。

使い方
    python tools/check_ortho_shift.py                # 全サイト
    python tools/check_ortho_shift.py siteB siteG    # サイトを指定
    python tools/check_ortho_shift.py --windows 20   # 窓を増やして精度を上げる
"""
from __future__ import annotations
import io, os, sys, math, glob, time, argparse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TILES = os.path.join(ROOT, 'data', 'tiles')
CACHE = os.path.join(ROOT, 'data', 'basemap', 'seamlessphoto')
GSI = 'https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg'
UA = 'naragare-viewer (local accuracy check)'
Z = 18                    # シームレス空中写真がある一番細かいズーム
TILE = 256


try:
    import numpy as np
    from PIL import Image
except ImportError as e:
    sys.stderr.write('%s が必要です。'
                     '「初期設定（最初に一度だけ）.bat」を実行してください。\n' % e.name)
    raise SystemExit(1)


# ------------------------------------------------------------ タイルを読む
def gsi_tile(x, y):
    """地理院の空中写真タイル。一度取ったら data/basemap/ に控える。
    無い場所は空ファイルを置いて、次から取りにいかない。"""
    p = os.path.join(CACHE, str(Z), str(x), '%d.jpg' % y)
    if os.path.exists(p):
        b = open(p, 'rb').read()
        return b or None
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        req = urllib.request.Request(GSI.format(z=Z, x=x, y=y), headers={'User-Agent': UA})
        b = urllib.request.urlopen(req, timeout=20).read()
    except Exception:
        open(p, 'wb').close()
        return None
    open(p, 'wb').write(b)
    time.sleep(0.12)          # 地理院のサーバーに負担をかけない
    return b


def _gray(im):
    a = np.asarray(im.convert('RGB'), dtype=np.float64)
    return 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]


def mosaic_ortho(site, x0, y0, n):
    """オルソのタイル n×n 枚を繋ぐ。欠けていたら None。
    戻り値は (濃淡画像, 不透明な画素の割合)。"""
    out = np.zeros((TILE * n, TILE * n), np.float64)
    solid = np.zeros((TILE * n, TILE * n), bool)
    for j in range(n):
        for i in range(n):
            hit = glob.glob(os.path.join(TILES, site, str(Z), str(x0 + i), '%d.*' % (y0 + j)))
            if not hit:
                return None, 0.0
            a = np.asarray(Image.open(hit[0]).convert('RGBA'), np.float64)
            sl = (slice(j * TILE, (j + 1) * TILE), slice(i * TILE, (i + 1) * TILE))
            out[sl] = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
            solid[sl] = a[:, :, 3] > 250
    return out, float(solid.mean())


def mosaic_gsi(x0, y0, n):
    out = np.zeros((TILE * n, TILE * n), np.float64)
    for j in range(n):
        for i in range(n):
            b = gsi_tile(x0 + i, y0 + j)
            if b is None:
                return None
            out[slice(j * TILE, (j + 1) * TILE), slice(i * TILE, (i + 1) * TILE)] \
                = _gray(Image.open(io.BytesIO(b)))
    return out


# ------------------------------------------------------ 位相相関でずれを測る
def phase_corr(ref, img):
    """img を ref に重ねるための移動量 (dy, dx) を画素で返す。
    あわせて、その山がどれだけ際立っているか（SNR）も返す。"""
    ref = (ref - ref.mean()) / (ref.std() + 1e-9)
    img = (img - img.mean()) / (img.std() + 1e-9)
    w = np.outer(np.hanning(ref.shape[0]), np.hanning(ref.shape[1]))
    R = np.fft.fft2(ref * w) * np.conj(np.fft.fft2(img * w))
    c = np.fft.fftshift(np.fft.ifft2(R / (np.abs(R) + 1e-9)).real)
    py, px = np.unravel_index(int(np.argmax(c)), c.shape)
    cy, cx = c.shape[0] // 2, c.shape[1] // 2

    def sub(arr, k):                       # 放物線あてはめで画素より細かく
        if k <= 0 or k >= len(arr) - 1:
            return 0.0
        l, m, r = arr[k - 1], arr[k], arr[k + 1]
        d = l - 2 * m + r
        return 0.0 if d == 0 else 0.5 * (l - r) / d

    dy = (py - cy) + sub(c[:, px], py)
    dx = (px - cx) + sub(c[py, :], px)
    ring = c.copy()
    ring[max(0, py - 4):py + 5, max(0, px - 4):px + 5] = 0
    return dy, dx, c[py, px] / (ring.std() + 1e-12)


def mpp(lat):
    """z18 の1画素が地上何メートルか"""
    return 156543.033928 / (2 ** Z) * math.cos(math.radians(lat))


def tile_center_lat(x, y):
    n = 2 ** Z
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))


# ------------------------------------------------------------------- 本体
def check(site, n=4, windows=14, min_snr=8.0, verbose=False):
    zdir = os.path.join(TILES, site, str(Z))
    if not os.path.isdir(zdir):
        return None
    spots = []
    for xd in sorted(glob.glob(os.path.join(zdir, '*'))):
        x0 = int(os.path.basename(xd))
        for f in sorted(glob.glob(os.path.join(xd, '*.*'))):
            spots.append((x0, int(os.path.splitext(os.path.basename(f))[0])))

    hits, tried = [], 0
    for x0, y0 in spots:
        if tried >= windows:
            break
        o, cov = mosaic_ortho(site, x0, y0, n)
        if o is None or cov < 0.995:       # 撮影範囲の外が混ざる窓は使わない
            continue
        g = mosaic_gsi(x0, y0, n)
        if g is None:
            continue
        tried += 1
        dy, dx, snr = phase_corr(g, o)
        m = mpp(tile_center_lat(x0 + n / 2, y0 + n / 2))
        if verbose:
            print('    x=%d y=%d  東%+7.2f 北%+7.2f  SNR %.1f'
                  % (x0, y0, dx * m, -dy * m, snr))
        if snr >= min_snr:
            hits.append((dx * m, -dy * m))
    if len(hits) < 3:
        return dict(site=site, tried=tried, ok=0)

    e = np.array([h[0] for h in hits])
    nn = np.array([h[1] for h in hits])
    # 中央値から大きく離れた窓（別の模様に合ってしまったもの）を落とす
    keep = (np.abs(e - np.median(e)) < 10) & (np.abs(nn - np.median(nn)) < 10)
    e, nn = e[keep], nn[keep]
    if len(e) < 3:
        return dict(site=site, tried=tried, ok=int(len(e)))
    return dict(site=site, tried=tried, ok=int(len(e)),
                east=float(np.median(e)), north=float(np.median(nn)),
                sd_e=float(e.std()), sd_n=float(nn.std()),
                dist=float(math.hypot(np.median(e), np.median(nn))))


def registered_sites():
    """survey.db に登録されているオルソだけを対象にする。
    data/tiles/ には過去の試作の残りも入っているので、それは見ない。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import db as dbmod
    con = dbmod.connect()
    out = [r[0] for r in con.execute(
        'SELECT id FROM sites WHERE zmax IS NOT NULL ORDER BY id')]
    con.close()
    return [s for s in out if os.path.isdir(os.path.join(TILES, s, str(Z)))]


def main():
    ap = argparse.ArgumentParser(description='オルソの絶対位置のずれを測る')
    ap.add_argument('sites', nargs='*', help='省略すると全サイト')
    ap.add_argument('--windows', type=int, default=14, help='サイトあたりの窓の数')
    ap.add_argument('--size', type=int, default=4, help='窓の一辺のタイル数（4なら約450m）')
    ap.add_argument('-v', '--verbose', action='store_true', help='窓ごとの結果も出す')
    a = ap.parse_args()

    sites = a.sites or registered_sites()
    print('オルソと国土地理院「シームレス空中写真」を重ねて、ずれを測ります。')
    print('（z18 の1画素 = 約 %.2f m。窓は %d×%d タイル ≒ %d m 四方）\n'
          % (mpp(42.05), a.size, a.size, int(a.size * TILE * mpp(42.05))))
    print('%-8s %8s  %8s  %10s  %s' % ('サイト', '東', '北', 'ずれ', '使えた窓'))
    print('-' * 56)
    for s in sites:
        if a.verbose:
            print(s)
        r = check(s, n=a.size, windows=a.windows, verbose=a.verbose)
        if r is None:
            print('%-8s タイルがありません' % s)
        elif not r.get('ok'):
            print('%-8s 測れませんでした（照合できる模様が無い。%d 窓ためした）'
                  % (s, r['tried']))
        else:
            print('%-8s %+7.2f m %+7.2f m   %6.2f m   %d / %d 窓（ばらつき 東±%.2f 北±%.2f）'
                  % (s, r['east'], r['north'], r['dist'], r['ok'], r['tried'],
                     r['sd_e'], r['sd_n']))
        sys.stdout.flush()
    print("""
読み方
  符号は「オルソをこの向きに動かすと空中写真に合う」という意味です。
  東 +3.4 / 北 -6.0 なら、オルソはいま 3.4m 西・6.0m 北にずれて描かれています。
  そのサイトのオルソを見て決めた木の緯度経度には、このずれがそのまま乗ります。

  比べる相手にも数メートルの誤差があるので、数字には ±2m ほどの幅があります。
  「測れませんでした」は、ずれが無いという意味ではありません。""")


if __name__ == '__main__':
    main()
