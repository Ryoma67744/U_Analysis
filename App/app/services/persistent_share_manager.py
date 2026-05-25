# =============================================================================
# MSI Analysis Application - Persistent Share Manager
# 無期限共有リンク管理モジュール
#
# share_manager.py との違い:
#   - 有効期限なし (expires_at 列を持たない)
#   - URL は /view/<token>。認証バイパス対象 (Tier B も不要)
#   - 1 プロジェクト × サブプロジェクトにつき 1 トークン (再発行で旧 token は失効)
#
# 既存の /share/<token> (期間付き、Tier B 必要) と並列に運用する。
# =============================================================================

import json
import logging
import os
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from filelock import FileLock

from app.config import OTHER_DIR

logger = logging.getLogger("msi.persistent_share_manager")

# /view/ 系の保存先 (shares.json と並列、別ファイル)
PERSISTENT_SHARES_DIR = OTHER_DIR / "shares"
PERSISTENT_SHARES_FILE = PERSISTENT_SHARES_DIR / "persistent_shares.json"

_persistent_lock = FileLock(str(PERSISTENT_SHARES_FILE) + ".lock", timeout=30)


def _ensure_file() -> None:
    PERSISTENT_SHARES_DIR.mkdir(parents=True, exist_ok=True)
    if not PERSISTENT_SHARES_FILE.exists():
        with open(PERSISTENT_SHARES_FILE, "w", encoding="utf-8") as f:
            json.dump({"shares": []}, f, indent=2, ensure_ascii=False)


def _load_all() -> dict:
    _ensure_file()
    try:
        with open(PERSISTENT_SHARES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("persistent_shares.json 読込失敗、空で初期化: %s", e)
        return {"shares": []}


def _save_all(data: dict) -> None:
    _ensure_file()
    fd, tmp_path = tempfile.mkstemp(
        dir=str(PERSISTENT_SHARES_DIR), suffix=".tmp",
        prefix="persistent_shares_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(PERSISTENT_SHARES_FILE))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# =========================================================================
# CRUD
# =========================================================================

def create_persistent_share(
    project_id: str,
    sub_project_id: str,
    project_name: str,
    sub_project_name: str,
    result_dir: str,
    rds_path: str = "",
    integration_method: str = "",
    memo: str = "",
    require_password: bool = False,
) -> dict:
    """無期限共有トークンを生成して保存。既存トークンがあれば置換 (再発行)。

    返り値: 作成された share dict (token フィールド含む)。
    """
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    token = secrets.token_urlsafe(16)
    share = {
        "token": token,
        "project_id": project_id,
        "sub_project_id": sub_project_id,
        "project_name": project_name,
        "sub_project_name": sub_project_name,
        "result_dir": result_dir,
        "rds_path": rds_path,
        "integration_method": integration_method,
        "created_at": now,
        "memo": memo,
        "view_count": 0,
        # ver4.2: パスワード要否を期限と独立させる (False=認証なしで開ける)
        "require_password": bool(require_password),
    }

    key = (project_id, sub_project_id)
    with _persistent_lock:
        data = _load_all()
        # 同じ project/sub の既存エントリを削除 (再発行で置換)
        data["shares"] = [
            s for s in data.get("shares", [])
            if (s.get("project_id"), s.get("sub_project_id")) != key
        ]
        data["shares"].append(share)
        _save_all(data)
    logger.info(
        "persistent_share created: project=%s sub=%s token=%s",
        project_id, sub_project_id, token[:8] + "...",
    )
    return share


def get_persistent_share(token: str) -> Optional[dict]:
    """token で検索。存在しなければ None。"""
    data = _load_all()
    for s in data.get("shares", []):
        if s.get("token") == token:
            return s
    return None


def get_persistent_share_by_project(
    project_id: str, sub_project_id: str,
) -> Optional[dict]:
    """project_id × sub_project_id で検索 (再発行検出用)。"""
    data = _load_all()
    for s in data.get("shares", []):
        if (s.get("project_id") == project_id
                and s.get("sub_project_id") == sub_project_id):
            return s
    return None


def increment_view_count(token: str) -> None:
    """ビュー数カウンタ。失敗しても呼び出し元には影響させない。"""
    try:
        with _persistent_lock:
            data = _load_all()
            for s in data.get("shares", []):
                if s.get("token") == token:
                    s["view_count"] = int(s.get("view_count", 0)) + 1
                    s["last_viewed_at"] = datetime.now().strftime(
                        "%Y-%m-%dT%H:%M:%S",
                    )
                    _save_all(data)
                    return
    except Exception as e:
        logger.warning("persistent_share view_count update failed: %s", e)


def list_persistent_shares() -> list[dict]:
    """全エントリを作成日時降順で返す。"""
    data = _load_all()
    shares = data.get("shares", [])
    shares.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return shares


def revoke_persistent_share(token: str) -> bool:
    """token を失効。削除に成功すれば True。"""
    with _persistent_lock:
        data = _load_all()
        original_len = len(data.get("shares", []))
        data["shares"] = [
            s for s in data.get("shares", [])
            if s.get("token") != token
        ]
        if len(data["shares"]) < original_len:
            _save_all(data)
            logger.info("persistent_share revoked: token=%s",
                        token[:8] + "...")
            return True
    return False


# =========================================================================
# URL 生成
# =========================================================================

def build_persistent_view_url(token: str) -> str:
    """無期限共有用の URL を生成。SHARE_BASE_URL を流用 (本番ドメイン共通)。"""
    from app.config import SHARE_BASE_URL, APP_PORT
    if SHARE_BASE_URL:
        base = SHARE_BASE_URL.rstrip("/")
    else:
        import socket
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
        except Exception:
            local_ip = "127.0.0.1"
        base = f"http://{local_ip}:{APP_PORT}"
    return f"{base}/view/{token}"
