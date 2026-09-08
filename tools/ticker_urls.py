#!/usr/bin/env python3
"""関連銘柄（calls）の銘柄ページURLを出す。作問時の精査用。

アプリは銘柄コードのバッジをYahoo!ファイナンスの銘柄ページへリンクしている。
**リンク先が本当にその会社のページか**は、コードが実在することとは別の問題で、
機械では判定できない（4739.T は「実在するが上場廃止済み」、992.HK は
「桁が足りず別物」という事故が実際にあった）。

このツールはURLを組み立てて並べるだけ。出力のURLを実際に開き、
ページのタイトルに社名が入っていることを確かめる（手順は CONTENT_GUIDE.md 5.2）。

**確認はブラウザで document.title を読む**のが速い。web_fetch はページ全文を
返すため1銘柄あたりの消費が大きく、しかも一部のページはJSで描くので空で返る。

使い方:
    python3 tools/ticker_urls.py data/2026-09-08_xxx.json   # 指定ファイル
    python3 tools/ticker_urls.py --latest                   # いちばん新しい記事
    python3 tools/ticker_urls.py                            # data/ 全件
"""
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")

# 日本語版（finance.yahoo.co.jp）が銘柄ページを持つのは日本株と米国株だけ。
# 日本株は取引所の接尾辞つき、米国株は接尾辞なし。
# それ以外（韓国 .KS / 香港 .HK / 欧州 .PA .DE など）は英語版へ送る。
# ※ この振り分けは app.js の yahooFinanceUrl() と同じ規則。片方だけ変えないこと
#    （--check で app.js と食い違っていないかを確かめられる）
JP_MARKET = re.compile(r"\.(T|O|N|F|S)$")


def yahoo_url(ticker):
    t = (ticker or "").strip()
    if not t:
        return ""
    jp = JP_MARKET.search(t) or "." not in t
    base = "https://finance.yahoo.co.jp/quote/" if jp else "https://finance.yahoo.com/quote/"
    return base + t


def check_against_app_js():
    """app.js 側の規則とズレていないかを見る（ズレたら警告）"""
    path = os.path.join(BASE, "app.js")
    if not os.path.exists(path):
        return
    src = open(path, encoding="utf-8").read()
    m = re.search(r"const JP_MARKET\s*=\s*/([^/]+)/", src)
    if not m:
        print("⚠ app.js に JP_MARKET が見つからない（規則が変わった可能性）")
        return
    if m.group(1) != JP_MARKET.pattern:
        print(f"⚠ 振り分けの規則が app.js とズレている: "
              f"app.js=/{m.group(1)}/ vs このツール=/{JP_MARKET.pattern}/")
    else:
        print("✓ 振り分けの規則は app.js と一致")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--latest" in sys.argv:
        files = sorted(glob.glob(os.path.join(DATA, "*.json")))[-1:]
    else:
        files = args or sorted(glob.glob(os.path.join(DATA, "*.json")))

    check_against_app_js()
    n = 0
    for p in files:
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            print(f"✗ {p}: {e}")
            continue
        calls = d.get("calls", [])
        if not calls:
            continue
        print(f"\n■ {d.get('id')}")
        for c in calls:
            n += 1
            print(f"  {c.get('name','')}（{c.get('ticker','')}）")
            print(f"    {yahoo_url(c.get('ticker'))}")
    print(f"\n{len(files)}記事 / {n}銘柄。"
          f"各URLを開き、ページのタイトルが上の社名と一致することを確かめること。")


if __name__ == "__main__":
    main()
