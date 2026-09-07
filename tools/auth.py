# -*- coding: utf-8 -*-
"""外に出すときの鍵。

これまでは「このPCの中だけ」で動かす前提だったので認証が無かった。
インターネット越しに同僚のスマホから使うなら、
合言葉を知っている人だけが入れる状態にしないといけない。
調査DBは書き込みもできるので、読まれるだけでは済まない。

考え方
------
役所の少人数（数人〜十数人）で使う道具に、
利用者アカウント管理を持ち込むと運用が破綻する。
「1つの合言葉をチームで共有し、端末ごとに1回入れる」だけにした。

  ・合言葉は data/.secret に置く（.gitignore 済み）。無ければ作る。
  ・合ったら HttpOnly クッキーを1年発行する。以後は素通り。
  ・クッキーの中身は HMAC 署名付き。合言葉そのものは端末に残さない。
  ・総当たりを避けるため、外れた回数で待たせる。

これで守れないもの
------------------
  ・合言葉を知っている人どうしの区別（誰が書いたかは担当者名の自己申告のまま）
  ・合言葉が漏れたとき（変えたら全端末で入れ直し）
役所の内部利用としては釣り合っている。厳密な本人確認が要るなら、
Tailscale のような「そもそも到達できない」層を前に置くほうが向く。
"""
from __future__ import annotations
import base64, hashlib, hmac, os, secrets, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
SECRET_FILE = os.path.join(DATA, '.secret')

COOKIE = 'nrgr'
MAX_AGE = 365 * 24 * 3600

# 外れた回数を覚えておいて、だんだん待たせる（総当たり対策）
_fails = {}


def _read():
    if not os.path.exists(SECRET_FILE):
        return None
    with open(SECRET_FILE, 'r', encoding='utf-8') as f:
        d = {}
        for line in f:
            if '=' in line:
                k, v = line.strip().split('=', 1)
                d[k] = v
        return d or None


def _write(passphrase, key):
    os.makedirs(DATA, exist_ok=True)
    with open(SECRET_FILE, 'w', encoding='utf-8') as f:
        f.write('pass=%s\nkey=%s\n' % (passphrase, key))
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass


def ensure(passphrase=None):
    """合言葉と署名鍵を用意する。すでにあればそれを返す。"""
    d = _read()
    if d and not passphrase:
        return d
    p = passphrase or _friendly_pass()
    key = secrets.token_urlsafe(32)
    _write(p, key)
    return {'pass': p, 'key': key}


def _friendly_pass():
    """口で伝えられる合言葉を作る。

    無線や電話で伝えることがあるので、
    紛らわしい文字（0/O、1/l/I）を外し、4文字ずつ区切る。
    """
    alpha = 'abcdefghjkmnpqrstuvwxyz23456789'
    parts = []
    for _ in range(3):
        parts.append(''.join(secrets.choice(alpha) for _ in range(4)))
    return '-'.join(parts)


def _sign(key, payload):
    return base64.urlsafe_b64encode(
        hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()
    ).decode().rstrip('=')


def make_cookie(cfg):
    exp = str(int(time.time()) + MAX_AGE)
    return '%s.%s' % (exp, _sign(cfg['key'], exp))


def check_cookie(cfg, value):
    if not value or '.' not in value:
        return False
    exp, sig = value.rsplit('.', 1)
    if not hmac.compare_digest(sig, _sign(cfg['key'], exp)):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


def check_pass(cfg, given, who='?'):
    """合言葉を照合する。外れが続いたら待たせる。"""
    now = time.time()
    n, until = _fails.get(who, (0, 0))
    if now < until:
        return None                       # まだ待ち時間の中
    ok = hmac.compare_digest((given or '').strip(), cfg['pass'])
    if ok:
        _fails.pop(who, None)
        return True
    n += 1
    # 3回目から 2秒, 4秒, 8秒 … 最大5分
    wait = 0 if n < 3 else min(300, 2 ** (n - 2))
    _fails[who] = (n, now + wait)
    return False


LOGIN_HTML = """<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>ナラ枯れ調査｜合言葉</title>
<link rel="icon" href="/app/icons/favicon.png">
<style>
 :root{color-scheme:light}
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#103d2e;
      color:#16202b;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN",
      "Yu Gothic UI","Noto Sans JP",sans-serif;padding:24px}
 .box{background:#fff;border-radius:20px;padding:26px 22px;max-width:380px;width:100%;
      box-shadow:0 12px 40px rgba(0,0,0,.3)}
 img{width:64px;height:64px;border-radius:16px;display:block;margin:0 auto 14px}
 h1{font-size:18px;margin:0 0 4px;text-align:center}
 p{font-size:13px;color:#46586b;margin:0 0 18px;text-align:center}
 input{width:100%;box-sizing:border-box;min-height:52px;padding:12px 14px;font-size:18px;
       border:1.5px solid #d5dee6;border-radius:12px;letter-spacing:.06em;text-align:center}
 button{width:100%;min-height:52px;margin-top:12px;border:0;border-radius:12px;
        background:#103d2e;color:#fff;font-size:16px;font-weight:700}
 .err{background:#fff5f5;border:1px solid #ffc9c9;color:#c92a2a;border-radius:10px;
      padding:10px;font-size:13px;margin-bottom:14px}
 .note{font-size:12px;color:#6b7684;margin-top:16px;text-align:center}
</style></head><body>
<div class="box">
  <img src="/app/icons/icon-192.png" alt="">
  <h1>ナラ枯れ現地調査</h1>
  <p>北海道茅部郡森町</p>
  __ERR__
  <form method="POST" action="/login">
    <input name="p" type="password" inputmode="text" autocomplete="current-password"
           placeholder="合言葉" autofocus>
    <input type="hidden" name="next" value="__NEXT__">
    <button type="submit">入る</button>
  </form>
  <p class="note">合言葉は担当者から聞いてください。<br>
     一度入れれば、この端末では次から聞かれません。</p>
</div></body></html>
"""


def login_page(err='', nxt='/field/'):
    e = ('<div class="err">%s</div>' % err) if err else ''
    nxt = (nxt or '/field/').replace('"', '')
    if not nxt.startswith('/'):
        nxt = '/field/'
    return LOGIN_HTML.replace('__ERR__', e).replace('__NEXT__', nxt)
