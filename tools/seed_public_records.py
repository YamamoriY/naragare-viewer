# -*- coding: utf-8 -*-
"""現地確認済みの記録（振興局・町が整理したもの）を登録する。

出典
  1) 渡島総合振興局が整理した森町のナラ枯れ現地確認結果
  2) 森町「ナラ枯れ被害の防止と森町産ミズナラ資源の活用について」
     https://www.town.hokkaido-mori.lg.jp/soshiki/norin/4319.html

AIが抽出した候補（source='ai'）と区別するため source='survey' で登録する。

【位置について】
  公表・整理されているのは林班-小班までで、木そのものの座標は無い。
  そのため位置は「その小班の代表点（重心）」であり、木の位置ではない。
  loc_accuracy にその旨を明記している。現地で座標が判明したら画面から直すこと。

  python tools/seed_public_records.py
  python tools/seed_public_records.py --list
  python tools/seed_public_records.py --reset   いったん消してから入れ直す
"""
from __future__ import annotations
import os, sys, json, argparse, sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

SITE = 'genchi'
SITE_NAME = '現地確認記録（振興局・町）'
SITE_NOTE = ('渡島総合振興局および森町が整理した現地確認の結果。'
             'オルソからの自動抽出ではなく、人が現地で確認した記録。')

SRC_SHINKO = '渡島総合振興局による森町のナラ枯れ現地確認結果の整理'
SRC_MORI = ('森町農林課「ナラ枯れ被害の防止と森町産ミズナラ資源の活用について」'
            ' https://www.town.hokkaido-mori.lg.jp/soshiki/norin/4319.html')

# status: damaged / clean / pending / unreachable / treated
RECORDS = [
    dict(key='S-1020-2', rinpan='1020', kosyoban='0002',
         status='treated', priority='高',
         species='ミズナラ', dieback='枯死',
         treatment='燻蒸処理（伐倒後 玉切り・薬剤注入・ビニール被覆。伐根も同様に処理）',
         treatment_date='2026-05-13', survey_date='2026-05-13', surveyor='森町農林課',
         stand='周辺にミズナラあり（町有林）',
         memo=('ナラ枯れ現地確認済み。周辺にミズナラあり。当該木は燻蒸処理済み。\n'
               '森町砂原2丁目の町有林。令和8年5月13日、森町で確認された被害木。\n'
               '出典: ' + SRC_SHINKO + ' / ' + SRC_MORI)),

    dict(key='S-121-1', rinpan='0121', kosyoban='0001',
         status='clean', priority='低', misjudge_reason='原因不明',
         memo='ナラ枯れではない。枯れの原因は不明。\n出典: ' + SRC_SHINKO),

    dict(key='S-1020-94', rinpan='1020', kosyoban='0094',
         status='clean', priority='低', misjudge_reason='つる植物の枯れ',
         memo=('ナラ枯れではない。シラカンバに巻き付いているツルが枯れていたもの。\n'
               '出典: ' + SRC_SHINKO)),

    dict(key='S-43-85', rinpan='0043', kosyoban='0085',
         status='clean', priority='低', misjudge_reason='根むくれによる枯れ',
         memo='ナラ枯れではない。根むくれによる枯れ。\n出典: ' + SRC_SHINKO),

    dict(key='S-105-328', rinpan='0105', kosyoban='0328',
         status='clean', priority='低', misjudge_reason='根むくれによる枯れ',
         memo='ナラ枯れではない。根むくれによる枯れ。\n出典: ' + SRC_SHINKO),

    dict(key='S-121-12', rinpan='0121', kosyoban='0012',
         status='clean', priority='低', misjudge_reason='原因不明',
         memo='ナラ枯れではない。枯れの原因は不明。\n出典: ' + SRC_SHINKO),

    dict(key='S-1007-50', rinpan='1007', kosyoban='0050',
         status='unreachable', priority='高',
         access_note='笹薮を1.5km以上進む必要があり到達できず',
         memo=('現地調査したが到達不能。1.5km以上の笹薮。\n'
               'この小班はドローンオルソ（Site B）の範囲内にあり、'
               '上空からの確認で代替できる。\n出典: ' + SRC_SHINKO)),

    dict(key='S-3-130', rinpan='0003', kosyoban='0130',
         status='unreachable', priority='高',
         access_note='高低差100mの急斜面のため到達できず',
         memo=('現地調査したが到達不能。高低差100mの急斜面。\n'
               'この小班はドローンオルソ（Site A）の範囲内にあり、'
               '上空からの確認で代替できる。\n出典: ' + SRC_SHINKO)),

    dict(key='S-105-146', rinpan='0105', kosyoban='0146',
         status='unreachable', priority='高',
         access_note='道路から3km。熊の足跡があり安全確保できず到達できず',
         memo=('現地調査したが到達不能。熊の足跡ありのため、道路から3km地点へは入れず。\n'
               'この小班はドローンオルソ（Site C）の範囲内にあり、'
               '上空からの確認で代替できる。\n出典: ' + SRC_SHINKO)),

    dict(key='S-105-188', rinpan='0105', kosyoban='0188',
         status='unreachable', priority='高',
         access_note='道路から1.5km。熊の足跡があり安全確保できず到達できず',
         memo=('現地調査したが到達不能。熊の足跡ありのため、道路から1.5km地点へは入れず。\n'
               'この小班はドローンオルソ（Site C）の範囲内にあり、'
               '上空からの確認で代替できる。\n出典: ' + SRC_SHINKO)),

    # 位置が特定できない記録。地図には出ないが、記録として残す。
    dict(key='S-kokuyu', rinpan=None, kosyoban=None,
         status='unreachable', priority='中',
         access_note='国有林。民有林の小班に該当せず、位置が特定できていない',
         memo=('現地調査したが到達不能。国有林のため民有林の森林調査簿に小班が無く、'
               '位置を特定できていない。\n'
               '国有林野の林小班データを入れれば位置を確定できる。\n出典: ' + SRC_SHINKO)),
]


