#!/usr/bin/env bash
# MSI Analysis Application - 初回セットアップ (macOS / Linux)
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/App"

echo "=== MSI Analysis Application Setup ==="

# [1/4] Python チェック
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 が見つかりません。Python 3.10+ をインストールしてください。"
  echo "  Mac:   brew install python@3.11   または https://www.python.org/downloads/"
  echo "  Linux: apt install python3 python3-pip   等"
  exit 1
fi
echo "[1/4] $(python3 --version) が見つかりました。"

# [2/4] Python パッケージ
echo "[2/4] Python パッケージをインストール中..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

# [3/4] R チェック (任意)
if ! command -v Rscript >/dev/null 2>&1; then
  echo "[3/4] [スキップ] Rscript が見つかりません。"
  echo "  ビューア機能のみ使用する場合は R は不要です。"
  echo "  R をインストールする場合:"
  echo "    Mac:   brew install --cask r   または https://cran.r-project.org/bin/macosx/"
  echo "    Linux: apt install r-base   等"
  echo ""
  echo "Setup complete (without R)."
  echo "  起動方法: ./run_app.sh"
  exit 0
fi
echo "[3/4] $(Rscript --version 2>&1 | head -1) が見つかりました。"

# [4/4] R パッケージ
echo "[4/4] R パッケージをインストール中... (初回は 10〜20 分かかる場合があります)"
Rscript install_r_packages.R

echo ""
echo "=== Setup complete ==="
echo "  起動方法: ./run_app.sh"
