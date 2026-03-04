# =============================================================================
# MSI Analysis Application - Project Manager
# プロジェクト管理モジュール
# =============================================================================

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import PROJECTS_DIR, PROJECTS_FILE

# メタデータファイル名（結果フォルダに自動保存）
_META_FILENAME = "_project_meta.json"


def _ensure_projects_file() -> None:
    """projects.json が存在しなければ初期化"""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    if not PROJECTS_FILE.exists():
        with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"projects": []}, f, indent=2, ensure_ascii=False)


def _load_all() -> dict:
    """projects.json 全体を読み込み"""
    _ensure_projects_file()
    try:
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"projects": []}


def _save_all(data: dict) -> None:
    """projects.json 全体を保存"""
    _ensure_projects_file()
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    # 自動バックアップ
    try:
        from app.services.backup_manager import backup_on_save
        backup_on_save(PROJECTS_FILE)
    except Exception:
        pass


# =========================================================================
# プロジェクト CRUD
# =========================================================================

def list_projects() -> list[dict]:
    """プロジェクト一覧を返す（最終更新日時の降順）"""
    data = _load_all()
    projects = data.get("projects", [])
    projects.sort(key=lambda p: p.get("last_modified", ""), reverse=True)
    return projects


def get_project(project_id: str) -> Optional[dict]:
    """IDでプロジェクトを検索"""
    for p in list_projects():
        if p["id"] == project_id:
            return p
    return None


def create_project(
    name: str,
    experiment_date: str = "",
    memo: str = "",
    *,
    force_id: str = "",
) -> dict:
    """新規プロジェクト作成

    Parameters
    ----------
    force_id : str, optional
        復元時に元のIDを保持するために使用。空文字の場合は自動生成。
    """
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    project = {
        "id": force_id if force_id else str(uuid.uuid4())[:8],
        "name": name,
        "experiment_date": experiment_date,
        "memo": memo,
        "sub_projects": [],
        "created_at": now,
        "last_modified": now,
    }
    data = _load_all()
    data["projects"].append(project)
    _save_all(data)
    return project


