# -*- coding: utf-8 -*-
"""バッチファイルから呼ばれて、日本語の案内を表示するだけのもの。

.bat の中身は ASCII だけにしてある（cmd.exe が日本語入り .bat を
誤って解釈して起動しないことがあるため）。日本語はここで出す。
"""
import sys, io

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

LINE = '  ' + '=' * 58

MSG = {
    'ingest': """
{line}
   オルソの取り込み
{line}

   「オルソ投入」フォルダの中のオルソを読み込み、
   地図タイルとナラ枯れ候補木を作ります。

   ★ すでに入力してある現地調査の記録は消えません。

   画像が大きいと 10〜60 分ほどかかることがあります。
   （Pillow と numpy が必要です:  pip install pillow numpy）
""",
    'setup': """
{line}
   初期設定（最初に一度だけ）
{line}

   次の3つを行います。インターネット接続が必要です。

     1. 森町の林班・小班・森林調査簿の取り込み（北海道オープンデータ）
     2. 現地確認記録（振興局・町）の登録
     3. 背景地図（国土地理院）の取り込み … 現地で圏外でも使えるように

   30分〜1時間ほどかかることがあります。
""",
    'setup_done': """
{line}
   初期設定が終わりました。
   「起動.bat」をダブルクリックしてビューアーを開いてください。
{line}
""",
    'error': """
{line}
   [!] 途中でエラーが起きました。
       上に出ているメッセージを確認してください。

       よくある原因:
         ・インターネットに繋がっていない
         ・Pillow / numpy が入っていない
              pip install pillow numpy
{line}
""",
}


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else 'ingest'
    print(MSG.get(key, '').format(line=LINE))


if __name__ == '__main__':
    main()
