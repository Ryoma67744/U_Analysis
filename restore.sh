#!/bin/bash
# =============================================================================
# MSI Analysis Application - Volume Restore Script
#
# Usage:
#   ./restore.sh <volume_name> <backup_file.tar.gz>           # 実際に復元
#   ./restore.sh --dry-run <volume_name> <backup_file.tar.gz> # 確認のみ
#   ./restore.sh --list                                       # 利用可能 backup 一覧
#
# 例:
#   ./restore.sh --list
#   ./restore.sh --dry-run msi-projects backups/msi-projects-20260507-030000.tar.gz
#   ./restore.sh msi-projects backups/msi-projects-20260507-030000.tar.gz
#
# 動作:
#   1. backup.sh で作成された .tar.gz から Docker named volume を復元
#   2. 既存 volume があれば削除前に確認を求める
#   3. docker compose の down/up 操作を含む (アプリ停止 → 復元 → 再起動)
# =============================================================================

set -e  # 任意のコマンド失敗で即終了

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- helpers ---
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
err() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; }

BACKUP_DIR="${BACKUP_DIR:-$SCRIPT_DIR/backups}"

# --- arg parsing ---
DRY_RUN=0
if [ "$1" = "--dry-run" ]; then
    DRY_RUN=1
    shift
fi

if [ "$1" = "--list" ]; then
    log "利用可能なバックアップ一覧: $BACKUP_DIR"
    if [ ! -d "$BACKUP_DIR" ]; then
        err "バックアップディレクトリが見つかりません: $BACKUP_DIR"
        exit 1
    fi
    ls -lh "$BACKUP_DIR"/*.tar.gz 2>/dev/null | awk '{print $9, "("$5")"}'
    exit 0
fi

if [ $# -lt 2 ]; then
    err "Usage: $0 [--dry-run|--list] <volume_name> <backup_file.tar.gz>"
    err "       $0 --list  でバックアップ一覧を表示"
    exit 1
fi

VOLUME_NAME="$1"
BACKUP_FILE="$2"

# 相対パスを絶対パスへ
if [ ! -f "$BACKUP_FILE" ] && [ -f "$BACKUP_DIR/$BACKUP_FILE" ]; then
    BACKUP_FILE="$BACKUP_DIR/$BACKUP_FILE"
fi

if [ ! -f "$BACKUP_FILE" ]; then
    err "バックアップファイルが見つかりません: $BACKUP_FILE"
    exit 1
fi

# --- ファイル検証 ---
log "バックアップファイル検証中: $BACKUP_FILE"
if ! tar tzf "$BACKUP_FILE" > /dev/null 2>&1; then
    err "バックアップファイルが壊れているか、形式が不正です"
    exit 1
fi

backup_size=$(du -h "$BACKUP_FILE" | cut -f1)
backup_files_count=$(tar tzf "$BACKUP_FILE" | wc -l)
log "  サイズ: $backup_size"
log "  ファイル数: $backup_files_count"
log "  対象 Docker volume: $VOLUME_NAME"

# --- dry-run ---
if [ $DRY_RUN -eq 1 ]; then
    log ""
    log "=== DRY-RUN MODE ==="
    log "以下の操作を実行する予定です（実際には実行しません）:"
    log "  1. docker compose down (アプリ停止)"
    log "  2. docker volume rm $VOLUME_NAME (既存 volume 削除)"
    log "  3. docker volume create $VOLUME_NAME"
    log "  4. tar xzf $BACKUP_FILE → $VOLUME_NAME"
    log "  5. docker compose up -d (アプリ再起動)"
    log ""
    log "実際に復元するには --dry-run を外してください。"
    exit 0
fi

# --- 既存 volume チェック + 確認 ---
if docker volume inspect "$VOLUME_NAME" > /dev/null 2>&1; then
    log "既存の volume '$VOLUME_NAME' が見つかりました。"
    log "復元すると現在の中身は失われます。"
    read -p "続行しますか? (yes/[no]): " confirm
    if [ "$confirm" != "yes" ]; then
        log "キャンセルしました。"
        exit 0
    fi
fi

# --- 復元実行 ---
log ""
log "Step 1/5: docker compose down (アプリ停止)"
docker compose down

log "Step 2/5: 既存 volume を削除"
docker volume rm "$VOLUME_NAME" 2>/dev/null || true

log "Step 3/5: 空の volume を作成"
docker volume create "$VOLUME_NAME"

log "Step 4/5: バックアップを展開"
docker run --rm \
    -v "$VOLUME_NAME":/data \
    -v "$(dirname "$BACKUP_FILE")":/backup:ro \
    alpine sh -c "tar xzf /backup/$(basename "$BACKUP_FILE") -C /data && ls -la /data | head"

log "Step 5/5: docker compose up -d (アプリ再起動)"
docker compose up -d

log ""
log "=== 復元完了 ==="
log "  Volume: $VOLUME_NAME"
log "  Source: $BACKUP_FILE"
log ""
log "起動確認:"
sleep 3
docker compose ps
log ""
log "ログ確認: docker compose logs -f msi-app"
