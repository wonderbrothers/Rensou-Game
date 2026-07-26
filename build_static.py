"""GitHub Pages 用の静的スナップショットを生成する。

Flask の /api/sessions と /api/calls/<id> と同じ形のJSONを、
そのままのパスでファイルとして書き出す。Pages は静的配信なので
Python が動かず、株価は「生成時点」のスナップショットになる。

実行: npm run build （または python build_static.py）
"""
import datetime
import hashlib
import json
import os
import re
import shutil

from server import (DATA, BASE, bench_symbol, cached, compute_eval,
                    fetch_history, fetch_price, fetch_price_on)

OUT = os.path.join(BASE, "api")


def load_sessions():
    out = []
    for fn in sorted(os.listdir(DATA)):
        if not fn.endswith(".json") or fn == "index.json":
            continue
        with open(os.path.join(DATA, fn), encoding="utf-8") as fp:
            out.append(json.load(fp))
    out.sort(key=lambda s: (s.get("date", ""), s.get("id", "")), reverse=True)
    return out


def build_calls(d, generated_at):
    """server.calls() と同じ構造を組み立てる（ファイルへの書き戻しも行う）"""
    call_list = d.get("calls", [])
    if not call_list:
        return {"calls": []}

    today = datetime.date.today().isoformat()
    news_date = d.get("date") or today
    bench_cache = {}
    changed = False
    results = []

    for c in call_list:
        r = dict(c)
        try:
            if not c.get("price_at_call"):
                p0 = fetch_price_on(c["ticker"], news_date)
                c["price_at_call"] = round(p0, 2)
                c["called_at"] = news_date
                changed = True
            r["price_at_call"] = c["price_at_call"]
            r["called_at"] = c["called_at"]

            price = cached(f"px|{c['ticker']}|{today}",
                           lambda t=c["ticker"]: fetch_price(t))
            chg = (price - c["price_at_call"]) / c["price_at_call"] * 100
            r.update(current=round(price, 2), change_pct=round(chg, 2))
            r["status"] = "recorded" if c["called_at"] == today else "checked"

            try:
                r["history"] = cached(
                    f"hist|{c['ticker']}|{c['called_at']}|{today}",
                    lambda t=c["ticker"], s=c["called_at"]: fetch_history(t, s))
            except Exception:
                r["history"] = []

            try:
                bsym, bname = bench_symbol(c["ticker"])
                if bsym not in bench_cache:
                    bench_cache[bsym] = cached(
                        f"hist|{bsym}|{news_date}|{today}",
                        lambda b=bsym: fetch_history(b, news_date))
                if r["history"]:
                    r["eval"] = compute_eval(r["history"], bench_cache[bsym], c["called_at"])
                    r["bench"] = bname
                    r["bench_history"] = bench_cache[bsym]
            except Exception:
                pass
        except Exception as e:
            r.update(status="error", message=str(e))
        results.append(r)

    if changed:
        path = os.path.join(DATA, f"{d['id']}.json")
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(d, fp, ensure_ascii=False, indent=2)
            fp.write("\n")

    return {"calls": results, "generated_at": generated_at}


def stamp_assets():
    """index.html の app.js / style.css のクエリを内容ハッシュに書き換える。

    ブラウザは URL が変わらないと古いファイルを使い続ける。中身が変わったときだけ
    ハッシュが変わるので、利用者が手動でリロードしなくても新しい版が読み込まれる。
    """
    index = os.path.join(BASE, "index.html")
    if not os.path.exists(index):
        return
    html = open(index, encoding="utf-8").read()
    before = html

    for asset in ("app.js", "style.css"):
        path = os.path.join(BASE, asset)
        if not os.path.exists(path):
            continue
        h = hashlib.md5(open(path, "rb").read()).hexdigest()[:8]
        html = re.sub(rf'({re.escape(asset)})(\?v=[^"\']*)?', rf'\1?v={h}', html)

    if html != before:
        with open(index, "w", encoding="utf-8") as fp:
            fp.write(html)
        print("✓ index.html のキャッシュ用バージョンを更新")


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False)
        fp.write("\n")


def main():
    generated_at = datetime.datetime.now().isoformat(timespec="seconds")
    sessions = load_sessions()
    if not sessions:
        print("✗ data/ にニュースJSONがありません")
        return

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)

    write(os.path.join(OUT, "sessions"), sessions)
    print(f"✓ api/sessions        ({len(sessions)}件)")

    ok = ng = 0
    bad_calls = []   # 株価を取得できなかった銘柄（push前に潰すための警告用）
    for d in sessions:
        sid = d.get("id")
        if not sid:
            continue
        try:
            payload = build_calls(d, generated_at)
            write(os.path.join(OUT, "calls", sid), payload)
            ok += 1
            print(f"✓ api/calls/{sid}")
            for c in payload.get("calls", []):
                if c.get("status") == "error" or not c.get("price_at_call"):
                    bad_calls.append((sid, c.get("ticker", "?"),
                                      c.get("name", ""), c.get("message", "")))
        except Exception as e:
            ng += 1
            print(f"✗ api/calls/{sid}  {e}")

    # Pages の Jekyll 処理を無効化（_ 始まりのファイル等をそのまま配信させる）
    open(os.path.join(BASE, ".nojekyll"), "w").close()

    stamp_assets()

    print(f"\n完了: {ok}件成功 / {ng}件失敗　株価スナップショット: {generated_at}")

    if bad_calls:
        print("\n" + "=" * 56)
        print(f"⚠ 株価を取得できなかった銘柄が {len(bad_calls)} 件あります（push前に確認）:")
        for sid, ticker, name, msg in bad_calls:
            print(f"   ✗ {ticker}  {name}  … {sid}")
            if msg:
                print(f"      {msg}")
        print("  対処のヒント:")
        print("   ・上場廃止/持株会社移行などでコードが変わった → data/ のtickerを更新")
        print("   ・yfinanceが安定配信しない小型株 → 別銘柄に差し替え、または許容")
        print("   ・一時的なYahoo側の欠落 → 基準価格は未記録なので次回ビルドで自動再取得")
        print("=" * 56)

    print("\n公開反映: ./sync-public.sh && cd ../Rensou-Game-public && git push")


if __name__ == "__main__":
    main()
