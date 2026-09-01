# -*- coding: utf-8 -*-
"""オルソ画像からナラ枯れ候補（赤褐色に変色した樹冠）を抽出する。

考え方は企画提案書 7-1 のデモと同じで、RGB の色情報から枯死した樹冠を拾い、
誤検出要因（道路・裸地・日陰・水面）を色と形で落とす。教師データを必要としない
ので、オルソを入れ替えればそのまま動く。AIモデルが用意できた段階では
detect_candidates() の中身だけを差し替えればよい。

判定に使う指標（いずれも RGB のみから計算できる）
  ExG  = 2g - r - b        正規化RGBの「緑らしさ」。生きた樹冠で高い
  ExR  = 1.4r - g          「赤らしさ」
  VARI = (g - r)/(g + r - b)  可視域の植生指数。枯れると負に振れる
  S, V = HSV の彩度・明度   道路や裸地は彩度が低い／日陰は明度が低い

誤検出対策
  * アルファ0（飛行範囲外）は除外
  * 明度が低い画素（日陰）は除外
  * 彩度が低い画素（道路・裸地・construction）は除外
  * 面積が小さすぎる／大きすぎる領域は除外（樹冠1本のサイズから外れる）
  * 細長い領域は除外（道路・林道は細長い）
"""
from __future__ import annotations
import math
import numpy as np

# 既定のしきい値。--tune で上書きできる。
DEFAULTS = dict(
    vari_max=0.02,      # VARI がこれ以下＝緑が失われている
    exr_min=0.02,       # 赤みがこれ以上
    sat_min=0.18,       # 彩度がこれ未満は道路・裸地とみなす
    val_min=0.22,       # 明度がこれ未満は日陰とみなす
    val_max=0.97,       # 白飛び除外
    min_area_m2=2.0,    # 樹冠として小さすぎる
    max_area_m2=400.0,  # 樹冠として大きすぎる（裸地・伐採跡）
    max_elongation=4.0, # 細長い＝道路の可能性
    work_res_m=0.25,    # 解析解像度。樹冠は数m大なのでこれで十分
    green_ring_min=0.25,  # 周囲が緑（生きた林冠）である割合の下限
    ring_m=6.0,           # 周囲を見る幅[m]
    exg_green=0.02,       # これ以上を「緑」とみなす
)


def _indices(rgb):
    f = rgb.astype(np.float32) / 255.0
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    s = r + g + b + 1e-6
    rn, gn, bn = r / s, g / s, b / s
    exg = 2.0 * gn - rn - bn
    exr = 1.4 * rn - gn
    vari = (g - r) / (g + r - b + 1e-6)
    mx = f.max(axis=2)
    mn = f.min(axis=2)
    val = mx
    sat = np.where(mx > 1e-6, (mx - mn) / (mx + 1e-6), 0.0)
    return exg, exr, vari, sat, val, rn, gn, bn


def _label(mask):
    """4近傍の連結成分ラベリング。scipy があれば使い、無ければ自前で行う。"""
    try:
        from scipy import ndimage
        lab, n = ndimage.label(mask)
        return lab, n
    except Exception:
        pass
    h, w = mask.shape
    lab = np.zeros((h, w), dtype=np.int32)
    parent = [0]

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    nxt = 1
    for y in range(h):
        row = mask[y]
        for x in range(w):
            if not row[x]:
                continue
            up = lab[y - 1, x] if y > 0 else 0
            lf = lab[y, x - 1] if x > 0 else 0
            if up and lf:
                lab[y, x] = min(up, lf)
                union(up, lf)
            elif up or lf:
                lab[y, x] = up or lf
            else:
                lab[y, x] = nxt
                parent.append(nxt)
                nxt += 1
    remap = {}
    out = 0
    for i in range(1, nxt):
        r = find(i)
        if r not in remap:
            out += 1
            remap[r] = out
    flat = lab.ravel()
    nz = flat > 0
    flat[nz] = [remap[find(v)] for v in flat[nz]]
    return lab, out


