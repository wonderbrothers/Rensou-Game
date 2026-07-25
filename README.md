# 連想ゲーム 🎯

ニュースを起点に「①本質 → ②一次影響− → ③相対優位＋ → ④二次影響 → ⑤逆シナリオ → ⑥検証」の6ステップで連想力を鍛えるWebアプリ。

## サーバーの起動方法

### かんたん起動（推奨）

```bash
cd ~/Claude/Projects/GS-Game
npm run dev        # または ./serve.sh
```

株価の一括記録も npm から:

```bash
npm run record     # または ./record.sh
```

初回は仮想環境（.venv）の作成と依存パッケージ（Flask / yfinance）のインストールが自動で走ります。
起動したらブラウザで **http://localhost:8000** を開く。停止は `Ctrl+C`。

※ `./serve.sh` で「permission denied」が出た場合は一度だけ実行権限を付与:

```bash
chmod +x serve.sh
```

### 手動起動（serve.shを使わない場合）

```bash
cd ~/Claude/Projects/GS-Game
python3 -m venv .venv                # 初回のみ
source .venv/bin/activate
pip install -r requirements.txt     # 初回のみ
python server.py
```

### 株価の一括記録（プレイせずに基準価格を記録する）

```bash
./record.sh
```

### その他のコマンド

```bash
npm run check   # data/ のJSONスキーマ検証＋ティッカー実在確認
npm run font    # Material Symbolsフォントをローカル同梱（オフラインでもアイコン表示）
```

### スマホのホーム画面に追加（PWA）

manifest対応済み。スマホのブラウザで開き「ホーム画面に追加」すると、アプリのように全画面で起動します。

### 成績のバックアップ

成績表ページの「バックアップを書き出す/読み込む」で、成績JSONのエクスポート・別端末への引っ越しができます。

または手動で:

```bash
source .venv/bin/activate    # venvの有効化を忘れずに
python server.py --record
```

※ `python server.py --record` を venv の外で実行すると `ModuleNotFoundError: No module named 'flask'` になります。必ず `./record.sh` を使うか、先に `source .venv/bin/activate` を実行してください。

全ニュースの遊びコールに、ニュース日付の終値を `price_at_call` として書き込みます（記録済みの銘柄はスキップ）。

### スマホからアクセスする（同一Wi-Fi）

サーバーは `0.0.0.0` で待ち受けているため、同じWi-Fiに繋がったスマホからもアクセスできます。
起動時のコンソールに表示される「スマホから: http://192.168.x.x:8000」のURLをスマホのブラウザで開いてください。

- つながらない場合は、macOSの「システム設定 > ネットワーク > ファイアウォール」でPythonの受信接続を許可
- スマホとPCが同じネットワーク（同じWi-Fi、ゲスト用Wi-Fiは不可の場合あり）にあることを確認

### ポートを変えたい場合

`server.py` 末尾の `app.run(host="0.0.0.0", port=8000)` の `port` を変更してください。

## 遊びコール（株価の答え合わせ）

各ニュースには「シニアアナリストの遊びコール」（この銘柄が＋/−になるかも、という遊びの予想）が付いています。

- 初回プレイ時: サーバーがyfinanceで**ニュース日付の終値**を取得し、基準価格としてJSONに記録
- 再プレイ時: 基準価格と現在価格を比較して **🎯的中／💨外れ／😐ほぼ横ばい** を表示

※ あくまで遊びです。連想の多くは既に株価に織り込まれています。外れた理由（織り込み済み？逆シナリオ発動？）を考えるのが本番。投資助言ではありません。

## 構成

```
index.html       … マークアップ
style.css        … デザイン
app.js           … フロントロジック
server.py        … Flaskサーバー（静的配信＋株価API）
serve.sh         … 起動スクリプト
requirements.txt … 依存パッケージ
data/
  YYYY-MM-DD_<slug>.json … ニュース1本＝1ファイル（自動で一覧に反映）
```

## API

- `GET /api/sessions` … data/ の全セッションJSONを返す（index.json不要・自動走査）
- `GET /api/calls/<id>` … コールの株価取得。初回は記録、以降は答え合わせ

## ニュースの渡し方（Claudeとの運用）

チャットで以下の3点を渡すと、ClaudeがJSONを作成して `data/` に追加します：

1. **日付**（例: 2026-07-24）※省略時は日本時間（JST）のその時点の日付を自動設定
2. **出典元**（例: 日経新聞、ブルームバーグ）
3. **ニュースタイトル**

株価の基準（`price_at_call`）は**ニュースの日付の終値**で記録されるため、初回プレイでも「ニュース時→現在」の答え合わせができます。

## 問題の追加方法

`data/` に `YYYY-MM-DD_<slug>.json` を置くだけ（一覧は自動反映）。

### JSONスキーマ

```json
{
  "id": "2026-07-24_example",
  "date": "2026-07-24",
  "categories": ["通商・関税"],
  "news": {
    "source": "ブルームバーグ",
    "headline": "ニュース見出し",
    "source_url": "https://...",
    "essence": "1〜2文の本質"
  },
  "calls": [
    { "ticker": "7203.T", "name": "トヨタ自動車", "direction": "-",
      "basis": "コールの根拠1文" }
  ],
  "questions": [
    { "step": "① 本質を掴む", "q": "設問文",
      "options": ["A", "B", "C", "D"], "correct": 1,
      "reason": "正誤の理由（引っかけの解説を含む）" }
  ],
  "learning": "このイベントに使える汎用連想パターン"
}
```

`calls[].price_at_call` と `called_at` は初回プレイ時にサーバーが自動で書き込みます。
