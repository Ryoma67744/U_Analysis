#!/usr/bin/env bash
# ★ ver74.0: 旧volumeを削除せず、事前展開・旧内容保全・失敗時復旧を行う。
# ./restore.sh [--dry-run] <論理volume名または実volume名> <archive.tar.gz>
# ./restore.sh --list / --yes は非対話で明示同意した復元用。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/App/tools/volume_backup.py" restore "$@"
