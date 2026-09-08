#!/usr/bin/env bash
# MSI Analysis Application - 初回セットアップ (macOS / Linux)
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/App"

echo "=== MSI Analysis Application Setup ==="

# [1/5] Python チェック
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 が見つかりません。Python 3.10+ をインストールしてください。"
  echo "  Mac:   brew install python@3.11   または https://www.python.org/downloads/"
  echo "  Linux: apt install python3 python3-pip   等"
  exit 1
fi
echo "[1/5] $(python3 --version) が見つかりました。"

# [2/5] 環境設定 (.env)
# ★ ver64.1: .env は .gitignore 済みで clone/ZIP 展開直後には存在しない。
#   FLASK_SECRET_KEY / MASTER_PASSWORD / INITIAL_PASSWORD_B が無いとアプリは
#   起動できず、越えてもログインできなかった。ここで自動生成する。
#   pip より前に置くのは、この処理が標準ライブラリだけで動くため
#   （パッケージ導入に失敗しても .env だけは残る）。
echo "[2/5] 環境設定 (.env) を確認中..."
python3 -m app.services.env_bootstrap || \
  echo "[警告] .env の自動生成に失敗しました。App/.env を手動で用意してください。"

# [3/5] Python パッケージ
echo "[3/5] Python パッケージをインストール中..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

# [4/5] R チェック (任意)
if ! command -v Rscript >/dev/null 2>&1; then
  echo "[4/5] [スキップ] Rscript が見つかりません。"
  echo "  ビューア機能のみ使用する場合は R は不要です。"
  echo "  R をインストールする場合:"
  echo "    Mac:   brew install --cask r   または https://cran.r-project.org/bin/macosx/"
  echo "    Linux: apt install r-base   等"
  echo ""
  echo "Setup complete (without R)."
  echo "  起動方法: ./run_app.sh"
  echo "  ログインパスワードは App/.env の MASTER_PASSWORD 行に記載されています。"
  exit 0
fi
echo "[4/5] $(Rscript --version 2>&1 | head -1) が見つかりました。"

# [5/5] R パッケージ
echo "[5/5] R パッケージをインストール中... (初回は 10〜20 分かかる場合があります)"
Rscript install_r_packages.R

echo ""
echo "=== Setup complete ==="
echo "  起動方法: ./run_app.sh"
echo "  ログインパスワードは App/.env の MASTER_PASSWORD 行に記載されています。"
