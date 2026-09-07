# -*- coding: utf-8 -*-
"""ホーム画面に置くアイコンを作る。

    py -3 tools/make_icons.py

スマホに「アプリとして入れる」とき、アイコンは PNG でないといけない
（iOS は SVG を受け付けない）。何度も作り直すものではないので、
出来たファイルはリポジトリに入れてある。作り直したいときだけ実行する。

絵柄
    深い緑の角丸に、ふちの丸いナラの葉を1枚。
    葉の色は「枯れ」の側 ―― 赤みのある橙。
    小さくしたときに何のアプリか分かることだけを狙っている。
"""
from __future__ import annotations
import math, os, sys

try:
    from PIL import Image, ImageDraw
except ImportError:
    print('Pillow が要ります:  py -3 -m pip install pillow')
    sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'app', 'icons')

BG = (16, 61, 46)          # 深い緑（森）
LEAF = (222, 104, 42)      # 枯れはじめの橙
LEAF2 = (176, 48, 38)      # 影の側
VEIN = (255, 236, 214)


def leaf_polygon(cx, cy, h, w, lobes=9):
    """ナラの葉。中心線に対して左右対称に、ふちを波打たせる。"""
    left, right = [], []
    n = 220
    for i in range(n + 1):
        t = i / n                                   # 0=葉柄 1=先端
        # 基本の輪郭（付け根で細く、真ん中でいちばん広く、先端で細く）
        base = math.sin(math.pi * min(1.0, t * 1.02)) ** 0.75
        # ふちの丸い切れ込み（ナラらしさはここ）
        wave = 1.0 + 0.20 * math.cos(lobes * math.pi * t + math.pi)
        # 先端に向かってだんだん切れ込みを浅くする
        wave = 1.0 + (wave - 1.0) * (0.35 + 0.65 * (1 - t))
        ww = base * wave * w / 2
        y = cy + h / 2 - t * h
        left.append((cx - ww, y))
        right.append((cx + ww, y))
    return left, right


def rounded(size, radius, color):
    im = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=color)
    return im


def draw_icon(size, pad_ratio=0.0, radius_ratio=0.22, square=False):
    """pad_ratio: マスカブル用の余白（端が切られても葉が欠けないように）"""
    ss = 4                                   # 4倍で描いて縮める（縁を滑らかに）
    S = size * ss
    if square:
        im = Image.new('RGBA', (S, S), BG + (255,))
    else:
        im = rounded(S, int(S * radius_ratio), BG + (255,))
    d = ImageDraw.Draw(im)

    inner = S * (1 - 2 * pad_ratio)
    cx, cy = S / 2, S / 2
    h = inner * 0.70
    w = inner * 0.46

    left, right = leaf_polygon(cx, cy - inner * 0.02, h, w)
    d.polygon(left + right[::-1], fill=LEAF + (255,))

    # 右半分だけ暗い色で塗り直して、葉に厚みを出す
    mid = [(cx, y) for _, y in right]
    d.polygon(mid + right[::-1], fill=LEAF2 + (255,))

    # 主脈と葉柄
    lw = max(2, int(S * 0.016))
    d.line([(cx, cy - inner * 0.02 + h / 2), (cx, cy - inner * 0.02 - h / 2 + h * 0.04)],
           fill=VEIN + (200,), width=lw)
    d.line([(cx, cy - inner * 0.02 + h / 2), (cx, cy - inner * 0.02 + h / 2 + inner * 0.09)],
           fill=VEIN + (140,), width=lw)
    # 側脈
    for k in range(1, 5):
        t = k / 5.0
        y = cy - inner * 0.02 + h / 2 - t * h
        ww = (math.sin(math.pi * t) ** 0.75) * w / 2 * 0.72
        d.line([(cx, y), (cx - ww, y - h * 0.06)], fill=VEIN + (90,), width=max(1, lw // 2))
        d.line([(cx, y), (cx + ww, y - h * 0.06)], fill=VEIN + (90,), width=max(1, lw // 2))

    return im.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    made = []
    for size in (192, 512):
        p = os.path.join(OUT, 'icon-%d.png' % size)
        draw_icon(size).save(p)
        made.append(p)
    # マスカブル（Android が好きな形に切り抜く。安全域は中央80%）
    p = os.path.join(OUT, 'icon-maskable-512.png')
    draw_icon(512, pad_ratio=0.14, square=True).save(p)
    made.append(p)
    # iOS のホーム画面用（角丸はOSが付けるので四角のまま）
    p = os.path.join(OUT, 'apple-touch-icon.png')
    draw_icon(180, radius_ratio=0.0, square=True).save(p)
    made.append(p)
    p = os.path.join(OUT, 'favicon.png')
    draw_icon(64).save(p)
    made.append(p)
    for m in made:
        print('  %s (%.1f KB)' % (os.path.relpath(m, ROOT), os.path.getsize(m) / 1024))
    print('できました。')


if __name__ == '__main__':
    main()
