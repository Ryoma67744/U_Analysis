"""編集フィールド単位の UI ロック管理。

(project_key, field_id) 単位で「誰が編集中か」を管理する。
heartbeat により延長されないと TIMEOUT 後に自動解放される。

スレッディング: 単一 Python プロセス内の dict + RLock で十分。
Dash の Flask 部はデフォルトで単一プロセスで動作する。複数 worker
構成（gunicorn など）にする場合は別途 diskcache / Redis 経由が必要。

使用例:
    ok, owner = try_acquire("proj_A", "cluster_rename:0", "u123", "User u1234")
    if ok:
        # 編集可能
        ...
    else:
        # owner.user_display が編集中
        ...
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.config import EDIT_LOCK_TIMEOUT_SEC


@dataclass
class LockEntry:
    user_id: str
    user_display: str
    expires_at: datetime
    acquired_at: datetime

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "user_display": self.user_display,
            "expires_at": self.expires_at.isoformat(),
            "acquired_at": self.acquired_at.isoformat(),
        }


_locks: dict[tuple[str, str], LockEntry] = {}
_lock_mutex = threading.RLock()


def try_acquire(
    project_key: str,
    field_id: str,
    user_id: str,
    user_display: str,
) -> tuple[bool, Optional[LockEntry]]:
    """ロック取得を試みる。

    Returns:
        (success, current_owner)
        - success=True: 取得 / 延長成功（current_owner = 自分）
        - success=False: 他人が編集中（current_owner = 現所有者）
    """
    now = datetime.now()
    expires = now + timedelta(seconds=EDIT_LOCK_TIMEOUT_SEC)
    with _lock_mutex:
        cur = _locks.get((project_key, field_id))
        if cur and cur.user_id != user_id and cur.expires_at > now:
            return False, cur
        new_entry = LockEntry(
            user_id=user_id,
            user_display=user_display,
            expires_at=expires,
            acquired_at=cur.acquired_at if (cur and cur.user_id == user_id) else now,
        )
        _locks[(project_key, field_id)] = new_entry
        return True, new_entry


def release(project_key: str, field_id: str, user_id: str) -> bool:
    """自分が所有するロックを解放。他人のロックは触らない。"""
    with _lock_mutex:
        cur = _locks.get((project_key, field_id))
        if cur and cur.user_id == user_id:
            _locks.pop((project_key, field_id), None)
            return True
        return False


def get_owner(project_key: str, field_id: str) -> Optional[LockEntry]:
    """指定フィールドの所有者を返す。TIMEOUT 済みは None（同時に削除）。"""
    now = datetime.now()
    with _lock_mutex:
        cur = _locks.get((project_key, field_id))
        if cur and cur.expires_at > now:
            return cur
        if cur:
            _locks.pop((project_key, field_id), None)
        return None


def get_locks_for_project(project_key: str) -> dict[str, dict]:
    """プロジェクト内の全アクティブロックを field_id -> dict 形式で返す（UI 用）。"""
    now = datetime.now()
    result: dict[str, dict] = {}
    with _lock_mutex:
        for (pk, fid), entry in list(_locks.items()):
            if pk != project_key:
                continue
            if entry.expires_at > now:
                result[fid] = entry.to_dict()
            else:
                _locks.pop((pk, fid), None)
    return result


def release_all_for_user(user_id: str) -> int:
    """指定ユーザーの全ロックを解放（セッション切断検知時用）。"""
    n = 0
    with _lock_mutex:
        for key in list(_locks.keys()):
            if _locks[key].user_id == user_id:
                _locks.pop(key, None)
                n += 1
    return n


def cleanup_expired() -> int:
    """TIMEOUT 済みのロックを一括削除（heartbeat callback で定期実行用）。"""
    now = datetime.now()
    n = 0
    with _lock_mutex:
        for key in list(_locks.keys()):
            if _locks[key].expires_at <= now:
                _locks.pop(key, None)
                n += 1
    return n
