"""compute_eval / bench_symbol のユニットテスト
実行: python3 test_server.py（ネットワーク不要）
"""
from server import compute_eval, bench_symbol


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
    test_bench_symbol()
    test_t5_absolute_return()
    test_relative_return_cancels_market()
    test_pending_window()
    test_now_uses_latest()
    print("\nすべてのテストに合格 🎉")
