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
import os

from flask import Flask, jsonify, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

app = Flask(__name__, static_folder=None)

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


@app.route("/api/sessions")
def sessions():
    out = []
    for f in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        if os.path.basename(f) == "index.json":
            continue
        with open(f, encoding="utf-8") as fp:
            out.append(json.load(fp))
    return jsonify(out)


def fetch_price(ticker: str) -> float:
    """現在（直近）の株価"""
    import yfinance as yf
    tk = yf.Ticker(ticker)
    try:
        p = tk.fast_info["last_price"]
        if p:
            return float(p)
    except Exception:
        pass
    h = tk.history(period="5d")
    if len(h):
        return float(h["Close"].iloc[-1])
    raise RuntimeError(f"price unavailable: {ticker}")


def fetch_price_on(ticker: str, date_str: str) -> float:
    """指定日（ニュース日付）の終値。休場日はその直前の営業日終値。"""
    import yfinance as yf
    d0 = datetime.date.fromisoformat(date_str)
    start = d0 - datetime.timedelta(days=10)
    end = d0 + datetime.timedelta(days=1)
    h = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
    if len(h):
        return float(h["Close"].iloc[-1])
    raise RuntimeError(f"price unavailable on {date_str}: {ticker}")


def bench_symbol(ticker: str):
    """銘柄の市場に対応するベンチマーク指数"""
    if ticker.endswith(".T"):
        return "^N225", "日経平均"
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return "^KS11", "KOSPI"
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
    import yfinance as yf
    start = datetime.date.fromisoformat(start_str) - datetime.timedelta(days=7)
    end = datetime.date.today() + datetime.timedelta(days=1)
    h = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
    return [{"d": i.strftime("%m/%d"), "iso": i.strftime("%Y-%m-%d"),
             "p": round(float(row["Close"]), 2)}
            for i, row in h.iterrows()]


@app.route("/api/calls/<sid>")
def calls(sid):
    path = os.path.join(DATA, f"{sid}.json")
    if not os.path.exists(path):
        return jsonify({"error": "session not found"}), 404
    with open(path, encoding="utf-8") as fp:
        d = json.load(fp)

    call_list = d.get("calls", [])
    if not call_list:
        return jsonify({"calls": []})

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

    return jsonify({"calls": results})


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
        for i, q in enumerate(qs):
            if len(q.get("options", [])) < 2:
                errs.append(f"Q{i+1}: 選択肢不足")
            elif not (0 <= q.get("correct", -1) < len(q["options"])):
                errs.append(f"Q{i+1}: correctが範囲外")
            if not q.get("reason"):
                errs.append(f"Q{i+1}: reasonが空")
        # ティッカー
        for c in d.get("calls", []):
            t = c.get("ticker", "")
            if not t:
                errs.append("callsにticker欠落")
                continue
            if c.get("direction") not in ("+", "-"):
                errs.append(f"{t}: directionは + / - のみ")
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
