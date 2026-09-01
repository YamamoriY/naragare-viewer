# -*- coding: utf-8 -*-
"""サイト（オルソ1枚ぶん）を削除する。

オルソを差し替えるときや、検証用に入れたデータを消すときに使う。
現地調査の記録が入っている候補木がある場合は、既定では消さずに警告する。

  python tools/remove_site.py                    サイト一覧を表示
  python tools/remove_site.py siteB_ref          削除（調査記録があれば中止）
  python tools/remove_site.py siteB_ref --force  調査記録ごと削除
  python tools/remove_site.py siteB_ref --keep-trees   タイルだけ消す
"""
from __future__ import annotations
import os, sys, shutil, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

DATA = dbmod.DATA


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('site', nargs='?')
    ap.add_argument('--force', action='store_true', help='調査記録があっても削除する')
    ap.add_argument('--keep-trees', action='store_true', help='候補木は残してタイルだけ消す')
    args = ap.parse_args()

    con = dbmod.connect()
    if not args.site:
        print('登録されているサイト:')
        for r in con.execute('SELECT * FROM sites ORDER BY id'):
            n = con.execute('SELECT COUNT(*) c FROM trees WHERE site=?', (r['id'],)).fetchone()['c']
            done = con.execute(
                'SELECT COUNT(*) c FROM trees WHERE site=? AND status<>"unsurveyed"',
                (r['id'],)).fetchone()['c']
            print('  %-16s %-34s 候補木 %5d 件（調査済 %d）'
                  % (r['id'], (r['name'] or '')[:32], n, done))
        con.close()
        return

    site = args.site
    row = con.execute('SELECT * FROM sites WHERE id=?', (site,)).fetchone()
    if not row:
        print('そのサイトはありません: %s' % site)
        con.close()
        return
    n = con.execute('SELECT COUNT(*) c FROM trees WHERE site=?', (site,)).fetchone()['c']
    done = con.execute('SELECT COUNT(*) c FROM trees WHERE site=? AND status<>"unsurveyed"',
                       (site,)).fetchone()['c']
    ph = con.execute('SELECT COUNT(*) c FROM photos p JOIN trees t ON p.tree_id=t.id '
                     'WHERE t.site=?', (site,)).fetchone()['c']
    print('サイト %s（%s）' % (site, row['name'] or ''))
    print('  候補木 %d 件 / うち調査済 %d 件 / 写真 %d 枚' % (n, done, ph))

    if not args.keep_trees and (done or ph) and not args.force:
        print('')
        print('[中止] 現地調査の記録が入っています。')
        print('       本当に消す場合は --force を付けてください。')
        print('       タイルだけ消したい場合は --keep-trees を付けてください。')
        con.close()
        return

    tiles = os.path.join(DATA, 'tiles', site)
    if os.path.isdir(tiles):
        shutil.rmtree(tiles, ignore_errors=True)
        print('  タイルを削除: %s' % tiles)

    if not args.keep_trees:
        ids = [r['id'] for r in con.execute('SELECT id FROM trees WHERE site=?', (site,))]
        for tid in ids:
            for p in con.execute('SELECT filename FROM photos WHERE tree_id=?', (tid,)):
                fp = os.path.join(DATA, 'photos', p['filename'])
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
            con.execute('DELETE FROM history WHERE tree_id=?', (tid,))
        con.execute('DELETE FROM trees WHERE site=?', (site,))
        con.execute('DELETE FROM sites WHERE id=?', (site,))
        print('  候補木 %d 件とサイト登録を削除しました。' % n)
    else:
        con.execute('UPDATE sites SET zmin=NULL, zmax=NULL WHERE id=?', (site,))
        print('  候補木は残しました。')
    con.commit()
    con.close()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ingest_ortho
    ingest_ortho.write_sites_json()
    print('完了。')


if __name__ == '__main__':
    main()
