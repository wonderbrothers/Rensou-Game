#!/bin/bash
# 連想ゲーム Webアプリ起動スクリプト
cd "$(dirname "$0")"

# 公開用コピー（Rensou-Game-public）で起動すると、npm run build するまで
# 同期されていない古いコードとデータを配信してしまう。実際に2回、
# 「直したのに反映されない」として時間を溶かしたので、ここで止める。
# 判定は CLAUDE.md の有無（sync-public.sh が同期から除外している）。
if [ ! -f CLAUDE.md ]; then
  echo "✗ ここは公開用のコピーです（開発リポジトリではありません）: $(pwd)"
  echo ""
  echo "  このフォルダの中身は npm run build で同期された結果なので、"
  echo "  編集しても反映されず、古い内容がそのまま表示されます。"
  echo ""
  echo "  開発サーバーは開発リポジトリで起動してください:"
  echo "    cd ~/Claude/Projects/Rensou-Game && npm run dev"
  exit 1
fi
if [ ! -d .venv ]; then
  echo "初回セットアップ: 仮想環境を作成します…"
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt
python server.py
