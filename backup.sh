#!/usr/bin/env bash
# =============================================================================
# MSI Analysis Application - Volume バックアップスクリプト
#
# Docker Volume をまるごと tar.gz でバックアップ。災害復旧用。
# 対象: msi-projects, msi-sessions, msi-presets, msi-shares, msi-output, msi-logs
# 除外: msi-cache（再生成可能）, msi-desi-data / msi-tims-data（巨大、原本あり前提）
#
# 使い方:
#   ./backup.sh                       # ./backups/ に出力
#   BACKUP_DIR=/mnt/backup ./backup.sh # 任意ディレクトリに出力
#
# cron 例（毎週日曜 3:00）:
#   0 3 * * 0 cd /home/ubuntu/umap-webapp-claudecode && ./backup.sh
#
# 30日以上古いアーカイブは自動削除。
# =============================================================================

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
VOLUMES=(msi-projects msi-sessions msi-presets msi-shares msi-output msi-logs)

mkdir -p "$BACKUP_DIR"
BACKUP_DIR_ABS="$(cd "$BACKUP_DIR" && pwd)"
LOG="$BACKUP_DIR_ABS/backup.log"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

log() {
    echo "[$(date -Iseconds)] $*" | tee -a "$LOG"
}

log "Backup started (output: $BACKUP_DIR_ABS)"

success_count=0
fail_count=0
for vol in "${VOLUMES[@]}"; do
    if ! docker volume inspect "$vol" >/dev/null 2>&1; then
        log "  - SKIP $vol (volume not found)"
        continue
    fi
    archive_name="${vol}-${TIMESTAMP}.tar.gz"
    if docker run --rm \
        -v "$vol:/data:ro" \
        -v "$BACKUP_DIR_ABS:/backup" \
        alpine tar czf "/backup/$archive_name" -C /data . 2>>"$LOG"; then
        size="$(du -h "$BACKUP_DIR_ABS/$archive_name" | cut -f1)"
        log "  + OK   $vol -> $archive_name ($size)"
        success_count=$((success_count + 1))
    else
        log "  ! FAIL $vol"
        fail_count=$((fail_count + 1))
    fi
done

# 古いアーカイブを削除
deleted=0
if [ -d "$BACKUP_DIR_ABS" ]; then
    while IFS= read -r old; do
        rm -f "$old"
        log "  - DELETE old: $(basename "$old")"
        deleted=$((deleted + 1))
    done < <(find "$BACKUP_DIR_ABS" -maxdepth 1 -name "*.tar.gz" -mtime +"$RETENTION_DAYS" -print)
fi

log "Backup finished (ok=$success_count, fail=$fail_count, deleted=$deleted)"

if [ "$fail_count" -gt 0 ]; then
    exit 1
fi
