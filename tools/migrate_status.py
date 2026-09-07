# -*- coding: utf-8 -*-
"""ステータスを2軸に分け、既存の調査結果を surveys テーブルに写す。

【なぜ要るか】
これまでステータスは1つの欄で
  未調査 / 被害あり / 被害なし / 判定保留 / 到達できず / 処理済
を持っていた。これだと「被害あり」を「処理済」に変えたとたん
**「被害ありだった」という事実が消える**。
「被害あり・所有者確認中」「被害あり・処理待ち」も表せない。

そこで
  status       … 調査ステータス（現地で何と判定したか）
  work_status  … 処理ステータス（伐倒・くん蒸をどこまで進めたか）
の2軸に分けた。この道具は、その移行を1回だけ行う。

また、調査日・調査者が1組しかなかったので、
「再度立ち合い調査」を記録すると1回目が消えていた。
既存の調査内容を surveys テーブルの1回目として写し、
以後は何回でも積めるようにする。

【移行の内容】
  旧 status      新 status      work_status
  ------------   ------------   ------------
  unsurveyed  →  unsurveyed     none
  clean       →  clean          none
  pending     →  pending        none
  unreachable →  unreachable    none
  damaged     →  damaged        waiting      （被害あり＝これから処理する）
  treated     →  damaged        done         （処理済＝被害ありだったはず）

  land_class … 民有林の小班に入っていれば「民有林」。
               「位置の確からしさ」に国有林と書いてあれば「国有林」。
               どちらでもなければ空のまま。
  owner_status … **推測しない。** 空（未着手）のままにする。
               処理済の木でも、同意の有無は記録に無いため。

何度実行しても結果は変わらない（移行済みのものは飛ばす）。

使い方
    python tools/migrate_status.py --dry-run   # 何が変わるか見るだけ
    python tools/migrate_status.py             # 実行
"""
from __future__ import annotations
import os, sys, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

# 旧コード -> (新 status, work_status)
MAP = {
    'unsurveyed':  ('unsurveyed',  'none'),
    'clean':       ('clean',       'none'),
    'pending':     ('pending',     'none'),
    'unreachable': ('unreachable', 'none'),
    'damaged':     ('damaged',     'waiting'),
    'treated':     ('damaged',     'done'),
}


def land_class_of(r):
    if r['land_class']:
        return r['land_class']
    if '国有林' in (r['loc_accuracy'] or ''):
        return '国有林'
    if r['kosyoban']:
        return '民有林'          # 森林調査簿は民有林のみなので、入っていれば民有林
    return ''


MARK = 'status2_migrated'


def has_survey_content(r):
    """1回でも現地を見た形跡があるか。
    中身が何も無いものは、ステータスが付いていても調査記録は作らない
    （地図でタップして「判定保留」にしただけ、という状態があるため）。"""
    for f in ('survey_date', 'surveyor', 'species', 'dbh_cm', 'leaf_color',
              'dieback', 'boring', 'frass', 'stand', 'misjudge_reason',
              'access_note'):
        if r[f] not in (None, ''):
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description='ステータスを2軸に分け、調査を surveys に写す')
    ap.add_argument('--dry-run', action='store_true', help='書き換えずに内容だけ出す')
    ap.add_argument('--force', action='store_true', help='移行済みでもやり直す')
    a = ap.parse_args()

    con = dbmod.connect()
    done = con.execute('SELECT v FROM meta WHERE k=?', (MARK,)).fetchone()
    if done and not a.force and not a.dry_run:
        print('すでに移行済みです（%s）。やり直すなら --force を付けてください。' % done['v'])
        return
    if done:
        print('※ すでに %s に移行済み。以下は %s の結果です。\n'
              % (done['v'], '確認のみ' if a.dry_run else 'やり直し'))
    rows = con.execute('SELECT * FROM trees ORDER BY code').fetchall()

    print('%-11s %-12s -> %-12s %-10s %-8s %s'
          % ('記録', '旧ステータス', '調査', '処理', '所管', '調査記録'))
    print('-' * 78)

    n_stat = n_visit = 0
    for r in rows:
        old = r['status'] or 'unsurveyed'
        new_st, new_wk = MAP.get(old, (old, 'none'))
        lc = land_class_of(r)

        st_note = '  ★調査ステータスが変わります' if new_st != old else ''

        # 調査記録
        have = con.execute('SELECT COUNT(*) c FROM surveys WHERE tree_id=?',
                           (r['id'],)).fetchone()['c']
        mk_visit = (have == 0 and has_survey_content(r))

        print('%-11s %-12s -> %-12s %-10s %-8s %s%s'
              % (r['code'], old, new_st, new_wk, lc or '—',
                 '1回目を作る' if mk_visit else ('%d件あり' % have if have else 'なし'),
                 st_note))

        if a.dry_run:
            continue

        sets, args = [], []
        if new_st != old:
            dbmod.log_change(con, r['id'], '移行', 'status', old, new_st)
        if new_wk != (r['work_status'] or 'none'):
            dbmod.log_change(con, r['id'], '移行', 'work_status',
                             r['work_status'] or 'none', new_wk)
        sets += ['status=?', 'work_status=?']
        args += [new_st, new_wk]
        n_stat += 1
        if lc and not r['land_class']:
            sets.append('land_class=?')
            args.append(lc)
        if sets:
            con.execute('UPDATE trees SET %s WHERE id=?' % ','.join(sets),
                        args + [r['id']])

        if mk_visit:
            con.execute(
                'INSERT INTO surveys(tree_id, seq, survey_date, surveyor, witness,'
                ' result, species, dbh_cm, leaf_color, dieback, boring, frass,'
                ' stand, misjudge_reason, access_note, sample, note,'
                ' created_at, updated_at) '
                'VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (r['id'], r['survey_date'], r['surveyor'], '', new_st,
                 r['species'], r['dbh_cm'], r['leaf_color'], r['dieback'],
                 r['boring'], r['frass'], r['stand'], r['misjudge_reason'],
                 r['access_note'], '',
                 '既存の記録から自動で作った1回目です',
                 dbmod.now(), dbmod.now()))
            n_visit += 1

    if not a.dry_run:
        con.execute('INSERT OR REPLACE INTO meta(k,v) VALUES (?,?)', (MARK, dbmod.now()))
        con.commit()
        print('-' * 78)
        print('ステータスを移行: %d 件 / 調査記録を作成: %d 件' % (n_stat, n_visit))
        print('所有者確認は推測せず、すべて「未着手」のままにしてあります。')
    else:
        print('-' * 78)
        print('--dry-run なので何も書き換えていません。')
    con.close()


if __name__ == '__main__':
    main()
