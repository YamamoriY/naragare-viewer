# -*- coding: utf-8 -*-
"""調査データベース（data/survey.db）の定義。

【重要】
  data/survey.db  … 人が入力したデータ。現地調査の結果はここにしか無い。
                    絶対に消さないこと。バックアップの対象はこのファイル。
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

# ステータス（内部コード -> 画面表示）
# 白い背景でもはっきり見えるよう、屋外での視認性を優先した色にしてある
STATUS = [
    ('unsurveyed',  '未調査',           '#6b7684'),
    ('damaged',     '現地調査済・被害あり', '#c92a2a'),
    ('clean',       '現地調査済・被害なし', '#2b8a3e'),
    ('pending',     '判定保留',         '#d9480f'),
    ('unreachable', '到達できず',        '#5f3dc4'),
    ('treated',     '処理済',           '#1864ab'),
]
STATUS_CODES = [s[0] for s in STATUS]
STATUS_LABEL = {c: l for c, l, _ in STATUS}
STATUS_COLOR = {c: k for c, _, k in STATUS}

PRIORITY = ['高', '中', '低']

# 現地調査で記録する項目（企画提案書 9-1 のプロット調査項目に対応）
SURVEY_FIELDS = [
    'status', 'survey_date', 'surveyor', 'species', 'dbh_cm',
    'leaf_color', 'dieback', 'boring', 'frass', 'stand',
    'misjudge_reason', 'access_note', 'treatment', 'treatment_date', 'memo',
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
]


def _migrate(con):
    for table, col, typ in MIGRATIONS:
        cols = [r[1] for r in con.execute('PRAGMA table_info(%s)' % table)]
        if col not in cols:
            con.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, col, typ))
    con.commit()


def connect(path=None, forest=False):
    os.makedirs(DATA, exist_ok=True)
    con = sqlite3.connect(path or SURVEY_DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    if not forest:
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
