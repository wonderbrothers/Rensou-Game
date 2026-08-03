#!/usr/bin/env python3
"""コンテンツ検査（npm run check の代替・オフライン版）

Claudeのサンドボックス等で `npm run check` が実行できない場合に必ず使う。
ティッカーの実在確認（要ネットワーク）以外の全項目を検査する。

使い方:
  python3 tools/check_content.py            # data/ 全ファイル
  python3 tools/check_content.py data/x.json  # 指定ファイルのみ

検査項目（CONTENT_GUIDE.md §4.5 と同一）:
  1. 文字数バイアス: 4択の文字数差6字以内／正解が単独最長なら突出2字以内
  2. reason内の選択肢参照が {A}{B}{C}{D} トークン形式（生のA〜Dはエラー）
  3. 全設問に glossary（2語以上）
  4. correct の同一位置3連続なし・6問中同一位置4回以上なし
  5. スキーマ: id=ファイル名 / date / news4キー / 6問 / 各4択 / correct範囲 /
     calls3銘柄・方向は＋2−1か＋1−2 / learning
"""
import json, re, sys, glob, os

def check_file(path):
    errs = []
    fid = os.path.basename(path)[:-5]
    try:
        d = json.load(open(path))
    except Exception as e:
        return [f"JSONパースエラー: {e}"]
    if d.get("id") != fid:
        errs.append(f"idがファイル名と不一致: {d.get('id')} != {fid}")
    if not d.get("date"):
        errs.append("dateがない")
    news = d.get("news", {})
    if set(news.keys()) != {"source", "headline", "source_url", "essence"}:
        errs.append(f"newsのキーが不正: {sorted(news.keys())}")
    calls = d.get("calls", [])
    if len(calls) != 3:
        errs.append(f"callsが3銘柄でない: {len(calls)}")
    dirs = sorted(c.get("direction") for c in calls)
    if dirs not in (["+", "+", "-"], ["+", "-", "-"]):
        errs.append(f"callsの方向バランスが不正: {dirs}")
    # callsのティッカー表記（yfinanceが引ける形か）
    #   香港は4桁ゼロ埋め（992.HK ではなく 0992.HK）、日本・韓国は数字4桁＋接尾辞。
    #   桁が足りないと「price unavailable」でビルド時に落ちるため、ここで先に止める。
    for c in calls:
        t = c.get("ticker", "")
        if not t:
            errs.append("callsにtickerがない")
            continue
        m = re.match(r"^([0-9A-Z]+)\.(HK|T|KS|KQ)$", t)
        if m:
            code, sfx = m.groups()
            if sfx == "HK" and not re.match(r"^\d{4,5}$", code):
                errs.append(f"ticker表記が不正: {t}（香港は4桁ゼロ埋め。例 0992.HK）")
            if sfx in ("T", "KS", "KQ") and not re.match(r"^\d{3}[0-9A-Z]$|^\d{6}$", code):
                errs.append(f"ticker表記が不正: {t}（{sfx}は4桁もしくは6桁）")
        elif not re.match(r"^[A-Z][A-Z.\-]{0,6}$", t):
            errs.append(f"ticker表記が不正: {t}")

    qs = d.get("questions", [])
    if len(qs) != 6:
        errs.append(f"設問が6問でない: {len(qs)}")
    cs = []
    for i, q in enumerate(qs):
        tag = f"q{i+1}"
        opts = q.get("options", [])
        c = q.get("correct", -1)
        cs.append(c)
        if len(opts) != 4:
            errs.append(f"{tag}: 選択肢が4つでない")
            continue
        if not (0 <= c < 4):
            errs.append(f"{tag}: correctが範囲外: {c}")
            continue
        # 1. 文字数バイアス
        L = [len(o) for o in opts]
        spread = max(L) - min(L)
        if spread > 6:
            errs.append(f"{tag}: 文字数差{spread}字（6字以内）len={L}")
        if L[c] == max(L) and L.count(max(L)) == 1 and sorted(L)[-1] - sorted(L)[-2] > 2:
            errs.append(f"{tag}: 正解が単独最長で突出{sorted(L)[-1]-sorted(L)[-2]}字（2字以内）len={L} correct={c}")
        # 2. トークン形式
        reason = q.get("reason", "")
        if not reason:
            errs.append(f"{tag}: reasonがない")
        bare = re.findall(r"(?<![A-Za-z{&])([A-D])(?![A-Za-z}&])", reason)
        if bare:
            errs.append(f"{tag}: reasonに生の選択肢参照: {bare}")
        # 3. glossary
        g = q.get("glossary", [])
        if len(g) < 2:
            errs.append(f"{tag}: glossaryが2語未満")
    # 4. correct分散
    for k in range(len(cs) - 2):
        if cs[k] == cs[k + 1] == cs[k + 2]:
            errs.append(f"correctが3問連続で同位置: {cs}")
            break
    if cs and max(cs.count(v) for v in set(cs)) >= 4:
        errs.append(f"correctが同一位置に4回以上: {cs}")
    if not d.get("learning"):
        errs.append("learningがない")
    return errs

def main():
    targets = sys.argv[1:] or sorted(glob.glob("data/*.json"))
    total_err = 0
    for p in targets:
        errs = check_file(p)
        if errs:
            print(f"FAIL {p}")
            for e in errs:
                print(f"  - {e}")
            total_err += len(errs)
    if total_err:
        print(f"\nFAIL: {total_err}件の違反")
        sys.exit(1)
    print(f"PASS: {len(targets)}ファイル（ティッカー実在は npm run check / build で別途確認）")

if __name__ == "__main__":
    main()
