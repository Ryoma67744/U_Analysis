#!/usr/bin/env bash
# MSI Analysis Application Launcher (macOS / Linux)
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/App"

# .env があれば読み込み (R_HOME 等) — App/.env を優先
if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

echo "Starting MSI Analysis Application..."
python3 run_app.py
