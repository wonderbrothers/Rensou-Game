#!/bin/bash
# 全ニュースの遊びコールに基準株価を一括記録（venvを自動で使う）
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "初回セットアップ: 仮想環境を作成します…"
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt
python server.py --record
