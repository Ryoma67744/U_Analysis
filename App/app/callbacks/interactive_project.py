# =============================================================================
# MSI Analysis Application - Interactive Project Callbacks
# インタラクティブ解析 プロジェクト連携・保存 コールバック
#
# interactive_callbacks.py から分離されたプロジェクト関連コールバック。
# =============================================================================

import logging
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import (Input, Output, State, callback, ctx, no_update)

logger = logging.getLogger("msi.interactive.project")


# 共有状態を interactive_callbacks から参照
from app.callbacks.interactive_callbacks import (
    _interactive_data,
    _drop_state,
    _set_active_key,
)


# ---------------------------------------------------------------------------
# 解析完了後 → インタラクティブタブ自動連携
# ---------------------------------------------------------------------------

@callback(
    [Output("interactive_result_folder", "value", allow_duplicate=True),
     Output("interactive_msi_folder", "value", allow_duplicate=True),
     Output("interactive_entry_mode", "data", allow_duplicate=True),
     Output("interactive_project_select", "value", allow_duplicate=True),
     Output("interactive_sub_project_select", "value", allow_duplicate=True)],
    Input("main_tabs", "active_tab"),
    [State("app_state", "data"),
     State("data_folder", "value"),
     State("interactive_entry_mode", "data"),
     State("current_page", "data")],
    prevent_initial_call=True,
)
def auto_fill_interactive_from_analysis(active_tab, app_state, data_folder, entry_mode, current_page):
    """解析完了後にインタラクティブタブへ切替えた際、結果フォルダを自動設定"""
    if active_tab != "interactive" or current_page != "analysis":
        return (no_update,) * 5
    # sub_action_interactive から来た場合は既にセット済み → スキップ
    if entry_mode in ("sub_project", "shared"):
        return (no_update,) * 5
    # 解析が実行されていない場合はスキップ
    if not app_state or not app_state.get("full_output_dir"):
        return (no_update,) * 5
    # 解析中はスキップ
    if app_state.get("is_running"):
        return (no_update,) * 5

    return (
        app_state["full_output_dir"],
        data_folder or no_update,
        "sub_project",
        app_state.get("project_id") or no_update,
        app_state.get("sub_project_id") or no_update,
    )


# ---------------------------------------------------------------------------
# プロジェクト / サブプロジェクト選択コールバック
# ---------------------------------------------------------------------------

@callback(
    Output("interactive_project_row", "style"),
    Input("interactive_entry_mode", "data"),
    prevent_initial_call=True,
)
def toggle_project_dropdown_visibility(entry_mode):
    """entry_mode に応じてプロジェクトドロップダウンの表示/非表示を切り替え"""
    if entry_mode in ("sub_project", "shared"):
        return {"display": "none"}
    return {}


@callback(
    Output("interactive_project_select", "options"),
    [Input("main_tabs", "active_tab"),
     Input("current_page", "data")],
    prevent_initial_call=True,
)
def populate_interactive_projects(active_tab, current_page):
    """interactiveタブがアクティブになった時にプロジェクト一覧を取得"""
    if current_page != "analysis" or active_tab != "interactive":
        return no_update
    from app.services.project_manager import list_projects
    projects = list_projects()
    return [{"label": p["name"], "value": p["id"]} for p in projects]


@callback(
    [Output("interactive_sub_project_select", "options", allow_duplicate=True),
     Output("interactive_sub_project_select", "value", allow_duplicate=True)],
    Input("interactive_project_select", "value"),
    State("interactive_entry_mode", "data"),
    prevent_initial_call=True,
)
def populate_interactive_sub_projects(project_id, entry_mode):
    """プロジェクト選択時にサブプロジェクト一覧を取得。
    sub_project モード: options のみ更新（value は sub_action_interactive が設定済み）
    standalone モード: options を更新、value は None（ユーザーが選択）"""
    if not project_id:
        return [], None
    from app.services.project_manager import list_sub_projects
    subs = list_sub_projects(project_id)
    options = [{"label": s["name"], "value": s["id"]} for s in subs]
    if entry_mode in ("sub_project", "shared"):
        return options, no_update
    return options, None


