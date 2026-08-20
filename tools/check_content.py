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
  6. 使い回しの罠: 同じ選択肢が複数の記事に登場しないこと（記事をまたぐ検査）
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
        # 記号バイアス: 正解だけにダッシュ等があると読まずに当てられる
        DASH = re.compile(r"──|—|―|‐‐|--|…")
        marks = [bool(DASH.search(o)) for o in opts]
        if marks[c] and sum(marks) == 1:
            errs.append(f"{tag}: 正解だけがダッシュ等の記号を含む（読まずに当てられる）")

        # --- 語尾バイアス（言い切っている選択肢を消すだけで絞れるのを防ぐ） ---
        # 罠を「〜のはずである」「必ず〜」で書き、正解だけ含みのある表現に
        # すると、内容を読まずに消去法で当てられる
        HEDGE = re.compile(r"はず(だ|である|です)?。?$|のはず|であるはず|わけがない|"
                           r"に決まって|必ず|すべて|一切|無関係|関係がない|影響を与えない")
        hedges = [bool(HEDGE.search(o)) for o in opts]
        if not hedges[c] and sum(hedges) >= 3:
            errs.append(f"{tag}: 罠3つが断定表現で正解だけ含みを持つ（消去法で当てられる）")

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

def norm_option(s):
    """選択肢の比較用の正規化。助詞・記号のゆれを吸収して同じ罠を同じとみなす。

    「世界のスマートフォンの出荷の台数の統計を確認していく」と
    「世界のスマートフォンの出荷台数の統計を確認していく」は同じ罠。
    """
    return re.sub(r"[のをだけ、。・\s]", "", s)


def check_corpus(targets):
    """記事をまたぐ検査: 選択肢の使い回しを見つける。

    背景（2026-08-20発見）: ⑥検証ポイントの罠が「世界のGDP成長率…」
    「日本の失業率…」といった汎用マクロ指標のテンプレになっており、
    125記事750問のうち100問が同じ罠を共有していた。そのうち82問は
    罠3本すべてが使い回しで、**記事を読まなくても「見覚えのない選択肢＝正解」**
    で当たる状態だった（使い回しの罠が正解になったことは一度も無い）。

    比較は data/ 全体に対して行う。1ファイルだけ検査するときも、
    corpus 全体と突き合わせないと使い回しは見つけられない。
    """
    seen = {}                       # 正規化した選択肢 -> [(記事id, 設問番号)]
    for p in sorted(glob.glob("data/*.json")):
        try:
            d = json.load(open(p))
        except Exception:
            continue
        for i, q in enumerate(d.get("questions", [])):
            for o in q.get("options", []):
                seen.setdefault(norm_option(o), []).append((d.get("id"), i + 1, o))

    target_ids = {os.path.basename(p)[:-5] for p in targets}
    out = {}
    for uses in seen.values():
        arts = {u[0] for u in uses}
        if len(arts) < 2:
            continue
        for sid, qn, text in uses:
            if sid not in target_ids:
                continue
            others = sorted(arts - {sid})
            out.setdefault(sid, []).append(
                f"q{qn}: 使い回しの罠（他{len(others)}記事と同一の選択肢）"
                f"「{text}」→ 例: {others[0]}")
    return out


def longest_hit_rate():
    """「一番長い選択肢を選ぶ」だけで何割当たるかを測る（偶然は25%）。

    1問ごとの検査（正解が単独最長なら突出2字以内）は通っていても、
    **正解がいつも最長寄り**なら、中身を読まずに長さの比較だけで当てられる。
    実測で全750問の50.0%、⑥検証ポイントに至っては67.2%だった（2026-08-20）。
    2字差でも、比べれば分かってしまう。

    最長が複数ある設問は「長さでは決められない」ので、当たりに数えない。
    ここは記事単位の合否ではないので警告として出す（是正は記事の書き換えが要る）。
    """
    hit = tot = 0
    per_step = {}
    for p in sorted(glob.glob("data/*.json")):
        try:
            d = json.load(open(p))
        except Exception:
            continue
        for q in d.get("questions", []):
            opts = q.get("options", [])
            c = q.get("correct", -1)
            if len(opts) != 4 or not (0 <= c < 4):
                continue
            L = [len(o) for o in opts]
            step = q.get("step", "?")
            s = per_step.setdefault(step, [0, 0])
            tot += 1
            s[1] += 1
            if L.count(max(L)) > 1:
                continue
            if L.index(max(L)) == c:
                hit += 1
                s[0] += 1
    return hit, tot, per_step


def main():
    targets = sys.argv[1:] or sorted(glob.glob("data/*.json"))
    corpus_errs = check_corpus(targets)
    total_err = 0
    for p in targets:
        errs = check_file(p) + corpus_errs.get(os.path.basename(p)[:-5], [])
        if errs:
            print(f"FAIL {p}")
            for e in errs:
                print(f"  - {e}")
            total_err += len(errs)
    hit, tot, per_step = longest_hit_rate()
    if tot:
        rate = hit / tot * 100
        mark = "⚠" if rate >= 35 else "✓"
        print(f"\n{mark} 長さバイアス: 一番長い選択肢を選ぶだけで {hit}/{tot} = {rate:.1f}% "
              f"当たる（偶然は25%）")
        worst = sorted(((v[0] / v[1] * 100, k, v) for k, v in per_step.items()
                        if v[1] >= 20 and v[0] / v[1] >= 0.45), reverse=True)
        for r, k, v in worst:
            print(f"    {k}: {v[0]}/{v[1]} = {r:.1f}%")

    if total_err:
        print(f"\nFAIL: {total_err}件の違反")
        sys.exit(1)
    print(f"PASS: {len(targets)}ファイル（ティッカー実在は npm run check / build で別途確認）")

if __name__ == "__main__":
    main()