def update_project(project_id: str, updates: dict) -> Optional[dict]:
    """プロジェクト情報を更新"""
    data = _load_all()
    for p in data["projects"]:
        if p["id"] == project_id:
            p.update(updates)
            p["last_modified"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            _save_all(data)
            # メタデータを結果フォルダへバックアップ
            for s in p.get("sub_projects", []):
                _write_meta_to_folder(project_id, s["id"])
            return p
    return None


def delete_project(project_id: str) -> bool:
    """プロジェクトを削除（データファイルは削除しない）"""
    data = _load_all()
    original_len = len(data["projects"])
    data["projects"] = [p for p in data["projects"] if p["id"] != project_id]
    if len(data["projects"]) < original_len:
        _save_all(data)
        return True
    return False


# =========================================================================
# サブプロジェクト CRUD
# =========================================================================

def list_sub_projects(project_id: str) -> list[dict]:
    """指定プロジェクト内のサブプロジェクト一覧（最終更新日時の降順）"""
    project = get_project(project_id)
    if not project:
        return []
    subs = project.get("sub_projects", [])
    subs.sort(key=lambda s: s.get("last_modified", ""), reverse=True)
    return subs


def get_sub_project(project_id: str, sub_id: str) -> Optional[dict]:
    """サブプロジェクトをIDで検索"""
    for s in list_sub_projects(project_id):
        if s["id"] == sub_id:
            return s
    return None


def create_sub_project(
    project_id: str,
    name: str,
    experiment_date: str = "",
    target_compound: str = "",
    ms_instrument: str = "",
    polarity: list[str] | None = None,
    memo: str = "",
    data_folder: str = "",
    output_dir: str = "",
    *,
    force_id: str = "",
    extra_fields: dict | None = None,
) -> Optional[dict]:
    """サブプロジェクト作成

    Parameters
    ----------
    force_id : str, optional
        復元時に元のIDを保持するために使用。空文字の場合は自動生成。
    extra_fields : dict, optional
        復元時に last_analysis_settings 等の追加フィールドをマージする。
    """
    data = _load_all()
    for p in data["projects"]:
        if p["id"] == project_id:
            now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            sub = {
                "id": force_id if force_id else str(uuid.uuid4())[:8],
                "name": name,
                "experiment_date": experiment_date,
                "target_compound": target_compound,
                "ms_instrument": ms_instrument,
                "polarity": polarity or [],
                "memo": memo,
                "data_folder": data_folder,
                "output_dir": output_dir,
                "created_at": now,
                "last_modified": now,
            }
            if extra_fields:
                sub.update(extra_fields)
            if "sub_projects" not in p:
                p["sub_projects"] = []
            p["sub_projects"].append(sub)
            p["last_modified"] = now
            _save_all(data)
            return sub
    return None


def update_sub_project(
    project_id: str, sub_id: str, updates: dict
) -> Optional[dict]:
    """サブプロジェクト情報を更新"""
    data = _load_all()
    for p in data["projects"]:
        if p["id"] == project_id:
            for s in p.get("sub_projects", []):
                if s["id"] == sub_id:
                    s.update(updates)
                    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    s["last_modified"] = now
                    p["last_modified"] = now
                    _save_all(data)
                    _write_meta_to_folder(project_id, sub_id)
                    return s
    return None


def save_sub_project_settings(
    project_id: str, sub_id: str, settings: dict
) -> bool:
    """サブプロジェクトに解析設定を保存"""
    data = _load_all()
    for p in data["projects"]:
        if p["id"] == project_id:
            for s in p.get("sub_projects", []):
                if s["id"] == sub_id:
                    s["last_analysis_settings"] = settings
                    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    s["last_modified"] = now
                    p["last_modified"] = now
                    _save_all(data)
                    _write_meta_to_folder(project_id, sub_id)
                    return True
    return False


def get_sub_project_settings(
    project_id: str, sub_id: str
) -> Optional[dict]:
    """サブプロジェクトに保存された解析設定を取得"""
    sub = get_sub_project(project_id, sub_id)
    if sub:
        return sub.get("last_analysis_settings")
    return None


def save_sub_project_result_dir(
    project_id: str, sub_id: str, result_dir: str
) -> bool:
    """サブプロジェクトに最新の解析結果ディレクトリを保存"""
    data = _load_all()
    for p in data["projects"]:
        if p["id"] == project_id:
            for s in p.get("sub_projects", []):
                if s["id"] == sub_id:
                    s["last_result_dir"] = result_dir
                    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    s["last_modified"] = now
                    p["last_modified"] = now
                    _save_all(data)
                    _write_meta_to_folder(project_id, sub_id)
                    return True
    return False


def delete_sub_project(project_id: str, sub_id: str) -> bool:
    """サブプロジェクトを削除"""
    data = _load_all()
    for p in data["projects"]:
        if p["id"] == project_id:
            original_len = len(p.get("sub_projects", []))
            p["sub_projects"] = [
                s for s in p.get("sub_projects", []) if s["id"] != sub_id
            ]
            if len(p["sub_projects"]) < original_len:
                p["last_modified"] = datetime.now().strftime(
                    "%Y-%m-%dT%H:%M:%S"
                )
                _save_all(data)
                return True
    return False


# =========================================================================
# メタデータバックアップ・スキャン・復元
# =========================================================================

def _write_meta_to_folder(project_id: str, sub_id: str) -> None:
    """サブプロジェクトのメタデータを結果フォルダに自動保存する。

    last_result_dir が存在しない場合はスキップ（解析前など）。
    書き込み失敗時はログのみ出力し例外は発生させない。
    """
    project = get_project(project_id)
    sub = get_sub_project(project_id, sub_id)
    if not project or not sub:
        return
    result_dir = sub.get("last_result_dir", "")
    if not result_dir or not Path(result_dir).is_dir():
        return

    # プロジェクトレベル情報（sub_projects は含めない）
    proj_info = {
        k: v for k, v in project.items() if k != "sub_projects"
    }
    meta = {
        "version": "1.0",
        "project": proj_info,
        "sub_project": dict(sub),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    meta_path = Path(result_dir) / _META_FILENAME
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[project_manager] メタデータ書き出し失敗: {exc}",
              file=sys.stderr)


def scan_project_meta(root_folder: str) -> list[dict]:
    """指定フォルダを再帰的にスキャンし _project_meta.json を収集する。

    Returns
    -------
    list[dict]
        各要素は読み込んだメタデータ dict に ``_found_dir`` キーを追加したもの。
    """
    results: list[dict] = []
    root = Path(root_folder)
    if not root.is_dir():
        return results
    for meta_path in root.rglob(_META_FILENAME):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta["_found_dir"] = str(meta_path.parent)
            results.append(meta)
        except Exception:
            continue
    return results


def restore_projects_from_meta(
    meta_list: list[dict],
    action_map: dict[str, str],
) -> list[str]:
    """スキャン結果からプロジェクトを復元する。

    Parameters
    ----------
    meta_list : list[dict]
        ``scan_project_meta`` の返り値。
    action_map : dict[str, str]
        ``{sub_project_id: "restore" | "update_paths" | "skip"}``

    Returns
    -------
    list[str]
        復元結果のサマリーメッセージのリスト。
    """
    messages: list[str] = []
    for meta in meta_list:
        sub_info = meta.get("sub_project", {})
        proj_info = meta.get("project", {})
        sub_id = sub_info.get("id", "")
        proj_id = proj_info.get("id", "")
        found_dir = meta.get("_found_dir", "")

        action = action_map.get(sub_id, "skip")
        if action == "skip" or not sub_id or not proj_id:
            continue

        existing_proj = get_project(proj_id)

        if action == "restore":
            # --- プロジェクトが存在しなければ作成 ---
            if not existing_proj:
                create_project(
                    name=proj_info.get("name", "復元プロジェクト"),
                    experiment_date=proj_info.get("experiment_date", ""),
                    memo=proj_info.get("memo", ""),
                    force_id=proj_id,
                )
            # --- サブプロジェクトが存在しなければ作成 ---
            existing_sub = get_sub_project(proj_id, sub_id)
            if not existing_sub:
                # last_result_dir を発見パスで上書き
                extra = {}
                for key in ("last_analysis_settings", "last_result_dir"):
                    if key in sub_info:
                        extra[key] = sub_info[key]
                extra["last_result_dir"] = found_dir

                create_sub_project(
                    project_id=proj_id,
                    name=sub_info.get("name", "復元サブプロジェクト"),
                    experiment_date=sub_info.get("experiment_date", ""),
                    target_compound=sub_info.get("target_compound", ""),
                    ms_instrument=sub_info.get("ms_instrument", ""),
                    polarity=sub_info.get("polarity"),
                    memo=sub_info.get("memo", ""),
                    data_folder=sub_info.get("data_folder", ""),
                    output_dir=sub_info.get("output_dir", ""),
                    force_id=sub_id,
                    extra_fields=extra,
                )
                messages.append(
                    f"✅ 復元: {proj_info.get('name', '')} / "
                    f"{sub_info.get('name', '')}"
                )
            else:
                messages.append(
                    f"⏭ スキップ（既存）: {proj_info.get('name', '')} / "
                    f"{sub_info.get('name', '')}"
                )

        elif action == "update_paths":
            # --- 既存プロジェクトのパスのみ更新 ---
            if existing_proj:
                existing_sub = get_sub_project(proj_id, sub_id)
                if existing_sub:
                    update_sub_project(
                        proj_id, sub_id,
                        {"last_result_dir": found_dir},
                    )
                    messages.append(
                        f"🔄 パス更新: {proj_info.get('name', '')} / "
                        f"{sub_info.get('name', '')}"
                    )
                else:
                    messages.append(
                        f"⚠ サブプロジェクト未検出: {sub_info.get('name', '')}"
                    )
            else:
                messages.append(
                    f"⚠ プロジェクト未検出: {proj_info.get('name', '')}"
                )

    return messages
