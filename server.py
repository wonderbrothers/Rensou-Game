"""連想ゲーム Webサーバー
- 静的ファイル配信（index.html / style.css / app.js）
- /api/sessions        : data/ の全セッションJSONを返す
- /api/calls/<sid>     : 遊びコールの株価を取得。
                         初回は price_at_call をJSONに記録、
                         2回目以降は現在価格と比較して答え合わせを返す。
"""
import datetime
import glob
import json
import math
import os
import random
import time

from flask import Flask, jsonify, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

app = Flask(__name__, static_folder=None)


class _StrictJSON(app.json_provider_class):
    """NaN/Infinity を含む応答を作らせない（作ろうとしたらdevで即500になる）。

    静的ビルド側の write() と同じ方針。壊れたJSONを黙って配るより、
    開発中に大きな音で失敗するほうが早く直せる。
    """
    def dumps(self, obj, **kwargs):
        kwargs.setdefault("allow_nan", False)
        return super().dumps(obj, **kwargs)


app.json = _StrictJSON(app)

# ---------- 日次価格キャッシュ（同じ銘柄×同じ日は再取得しない） ----------
CACHE_FILE = os.path.join(BASE, ".cache_prices.json")
_price_cache = None


def _cache():
    global _price_cache
    if _price_cache is None:
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                _price_cache = json.load(f)
        except Exception:
            _price_cache = {}
    return _price_cache


def cached(key, fn):
    c = _cache()
    if key in c:
        return c[key]
    v = fn()
    c[key] = v
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass
    return v


@app.route("/")
def root():
    return send_from_directory(BASE, "index.html")


@app.route("/<path:p>")
def static_files(p):
    return send_from_directory(BASE, p)


@app.after_request
def no_cache(resp):
    """開発サーバーは常に最新のファイルを配る（キャッシュさせない）。

    index.html の ?v= ハッシュはビルド時にしか変わらないため、開発中に
    app.js / style.css を編集しても、ブラウザが同じURLのキャッシュを
    使い続けて「直したのに反映されない」が起きる。本番（GitHub Pages）には
    このヘッダーは付かないので影響しない。
    """
    resp.headers["Cache-Control"] = "no-store"
    return resp


def load_all_sessions():
    out = []
    for f in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        if os.path.basename(f) == "index.json":
            continue
        with open(f, encoding="utf-8") as fp:
            out.append(json.load(fp))
    out.sort(key=lambda s: (s.get("date", ""), s.get("id", "")), reverse=True)
    return out


def summary_of(d):
    """一覧表示に必要な最小限のメタデータ（api/index 用）。build_static.py も利用する"""
    news = d.get("news", {})
    return {
        "id": d["id"],
        "date": d.get("date", ""),
        "categories": d.get("categories", []),
        "news": {k: news[k] for k in ("source", "headline", "source_url", "essence")
                 if news.get(k) is not None},
        "has_calls": bool(d.get("calls")),
        "call_names": [f"{c.get('name', '')} {c.get('ticker', '')}"
                       for c in d.get("calls", [])],
        "q_n": len(d.get("questions", [])),
    }


@app.route("/api/sessions")
def sessions():
    return jsonify(load_all_sessions())


@app.route("/api/index")
def api_index():
    """一覧用の軽量インデックス（静的ビルドの api/index と同形）。
    ルートが静的ファイルより優先されるため、devサーバーでは常に data/ の最新を返す"""
    return jsonify([summary_of(d) for d in load_all_sessions()])


@app.route("/api/session/<sid>")
def api_session(sid):
    path = os.path.join(DATA, f"{sid}.json")
    if not os.path.exists(path):
        return jsonify({"error": "session not found"}), 404
    with open(path, encoding="utf-8") as fp:
        return jsonify(json.load(fp))


@app.route("/api/patterns")
def api_patterns():
    return jsonify([{"id": d["id"], "date": d.get("date", ""),
                     "categories": d.get("categories", []), "learning": d["learning"]}
                    for d in load_all_sessions() if d.get("learning")])