def detect_candidates(ortho, params=None, progress=None):
    """オルソから候補領域を返す。

    戻り値: list of dict
      x, y            平面直角座標XI系の重心
      area_m2         領域面積
      score           0..1 の枯死らしさ（大きいほど枯れている見込み）
      width_m/height_m 外接矩形
      exr, vari, sat  代表的な指標値
    """
    p = dict(DEFAULTS)
    if params:
        p.update({k: v for k, v in params.items() if v is not None})

    # 解析解像度へ間引く（元が0.1mなら 2〜3画素に1つ）
    step = max(1, int(round(p['work_res_m'] / max(ortho.sx, 1e-6))))
    rgb = ortho.rgb[::step, ::step]
    alpha = ortho.alpha[::step, ::step]
    res = ortho.sx * step
    cell = res * res

    exg, exr, vari, sat, val, rn, gn, bn = _indices(rgb)
    valid = alpha > 0

    dead = (valid
            & (vari <= p['vari_max'])
            & (exr >= p['exr_min'])
            & (sat >= p['sat_min'])
            & (val >= p['val_min'])
            & (val <= p['val_max'])
            & (rn > bn))                 # 赤が青を上回る＝褐色系。水面や影を弾く

    if progress:
        progress('色判定', int(dead.sum()), dead.size)

    lab, n = _label(dead)
    if n == 0:
        return []

    idx = np.arange(1, n + 1)
    flat = lab.ravel()
    area_px = np.bincount(flat, minlength=n + 1)[1:]

    ys, xs = np.nonzero(lab)
    lv = lab[ys, xs]
    sum_x = np.bincount(lv, weights=xs, minlength=n + 1)[1:]
    sum_y = np.bincount(lv, weights=ys, minlength=n + 1)[1:]
    min_x = np.full(n + 1, 1 << 30, dtype=np.int64)
    max_x = np.full(n + 1, -1, dtype=np.int64)
    min_y = np.full(n + 1, 1 << 30, dtype=np.int64)
    max_y = np.full(n + 1, -1, dtype=np.int64)
    np.minimum.at(min_x, lv, xs)
    np.maximum.at(max_x, lv, xs)
    np.minimum.at(min_y, lv, ys)
    np.maximum.at(max_y, lv, ys)

    s_exr = np.bincount(lv, weights=exr[ys, xs], minlength=n + 1)[1:]
    s_vari = np.bincount(lv, weights=vari[ys, xs], minlength=n + 1)[1:]
    s_sat = np.bincount(lv, weights=sat[ys, xs], minlength=n + 1)[1:]

    # 生きた林冠のマスク。枯死候補は「緑の林冠に囲まれている」はずで、
    # 裸地・農地・建物・道路はそうならない。誤検出を落とす主力の判定。
    green = valid & (exg >= p['exg_green']) & (val >= p['val_min'])
    ring_px = max(2, int(round(p['ring_m'] / res)))

    out = []
    dropped_ring = 0
    for i in range(n):
        a_px = area_px[i]
        area = a_px * cell
        if area < p['min_area_m2'] or area > p['max_area_m2']:
            continue
        bx0, bx1 = int(min_x[i + 1]), int(max_x[i + 1])
        by0, by1 = int(min_y[i + 1]), int(max_y[i + 1])
        w = (bx1 - bx0 + 1) * res
        h = (by1 - by0 + 1) * res
        long_side, short_side = max(w, h), max(min(w, h), 1e-6)
        if long_side / short_side > p['max_elongation']:
            continue
        # 外接矩形に対する充填率が低い＝線状/樹枝状なので落とす
        fill = area / max(w * h, 1e-6)
        if fill < 0.30:
            continue

        # 周囲が緑か（外接矩形を ring_px 広げた枠の中で、候補領域以外を見る）
        ox0 = max(0, bx0 - ring_px); ox1 = min(lab.shape[1] - 1, bx1 + ring_px)
        oy0 = max(0, by0 - ring_px); oy1 = min(lab.shape[0] - 1, by1 + ring_px)
        sub_lab = lab[oy0:oy1 + 1, ox0:ox1 + 1]
        sub_out = (sub_lab != (i + 1))
        sub_valid = valid[oy0:oy1 + 1, ox0:ox1 + 1] & sub_out
        nvalid = int(sub_valid.sum())
        if nvalid < 8:
            continue
        ring = float((green[oy0:oy1 + 1, ox0:ox1 + 1] & sub_valid).sum()) / nvalid
        if ring < p['green_ring_min']:
            dropped_ring += 1
            continue

        cx = sum_x[i] / a_px * step
        cy = sum_y[i] / a_px * step
        X, Y = ortho.pixel_to_xy(cx, cy)
        exr_m = s_exr[i] / a_px
        vari_m = s_vari[i] / a_px
        sat_m = s_sat[i] / a_px

        # スコア: 赤みが強く緑が失われ、周囲が生きた林冠であるほど高い
        sc = (min(1.0, max(0.0, (exr_m - p['exr_min']) / 0.12)) * 0.40
              + min(1.0, max(0.0, (p['vari_max'] - vari_m) / 0.20)) * 0.35
              + min(1.0, max(0.0, (sat_m - p['sat_min']) / 0.25)) * 0.10
              + min(1.0, max(0.0, (ring - p['green_ring_min']) / 0.45)) * 0.15)
        out.append(dict(
            x=float(X), y=float(Y), area_m2=float(area), score=float(round(sc, 4)),
            width_m=float(round(w, 2)), height_m=float(round(h, 2)),
            exr=float(round(exr_m, 4)), vari=float(round(vari_m, 4)),
            sat=float(round(sat_m, 4)), fill=float(round(fill, 3)),
            green_ring=float(round(ring, 3)),
        ))
    out.sort(key=lambda d: -d['score'])
    if progress:
        progress('領域抽出', len(out), n)
        progress('周囲が緑でないため除外', dropped_ring, n)
    return out


