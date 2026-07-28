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
import subprocess

from server import (DATA, BASE, bench_symbol, cached, compute_eval,
                    fetch_history, fetch_price, fetch_price_on, prefetch_all)

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


def summary_of(d):
    """一覧表示に必要な最小限のメタデータ（api/index 用）"""
    news = d.get("news", {})
    return {
        "id": d["id"],
        "date": d.get("date", ""),
        "categories": d.get("categories", []),
        "news": {k: news[k] for k in ("source", "headline", "source_url", "essence")
                 if news.get(k) is not None},
        "has_calls": bool(d.get("calls")),
        "call_names": [f"{c.get('name', '')} {c.get('ticker', '')}"
                       for c in d.get("calls", [])],   # 銘柄名でのキーワード検索用
        "q_n": len(d.get("questions", [])),
    }


def is_frozen(payload):
    """全コールのT+20判定が確定し、エラーが無いスナップショットか。

    凍結されたニュースの株価は以後再取得しない（答え合わせは確定済みのため）。
    これにより日々の取得対象が「直近の未確定ニュースの銘柄」だけに絞られ、
    記事数が増えてもyfinanceへのリクエスト量が頭打ちになる。
    """
    calls = payload.get("calls") or []
    if not calls:
        return False
    for c in calls:
        if c.get("status") == "error" or not c.get("price_at_call"):
            return False
        t20 = (c.get("eval") or {}).get("t20") or {}
        if t20.get("status") != "done":
            return False
    return True


def load_previous_payloads():
    """前回ビルドの api/calls/* を読み込む（凍結判定・再利用のため）"""
    prev = {}
    calls_dir = os.path.join(OUT, "calls")
    if not os.path.isdir(calls_dir):
        return prev
    for fn in os.listdir(calls_dir):
        try:
            with open(os.path.join(calls_dir, fn), encoding="utf-8") as fp:
                prev[fn] = json.load(fp)
        except Exception:
            pass
    return prev


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


def stamp_version():
    """フッターにビルド版数（ビルド日付＋git短縮ハッシュ）を自動刻印する。

    手動でのバージョン上げ忘れを構造的に防ぐ。不具合報告時に「どの版か」を
    特定でき、キャッシュ由来か修正未反映かの切り分けにも使える。
    """
    index = os.path.join(BASE, "index.html")
    if not os.path.exists(index):
        return
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=BASE,
                           capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        h = ""
    ver = "v" + datetime.date.today().strftime("%Y.%m.%d") + (f"-{h}" if h else "")
    html = open(index, encoding="utf-8").read()
    new = re.sub(r'<span class="ver">[^<]*</span>',
                 f'<span class="ver">{ver}</span>', html)
    if new != html:
        with open(index, "w", encoding="utf-8") as fp:
            fp.write(new)
        print(f"✓ バージョンを刻印: {ver}")


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

    # 前回スナップショットを読み、T+20確定済み（凍結）のものは再利用する
    prev = load_previous_payloads()
    frozen_ids = {sid for sid, p in prev.items() if p.get("frozen") or is_frozen(p)}

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)

    # --- 静的APIの生成 ---
    # api/sessions      : 全記事の完全な配列（互換用・旧クライアント向け）
    # api/index         : 一覧用の軽量メタデータ（本命。記事が増えても軽い）
    # api/session/<id>  : 記事ごとの完全なJSON（プレイ・解答時に取得）
    # api/patterns      : 連想パターン図鑑用（id/date/categories/learning）
    write(os.path.join(OUT, "sessions"), sessions)
    write(os.path.join(OUT, "index"), [summary_of(d) for d in sessions])
    for d in sessions:
        write(os.path.join(OUT, "session", d["id"]), d)
    write(os.path.join(OUT, "patterns"),
          [{"id": d["id"], "date": d.get("date", ""),
            "categories": d.get("categories", []), "learning": d["learning"]}
           for d in sessions if d.get("learning")])
    print(f"✓ api/sessions + api/index + api/session/* + api/patterns ({len(sessions)}件)")

    # 未確定（凍結されていない）ニュースの銘柄だけを一括で先読みする。
    # 凍結済みは前回スナップショットを再利用するため取得ゼロ。
    tickers = set()
    earliest = datetime.date.today().isoformat()
    for d in sessions:
        if d.get("id") in frozen_ids:
            continue
        nd = d.get("date") or earliest
        for c in d.get("calls", []):
            tickers.add(c["ticker"])
            tickers.add(bench_symbol(c["ticker"])[0])
            if nd < earliest:
                earliest = nd
    if tickers:
        print(f"→ 株価を一括取得中…（未確定 {len(tickers)}銘柄, {earliest}〜"
              f"／凍結済み {len(frozen_ids)}件はスキップ）")
        prefetch_all(sorted(tickers), earliest)

    ok = ng = 0
    bad_calls = []   # 株価を取得できなかった銘柄（push前に潰すための警告用）
    for d in sessions:
        sid = d.get("id")
        if not sid:
            continue
        try:
            if sid in frozen_ids:
                payload = prev[sid]
                payload["frozen"] = True
                write(os.path.join(OUT, "calls", sid), payload)
                ok += 1
                print(f"❄ api/calls/{sid} （T+20確定・凍結を再利用）")
                continue
            payload = build_calls(d, generated_at)
            if is_frozen(payload):
                payload["frozen"] = True   # 次回ビルドから再取得しない
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

    stamp_version()
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
