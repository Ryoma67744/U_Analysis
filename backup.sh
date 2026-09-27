#!/usr/bin/env bash
# ★ ver74.0: Compose接頭辞/host bindを実mountから解決し、全skip成功と誤削除を防ぐ。
# Python 3 + Docker が必要。BACKUP_DIR / RETENTION_DAYS / MSI_CONTAINER で設定。
# 稼働中backupはファイル単位。解析を止めた時間帯に実行して整合性を確保する。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/App/tools/volume_backup.py" backup "$@"
