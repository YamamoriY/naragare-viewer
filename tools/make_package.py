# -*- coding: utf-8 -*-
"""役場に渡す一式を作る。

■ 何を作るのか

  ZIPを1つ作る。相手はそれを好きな場所に展開して、
  中の「起動.bat」をダブルクリックするだけ。**インストールは要らない。**

  Python がPCに入っていなくても動くように、**埋め込み版のPythonを同梱する**。
  役場のPCは管理者権限が無くてソフトを入れられないことが多いため。
  埋め込み版はフォルダに置くだけで動き、レジストリも環境変数も触らない。
  （Python は PSF ライセンスで再配布が認められている。LICENSE も同梱する）

■ 何が入るのか

  コードと文書                     1 MB
  data/survey.db                   現地調査の記録
  data/photos/                     現地写真
  data/forest.db, layers/          林班・小班・森林調査簿
  data/tiles/                      オルソの地図タイル（いちばん大きい）
  data/basemap/, dem_cache/        背景地図と標高の控え
  python/                          埋め込み版Python ＋ numpy ＋ Pillow

  data/_src/（ダウンロードした元zip）は入れない。要るときに取り直せる。

■ 使い方

    python tools/make_package.py                一式（既定）
    python tools/make_package.py --update       コードと文書だけ（データを上書きしない）
    python tools/make_package.py --sample       調査記録を空にした見本
    python tools/make_package.py --no-tiles     オルソのタイルを入れない（軽い）
    python tools/make_package.py --no-python    同梱Pythonを入れない

■ 渡し方

  できたZIPは 納品/ に入る。USBメモリに入れて持っていくのがいちばん確実。
  詳しくは docs/納品のしかた.md を読むこと。
"""
from __future__ import annotations
import os, sys, io, shutil, time, zipfile, argparse, subprocess, sqlite3, tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, '納品')
PYVER = '3.12.10'
PYURL = 'https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip' % (PYVER, PYVER)
CACHE = os.path.join(ROOT, 'data', '_src')

# 相手のPCに置くもの
CODE = [
    'server.py',
    'app',
    'tools',
    'docs',
    '起動.bat',
    'オルソを取り込む.bat',
    '初期設定（最初に一度だけ）.bat',
    'バックアップ.bat',
    '納品セットを作る.bat',
    'はじめにお読みください.txt',
    'README.md',
]
DATA_ALWAYS = ['survey.db', 'forest.db', 'layers', 'photos']
DATA_HEAVY = ['tiles', 'basemap', 'dem_cache']

# 圧縮しても縮まないもの（時間の無駄なのでそのまま入れる）
STORED = ('.webp', '.jpg', '.jpeg', '.png', '.zip', '.whl')

SKIP_DIRS = {'__pycache__', '.git', '.venv', 'venv', '.idea', '.vscode'}
SKIP_FILES = {'desktop.ini', 'Thumbs.db', '.DS_Store', '_import.log'}


