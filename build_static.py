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

import sys

import server
from server import (DATA, BASE, bench_symbol, cached, compute_eval,
                    fetch_history, fetch_price, fetch_price_on, prefetch_all,
                    summary_of)

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


def classify_bad_ticker(ticker):
    """株価を取得できなかった銘柄を「実在の疑い」と「一時的な欠落」に分ける。

    単純に「引けなければ停止」にすると、香港市場のデータ反映が遅れているだけの
    ケースでも公開が止まってしまう。そこで履歴が1本でも取れるかで区別する。

    - 履歴がまったく無い  → 上場廃止・コード誤りの疑い（公開を止める）
    - 履歴はあるが当日だけ無い → 市場休場やデータ反映待ち（警告のみ・次回自動再取得）
    """
    if server._BULK.get(ticker):
        return "transient"
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="6mo")
        return "transient" if len(h) else "unknown"
    except Exception:
        return "unknown"


def write_build_report(bad_calls, blocking, generated_at):
    """検査結果をファイルに残す。

    Claudeのサンドボックスからは Yahoo Finance に到達できず、実在確認は
    ユーザーのビルドでしか行えない。結果をリポジトリ内のファイルに残せば、
    ユーザーが端末の出力を貼り付けなくてもClaudeが自分で読める。
    公開リポジトリには同期しない（sync-public.sh で除外）。
    """
    path = os.path.join(BASE, "BUILD_REPORT.md")
    lines = ["# ビルド検査レポート", "",
             f"- 生成: {generated_at}",
             f"- 判定: {'FAIL（公開を停止しました）' if blocking else 'PASS'}", ""]
    if blocking:
        lines += ["## 実在が確認できない銘柄（要修正）", "",
                  "履歴が1本も取得できませんでした。上場廃止・コード誤り・社名変更を疑ってください。", ""]
        for sid, ticker, name, msg in blocking:
            lines.append(f"- `{ticker}` {name} … `{sid}`")
            if msg:
                lines.append(f"  - {msg}")
        lines += ["",
                  "対処: 「<社名> 上場廃止」「<社名> TOB 完全子会社」で確認し、",
                  "`data/` の ticker と name を更新するか、連想ロジックが同じ上場銘柄に差し替える。",
                  "（CONTENT_GUIDE.md 5.2）", ""]
    transient = [b for b in bad_calls if b not in blocking]
    if transient:
        lines += ["## 一時的に価格が取れなかった銘柄（対応不要）", "",
                  "履歴自体は取得できているため、市場休場やデータ反映待ちと判断しました。",
                  "基準価格は未記録のまま残るので、次回のビルドで自動的に再取得されます。", ""]
        for sid, ticker, name, msg in transient:
            lines.append(f"- `{ticker}` {name} … `{sid}`")
    if not bad_calls:
        lines.append("すべての銘柄で株価を取得できました。")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    return path


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


def sanitize_payload(payload):
    """スナップショットからJSONとして不正な NaN/Inf を取り除く。

    NaN はブラウザの response.json() がパースを拒否する。凍結済みの
    再利用パイロードは過去のNaNを含んだまま配られ続けるため、
    書き出す直前に必ずここを通す。履歴のNaN行は削除し、evalに
    非有限値が混ざっていた場合はきれいな履歴から再計算する。
    """
    import math

    def finite(x):
        try:
            return x is not None and math.isfinite(float(x))
        except (TypeError, ValueError):
            return False

    def has_bad(o):
        if isinstance(o, dict):
            return any(has_bad(v) for v in o.values())
        if isinstance(o, list):
            return any(has_bad(v) for v in o)
        return isinstance(o, float) and not math.isfinite(o)

    for c in payload.get("calls", []):
        for k in ("history", "bench_history"):
            if isinstance(c.get(k), list):
                c[k] = [r for r in c[k] if finite(r.get("p"))]
        if has_bad(c.get("eval", {})):
            try:
                c["eval"] = compute_eval(c["history"], c.get("bench_history") or [],
                                         c.get("called_at"))
            except Exception:
                c.pop("eval", None)
        for k in ("current", "change_pct", "price_at_call"):
            if k in c and not finite(c[k]):
                c.pop(k, None)
    return payload


def write_sitemap(sessions):
    """sitemap.xml を生成する。SPAのためURLはトップのみだが、
    lastmod を最新記事の日付で更新することで、クローラーに
    「更新されているサイト」であることを伝える。"""
    latest = max((d.get("date", "") for d in sessions), default="")
    lastmod = latest or datetime.date.today().isoformat()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url>\n'
        '    <loc>https://rensougame.wonder-bros.com/</loc>\n'
        f'    <lastmod>{lastmod}</lastmod>\n'
        '    <changefreq>daily</changefreq>\n'
        '  </url>\n'
        '</urlset>\n'
    )
    with open(os.path.join(BASE, "sitemap.xml"), "w", encoding="utf-8") as fp:
        fp.write(xml)
    print(f"✓ sitemap.xml（lastmod: {lastmod}）")


