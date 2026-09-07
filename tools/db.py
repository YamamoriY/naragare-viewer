# -*- coding: utf-8 -*-
"""調査データベース（data/survey.db）の定義。

【重要】
  data/survey.db  … 人が入力したデータ。現地調査の結果はここにしか無い。
                    絶対に消さないこと。バックアップの対象はこのファイル。
  data/photos/    … 現地写真。**Git には入れていない。** ここにしか無い。
                    survey.db と一緒に「バックアップ.bat」で控えること。
  data/forest.db  … 北海道オープンデータから作った参照データ。いつでも再生成できる。
  data/tiles/     … オルソのタイル。オルソから再生成できる。

オルソを入れ直しても survey.db の調査記録は保持される（tools/ingest_ortho.py が
既存の候補木と突き合わせ、人が入力した項目には触れない）。
"""
from __future__ import annotations
import os, sqlite3, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
SURVEY_DB = os.path.join(DATA, 'survey.db')
FOREST_DB = os.path.join(DATA, 'forest.db')

# ステータスは2軸で持つ。
#
#   status（調査ステータス）… 現地で見て何と判定したか
#   work_status（処理ステータス）… 伐倒・くん蒸をどこまで進めたか
#
# 1本にまとめていたときは「被害あり」を「処理済」に変えると
# 「被害ありだった」という事実が消えてしまい、
# 「被害あり・処理待ち」と「被害あり・処理済」も区別できなかった。
#
# 白い背景でもはっきり見えるよう、屋外での視認性を優先した色にしてある。
STATUS = [
    ('unsurveyed',  '未調査',            '#6b7684'),
    ('damaged',     '被害あり（ナラ枯れ）', '#c92a2a'),
    ('clean',       '被害なし',           '#2b8a3e'),
    ('pending',     '判定保留',           '#d9480f'),
    ('unreachable', '到達できず',          '#5f3dc4'),
]
STATUS_CODES = [s[0] for s in STATUS]
STATUS_LABEL = {c: l for c, l, _ in STATUS}
STATUS_COLOR = {c: k for c, _, k in STATUS}

# 処理ステータス。被害ありと判定した木だけが対象になる。
WORK = [
    ('none',    '対象外',    '#adb5bd'),
    ('waiting', '処理待ち',   '#e8590c'),
    ('ordered', '発注済',    '#1971c2'),
    ('done',    '処理済',    '#2f9e44'),
]
WORK_CODES = [w[0] for w in WORK]
WORK_LABEL = {c: l for c, l, _ in WORK}
WORK_COLOR = {c: k for c, _, k in WORK}

# 所有者確認。民有林の立木を伐るには所有者の同意が要るので、
# 伐倒に進む前の関門としてステータスとは別に持つ。
OWNER = [
    ('',         '未着手',    '#adb5bd'),
    ('checking', '確認中',    '#f08c00'),
    ('agreed',   '同意取得済', '#2f9e44'),
    ('refused',  '不同意',    '#c92a2a'),
    ('na',       '不要',      '#868e96'),
]
OWNER_CODES = [o[0] for o in OWNER]
OWNER_LABEL = {c: l for c, l, _ in OWNER}

LAND_CLASS = ['', '民有林', '国有林', '不明']

PRIORITY = ['高', '中', '低']

# 現地調査で記録する項目（企画提案書 9-1 のプロット調査項目に対応）
# ここに挙げた列は「人が入力したもの」として扱い、
# オルソを取り込み直しても絶対に上書きしない。
SURVEY_FIELDS = [
    'status', 'survey_date', 'surveyor', 'species', 'dbh_cm',
    'leaf_color', 'dieback', 'boring', 'frass', 'stand',
    'misjudge_reason', 'access_note', 'treatment', 'treatment_date', 'memo',
    # 処理ステータスと、伐倒・くん蒸を進めるための項目
    'work_status', 'owner_status', 'owner_note', 'land_class',
    'contractor', 'volume_m3', 'fumigant', 'fumigant_amount',
    'checked_by', 'checked_date', 'fiscal_year',
]

