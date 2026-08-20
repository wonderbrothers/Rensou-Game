#!/usr/bin/env python3
"""⑥検証ポイントの「使い回しの罠」を、記事固有の罠へ差し替える作業用ツール。

使い回しの罠の是正（CONTENT_GUIDE 4.2）は112記事に及ぶため、
差し替えの適用と字数バイアスの検算を手作業でやると必ず取りこぼす。
JSONのパッチを食わせて、適用と検査を機械にやらせるためのもの。

パッチの形式（1記事1エントリ）:
  {
    "<記事id>": {
      "options": {"A": "新しい罠", "B": "新しい罠", "D": "新しい罠"},
      "q": 6,
      "reason_from": "{A}{B}{D}は経路が遠くこの仮説を分離できない。",
      "reason_to":   "{A}は…。{B}は…。{D}は…。"
    }
  }

options のキーは options 配列の位置（A=1番目）。正解の位置は動かさない。
"q" は設問番号（1始まり・省略時は6）。
reason_from はその記事の reason に完全一致で1回だけ現れること（曖昧なら失敗する）。

使い方:
  python3 tools/apply_q6.py patch.json          # 検算だけ（書き込まない）
  python3 tools/apply_q6.py patch.json --write  # 適用する
"""
import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")


def load_corpus_options(skip_ids):
    """data/ 全体の選択肢を正規化して集める（差し替え先の重複検査用）。

    差し替える罠どうしがぶつかると、使い回しを別の使い回しに置き換える
    ことになる。実際に「アドバンテストの株価の…」を2記事へ同時に入れて
    しまったので、ここで機械的に止める。
    """
    sys.path.insert(0, os.path.join(BASE, "tools"))
    from check_content import norm_option
    seen = {}
    for p in sorted(glob.glob(os.path.join(DATA, "*.json"))):
        with open(p, encoding="utf-8") as fp:
            d = json.load(fp)
        if d.get("id") in skip_ids:
            continue
        for q in d.get("questions", []):
            for o in q.get("options", []):
                seen.setdefault(norm_option(o), d["id"])
    return seen, norm_option


def check_lengths(opts, correct):
    """文字数バイアスの検算（CONTENT_GUIDE 4）"""
    L = [len(o) for o in opts]
    errs = []
    if max(L) - min(L) > 6:
        errs.append(f"文字数差{max(L)-min(L)}字（6字以内）len={L}")
    if L[correct] == max(L) and L.count(max(L)) == 1:
        gap = sorted(L)[-1] - sorted(L)[-2]
        if gap > 2:
            errs.append(f"正解が単独最長で突出{gap}字（2字以内）len={L}")
    return errs


def apply_patch(patch, write=False):
    corpus, norm = load_corpus_options(set(patch))
    used = {}                       # このパッチ内での重複も見る
    total_err = 0
    for sid, spec in patch.items():
        path = os.path.join(DATA, f"{sid}.json")
        if not os.path.exists(path):
            print(f"✗ {sid}: ファイルが無い")
            total_err += 1
            continue
        with open(path, encoding="utf-8") as fp:
            d = json.load(fp)

        # 既定は⑥（6問目）。"q": 2 のように指定すれば他のステップも直せる
        qi = spec.get("q", 6) - 1
        q = d["questions"][qi]

        opts = list(q["options"])
        errs = []
        for key, text in spec["options"].items():
            i = ord(key) - 65
            if i == q["correct"]:
                errs.append(f"正解の位置({key})を差し替えようとしている")
                continue
            opts[i] = text
            k = norm(text)
            if k in corpus:
                errs.append(f"{key}: 他の記事と同じ選択肢になっている（{corpus[k]}）: {text}")
            if k in used:
                errs.append(f"{key}: このパッチ内で重複している（{used[k]}）: {text}")
            used[k] = sid

        errs += check_lengths(opts, q["correct"])

        reason = q["reason"]
        # 罠を1本だけ差し替え直すときは reason を触らなくてよい
        frm, to = spec.get("reason_from"), spec.get("reason_to")
        if frm is None:
            pass
        elif reason.count(frm) != 1:
            errs.append(f"reason_from が{reason.count(frm)}回一致（1回であること）: {frm[:40]}")
        else:
            reason = reason.replace(frm, to)

        if errs:
            print(f"✗ {sid}")
            for e in errs:
                print(f"    - {e}")
            total_err += len(errs)
            continue

        print(f"✓ {sid}  len={[len(o) for o in opts]} correct={q['correct']}")
        if write:
            q["options"] = opts
            q["reason"] = reason
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(d, fp, ensure_ascii=False, indent=2)
                fp.write("\n")
    return total_err


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    patch = json.load(open(sys.argv[1], encoding="utf-8"))
    write = "--write" in sys.argv
    n = apply_patch(patch, write)
    print(f"\n{'適用' if write else '検算'}: {len(patch)}記事 / エラー{n}件")
    sys.exit(1 if n else 0)


if __name__ == "__main__":
    main()