def _with_retry(fn, tries=3, base=1.5):
    """一時的な失敗（レート制限・接続断）を指数バックオフで再試行する。

    Yahoo は 429 Too Many Requests や接続リセットを断続的に返すことがある。
    恒久的な失敗（銘柄が存在しない等）はリトライしても無駄なのでそのまま上げる。
    """
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            transient = any(k in msg for k in (
                "429", "Too Many Requests", "curl", "Connection",
                "timed out", "Timeout", "temporarily", "50"))
            if i == tries - 1 or not transient:
                raise
            time.sleep(base * (2 ** i) + random.random())


# ---------- 一括取得ストア ----------
# build_static.py が prefetch_all() で全銘柄の日次終値を1リクエストにまとめて取得し、
# ここに格納する。以降の fetch_price / fetch_price_on / fetch_history はまず
# このストアを参照し、無い銘柄だけ従来どおり個別に取得する（Flaskサーバー単体でも動く）。
_BULK = {}   # ticker -> [{"d","iso","p"}, ...]（日付昇順）


def _finite(x):
    """数値として有効か（NaN/Inf/None を弾く）。

    Yahooは市場データの反映前に NaN の行を返すことがある。NaN は正式なJSONでは
    不正な値で、Pythonのjsonは平気で出力するがブラウザの response.json() は
    パースを拒否する。APIに載せる数値は必ずこの関数を通して除外する。
    """
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def prefetch_all(tickers, start_str):
    """全銘柄の日次終値を yf.download 一括で取得して _BULK に格納する。

    銘柄ごとに1件ずつ叩くとN×リクエストになるが、これなら実質1リクエスト。
    失敗しても例外は投げず、取れた銘柄だけ格納する（残りは個別取得にフォールバック）。
    """
    import yfinance as yf
    if not tickers:
        return
    start = (datetime.date.fromisoformat(start_str)
             - datetime.timedelta(days=10)).isoformat()
    end = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    try:
        df = _with_retry(lambda: yf.download(
            tickers=list(tickers), start=start, end=end,
            group_by="ticker", auto_adjust=True,
            threads=True, progress=False))
    except Exception as e:
        print(f"  (一括取得に失敗、個別取得にフォールバック: {e})")
        return
    if df is None or df.empty:
        return
    for t in tickers:
        try:
            closes = df[t]["Close"] if len(tickers) > 1 else df["Close"]
            closes = closes.dropna()
            if not len(closes):
                continue
            _BULK[t] = [{"d": i.strftime("%m/%d"),
                         "iso": i.strftime("%Y-%m-%d"),
                         "p": round(float(v), 2)}
                        for i, v in closes.items()]
        except Exception:
            continue
    print(f"  一括取得: {len(_BULK)}/{len(tickers)} 銘柄")


def fetch_price(ticker: str) -> float:
    """現在（直近）の株価"""
    rows = [r for r in (_BULK.get(ticker) or []) if _finite(r["p"])]
    if rows:
        return rows[-1]["p"]
    import yfinance as yf
    tk = yf.Ticker(ticker)
    try:
        p = tk.fast_info["last_price"]
        if p and _finite(p):
            return float(p)
    except Exception:
        pass

    def _hist():
        h = tk.history(period="5d")
        h = h[h["Close"].notna()]
        if not len(h):
            raise RuntimeError(f"price unavailable: {ticker}")
        return float(h["Close"].iloc[-1])
    return _with_retry(_hist)


def fetch_price_on(ticker: str, date_str: str) -> float:
    """指定日（ニュース日付）の終値。休場日はその直前の営業日終値。"""
    rows = _BULK.get(ticker)
    if rows:
        upto = [r for r in rows if r["iso"] <= date_str and _finite(r["p"])]
        if upto:
            return upto[-1]["p"]
    import yfinance as yf
    d0 = datetime.date.fromisoformat(date_str)
    start = d0 - datetime.timedelta(days=10)
    end = d0 + datetime.timedelta(days=1)

    def _hist():
        h = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
        h = h[h["Close"].notna()]
        if not len(h):
            raise RuntimeError(f"price unavailable on {date_str}: {ticker}")
        return float(h["Close"].iloc[-1])
    return _with_retry(_hist)