def human(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return '%.1f %s' % (n, u)
        n /= 1024.0
    return '%.1f TB' % n


def longpath(p):
    """Windows の 260文字制限を外す。numpy の中は階層が深い。"""
    if os.name == 'nt':
        p = os.path.abspath(p)
        if not p.startswith('\\\\?\\'):
            return '\\\\?\\' + p
    return p


def rmtree(path, tries=5):
    """作業フォルダを消す。OneDrive や検索の索引が一瞬つかんでいて
    「アクセスが拒否されました」になることがあるので、少し待って何度か試す。"""
    for i in range(tries):
        if not os.path.exists(path):
            return True
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return True
        shutil.rmtree(longpath(path), ignore_errors=True)
        if not os.path.exists(path):
            return True
        time.sleep(0.6 * (i + 1))
    return not os.path.exists(path)


def walk(src):
    """入れてよいファイルだけを (絶対パス, 相対パス) で返す"""
    if os.path.isfile(src):
        yield src, os.path.basename(src)
        return
    base = os.path.dirname(src)
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES or f.endswith(('.pyc', '.db-wal', '.db-shm')):
                continue
            p = os.path.join(root, f)
            yield p, os.path.relpath(p, base)


# ------------------------------------------------------- 埋め込みPythonを作る
def build_python(stage, with_libs=True):
    """埋め込み版Pythonを stage/python/ に用意する。

    そのままでは site-packages を見ないので ._pth を書き換え、
    ホスト側の pip で numpy と Pillow を入れる
    （同じ 3.12 / win_amd64 なので、そのまま動く車輪が落ちてくる）。"""
    os.makedirs(CACHE, exist_ok=True)
    zp = os.path.join(CACHE, os.path.basename(PYURL))
    if not os.path.exists(zp):
        print('  埋め込み版Pythonを取ってきます … %s' % PYURL)
        req = urllib.request.Request(PYURL, headers={'User-Agent': 'naragare-viewer'})
        with urllib.request.urlopen(req, timeout=120) as r, open(zp, 'wb') as f:
            shutil.copyfileobj(r, f)
    pydir = os.path.join(stage, 'python')
    os.makedirs(pydir, exist_ok=True)
    with zipfile.ZipFile(zp) as z:
        z.extractall(pydir)

    # ._pth を書き換えて、同梱ライブラリとアプリ本体を見えるようにする
    for name in os.listdir(pydir):
        if name.endswith('._pth'):
            p = os.path.join(pydir, name)
            txt = io.open(p, encoding='utf-8').read()
            txt = txt.replace('#import site', 'import site')
            if 'Lib\\site-packages' not in txt:
                txt = txt.rstrip() + '\nLib\\site-packages\n..\n'
            io.open(p, 'w', encoding='utf-8', newline='\n').write(txt)

    if not with_libs:
        return pydir

    site = os.path.join(pydir, 'Lib', 'site-packages')
    os.makedirs(site, exist_ok=True)
    print('  numpy と Pillow を同梱します（オルソの取り込みに要る）…')
    r = subprocess.run([sys.executable, '-m', 'pip', 'install',
                        '--target', site, '--no-compile',
                        '--only-binary', ':all:', 'numpy', 'pillow'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print('  ★ 入れられませんでした。取り込み機能は相手のPCで使えません。')
        if r.stderr:
            print('    ' + r.stderr.strip().splitlines()[-1])
        return pydir

    # numpy の試験用ファイルは要らない。数十MBあるうえ、
    # 深い階層で Windows のパス長 260 文字にひっかかる。
    cut = 0
    for root, dirs, files in os.walk(site, topdown=False):
        base = os.path.basename(root)
        if base in ('tests', 'testing', '__pycache__'):
            n = sum(len(f) for _, _, f in os.walk(root))
            shutil.rmtree(root, ignore_errors=True)
            cut += n
    n = sum(1 for _ in walk(site))
    print('  同梱しました（%d ファイル / 試験用 %d ファイルを除外）' % (n, cut))
    return pydir


def registered_sites():
    """survey.db に登録されているオルソの名前。
    data/tiles/ に残っている過去の試作を渡さないため。"""
    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    import db as dbmod
    con = dbmod.connect()
    out = {r[0] for r in con.execute(
        'SELECT id FROM sites WHERE zmax IS NOT NULL')}
    con.close()
    return out


# ------------------------------------------------------------------ 見本を作る
def make_sample_db(src, dst):
    """調査記録を空にした見本を作る。外に見せるとき用。
    位置・林班・小班は残し、人が入力したものだけを消す。"""
    shutil.copy2(src, dst)
    con = sqlite3.connect(dst)
    con.execute('DELETE FROM surveys')
    con.execute('DELETE FROM photos')
    con.execute('DELETE FROM history')
    cols = ['survey_date', 'surveyor', 'species', 'dbh_cm', 'leaf_color',
            'dieback', 'boring', 'frass', 'stand', 'misjudge_reason',
            'access_note', 'treatment', 'treatment_date', 'memo',
            'owner_status', 'owner_note', 'contractor', 'fumigant',
            'fumigant_amount', 'checked_by', 'checked_date', 'fiscal_year']
    have = [r[1] for r in con.execute('PRAGMA table_info(trees)')]
    sets = ','.join('%s=NULL' % c for c in cols if c in have)
    con.execute('UPDATE trees SET %s, status="unsurveyed", work_status="none"' % sets)
    con.execute('VACUUM')
    con.commit()
    con.close()


# -------------------------------------------------------------- 読む人向けの紙
READ_ME = """ナラ枯れビューアー（北海道茅部郡森町）

■ 使いはじめる

  1. このフォルダを、Cドライブなど**書き込みできる場所**に置いてください。
     （USBメモリのまま動かすと遅くなります）
  2. 「起動.bat」をダブルクリックしてください。
  3. 黒い画面が出たあと、ブラウザが開きます。

  インストールは要りません。管理者権限も要りません。
  このフォルダの外には何も書き込みません。レジストリも触りません。
  Python はこのフォルダの中（python\\）に同梱してあります。

■ 終わるとき

  黒い画面を閉じてください。

■ 大事なファイル

  data\\survey.db    現地調査の記録。**ここにしかありません。**
  data\\photos\\      現地写真。**ここにしかありません。**

  この2つは「バックアップ.bat」で控えを取ってください。月に一度で十分です。
  ほかのもの（地図タイル・林班データ・背景地図）は作り直せます。

■ 通信について

  地図と標高は、このフォルダの中に控えてあります。
  インターネットに繋がらなくても動きます。
  控えに無い範囲を見たときだけ、国土地理院のタイルを取りに行きます。

■ 詳しい使い方

  docs\\運用マニュアル.md          使う人向け
  docs\\背景と設計.md              なぜこの作りにしたか
  docs\\位置合わせと精度.md        位置はどれくらい信用できるか
  docs\\引き継ぎ.md                直す人向け

■ うまく動かないとき

  「起動.bat」を右クリック →「編集」で中身を見ると、何をしているか分かります。
  黒い画面に出たメッセージを、そのまま担当者に伝えてください。

作成日: {date}
"""


def main():
    ap = argparse.ArgumentParser(description='役場に渡す一式を作る')
    ap.add_argument('--update', action='store_true',
                    help='コードと文書だけ（相手のデータを上書きしない）')
    ap.add_argument('--sample', action='store_true', help='調査記録を空にした見本')
    ap.add_argument('--no-tiles', action='store_true', help='オルソのタイルを入れない')
    ap.add_argument('--no-python', action='store_true', help='同梱Pythonを入れない')
    ap.add_argument('-o', '--out', default=None, help='出力先のフォルダ')
    a = ap.parse_args()

    stamp = time.strftime('%Y%m%d')
    kind = 'コードのみ' if a.update else ('見本' if a.sample else '一式')
    name = 'ナラ枯れビューアー_%s_%s' % (kind, stamp)
    out_dir = a.out or OUT
    os.makedirs(out_dir, exist_ok=True)
    # 作業フォルダは OneDrive の外に作る。
    # このプロジェクトは OneDrive の中にあるので、ここで2万5千個のファイルを
    # 作ると OneDrive が同期を始めてファイルをつかみ、消せなくなる。
    # 最後にできる ZIP だけを 納品/ に置く。
    stage = tempfile.mkdtemp(prefix='naragare_pkg_')

    print('作るもの: %s' % name)
    print()

    # --- コードと文書 ---
    print('コードと文書を集めています…')
    for rel in CODE:
        src = os.path.join(ROOT, rel)
        if not os.path.exists(src):
            continue
        dst = os.path.join(stage, rel)
        if os.path.isdir(src):
            shutil.copytree(src, dst,
                            ignore=shutil.ignore_patterns(*SKIP_DIRS, '*.pyc'))
        else:
            shutil.copy2(src, dst)

    # --- データ ---
    if not a.update:
        os.makedirs(os.path.join(stage, 'data'), exist_ok=True)
        want = list(DATA_ALWAYS) + ([] if a.no_tiles else DATA_HEAVY)
        for rel in want:
            src = os.path.join(ROOT, 'data', rel)
            if not os.path.exists(src):
                continue
            dst = os.path.join(stage, 'data', rel)
            if rel == 'survey.db' and a.sample:
                print('  survey.db … 調査記録を空にした見本を作ります')
                make_sample_db(src, dst)
                continue
            if rel == 'photos' and a.sample:
                os.makedirs(dst, exist_ok=True)
                continue
            print('  %s …' % rel)
            if rel == 'tiles':
                # data/tiles/ には過去の試作の残り（siteB_test など）が入っている。
                # survey.db に登録されているサイトだけを入れる。
                keep = registered_sites()
                os.makedirs(dst, exist_ok=True)
                for s in sorted(os.listdir(src)):
                    if s not in keep:
                        print('     %s は登録されていないので入れません' % s)
                        continue
                    shutil.copytree(os.path.join(src, s), os.path.join(dst, s),
                                    dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns(*SKIP_FILES))
                continue
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(*SKIP_FILES))
            else:
                shutil.copy2(src, dst)
        # オルソの投入口（空でも作っておく）
        d = os.path.join(stage, 'オルソ投入')
        os.makedirs(d, exist_ok=True)
        io.open(os.path.join(d, 'ここにオルソを入れてください.txt'), 'w',
                encoding='utf-8').write(
            'ここにオルソ（.tif / .jpg）とワールドファイルを入れて、\n'
            '「オルソを取り込む.bat」を実行してください。\n')

    # --- 埋め込みPython ---
    if not a.no_python:
        print('Python を同梱しています…')
        build_python(stage, with_libs=not a.update)

    # --- 読む人向けの紙 ---
    io.open(os.path.join(stage, 'はじめにお読みください.txt'), 'w',
            encoding='utf-8', newline='\r\n').write(
        READ_ME.format(date=time.strftime('%Y年%m月%d日')))

    # --- ZIP ---
    print()
    print('ZIP にまとめています…')
    zip_path = os.path.join(out_dir, name + '.zip')
    if os.path.exists(zip_path):
        os.remove(zip_path)
    # 作業フォルダ自身は名前を含めない（展開したとき一段深くならないように）
    files = [(p, os.path.relpath(p, stage)) for p, _ in walk(stage)]
    total = len(files)
    done = raw = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for p, rel in files:
            arc = os.path.join(name, rel).replace('\\', '/')
            ct = zipfile.ZIP_STORED if p.lower().endswith(STORED) else zipfile.ZIP_DEFLATED
            z.write(longpath(p), arc, compress_type=ct)
            raw += os.path.getsize(longpath(p))
            done += 1
            if done % 2000 == 0 or done == total:
                print('  %d / %d' % (done, total))
                sys.stdout.flush()

    if not rmtree(stage):
        print('  （作業フォルダ %s が残りました。手で消してください）' % stage)
    sz = os.path.getsize(zip_path)
    print()
    print('できました')
    print('  %s' % zip_path)
    print('  %s（中身 %s / %d ファイル）' % (human(sz), human(raw), total))
    print()
    if a.update:
        print('これはコードと文書だけです。相手のPCで、data と python を残したまま')
        print('上書き展開してください。調査の記録は消えません。')
    elif a.sample:
        print('これは調査記録を空にした見本です。外部に見せるときに使ってください。')
    else:
        print('これは実際の調査記録が入った一式です。渡す相手を確かめてください。')
    print()
    print('渡し方は docs/納品のしかた.md を読んでください。')


if __name__ == '__main__':
    main()
