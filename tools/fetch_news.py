#!/usr/bin/env python3
"""公式RSSから、連想ゲームの作問候補になりそうな記事を集める。

出力は Markdown。1件につき「公開日 / ソース / [タイトル](URL)」を出す。
そのまま Claude に貼れば作問に入れる形にしてある。

使い方:
    python3 tools/fetch_news.py                # 直近24時間ぶん
    python3 tools/fetch_news.py --hours 48     # 期間を変える
    python3 tools/fetch_news.py --all          # 既出記事も含めて全部出す
    python3 tools/fetch_news.py --json         # 機械処理用にJSONで出す

方針:
- 公式が配信目的で出しているRSSのみを使う（スクレイピングはしない）。
  Bloombergなど規約で自動取得を禁じているサイトは対象外。
  第三者のRSS変換サービスも、元サイトの規約に抵触しうるため使わない。
- ロイター・日経は個人向けRSSを終了しており公式フィードが無い。両社のネタは
  購読者であるユーザーが直接サイトを見て選ぶ運用を続ける。
- 依存を増やさないため標準ライブラリだけで動く。
- data/*.json の source_url と照合し、作問済みの記事は既定で除外する。
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
UA = "RensouGame/1.0 (news candidate collector; contact: sato@wonder-bros.com)"
TIMEOUT = 12

# 疎通確認（--probe）で生きていたフィードだけを取得先にする。
# ロイター・日経は個人向けRSSの配信を終えており（401/404）公式フィードが
# 存在しない。第三者のRSS変換サービスは元サイトの規約に抵触しうるため使わない。
# 両社のネタは従来どおり、購読者であるユーザーが直接サイトを見て選ぶ。
#
# NHK経済(81件) … 速報性が高く企業決算・指標が厚い。作問の主力
# Yahoo!経済(8件) … 各社配信の主要トピック。ロイター・日経の記事も流れる
# 東洋経済/ダイヤモンド … 解説寄り。背景の厚い記事が拾える
FEEDS = [
    ("NHK", ["https://www.nhk.or.jp/rss/news/cat5.xml"]),
    ("Yahoo!ニュース", ["https://news.yahoo.co.jp/rss/topics/business.xml"]),
    ("東洋経済", ["https://toyokeizai.net/list/feed/rss"]),
    ("ダイヤモンド", ["https://diamond.jp/list/feed/rss/dol"]),
]

# --probe 用。公式が配信目的で出しているフィードのみを候補にする
PROBE = [
    ("NHK経済", "https://www.nhk.or.jp/rss/news/cat5.xml"),
    ("NHK主要", "https://www.nhk.or.jp/rss/news/cat0.xml"),
    ("Yahoo!経済", "https://news.yahoo.co.jp/rss/topics/business.xml"),
    ("Yahoo!主要", "https://news.yahoo.co.jp/rss/topics/top-picks.xml"),
    ("Yahoo!国際", "https://news.yahoo.co.jp/rss/topics/world.xml"),
    ("東洋経済", "https://toyokeizai.net/list/feed/rss"),
    ("ダイヤモンド", "https://diamond.jp/list/feed/rss/dol"),
    ("ITmedia ビジネス", "https://rss.itmedia.co.jp/rss/2.0/business.xml"),
    # ロイター・日経は2026-08-06時点で公式フィードが応答しない（401/404）
]

# --- 記事の絞り込み ---------------------------------------------------------
# 連想ゲームの材料になるのは「市場が動く新しい事実」。生活・健康・教育などの
# 読み物は、面白くても作問には使えないため落とす。
#
# 判定は3段階:
#   ① NGカテゴリ … 配信元がタイトルに埋めているジャンル表記で落とす
#   ② NGワード   … カテゴリが無い配信元向けの保険
#   ③ 材料ワード … 相場・政策・企業活動を動かす語が1つでもあれば採用

# 東洋経済は「… | ライフ | 東洋経済オンライン」の形でジャンルが入る
NG_CATEGORIES = ["ライフ", "キャリア・教育", "読書", "スポーツ", "エンタメ"]

NG_WORDS = [
    # 健康・生活
    "健康法", "セルフケア", "ダイエット", "レシピ", "食べ方", "食べ方",
    "睡眠", "ストレッチ", "節約術", "片づけ", "掃除", "収納", "住まい方",
    # 教育・キャリア論・人生訓
    "子育て", "受験", "偏差値", "習い事", "勉強法", "親子", "夫婦", "恋愛",
    "の教え", "生き方", "後悔", "人生", "心理学", "処世術", "話し方", "コミュ",
    # 娯楽
    "ドラマ", "アニメ", "映画", "芸能", "占い", "グルメ", "旅行", "観光名所",
    "ファッション", "推し活", "ペット",
]

# 相場・政策・企業活動を動かす材料。政治も対象（政策は市場の材料になるため）
MATERIAL = [
    # 金融政策・金利・為替
    "日銀", "FRB", "ECB", "利上げ", "利下げ", "金融政策", "政策金利", "金利",
    "国債", "債券", "利回り", "為替", "円安", "円高", "介入", "ドル", "ユーロ",
    # 相場
    "株価", "日経平均", "TOPIX", "ダウ", "ナスダック", "S&P", "相場", "急騰", "急落",
    "上昇", "下落", "反発", "続落", "最高値", "安値", "時価総額",
    # 企業活動
    "決算", "営業利益", "最終利益", "純利益", "増益", "減益", "赤字", "黒字",
    "上方修正", "下方修正", "業績予想", "配当", "自社株買い", "増資",
    "買収", "TOB", "経営統合", "資本提携", "業務提携", "出資", "売却", "撤退",
    "上場", "IPO", "工場", "増産", "減産", "リコール",
    "連携", "協定", "締結", "契約", "受注", "融資", "商機", "参入", "新工場",
    # 政治・政策・規制
    "首相", "政権", "内閣", "選挙", "国会", "法案", "予算", "税制", "増税", "減税",
    "補助金", "規制", "認可", "独禁法", "公取委", "制裁", "関税", "通商", "貿易協定",
    # 地政学・資源
    "中東", "ウクライナ", "台湾", "南シナ海", "地政学", "紛争", "原油", "天然ガス",
    "レアアース", "供給網", "サプライチェーン",
    # マクロ指標
    "インフレ", "物価", "CPI", "GDP", "景気", "雇用統計", "失業率", "貿易収支",
    "経常収支", "設備投資", "消費支出", "景況感",
    # 産業テーマ
    "半導体", "EV", "電池", "生成AI", "データセンター", "脱炭素", "再エネ", "原発",
    "ロボット", "ヒューマノイド", "新薬", "医薬", "創薬", "薬価", "治験", "特許",
    "治療薬", "がん治療", "承認申請",   # 製薬は市場材料。健康読み物はNGカテゴリ側で落とす
]

def category_of(title):
    """配信元がタイトルに埋めたジャンル表記を取り出す（無ければ空）"""
    m = re.search(r"[|｜]\s*([^|｜]+?)\s*[|｜]\s*東洋経済オンライン\s*$", title)
    return m.group(1).strip() if m else ""


def looks_relevant(title):
    """作問の材料になりそうか"""
    if category_of(title) in NG_CATEGORIES:
        return False
    if any(w in title for w in NG_WORDS):
        return False
    return any(w in title for w in MATERIAL)


# --- 同一トピックの重複除去 -------------------------------------------------
# 同じ出来事が複数の配信元から流れてくる（例: SBI新生銀行の地銀連携が
# NHKとYahoo!の両方に出る）。URLが違うので素通りしてしまうため、
# 見出しの類似で束ねてどちらか一方だけを残す。
# 残す側は FEEDS の並び順（＝配信元の優先度）で決める。Yahoo!のURLは
# 一定期間で消えるため、原典を出しているNHK等を優先する。

SOURCE_RANK = {name: i for i, (name, _) in enumerate(FEEDS)}
DUP_THRESHOLD = 0.34          # 見出し全体の一致率（bigram）

# 見出しの区切りになる記号
SEP = re.compile(r"[｜|【】〈〉《》「」『』｢｣（）()\[\]、。,.・:：/／…‥\-―ー－\s”“\"'0-9０-９]+")


def bigrams(t):
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


def norm_for_compare(title):
    """比較用に見出しを正規化する（表示用のタイトルには手を触れない）"""
    return SEP.sub("", title)


def head_word(title):
    """見出しの先頭に来る語＝話の主役を取り出す。

    日本語の見出しは主語が先頭に来ることが多い。全体の一致率だけで見ると
    「ホンダ 4-6月決算 営業利益が過去最高」と「トヨタ 4-6月決算 …」のように
    文の型が同じ別会社のニュースを同一視してしまうため、主役の一致を必須にする。
    取りこぼし（同じ出来事が2件残る）は目視で分かるが、別の出来事を消すと
    候補から永久に消えてしまうので、安全側に倒している。
    """
    t = SEP.sub(" ", title)
    m = re.search(r"[ァ-ヶ][ァ-ヶー]+|[一-龥]{2,}|[A-Za-z]{2,}", t)
    return m.group(0) if m else ""


def similar(a, b):
    """2つの見出しが同じ出来事を指していそうか"""
    if head_word(a) != head_word(b):
        return False
    A, B = bigrams(norm_for_compare(a)), bigrams(norm_for_compare(b))
    if not A or not B:
        return False
    # 短い方がどれだけ長い方に含まれるか（見出しの詳しさが違っても拾える）
    return len(A & B) / min(len(A), len(B)) >= DUP_THRESHOLD


def dedupe_by_title(rows):
    """同一トピックは1件に。優先度の高い配信元・情報量の多い見出しを残す"""
    kept = []
    for r in rows:
        hit = None
        for i, k in enumerate(kept):
            if similar(r["title"], k["title"]):
                hit = i
                break
        if hit is None:
            kept.append(r)
            continue
        cur = kept[hit]
        mine = (SOURCE_RANK.get(r["source"], 99), -len(r["title"]))
        theirs = (SOURCE_RANK.get(cur["source"], 99), -len(cur["title"]))
        if mine < theirs:
            kept[hit] = r
    return kept



def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def text_of(el, *names):
    """名前空間の有無に関わらず子要素のテキストを拾う"""
    for n in names:
        for child in el:
            tag = child.tag.split("}")[-1].lower()
            if tag == n.lower() and (child.text or "").strip():
                return child.text.strip()
    return ""


def link_of(el):
    """RSS/RDF は <link>本文、Atom は <link href="…"> と形式が違う"""
    for child in el:
        if child.tag.split("}")[-1].lower() != "link":
            continue
        if (child.text or "").strip():
            return child.text.strip()
        href = child.get("href")
        if href and child.get("rel") in (None, "alternate"):
            return href
    return ""


def parse_date(s):
    """RFC822（RSS）とISO8601（Atom/RDF）の両方を受ける"""
    s = (s or "").strip()
    if not s:
        return None
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(s)
        if d:
            return d.astimezone(datetime.timezone.utc)
    except Exception:
        pass
    try:
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def parse_feed(xml_bytes, source):
    """RSS2.0 / RDF / Atom のいずれでも記事の配列を返す"""
    root = ET.fromstring(xml_bytes)
    items = []
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag not in ("item", "entry"):
            continue
        title = text_of(el, "title")
        url = link_of(el)
        if not (title and url):
            continue
        pub = parse_date(text_of(el, "pubDate", "published", "updated", "date"))
        items.append({"source": source, "title": re.sub(r"\s+", " ", title),
                      "url": url, "published": pub})
    return items


def collect(hours):
    """各社から1本ずつ取得する。失敗した社は理由を添えて報告する"""
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    out, errors = [], []
    for source, urls in FEEDS:
        got = False
        last = ""
        for u in urls:
            try:
                items = parse_feed(fetch(u), source)
            except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, OSError) as e:
                last = f"{type(e).__name__}: {e}"
                continue
            if not items:
                last = "記事が0件（形式が変わった可能性）"
                continue
            for it in items:
                # 日付が取れない記事は落とさず残す（判断は人に委ねる）
                if it["published"] and it["published"] < since:
                    continue
                out.append(it)
            got = True
            break
        if not got:
            errors.append(f"{source}: 取得できませんでした（{last}）")
    return out, errors


def used_urls():
    """作問済みの記事URL（data/*.json の source_url）"""
    used = set()
    for f in glob.glob(os.path.join(DATA, "*.json")):
        try:
            with open(f, encoding="utf-8") as fp:
                d = json.load(fp)
            u = (d.get("news") or {}).get("source_url")
            if u:
                used.add(u.split("?")[0].rstrip("/"))
        except Exception:
            pass
    return used


def probe():
    """候補フィードの疎通と中身を1本ずつ確認する。

    報道各社はRSSの提供方針を頻繁に変えるため、どれが生きているかを
    実行環境（＝ネットに出られる側）で確かめられるようにしてある。
    """
    print("# フィードの疎通確認\n")
    alive = []
    for name, url in PROBE:
        try:
            body = fetch(url)
        except Exception as e:
            print(f"✗ {name:14} {type(e).__name__}: {str(e)[:44]}")
            print(f"   {url}")
            continue
        try:
            items = parse_feed(body, name)
        except ET.ParseError as e:
            print(f"△ {name:14} 取得OKだがXMLとして読めない（{str(e)[:36]}）")
            print(f"   {url}")
            continue
        if not items:
            print(f"△ {name:14} 取得OKだが記事0件")
            print(f"   {url}")
            continue
        alive.append((name, url, len(items)))
        newest = items[0]
        d = newest["published"].astimezone().strftime("%m/%d %H:%M") if newest["published"] else "日付不明"
        print(f"✓ {name:14} {len(items):3}件  最新: {d}  {newest['title'][:28]}")
        print(f"   {url}")
    print(f"\n生きているフィード: {len(alive)}/{len(PROBE)}")
    if alive:
        print("\nこの結果を Claude に貼ると FEEDS を正しいURLに直せます。")
    return 0 if alive else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="候補フィードの疎通を確認する")
    ap.add_argument("--hours", type=int, default=24, help="何時間前までを対象にするか")
    ap.add_argument("--all", action="store_true", help="作問済みの記事も除外しない")
    ap.add_argument("--raw", action="store_true", help="経済ニュースの絞り込みをしない")
    ap.add_argument("--json", action="store_true", help="JSONで出力する")
    a = ap.parse_args()

    if a.probe:
        return probe()

    items, errors = collect(a.hours)
    used = set() if a.all else used_urls()

    seen, rows = set(), []
    for it in items:
        key = it["url"].split("?")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        if key in used:
            continue
        if not a.raw and not looks_relevant(it["title"]):
            continue
        rows.append(it)

    rows.sort(key=lambda r: (r["published"] or datetime.datetime.min.replace(
        tzinfo=datetime.timezone.utc)), reverse=True)
    before = len(rows)
    rows = dedupe_by_title(rows)
    merged = before - len(rows)

    if a.json:
        print(json.dumps([{
            "date": (r["published"].astimezone().strftime("%Y-%m-%d") if r["published"] else ""),
            "source": r["source"], "title": r["title"], "url": r["url"],
        } for r in rows], ensure_ascii=False, indent=2))
        return 0 if rows else 1

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"# ニュース候補（直近{a.hours}時間・取得 {now}）\n")
    if not rows:
        print("条件に合う記事がありませんでした。`--hours 48` や `--raw` も試してください。\n")
    for r in rows:
        d = r["published"].astimezone().strftime("%Y-%m-%d") if r["published"] else "日付不明"
        print(f"- {d} / {r['source']} / [{r['title']}]({r['url']})")

    if errors:
        print("\n## 取得できなかったフィード\n")
        for e in errors:
            print(f"- {e}")
        print("\nフィードのURLが変わった可能性があります。"
              "`tools/fetch_news.py` の FEEDS を確認してください。")
    if merged:
        print(f"\n（同じ出来事を伝える {merged}件は1件にまとめました）")
    if not a.all:
        print(f"（作問済み {len(used)}件は除外しています。全部見るには --all）")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