def bench_symbol(ticker: str):
    """銘柄の市場に対応するベンチマーク指数"""
    if ticker.endswith(".T"):
        return "^N225", "日経平均"
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return "^KS11", "KOSPI"
    if ticker.endswith(".HK"):
        return "^HSI", "ハンセン指数"
    return "^GSPC", "S&P500"


def compute_eval(hist, bhist, called_at):
    """T+5 / T+20 営業日の絶対・相対（対ベンチマーク）リターンを計算"""
    isos = [h["iso"] for h in hist]
    ni = next((i for i, s in enumerate(isos) if s >= called_at), 0)
    p0 = hist[ni]["p"]

    def bench_at(iso):
        prev = None
        for h in bhist:
            if h["iso"] <= iso:
                prev = h["p"]
            else:
                break
        return prev

    b0 = bench_at(isos[ni]) if bhist else None
    out = {}
    for label, n in (("t5", 5), ("t20", 20)):
        j = ni + n
        if j < len(hist):
            chg = (hist[j]["p"] / p0 - 1) * 100
            bj = bench_at(isos[j]) if b0 else None
            rel = chg - ((bj / b0 - 1) * 100 if (b0 and bj) else 0)
            out[label] = {"status": "done", "asof": isos[j][5:].replace("-", "/"),
                          "chg": round(chg, 2), "rel": round(rel, 2)}
        else:
            out[label] = {"status": "pending", "remaining": j - (len(hist) - 1)}
    # 現時点
    chg = (hist[-1]["p"] / p0 - 1) * 100
    bl = bench_at(isos[-1]) if b0 else None
    rel = chg - ((bl / b0 - 1) * 100 if (b0 and bl) else 0)
    out["now"] = {"chg": round(chg, 2), "rel": round(rel, 2)}
    return out


def fetch_history(ticker: str, start_str: str):
    """ニュース日の7日前→現在の日次終値（損益グラフ用）。
    ニュース当日でも文脈が見えるよう、少し手前から取得する。"""
    start = datetime.date.fromisoformat(start_str) - datetime.timedelta(days=7)
    rows = _BULK.get(ticker)
    if rows:
        return [r for r in rows if r["iso"] >= start.isoformat()]
    import yfinance as yf
    end = datetime.date.today() + datetime.timedelta(days=1)

    def _hist():
        h = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
        return [{"d": i.strftime("%m/%d"), "iso": i.strftime("%Y-%m-%d"),
                 "p": round(float(row["Close"]), 2)}
                for i, row in h.iterrows() if _finite(row["Close"])]
    return _with_retry(_hist)


