#!/bin/bash
# 連想ゲーム Webアプリ起動スクリプト
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "初回セットアップ: 仮想環境を作成します…"
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt
python server.py
