# =============================================================================
# MSI Analysis Application - Session Callbacks
# セッション管理 コールバック
# =============================================================================

from dash import Input, Output, State, callback, ctx, no_update

from app.services.session_manager import (
    save_session, load_session, list_sessions, delete_session,
)


# ---------------------------------------------------------------------------
# セッション保存
# ---------------------------------------------------------------------------

@callback(
    [Output("notification_toast", "children", allow_duplicate=True),
     Output("notification_toast", "is_open", allow_duplicate=True)],
    Input("save_session", "n_clicks"),
    [State("analysis_method", "value"),
     State("analysis_method_tims", "value"),
     State("data_folder", "value"),
     State("output_dir", "value"),
     State("mrm_path", "value"),
     State("p_thresh", "value"),
     State("logfc_thresh", "value"),
     State("resume_rds", "value"),
     State("filter_mode", "value"),
     State("target_clusters", "value"),
     State("rds_path", "value"),
     State("output_subfolder", "value")],
    prevent_initial_call=True,
)
def handle_save_session(
    n_clicks,
    desi_method, tims_method,
    data_folder, output_dir, mrm_path,
    p_thresh, logfc_thresh, resume_rds,
    filter_mode, target_clusters, rds_path,
    output_subfolder,
):
    if not n_clicks:
        return no_update, no_update

    session_data = {
        "analysis_method": desi_method or "",
        "analysis_method_tims": tims_method or "",
        "data_folder": data_folder or "",
        "output_dir": output_dir or "",
        "mrm_path": mrm_path or "",
        "p_thresh": p_thresh,
        "logfc_thresh": logfc_thresh,
        "resume_rds": resume_rds,
        "filter_mode": filter_mode or "exclude",
        "target_clusters": target_clusters or "",
        "rds_path": rds_path or "",
        "output_subfolder": output_subfolder or "",
    }

    try:
        path = save_session(session_data, output_dir or ".")
        return f"セッションを保存しました: {path}", True
    except Exception as e:
        return f"保存エラー: {e}", True


# ---------------------------------------------------------------------------
# セッション読込（ファイル選択 → UI復元）
# ---------------------------------------------------------------------------

@callback(
    [Output("analysis_method", "value", allow_duplicate=True),
     Output("analysis_method_tims", "value", allow_duplicate=True),
     Output("data_folder", "value", allow_duplicate=True),
     Output("output_dir", "value", allow_duplicate=True),
     Output("mrm_path", "value", allow_duplicate=True),
     Output("p_thresh", "value", allow_duplicate=True),
     Output("logfc_thresh", "value", allow_duplicate=True),
     Output("resume_rds", "value", allow_duplicate=True),
     Output("filter_mode", "value", allow_duplicate=True),
     Output("target_clusters", "value", allow_duplicate=True),
     Output("rds_path", "value", allow_duplicate=True),
     Output("notification_toast", "children", allow_duplicate=True),
     Output("notification_toast", "is_open", allow_duplicate=True)],
    Input("reload_session", "n_clicks"),
    State("session_history_table", "selected_rows"),
    State("session_history_table", "data"),
    prevent_initial_call=True,
)
def handle_reload_session(n_clicks, selected_rows, table_data):
    if not n_clicks or not selected_rows or not table_data:
        return (no_update,) * 13

    row = table_data[selected_rows[0]]
    session_path = row.get("path", "")

    try:
        data = load_session(session_path)

        desi_method = data.get("analysis_method") or None
        tims_method = data.get("analysis_method_tims") or None

        return (
            desi_method,
            tims_method,
            data.get("data_folder", ""),
            data.get("output_dir", ""),
            data.get("mrm_path", ""),
            data.get("p_thresh", 0.05),
            data.get("logfc_thresh", 0.10),
            data.get("resume_rds", False),
            data.get("filter_mode", "exclude"),
            data.get("target_clusters", ""),
            data.get("rds_path", ""),
            f"セッションを読み込みました: {row.get('name', '')}",
            True,
        )
    except Exception as e:
        return (no_update,) * 11 + (f"読み込みエラー: {e}", True)


# ---------------------------------------------------------------------------
# セッション履歴テーブル更新
# ---------------------------------------------------------------------------

@callback(
    Output("session_history_table", "data"),
    [Input("main_tabs", "active_tab"),
     Input("save_session", "n_clicks"),
     Input("delete_session", "n_clicks")],
    State("output_dir", "value"),
)
def update_session_history(active_tab, save_clicks, del_clicks, output_dir):
    if active_tab != "history" and not ctx.triggered_id:
        return []

    if not output_dir:
        return []

    sessions = list_sessions(output_dir)
    return sessions


# ---------------------------------------------------------------------------
# セッション削除
# ---------------------------------------------------------------------------

@callback(
    [Output("notification_toast", "children", allow_duplicate=True),
     Output("notification_toast", "is_open", allow_duplicate=True)],
    Input("delete_session", "n_clicks"),
    [State("session_history_table", "selected_rows"),
     State("session_history_table", "data")],
    prevent_initial_call=True,
)
def handle_delete_session(n_clicks, selected_rows, table_data):
    if not n_clicks or not selected_rows or not table_data:
        return no_update, no_update

    row = table_data[selected_rows[0]]
    session_path = row.get("path", "")

    if delete_session(session_path):
        return f"セッションを削除しました: {row.get('name', '')}", True
    return "削除に失敗しました", True


# ---------------------------------------------------------------------------
# サイドバーの「読込」ボタン → 履歴タブに切り替え
# ---------------------------------------------------------------------------

@callback(
    Output("main_tabs", "active_tab", allow_duplicate=True),
    Input("load_session", "n_clicks"),
    prevent_initial_call=True,
)
def switch_to_history_tab(n_clicks):
    if not n_clicks:
        return no_update
    return "history"
