# =============================================================================
# MSI Analysis Application - Share Manager
# 共有リンク管理モジュール
# =============================================================================

import json
import logging
import os
import secrets
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from filelock import FileLock

from app.config import SHARES_DIR, SHARES_FILE, DEFAULT_SHARE_EXPIRY_DAYS

logger = logging.getLogger("msi.share_manager")

# プロセス横断の排他ロック（複数ユーザー同時保存対策）
_shares_lock = FileLock(str(SHARES_FILE) + ".lock", timeout=30)


def _ensure_shares_file() -> None:
    """shares.json が存在しなければ初期化"""
    SHARES_DIR.mkdir(parents=True, exist_ok=True)
    if not SHARES_FILE.exists():
        with open(SHARES_FILE, "w", encoding="utf-8") as f:
            json.dump({"shares": []}, f, indent=2, ensure_ascii=False)


def _load_all() -> dict:
    """shares.json 全体を読み込み"""
    _ensure_shares_file()
    try:
        with open(SHARES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"shares.json 読込失敗、空で初期化: {e}")
        return {"shares": []}


def _save_all(data: dict) -> None:
    """shares.json 全体を原子的に保存（書き込み中断による破損を防止）"""
    _ensure_shares_file()
    fd, tmp_path = tempfile.mkstemp(
        dir=str(SHARES_DIR), suffix=".tmp", prefix="shares_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(SHARES_FILE))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    # 自動バックアップ
    try:
        from app.services.backup_manager import backup_on_save
        backup_on_save(SHARES_FILE)
    except Exception as e:
        logger.warning(f"shares.json 自動バックアップ失敗（処理続行）: {e}")


# =========================================================================
# 共有リンク CRUD
# =========================================================================

def create_share(
    project_id: str,
    sub_project_id: str,
    project_name: str,
    sub_project_name: str,
    result_dir: str,
    rds_path: str = "",
    integration_method: str = "",
    expires_days: int | None = None,
    memo: str = "",
    require_password: bool = True,
) -> dict:
    """共有トークンを生成して保存"""
    if expires_days is None:
        expires_days = DEFAULT_SHARE_EXPIRY_DAYS

    now = datetime.now()
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
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "expires_at": (now + timedelta(days=expires_days)).strftime("%Y-%m-%dT%H:%M:%S"),
        "memo": memo,
        # ver4.2: パスワード要否を期限と独立させる (True=共有パスでログイン必須)
        "require_password": bool(require_password),
    }

    with _shares_lock:
        data = _load_all()
        data["shares"].append(share)
        _save_all(data)
    return share


def get_share(token: str) -> Optional[dict]:
    """トークンで共有情報を検索（期限切れは None を返す）"""
    data = _load_all()
    for s in data["shares"]:
        if s["token"] == token:
            if _is_expired(s):
                return None
            return s
    return None


def is_valid(token: str) -> bool:
    """トークンが有効かどうか"""
    return get_share(token) is not None


def list_shares() -> list[dict]:
    """全共有リンクを返す（作成日時の降順）"""
    data = _load_all()
    shares = data.get("shares", [])
    for s in shares:
        s["is_expired"] = _is_expired(s)
    shares.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return shares


def delete_share(token: str) -> bool:
    """共有リンクを削除"""
    with _shares_lock:
        data = _load_all()
        original_len = len(data["shares"])
        data["shares"] = [s for s in data["shares"] if s["token"] != token]
        if len(data["shares"]) < original_len:
            _save_all(data)
            return True
    return False


def cleanup_expired() -> int:
    """期限切れの共有リンクを全て削除。削除件数を返す"""
    with _shares_lock:
        data = _load_all()
        original_len = len(data["shares"])
        data["shares"] = [s for s in data["shares"] if not _is_expired(s)]
        removed = original_len - len(data["shares"])
        if removed > 0:
            _save_all(data)
    return removed


# =========================================================================
# URL生成ヘルパー
# =========================================================================

def build_share_url(token: str) -> str:
    """共有URLを生成"""
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
    return f"{base}/share/{token}"


# =========================================================================
# 内部ヘルパー
# =========================================================================

def _is_expired(share: dict) -> bool:
    """共有リンクが期限切れかどうか"""
    expires_at = share.get("expires_at", "")
    if not expires_at:
        return False
    try:
        return datetime.now() > datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return False
