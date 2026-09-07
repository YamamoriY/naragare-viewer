# -*- coding: utf-8 -*-
"""座標の計算が正しいかを、国土地理院の公式サービスと突き合わせて確かめる。

「入れた緯度経度と違う場所にピンが立つ」と思ったときに、どこが原因かを
切り分けるための道具。確かめるのは次の5つ。

  1. 緯度経度 → 平面直角座標XI系
     国土地理院「測量計算サイト」の公式計算と比べる。
     ここが合っていれば、座標系の変換に間違いは無い。

  2. 平面直角座標XI系 → 緯度経度（戻り）
     同サイトの逆計算と比べる。

  3. 緯度経度 → 地図タイルの番号と画素
     Webメルカトル・XYZタイルの教科書どおりの式（国土地理院・OpenStreetMap
     ほか世界中の地図が使っているもの）と、手元の2つの実装
     （tools/dem.py と server.py）を比べる。
     ここが合っていれば、緯度経度から地図上の位置を求める計算に間違いは無い。

  4. 緯度経度 → 標高
     国土地理院の標高APIと、手元の data/dem_cache/ の読み取りを比べる。
     ただし国土地理院APIはその場所で一番細かい製品（多くは5mレーザ）を返し、
     こちらが持っているのは DEM10B（10mメッシュ）。製品が違うので、
     急斜面では数メートル食い違うのが普通。位置の検証は 3 で行う。

  5. 往復の自己検算

1・2・3 が合っていれば、**このソフトは入れた緯度経度をそのとおりの場所に置いている**。
それでも現地と合わない場合、原因は座標の計算ではなく

  ・記録そのものの位置      → 「位置の確からしさ」を見る。
                              「小班の代表点」は木の座標ではない。小班は数haあり、
                              木は代表点から数百m離れていることがある
  ・オルソの絶対位置のずれ  → tools/check_ortho_shift.py で測る（1〜7m）
  ・GPSの受信誤差           → 樹冠の下では 5〜15m ずれる

のいずれか。docs/位置合わせと精度.md を参照。

なお、画面に描く位置（Leafletの投影）も同じ教科書の式と 1e-9 画素まで一致することを
確認済み。ブラウザのコンソールで確かめ直す手順は docs/引き継ぎ.md にある。

インターネットに繋がっていないと 1・2・4 は実行できない（3 と 5 は動く）。

使い方
    python tools/verify_coords.py                    # 登録済みの地点で確かめる
    python tools/verify_coords.py 42.105986 140.683474
"""
from __future__ import annotations
import os, sys, json, math, time, argparse, urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geo
import db as dbmod

UA = 'naragare-viewer (coordinate verification)'
BL2XY = ('https://vldb.gsi.go.jp/sokuchi/surveycalc/surveycalc/bl2xy.pl'
         '?outputType=json&latitude=%.9f&longitude=%.9f&refFrame=2&zone=11')
XY2BL = ('https://vldb.gsi.go.jp/sokuchi/surveycalc/surveycalc/xy2bl.pl'
         '?outputType=json&publicX=%.4f&publicY=%.4f&refFrame=2&zone=11')
ELEV = ('https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php'
        '?lon=%.9f&lat=%.9f&outtype=JSON')


def get_json(url, tries=3):
    """公式サービスは続けて叩くと弾かれることがあるので、少し待って三度まで試す。"""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            b = urllib.request.urlopen(req, timeout=25).read()
            d = json.loads(b.decode('utf-8'))
            if 'OutputData' in d or 'elevation' in d:
                time.sleep(0.4)          # 公式サービスに負担をかけない
                return d
            last = RuntimeError('返事の形が想定と違う: %s' % str(d)[:120])
        except Exception as e:
            last = e
        time.sleep(1.0 + i)
    raise last


# ---------------------------------------------------------------- 1. 順変換
def check_forward(lat, lon):
    """緯度経度 → 平面直角XI系。地理院の公式計算との差をメートルで返す。"""
    mine_e, mine_n = geo.XI.from_lonlat(lon, lat)      # 内部は (東, 北)
    d = get_json(BL2XY % (lat, lon))['OutputData']
    gsi_n = float(d['publicX'])                        # 公式は X=北
    gsi_e = float(d['publicY'])                        # 　　　 Y=東
    return dict(mine=(gsi_n and mine_n, mine_e), gsi=(gsi_n, gsi_e),
                dn=mine_n - gsi_n, de=mine_e - gsi_e,
                dist=math.hypot(mine_n - gsi_n, mine_e - gsi_e),
                mine_n=mine_n, mine_e=mine_e, gsi_n=gsi_n, gsi_e=gsi_e)


