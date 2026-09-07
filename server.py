# -*- coding: utf-8 -*-
"""ナラ枯れビューアー ローカルサーバー

Python の標準ライブラリだけで動く。外部ライブラリも、インターネット接続も要らない。
（オルソの取り込みだけは Pillow と numpy が要る。tools/ingest_ortho.py 参照）

  python server.py            起動してブラウザを開く
  python server.py --port 9000
  python server.py --no-browser
"""
from __future__ import annotations
import os, sys, io, json, re, csv, math, time, sqlite3, argparse, mimetypes, threading, webbrowser
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
import db as dbmod

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, 'app')
DATA = os.path.join(ROOT, 'data')
PHOTOS = os.path.join(DATA, 'photos')

mimetypes.add_type('image/webp', '.webp')
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('application/geo+json', '.geojson')

EDITABLE = set(dbmod.SURVEY_FIELDS) | {'priority', 'memo', 'lon', 'lat', 'loc_accuracy'}
LOCK = threading.Lock()

IMG_EXT = ('.tif', '.tiff', '.jpg', '.jpeg', '.png')


# ------------------------------------------------ オルソ取り込みジョブ
class ImportJob:
    """tools/ingest_ortho.py を裏で走らせ、進み具合を画面に返す。"""

    def __init__(self):
        self.proc = None
        self.lines = []
        self.started = None
        self.finished = None
        self.rc = None
        self.args = None
        self.logpath = None
        self._logf = None
        self.lock = threading.Lock()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, opts):
        import subprocess
        if self.running:
            raise RuntimeError('すでに取り込み中です')
        path = opts.get('path') or ''
        if not os.path.isfile(path):
            raise RuntimeError('ファイルが見つかりません: %s' % path)
        cmd = [sys.executable, '-u', os.path.join(ROOT, 'tools', 'ingest_ortho.py'), path]
        for key, flag in (('site', '--site'), ('name', '--name'),
                          ('date', '--date'), ('note', '--note')):
            v = (opts.get(key) or '').strip()
            if v:
                cmd += [flag, v]
        if opts.get('tiles_only'):
            cmd.append('--tiles-only')
        if opts.get('detect'):
            cmd.append('--detect')
        if opts.get('zmax'):
            cmd += ['--zmax', str(int(opts['zmax']))]

        self.lines = []
        self.rc = None
        self.finished = None
        self.started = time.time()
        self.args = dict(opts)
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        self.logpath = os.path.join(DATA, '_import.log')
        try:
            self._logf = open(self.logpath, 'w', encoding='utf-8')
            self._logf.write('$ ' + ' '.join(cmd) + '\n')
            self._logf.flush()
        except OSError:
            self._logf = None
        self.proc = subprocess.Popen(
            cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env, bufsize=0)
        threading.Thread(target=self._pump, daemon=True).start()
        return True

    def _pump(self):
        buf = b''
        try:
            while True:
                ch = self.proc.stdout.read(1)
                if not ch:
                    break
                if ch in (b'\n', b'\r'):
                    if buf:
                        self._push(buf.decode('utf-8', 'replace'))
                        buf = b''
                else:
                    buf += ch
        except Exception as e:
            self._push('[読み取りエラー] %s' % e)
        if buf:
            self._push(buf.decode('utf-8', 'replace'))
        self.rc = self.proc.wait()
        if self.rc != 0:
            self._push('--- 異常終了しました（終了コード %s）。'
                       'くわしくは data/_import.log ---' % self.rc)
        if self._logf:
            try:
                self._logf.close()
            except OSError:
                pass
            self._logf = None
        self.finished = time.time()
        try:
            import ingest_ortho
            ingest_ortho.write_sites_json()
        except Exception:
            pass

    def _push(self, line):
        line = line.rstrip()
        if not line:
            return
        if self._logf:
            try:
                self._logf.write(line + '\n')
                self._logf.flush()
            except (OSError, ValueError):
                pass
        with self.lock:
            # 進捗行（z20  100 / 442）は積み上げずに最後の1行だけ差し替える
            if self.lines and re.match(r'^\s*z\d+\s', line) and re.match(r'^\s*z\d+\s', self.lines[-1]):
                self.lines[-1] = line
            else:
                self.lines.append(line)
            if len(self.lines) > 400:
                del self.lines[:-400]

    def status(self):
        with self.lock:
            lines = list(self.lines)
        pct = None
        phase = ''
        for ln in reversed(lines):
            m = re.match(r'^\s*z(\d+)\s+(\d+)\s*/\s*(\d+)', ln)
            if m:
                phase = 'タイル生成 z%s' % m.group(1)
                tot = int(m.group(3)) or 1
                pct = min(99, int(int(m.group(2)) / tot * 100))
                break
        if self.rc is not None:
            pct = 100
            phase = '完了' if self.rc == 0 else 'エラー'
        elif self.running and pct is None:
            phase = '準備中'
        return {
            'running': self.running,
            'rc': self.rc,
            'percent': pct,
            'phase': phase,
            'lines': lines[-60:],
            'args': self.args,
            'elapsed': int((self.finished or time.time()) - self.started) if self.started else 0,
        }

    def cancel(self):
        if self.running:
            self.proc.terminate()
            return True
        return False


JOB = ImportJob()


