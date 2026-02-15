# =============================================================================
# MSI Analysis Application - Project Manager
# プロジェクト管理モジュール
# =============================================================================

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import PROJECTS_DIR, PROJECTS_FILE


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
) -> dict:
    """新規プロジェクト作成"""
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    project = {
        "id": str(uuid.uuid4())[:8],
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
) -> Optional[dict]:
    """サブプロジェクト作成"""
    data = _load_all()
    for p in data["projects"]:
        if p["id"] == project_id:
            now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            sub = {
                "id": str(uuid.uuid4())[:8],
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