# ---------------------------------------------------------------- 2. 逆変換
def check_inverse(north, east):
    """平面直角XI系 → 緯度経度。公式計算との差をメートルで返す。"""
    mine_lon, mine_lat = geo.XI.to_lonlat(east, north)
    d = get_json(XY2BL % (north, east))['OutputData']
    gsi_lat = float(d['latitude'])
    gsi_lon = float(d['longitude'])
    return dict(mine_lat=mine_lat, mine_lon=mine_lon,
                gsi_lat=gsi_lat, gsi_lon=gsi_lon,
                dist=geo.haversine_m(mine_lon, mine_lat, gsi_lon, gsi_lat))


# ------------------------------------------------------------------ 3. 標高
_DEM = None


def canonical_tile(lon, lat, z=14, tile=256):
    """Webメルカトル・XYZタイルの教科書どおりの式。
    国土地理院・OpenStreetMap ほか、世界中のタイル地図が使っているもの。
    これを基準に、手元の実装がずれていないかを見る。"""
    n = 2.0 ** z
    fx = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    fy = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return (int(fx), int(fy),
            int((fx - int(fx)) * tile), int((fy - int(fy)) * tile))


def check_tiling(lat, lon):
    """緯度経度 → 地図タイルの番号と画素。手元の2実装を教科書の式と比べる。

    tools/dem.py（numpy）と server.py（標準ライブラリ）は別々に書いてあるので、
    両方が同じ答えになることも確かめる。ここが合っていれば、
    緯度経度から地図上の位置を求める計算に間違いは無い。"""
    want = canonical_tile(lon, lat)

    import dem as demmod
    fx, fy = demmod._tile_xy(lon, lat)
    a = (int(fx), int(fy), int((fx - int(fx)) * 256), int((fy - int(fy)) * 256))

    n = 2 ** 14
    fx2 = (lon + 180.0) / 360.0 * n
    lr = math.radians(max(-85.05, min(85.05, lat)))
    fy2 = (1.0 - math.log(math.tan(lr) + 1.0 / math.cos(lr)) / math.pi) / 2.0 * n
    b = (int(fx2), int(fy2), int((fx2 - int(fx2)) * 256), int((fy2 - int(fy2)) * 256))

    return dict(want=want, dem=a, srv=b, ok=(want == a == b))


def check_elev(lat, lon):
    """標高の参照。国土地理院APIと手元の2つの読み取り経路を比べる。

    ただし国土地理院APIは、その場所にある一番細かい製品（多くは5mレーザ）を
    返すのに対し、こちらが持っているのは DEM10B（10mメッシュ）。
    製品が違うので、急斜面では数メートル食い違うのが普通。
    位置がずれているかどうかは check_tiling で見ること。"""
    global _DEM
    d = get_json(ELEV % (lon, lat))
    try:
        gsi = float(d['elevation'])
    except (TypeError, ValueError):
        gsi = None

    import dem as demmod
    if _DEM is None:
        _DEM = demmod.DEM(os.path.join(dbmod.DATA, 'dem_cache'), quiet=True)
    a = _DEM.elevation(lon, lat)

    sys.path.insert(0, dbmod.ROOT)
    import server
    b = server.point_elevation(lon, lat)

    return dict(gsi=gsi, dem=a, api=b, src=d.get('hsrc'),
                d1=None if (gsi is None or a is None) else a - gsi,
                d2=None if (gsi is None or b is None) else b - gsi,
                same=(a == b))


# ------------------------------------------------------- 4. 自己検算（往復）
def check_roundtrip(lat, lon):
    e, n = geo.XI.from_lonlat(lon, lat)
    lon2, lat2 = geo.XI.to_lonlat(e, n)
    return geo.haversine_m(lon, lat, lon2, lat2)


def points_from_db():
    con = dbmod.connect()
    rows = con.execute('SELECT code, lat, lon, loc_accuracy FROM trees '
                       'WHERE lat IS NOT NULL ORDER BY code').fetchall()
    con.close()
    return [(r['code'], r['lat'], r['lon'], r['loc_accuracy']) for r in rows]