def calls_payload(sid):
    """遊びコールの株価・評価を組み立てて返す（見つからなければ None）。

    /api/calls/<sid> と /api/callstats の両方から使う。集計APIが
    このまま同じ計算を通ることで、devでも常に data/ の最新が反映される。
    """
    path = os.path.join(DATA, f"{sid}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fp:
        d = json.load(fp)

    call_list = d.get("calls", [])
    if not call_list:
        return {"calls": []}

    changed = False
    results = []
    today = datetime.date.today().isoformat()
    news_date = d.get("date") or today
    bench_cache = {}

    for c in call_list:
        r = dict(c)
        try:
            if not c.get("price_at_call"):
                # 初回: ニュース日付の終値を基準価格として記録
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
            # ニュース当日は答え合わせにならないので「記録」扱い
            r["status"] = "recorded" if c["called_at"] == today else "checked"
            # 損益グラフ用の日次履歴（失敗しても本体は返す）
            try:
                r["history"] = cached(
                    f"hist|{c['ticker']}|{c['called_at']}|{today}",
                    lambda t=c["ticker"], s=c["called_at"]: fetch_history(t, s))
            except Exception:
                r["history"] = []
            # T+5 / T+20 の絶対・相対リターン（ベンチマーク比較）
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
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(d, fp, ensure_ascii=False, indent=2)
            fp.write("\n")

    return {"calls": results}


@app.route("/api/calls/<sid>")
def calls(sid):
    payload = calls_payload(sid)
    if payload is None:
        return jsonify({"error": "session not found"}), 404
    return jsonify(payload)


@app.route("/api/callstats")
def callstats():
    """通算成績の集計用。全記事のコールを1レスポンスにまとめる。

    従来クライアントは api/calls/<id> を記事数ぶん直列に取得しており、
    記事が増えるほど往復回数がそのまま待ち時間になっていた（75記事=75往復）。
    集計に必要な項目だけを1本にまとめて往復を1回にする。
    market の判定はクライアントの marketOf に任せる（ロジックの二重化を避ける）。
    """
    rows = []
    for d in load_all_sessions():
        if not d.get("calls"):
            continue
        payload = calls_payload(d["id"]) or {}
        for c in payload.get("calls", []):
            e = c.get("eval")
            if not e:
                continue
            w = ({**e["t20"], "win": "T+20"} if (e.get("t20") or {}).get("status") == "done"
                 else {**e["t5"], "win": "T+5"} if (e.get("t5") or {}).get("status") == "done"
                 else {**(e.get("now") or {}), "win": "経過中"})
            rows.append({"sid": d["id"], "date": d.get("date", ""),
                         "news": d["news"]["headline"], "name": c.get("name", ""),
                         "dir": c.get("direction"), "win": w.get("win"),
                         "rel": w.get("rel"), "bench": c.get("bench", ""),
                         "ticker": c.get("ticker")})
    return jsonify(rows)


def record_all():
    """全セッションの遊びコールにニュース日付の基準株価を一括記録する。"""
    for f in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        with open(f, encoding="utf-8") as fp:
            d = json.load(fp)
        news_date = d.get("date")
        changed = False
        for c in d.get("calls", []):
            if c.get("price_at_call"):
                print(f"  = {c['ticker']:8s} 記録済み ({c['price_at_call']} @ {c['called_at']})")
                continue
            try:
                p0 = fetch_price_on(c["ticker"], news_date)
                c["price_at_call"] = round(p0, 2)
                c["called_at"] = news_date
                changed = True
                print(f"  + {c['ticker']:8s} {c['price_at_call']} @ {news_date} を記録")
            except Exception as e:
                print(f"  ! {c['ticker']:8s} 取得失敗: {e}")
        if changed:
            with open(f, "w", encoding="utf-8") as fp:
                json.dump(d, fp, ensure_ascii=False, indent=2)
                fp.write("\n")
        print(os.path.basename(f), "→ 更新" if changed else "→ 変更なし")


def check_data(verify_tickers=True):
    """data/ の全JSONをスキーマ検証し、ティッカーの実在も確認する"""
    import re
    ok = True
    for f in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        name = os.path.basename(f)
        errs = []
        try:
            with open(f, encoding="utf-8") as fp:
                d = json.load(fp)
        except Exception as e:
            print(f"✗ {name}: JSONが壊れています ({e})")
            ok = False
            continue
        # スキーマ
        for k in ("id", "date", "news", "questions"):
            if k not in d:
                errs.append(f"必須キー欠落: {k}")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d.get("date", "")):
            errs.append("dateがYYYY-MM-DD形式でない")
        news = d.get("news", {})
        for k in ("source", "headline", "essence"):
            if not news.get(k):
                errs.append(f"news.{k} が空")
        qs = d.get("questions", [])
        if len(qs) != 6:
            errs.append(f"questionsが6問でない ({len(qs)}問)")
        correct_positions = []
        for i, q in enumerate(qs):
            opts = q.get("options", [])
            if len(opts) < 2:
                errs.append(f"Q{i+1}: 選択肢不足")
                continue
            c = q.get("correct", -1)
            if not (0 <= c < len(opts)):
                errs.append(f"Q{i+1}: correctが範囲外")
                continue
            correct_positions.append(c)
            if not q.get("reason"):
                errs.append(f"Q{i+1}: reasonが空")
                continue

            # --- 文字数バイアス（正解が長いと当てられてしまうメタ攻略の防止） ---
            ls = [len(o) for o in opts]
            spread = max(ls) - min(ls)
            margin = ls[c] - max(l for j, l in enumerate(ls) if j != c)
            if spread > 6:
                errs.append(f"Q{i+1}: 選択肢の文字数差が{spread}字（6字以内に。lens={ls}）")
            if ls[c] == max(ls) and ls.count(max(ls)) == 1 and margin > 2:
                errs.append(f"Q{i+1}: 正解が単独最長で+{margin}字突出（2字以内に。詳細はreasonへ）")

            # --- reason内の選択肢参照はトークン必須（シャッフル対応） ---
            for m in re.finditer(r'(?<![A-Za-z&{])([A-E])(?![A-Za-z}])', q["reason"]):
                errs.append(f"Q{i+1}: reasonに生の選択肢参照 '{m.group(1)}'（{{A}}形式で書く）")
                break
            for m in re.finditer(r'\{([A-E])\}', q["reason"]):
                if ord(m.group(1)) - 65 >= len(opts):
                    errs.append(f"Q{i+1}: トークン{{{m.group(1)}}}が選択肢数を超えている")

            # --- 用語解説 ---
            if not q.get("glossary"):
                errs.append(f"Q{i+1}: glossary（用語解説）がない")

        # --- 正解位置の分散（同一位置の3連続以上は禁止） ---
        for i in range(len(correct_positions) - 2):
            if correct_positions[i] == correct_positions[i+1] == correct_positions[i+2]:
                errs.append(f"correctがQ{i+1}〜Q{i+3}で同じ位置に3連続")
                break
        # ティッカー
        for c in d.get("calls", []):
            t = c.get("ticker", "")
            if not t:
                errs.append("callsにticker欠落")
                continue
            if c.get("direction") not in ("+", "-"):
                errs.append(f"{t}: directionは + / - のみ")
            # 表記の桁ミスはネットワークを使わずに先に弾く。
            # 香港は4桁ゼロ埋めでないとyfinanceが引けない（992.HK ✗ / 0992.HK ○）
            m = re.match(r"^([0-9A-Z]+)\.(HK|T|KS|KQ)$", t)
            if m:
                code, sfx = m.groups()
                if sfx == "HK" and not re.match(r"^\d{4,5}$", code):
                    errs.append(f"{t}: 香港のコードは4桁ゼロ埋めで書く（例 0992.HK）")
                if sfx in ("T", "KS", "KQ") and not re.match(r"^\d{3}[0-9A-Z]$|^\d{6}$", code):
                    errs.append(f"{t}: {sfx}のコード桁数が不正")
            if verify_tickers and not c.get("price_at_call"):
                try:
                    fetch_price(t)
                    print(f"  ✓ {t} 実在確認OK")
                except Exception as e:
                    errs.append(f"{t}: 株価を取得できない（ティッカー誤り？） {e}")
        if errs:
            ok = False
            print(f"✗ {name}")
            for e in errs:
                print(f"    - {e}")
        else:
            print(f"✓ {name}")
    print("\n結果:", "すべてOK 🎉" if ok else "エラーあり（上記を修正してください）")
    return ok


def lan_ip():
    """同一ネットワーク内からアクセスするためのローカルIPを推定"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    if "--record" in sys.argv:
        record_all()
    elif "--check" in sys.argv:
        check_data(verify_tickers="--offline" not in sys.argv)
    else:
        print("🎯 連想ゲーム")
        print("  PCから      : http://localhost:8000")
        ip = lan_ip()
        if ip:
            print(f"  スマホから  : http://{ip}:8000  （同一Wi-Fiに接続していること）")
        # 0.0.0.0 で待ち受けて同一ネットワークの端末からもアクセス可能にする
        try:
            from waitress import serve
            serve(app, host="0.0.0.0", port=8000, threads=8)
        except ImportError:
            app.run(host="0.0.0.0", port=8000, debug=False)
