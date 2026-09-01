# -*- coding: utf-8 -*-
"""自動抽出した候補木（source='ai'）を消す。

候補木の抽出は別のアルゴリズムで行い、後からマージする運用のためのもの。
現地調査の記録が入っているもの（ステータスが未調査以外、メモや写真があるもの）は
既定では残す。--all を付けると、それも含めて全部消す。

  python tools/purge_candidates.py                 全サイトの候補木を消す
  python tools/purge_candidates.py --site siteB    サイトを指定して消す
  python tools/purge_candidates.py --dry-run       消す前に件数だけ見る
  python tools/purge_candidates.py --all           調査済みのものも消す
"""
from __future__ import annotations
import os, sys, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

KEEP_COND = ('(status<>"unsurveyed" OR (memo IS NOT NULL AND memo<>"") '
             'OR id IN (SELECT tree_id FROM photos))')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--site', help='対象サイト（省略時は全部）')
    ap.add_argument('--source', default='ai', help="対象の登録元（既定 'ai'）")
    ap.add_argument('--all', action='store_true', help='調査記録があるものも消す')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    con = dbmod.connect()
    where = 'source=?'
    params = [args.source]
    if args.site:
        where += ' AND site=?'
        params.append(args.site)

    total = con.execute('SELECT COUNT(*) n FROM trees WHERE ' + where, params).fetchone()['n']
    kept = con.execute('SELECT COUNT(*) n FROM trees WHERE %s AND %s' % (where, KEEP_COND),
                       params).fetchone()['n']

    target_where = where if args.all else '%s AND NOT %s' % (where, KEEP_COND)
    n = con.execute('SELECT COUNT(*) n FROM trees WHERE ' + target_where, params).fetchone()['n']

    print('登録元 "%s"%s' % (args.source, ('／サイト ' + args.site) if args.site else ''))
    print('  対象 %d 件 / うち調査記録あり %d 件' % (total, kept))
    print('  削除する件数: %d' % n)
    if not args.all and kept:
        print('  （調査記録がある %d 件は残します。消すなら --all）' % kept)

    if args.dry_run:
        print('--dry-run のため削除しません。')
        con.close()
        return
    if n == 0:
        con.close()
        return

    ids = [r['id'] for r in con.execute('SELECT id FROM trees WHERE ' + target_where, params)]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ','.join('?' * len(chunk))
        con.execute('DELETE FROM history WHERE tree_id IN (%s)' % q, chunk)
        con.execute('DELETE FROM photos WHERE tree_id IN (%s)' % q, chunk)
        con.execute('DELETE FROM trees WHERE id IN (%s)' % q, chunk)
    con.commit()
    left = con.execute('SELECT COUNT(*) n FROM trees').fetchone()['n']
    con.close()

    import ingest_ortho
    ingest_ortho.write_sites_json()
    print('  削除しました。残りの登録は %d 件です。' % left)


if __name__ == '__main__':
    main()
