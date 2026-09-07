# -*- coding: utf-8 -*-
"""人が入力したデータを、まとめて別の場所に控える。

Git に入れてあるのはコードと文書だけ。**現地調査の記録と写真は Git に入っていない。**
（写真は数が増えるとリポジトリが実用に耐えなくなるため。個人が写り込む
 可能性もあるので、公開の経路に載せない方が安全でもある。）

したがって、この2つは自分で控える必要がある。

  data/survey.db   現地調査の記録。**ここにしか無い。**
  data/photos/     現地写真。**ここにしか無い。**

再生成できるもの（forest.db, tiles, basemap, dem_cache）は控えない。
オルソと tools/build_forest.py から作り直せる。

使い方
    python tools/backup.py                       既定の控え先に保存
    python tools/backup.py "D:\\ナラ枯れ控え"      場所を指定
    python tools/backup.py --list                これまでの控えを見る

控えは日付つきのフォルダに入るので、上書きされない。
月に一度でも実行しておけば、取り違えても戻せる。
"""
from __future__ import annotations
import os, sys, shutil, time, argparse, sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
DEFAULT = os.path.join(ROOT, 'バックアップ')

TARGETS = [
    ('data/survey.db', '現地調査の記録'),
    ('data/photos', '現地写真'),
]


def db_summary(path):
    """控える前に、中身が壊れていないかを見る。"""
    try:
        con = sqlite3.connect('file:%s?mode=ro' % path.replace('\\', '/'), uri=True)
        ok = con.execute('PRAGMA quick_check').fetchone()[0]
        n = con.execute('SELECT COUNT(*) FROM trees').fetchone()[0]
        s = con.execute('SELECT COUNT(*) FROM surveys').fetchone()[0]
        p = con.execute('SELECT COUNT(*) FROM photos').fetchone()[0]
        con.close()
        return ok, n, s, p
    except sqlite3.Error as e:
        return str(e), 0, 0, 0


def human(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return '%.1f %s' % (n, u)
        n /= 1024.0
    return '%.1f TB' % n


def dir_size(p):
    t = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                t += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return t


def main():
    ap = argparse.ArgumentParser(description='現地調査の記録と写真を控える')
    ap.add_argument('dest', nargs='?', default=None,
                    help='控え先のフォルダ（省略すると ナラ枯れビューアー/バックアップ/）')
    ap.add_argument('--list', action='store_true', help='これまでの控えを見るだけ')
    a = ap.parse_args()

    dest_root = a.dest or DEFAULT

    if a.list:
        if not os.path.isdir(dest_root):
            print('控えはまだありません: %s' % dest_root)
            return
        for d in sorted(os.listdir(dest_root)):
            p = os.path.join(dest_root, d)
            if os.path.isdir(p):
                print('%-22s %s' % (d, human(dir_size(p))))
        return

    db = os.path.join(ROOT, 'data', 'survey.db')
    if not os.path.exists(db):
        sys.exit('data/survey.db が見つかりません。')

    ok, n, ns, np_ = db_summary(db)
    print('控える前の確認')
    print('  データベース : %s' % ('問題なし' if ok == 'ok' else '★ ' + str(ok)))
    print('  記録         : %d 本' % n)
    print('  調査の記録   : %d 件' % ns)
    print('  写真         : %d 枚' % np_)
    if ok != 'ok':
        sys.exit('データベースに問題があります。控えを取らずに中止しました。')

    stamp = time.strftime('%Y%m%d_%H%M')
    out = os.path.join(dest_root, stamp)
    os.makedirs(out, exist_ok=True)

    total = 0
    for rel, what in TARGETS:
        src = os.path.join(ROOT, rel.replace('/', os.sep))
        if not os.path.exists(src):
            print('  %-16s ありません（とばします）' % what)
            continue
        dst = os.path.join(out, os.path.basename(src))
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
            sz = dir_size(dst)
        else:
            shutil.copy2(src, dst)
            sz = os.path.getsize(dst)
        total += sz
        print('  %-16s %s' % (what, human(sz)))

    with open(os.path.join(out, 'この控えについて.txt'), 'w', encoding='utf-8') as f:
        f.write('ナラ枯れビューアー のデータ控え\n')
        f.write('作成 %s\n\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
        f.write('  survey.db  現地調査の記録  %d 本 / 調査 %d 件 / 写真 %d 枚\n'
                % (n, ns, np_))
        f.write('  photos/    現地写真\n\n')
        f.write('戻し方\n')
        f.write('  ナラ枯れビューアーを閉じてから、この2つを\n')
        f.write('  ナラ枯れビューアー/data/ に上書きコピーしてください。\n')
        f.write('  forest.db・tiles・basemap は入っていません。\n')
        f.write('  それらは「初期設定（最初に一度だけ）.bat」と\n')
        f.write('  オルソの取り込みで作り直せます。\n')

    print()
    print('控えました: %s' % out)
    print('合計 %s' % human(total))
    print()
    print('この控えは、できれば別のパソコンか外付けの媒体にも置いてください。')
    print('同じパソコンの中だけだと、故障したときに一緒に失われます。')


if __name__ == '__main__':
    main()
