"""compute_eval / bench_symbol と、モジュールの静的な健全性のテスト
実行: python3 test_server.py（ネットワーク不要）
"""
import ast
import builtins
import os

from server import compute_eval, bench_symbol, baseline_index, rate_series


def test_no_undefined_names():
    """server.py / build_static.py に未定義の名前が無いこと。

    2026-09-21に、使わなくなったと思って from server import から
    bench_symbol を外したが1箇所で使われたままで、ビルドが
    NameError で落ちた。import の整理は目視だと取りこぼすので機械で見る。
    """
    base = os.path.dirname(os.path.abspath(__file__))
    for fn in ("server.py", "build_static.py"):
        tree = ast.parse(open(os.path.join(base, fn), encoding="utf-8").read())
        # モジュール実行時に自動で入る名前
        defined = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(n.name)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                defined.add(n.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                defined.update((a.asname or a.name).split(".")[0] for a in n.names)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                defined.add(n.name)
            elif isinstance(n, ast.arg):
                defined.add(n.arg)
        missing = sorted({n.id for n in ast.walk(tree)
                          if isinstance(n, ast.Name)
                          and isinstance(n.ctx, ast.Load) and n.id not in defined})
        assert not missing, f"{fn} に未定義の名前: {missing}"
    print("✓ 未定義の名前なし（server.py / build_static.py）")


def test_rate_series_has_no_prices():
    """公開する系列に実価格が混ざらないこと（終値は rate へ変換される）"""
    hist = [{"iso": "2026-09-17", "d": "09/17", "p": 100.0},
            {"iso": "2026-09-18", "d": "09/18", "p": 110.0},
            {"iso": "2026-09-19", "d": "09/19", "p": 99.0}]
    i = baseline_index([h["iso"] for h in hist], "2026-09-18")
    assert i == 1, i
    s = rate_series(hist, hist[i]["p"])
    assert [r["rate"] for r in s] == [-9.091, 0.0, -10.0], s
    assert all(set(r) == {"date", "rate"} for r in s), s
    print("✓ 騰落率の系列に実価格が混ざらない")


def make_hist(prices, start_day=1):
    """7月start_day日からの連続営業日として履歴を作る"""
    return [{"iso": f"2026-07-{start_day + i:02d}", "d": f"07/{start_day + i:02d}", "p": p}
            for i, p in enumerate(prices)]


def test_bench_symbol():
    assert bench_symbol("7203.T") == ("^N225", "日経平均")
    assert bench_symbol("005930.KS") == ("^KS11", "KOSPI")
    assert bench_symbol("035720.KQ") == ("^KS11", "KOSPI")
    assert bench_symbol("NVDA") == ("^GSPC", "S&P500")
    print("✓ bench_symbol")


def test_t5_absolute_return():
    # ニュース日=07-08(idx7,価格100)、T+5=07-13(価格110)。ベンチは横ばい→相対=絶対
    hist = make_hist([95, 96, 97, 98, 99, 100, 100, 100, 102, 104, 106, 108, 110])
    bench = make_hist([1000] * 13)
    e = compute_eval(hist, bench, "2026-07-08")
    assert e["t5"]["status"] == "done"
    assert abs(e["t5"]["chg"] - 10.0) < 0.01, e["t5"]
    assert abs(e["t5"]["rel"] - 10.0) < 0.01, e["t5"]
    print("✓ T+5 絶対リターン（ベンチ横ばい→相対=絶対）")


def test_relative_return_cancels_market():
    # 銘柄+10%・ベンチ+10% → 相対リターンはゼロ（地合いで上がっただけは的中にしない）
    hist = make_hist([100, 102, 104, 106, 108, 110])
    bench = make_hist([1000, 1020, 1040, 1060, 1080, 1100])
    e = compute_eval(hist, bench, "2026-07-01")
    assert e["t5"]["status"] == "done"
    assert abs(e["t5"]["chg"] - 10.0) < 0.01
    assert abs(e["t5"]["rel"]) < 0.01, e["t5"]
    print("✓ 相対リターン（市場と同率上昇→相対ゼロ）")


def test_pending_window():
    # ニュース日から3営業日しか経っていない → T+5は経過待ち（あと3営業日）
    hist = make_hist([100, 101, 102, 103])
    bench = make_hist([1000] * 4)
    e = compute_eval(hist, bench, "2026-07-01")
    assert e["t5"]["status"] == "pending"
    assert e["t5"]["remaining"] == 2, e["t5"]
    assert e["t20"]["status"] == "pending"
    print("✓ 経過待ちウィンドウ")


def test_now_uses_latest():
    hist = make_hist([100, 100, 90])
    bench = make_hist([1000, 1000, 1000])
    e = compute_eval(hist, bench, "2026-07-01")
    assert abs(e["now"]["chg"] - (-10.0)) < 0.01
    print("✓ 現時点リターン")


if __name__ == "__main__":
    test_no_undefined_names()
    test_rate_series_has_no_prices()
    test_bench_symbol()
    test_t5_absolute_return()
    test_relative_return_cancels_market()
    test_pending_window()
    test_now_uses_latest()
    print("\nすべてのテストに合格 🎉")
