# =============================================================================
# MSI Analysis Application - Project Manager
# プロジェクト管理モジュール
# =============================================================================

import json
import logging
import os
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from filelock import FileLock

logger = logging.getLogger("msi.project_manager")

from app.config import PROJECTS_DIR, PROJECTS_FILE
from app.services.path_resolver import resolve_project_paths

# メタデータファイル名（結果フォルダに自動保存）
_META_FILENAME = "_project_meta.json"

# プロセス横断の排他ロック（複数ユーザー同時保存対策）
_projects_lock = FileLock(str(PROJECTS_FILE) + ".lock", timeout=30)


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
            data = json.load(f)
        # リスト型や不正な形式の場合は正しい形式に変換
        if isinstance(data, list):
            data = {"projects": data}
        if not isinstance(data, dict) or "projects" not in data:
            data = {"projects": []}
        return data
    except Exception as e:
        logger.warning("projects.json読み込み失敗: %s", e)
        return {"projects": []}


def _save_all(data: dict) -> None:
    """projects.json 全体を原子的に保存（書き込み中断による破損を防止）"""
    _ensure_projects_file()
    # 一時ファイルに書いてから os.replace() で原子的に差し替える
    fd, tmp_path = tempfile.mkstemp(
        dir=str(PROJECTS_DIR), suffix=".tmp", prefix="projects_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(PROJECTS_FILE))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    # 自動バックアップ
    try:
        from app.services.backup_manager import backup_on_save
        backup_on_save(PROJECTS_FILE)
    except Exception as e:
        logger.warning("プロジェクトバックアップ失敗: %s", e)


# =========================================================================
# プロジェクト CRUD
# =========================================================================

def list_projects(*, include_deleted: bool = False) -> list[dict]:
    """プロジェクト一覧を返す（最終更新日時の降順）。

    Parameters
    ----------
    include_deleted : bool, default False
        True なら soft delete されたプロジェクトも含む (ゴミ箱表示用)。
    """
    data = _load_all()
    projects = data.get("projects", [])
    if not include_deleted:
        projects = [p for p in projects if not p.get("deleted_at")]
    projects.sort(key=lambda p: p.get("last_modified", ""), reverse=True)
    return projects


def get_project(project_id: str, *, include_deleted: bool = False) -> Optional[dict]:
    """ID でプロジェクトを検索。

    include_deleted=False (デフォルト) は soft delete 済みを返さない。
    解析実行中の background callback など、UI 不可視でもデータアクセスが
    必要な場合は include_deleted=True で取得可能。
    """
    for p in list_projects(include_deleted=include_deleted):
        if p["id"] == project_id:
            return p
    return None


def _safe_current_user() -> str:
    """現在のリクエストの「作成者」名を取得。

    Flask session の analyst_name → session_id 短縮 → "Unknown user" の優先順位。
    Flask context 外で呼ばれた場合も "Unknown user" を返し例外を投げない。
    """
    try:
        from app.services.session_id import get_display_name
        return get_display_name()
    except Exception:
        return "Unknown user"


def can_modify_project(project: dict, *, current_user: Optional[str] = None) -> bool:
    """指定プロジェクトを現在のユーザーが変更 (削除/編集) できるか判定。

    ルール:
    - created_by フィールドが無い (旧プロジェクト) → 全員 OK (後方互換)
    - created_by が "Unknown user" → 全員 OK (匿名作成、後方互換)
    - 上記以外 → current_user (display_name) が一致する場合のみ OK

    本関数はサーバ側ガード用。UI 側でも事前判定して非表示にすることを推奨。
    """
    if not project:
        return False
    creator = project.get("created_by")
    if not creator or creator == "Unknown user":
        return True  # 後方互換: owner 不在プロジェクトは全員操作可
    user = current_user if current_user is not None else _safe_current_user()
    return creator == user


def create_project(
    name: str,
    experiment_date: str = "",
    memo: str = "",
    *,
    google_keep_url: str = "",
    msi_share_url: str = "",
    other_url: str = "",
    force_id: str = "",
    created_by: Optional[str] = None,
) -> dict:
    """新規プロジェクト作成

    Parameters
    ----------
    google_keep_url, msi_share_url, other_url : str, optional (ver3.16)
        プロジェクトに紐づく外部リンク URL。サブプロ一覧ページに
        「プロジェクト関連情報」セクションとして表示される。
    force_id : str, optional
        復元時に元のIDを保持するために使用。空文字の場合は自動生成。
    created_by : str, optional
        作成者の display_name (BasicAuth username など)。
        指定が無ければ Flask request context から自動取得。
        Context 外なら "Unknown user"。
    """
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    if created_by is None:
        created_by = _safe_current_user()
    project = {
        "id": force_id if force_id else str(uuid.uuid4())[:8],
        "name": name,
        "experiment_date": experiment_date,
        "memo": memo,
        # ver3.16: 関連 URL 3 種
        "google_keep_url": google_keep_url,
        "msi_share_url": msi_share_url,
        "other_url": other_url,
        "sub_projects": [],
        "created_at": now,
        "last_modified": now,
        "created_by": created_by,
    }
    with _projects_lock:
        data = _load_all()
        # 重複防止: force_id 指定時、同じ id のプロジェクトが既に存在すれば
        # 新規作成せず既存を返す (復元の冪等性確保)。
        # 過去に存在していた「同じ id のプロジェクトが何度も append される」
        # バグの再発を防ぐ。
        # さらに、既存が soft-deleted (deleted_at 設定済) なら明示的に
        # undelete する。これにより「削除 → 後から復元」で UI に出てこない
        # 問題が解消する (復元機能の本来の意味)。
        if force_id:
            for existing in data["projects"]:
                if existing.get("id") == force_id:
                    if existing.get("deleted_at"):
                        existing.pop("deleted_at", None)
                        existing["last_modified"] = now
                        _save_all(data)
                        logger.info(
                            "create_project: id=%s の deleted_at を解除して"
                            "既存を返す (undelete)", force_id,
                        )
                    else:
                        logger.warning(
                            "create_project: id=%s が既に存在するため新規作成を"
                            "スキップし既存を返す", force_id,
                        )
                    return existing
        data["projects"].append(project)
        _save_all(data)
    return project


def update_project(project_id: str, updates: dict) -> Optional[dict]:
    """プロジェクト情報を更新"""
    with _projects_lock:
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


class ProjectAccessDenied(PermissionError):
    """プロジェクトの所有者でないユーザーによる変更操作を拒否したことを示す例外。"""


def delete_project(
    project_id: str,
    *,
    enforce_owner: bool = True,
    hard_delete: bool = False,
) -> bool:
    """プロジェクトを削除（データファイルは削除しない）。

    デフォルトは soft delete: ``deleted_at`` フィールドを付与するのみ。
    list_projects() / get_project() の結果からは除外されるが、projects.json
    にはエントリが残り、restore_project() で復元可能。

    並行アクセス対策: 他ユーザーが解析中でも、データファイルは残るので
    R 側の analysis は継続可能。projects.json 上は消えるため UI 上は不可視に
    なるが、`get_project(..., include_deleted=True)` で参照可能。

    Parameters
    ----------
    enforce_owner : bool, default True
        True なら現在のユーザーが creator でない場合に ProjectAccessDenied を raise。
    hard_delete : bool, default False
        True なら projects.json エントリ自体を削除 (復元不可)。
        通常は False (soft delete) で運用し、運用者が手動で hard_delete する想定。
    """
    with _projects_lock:
        data = _load_all()
        target = None
        for p in data["projects"]:
            if p["id"] == project_id:
                target = p
                break
        if target is None:
            return False
        if enforce_owner and not can_modify_project(target):
            owner = target.get("created_by", "Unknown user")
            raise ProjectAccessDenied(
                f"プロジェクト '{target.get('name', project_id)}' は {owner} さんのもので、"
                f"削除権限がありません。"
            )
        if hard_delete:
            data["projects"] = [p for p in data["projects"] if p["id"] != project_id]
        else:
            # Soft delete: deleted_at を付与
            target["deleted_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            try:
                target["deleted_by"] = _safe_current_user()
            except Exception:
                pass
        _save_all(data)
        return True


def restore_project(project_id: str) -> bool:
    """soft delete されたプロジェクトを復元 (deleted_at を削除)。

    Returns:
        True なら復元成功、False なら対象が存在しない or 元から deleted_at が無い。
    """
    with _projects_lock:
        data = _load_all()
        for p in data["projects"]:
            if p["id"] == project_id and "deleted_at" in p:
                p.pop("deleted_at", None)
                p.pop("deleted_by", None)
                p["last_modified"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                _save_all(data)
                return True
    return False


def list_deleted_projects() -> list[dict]:
    """soft delete されたプロジェクトの一覧を返す (deleted_at の降順)。"""
    data = _load_all()
    deleted = [p for p in data.get("projects", []) if p.get("deleted_at")]
    deleted.sort(key=lambda p: p.get("deleted_at", ""), reverse=True)
    return deleted


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
    with _projects_lock:
        data = _load_all()
        for p in data["projects"]:
            if p["id"] == project_id:
                # 重複防止: force_id 指定時、同じ id のサブプロジェクトが
                # 既に存在すれば新規作成せず既存を返す (復元の冪等性確保)。
                # 過去に存在していた「同じ sub_id のサブが何度も append される」
                # バグの再発を防ぐ。
                # さらに、既存が soft-deleted (deleted_at 設定済) なら明示的に
                # undelete する (復元機能の本来の意味)。
                if force_id:
                    for existing_sub in p.get("sub_projects", []):
                        if existing_sub.get("id") == force_id:
                            now_ts = datetime.now().strftime(
                                "%Y-%m-%dT%H:%M:%S"
                            )
                            if existing_sub.get("deleted_at"):
                                existing_sub.pop("deleted_at", None)
                                existing_sub["last_modified"] = now_ts
                                p["last_modified"] = now_ts
                                _save_all(data)
                                logger.info(
                                    "create_sub_project: sub_id=%s の "
                                    "deleted_at を解除 (undelete)",
                                    force_id,
                                )
                            else:
                                logger.warning(
                                    "create_sub_project: sub_id=%s が "
                                    "project=%s に既に存在するため新規作成を"
                                    "スキップし既存を返す",
                                    force_id, project_id,
                                )
                            return existing_sub
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
    with _projects_lock:
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
    with _projects_lock:
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
    with _projects_lock:
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


def delete_sub_project(
    project_id: str, sub_id: str, *, enforce_owner: bool = True
) -> bool:
    """サブプロジェクトを削除

    Parameters
    ----------
    enforce_owner : bool, default True
        True なら現在のユーザーが parent project の creator でない場合
        ProjectAccessDenied を raise。False なら所有権チェックなし。
    """
    with _projects_lock:
        data = _load_all()
        for p in data["projects"]:
            if p["id"] == project_id:
                if enforce_owner and not can_modify_project(p):
                    owner = p.get("created_by", "Unknown user")
                    raise ProjectAccessDenied(
                        f"プロジェクト '{p.get('name', project_id)}' は {owner} さんのもので、"
                        f"サブプロジェクト削除権限がありません。"
                    )
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
        logger.error("メタデータ書き出し失敗: %s", exc)


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
        except Exception as e:
            logger.warning("メタデータ読み込み失敗 (%s): %s", meta_path, e)
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
            # --- 破損した絶対パスを現在の DATA_CANDIDATES 配下で再解決 ---
            sub_info, unresolved = resolve_project_paths(sub_info)

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
                label = (
                    f"✅ 復元: {proj_info.get('name', '')} / "
                    f"{sub_info.get('name', '')}"
                )
                if unresolved:
                    label += f" (未解決パス {len(unresolved)} 件)"
                messages.append(label)
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
