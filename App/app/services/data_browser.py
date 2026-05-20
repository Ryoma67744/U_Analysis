# =============================================================================
# MSI Analysis Application - Data Browser Service
# データ管理サブタブが利用するサービス層
# DESI/TIMS生データ、解析出力、アプリ内部データの4箇所を統一して扱う
# =============================================================================

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import (
    DESI_DATA_DIR,
    TIMS_DATA_DIR,
    OUTPUT_DATA_DIR,
    OTHER_DIR,
)
from app.layouts.file_browser_modal import list_directory
from app.services.project_manager import scan_project_meta
from app.services.backup_manager import list_backups


@dataclass(frozen=True)
class DataLocation:
    key: str
    label: str
    root: Path
    env_var: Optional[str]
    description: str


DATA_LOCATIONS: dict[str, DataLocation] = {
    "desi": DataLocation(
        key="desi",
        label="DESI生データ",
        root=DESI_DATA_DIR,
        env_var="DESI_DATA_DIR",
        description="DESI法による質量分析の生データ",
    ),
    "tims": DataLocation(
        key="tims",
        label="TIMS生データ",
        root=TIMS_DATA_DIR,
        env_var="TIMS_DATA_DIR",
        description="TIMS法による質量分析の生データ",
    ),
    "output": DataLocation(
        key="output",
        label="解析出力",
        root=OUTPUT_DATA_DIR,
        env_var="OUTPUT_DATA_DIR",
        description="アプリが生成した解析結果と _project_meta.json",
    ),
    "internal": DataLocation(
        key="internal",
        label="アプリ内部データ",
        root=OTHER_DIR,
        env_var=None,
        description="セッション・プロジェクト・プリセット・共有・キャッシュ",
    ),
}


def get_location(key: str) -> Optional[DataLocation]:
    return DATA_LOCATIONS.get(key)


def get_location_root(key: str) -> Optional[Path]:
    loc = DATA_LOCATIONS.get(key)
    return loc.root if loc else None


def get_layout_summary() -> list[dict]:
    """データ管理サブタブの配置サマリー表示用"""
    rows = []
    for loc in DATA_LOCATIONS.values():
        path = loc.root
        env_value = os.environ.get(loc.env_var) if loc.env_var else None
        rows.append({
            "key": loc.key,
            "label": loc.label,
            "path": str(path),
            "exists": path.is_dir(),
            "env_var": loc.env_var,
            "env_value": env_value,
            "description": loc.description,
        })
    return rows


def _safe_resolve(root: Path, subpath: str) -> Path:
    """サブパスを root 配下に制限して解決（root 脱出禁止）"""
    target = Path(subpath) if subpath else root
    if not target.is_absolute():
        target = (root / target).resolve()
    else:
        target = target.resolve()
    try:
        root_resolved = root.resolve()
    except OSError:
        return target
    try:
        target.relative_to(root_resolved)
    except ValueError:
        return root_resolved
    return target


def _build_breadcrumb(root: Path, target: Path) -> list[dict]:
    """root から target までのパンくず階層を生成"""
    try:
        rel = target.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return [{"name": target.name or str(target), "path": str(target)}]
    crumbs = [{"name": root.name or str(root), "path": str(root)}]
    current = root
    for part in rel.parts:
        current = current / part
        crumbs.append({"name": part, "path": str(current)})
    return crumbs


def get_directory_listing(key: str, subpath: str = "") -> dict:
    """指定場所のサブパスを list_directory で列挙

    Returns
    -------
    dict
        {"current_dir": str, "items": list, "exists": bool,
         "breadcrumb": list, "root": str}
    """
    root = get_location_root(key)
    if root is None:
        return {
            "current_dir": "", "items": [], "exists": False,
            "breadcrumb": [], "root": "",
        }
    target = _safe_resolve(root, subpath)
    items = list_directory(str(target), show_files=True) if target.is_dir() else []
    breadcrumb = _build_breadcrumb(root, target)
    return {
        "current_dir": str(target),
        "items": items,
        "exists": target.is_dir(),
        "breadcrumb": breadcrumb,
        "root": str(root),
    }


def _dir_stats(path: Path, max_items: int = 50000) -> tuple[int, int]:
    """フォルダ配下のファイル数と合計サイズを集計（上限あり）"""
    count = 0
    total = 0
    if not path.is_dir():
        return 0, 0
    try:
        for entry in path.rglob("*"):
            if count >= max_items:
                break
            try:
                if entry.is_file():
                    total += entry.stat().st_size
                    count += 1
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        pass
    return count, total


def get_storage_stats() -> list[dict]:
    """各場所のディスク使用量とディスク全体の空き容量"""
    rows = []
    for loc in DATA_LOCATIONS.values():
        path = loc.root
        if not path.is_dir():
            rows.append({
                "key": loc.key,
                "label": loc.label,
                "path": str(path),
                "exists": False,
                "file_count": 0,
                "used_bytes": 0,
                "disk_total_bytes": 0,
                "disk_free_bytes": 0,
            })
            continue
        count, used = _dir_stats(path)
        try:
            usage = shutil.disk_usage(path)
            disk_total = usage.total
            disk_free = usage.free
        except OSError:
            disk_total = 0
            disk_free = 0
        rows.append({
            "key": loc.key,
            "label": loc.label,
            "path": str(path),
            "exists": True,
            "file_count": count,
            "used_bytes": used,
            "disk_total_bytes": disk_total,
            "disk_free_bytes": disk_free,
        })
    return rows


def find_meta_projects(key: str) -> list[dict]:
    """指定場所配下から _project_meta.json を持つフォルダを列挙

    Returns
    -------
    list[dict]
        scan_project_meta() の返り値そのまま
        (各要素は project/sub_project メタ + _found_dir キー)
    """
    root = get_location_root(key)
    if root is None or not root.is_dir():
        return []
    return scan_project_meta(str(root))


def list_backup_generations(limit: int = 20) -> list[dict]:
    """起動時バックアップの世代一覧（新しい順、上限あり）"""
    try:
        all_backups = list_backups()
    except Exception:
        return []
    return all_backups[:limit] if limit else all_backups


def format_bytes(num) -> str:
    """バイト数を人間可読に整形"""
    if num is None:
        return "-"
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