def stat_rows_of(d, payload):
    """通算成績の集計行を組み立てる（server.py の /api/callstats と同じ形）。

    market の判定はクライアントの marketOf に任せる（ロジックを二重に持たない）。
    """
    out = []
    for c in payload.get("calls", []):
        e = c.get("eval")
        if not e:
            continue
        w = ({**e["t20"], "win": "T+20"} if (e.get("t20") or {}).get("status") == "done"
             else {**e["t5"], "win": "T+5"} if (e.get("t5") or {}).get("status") == "done"
             else {**(e.get("now") or {}), "win": "経過中"})
        out.append({"sid": d["id"], "date": d.get("date", ""),
                    "news": d["news"]["headline"], "name": c.get("name", ""),
                    "dir": c.get("direction"), "win": w.get("win"),
                    "rel": w.get("rel"), "bench": c.get("bench", ""),
                    "ticker": c.get("ticker")})
    return out


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
    new = re.sub(r'<(span|p) class="ver">[^<]*</\1>',
                 f'<p class="ver">{ver}</p>', html)
    if new != html:
        with open(index, "w", encoding="utf-8") as fp:
            fp.write(new)
        print(f"✓ バージョンを刻印: {ver}")


def stamp_sw():
    """sw.js のキャッシュ版数を、アプリ本体の内容ハッシュで更新する。

    この文字列が変わると Service Worker が「新しい版」として認識され、
    アプリ側に更新トーストが出る。逆に中身が変わっていなければ版数も変わらず、
    無用な更新通知は出ない。
    """
    sw = os.path.join(BASE, "sw.js")
    if not os.path.exists(sw):
        return
    h = hashlib.md5()
    for name in ("index.html", "app.js", "style.css"):
        p = os.path.join(BASE, name)
        if os.path.exists(p):
            h.update(open(p, "rb").read())
    ver = h.hexdigest()[:12]
    src = open(sw, encoding="utf-8").read()
    new = re.sub(r'const CACHE_VERSION = "[^"]*";',
                 f'const CACHE_VERSION = "{ver}";', src)
    if new != src:
        with open(sw, "w", encoding="utf-8") as fp:
            fp.write(new)
        print(f"✓ sw.js のキャッシュ版数を更新: {ver}")


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
    """静的APIの唯一の書き出し口。allow_nan=False で NaN/Infinity を構造的に禁止する。

    NaNはブラウザの response.json() がパースを拒否するため、1件でも混ざると
    その記事のAPIは全損する（2026-08-04の事故）。sanitize_payload が仕事を
    していれば発火しないが、万一すり抜けてもここで音を立てて止まり、
    壊れたJSONが公開されることはない。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        text = json.dumps(obj, ensure_ascii=False, allow_nan=False)
    except ValueError as e:
        raise ValueError(f"{path}: JSONに不正な数値（NaN/Infinity）が混入 — {e}")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(text)
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
    failed_ids = []   # 生成に失敗した記事（CIでは前回分で穴埋めする）
    stats_rows = []  # api/callstats 用（通算成績の集計を1リクエストで済ませる）
    built = {}       # sid -> payload（callstatsはCIフォールバック確定後に集計する）
    bad_calls = []   # 株価を取得できなかった銘柄（push前に潰すための警告用）
    for d in sessions:
        sid = d.get("id")
        if not sid:
            continue
        try:
            if sid in frozen_ids:
                payload = sanitize_payload(prev[sid])
                payload["frozen"] = True
                write(os.path.join(OUT, "calls", sid), payload)
                built[sid] = payload
                ok += 1
                print(f"❄ api/calls/{sid} （T+20確定・凍結を再利用）")
                continue
            payload = sanitize_payload(build_calls(d, generated_at))
            if is_frozen(payload):
                payload["frozen"] = True   # 次回ビルドから再取得しない
            write(os.path.join(OUT, "calls", sid), payload)
            built[sid] = payload
            ok += 1
            print(f"✓ api/calls/{sid}")
            for c in payload.get("calls", []):
                if c.get("status") == "error" or not c.get("price_at_call"):
                    bad_calls.append((sid, c.get("ticker", "?"),
                                      c.get("name", ""), c.get("message", "")))
        except Exception as e:
            ng += 1
            failed_ids.append(sid)
            print(f"✗ api/calls/{sid}  {e}")

    # callstats はCIフォールバックの確定後（classify の後）に書き出す

    # Pages の Jekyll 処理を無効化（_ 始まりのファイル等をそのまま配信させる）
    open(os.path.join(BASE, ".nojekyll"), "w").close()

    write_sitemap(sessions)
    stamp_version()
    stamp_assets()
    stamp_sw()   # ← app.js/style.css のハッシュ更新後に実行する

    print(f"\n完了: {ok}件成功 / {ng}件失敗　株価スナップショット: {generated_at}")

    # --- 公開前の関所 ---
    # 履歴が1本も取れない銘柄（上場廃止・コード誤りの疑い）があれば、
    # 同期もコミットもさせずにここで止める。npm run build は
    # `build_static.py && ./sync-public.sh` の連結なので、異常終了すれば同期は走らない。
    blocking = []
    if bad_calls:
        print("\n→ 取得できなかった銘柄を判定中…")
        for b in bad_calls:
            kind = classify_bad_ticker(b[1])
            print(f"   {'✗ 実在の疑い' if kind == 'unknown' else '△ 一時的な欠落'}: {b[1]} {b[2]}")
            if kind == "unknown":
                blocking.append(b)

    # --- CI（定期リフレッシュ）でのフォールバック ---
    # GitHub Actions のランナーは Yahoo にレート制限されやすく、実在する銘柄でも
    # 「履歴ゼロ」と誤判定されて関所（exit 1）に引っかかることがある。
    # PRICE_REFRESH=1 のときに限り、前回の正常なスナップショットが残っている記事は
    # それを再利用して公開を継続する（銘柄の実在検査という関所は、コンテンツを
    # 追加する手元ビルドでは従来どおり厳格に働く）。
    if os.environ.get("PRICE_REFRESH") == "1" and failed_ids:
        # 例外で生成できなかった記事。api/ は作り直すため、放置すると
        # そのニュースのAPIがファイルごと消えてフロントが404になる
        for sid in list(failed_ids):
            p = prev.get(sid)
            if not p:
                continue
            payload = sanitize_payload(p)
            if is_frozen(payload):
                payload["frozen"] = True
            write(os.path.join(OUT, "calls", sid), payload)
            built[sid] = payload
            failed_ids.remove(sid)
            ng -= 1
            print(f"   ↩ 生成に失敗したため前回スナップショットを再利用: {sid}")

    if os.environ.get("PRICE_REFRESH") == "1" and blocking:
        still = []
        for b in blocking:
            sid = b[0]
            p = prev.get(sid)
            if p and sid in built:
                payload = sanitize_payload(p)
                if is_frozen(payload):
                    payload["frozen"] = True
                write(os.path.join(OUT, "calls", sid), payload)
                built[sid] = payload
                print(f"   ↩ 取得失敗のため前回スナップショットを再利用: {sid}（{b[1]}）")
            else:
                still.append(b)
        blocking = still

    stats_rows = []
    for d in sessions:
        sid = d.get("id")
        if sid in built:
            stats_rows.extend(stat_rows_of(d, built[sid]))
    write(os.path.join(OUT, "callstats"), stats_rows)
    print(f"✓ api/callstats（{len(stats_rows)}コール・通算成績はこれ1本で読む）")

    if ng:
        print(f"\n✗ {ng}件の生成に失敗し、前回スナップショットでも補えませんでした。")
        print("   公開を停止します（上の ✗ 行を確認）。")
        sys.exit(1)

    report = write_build_report(bad_calls, blocking, generated_at)
    print(f"\n検査レポート: {os.path.relpath(report, BASE)}")

    if blocking:
        print("\n" + "=" * 60)
        print(f"✗ 実在を確認できない銘柄が {len(blocking)} 件あります。公開を停止しました。")
        for sid, ticker, name, msg in blocking:
            print(f"   ✗ {ticker}  {name}  … {sid}")
            if msg:
                print(f"      {msg}")
        print("")
        print("  履歴が1本も取得できていません。上場廃止・コード誤り・社名変更を疑ってください。")
        print("  「<社名> 上場廃止」「<社名> TOB 完全子会社」で確認し、data/ を修正して再ビルド。")
        print("  ※ 同期・コミットは行っていないので、公開サイトは元のままです。")
        print("=" * 60)
        sys.exit(1)

    transient = [b for b in bad_calls if b not in blocking]
    if transient:
        print("\n" + "-" * 60)
        print(f"△ 一時的に価格を取れなかった銘柄が {len(transient)} 件あります（対応不要）:")
        for sid, ticker, name, msg in transient:
            print(f"   △ {ticker}  {name}  … {sid}")
        print("  履歴自体は取れているため、市場休場・データ反映待ちと判断しました。")
        print("  基準価格は未記録のまま残り、次回のビルドで自動的に再取得されます。")
        print("-" * 60)

    print("\n公開反映: ./sync-public.sh && cd ../Rensou-Game-public && git push")


if __name__ == "__main__":
    main()