def list_dir(path):
    """オルソを選ぶためのフォルダ一覧。"""
    out = {'path': path or '', 'parent': None, 'drives': [], 'dirs': [], 'files': []}
    if os.name == 'nt':
        import string
        for d in string.ascii_uppercase:
            root = '%s:\\' % d
            if os.path.exists(root):
                out['drives'].append(root)
    if not path:
        return out
    path = os.path.abspath(path)
    out['path'] = path
    parent = os.path.dirname(path.rstrip('\\/'))
    out['parent'] = parent if parent and parent != path else None
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except OSError as e:
        out['error'] = str(e)
        return out
    for e in entries:
        try:
            if e.name.startswith('.') or e.name.startswith('$'):
                continue
            if e.is_dir():
                out['dirs'].append(e.name)
            elif e.name.lower().endswith(IMG_EXT):
                low = e.name.lower()
                if '_dsm' in low or '_dtm' in low:
                    continue
                stem = os.path.splitext(e.path)[0]
                world = any(os.path.exists(stem + w)
                            for w in ('.jgw', '.pgw', '.tfw', '.wld'))
                # 「siteA_ortho.tif」に対する「siteA_dsm.tif」を探す。
                # 置換はファイル名部分だけに掛ける（フォルダ名に ortho が入るため）
                base = re.sub(r'[_-]?ortho.*$', '',
                              os.path.splitext(e.name)[0], flags=re.I)
                dsm = any(os.path.exists(os.path.join(path, base + s))
                          for s in ('_dsm.tif', '_dsm.tiff'))
                out['files'].append({
                    'name': e.name, 'size': e.stat().st_size,
                    'geotiff': low.endswith(('.tif', '.tiff')),
                    'world': world, 'dsm': dsm,
                })
        except OSError:
            continue
    return out


def jdump(o):
    return json.dumps(o, ensure_ascii=False, default=str).encode('utf-8')


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------- multipart
def parse_multipart(body, content_type):
    """写真アップロード用の最小限のマルチパート解析。"""
    m = re.search(r'boundary=("?)([^";]+)\1', content_type)
    if not m:
        return {}, {}
    bnd = ('--' + m.group(2)).encode('utf-8')
    fields, files = {}, {}
    for part in body.split(bnd):
        if not part or part in (b'--\r\n', b'--', b'\r\n'):
            continue
        if part.startswith(b'\r\n'):
            part = part[2:]
        head, _, data = part.partition(b'\r\n\r\n')
        if not _:
            continue
        data = data[:-2] if data.endswith(b'\r\n') else data
        head_s = head.decode('utf-8', 'replace')
        nm = re.search(r'name="([^"]*)"', head_s)
        fn = re.search(r'filename="([^"]*)"', head_s)
        if not nm:
            continue
        if fn and fn.group(1):
            files[nm.group(1)] = (fn.group(1), data)
        else:
            fields[nm.group(1)] = data.decode('utf-8', 'replace')
    return fields, files