@callback(
    [Output("interactive_viz_container", "style", allow_duplicate=True),
     Output("interactive_data_info", "children", allow_duplicate=True),
     Output("sap_skip_reset", "data", allow_duplicate=True),
     Output("sap_btn_wrapper", "style", allow_duplicate=True)],
    Input("interactive_project_select", "value"),
    State("sap_skip_reset", "data"),
    prevent_initial_call=True,
)
def reset_interactive_on_project_change(project_id, skip_reset):
    """プロジェクト変更時にインタラクティブデータをリセット。
    sub_project_select の value が同一でも確実にクリアされる。
    sap_skip_reset=True の場合はリセットをスキップ（保存後の自動切替時）。"""
    if skip_reset:
        return no_update, no_update, False, no_update

    # アクティブプロジェクトの state エントリを破棄（プロジェクト別キャッシュ対応）
    _drop_state()
    _set_active_key(None)

    if not project_id:
        return {"display": "none"}, "", False, {"display": "none"}
    return {"display": "none"}, "データを読み込んでください", False, {"display": "none"}


@callback(
    [Output("interactive_result_folder", "value", allow_duplicate=True),
     Output("interactive_msi_folder", "value", allow_duplicate=True),
     Output("interactive_data_info", "children", allow_duplicate=True),
     Output("int_cal_ms_instrument", "data"),
     Output("interactive_viz_container", "style", allow_duplicate=True),
     Output("sap_skip_reset", "data", allow_duplicate=True),
     Output("sap_btn_wrapper", "style", allow_duplicate=True)],
    Input("interactive_sub_project_select", "value"),
    [State("interactive_project_select", "value"),
     State("sap_skip_reset", "data")],
    prevent_initial_call=True,
)
def set_interactive_folders_from_sub_project(sub_id, project_id, skip_reset):
    """サブプロジェクト選択時にフォルダパスを自動設定 + データリセット
    sap_skip_reset=True の場合はリセットをスキップ（保存後の自動切替時）。"""
    if skip_reset:
        # ★ ver51.9 / C-4: 並びが 1 つずれていた。Output は
        #   [result_folder, msi_folder, data_info, ms_instrument,
        #    viz_container.style, sap_skip_reset, sap_btn_wrapper.style]
        #   の 7 つ。`(no_update,)*6 + (False,)` だと sap_skip_reset に
        #   no_update、sap_btn_wrapper.style に False（style として不正）が入る。
        #   skip フラグが降りないので **次のサブプロジェクト切替でもリセットが
        #   skip され**、フォルダ入力が前のサブプロジェクトを指したまま残る。
        return (no_update,) * 5 + (False, no_update)

    # 前のプロジェクトの state を破棄（複数プロジェクト同時閲覧時の混線防止）
    _drop_state()
    _set_active_key(None)

    if not sub_id or not project_id:
        return no_update, no_update, no_update, no_update, {"display": "none"}, False, {"display": "none"}
    from app.services.project_manager import get_sub_project
    sub = get_sub_project(project_id, sub_id)
    if not sub:
        return no_update, no_update, no_update, no_update, {"display": "none"}, False, {"display": "none"}
    result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")
    data_folder = sub.get("data_folder", "")
    ms_instrument = sub.get("ms_instrument", "TIMS")
    # 未設定フォルダの警告メッセージ
    warnings = []
    if not result_dir:
        warnings.append("結果フォルダが未設定です")
    elif not Path(result_dir).is_dir():
        # ver56.2: 「未設定」だけでなく「実体が無い」も出す。出力先が
        # コンテナの書き込み層 (/app 直下など) のままだと再ビルドで消えるが、
        # 登録は残るので従来は画面上どこにも異常が出なかった。
        warnings.append(
            f"結果フォルダが見つかりません: {result_dir}"
            "（移動されたか、コンテナ再作成で失われた可能性があります）"
        )
    if not data_folder:
        warnings.append("MSIデータフォルダが未設定です")
    msg = "\u26a0 " + "\u3001".join(warnings) if warnings else "データを読み込んでください"
    return (result_dir, data_folder, msg, ms_instrument, {"display": "none"}, False, {"display": "none"})


