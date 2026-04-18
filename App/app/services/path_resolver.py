# =============================================================================
# MSI Analysis Application - Path Resolver
# 生データパスの自動補正ユーティリティ
# =============================================================================
#
# projects.json に絶対パスで保存された生データ参照を、
# 別マシン/アップデート後の環境でも解決可能にするための補正ロジック。
#
# 動作:
#   1. 破損パス (例: Windows Dropbox パス) の末尾フォルダ名を抽出
#   2. DESI_DATA_CANDIDATES / TIMS_DATA_CANDIDATES を順に検索
#   3. 同名フォルダを発見したら新しい絶対パスを返す
# =============================================================================

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

from app.config import (
    DESI_DATA_CANDIDATES,
    TIMS_DATA_CANDIDATES,
)

logger = logging.getLogger("msi.path_resolver")

Modality = Literal["desi", "tims", "auto"]

# projects.json 内で生データを参照しているフィールド
DATA_PATH_FIELDS = ("data_folder", "output_dir", "last_result_dir")
FILE_PATH_FIELDS = ("annotation_path", "annotation_csv_path")

# 探索深度 (性能と汎用性のトレードオフ)
_MAX_SCAN_DEPTH = 5


def _split_path(raw: str) -> list[str]:
    """Windows / POSIX 両対応でパス末尾の要素列を抽出"""
    if not raw:
        return []
    # Windows 区切りと POSIX 区切りの両方を扱う
    normalized = raw.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p and ":" not in p]
    return parts


def _candidates_for(modality: Modality) -> list[Path]:
    if modality == "desi":
        return list(DESI_DATA_CANDIDATES)
    if modality == "tims":
        return list(TIMS_DATA_CANDIDATES)
    # auto: DESI / TIMS 両方を試す
    return list(DESI_DATA_CANDIDATES) + list(TIMS_DATA_CANDIDATES)


def _infer_modality(path_str: str) -> Modality:
    """パス文字列から DESI / TIMS を推測"""
    lower = path_str.lower()
    if "/tims" in lower or "\\tims" in lower:
        return "tims"
    if "/desi" in lower or "\\desi" in lower:
        return "desi"
    return "auto"


def _find_by_tail(tail: str, root: Path, is_file: bool) -> Optional[Path]:
    """root 配下で末尾名 (tail) に一致するパスを探す"""
    if not root.is_dir():
        return None
    try:
        for candidate in root.rglob(tail):
            if is_file and candidate.is_file():
                return candidate
            if not is_file and candidate.is_dir():
                return candidate
    except (PermissionError, OSError) as exc:
        logger.debug("探索中にエラー %s: %s", root, exc)
    return None


def resolve_data_path(
    broken_path: str,
    modality: Modality = "auto",
    is_file: bool = False,
) -> Optional[Path]:
    """破損した絶対パスを DATA_CANDIDATES 配下で再解決する。

    Parameters
    ----------
    broken_path : str
        projects.json に保存されていた絶対パス (別マシン由来の可能性あり)
    modality : "desi" | "tims" | "auto"
        "auto" は DESI / TIMS 両方を探索
    is_file : bool
        True の場合はファイルを、False の場合はディレクトリを探す

    Returns
    -------
    Optional[Path]
        発見したパス。未発見時は None
    """
    if not broken_path:
        return None

    # 既に有効な絶対パスなら何もしない
    if Path(broken_path).exists():
        return Path(broken_path)

    if modality == "auto":
        modality = _infer_modality(broken_path)

    parts = _split_path(broken_path)
    if not parts:
        return None

    tail = parts[-1]
    candidates = _candidates_for(modality)

    for root in candidates:
        found = _find_by_tail(tail, root, is_file)
        if found:
            logger.info("パス補正: %s → %s", broken_path, found)
            return found

    # 末尾 1 階層で見つからない場合、末尾 2 階層を試す
    if len(parts) >= 2:
        tail2 = f"{parts[-2]}/{parts[-1]}"
        for root in candidates:
            if not root.is_dir():
                continue
            for candidate in root.rglob(parts[-2]):
                target = candidate / parts[-1]
                if is_file and target.is_file():
                    logger.info("パス補正 (2階層): %s → %s", broken_path, target)
                    return target
                if not is_file and target.is_dir():
                    logger.info("パス補正 (2階層): %s → %s", broken_path, target)
                    return target

    logger.warning("パス補正失敗: %s", broken_path)
    return None


def resolve_project_paths(sub_info: dict) -> tuple[dict, list[str]]:
    """サブプロジェクトのメタデータ内パスを一括補正。

    Parameters
    ----------
    sub_info : dict
        `_project_meta.json` 内の ``sub_project`` セクション

    Returns
    -------
    corrected : dict
        補正後の sub_info のコピー
    unresolved : list[str]
        解決できなかったパス (警告表示用)
    """
    corrected = dict(sub_info)
    unresolved: list[str] = []

    modality = _infer_modality(
        str(sub_info.get("data_folder", ""))
        or str(sub_info.get("ms_instrument", ""))
    )

    # last_analysis_settings 内のパスも補正
    settings = corrected.get("last_analysis_settings")
    if isinstance(settings, dict):
        new_settings = dict(settings)
        for field in DATA_PATH_FIELDS:
            value = new_settings.get(field, "")
            if not value:
                continue
            resolved = resolve_data_path(value, modality, is_file=False)
            if resolved:
                new_settings[field] = str(resolved)
            else:
                unresolved.append(f"{field}: {value}")

        for field in FILE_PATH_FIELDS:
            value = new_settings.get(field, "")
            if not value:
                continue
            resolved = resolve_data_path(value, modality, is_file=True)
            if resolved:
                new_settings[field] = str(resolved)
            else:
                unresolved.append(f"{field}: {value}")

        corrected["last_analysis_settings"] = new_settings

    # サブプロジェクト直下の data_folder / output_dir も補正
    for field in DATA_PATH_FIELDS:
        value = corrected.get(field, "")
        if not value:
            continue
        resolved = resolve_data_path(value, modality, is_file=False)
        if resolved:
            corrected[field] = str(resolved)
        elif field not in ("last_result_dir",):
            # last_result_dir は呼び出し側が found_dir で上書きするため除外
            unresolved.append(f"{field}: {value}")

    return corrected, unresolved