def main():
    ap = argparse.ArgumentParser(
        description='座標の計算を国土地理院の公式サービスと突き合わせる')
    ap.add_argument('latlon', nargs='*', type=float, metavar='緯度 経度',
                    help='省略すると登録済みの地点すべて')
    ap.add_argument('--offline', action='store_true',
                    help='通信せず、往復の自己検算だけ行う')
    a = ap.parse_args()

    if len(a.latlon) == 2:
        pts = [('入力', a.latlon[0], a.latlon[1], '')]
    elif a.latlon:
        sys.exit('緯度と経度を2つ指定してください')
    else:
        pts = points_from_db()

    print('=' * 74)
    print('1. 緯度経度 → 平面直角座標XI系')
    print('   国土地理院「測量計算サイト」の公式計算との差')
    print('=' * 74)
    print('%-11s %13s %13s %10s' % ('地点', 'X（北）[m]', 'Y（東）[m]', '公式との差'))
    print('-' * 74)
    worst = 0.0
    for code, lat, lon, _ in pts:
        if a.offline:
            e, n = geo.XI.from_lonlat(lon, lat)
            print('%-11s %13.4f %13.4f %10s' % (code, n, e, '（通信なし）'))
            continue
        try:
            r = check_forward(lat, lon)
        except Exception as ex:
            print('%-11s 取得できませんでした（%s）' % (code, type(ex).__name__))
            continue
        worst = max(worst, r['dist'])
        print('%-11s %13.4f %13.4f %8.4f m   (北 %+.4f / 東 %+.4f)'
              % (code, r['mine_n'], r['mine_e'], r['dist'], r['dn'], r['de']))
        sys.stdout.flush()
    if not a.offline:
        print('-' * 74)
        print('最大の差: %.4f m' % worst)
        print('※ 公式計算の出力は 0.1mm 単位なので、この桁の差は丸めによるもの。')

    print()
    print('=' * 74)
    print('2. 平面直角座標XI系 → 緯度経度（戻り）')
    print('=' * 74)
    if a.offline:
        print('（通信なし）')
    else:
        for code, lat, lon, _ in pts[:4]:
            e, n = geo.XI.from_lonlat(lon, lat)
            try:
                r = check_inverse(n, e)
            except Exception as ex:
                print('%-11s 取得できませんでした（%s）' % (code, type(ex).__name__))
                continue
            print('%-11s 手元 %.7f, %.7f / 公式 %.7f, %.7f  差 %.4f m'
                  % (code, r['mine_lat'], r['mine_lon'],
                     r['gsi_lat'], r['gsi_lon'], r['dist']))
            sys.stdout.flush()

    print()
    print('=' * 74)
    print('3. 緯度経度 → 地図タイルの番号と画素（z14）')
    print('   Webメルカトルの教科書どおりの式と、手元の2実装を比べる')
    print('=' * 74)
    ng = 0
    for code, lat, lon, _ in pts:
        r = check_tiling(lat, lon)
        if not r['ok']:
            ng += 1
            print('%-11s ★食い違い  教科書 %s / dem.py %s / server.py %s'
                  % (code, r['want'], r['dem'], r['srv']))
        else:
            print('%-11s タイル %d/%d  画素 (%d, %d)   3つとも一致'
                  % (code, r['want'][0], r['want'][1], r['want'][2], r['want'][3]))
    print('-' * 74)
    print('食い違い: %d / %d 地点' % (ng, len(pts)))

    print()
    print('=' * 74)
    print('4. 緯度経度 → 標高')
    print('   国土地理院 標高API と 手元の data/dem_cache/ の読み取り')
    print('   ※ 公式APIは5mレーザ、こちらはDEM10B。製品が違うので数mの差は正常')
    print('=' * 74)
    if a.offline:
        print('（通信なし）')
    else:
        for code, lat, lon, _ in pts:
            try:
                r = check_elev(lat, lon)
            except Exception as ex:
                print('%-11s 取得できませんでした（%s）' % (code, type(ex).__name__))
                continue
            f = lambda v, u='': '—' if v is None else ('%+.1f' % v if u else '%.1f' % v)
            print('%-11s 公式 %7s m / dem.py %7s m (%s) / server.py %7s m (%s)  [%s]'
                  % (code, f(r['gsi']), f(r['dem']), f(r['d1'], '+'),
                     f(r['api']), f(r['d2'], '+'), r['src'] or '?'))
            sys.stdout.flush()

    print()
    print('=' * 74)
    print('5. 往復の自己検算（緯度経度 → XI系 → 緯度経度）')
    print('=' * 74)
    mx = 0.0
    for code, lat, lon, _ in pts:
        d = check_roundtrip(lat, lon)
        mx = max(mx, d)
    print('全 %d 地点で、戻したときの最大の食い違い: %.3e m' % (len(pts), mx))

    print("""
======================================================================
読み方
======================================================================
1・2 の差が 1mm 程度で、3 に食い違いが無ければ、
**このソフトは入れた緯度経度をそのとおりの場所に置いている。**
4 の数メートルの差は、5mレーザとDEM10Bという製品の違いによるもの。

それでも現地と合わない場合、原因は座標の計算ではない。次を順に確かめること。

  a) その記録の「位置の確からしさ」  ← まずここを見る
     「小班の代表点」は **木の座標ではない**。小班の重心である。
     森町の該当小班は 0.28〜29.9 ha あり、代表点から小班の縁まで
     51m〜489m ある。つまり木そのものは **数百メートル離れていることがある**。
     オルソで木が特定できたら「オルソ上で位置を直す」または
     「緯度経度を入れて直す」で直すこと。直せば確からしさの表示も変わる。

  b) オルソの絶対位置のずれ
     tools/check_ortho_shift.py で測る。サイトによって 1m〜7m ずれている。

  c) GPSの受信誤差
     樹冠の下では 5〜15m ずれる。ジオグラフィカの表示精度も確認すること。
     度分秒表示は 0.1秒（約3m）、度分表示は 0.001分（約2m）までしか出ない。""")


if __name__ == '__main__':
    main()
