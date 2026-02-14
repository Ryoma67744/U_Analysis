# =============================================================================
# MSI Analysis Application - File Handler Callbacks
# ファイル選択・ファイルブラウザ コールバック
# =============================================================================

from pathlib import Path

from dash import Input, Output, State, callback, ctx, no_update, html, ALL
import dash_bootstrap_components as dbc

from app.config import (
    DEFAULT_DESI_DATA_FOLDER, DEFAULT_MRM_FILE_PATH,
    DEFAULT_TIMS_DATA_FOLDER, DEFAULT_ANNOTATION_CSV_PATH,
    DESI_DATA_DIR, TIMS_DATA_DIR, APP_BASE_DIR,
    DESI_V8_TEMPLATE_PATH, DESI_CLUSTER_FILTER_PATH,
    TIMS_V8_TEMPLATE_PATH, TIMS_CLUSTER_FILTER_PATH,
)
from app.layouts.file_browser_modal import (
    get_available_drives, list_directory, build_breadcrumb_parts,
)
from app.services.data_manager import list_msi_files


# ---------------------------------------------------------------------------
# DESI / TIMS 排他選択
# ---------------------------------------------------------------------------

@callback(
    Output("analysis_method_tims", "value"),
    Input("analysis_method", "value"),
    prevent_initial_call=True,
)
def clear_tims_on_desi_select(desi_val):
    """DESI が選択されたら TIMS の選択をクリア"""
    if desi_val:
        return None
    return no_update


@callback(
    Output("analysis_method", "value"),
    Input("analysis_method_tims", "value"),
    prevent_initial_call=True,
)
def clear_desi_on_tims_select(tims_val):
    """TIMS が選択されたら DESI の選択をクリア"""
    if tims_val:
        return None
    return no_update


# ---------------------------------------------------------------------------
# 解析設定パネルの表示切替
# ---------------------------------------------------------------------------

