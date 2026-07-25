#!/bin/bash
# Material Symbols Rounded をローカルに同梱する（オフラインでもアイコン表示）
# 使い方: ./fetch_font.sh  → fonts/ にwoff2とlocal.cssが生成され、以後は自己ホスト優先
cd "$(dirname "$0")"
mkdir -p fonts
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
CSS_URL="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,400..700,0..1,0"
WOFF_URL=$(curl -s -A "$UA" "$CSS_URL" | grep -o 'https://fonts.gstatic.com[^)]*' | head -1)
if [ -z "$WOFF_URL" ]; then
  echo "取得に失敗しました（ネットワークを確認してください）"
  exit 1
fi
curl -s "$WOFF_URL" -o fonts/material-symbols-rounded.woff2
cat > fonts/local.css <<'EOF'
/* 自己ホスト版 Material Symbols Rounded（Googleフォントより後に読み込むため優先される） */
@font-face {
  font-family: 'Material Symbols Rounded';
  font-style: normal;
  font-weight: 400 700;
  src: url('material-symbols-rounded.woff2') format('woff2');
}
EOF
echo "done: $(ls -la fonts/material-symbols-rounded.woff2 | awk '{print $5}') bytes"