def rank_score(score, elev=None, nara_rank=None):
    """順位づけ用の点数。色のスコアに、行政判断に効く要素を足し引きする。

    画像の色だけでは決めない。行政が実際に知りたいのは
    「ナラ類がある場所で」「カシナガが越冬できる標高で」枯れているか、なので
      * 森林調査簿の樹種（ナラ類か）
      * 標高（森町は標高200m以下を重点管理地域に設定している）
    を必ず加味する。小班の外（農地・宅地・国有林など、民有林の森林簿に無い場所）
    は森林として扱わず、大きく下げる。

    nara_rank: 2=ナラ類確実 / 1=ナラ類の可能性 / 0=小班内だがナラ類でない
               None=民有林の小班の外
    """
    s = score
    if nara_rank == 2:
        s += 0.20
    elif nara_rank == 1:
        s += 0.08
    elif nara_rank == 0:
        s -= 0.10
    else:
        s -= 0.25
    if elev is not None and elev <= 200:
        s += 0.08
    return s


def assign_priority(items, high_n=40, mid_n=120, floor=0.30):
    """サイト内の相対順位で優先度（高/中/低）を割り当てる。

    絶対的なしきい値にしないのは、オルソの色味が撮影条件で大きく変わり、
    同じ数値でも意味が違ってしまうため。それよりも
    「このサイトで最初に見に行くべき40本」を必ず出せることを優先する。

    渡島総合振興局による森町の現地確認では、確認した地点のうち実際にナラ枯れ
    だったのは1地点だけだった。色による抽出は診断ではなく順位づけであり、
    現地確認の順番を決めるために使うもの、という前提で設計している。

    items: rank_score を 'rank' キーに入れた dict のリスト（この場で 'priority' を書き込む）
    high_n / mid_n: 高・中に入れる件数
    floor: これ未満の点数は件数に関わらず「低」
    """
    order = sorted(range(len(items)), key=lambda i: -items[i].get('rank', 0.0))
    for pos, i in enumerate(order):
        r = items[i].get('rank', 0.0)
        if r < floor:
            items[i]['priority'] = '低'
        elif pos < high_n:
            items[i]['priority'] = '高'
        elif pos < high_n + mid_n:
            items[i]['priority'] = '中'
        else:
            items[i]['priority'] = '低'
    return items


def priority_of(score, elev=None, nara_rank=None):
    """1本だけの優先度が要るとき（手入力など）の簡易版。"""
    s = rank_score(score, elev, nara_rank)
    if s >= 0.70:
        return '高'
    if s >= 0.50:
        return '中'
    return '低'
