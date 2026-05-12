"""UI ロック関連の callback。

本ファイルは F2 で基盤を提供。F3-F5 で各編集フィールドが
acquire_lock_for_callback / release_lock_for_callback を呼び出す形で
UI 統合される。

機能:
- session_id_store: clientside callback で Cookie から session_id を取得し格納
- edit_lock_state: heartbeat 間隔で全 lock 状態をサーバから取得して格納
- refresh_edit_lock_state: heartbeat callback (server-side、定期更新 + 期限切れ削除)
"""
from __future__ import annotations

import logging
from typing import Optional

from dash import ClientsideFunction, Input, Output, State, callback, clientside_callback

from app.services import edit_lock_manager as elm
from app.services.session_id import short_display_id

logger = logging.getLogger("msi.edit_lock")


# ---------------------------------------------------------------------------
# Clientside: Cookie の session_id を dcc.Store に転送
# ---------------------------------------------------------------------------
# ページロード時 + heartbeat の最初の発火で実行され、msi_session_id Cookie を
# session_id_store に書き込む。これにより以降の server-side callback が
# State("session_id_store", "data") で参照できる。

clientside_callback(
    ClientsideFunction(namespace="session", function_name="get_session_id"),
    Output("session_id_store", "data"),
    Input("edit_lock_heartbeat", "n_intervals"),
    prevent_initial_call=False,
)


# ---------------------------------------------------------------------------
# Server-side: heartbeat 間隔で全 lock 状態を Store に同期 + 期限切れ削除
# ---------------------------------------------------------------------------
@callback(
    Output("edit_lock_state", "data"),
    Input("edit_lock_heartbeat", "n_intervals"),
    State("seurat_rds_path_store", "data"),
    State("session_id_store", "data"),
    prevent_initial_call=False,
)
def refresh_edit_lock_state(_n, rds_path, _session_id):
    """heartbeat 間隔で全 lock 状態を Store に同期 + 期限切れを削除。"""
    elm.cleanup_expired()
    if not rds_path:
        return {}
    return elm.get_locks_for_project(rds_path)


# ---------------------------------------------------------------------------
# Helper functions: F3-F5 で各 callback が呼び出すための取得 / 解放 API
# ---------------------------------------------------------------------------

def acquire_lock_for_callback(
    rds_path: Optional[str],
    field_id: str,
    session_id: Optional[str],
) -> tuple[bool, str]:
    """callback 内で使用するヘルパー: ロック取得を試み、(取得可否, owner_display) を返す。

    session_id または rds_path が None の場合は取得失敗扱い（フェイルセーフ）。
    Args:
        rds_path: 現在のプロジェクトキー
        field_id: ロック対象のフィールド識別子 (例: "cluster_rename:0")
        session_id: 取得しようとしているユーザーの session_id
    Returns:
        (success, owner_display)
        - success=True: 取得成功 (owner_display = 自分の表示名)
        - success=False: 他人が編集中 (owner_display = 現所有者の表示名)
    """
    if not (rds_path and session_id):
        return False, "Unknown"
    user_display = short_display_id(session_id)
    ok, entry = elm.try_acquire(rds_path, field_id, session_id, user_display)
    return ok, (entry.user_display if entry else "Unknown")


def release_lock_for_callback(
    rds_path: Optional[str],
    field_id: str,
    session_id: Optional[str],
) -> bool:
    """callback 内で使用するヘルパー: ロックを解放。"""
    if not (rds_path and session_id):
        return False
    return elm.release(rds_path, field_id, session_id)
