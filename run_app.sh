#!/usr/bin/env bash
# MSI Analysis Application Launcher (macOS / Linux)
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/App"

# ★ ver63.4: 初回起動時に App/.env を用意する（FLASK_SECRET_KEY 等の自動生成）。
#   .env は .gitignore 済みで clone/ZIP 展開直後には存在せず、その状態で起動すると
#   FLASK_SECRET_KEY 未設定の RuntimeError で即死していた。
#   既に .env があれば何もしない（設定済みの値は書き換えない）。
#   失敗してもランチャは止めない（アプリ側が具体的な案内付きで落ちる）。
python3 -m app.services.env_bootstrap || \
  echo "[警告] .env の自動生成に失敗しました。App/.env を手動で用意してください。"

# .env があれば読み込み (R_HOME 等) — App/.env を優先
if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

echo "Starting MSI Analysis Application..."
python3 run_app.py