def lookup(con_f, rinpan, kosyoban):
    if not rinpan:
        return None
    r = con_f.execute(
        'SELECT lon,lat,x,y,chiku,sp_main,nara_rank,chiban,elev,area_ha '
        'FROM kosyoban WHERE rinpan=? AND kosyoban=?',
        (rinpan.zfill(4), kosyoban.zfill(4))).fetchone()
    return dict(r) if r else None


def covering_site(con_s, lon, lat):
    if lon is None:
        return None
    for r in con_s.execute('SELECT id,name,minlon,minlat,maxlon,maxlat FROM sites '
                           'WHERE minlon IS NOT NULL'):
        if r['minlon'] <= lon <= r['maxlon'] and r['minlat'] <= lat <= r['maxlat']:
            return r['id']
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--reset', action='store_true', help='既存の記録を消してから入れ直す')
    args = ap.parse_args()

    if args.list:
        for r in RECORDS:
            print('%-12s %-9s %-12s %s' % (
                r['key'],
                ('%s-%s' % (r['rinpan'].lstrip('0'), r['kosyoban'].lstrip('0')))
                if r['rinpan'] else '（位置不明）',
                dbmod.STATUS_LABEL.get(r['status'], r['status']),
                (r.get('access_note') or r.get('misjudge_reason') or r.get('treatment') or '')[:40]))
        return

    if not os.path.exists(dbmod.FOREST_DB):
        raise SystemExit('data/forest.db がありません。先に tools/build_forest.py を実行してください。')

    con = dbmod.connect()
    conf = sqlite3.connect(dbmod.FOREST_DB)
    conf.row_factory = sqlite3.Row
    now = dbmod.now()

    if args.reset:
        ids = [r['id'] for r in con.execute('SELECT id FROM trees WHERE site IN (?,?)',
                                            (SITE, 'public'))]
        for tid in ids:
            con.execute('DELETE FROM history WHERE tree_id=?', (tid,))
        con.execute('DELETE FROM trees WHERE site IN (?,?)', (SITE, 'public'))
        con.execute('DELETE FROM sites WHERE id=?', ('public',))
        print('既存の記録 %d 件を削除しました。' % len(ids))

    # 旧バージョンで作った 'public' サイトがあれば片付ける
    old = con.execute('SELECT COUNT(*) c FROM trees WHERE site=?', ('public',)).fetchone()['c']
    if old and not args.reset:
        print('[!] 旧形式の記録が %d 件あります。--reset を付けて入れ直してください。' % old)

    if not con.execute('SELECT 1 FROM sites WHERE id=?', (SITE,)).fetchone():
        con.execute('INSERT INTO sites(id,name,note,created_at,updated_at) VALUES (?,?,?,?,?)',
                    (SITE, SITE_NAME, SITE_NOTE, now, now))
    else:
        con.execute('UPDATE sites SET name=?, note=?, updated_at=? WHERE id=?',
                    (SITE_NAME, SITE_NOTE, now, SITE))

    added = updated = 0
    covered = []
    for r in RECORDS:
        ko = lookup(conf, r.get('rinpan'), r.get('kosyoban'))
        if r.get('rinpan') and not ko:
            print('[!] 小班が見つかりません: %s-%s（%s）'
                  % (r['rinpan'], r['kosyoban'], r['key']))
            continue

        site_hit = covering_site(con, ko['lon'], ko['lat']) if ko else None
        if site_hit and r['status'] == 'unreachable':
            covered.append((r['key'], site_hit))

        memo = r['memo'] + '\n［整理番号 %s］' % r['key']
        f = dict(
            site=SITE,
            rinpan=(r['rinpan'].zfill(4) if r.get('rinpan') else None),
            kosyoban=(r['kosyoban'].zfill(4) if r.get('kosyoban') else None),
            status=r['status'], priority=r.get('priority', '中'),
            survey_date=r.get('survey_date'), surveyor=r.get('surveyor', '渡島総合振興局'),
            species=r.get('species'), dieback=r.get('dieback'), stand=r.get('stand'),
            misjudge_reason=r.get('misjudge_reason'), access_note=r.get('access_note'),
            treatment=r.get('treatment'), treatment_date=r.get('treatment_date'),
            memo=memo, source='survey', updated_at=now,
            loc_accuracy=('小班の代表点（木そのものの座標は整理されていない）' if ko
                          else '位置未特定（国有林のため民有林の小班に該当なし）'),
        )
        if ko:
            f.update(lon=ko['lon'], lat=ko['lat'], x=ko['x'], y=ko['y'],
                     chiku=ko['chiku'], sp_main=ko['sp_main'],
                     nara_rank=ko['nara_rank'], chiban=ko['chiban'], elev=ko['elev'])

        cur = con.execute("SELECT id FROM trees WHERE site=? AND memo LIKE ?",
                          (SITE, '%［整理番号 ' + r['key'] + '］%')).fetchone()
        if cur:
            con.execute('UPDATE trees SET %s WHERE id=?' % ','.join('%s=?' % k for k in f),
                        list(f.values()) + [cur['id']])
            updated += 1
        else:
            f['code'] = dbmod.next_code(con, SITE)
            f['created_at'] = now
            con.execute('INSERT INTO trees(%s) VALUES (%s)'
                        % (','.join(f), ','.join('?' * len(f))), list(f.values()))
            added += 1

    con.commit()
    con.close()
    conf.close()

    import ingest_ortho
    ingest_ortho.write_sites_json()

    print('現地確認記録: 追加 %d 件 / 更新 %d 件' % (added, updated))
    if covered:
        print('')
        print('■ 到達できなかった地点のうち、オルソの範囲に入っているもの')
        for k, s in covered:
            print('   %-12s -> %s' % (k, s))
        print('   これらは上空からの確認で代替できます。')


if __name__ == '__main__':
    main()
