# -*- coding: utf-8 -*-
"""このPCを、外のスマホから使える状態にする。

    py -3 tools/publish.py

何が起きるか
------------
  1. サーバーを「合言葉つき」で起動する
  2. トンネルを張って、HTTPS の URL を1本もらう
  3. 画面にURLと合言葉を出す

なぜトンネルが要るのか
----------------------
スマホのブラウザは **HTTPS でないと GPS を使わせてくれない**。
（iPhone / Android どちらも同じ。http://192.168.… の社内LANでもだめ）
現在地が使えない現地調査アプリは意味が無いので、
HTTPS を無料で用意できるトンネルを通す。

  cloudflared … アカウント不要。URLは起動のたびに変わる。まず試すとき向け。
  tailscale   … 無料アカウントが要る。URLが変わらない。同僚と使うとき向け。

止めかた
--------
この画面で Ctrl+C。サーバーもトンネルも一緒に止まる。
止めれば外からは一切つながらなくなる。
"""
from __future__ import annotations
import os, re, subprocess, sys, threading, time, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import auth as authmod

PORT = 8765


def find(name):
    p = shutil.which(name)
    if p:
        return p
    for base in (os.environ.get('ProgramFiles', r'C:\Program Files'),
                 os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                 os.path.join(os.environ.get('LOCALAPPDATA', ''),
                              'Microsoft', 'WinGet', 'Links'),
                 os.path.join(ROOT, 'bin')):
        for sub in ('', name):
            c = os.path.join(base, sub, name + '.exe')
            if os.path.isfile(c):
                return c
    return None


def box(lines):
    w = max(len(l) + sum(1 for ch in l if ord(ch) > 0x2000) for l in lines) + 2
    print('┏' + '━' * w + '┓')
    for l in lines:
        pad = w - len(l) - sum(1 for ch in l if ord(ch) > 0x2000) - 1
        print('┃ ' + l + ' ' * max(0, pad) + '┃')
    print('┗' + '━' * w + '┛')


def start_server(port):
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    return subprocess.Popen(
        [sys.executable, os.path.join(ROOT, 'server.py'),
         '--no-browser', '--port', str(port), '--auth'],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def wait_server(port, proc, timeout=30):
    import urllib.request, urllib.error
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            urllib.request.urlopen('http://127.0.0.1:%d/login' % port, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(0.4)
    return False


def run_cloudflared(exe, port, on_url):
    p = subprocess.Popen(
        [exe, 'tunnel', '--no-autoupdate', '--url', 'http://127.0.0.1:%d' % port],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, encoding='utf-8', errors='replace')

    def pump():
        seen = False
        for line in p.stdout:
            m = re.search(r'https://[-a-z0-9]+\.trycloudflare\.com', line)
            if m and not seen:
                seen = True
                on_url(m.group(0))
        return
    threading.Thread(target=pump, daemon=True).start()
    return p


def main():
    cfg = authmod.ensure()
    exe = find('cloudflared')
    ts = find('tailscale')

    if not exe and not ts:
        print('')
        print('トンネルの道具がまだ入っていません。')
        print('')
        print('  どちらか一つを入れてください。')
        print('')
        print('  A) cloudflared … アカウント不要。まず試すならこちら。')
        print('     winget install --id Cloudflare.cloudflared')
        print('     （うまくいかないときは公式の配布物を bin/ に置いてください）')
        print('     https://github.com/cloudflare/cloudflared/releases/latest')
        print('       → cloudflared-windows-amd64.exe を')
        print('         %s\\bin\\cloudflared.exe' % ROOT)
        print('         という名前で保存する')
        print('')
        print('  B) tailscale … 無料アカウントが要るが、URLが毎回同じになる。')
        print('     winget install --id tailscale.tailscale')
        print('')
        print('くわしくは docs/スマホで使う.md')
        return 1

    print('サーバーを起動しています…')
    srv = start_server(PORT)
    if not wait_server(PORT, srv):
        print('サーバーが起動しませんでした。')
        out = srv.stdout.read() if srv.stdout else b''
        print(out.decode('utf-8', 'replace')[:2000])
        return 1
    print('  できました（http://127.0.0.1:%d）' % PORT)

    got = threading.Event()
    url_holder = {}

    def on_url(u):
        url_holder['url'] = u
        got.set()

    tun = None
    if exe:
        print('トンネルを張っています…（10秒ほど）')
        tun = run_cloudflared(exe, PORT, on_url)
        got.wait(60)

    print('')
    if url_holder.get('url'):
        box([
            'スマホからこのURLを開いてください',
            '',
            '  ' + url_holder['url'] + '/field/',
            '',
            '合言葉：  ' + cfg['pass'],
            '',
            '初めての端末は合言葉を1回だけ聞かれます。',
            'そのあと「ホーム画面に追加」すると、',
            'アプリとして開けるようになります。',
        ])
        print('')
        print('※ このURLは今回かぎりです。次に起動すると変わります。')
        print('   同僚とずっと使うなら Tailscale のほうにしてください。')
        print('   docs/スマホで使う.md を見てください。')
    elif ts:
        print('cloudflared が無いので Tailscale を使います。')
        print('  この2つを、管理者権限のターミナルで実行してください：')
        print('')
        print('    tailscale up')
        print('    tailscale funnel --bg %d' % PORT)
        print('')
        print('  そのあと表示される https://….ts.net/field/ を開きます。')
        print('  合言葉： %s' % cfg['pass'])
    else:
        print('トンネルを張れませんでした。')

    print('')
    print('止めるときは、この画面で Ctrl+C。')
    print('=' * 60)
    try:
        while True:
            time.sleep(1)
            if srv.poll() is not None:
                print('サーバーが終了しました。')
                break
            if tun and tun.poll() is not None:
                print('トンネルが切れました。もう一度実行してください。')
                break
    except KeyboardInterrupt:
        print('\n止めています…')
    finally:
        for p in (tun, srv):
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        print('外からはつながらなくなりました。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