# =========================================================================
# プロジェクトとして保存
# =========================================================================


@callback(
    Output("save_as_project_modal", "is_open"),
    [Input("open_save_as_project_modal", "n_clicks"),
     Input("close_save_as_project_modal", "n_clicks")],
    State("save_as_project_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_save_as_project_modal(open_clicks, close_clicks, is_open):
    """モーダルの開閉"""
    if ctx.triggered_id in ("open_save_as_project_modal",
                            "close_save_as_project_modal"):
        return not is_open
    return no_update


@callback(
    [Output("sap_new_project_section", "style"),
     Output("sap_existing_project_section", "style"),
     Output("sap_new_sub_section", "style"),
     Output("sap_existing_sub_section", "style")],
    Input("sap_action_type", "value"),
    prevent_initial_call=True,
)
def switch_sap_action_type(action_type):
    """アクション切替でフィールドの表示/非表示を制御"""
    show, hide = {}, {"display": "none"}
    if action_type == "new_all":
        return show, hide, show, hide
    elif action_type == "add_sub":
        return hide, show, show, hide
    elif action_type == "link_existing":
        return hide, show, hide, show
    return no_update, no_update, no_update, no_update


@callback(
    Output("sap_project_select", "options"),
    Input("save_as_project_modal", "is_open"),
    prevent_initial_call=True,
)
def populate_sap_projects(is_open):
    """モーダル表示時にプロジェクト一覧を取得"""
    if not is_open:
        return no_update
    from app.services.project_manager import list_projects
    projects = list_projects()
    return [{"label": p["name"], "value": p["id"]} for p in projects]


@callback(
    Output("sap_sub_select", "options"),
    Input("sap_project_select", "value"),
    prevent_initial_call=True,
)
def populate_sap_sub_projects(project_id):
    """プロジェクト選択時にサブプロジェクト一覧を取得"""
    if not project_id:
        return []
    from app.services.project_manager import list_sub_projects
    subs = list_sub_projects(project_id)
    return [{"label": s["name"], "value": s["id"]} for s in subs]


@callback(
    [Output("sap_result_folder_display", "children"),
     Output("sap_msi_folder_display", "children")],
    Input("save_as_project_modal", "is_open"),
    [State("interactive_result_folder", "value"),
     State("interactive_msi_folder", "value")],
    prevent_initial_call=True,
)
def display_sap_paths(is_open, result_folder, msi_folder):
    """モーダル表示時にパス情報を表示"""
    if not is_open:
        return no_update, no_update
    return (
        f"結果フォルダ: {result_folder or '(未指定)'}",
        f"MSIデータフォルダ: {msi_folder or '(未指定)'}",
    )


@callback(
    [Output("sap_status", "children"),
     Output("save_as_project_modal", "is_open", allow_duplicate=True),
     Output("interactive_project_select", "options", allow_duplicate=True),
     Output("interactive_project_select", "value", allow_duplicate=True),
     Output("interactive_sub_project_select", "value", allow_duplicate=True),
     Output("interactive_entry_mode", "data", allow_duplicate=True),
     Output("sap_skip_reset", "data", allow_duplicate=True)],
    Input("execute_save_as_project", "n_clicks"),
    [State("sap_action_type", "value"),
     State("sap_project_name", "value"),
     State("sap_project_date", "value"),
     State("sap_project_select", "value"),
     State("sap_sub_name", "value"),
     State("sap_sub_date", "value"),
     State("sap_target_compound", "value"),
     State("sap_ms_instrument", "value"),
     State("sap_polarity", "value"),
     State("sap_sub_select", "value"),
     State("interactive_result_folder", "value"),
     State("interactive_msi_folder", "value")],
    prevent_initial_call=True,
)
def execute_save_as_project(
    n_clicks, action_type,
    proj_name, proj_date, existing_proj_id,
    sub_name, sub_date, target_compound, ms_instrument, polarity,
    existing_sub_id,
    result_folder, msi_folder,
):
    """プロジェクトとして保存を実行"""
    if not n_clicks:
        return (no_update,) * 7

    from app.services.project_manager import (
        create_project, create_sub_project, update_sub_project,
        list_projects, get_sub_project,
    )

    _no = (no_update,) * 7

    try:
        if action_type == "new_all":
            # --- 新規プロジェクト + 新規サブプロジェクト ---
            if not proj_name:
                return (
                    dbc.Alert("プロジェクト名を入力してください。",
                              color="warning"),
                ) + (no_update,) * 6
            if not sub_name:
                return (
                    dbc.Alert("サブプロジェクト名を入力してください。",
                              color="warning"),
                ) + (no_update,) * 6
            proj = create_project(
                name=proj_name,
                experiment_date=proj_date or "",
            )
            sub = create_sub_project(
                project_id=proj["id"],
                name=sub_name,
                experiment_date=sub_date or "",
                target_compound=target_compound or "",
                ms_instrument=ms_instrument or "",
                polarity=polarity or [],
                data_folder=msi_folder or "",
                output_dir=result_folder or "",
                extra_fields={"last_result_dir": result_folder or "",
                              "matrix": ""},
            )
            pid, sid = proj["id"], sub["id"]

        elif action_type == "add_sub":
            # --- 既存プロジェクトにサブプロジェクト追加 ---
            if not existing_proj_id:
                return (
                    dbc.Alert("プロジェクトを選択してください。",
                              color="warning"),
                ) + (no_update,) * 6
            if not sub_name:
                return (
                    dbc.Alert("サブプロジェクト名を入力してください。",
                              color="warning"),
                ) + (no_update,) * 6
            sub = create_sub_project(
                project_id=existing_proj_id,
                name=sub_name,
                experiment_date=sub_date or "",
                target_compound=target_compound or "",
                ms_instrument=ms_instrument or "",
                polarity=polarity or [],
                data_folder=msi_folder or "",
                output_dir=result_folder or "",
                extra_fields={"last_result_dir": result_folder or "",
                              "matrix": ""},
            )
            pid, sid = existing_proj_id, sub["id"]

        elif action_type == "link_existing":
            # --- 既存サブプロジェクトに紐付け ---
            if not existing_proj_id or not existing_sub_id:
                return (
                    dbc.Alert("プロジェクトとサブプロジェクトを選択してください。",
                              color="warning"),
                ) + (no_update,) * 6
            updates = {"last_result_dir": result_folder or ""}
            if msi_folder:
                updates["data_folder"] = msi_folder
            update_sub_project(existing_proj_id, existing_sub_id, updates)
            pid, sid = existing_proj_id, existing_sub_id
        else:
            return _no

        # プロジェクト一覧を更新
        projects = list_projects()
        proj_options = [
            {"label": p["name"], "value": p["id"]} for p in projects
        ]
        return (
            "",             # sap_status (クリア)
            False,          # モーダルを閉じる
            proj_options,   # プロジェクトドロップダウン更新
            pid,            # プロジェクト選択
            sid,            # サブプロジェクト選択
            "sub_project",  # entry_mode (populate が value を保持)
            True,           # sap_skip_reset (リセット抑止)
        )
    except Exception as e:
        return (
            dbc.Alert(f"保存エラー: {e}", color="danger"),
        ) + (no_update,) * 6