# 1回の現地調査で記録する項目（surveys テーブル）
VISIT_FIELDS = [
    'survey_date', 'surveyor', 'witness', 'result',
    'species', 'dbh_cm', 'leaf_color', 'dieback', 'boring', 'frass',
    'stand', 'misjudge_reason', 'access_note', 'sample', 'note',
]

# 調査を確定したとき、その内容を trees 側（＝現況）にも写す項目
VISIT_TO_TREE = [
    'survey_date', 'surveyor', 'species', 'dbh_cm', 'leaf_color',
    'dieback', 'boring', 'frass', 'stand', 'misjudge_reason', 'access_note',
]

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS sites(
  id            TEXT PRIMARY KEY,      -- siteA など
  name          TEXT,                  -- 画面に出す名前
  flown_on      TEXT,                  -- 撮影日
  source        TEXT,                  -- 元ファイル名
  tile_ext      TEXT,                  -- webp / png
  zmin          INTEGER,
  zmax          INTEGER,
  minlon REAL, minlat REAL, maxlon REAL, maxlat REAL,
  px_size       REAL,                  -- 元オルソの地上画素寸法[m]
  coverage      REAL,                  -- アルファ>0 の割合
  rinpan        TEXT,                  -- 主に含まれる林班
  note          TEXT,
  detected_at   TEXT,
  created_at    TEXT,
  updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS trees(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  code          TEXT UNIQUE,           -- 候補木ID  例 A-0001
  site          TEXT,
  lon REAL, lat REAL,                  -- WGS84（EPSG:6668相当）
  x   REAL, y   REAL,                  -- 平面直角座標XI系[m]
  elev          REAL,                  -- 地表標高[m] 国土地理院DEM10B
  canopy_h      REAL,                  -- 樹高の参考値[m] DSM-DEM（誤差大）
  rinpan        TEXT,
  kosyoban      TEXT,
  chiku         TEXT,                  -- 旧森町 / 旧砂原町
  sp_main       TEXT,                  -- 森林調査簿の主樹種
  nara_rank     INTEGER,               -- 2:ナラ類確実 1:可能性 0:該当なし
  chiban        TEXT,                  -- 代表地番
  area_m2       REAL,                  -- 検出領域の面積
  score         REAL,                  -- 検出スコア 0..1
  priority      TEXT,                  -- 高/中/低
  -- ここから下は人が入力する項目。取り込み直しても上書きしない。
  status        TEXT DEFAULT 'unsurveyed',
  survey_date   TEXT,
  surveyor      TEXT,
  species       TEXT,                  -- 現地で確認した樹種
  dbh_cm        REAL,                  -- 胸高直径
  leaf_color    TEXT,                  -- 葉の変色
  dieback       TEXT,                  -- 枯死状況
  boring        TEXT,                  -- 穿孔
  frass         TEXT,                  -- フラス量
  stand         TEXT,                  -- 周辺林相
  misjudge_reason TEXT,                -- 誤判別要因
  access_note   TEXT,                  -- 到達できなかった理由 等
  treatment     TEXT,                  -- 処理方法
  treatment_date TEXT,
  memo          TEXT,
  source        TEXT,                  -- ai / manual / public
  loc_accuracy  TEXT,                  -- 位置の確からしさ
  created_at    TEXT,
  updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_tree_site ON trees(site);
CREATE INDEX IF NOT EXISTS ix_tree_status ON trees(status);
CREATE INDEX IF NOT EXISTS ix_tree_rinpan ON trees(rinpan);
CREATE INDEX IF NOT EXISTS ix_tree_xy ON trees(x, y);

-- 現地調査は複数回ある。「1回目は判定保留、2回目に振興局立会で被害確定」
-- という経緯こそがいちばん残すべき情報なので、1回ごとに1行で積む。
-- trees 側の調査項目は「いちばん新しい調査の結果＝現況」を保持する。
CREATE TABLE IF NOT EXISTS surveys(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tree_id       INTEGER NOT NULL REFERENCES trees(id) ON DELETE CASCADE,
  seq           INTEGER,               -- 1回目, 2回目 …
  survey_date   TEXT,
  surveyor      TEXT,                  -- 調査者
  witness       TEXT,                  -- 立会者（振興局担当者など）
  result        TEXT,                  -- そのときの判定（status のコード）
  species       TEXT,
  dbh_cm        REAL,
  leaf_color    TEXT,
  dieback       TEXT,
  boring        TEXT,
  frass         TEXT,
  stand         TEXT,
  misjudge_reason TEXT,
  access_note   TEXT,
  sample        TEXT,                  -- 採取したもの（枝・フラス・虫体・採取番号）
  note          TEXT,
  created_at    TEXT,
  updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_survey_tree ON surveys(tree_id, seq);

CREATE TABLE IF NOT EXISTS photos(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tree_id     INTEGER NOT NULL REFERENCES trees(id) ON DELETE CASCADE,
  filename    TEXT NOT NULL,
  caption     TEXT,
  created_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_photo_tree ON photos(tree_id);

CREATE TABLE IF NOT EXISTS history(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tree_id     INTEGER NOT NULL,
  at          TEXT,
  who         TEXT,
  field       TEXT,
  old         TEXT,
  new         TEXT
);
CREATE INDEX IF NOT EXISTS ix_hist_tree ON history(tree_id);

-- ============================ 同期（スマホ⇄事務所） ============================
-- 端末はオフラインで記録し、電波のあるときにまとめて送る。
-- どの端末で作った記録も uuid で一意に決まるので、
-- 「同じ木が2本に増える」ことが起きない。
--
-- seq は「この行が何番目に変わったか」を表す通し番号（meta.sync_seq を増やして振る）。
-- 端末は「前回どこまで受け取ったか」を seq で覚えておき、その続きだけをもらう。

CREATE TABLE IF NOT EXISTS tracks(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  uuid        TEXT UNIQUE,
  name        TEXT,
  device      TEXT,               -- 記録した端末
  surveyor    TEXT,               -- 歩いた人
  started_at  TEXT,
  ended_at    TEXT,
  dist_m      REAL,               -- 総距離[m]
  dur_s       REAL,               -- 所要[s]
  up_m        REAL,               -- 累積登り[m]
  pt_n        INTEGER,            -- 点の数
  minlon REAL, minlat REAL, maxlon REAL, maxlat REAL,
  color       TEXT,
  note        TEXT,
  points      TEXT,               -- JSON [[lon,lat,elev,t_ms,acc_m], ...]
  deleted     INTEGER DEFAULT 0,
  sseq        INTEGER DEFAULT 0,  -- 同期の通し番号
  created_at  TEXT,
  updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_track_sseq ON tracks(sseq);

CREATE TABLE IF NOT EXISTS devices(
  id          TEXT PRIMARY KEY,   -- 端末が自分で作る ID
  name        TEXT,               -- 「山森のiPhone」など
  who         TEXT,               -- 担当者名
  ua          TEXT,
  first_seen  TEXT,
  last_seen   TEXT,
  last_pull   INTEGER DEFAULT 0,  -- その端末が受け取り終えた seq
  pushed      INTEGER DEFAULT 0,  -- 送ってきた件数（累計）
  note        TEXT
);

-- 送られてきたが採用しなかった変更（時刻が古かった等）。捨てずに残す。
CREATE TABLE IF NOT EXISTS conflicts(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT,
  kind        TEXT,               -- tree / survey / track
  uuid        TEXT,
  device      TEXT,
  who         TEXT,
  field       TEXT,
  kept        TEXT,               -- 採用した値（サーバー側）
  dropped     TEXT,               -- 採用しなかった値（端末側）
  resolved    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_conflict_uuid ON conflicts(uuid);
"""


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def today():
    return time.strftime('%Y-%m-%d')


# 後から足した列。既存の survey.db にも自動で追加する。
MIGRATIONS = [
    ('trees', 'orig_lon', 'REAL'),      # 最初に登録されたときの経度
    ('trees', 'orig_lat', 'REAL'),      # 同 緯度
    ('trees', 'orig_note', 'TEXT'),     # 元の位置が何だったか（小班の代表点 など）
    # --- 処理ステータスと、伐倒・くん蒸に進むための項目 ---
    ('trees', 'work_status', "TEXT DEFAULT 'none'"),
    ('trees', 'owner_status', 'TEXT'),      # 所有者確認（伐倒の関門）
    ('trees', 'owner_note', 'TEXT'),        # 林地台帳の確認結果・連絡先など
    ('trees', 'land_class', 'TEXT'),        # 民有林 / 国有林 / 不明
    ('trees', 'contractor', 'TEXT'),        # 施工者
    ('trees', 'volume_m3', 'REAL'),         # 材積[m3]（補助金・積算に使う）
    ('trees', 'fumigant', 'TEXT'),          # くん蒸剤名
    ('trees', 'fumigant_amount', 'TEXT'),   # 使用量
    ('trees', 'checked_by', 'TEXT'),        # 処理後の確認者
    ('trees', 'checked_date', 'TEXT'),      # 確認日
    ('trees', 'fiscal_year', 'TEXT'),       # 年度（防除事業は年度単位）
    # --- ここから同期用。スマホでオフライン入力したものを取り込むために要る ---
    ('trees', 'uuid', 'TEXT'),              # 端末が作る一意ID（採番をサーバーに頼らない）
    ('trees', 'deleted', 'INTEGER DEFAULT 0'),   # 消したことも同期する（墓標）
    ('trees', 'sseq', 'INTEGER DEFAULT 0'), # 同期の通し番号
    ('trees', 'device', 'TEXT'),            # 最後に触った端末
    ('trees', 'gps_acc', 'REAL'),           # 登録時のGPS精度[m]
    ('trees', 'heading', 'REAL'),           # 登録時に向いていた方位[°]
    ('surveys', 'uuid', 'TEXT'),
    ('surveys', 'deleted', 'INTEGER DEFAULT 0'),
    ('surveys', 'sseq', 'INTEGER DEFAULT 0'),
    ('surveys', 'device', 'TEXT'),
    ('photos', 'uuid', 'TEXT'),
    ('photos', 'deleted', 'INTEGER DEFAULT 0'),
    ('photos', 'sseq', 'INTEGER DEFAULT 0'),
    ('photos', 'device', 'TEXT'),
    ('photos', 'taken_at', 'TEXT'),
    ('photos', 'lon', 'REAL'),
    ('photos', 'lat', 'REAL'),
    ('photos', 'heading', 'REAL'),
    ('photos', 'bytes', 'INTEGER'),
]


# 同期の対象になるテーブル。（テーブル名, 端末が作る uuid を持つか）
SYNC_TABLES = ('trees', 'surveys', 'photos', 'tracks')


def _migrate(con):
    for table, col, typ in MIGRATIONS:
        cols = [r[1] for r in con.execute('PRAGMA table_info(%s)' % table)]
        if col not in cols:
            con.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, col, typ))
    # uuid は「同じ記録が2つに増えない」ための鍵なので、必ず一意にしておく。
    for t in SYNC_TABLES:
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS ux_%s_uuid ON %s(uuid) '
                    'WHERE uuid IS NOT NULL' % (t, t))
        con.execute('CREATE INDEX IF NOT EXISTS ix_%s_sseq ON %s(sseq)' % (t, t))
    _drop_stray_seq(con)
    _ensure_triggers(con)
    con.commit()
    _backfill_uuid(con)
    _backfill_sseq(con)


def _drop_stray_seq(con):
    """開発の途中で付けた seq 列（trees / photos）を落とす。

    surveys.seq は「1回目・2回目」の意味で昔から使っているので触らない。
    同期の通し番号は sseq に統一してある。
    """
    for t in ('trees', 'photos'):
        cols = [r[1] for r in con.execute('PRAGMA table_info(%s)' % t)]
        if 'seq' in cols and 'sseq' in cols:
            # 索引が張られている列は落とせないので、先に索引を消す
            con.execute('DROP INDEX IF EXISTS ix_%s_seq' % t)
            try:
                con.execute('ALTER TABLE %s DROP COLUMN seq' % t)
            except sqlite3.Error:
                pass


def _pre_migrate(con):
    """SCHEMA を流す前に、古い形のテーブルを片付ける。

    CREATE TABLE IF NOT EXISTS は「あれば何もしない」ので、
    列が変わったテーブルはここで落としておかないと
    そのあとの CREATE INDEX で失敗する。
    """
    cols = [r[1] for r in con.execute('PRAGMA table_info(tracks)')]
    if cols and 'sseq' not in cols:
        if con.execute('SELECT COUNT(*) c FROM tracks').fetchone()[0] == 0:
            con.execute('DROP TABLE tracks')
        else:
            con.execute('ALTER TABLE tracks ADD COLUMN sseq INTEGER DEFAULT 0')
    con.commit()


def _backfill_uuid(con):
    """同期を入れる前からある行に uuid と seq を振る。一度だけ走る。"""
    for t in SYNC_TABLES:
        miss = con.execute('SELECT id FROM %s WHERE uuid IS NULL OR uuid=""' % t).fetchall()
        if not miss:
            continue
        for r in miss:
            con.execute('UPDATE %s SET uuid=? WHERE id=?' % t, (new_uuid(), r[0]))
        print('  [db] %s: %d 件に uuid を振りました' % (t, len(miss)))
    con.commit()


def _backfill_sseq(con):
    """同期を入れる前からある行に、通し番号を打つ。

    0 のままだと「since=0 より大きい」に引っかからず、
    スマホが1件も受け取れない。
    uuid を自分自身で上書きするとトリガーが走り、番号が付く。
    """
    for t in SYNC_TABLES:
        n = con.execute('SELECT COUNT(*) c FROM %s WHERE COALESCE(sseq,0)=0' % t).fetchone()[0]
        if n:
            con.execute('UPDATE %s SET uuid=uuid WHERE COALESCE(sseq,0)=0' % t)
            print('  [db] %s: %d 件に同期番号を振りました' % (t, n))
    con.commit()


def new_uuid():
    """端末でもサーバーでも作れる一意ID。

    UUIDv4 を使う。62ビット以上の乱数なので、
    現場の端末が何台あっても衝突しない。
    """
    import uuid as _uuid
    return str(_uuid.uuid4())


def next_seq(con, n=1):
    """変更の通し番号を n 個進めて、最初の番号を返す。

    端末は「前回もらった seq」を覚えていて、その続きだけを要求する。
    テーブルをまたいで1本の番号にしてあるので、
    「木を先に、写真を後に」といった順序も自然に保たれる。
    """
    row = con.execute("SELECT v FROM meta WHERE k='sync_seq'").fetchone()
    cur = int(row[0]) if row and str(row[0]).isdigit() else 0
    nxt = cur + n
    con.execute("INSERT INTO meta(k,v) VALUES('sync_seq',?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(nxt),))
    return cur + 1


def current_seq(con):
    row = con.execute("SELECT v FROM meta WHERE k='sync_seq'").fetchone()
    return int(row[0]) if row and str(row[0]).isdigit() else 0


def touch(con, table, ident, by_uuid=False):
    """1行に新しい sseq と updated_at を打つ。書き換えたら必ず呼ぶ。

    これを忘れると、その変更はスマホに届かない。
    server.py 側で trees を UPDATE しているところは全部これを通す。
    """
    col = 'uuid' if by_uuid else 'id'
    con.execute('UPDATE %s SET sseq=?, updated_at=? WHERE %s=?' % (table, col),
                (next_seq(con), now(), ident))


def bump(con, table, ident, by_uuid=False):
    """updated_at は触らず、同期の通し番号だけ進める。"""
    col = 'uuid' if by_uuid else 'id'
    con.execute('UPDATE %s SET sseq=? WHERE %s=?' % (table, col),
                (next_seq(con), ident))


def log_conflict(con, kind, uuid, device, who, field, kept, dropped):
    """採用しなかった変更を残す。現場の入力を黙って消さないため。"""
    con.execute('INSERT INTO conflicts(at,kind,uuid,device,who,field,kept,dropped) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (now(), kind, uuid, device or '', who or '', field,
                 '' if kept is None else str(kept),
                 '' if dropped is None else str(dropped)))


def connect(path=None, forest=False):
    os.makedirs(DATA, exist_ok=True)
    con = sqlite3.connect(path or SURVEY_DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    if not forest:
        _pre_migrate(con)
        con.executescript(SCHEMA)
        _migrate(con)
    return con


def attach_forest(con):
    """survey.db の接続に forest.db を読み取り用で繋ぐ。"""
    if os.path.exists(FOREST_DB):
        con.execute("ATTACH DATABASE ? AS forest", (FOREST_DB,))
        return True
    return False


def next_code(con, site):
    """A-0001 形式の候補木IDを採番する。"""
    import re as _re
    letter = _re.sub(r'^site[_-]?', '', site, flags=_re.I)
    letter = _re.sub(r'[^0-9A-Za-z]', '', letter).upper()[:4] or 'T'
    row = con.execute(
        "SELECT code FROM trees WHERE site=? AND code LIKE ? ORDER BY code DESC LIMIT 1",
        (site, letter + '-%')).fetchone()
    n = 0
    if row:
        try:
            n = int(row['code'].rsplit('-', 1)[1])
        except (ValueError, IndexError):
            n = 0
    return '%s-%04d' % (letter, n + 1)


def log_change(con, tree_id, who, field, old, new):
    if (old or '') == (new or ''):
        return
    con.execute('INSERT INTO history(tree_id, at, who, field, old, new) VALUES (?,?,?,?,?,?)',
                (tree_id, now(), who or '', field, str(old or ''), str(new or '')))


# 同期の通し番号は「書いたら必ず進む」でなければならない。
# 画面から直したのにスマホに届かない、という抜けを作らないため、
# アプリ側のコードではなく SQLite のトリガーで打つ。
#
# SQLite は既定で再帰トリガーが無効なので、
# トリガーの中で同じ表を UPDATE してもトリガーは再び走らない。
_TRIG = '''
CREATE TRIGGER IF NOT EXISTS trg_%(t)s_ins AFTER INSERT ON %(t)s
BEGIN
  UPDATE meta SET v = CAST(v AS INTEGER) + 1 WHERE k='sync_seq';
  UPDATE %(t)s SET sseq = (SELECT CAST(v AS INTEGER) FROM meta WHERE k='sync_seq')
   WHERE rowid = NEW.rowid;
END;

CREATE TRIGGER IF NOT EXISTS trg_%(t)s_upd AFTER UPDATE ON %(t)s
FOR EACH ROW WHEN NEW.sseq IS OLD.sseq
BEGIN
  UPDATE meta SET v = CAST(v AS INTEGER) + 1 WHERE k='sync_seq';
  UPDATE %(t)s SET sseq = (SELECT CAST(v AS INTEGER) FROM meta WHERE k='sync_seq')
   WHERE rowid = NEW.rowid;
END;
'''


def _ensure_triggers(con):
    """書き込みのたびに sseq を進めるトリガーを用意する。"""
    con.execute("INSERT OR IGNORE INTO meta(k,v) VALUES('sync_seq','0')")
    for t in SYNC_TABLES:
        con.executescript(_TRIG % {'t': t})