# --------------------------------------------------------------- ハンドラ
class Handler(BaseHTTPRequestHandler):
    server_version = 'NaragareViewer/1.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *a):
        if self.server.verbose:
            sys.stderr.write('  %s\n' % (fmt % a))

    # ---------- 返し方 ----------
    def send_json(self, obj, code=200):
        b = jdump(obj)
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def send_bytes(self, b, ctype, code=200, cache=None, filename=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        if cache:
            self.send_header('Cache-Control', cache)
        if filename:
            q = urllib.parse.quote(filename)
            self.send_header('Content-Disposition',
                             "attachment; filename*=UTF-8''%s" % q)
        self.end_headers()
        self.wfile.write(b)

    def send_err(self, code, msg):
        self.send_json({'error': msg}, code)

    def serve_file(self, path, cache='public, max-age=3600'):
        if not os.path.isfile(path):
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/javascript',
                                                  'application/json',
                                                  'application/geo+json'):
            ctype += '; charset=utf-8'
        with open(path, 'rb') as f:
            b = f.read()
        self.send_bytes(b, ctype, cache=cache)

    # ---------- ルーティング ----------
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = urllib.parse.unquote(u.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if p.startswith('/api/'):
                return self.api_get(p[5:], q)
            if p in ('/', '/index.html'):
                return self.serve_file(os.path.join(APP, 'index.html'), cache='no-store')
            for prefix, base in (('/app/', APP), ('/data/', DATA)):
                if p.startswith(prefix):
                    rel = p[len(prefix):]
                    full = os.path.normpath(os.path.join(base, rel))
                    if not full.startswith(base):
                        return self.send_err(403, 'forbidden')
                    # 画面のファイルは毎回読み直す。直したのに変わらない、を防ぐため。
                    # タイルと写真は中身が変わらないので長くキャッシュしてよい。
                    if p.startswith('/app/'):
                        cache = 'no-store'
                    elif '/tiles/' in p or '/basemap/' in p or '/photos/' in p:
                        cache = 'public, max-age=86400'
                    else:
                        cache = 'public, max-age=60'
                    return self.serve_file(full, cache=cache)
            return self.send_err(404, 'not found')
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # 地図を動かすとブラウザが読み込み中のタイルを打ち切る。異常ではない。
            pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self.send_err(500, str(e))
            except Exception:
                pass

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        p = urllib.parse.unquote(u.path)
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n else b''
        try:
            if not p.startswith('/api/'):
                return self.send_err(404, 'not found')
            return self.api_post(p[5:], body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self.send_err(500, str(e))
            except Exception:
                pass

    def handle_one_request(self):
        # 接続を打ち切られたときに標準エラーへ長い traceback を出さない
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            self.close_connection = True

    def do_PUT(self):
        """画面にドロップされた画像を オルソ投入/ へ保存する。

        メモリに全部載せずに少しずつ書く（オルソは数百MBになる）。
        """
        u = urllib.parse.urlparse(self.path)
        if urllib.parse.unquote(u.path) != '/api/upload':
            return self.send_err(404, 'not found')
        q = urllib.parse.parse_qs(u.query)
        name = (q.get('name') or [''])[0]
        name = os.path.basename(name.replace('\\', '/'))
        name = re.sub(r'[^0-9A-Za-z._\-ぁ-んァ-ヶ一-龠々ー]', '_', name)[:120]
        if not name or name.startswith('.'):
            return self.send_err(400, 'ファイル名が不正です')
        ext = os.path.splitext(name)[1].lower()
        if ext not in ('.tif', '.tiff', '.jpg', '.jpeg', '.png',
                       '.jgw', '.pgw', '.tfw', '.wld', '.prj'):
            return self.send_err(400, '扱えない拡張子です: %s' % ext)

        inbox = os.path.join(ROOT, 'オルソ投入')
        os.makedirs(inbox, exist_ok=True)
        dest = os.path.join(inbox, name)
        total = int(self.headers.get('Content-Length') or 0)
        got = 0
        try:
            with open(dest, 'wb') as f:
                while got < total:
                    chunk = self.rfile.read(min(1 << 20, total - got))
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError) as e:
            try:
                os.remove(dest)
            except OSError:
                pass
            return self.send_err(500, '保存できませんでした: %s' % e)
        if got < total:
            try:
                os.remove(dest)
            except OSError:
                pass
            return self.send_err(400, '転送が途中で切れました（%d/%d バイト）' % (got, total))
        return self.send_json({'ok': True, 'path': dest, 'name': name, 'size': got})

    def do_DELETE(self):
        # 中身が付いてきたら読み捨てる。読まずに返すと接続が壊れ、
        # 次のリクエストが「Unsupported method」になってしまう。
        n = int(self.headers.get('Content-Length') or 0)
        if n > 0:
            self.rfile.read(n)
        u = urllib.parse.urlparse(self.path)
        p = urllib.parse.unquote(u.path)
        m = re.match(r'^/api/tree/(\d+)$', p)
        if not m:
            return self.send_err(404, 'not found')
        with LOCK:
            con = dbmod.connect()
            r = con.execute('SELECT source FROM trees WHERE id=?', (m.group(1),)).fetchone()
            if not r:
                con.close()
                return self.send_err(404, 'no such tree')
            if r['source'] == 'ai':
                con.close()
                return self.send_err(400, 'AI が抽出した候補木は削除できません。'
                                          'ステータスを「現地調査済・被害なし」にしてください。')
            con.execute('DELETE FROM trees WHERE id=?', (m.group(1),))
            con.commit()
            con.close()
        self.send_json({'ok': True})

    # ---------- API (GET) ----------
    def api_get(self, name, q):
        one = lambda k, d=None: (q.get(k) or [d])[0]

        if name == 'bootstrap':
            return self.send_json(self.bootstrap())

        if name == 'trees':
            return self.send_json({'trees': self.query_trees(q)})

        m = re.match(r'^tree/(\d+)$', name)
        if m:
            con = dbmod.connect()
            r = con.execute('SELECT * FROM trees WHERE id=?', (m.group(1),)).fetchone()
            if not r:
                con.close()
                return self.send_err(404, 'no such tree')
            d = dict(r)
            d['photos'] = rows(con.execute(
                'SELECT * FROM photos WHERE tree_id=? ORDER BY id', (m.group(1),)))
            d['history'] = rows(con.execute(
                'SELECT * FROM history WHERE tree_id=? ORDER BY id DESC LIMIT 200',
                (m.group(1),)))
            con.close()
            return self.send_json(d)

        if name == 'kosyoban':
            bbox = one('bbox')
            if not bbox:
                return self.send_err(400, 'bbox が要ります')
            lo0, la0, lo1, la1 = [float(v) for v in bbox.split(',')]
            nara = one('nara')
            con = dbmod.connect(dbmod.FOREST_DB, forest=True)
            sql = ('SELECT id,rinpan,kosyoban,chiku,sp_main,species,age,height,area_ha,'
                   'elev,nara_rank,rinshu,chiban,geom FROM kosyoban '
                   'WHERE maxlon>=? AND minlon<=? AND maxlat>=? AND minlat<=?')
            args = [lo0, lo1, la0, la1]
            if nara:
                sql += ' AND nara_rank>=?'
                args.append(int(nara))
            sql += ' LIMIT 4000'
            feats = []
            for r in con.execute(sql, args):
                try:
                    g = json.loads(r['geom'])
                except Exception:
                    continue
                pr = {k: r[k] for k in r.keys() if k != 'geom'}
                try:
                    pr['species'] = json.loads(pr.get('species') or '[]')
                except Exception:
                    pr['species'] = []
                feats.append({'type': 'Feature', 'properties': pr, 'geometry': g})
            con.close()
            return self.send_json({'type': 'FeatureCollection', 'features': feats})

        if name == 'stats':
            return self.send_json(self.stats(one('site')))

        if name == 'export.csv':
            return self.export_csv(q)

        if name == 'export.geojson':
            return self.export_geojson(q)

        if name == 'browse':
            return self.send_json(list_dir(one('path', '')))

        if name == 'import/status':
            return self.send_json(JOB.status())

        if name == 'locate':
            # 1点の林班・小班・樹種・標高を引く（地図をタップしたとき用）
            try:
                lon = float(one('lon'))
                lat = float(one('lat'))
            except (TypeError, ValueError):
                return self.send_err(400, 'lon/lat が要ります')
            return self.send_json(locate_point(lon, lat))

        return self.send_err(404, 'unknown api: ' + name)

    # ---------- API (POST) ----------
    def api_post(self, name, body):
        m = re.match(r'^tree/(\d+)/photo$', name)
        if m:
            return self.upload_photo(int(m.group(1)), body)

        m = re.match(r'^tree/(\d+)$', name)
        if m:
            data = json.loads(body.decode('utf-8') or '{}')
            return self.update_tree(int(m.group(1)), data)

        if name == 'tree':
            data = json.loads(body.decode('utf-8') or '{}')
            return self.create_tree(data)

        if name == 'import':
            opts = json.loads(body.decode('utf-8') or '{}')
            try:
                JOB.start(opts)
            except Exception as e:
                return self.send_err(400, str(e))
            return self.send_json(JOB.status())

        if name == 'import/cancel':
            return self.send_json({'ok': JOB.cancel()})

        m = re.match(r'^tree/(\d+)/reset_position$', name)
        if m:
            data = json.loads(body.decode('utf-8') or '{}')
            return self.reset_position(int(m.group(1)), data.get('_who', ''))

        if name == 'trees/bulk':
            data = json.loads(body.decode('utf-8') or '{}')
            return self.bulk_update(data)

        if name == 'photos/import':
            return self.import_photos(body)

        m = re.match(r'^photo/(\d+)/delete$', name)
        if m:
            with LOCK:
                con = dbmod.connect()
                r = con.execute('SELECT * FROM photos WHERE id=?', (m.group(1),)).fetchone()
                if r:
                    fp = os.path.join(PHOTOS, r['filename'])
                    if os.path.exists(fp):
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
                    con.execute('DELETE FROM photos WHERE id=?', (m.group(1),))
                    con.commit()
                con.close()
            return self.send_json({'ok': True})

        return self.send_err(404, 'unknown api: ' + name)

    # ---------- 実装 ----------
    def bootstrap(self):
        con = dbmod.connect()
        sites = rows(con.execute('SELECT * FROM sites ORDER BY id'))
        for s in sites:
            s['tiles'] = '/data/tiles/%s/{z}/{x}/{y}.%s' % (s['id'], s['tile_ext'] or 'png')
            s['tree_count'] = con.execute(
                'SELECT COUNT(*) c FROM trees WHERE site=?', (s['id'],)).fetchone()['c']
        total = con.execute('SELECT COUNT(*) c FROM trees').fetchone()['c']
        con.close()

        forest_meta = {}
        if os.path.exists(dbmod.FOREST_DB):
            fc = dbmod.connect(dbmod.FOREST_DB, forest=True)
            try:
                for r in fc.execute('SELECT k,v FROM meta'):
                    forest_meta[r['k']] = r['v']
            except sqlite3.Error:
                pass
            fc.close()

        base = os.path.join(DATA, 'basemap')
        return {
            'sites': sites,
            'tree_total': total,
            'status': [{'code': c, 'label': l, 'color': k} for c, l, k in dbmod.STATUS],
            'priority': dbmod.PRIORITY,
            'forest': forest_meta,
            'layers': {
                'rinpan': '/data/layers/rinpan_mori.geojson'
                if os.path.exists(os.path.join(DATA, 'layers', 'rinpan_mori.geojson')) else None,
                'contour200': '/data/layers/contour200_mori.geojson'
                if os.path.exists(os.path.join(DATA, 'layers', 'contour200_mori.geojson')) else None,
            },
            'basemap_local': {
                'photo': os.path.isdir(os.path.join(base, 'photo')),
                'pale': os.path.isdir(os.path.join(base, 'pale')),
            },
            'today': dbmod.today(),
            'deadline': self.deadline(),
        }

    @staticmethod
    def deadline():
        """カシナガが脱出する翌年5月末までに被害木を処理する必要がある。"""
        t = time.localtime()
        y = t.tm_year if (t.tm_mon, t.tm_mday) <= (5, 31) else t.tm_year + 1
        end = time.mktime((y, 5, 31, 23, 59, 59, 0, 0, -1))
        days = int((end - time.time()) // 86400)
        return {'date': '%d-05-31' % y, 'days_left': days,
                'note': 'カシノナガキクイムシの脱出前（5月末）までに被害木の処理が必要'}

    def query_trees(self, q):
        one = lambda k, d=None: (q.get(k) or [d])[0]
        sql = 'SELECT * FROM trees WHERE 1=1'
        args = []
        for key, col in (('site', 'site'), ('rinpan', 'rinpan'), ('priority', 'priority')):
            v = q.get(key)
            if v and v[0]:
                vs = [x for x in v[0].split(',') if x]
                if vs:
                    sql += ' AND %s IN (%s)' % (col, ','.join('?' * len(vs)))
                    args += vs
        st = one('status')
        if st:
            vs = [x for x in st.split(',') if x]
            if vs:
                sql += ' AND status IN (%s)' % ','.join('?' * len(vs))
                args += vs
        if one('nara'):
            sql += ' AND nara_rank>=?'
            args.append(int(one('nara')))
        bbox = one('bbox')
        if bbox:
            lo0, la0, lo1, la1 = [float(v) for v in bbox.split(',')]
            sql += ' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?'
            args += [lo0, lo1, la0, la1]
        text = (one('q') or '').strip()
        if text:
            sql += (' AND (code LIKE ? OR memo LIKE ? OR surveyor LIKE ? OR species LIKE ?'
                    ' OR chiban LIKE ? OR rinpan LIKE ?)')
            args += ['%%%s%%' % text] * 6
        sql += ' ORDER BY CASE priority WHEN "高" THEN 0 WHEN "中" THEN 1 ELSE 2 END, score DESC'
        lim = int(one('limit', '5000'))
        sql += ' LIMIT %d' % max(1, min(lim, 20000))
        con = dbmod.connect()
        out = rows(con.execute(sql, args))
        con.close()
        return out

    def stats(self, site=None):
        con = dbmod.connect()
        where = ' WHERE site=?' if site else ''
        a = [site] if site else []
        st = {r['status']: r['c'] for r in con.execute(
            'SELECT status, COUNT(*) c FROM trees%s GROUP BY status' % where, a)}
        pr = {r['priority']: r['c'] for r in con.execute(
            'SELECT priority, COUNT(*) c FROM trees%s GROUP BY priority' % where, a)}
        by_rinpan = rows(con.execute(
            'SELECT COALESCE(rinpan,"") rinpan, chiku, COUNT(*) n,'
            ' SUM(CASE WHEN status="unsurveyed" THEN 1 ELSE 0 END) unsurveyed,'
            ' SUM(CASE WHEN status="damaged" THEN 1 ELSE 0 END) damaged,'
            ' SUM(CASE WHEN status="treated" THEN 1 ELSE 0 END) treated,'
            ' SUM(CASE WHEN priority="高" THEN 1 ELSE 0 END) high'
            ' FROM trees%s GROUP BY rinpan, chiku ORDER BY high DESC, n DESC' % where, a))
        tot = con.execute('SELECT COUNT(*) c FROM trees%s' % where, a).fetchone()['c']
        done = con.execute(
            'SELECT COUNT(*) c FROM trees%s%s status<>"unsurveyed"'
            % (where, ' AND' if where else ' WHERE'), a).fetchone()['c']
        # 被害ありで未処理のもの＝5月末までに処理が要るもの
        todo = con.execute(
            'SELECT COUNT(*) c FROM trees%s%s status="damaged"'
            % (where, ' AND' if where else ' WHERE'), a).fetchone()['c']
        # 到達できなかった地点が、どのオルソの範囲に入っているか。
        # 人が入れなかった場所を上空から確認できる、というのがドローンの主眼なので
        # ここを画面に出す。
        sites = [dict(r) for r in con.execute(
            'SELECT id,name,minlon,minlat,maxlon,maxlat FROM sites WHERE minlon IS NOT NULL')]
        unreachable = []
        for r in con.execute(
                'SELECT id,code,rinpan,kosyoban,lon,lat,access_note,memo FROM trees '
                'WHERE status="unreachable" ORDER BY rinpan,kosyoban'):
            d = dict(r)
            d['covered_by'] = None
            if d['lon'] is not None:
                for s in sites:
                    if (s['minlon'] <= d['lon'] <= s['maxlon']
                            and s['minlat'] <= d['lat'] <= s['maxlat']):
                        d['covered_by'] = {'id': s['id'], 'name': s['name']}
                        break
            # その小班の中で、オルソから何件の候補木を拾えたか
            d['found'] = d['found_high'] = 0
            if d['rinpan'] and d['kosyoban']:
                f = con.execute(
                    'SELECT COUNT(*) n, SUM(CASE WHEN priority="高" THEN 1 ELSE 0 END) h '
                    'FROM trees WHERE source="ai" AND rinpan=? AND kosyoban=?',
                    (d['rinpan'], d['kosyoban'])).fetchone()
                d['found'] = f['n'] or 0
                d['found_high'] = f['h'] or 0
            d.pop('memo', None)
            unreachable.append(d)
        con.close()
        return {'status': st, 'priority': pr, 'by_rinpan': by_rinpan,
                'total': tot, 'surveyed': done, 'to_treat': todo,
                'unreachable': unreachable,
                'deadline': self.deadline()}

    def update_tree(self, tid, data):
        who = (data.pop('_who', '') or '').strip()
        with LOCK:
            con = dbmod.connect()
            cur = con.execute('SELECT * FROM trees WHERE id=?', (tid,)).fetchone()
            if not cur:
                con.close()
                return self.send_err(404, 'no such tree')

            # 位置を動かしたら、平面直角座標・林班・小班・樹種・標高を引き直す
            if 'lon' in data and 'lat' in data:
                try:
                    loc = locate_point(float(data['lon']), float(data['lat']))
                except (TypeError, ValueError):
                    con.close()
                    return self.send_err(400, '緯度経度の値が不正です')
                data['lon'], data['lat'] = loc['lon'], loc['lat']
                derived = {k: loc[k] for k in
                           ('x', 'y', 'rinpan', 'kosyoban', 'chiku',
                            'sp_main', 'nara_rank', 'chiban', 'elev')}
                # 初めて動かすときだけ、元の位置を控えておく（あとで戻せるように）
                if cur['orig_lon'] is None and cur['lon'] is not None:
                    derived['orig_lon'] = cur['lon']
                    derived['orig_lat'] = cur['lat']
                    derived['orig_note'] = cur['loc_accuracy'] or '登録時の位置'
                con.execute('UPDATE trees SET %s WHERE id=?'
                            % ','.join('%s=?' % k for k in derived),
                            list(derived.values()) + [tid])
                old_ll = '%s, %s' % (cur['lat'], cur['lon'])
                new_ll = '%s, %s' % (loc['lat'], loc['lon'])
                if old_ll != new_ll:
                    import geo
                    d = (geo.haversine_m(cur['lon'], cur['lat'], loc['lon'], loc['lat'])
                         if cur['lon'] is not None else 0.0)
                    dbmod.log_change(con, tid, who, '位置',
                                     old_ll, '%s（%.1f m 移動）' % (new_ll, d))

            sets, args = [], []
            for k, v in data.items():
                if k not in EDITABLE:
                    continue
                if k == 'status' and v not in dbmod.STATUS_CODES:
                    con.close()
                    return self.send_err(400, 'ステータスの値が不正です: %s' % v)
                old = cur[k] if k in cur.keys() else None
                if (old or '') == (v or ''):
                    continue
                sets.append('%s=?' % k)
                args.append(v)
                # 緯度と経度は上で「位置」としてまとめて記録済み
                if k not in ('lon', 'lat'):
                    dbmod.log_change(con, tid, who, k, old, v)
            if sets:
                sets.append('updated_at=?')
                args.append(dbmod.now())
                con.execute('UPDATE trees SET %s WHERE id=?' % ','.join(sets), args + [tid])
                con.commit()
            r = con.execute('SELECT * FROM trees WHERE id=?', (tid,)).fetchone()
            out = dict(r)
            con.close()
        return self.send_json(out)

    def create_tree(self, data):
        """現地で見つけた木を手入力で追加する（AI候補に無かったもの）。"""
        who = (data.get('_who') or '').strip()
        try:
            lon = float(data['lon'])
            lat = float(data['lat'])
        except (KeyError, TypeError, ValueError):
            return self.send_err(400, '緯度経度が要ります')
        site = data.get('site') or 'manual'
        loc = locate_point(lon, lat)
        x, y = loc['x'], loc['y']
        rinpan, kosyoban, chiku = loc['rinpan'], loc['kosyoban'], loc['chiku']
        sp_main, nara_rank, chiban = loc['sp_main'], loc['nara_rank'], loc['chiban']
        elev = loc['elev']

        with LOCK:
            con = dbmod.connect()
            if not con.execute('SELECT 1 FROM sites WHERE id=?', (site,)).fetchone():
                con.execute('INSERT INTO sites(id,name,created_at,updated_at) VALUES (?,?,?,?)',
                            (site, data.get('site_name') or '手入力', dbmod.now(), dbmod.now()))
            code = dbmod.next_code(con, site)
            f = dict(code=code, site=site, lon=round(lon, 7), lat=round(lat, 7),
                     x=round(x, 2), y=round(y, 2), elev=elev,
                     rinpan=rinpan, kosyoban=kosyoban, chiku=chiku, sp_main=sp_main,
                     nara_rank=nara_rank, chiban=chiban,
                     priority=data.get('priority') or '中',
                     status=data.get('status') or 'unsurveyed',
                     source='manual',
                     loc_accuracy=data.get('loc_accuracy') or '手入力',
                     memo=data.get('memo') or '',
                     created_at=dbmod.now(), updated_at=dbmod.now())
            for k in dbmod.SURVEY_FIELDS:
                if k in data and k not in f:
                    f[k] = data[k]
            con.execute('INSERT INTO trees(%s) VALUES (%s)'
                        % (','.join(f), ','.join('?' * len(f))), list(f.values()))
            tid = con.execute('SELECT id FROM trees WHERE code=?', (code,)).fetchone()['id']
            dbmod.log_change(con, tid, who, 'created', '', code)
            con.commit()
            r = dict(con.execute('SELECT * FROM trees WHERE id=?', (tid,)).fetchone())
            con.close()
        return self.send_json(r)

    def reset_position(self, tid, who=''):
        """動かした位置を、登録されたときの位置に戻す。"""
        with LOCK:
            con = dbmod.connect()
            cur = con.execute('SELECT * FROM trees WHERE id=?', (tid,)).fetchone()
            if not cur:
                con.close()
                return self.send_err(404, 'no such tree')
            if cur['orig_lon'] is None:
                con.close()
                return self.send_err(400, 'この記録はまだ動かされていません')
            loc = locate_point(cur['orig_lon'], cur['orig_lat'])
            f = {k: loc[k] for k in ('lon', 'lat', 'x', 'y', 'rinpan', 'kosyoban',
                                     'chiku', 'sp_main', 'nara_rank', 'chiban', 'elev')}
            f['loc_accuracy'] = cur['orig_note'] or '登録時の位置'
            f['orig_lon'] = None
            f['orig_lat'] = None
            f['orig_note'] = None
            f['updated_at'] = dbmod.now()
            con.execute('UPDATE trees SET %s WHERE id=?' % ','.join('%s=?' % k for k in f),
                        list(f.values()) + [tid])
            dbmod.log_change(con, tid, who, '位置',
                             '%s, %s' % (cur['lat'], cur['lon']),
                             '%s, %s（元の位置に戻した）' % (loc['lat'], loc['lon']))
            con.commit()
            r = dict(con.execute('SELECT * FROM trees WHERE id=?', (tid,)).fetchone())
            con.close()
        return self.send_json(r)

    def bulk_update(self, data):
        """一覧で選んだ木をまとめて更新する。"""
        ids = [int(i) for i in (data.get('ids') or [])]
        fields = data.get('fields') or {}
        who = (data.get('_who') or '').strip()
        fields = {k: v for k, v in fields.items()
                  if k in EDITABLE and k not in ('lon', 'lat') and str(v or '') != ''}
        if not ids:
            return self.send_err(400, '対象が選ばれていません')
        if not fields:
            return self.send_err(400, '変更する項目がありません')
        if 'status' in fields and fields['status'] not in dbmod.STATUS_CODES:
            return self.send_err(400, 'ステータスの値が不正です')

        changed = 0
        with LOCK:
            con = dbmod.connect()
            now = dbmod.now()
            for tid in ids:
                cur = con.execute('SELECT * FROM trees WHERE id=?', (tid,)).fetchone()
                if not cur:
                    continue
                sets, args = [], []
                for k, v in fields.items():
                    old = cur[k] if k in cur.keys() else None
                    if (old or '') == (v or ''):
                        continue
                    sets.append('%s=?' % k)
                    args.append(v)
                    dbmod.log_change(con, tid, who, k, old, v)
                if sets:
                    sets.append('updated_at=?')
                    args.append(now)
                    con.execute('UPDATE trees SET %s WHERE id=?' % ','.join(sets),
                                args + [tid])
                    changed += 1
            con.commit()
            con.close()
        return self.send_json({'ok': True, 'requested': len(ids), 'changed': changed})

    def import_photos(self, body):
        """現地写真をまとめて取り込み、EXIFの位置情報で木に紐づける。

        近くに木があればその木の写真として追加し、無ければその場に新しく登録する。
        位置情報が無い写真は、どの木のものか決められないので報告だけする。
        """
        import geo
        import exif as exifmod
        ctype = self.headers.get('Content-Type') or ''
        fields, files = parse_multipart(body, ctype)
        if not files:
            return self.send_err(400, '画像が届いていません')

        radius = float(fields.get('radius') or 30.0)
        create = (fields.get('create') or '1') not in ('0', 'false', '')
        site = fields.get('site') or 'genchi'
        who = (fields.get('_who') or '').strip()
        status = fields.get('status') or 'damaged'

        os.makedirs(PHOTOS, exist_ok=True)
        result = {'attached': [], 'created': [], 'nogps': [], 'skipped': []}

        with LOCK:
            con = dbmod.connect()
            trees = [dict(r) for r in con.execute(
                'SELECT id, code, lon, lat FROM trees WHERE lon IS NOT NULL')]

            for key, (fn, blob) in files.items():
                ext = os.path.splitext(fn)[1].lower() or '.jpg'
                if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
                    result['skipped'].append({'file': fn, 'why': '対応していない形式'})
                    continue
                if len(blob) > 30 * 1024 * 1024:
                    result['skipped'].append({'file': fn, 'why': '30MBを超えています'})
                    continue

                info = exifmod.read(blob[:256 * 1024]) if ext in ('.jpg', '.jpeg') else {}
                lat, lon = info.get('lat'), info.get('lon')

                tid = None
                created = None
                if lat is not None and lon is not None:
                    best, bd = None, radius
                    for t in trees:
                        d = geo.haversine_m(lon, lat, t['lon'], t['lat'])
                        if d < bd:
                            best, bd = t, d
                    if best:
                        tid = best['id']
                        result['attached'].append(
                            {'file': fn, 'code': best['code'], 'dist': round(bd, 1),
                             'taken': info.get('taken')})
                    elif create:
                        loc = locate_point(lon, lat)
                        code = dbmod.next_code(con, site)
                        f = dict(code=code, site=site,
                                 lon=loc['lon'], lat=loc['lat'], x=loc['x'], y=loc['y'],
                                 elev=loc['elev'], rinpan=loc['rinpan'],
                                 kosyoban=loc['kosyoban'], chiku=loc['chiku'],
                                 sp_main=loc['sp_main'], nara_rank=loc['nara_rank'],
                                 chiban=loc['chiban'], priority='高', status=status,
                                 source='manual',
                                 loc_accuracy='現地写真のGPS（スマートフォン。数m〜十数mの誤差）',
                                 survey_date=(info.get('taken') or '')[:10] or None,
                                 surveyor=who or None,
                                 memo='現地写真から自動登録（%s）' % fn,
                                 created_at=dbmod.now(), updated_at=dbmod.now())
                        con.execute('INSERT INTO trees(%s) VALUES (%s)'
                                    % (','.join(f), ','.join('?' * len(f))), list(f.values()))
                        tid = con.execute('SELECT id FROM trees WHERE code=?',
                                          (code,)).fetchone()['id']
                        dbmod.log_change(con, tid, who, 'created', '', code + '（写真から）')
                        trees.append({'id': tid, 'code': code, 'lon': lon, 'lat': lat})
                        created = code
                        result['created'].append(
                            {'file': fn, 'code': code, 'lat': loc['lat'], 'lon': loc['lon'],
                             'rinpan': loc['rinpan'], 'kosyoban': loc['kosyoban'],
                             'taken': info.get('taken')})
                else:
                    result['nogps'].append({'file': fn, 'taken': info.get('taken')})
                    continue

                if tid is None:
                    result['skipped'].append({'file': fn, 'why': '近くに木がありません'})
                    continue

                base = re.sub(r'[^0-9A-Za-z._-]', '_', os.path.basename(fn))[-60:]
                name = '%s_%s' % (time.strftime('%Y%m%d%H%M%S'), base)
                i = 1
                while os.path.exists(os.path.join(PHOTOS, name)):
                    name = '%s_%d_%s' % (time.strftime('%Y%m%d%H%M%S'), i, base)
                    i += 1
                with open(os.path.join(PHOTOS, name), 'wb') as fh:
                    fh.write(blob)
                cap = []
                if info.get('taken'):
                    cap.append(info['taken'])
                if info.get('model'):
                    cap.append(info['model'])
                con.execute(
                    'INSERT INTO photos(tree_id,filename,caption,created_at) VALUES (?,?,?,?)',
                    (tid, name, ' / '.join(cap), dbmod.now()))
            con.commit()
            con.close()

        result['summary'] = ('既存の木へ %d 枚 / 新しく登録 %d 本 / 位置情報なし %d 枚 / 見送り %d 枚'
                             % (len(result['attached']), len(result['created']),
                                len(result['nogps']), len(result['skipped'])))
        return self.send_json(result)

    def upload_photo(self, tid, body):
        ctype = self.headers.get('Content-Type') or ''
        fields, files = parse_multipart(body, ctype)
        if not files:
            return self.send_err(400, '画像が届いていません')
        os.makedirs(PHOTOS, exist_ok=True)
        con = dbmod.connect()
        t = con.execute('SELECT code FROM trees WHERE id=?', (tid,)).fetchone()
        if not t:
            con.close()
            return self.send_err(404, 'no such tree')
        saved = []
        for key, (fn, data) in files.items():
            ext = os.path.splitext(fn)[1].lower() or '.jpg'
            if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.heic'):
                continue
            if len(data) > 30 * 1024 * 1024:
                continue
            name = '%s_%s%s' % (t['code'], time.strftime('%Y%m%d%H%M%S'), ext)
            i = 1
            while os.path.exists(os.path.join(PHOTOS, name)):
                name = '%s_%s_%d%s' % (t['code'], time.strftime('%Y%m%d%H%M%S'), i, ext)
                i += 1
            with open(os.path.join(PHOTOS, name), 'wb') as f:
                f.write(data)
            con.execute('INSERT INTO photos(tree_id,filename,caption,created_at) VALUES (?,?,?,?)',
                        (tid, name, fields.get('caption', ''), dbmod.now()))
            saved.append(name)
        con.commit()
        out = rows(con.execute('SELECT * FROM photos WHERE tree_id=? ORDER BY id', (tid,)))
        con.close()
        return self.send_json({'ok': True, 'saved': saved, 'photos': out})

    # ---------- 出力 ----------
    def export_csv(self, q):
        trees = self.query_trees(dict(q, limit=['20000']))
        cols = [
            ('code', '候補木ID'), ('site', 'サイト'), ('rinpan', '林班'), ('kosyoban', '小班'),
            ('chiku', '地区'), ('lat', '緯度'), ('lon', '経度'),
            ('x', 'XI系X_東距m'), ('y', 'XI系Y_北距m'), ('elev', '標高m'),
            ('canopy_h', '樹高参考値m'), ('sp_main', '森林簿主樹種'), ('nara_rank', 'ナラ類区分'),
            ('chiban', '代表地番'), ('priority', '優先度'), ('score', '検出スコア'),
            ('area_m2', '検出面積m2'), ('status', 'ステータス'),
            ('survey_date', '調査日'), ('surveyor', '調査者'), ('species', '現地樹種'),
            ('dbh_cm', '胸高直径cm'), ('leaf_color', '葉の変色'), ('dieback', '枯死状況'),
            ('boring', '穿孔'), ('frass', 'フラス量'), ('stand', '周辺林相'),
            ('misjudge_reason', '誤判別要因'), ('access_note', '到達状況'),
            ('treatment', '処理方法'), ('treatment_date', '処理日'), ('memo', 'メモ'),
            ('loc_accuracy', '位置の確からしさ'), ('source', '登録元'),
            ('created_at', '登録日時'), ('updated_at', '更新日時'),
        ]
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator='\r\n')
        w.writerow([c[1] for c in cols])
        for t in trees:
            row = []
            for k, _ in cols:
                v = t.get(k)
                if k == 'status':
                    v = dbmod.STATUS_LABEL.get(v, v)
                elif k == 'nara_rank':
                    v = {2: 'ナラ類確実', 1: 'ナラ類の可能性', 0: '該当なし'}.get(v, '小班外')
                row.append('' if v is None else v)
            w.writerow(row)
        # Excel で開けるよう BOM 付き UTF-8
        b = b'\xef\xbb\xbf' + buf.getvalue().encode('utf-8')
        self.send_bytes(b, 'text/csv; charset=utf-8',
                        filename='ナラ枯れ候補木_%s.csv' % time.strftime('%Y%m%d'))

    def export_geojson(self, q):
        trees = self.query_trees(dict(q, limit=['20000']))
        feats = []
        for t in trees:
            if t.get('lon') is None:
                continue
            pr = dict(t)
            pr['status_label'] = dbmod.STATUS_LABEL.get(t.get('status'), '')
            feats.append({'type': 'Feature',
                          'geometry': {'type': 'Point', 'coordinates': [t['lon'], t['lat']]},
                          'properties': pr})
        fc = {'type': 'FeatureCollection',
              'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}},
              'name': 'ナラ枯れ候補木',
              'note': '位置は水平±3〜5mの誤差を含む（GCP/RTK非使用）。境界確定には使用不可。',
              'features': feats}
        self.send_bytes(jdump(fc), 'application/geo+json; charset=utf-8',
                        filename='ナラ枯れ候補木_%s.geojson' % time.strftime('%Y%m%d'))


def point_elevation(lon, lat):
    """カーソル位置の地表標高[m]を国土地理院DEMのキャッシュから引く。

    numpy を使わずに .npy を直接読む。ビューアー本体を
    「Python標準ライブラリだけで動く」状態に保つため。
    .npy は「マジック6バイト＋版2＋ヘッダ長2＋ASCIIヘッダ＋生データ」なので、
    ヘッダ長さえ分かれば目的の1画素へ直接シークできる。
    """
    import struct
    Z, TILE, NODATA = 14, 256, -32768
    n = 2 ** Z
    fx = (lon + 180.0) / 360.0 * n
    lr = math.radians(max(-85.05, min(85.05, lat)))
    fy = (1.0 - math.log(math.tan(lr) + 1.0 / math.cos(lr)) / math.pi) / 2.0 * n
    tx, ty = int(fx), int(fy)
    path = os.path.join(DATA, 'dem_cache', '%d_%d_%d.npy' % (Z, tx, ty))
    if not os.path.exists(path):
        return None
    col = max(0, min(TILE - 1, int((fx - tx) * TILE)))
    row = max(0, min(TILE - 1, int((fy - ty) * TILE)))
    try:
        with open(path, 'rb') as f:
            head = f.read(10)
            if head[:6] != b'\x93NUMPY':
                return None
            if head[6] == 1:
                hlen = struct.unpack('<H', head[8:10])[0]
                off = 10 + hlen
            else:                                   # v2.0 以降は4バイト
                hlen = struct.unpack('<I', f.read(2) + head[8:10])[0]
                off = 12 + hlen
            f.seek(off + (row * TILE + col) * 2)
            raw = f.read(2)
            if len(raw) < 2:
                return None
            v = struct.unpack('<h', raw)[0]
    except OSError:
        return None
    return None if v == NODATA else round(v / 10.0, 1)


def locate_point(lon, lat):
    """緯度経度から、平面直角座標・林班・小班・樹種・標高を引く。

    地図をタップして木を登録するときと、座標の表示に使う。
    """
    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    import geo
    x, y = geo.lonlat_to_xy(lon, lat)
    out = {
        'lon': round(lon, 7), 'lat': round(lat, 7),
        'x': round(x, 2), 'y': round(y, 2),
        'rinpan': None, 'kosyoban': None, 'chiku': None, 'sp_main': None,
        'species': [], 'nara_rank': None, 'chiban': None, 'elev': None,
        'age': None, 'rinshu': None, 'area_ha': None,
        'ground_elev': point_elevation(lon, lat),   # その地点の地表標高
    }
    if not os.path.exists(dbmod.FOREST_DB):
        return out
    fc = dbmod.connect(dbmod.FOREST_DB, forest=True)
    try:
        for r in fc.execute(
                'SELECT rinpan,kosyoban,chiku,sp_main,species,age,rinshu,area_ha,'
                'nara_rank,chiban,elev,geom FROM kosyoban '
                'WHERE maxx>=? AND minx<=? AND maxy>=? AND miny<=?', (x, x, y, y)):
            try:
                g = json.loads(r['geom'])
            except Exception:
                continue
            polys = g['coordinates'] if g['type'] == 'MultiPolygon' else [g['coordinates']]
            hit = False
            for poly in polys:
                if poly and _in_ring(lon, lat, poly[0]) and \
                        not any(_in_ring(lon, lat, h) for h in poly[1:]):
                    hit = True
                    break
            if hit:
                for k in ('rinpan', 'kosyoban', 'chiku', 'sp_main', 'age',
                          'rinshu', 'area_ha', 'nara_rank', 'chiban', 'elev'):
                    out[k] = r[k]
                try:
                    out['species'] = json.loads(r['species'] or '[]')
                except Exception:
                    out['species'] = []
                break
    except sqlite3.Error:
        pass
    fc.close()
    return out


def _in_ring(lon, lat, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            if lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()

    os.makedirs(PHOTOS, exist_ok=True)
    con = dbmod.connect()          # スキーマを作る
    n = con.execute('SELECT COUNT(*) c FROM trees').fetchone()['c']
    ns = con.execute('SELECT COUNT(*) c FROM sites').fetchone()['c']
    con.close()

    port = args.port
    for attempt in range(20):
        try:
            httpd = ThreadingHTTPServer((args.host, port), Handler)
            break
        except OSError:
            port += 1
    else:
        raise SystemExit('空きポートが見つかりませんでした')
    httpd.verbose = args.verbose

    url = 'http://%s:%d/' % (args.host, port)
    print('=' * 60)
    print(' ナラ枯れビューアー')
    print('=' * 60)
    print(' サイト %d 件 / 候補木 %d 件' % (ns, n))
    if not os.path.exists(dbmod.FOREST_DB):
        print(' [!] data/forest.db がありません。')
        print('     tools/build_forest.py を実行すると林班・小班・樹種が使えます。')
    print('')
    print('  %s' % url)
    print('')
    print(' 終了するには この画面で Ctrl+C を押すか、ウィンドウを閉じてください。')
    print('=' * 60)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n終了しました。')


if __name__ == '__main__':
    main()
