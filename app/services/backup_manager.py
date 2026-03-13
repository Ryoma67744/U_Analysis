# =============================================================================
# MSI Analysis Application - Backup Manager
# 自動バックアップ管理モジュール
# =============================================================================

import logging
import shutil
from datetime import datetime
from pathlib import Path

from app.config import PROJECTS_FILE, SHARES_FILE, SESSIONS_DIR

logger = logging.getLogger("msi.backup")

# バックアップ設定
BACKUPS_DIR = PROJECTS_FILE.parent / "backups"
MAX_BACKUPS = 5

# セッション設定ファイル
_LAST_SETTINGS_FILE = SESSIONS_DIR / "last_settings.json"


def _ensure_backup_dir() -> None:
    """バックアップディレクトリが存在しなければ作成"""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)


def create_backup(source_file: Path, max_backups: int = MAX_BACKUPS) -> Path | None:
    """ファイルのバックアップを作成し、古いバックアップをローテーション。

    Parameters
    ----------
    source_file : Path
        バックアップ対象のファイル
    max_backups : int
        保持する最大バックアップ数

    Returns
    -------
    Path | None
        作成されたバックアップファイルのパス。ソースが存在しない場合は None。
    """
    if not source_file.exists():
        return None

    _ensure_backup_dir()

    # タイムスタンプ付きファイル名を生成
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = source_file.stem
    suffix = source_file.suffix
    backup_path = BACKUPS_DIR / f"{stem}_{timestamp}{suffix}"

    # コピー
    shutil.copy2(source_file, backup_path)

    # ローテーション: 同じプレフィックスの古いバックアップを削除
    rotate_backups(stem, max_backups)

    return backup_path


def rotate_backups(prefix: str, max_count: int = MAX_BACKUPS) -> int:
    """古いバックアップを削除して最大数以内に保つ。

    Parameters
    ----------
    prefix : str
        ファイル名のプレフィックス（例: "projects"）
    max_count : int
        保持する最大ファイル数

    Returns
    -------
    int
        削除されたファイル数
    """
    _ensure_backup_dir()

    # 該当プレフィックスのバックアップを取得（名前順＝時刻順）
    backups = sorted(
        BACKUPS_DIR.glob(f"{prefix}_*.json"),
        key=lambda p: p.name,
    )

    deleted = 0
    while len(backups) > max_count:
        oldest = backups.pop(0)
        try:
            oldest.unlink()
            deleted += 1
        except OSError as e:
            logger.warning("バックアップ削除失敗 (%s): %s", oldest.name, e)

    return deleted


def startup_backup() -> list[str]:
    """アプリ起動時に重要ファイルのバックアップを作成。

    Returns
    -------
    list[str]
        作成されたバックアップファイル名のリスト
    """
    created = []

    targets = [PROJECTS_FILE, SHARES_FILE, _LAST_SETTINGS_FILE]
    for target in targets:
        result = create_backup(target)
        if result:
            created.append(result.name)

    return created


def backup_on_save(source_file: Path) -> None:
    """ファイル保存時に自動バックアップを作成（エラーは無視）。

    Parameters
    ----------
    source_file : Path
        保存されたファイル
    """
    try:
        create_backup(source_file)
    except Exception as e:
        logger.warning("バックアップ作成失敗 (%s): %s", source_file.name, e)


def list_backups() -> list[dict]:
    """全バックアップファイルの一覧を返す。

    Returns
    -------
    list[dict]
        各バックアップの情報（name, path, size_kb, created_at）
    """
    _ensure_backup_dir()
    result = []
    for p in sorted(BACKUPS_DIR.glob("*.json"), key=lambda x: x.name, reverse=True):
        try:
            stat = p.stat()
            result.append({
                "name": p.name,
                "path": str(p),
                "size_kb": round(stat.st_size / 1024, 1),
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        except OSError as e:
            logger.warning("バックアップ情報取得失敗 (%s): %s", p.name, e)
    return result