@callback(
    [Output("umap_settings_panel", "style"),
     Output("reanalysis_settings_panel", "style"),
     Output("tims_ion_settings", "style"),
     Output("tims_reanalysis_ion_settings", "style")],
    [Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
)
def toggle_settings_panels(desi_val, tims_val):
    active = desi_val or tims_val or "desi_v8"
    is_umap = active in ("desi_v8", "tims_v8")
    is_reanalysis = active in ("desi_cluster_filter", "tims_cluster_filter")
    is_tims_umap = active == "tims_v8"
    is_tims_reanalysis = active == "tims_cluster_filter"

    umap_style = {} if is_umap else {"display": "none"}
    reanalysis_style = {} if is_reanalysis else {"display": "none"}
    tims_ion_style = {} if is_tims_umap else {"display": "none"}
    tims_reanalysis_ion_style = {} if is_tims_reanalysis else {"display": "none"}

    return umap_style, reanalysis_style, tims_ion_style, tims_reanalysis_ion_style


# ---------------------------------------------------------------------------
# RDS途中再開パネル表示
# ---------------------------------------------------------------------------

@callback(
    Output("resume_rds_panel", "style"),
    Input("resume_rds", "value"),
)
def toggle_resume_panel(resume):
    if resume:
        return {"marginTop": "10px"}
    return {"display": "none"}


# ---------------------------------------------------------------------------
# データフォルダ → サンプル一覧
# ---------------------------------------------------------------------------

@callback(
    Output("sample_selector", "children"),
    Input("data_folder", "value"),
)
def update_sample_selector(data_folder):
    if not data_folder or not Path(data_folder).is_dir():
        return html.Div("データフォルダを指定してください", className="text-muted")

    samples = list_msi_files(data_folder)
    if not samples:
        return html.Div("対応ファイルが見つかりません", className="text-warning")

    return dbc.Checklist(
        id="selected_samples",
        options=[{"label": s, "value": s} for s in samples],
        value=samples,  # デフォルト全選択
    )


@callback(
    Output("sample_selector_reanalysis", "children"),
    Input("reanalysis_data_folder", "value"),
)
def update_reanalysis_sample_selector(data_folder):
    if not data_folder or not Path(data_folder).is_dir():
        return html.Div("データフォルダを指定してください", className="text-muted")

    samples = list_msi_files(data_folder)
    if not samples:
        return html.Div("対応ファイルが見つかりません", className="text-warning")

    return dbc.Checklist(
        id="selected_samples_reanalysis",
        options=[{"label": s, "value": s} for s in samples],
        value=samples,
    )


# ---------------------------------------------------------------------------
# RDSフォルダ → RDSファイル一覧
# ---------------------------------------------------------------------------

@callback(
    Output("rds_file_selector", "children"),
    Input("rds_folder", "value"),
)
def update_rds_file_selector(rds_folder):
    if not rds_folder or not Path(rds_folder).is_dir():
        return html.Div("RDSフォルダを指定してください", className="text-muted")

    rds_files = sorted(Path(rds_folder).glob("*.rds"))
    if not rds_files:
        return html.Div("RDSファイルが見つかりません", className="text-warning")

    return dbc.Checklist(
        id="selected_rds_files",
        options=[{"label": f.name, "value": str(f)} for f in rds_files],
        value=[str(rds_files[0])],
    )


# ---------------------------------------------------------------------------
# データフォルダ自動切替（DESI/TIMS）
# ---------------------------------------------------------------------------

@callback(
    Output("data_folder", "value"),
    [Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
    [State("default_desi_data_folder", "value"),
     State("default_tims_data_folder", "value")],
    prevent_initial_call=True,
)
def auto_switch_data_folder(desi_val, tims_val, desi_default, tims_default):
    active = desi_val or tims_val
    if active in ("desi_v8", "desi_cluster_filter"):
        return desi_default or DEFAULT_DESI_DATA_FOLDER
    elif active in ("tims_v8", "tims_cluster_filter"):
        return tims_default or DEFAULT_TIMS_DATA_FOLDER
    return no_update


# ---------------------------------------------------------------------------
# Adductフィルター自動切替（イオンモード変更時）
# ---------------------------------------------------------------------------

@callback(
    Output("adduct_filter", "value"),
    Input("ion_mode", "value"),
    prevent_initial_call=True,
)
def auto_switch_adduct(ion_mode):
    if ion_mode == "Positive":
        return ["+H", "+Na", "+NH4"]
    return ["-H"]


@callback(
    Output("reanalysis_adduct_filter", "value"),
    Input("reanalysis_ion_mode", "value"),
    prevent_initial_call=True,
)
def auto_switch_reanalysis_adduct(ion_mode):
    if ion_mode == "Positive":
        return ["+H", "+Na", "+NH4"]
    return ["-H"]


# ---------------------------------------------------------------------------
# デフォルト設定リセット
# ---------------------------------------------------------------------------

@callback(
    [Output("desi_v8_script_path", "value"),
     Output("desi_cluster_filter_script_path", "value"),
     Output("tims_v8_script_path", "value"),
     Output("tims_cluster_filter_script_path", "value")],
    Input("reset_script_paths", "n_clicks"),
    prevent_initial_call=True,
)
def reset_script_paths(n):
    return (
        str(DESI_V8_TEMPLATE_PATH),
        str(DESI_CLUSTER_FILTER_PATH),
        str(TIMS_V8_TEMPLATE_PATH),
        str(TIMS_CLUSTER_FILTER_PATH),
    )


@callback(
    [Output("default_desi_data_folder", "value", allow_duplicate=True),
     Output("default_mrm_file", "value", allow_duplicate=True),
     Output("default_desi_output_dir", "value", allow_duplicate=True)],
    Input("reset_desi_defaults", "n_clicks"),
    prevent_initial_call=True,
)
def reset_desi_defaults(n):
    return DEFAULT_DESI_DATA_FOLDER, DEFAULT_MRM_FILE_PATH, str(DESI_DATA_DIR)


@callback(
    [Output("default_tims_data_folder", "value", allow_duplicate=True),
     Output("default_annotation_csv", "value", allow_duplicate=True),
     Output("default_tims_output_dir", "value", allow_duplicate=True)],
    Input("reset_tims_defaults", "n_clicks"),
    prevent_initial_call=True,
)
def reset_tims_defaults(n):
    return DEFAULT_TIMS_DATA_FOLDER, DEFAULT_ANNOTATION_CSV_PATH, str(TIMS_DATA_DIR)


@callback(
    Output("default_output_dir", "value", allow_duplicate=True),
    Input("reset_output_defaults", "n_clicks"),
    prevent_initial_call=True,
)
def reset_output_defaults(n):
    return str(APP_BASE_DIR)


# ---------------------------------------------------------------------------
# ファイルブラウザモーダル
# ---------------------------------------------------------------------------

# ブラウズボタン ID → (mode, target_input_id) のマッピング
_BROWSE_BUTTONS = {
    "browse_folder": ("folder", "data_folder"),
    "browse_mrm": ("file", "mrm_path"),
    "browse_rds_folder": ("folder", "rds_folder"),
    "browse_rds": ("file", "rds_path"),
    "browse_reanalysis_folder": ("folder", "reanalysis_data_folder"),
    "browse_output": ("folder", "output_dir"),
    "browse_result_folder": ("folder", "result_folder_manual"),
    "browse_interactive_result": ("folder", "interactive_result_folder"),
    "browse_interactive_msi": ("folder", "interactive_msi_folder"),
    # サイドバーの参照ボタン
    "browse_desi_v8_script": ("file", "desi_v8_script_path"),
    "browse_desi_cluster_script": ("file", "desi_cluster_filter_script_path"),
    "browse_tims_v8_script": ("file", "tims_v8_script_path"),
    "browse_tims_cluster_script": ("file", "tims_cluster_filter_script_path"),
    "browse_default_desi_folder": ("folder", "default_desi_data_folder"),
    "browse_default_mrm": ("file", "default_mrm_file"),
    "browse_default_desi_output": ("folder", "default_desi_output_dir"),
    "browse_default_tims_folder": ("folder", "default_tims_data_folder"),
    "browse_default_annotation": ("file", "default_annotation_csv"),
    "browse_default_tims_output": ("folder", "default_tims_output_dir"),
    "browse_default_output": ("folder", "default_output_dir"),
}

# すべてのブラウズボタンからモーダルを開く
@callback(
    [Output("file_browser_modal", "is_open"),
     Output("fb_state", "data"),
     Output("fb_drive_selector", "options")],
    [Input(btn_id, "n_clicks") for btn_id in _BROWSE_BUTTONS],
    [State("fb_state", "data")],
    prevent_initial_call=True,
)
def open_file_browser(*args):
    state = args[-1]
    triggered = ctx.triggered_id
    if triggered is None:
        return no_update, no_update, no_update

    if triggered in _BROWSE_BUTTONS:
        mode, target_id = _BROWSE_BUTTONS[triggered]
        drives = get_available_drives()
        initial_dir = str(APP_BASE_DIR)

        new_state = {
            "current_dir": initial_dir,
            "mode": mode,
            "caller_id": target_id,
            "selected_path": "",
        }
        return True, new_state, drives

    return no_update, no_update, no_update


# ディレクトリ内容の表示
@callback(
    [Output("fb_file_list", "children"),
     Output("fb_breadcrumb", "children"),
     Output("fb_path_input", "value")],
    [Input("fb_state", "data"),
     Input("fb_drive_selector", "value"),
     Input("fb_go_btn", "n_clicks")],
    [State("fb_path_input", "value")],
    prevent_initial_call=True,
)
def update_file_browser(state, drive_val, go_clicks, path_input):
    if not state:
        return no_update, no_update, no_update

    triggered = ctx.triggered_id

    if triggered == "fb_drive_selector" and drive_val:
        current_dir = drive_val
    elif triggered == "fb_go_btn" and path_input:
        current_dir = path_input
    else:
        current_dir = state.get("current_dir", "")

    if not current_dir or not Path(current_dir).is_dir():
        return (
            [html.Div("有効なパスを入力してください", style={"padding": "20px", "color": "#999"})],
            [],
            current_dir,
        )

    mode = state.get("mode", "folder")
    show_files = (mode == "file")
    items = list_directory(current_dir, show_files=show_files)

    # ファイルリスト構築
    file_items = []
    # 親ディレクトリへの「..」
    parent = str(Path(current_dir).parent)
    if parent != current_dir:
        file_items.append(
            html.Div(
                className="file-browser-item",
                children=["📁 .."],
                id={"type": "fb_item", "path": parent},
                n_clicks=0,
            )
        )
    for item in items:
        file_items.append(
            html.Div(
                className="file-browser-item",
                children=[f"{item['icon']} {item['name']}"],
                id={"type": "fb_item", "path": item["path"]},
                n_clicks=0,
            )
        )

    if not file_items:
        file_items = [html.Div("空のフォルダです", style={"padding": "20px", "color": "#999"})]

    # パンくず
    parts = build_breadcrumb_parts(current_dir)
    breadcrumb = []
    for i, part in enumerate(parts):
        if i > 0:
            breadcrumb.append(html.Span(" / "))
        breadcrumb.append(html.Span(part["name"]))

    return file_items, breadcrumb, current_dir


# ファイルブラウザ内のアイテムクリック
@callback(
    Output("fb_state", "data", allow_duplicate=True),
    Input({"type": "fb_item", "path": ALL}, "n_clicks"),
    State("fb_state", "data"),
    prevent_initial_call=True,
)
def handle_fb_item_click(clicks, state):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update

    clicked_path = ctx.triggered_id["path"]
    path = Path(clicked_path)

    if path.is_dir():
        state["current_dir"] = str(path)
        state["selected_path"] = str(path)
    else:
        state["selected_path"] = str(path)
    return state


# fb_state の selected_path 変更 → 選択パス表示を更新
@callback(
    Output("fb_selected_path", "children"),
    Input("fb_state", "data"),
    prevent_initial_call=True,
)
def update_selected_path_display(state):
    if not state:
        return ""
    return state.get("selected_path", "")


# モーダルの「選択」ボタン → 対応するInputに値を設定
# Dashでは動的にOutput先を変えることが難しいため、
# fb_state の caller_id を使って全対象フィールドの Output を一括定義し、
# 該当する1つだけ値を更新、残りは no_update を返す。

# 全対象入力フィールドIDの一覧（_BROWSE_BUTTONSのvalue[1]を収集）
_ALL_TARGET_IDS = list(dict.fromkeys(v[1] for v in _BROWSE_BUTTONS.values()))

# dcc.Store は "data" プロパティ、dbc.Input/dcc.Input は "value" プロパティ
_STORE_TARGETS = {"result_folder_manual"}

def _target_property(tid):
    return "data" if tid in _STORE_TARGETS else "value"

@callback(
    [Output(tid, _target_property(tid), allow_duplicate=True) for tid in _ALL_TARGET_IDS]
    + [Output("file_browser_modal", "is_open", allow_duplicate=True)],
    Input("fb_select_btn", "n_clicks"),
    State("fb_state", "data"),
    prevent_initial_call=True,
)
def apply_file_browser_selection(n_clicks, state):
    if not n_clicks or not state or not state.get("selected_path"):
        return [no_update] * (len(_ALL_TARGET_IDS) + 1)

    caller_id = state.get("caller_id", "")
    selected_path = state["selected_path"]

    results = []
    for tid in _ALL_TARGET_IDS:
        if tid == caller_id:
            results.append(selected_path)
        else:
            results.append(no_update)
    results.append(False)  # close modal
    return results

# キャンセルボタン
@callback(
    Output("file_browser_modal", "is_open", allow_duplicate=True),
    Input("fb_cancel_btn", "n_clicks"),
    prevent_initial_call=True,
)
def close_file_browser(n):
    return False
