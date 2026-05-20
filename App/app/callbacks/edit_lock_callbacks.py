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
from app.services.session_id import get_display_name, short_display_id

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
    """heartbeat 間隔で全 lock 状態を Store に同期 + 期限切れを削除。

    並行して _project_states の stale eviction も実行 (リソースリーク防止)。
    """
    elm.cleanup_expired()
    # PR-H3 C1: project state も heartbeat で stale eviction
    try:
        from app.callbacks.interactive_callbacks import evict_stale_project_states
        evict_stale_project_states()
    except Exception:
        pass
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
    # 表示名は Flask session analyst_name 優先、なければ session_id ベース
    # (Flask request context 外で呼ばれた場合は session_id ベースに fallback)
    try:
        user_display = get_display_name()
    except Exception:
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


# ---------------------------------------------------------------------------
# PR-G3: キャリブレーション設定パネル全体を 1 ロックで管理
# ---------------------------------------------------------------------------
# パネル内の全フィールド (enable, ion_mode, matrix, adduct_filter, table_data,
# annotation_path, search_window, min_peaks, regression_mode) が変更されたら
# パネル全体に対する 1 ロック "calibration_panel" を取得。
# 他ユーザーには「⚠ alice がキャリブ設定を編集中」表示 + パネル内全フィールド disabled。

@callback(
    Output("calibration_panel_lock_indicator", "children", allow_duplicate=True),
    [Input("int_cal_enable", "value"),
     Input("int_cal_ion_mode", "value"),
     Input("int_cal_matrix", "value"),
     Input("int_cal_adduct_filter", "value"),
     Input("int_cal_table_data", "data"),
     Input("int_cal_annotation_path", "value"),
     Input("int_cal_search_window", "value"),
     Input("int_cal_min_peaks", "value"),
     Input("int_cal_regression_mode", "value")],
    [State("seurat_rds_path_store", "data"),
     State("session_id_store", "data")],
    prevent_initial_call=True,
)
def acquire_calibration_panel_lock(*args):
    """キャリブ設定の任意フィールドが変更された瞬間にパネルロック取得。"""
    rds_path = args[-2]
    session_id = args[-1]
    if not rds_path or not session_id:
        return no_update
    acquire_lock_for_callback(rds_path, "calibration_panel", session_id)
    return no_update


@callback(
    [Output("int_cal_enable", "disabled"),
     Output("int_cal_ion_mode", "options"),
     Output("int_cal_matrix", "disabled"),
     Output("int_cal_adduct_filter", "disabled"),
     Output("int_cal_table", "editable"),
     Output("int_cal_annotation_path", "disabled"),
     Output("int_cal_search_window", "disabled"),
     Output("int_cal_min_peaks", "disabled"),
     Output("int_cal_regression_mode", "disabled"),
     Output("int_cal_apply", "disabled"),
     Output("calibration_panel_lock_indicator", "children", allow_duplicate=True)],
    Input("edit_lock_state", "data"),
    State("session_id_store", "data"),
    prevent_initial_call="initial_duplicate",
)
def reflect_calibration_panel_lock(lock_state, my_session_id):
    """edit_lock_state を見て、キャリブパネル全フィールドの disabled / editable を一括反映。"""
    # Default (no lock): すべて編集可能
    default_ion_options = [
        {"label": "Positive", "value": "Positive"},
        {"label": "Negative", "value": "Negative"},
    ]
    disabled_ion_options = [
        {"label": "Positive", "value": "Positive", "disabled": True},
        {"label": "Negative", "value": "Negative", "disabled": True},
    ]

    if not lock_state:
        return (False, default_ion_options, False, False, True,
                False, False, False, False, False, "")

    owner = lock_state.get("calibration_panel")
    if owner and owner.get("user_id") != my_session_id:
        msg = f"⚠ {owner.get('user_display', '?')} がキャリブ設定を編集中"
        # 全フィールド disabled / editable=False
        return (True, disabled_ion_options, True, True, False,
                True, True, True, True, True, msg)
    return (False, default_ion_options, False, False, True,
            False, False, False, False, False, "")
