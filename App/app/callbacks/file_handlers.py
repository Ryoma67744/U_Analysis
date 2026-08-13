# =============================================================================
# MSI Analysis Application - File Handler Callbacks
# ファイル選択・ファイルブラウザ コールバック
# =============================================================================

from pathlib import Path

from dash import Input, Output, State, callback, clientside_callback, ctx, no_update, html, ALL, MATCH
import dash_bootstrap_components as dbc
from dash import dcc

from app.config import (
    DEFAULT_DESI_DATA_FOLDER, DEFAULT_ANNOTATION_FILE_PATH,
    DEFAULT_TIMS_DATA_FOLDER, DEFAULT_ANNOTATION_CSV_PATH,
    DESI_DATA_DIR, TIMS_DATA_DIR, OUTPUT_DATA_DIR, APP_BASE_DIR,
    DESI_V8_TEMPLATE_PATH, DESI_CLUSTER_FILTER_PATH,
    TIMS_V8_TEMPLATE_PATH, TIMS_CLUSTER_FILTER_PATH,
)
from app.layouts.file_browser_modal import (
    get_available_drives, list_directory, build_breadcrumb_parts,
)
from app.services.data_manager import (
    list_msi_files, list_tims_files,
    find_tims_file_path, read_parquet_annotations, read_desi_roi_list,
)
from app.services.session_manager import save_last_settings
from app.services.notify import warn_user


# ---------------------------------------------------------------------------
# DESI / TIMS 排他選択
# ---------------------------------------------------------------------------

@callback(
    Output("analysis_method_tims", "value", allow_duplicate=True),
    Input("analysis_method", "value"),
    prevent_initial_call=True,
)
def clear_tims_on_desi_select(desi_val):
    """DESI が選択されたら TIMS の選択をクリア"""
    if desi_val:
        return None
    return no_update


@callback(
    Output("analysis_method", "value", allow_duplicate=True),
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
     Output("tims_reanalysis_ion_settings", "style"),
     Output("extra_folders_section", "style"),
     Output("desi_recommended_banner", "style"),
     Output("tims_recommended_banner", "style")],
    [Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
)
def toggle_settings_panels(desi_val, tims_val):
    active = desi_val or tims_val or "desi_v8"
    is_umap = active in ("desi_v8", "tims_v8")
    is_reanalysis = active in ("desi_cluster_filter", "tims_cluster_filter")
    is_desi_umap = active == "desi_v8"
    is_tims_umap = active == "tims_v8"
    is_tims_reanalysis = active == "tims_cluster_filter"

    umap_style = {} if is_umap else {"display": "none"}
    reanalysis_style = {} if is_reanalysis else {"display": "none"}
    tims_ion_style = {} if is_tims_umap else {"display": "none"}
    tims_reanalysis_ion_style = {} if is_tims_reanalysis else {"display": "none"}
    extra_folders_style = {"marginTop": "10px"} if is_tims_umap else {"display": "none"}
    # 標準フロー推奨バナー: 選択中の手法のものだけ表示（両方同時には出さない）
    desi_banner_style = {} if is_desi_umap else {"display": "none"}
    tims_banner_style = {} if is_tims_umap else {"display": "none"}

    return (umap_style, reanalysis_style, tims_ion_style, tims_reanalysis_ion_style,
            extra_folders_style, desi_banner_style, tims_banner_style)


# ---------------------------------------------------------------------------
# 正規化トグルの既定切替・NORM_MODE有効化
# ---------------------------------------------------------------------------

@callback(
    Output("normalize_input", "value"),
    Input("analysis_method", "value"),
    Input("analysis_method_tims", "value"),
    prevent_initial_call=True,
)
def set_default_normalize(desi_val, tims_val):
    """解析法に応じて正規化の既定を切替。
    TIMS(SCiLS RMS等で正規化済み入力)は既定 OFF＝二重正規化を回避。
    DESI(生データ)は既定 ON。ユーザーは手動で上書き可能。
    （active 判定は toggle_settings_panels と同じ desi 優先ロジック）
    """
    active = desi_val or tims_val or "desi_v8"
    return "OFF" if active == "tims_v8" else "ON"


@callback(
    Output("norm_mode", "disabled"),
    Input("normalize_input", "value"),
)
def toggle_norm_mode_enabled(normalize_input):
    """NORM_MODE は正規化 OFF のときのみ有効。"""
    return normalize_input != "OFF"


@callback(
    Output("norm_mode_reanalysis", "disabled"),
    Input("normalize_input_reanalysis", "value"),
)
def toggle_norm_mode_reanalysis_enabled(normalize_input_reanalysis):
    """再解析の NORM_MODE は正規化 OFF のときのみ有効。"""
    return normalize_input_reanalysis != "OFF"


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
# 再解析の途中再開パネル表示 (ver46.0)
# ---------------------------------------------------------------------------

@callback(
    Output("resume_reanalysis_panel", "style"),
    Input("resume_reanalysis", "value"),
)
def toggle_resume_reanalysis_panel(resume):
    if resume:
        return {"marginTop": "10px"}
    return {"display": "none"}


# ---------------------------------------------------------------------------
# データフォルダ → サンプル一覧
# ---------------------------------------------------------------------------

@callback(
    Output("sample_selector", "children"),
    [Input("data_folder", "value"),
     Input("analysis_method", "value"),
     Input("analysis_method_tims", "value"),
     Input("extra_data_folders_store", "data")],
)
def update_sample_selector(data_folder, desi_method, tims_method, extra_folders):
    if not data_folder or not Path(data_folder).is_dir():
        return html.Div("データフォルダを指定してください", className="text-muted")

    active = desi_method or tims_method or "desi_v8"
    if active in ("tims_v8", "tims_cluster_filter"):
        from app.services.data_manager import list_tims_files_multi
        all_folders = [data_folder] + (extra_folders or [])
        samples = list_tims_files_multi(all_folders)
    else:
        samples = list_msi_files(data_folder)

    if not samples:
        return html.Div("対応ファイルが見つかりません", className="text-warning")

    return dbc.Checklist(
        id="selected_samples",
        options=[{"label": s, "value": s} for s in samples],
        value=samples,  # デフォルト全選択
    )


# ---------------------------------------------------------------------------
# selected_samples → selected_samples_store 同期
# 動的生成の Checklist を静的 Store にブリッジ
# ---------------------------------------------------------------------------

@callback(
    Output("selected_samples_store", "data"),
    Input("selected_samples", "value"),
    prevent_initial_call=True,
)
def sync_selected_samples(value):
    return value or []


# ---------------------------------------------------------------------------
# Annotation（切片）選択 — TIMS Parquet 内の annotation 列から生成
# ---------------------------------------------------------------------------

@callback(
    [Output("annotation_selector", "children"),
     Output("annotation_filter_store", "data")],
    [Input("selected_samples", "value"),
     Input("data_folder", "value"),
     Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
    prevent_initial_call=True,
)
def update_annotation_selector(selected_samples, data_folder, desi_method, tims_method):
    """選択されたTIMSファイルごとにannotation一覧をチェックボックスで表示"""
    active = desi_method or tims_method or "desi_v8"

    # TIMS UMAP以外では非表示
    if active != "tims_v8":
        return [], None

    if not selected_samples or not data_folder or not Path(data_folder).is_dir():
        return [], None

    children = []
    all_annotations = []

    for sample in selected_samples:
        file_path = find_tims_file_path(data_folder, sample)
        if not file_path:
            continue
        annotations = read_parquet_annotations(file_path)
        if not annotations:
            continue

        all_annotations.extend(annotations)

        # ファイル名ラベル + チェックボックス
        children.append(html.Div([
            html.Small(f"\U0001F4C4 {sample}", className="fw-bold"),
            dbc.Checklist(
                id={"type": "annotation_check", "index": sample},
                options=[{"label": f" {a}", "value": a} for a in annotations],
                value=annotations,  # デフォルト全選択
                inline=True,
                className="ms-2",
            ),
        ], className="mb-1"))

    if not children:
        return [], None

    ui = [
        html.Hr(className="my-1"),
        html.Small("Annotation（切片）選択:", className="fw-bold"),
    ] + children

    return ui, sorted(set(all_annotations))


@callback(
    Output("annotation_filter_store", "data", allow_duplicate=True),
    Input({"type": "annotation_check", "index": ALL}, "value"),
    prevent_initial_call=True,
)
def sync_annotation_to_store(all_values):
    """パターンマッチング: 全annotation_checkの選択値をStoreに集約"""
    if not all_values:
        return None
    merged = []
    for vals in all_values:
        if vals:
            merged.extend(vals)
    return sorted(set(merged)) if merged else None


# ---------------------------------------------------------------------------
# DESI ROI 選択 UI (.txt の最終列が ROI 文字列の場合)
# ---------------------------------------------------------------------------

@callback(
    [Output("desi_roi_selector", "children"),
     Output("desi_roi_filter_store", "data")],
    [Input("selected_samples", "value"),
     Input("data_folder", "value"),
     Input("analysis_method", "value")],
    prevent_initial_call=True,
)
def update_desi_roi_selector(selected_samples, data_folder, desi_method):
    """選択された DESI ファイルごとに ROI 一覧をチェックボックスで表示。

    TIMS の annotation_selector と同じ pattern-matching 構造。
    各ファイルの最終列 ROI を読み取り、見つかった ROI をチェックボックスとして
    並べる。デフォルトは全選択。
    """
    # DESI モード以外では非表示 (空)
    if desi_method != "desi_v8":
        return [], None
    if not selected_samples or not data_folder or not Path(data_folder).is_dir():
        return [], None

    children = []
    all_rois = []

    for sample in selected_samples:
        # `.txt` が無くても read_desi_roi_list 側で Excel/CSV から自動変換して読む。
        file_path = Path(data_folder) / f"{sample}.txt"
        rois = read_desi_roi_list(str(file_path))
        if not rois:
            continue

        all_rois.extend(rois)
        children.append(html.Div([
            html.Small(f"\U0001F4C4 {sample}", className="fw-bold"),
            dbc.Checklist(
                id={"type": "desi_roi_check", "index": sample},
                options=[{"label": f" {r}", "value": r} for r in rois],
                value=rois,  # デフォルト全選択
                inline=True,
                className="ms-2",
            ),
        ], className="mb-1"))

    if not children:
        return [], None

    ui = [
        html.Hr(className="my-1"),
        html.Small("ROI 選択 (DESI):", className="fw-bold"),
    ] + children

    return ui, sorted(set(all_rois))


@callback(
    Output("desi_roi_filter_store", "data", allow_duplicate=True),
    Input({"type": "desi_roi_check", "index": ALL}, "value"),
    prevent_initial_call=True,
)
def sync_desi_roi_to_store(all_values):
    """パターンマッチング: 全 desi_roi_check の選択値を Store に集約。"""
    if not all_values:
        return None
    merged = []
    for vals in all_values:
        if vals:
            merged.extend(vals)
    return sorted(set(merged)) if merged else None


@callback(
    Output("sample_selector_reanalysis", "children"),
    [Input("reanalysis_data_folder", "value"),
     Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
)
def update_reanalysis_sample_selector(data_folder, desi_method, tims_method):
    if not data_folder or not Path(data_folder).is_dir():
        return html.Div("データフォルダを指定してください", className="text-muted")

    active = desi_method or tims_method or "desi_v8"
    if active in ("tims_v8", "tims_cluster_filter"):
        samples = list_tims_files(data_folder)
    else:
        samples = list_msi_files(data_folder)

    if not samples:
        return html.Div("対応ファイルが見つかりません", className="text-warning")

    return dbc.Checklist(
        id="selected_samples_reanalysis",
        options=[{"label": s, "value": s} for s in samples],
        value=samples,
    )


# ---------------------------------------------------------------------------
# 再解析用 Annotation（切片）選択 — TIMS Cluster Filter のみ
# ---------------------------------------------------------------------------

@callback(
    [Output("annotation_selector_reanalysis", "children"),
     Output("annotation_filter_store_reanalysis", "data")],
    [Input("selected_samples_reanalysis", "value"),
     Input("reanalysis_data_folder", "value"),
     Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
    prevent_initial_call=True,
)
def update_reanalysis_annotation_selector(selected_samples, data_folder,
                                           desi_method, tims_method):
    """再解析側: 選択されたTIMSファイルごとにannotation一覧をチェックボックスで表示"""
    active = desi_method or tims_method or "desi_v8"

    if active != "tims_cluster_filter":
        return [], None

    if not selected_samples or not data_folder or not Path(data_folder).is_dir():
        return [], None

    children = []
    all_annotations = []

    for sample in selected_samples:
        file_path = find_tims_file_path(data_folder, sample)
        if not file_path:
            continue
        annotations = read_parquet_annotations(file_path)
        if not annotations:
            continue

        all_annotations.extend(annotations)

        children.append(html.Div([
            html.Small(f"\U0001F4C4 {sample}", className="fw-bold"),
            dbc.Checklist(
                id={"type": "annotation_check_reanalysis", "index": sample},
                options=[{"label": f" {a}", "value": a} for a in annotations],
                value=annotations,
                inline=True,
                className="ms-2",
            ),
        ], className="mb-1"))

    if not children:
        return [], None

    ui = [
        html.Hr(className="my-1"),
        html.Small("Annotation（切片）選択:", className="fw-bold"),
    ] + children

    return ui, sorted(set(all_annotations))


@callback(
    Output("annotation_filter_store_reanalysis", "data", allow_duplicate=True),
    Input({"type": "annotation_check_reanalysis", "index": ALL}, "value"),
    prevent_initial_call=True,
)
def sync_reanalysis_annotation_to_store(all_values):
    """再解析側: 全annotation_check_reanalysisの選択値をStoreに集約"""
    if not all_values:
        return None
    merged = []
    for vals in all_values:
        if vals:
            merged.extend(vals)
    return sorted(set(merged)) if merged else None


# ---------------------------------------------------------------------------
# TIMS/DESI モード切替時に再解析パラメータをデフォルトにリセット
# ---------------------------------------------------------------------------

@callback(
    [Output("reanalysis_ion_mode", "value", allow_duplicate=True),
     Output("reanalysis_tolerance_mz", "value", allow_duplicate=True)],
    [Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
    prevent_initial_call=True,
)
def reset_reanalysis_defaults(desi_val, tims_val):
    """TIMS/DESIモード切替時に再解析パラメータをデフォルトにリセット"""
    from app.config import DEFAULT_ION_MODE, DEFAULT_TOLERANCE_MZ
    return DEFAULT_ION_MODE, DEFAULT_TOLERANCE_MZ


# ---------------------------------------------------------------------------
# データフォルダ自動切替（DESI/TIMS）
# ---------------------------------------------------------------------------

@callback(
    Output("data_folder", "value", allow_duplicate=True),
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
        return ["+H", "+Na", "+NH4", "+K"]
    return ["-H"]


@callback(
    Output("reanalysis_adduct_filter", "value"),
    Input("reanalysis_ion_mode", "value"),
    prevent_initial_call=True,
)
def auto_switch_reanalysis_adduct(ion_mode):
    if ion_mode == "Positive":
        return ["+H", "+Na", "+NH4", "+K"]
    return ["-H"]


# ---------------------------------------------------------------------------
# デフォルト設定リセット
# ---------------------------------------------------------------------------

@callback(
    [Output("desi_v8_script_path", "value", allow_duplicate=True),
     Output("desi_cluster_filter_script_path", "value", allow_duplicate=True),
     Output("tims_v8_script_path", "value", allow_duplicate=True),
     Output("tims_cluster_filter_script_path", "value", allow_duplicate=True)],
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
     Output("default_annotation_file", "value", allow_duplicate=True),
     Output("default_desi_output_dir", "value", allow_duplicate=True)],
    Input("reset_desi_defaults", "n_clicks"),
    prevent_initial_call=True,
)
def reset_desi_defaults(n):
    return DEFAULT_DESI_DATA_FOLDER, DEFAULT_ANNOTATION_FILE_PATH, str(DESI_DATA_DIR)


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
# デフォルト設定適用
# ---------------------------------------------------------------------------

@callback(
    [Output("data_folder", "value", allow_duplicate=True),
     Output("annotation_path", "value", allow_duplicate=True),
     Output("output_dir", "value", allow_duplicate=True)],
    Input("apply_desi_defaults", "n_clicks"),
    [State("default_desi_data_folder", "value"),
     State("default_annotation_file", "value"),
     State("default_desi_output_dir", "value")],
    prevent_initial_call=True,
)
def apply_desi_defaults(n, desi_folder, annotation_file, desi_output):
    if not n:
        return no_update, no_update, no_update
    try:
        save_last_settings({
            "default_desi_data_folder": desi_folder,
            "default_annotation_file": annotation_file,
            "default_desi_output_dir": desi_output,
        })
    except Exception as e:
        warn_user(f"DESI初期設定の保存に失敗: {e}")
    return desi_folder or no_update, annotation_file or no_update, desi_output or no_update


@callback(
    [Output("data_folder", "value", allow_duplicate=True),
     Output("annotation_path", "value", allow_duplicate=True),
     Output("output_dir", "value", allow_duplicate=True)],
    Input("apply_tims_defaults", "n_clicks"),
    [State("default_tims_data_folder", "value"),
     State("default_annotation_csv", "value"),
     State("default_tims_output_dir", "value")],
    prevent_initial_call=True,
)
def apply_tims_defaults(n, tims_folder, annotation_csv, tims_output):
    if not n:
        return no_update, no_update, no_update
    try:
        save_last_settings({
            "default_tims_data_folder": tims_folder,
            "default_annotation_csv": annotation_csv,
            "default_tims_output_dir": tims_output,
        })
    except Exception as e:
        warn_user(f"TIMS初期設定の保存に失敗: {e}")
    return tims_folder or no_update, annotation_csv or no_update, tims_output or no_update


@callback(
    Output("output_dir", "value", allow_duplicate=True),
    Input("apply_output_defaults", "n_clicks"),
    State("default_output_dir", "value"),
    prevent_initial_call=True,
)
def apply_output_defaults(n, output_dir):
    if not n:
        return no_update
    try:
        save_last_settings({"default_output_dir": output_dir})
    except Exception as e:
        warn_user(f"出力設定の保存に失敗: {e}")
    return output_dir or no_update


# ---------------------------------------------------------------------------
# ファイルブラウザモーダル
# ---------------------------------------------------------------------------

# ブラウズボタン ID → (mode, target_input_id) のマッピング
_BROWSE_BUTTONS = {
    "browse_folder": ("folder", "data_folder"),
    "browse_annotation": ("file", "annotation_path"),
    "browse_rds_folder": ("folder", "rds_folder"),
    "browse_rds_folder_reanalysis": ("folder", "rds_folder_reanalysis"),
    "browse_resume_reanalysis_dir": ("folder", "resume_reanalysis_dir"),
    "browse_reanalysis_folder": ("folder", "reanalysis_data_folder"),
    "browse_reanalysis_annotation": ("file", "reanalysis_annotation_path"),
    "browse_output": ("folder", "output_dir"),
    "browse_interactive_result": ("folder", "interactive_result_folder"),
    "browse_interactive_msi": ("folder", "interactive_msi_folder"),
    # サイドバーの参照ボタン
    "browse_desi_v8_script": ("file", "desi_v8_script_path"),
    "browse_desi_cluster_script": ("file", "desi_cluster_filter_script_path"),
    "browse_tims_v8_script": ("file", "tims_v8_script_path"),
    "browse_tims_cluster_script": ("file", "tims_cluster_filter_script_path"),
    "browse_default_desi_folder": ("folder", "default_desi_data_folder"),
    "browse_default_annotation_desi": ("file", "default_annotation_file"),
    "browse_default_desi_output": ("folder", "default_desi_output_dir"),
    "browse_default_tims_folder": ("folder", "default_tims_data_folder"),
    "browse_default_annotation": ("file", "default_annotation_csv"),
    "browse_default_tims_output": ("folder", "default_tims_output_dir"),
    "browse_default_output": ("folder", "default_output_dir"),
    "browse_int_cal_annotation": ("file", "int_cal_annotation_path"),
    # 再アノテーション
    "browse_reann_annotation": ("file", "reann_annotation_path"),
    # プロジェクト復元スキャンフォルダ
    "browse_restore_scan_folder": ("folder", "restore_scan_folder"),
    # データ管理サブタブ: 移動元フォルダ (退避したい /app 直下も選べるよう起点は APP_BASE_DIR)
    "dm_browse_move_src": ("folder", "dm_move_src"),
    # データ管理サブタブ: 移動先フォルダ (起点は解析出力。上部ショートカットで 4 か所へ飛べる)
    "dm_browse_move_dest": ("folder", "dm_move_dest_path"),
    # ver3.9: プロジェクト編集モーダルのサムネ画像パス
    "browse_edit_thumbnail": ("file", "edit_project_thumbnail"),
    # TIMS 追加データフォルダ
    "btn_add_extra_folder": ("folder", "extra_folder_pending_store"),
    # SCiLS 変換モーダル
    "browse_scils_input_folder": ("folder", "scils_input_folder"),
    "browse_scils_output_folder": ("folder", "scils_output_folder"),
    # 環境設定 (.env) モーダル
    "browse_env_tims_data_dir": ("folder", "env_tims_data_dir"),
    "browse_env_desi_data_dir": ("folder", "env_desi_data_dir"),
    "browse_env_r_home": ("folder", "env_r_home"),
    # RDS メンテナンスモーダル
    "browse_rds_maint_folder": ("folder", "rds_maint_folder"),
    # Parquet 再パックモーダル
    "browse_parquet_maint_folder": ("folder", "parquet_maint_folder"),
}

# 全対象入力フィールドIDの一覧（_BROWSE_BUTTONSのvalue[1]を収集）
_ALL_TARGET_IDS = list(dict.fromkeys(v[1] for v in _BROWSE_BUTTONS.values()))

# target_id → デフォルト起点ディレクトリのマッピング
# (環境変数 DESI_DATA_DIR / TIMS_DATA_DIR / OUTPUT_DATA_DIR が優先される)
_DEFAULT_START_DIR = {
    "data_folder": DESI_DATA_DIR,
    "default_desi_data_folder": DESI_DATA_DIR,
    "default_desi_output_dir": OUTPUT_DATA_DIR,
    "reanalysis_data_folder": DESI_DATA_DIR,
    "default_tims_data_folder": TIMS_DATA_DIR,
    "default_tims_output_dir": OUTPUT_DATA_DIR,
    "scils_output_folder": TIMS_DATA_DIR,
    "env_tims_data_dir": TIMS_DATA_DIR,
    "env_desi_data_dir": DESI_DATA_DIR,
    "output_dir": OUTPUT_DATA_DIR,
    "default_output_dir": OUTPUT_DATA_DIR,
    "restore_scan_folder": OUTPUT_DATA_DIR,
    "dm_move_dest_path": OUTPUT_DATA_DIR,
    # ver3.9: サムネ用 PNG は output 配下にあることが多い
    "edit_project_thumbnail": OUTPUT_DATA_DIR,
}

# dcc.Store は "data" プロパティ、dbc.Input/dcc.Input は "value" プロパティ
# NOTE: 旧「手動結果フォルダ」機能の孤児参照 result_folder_manual / browse_result_folder は
#       レイアウト未生成のため削除済み（存在しない Input/Output は共有コールバック
#       open_file_browser / apply_file_browser_selection を丸ごと停止させ、全参照ボタンを
#       無反応にしていた）。
_STORE_TARGETS = {"extra_folder_pending_store"}

def _target_property(tid):
    return "data" if tid in _STORE_TARGETS else "value"


# すべてのブラウズボタンからモーダルを開く
@callback(
    [Output("file_browser_modal", "is_open", allow_duplicate=True),
     Output("fb_state", "data", allow_duplicate=True),
     Output("fb_drive_selector", "options"),
     Output("fb_selected_path", "children", allow_duplicate=True)],
    [Input(btn_id, "n_clicks") for btn_id in _BROWSE_BUTTONS],
    [State("fb_state", "data")]
    + [State(tid, _target_property(tid)) for tid in _ALL_TARGET_IDS],
    prevent_initial_call=True,
)
def open_file_browser(*args):
    # args: [btn_clicks..., fb_state, target_values...]
    n_buttons = len(_BROWSE_BUTTONS)
    state = args[n_buttons]  # fb_state
    target_values = args[n_buttons + 1:]  # 各ターゲット入力欄の現在値

    triggered = ctx.triggered_id
    if triggered is None:
        return no_update, no_update, no_update, no_update

    if triggered in _BROWSE_BUTTONS:
        mode, target_id = _BROWSE_BUTTONS[triggered]
        drives = get_available_drives()

        # 対応する入力欄の現在値を取得し、初期ディレクトリを決定
        # 優先順: 入力欄の現在値 → target_id 既定 (DESI/TIMS DATA_DIR) → APP_BASE_DIR
        default_start = _DEFAULT_START_DIR.get(target_id)
        if default_start and default_start.is_dir():
            initial_dir = str(default_start)
        else:
            initial_dir = str(APP_BASE_DIR)
        try:
            idx = _ALL_TARGET_IDS.index(target_id)
            current_val = target_values[idx]
            if current_val:
                p = Path(current_val)
                if p.is_dir():
                    initial_dir = str(p)
                elif p.parent.is_dir():
                    # ファイルパスの場合は親ディレクトリを使用
                    initial_dir = str(p.parent)
        except (ValueError, IndexError):
            pass

        new_state = {
            "current_dir": initial_dir,
            "mode": mode,
            "caller_id": target_id,
            "selected_path": "",
        }
        return True, new_state, drives, ""

    return no_update, no_update, no_update, no_update


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
    [Output("fb_state", "data", allow_duplicate=True),
     Output("fb_selected_path", "children", allow_duplicate=True)],
    Input({"type": "fb_item", "path": ALL}, "n_clicks"),
    State("fb_state", "data"),
    prevent_initial_call=True,
)
def handle_fb_item_click(clicks, state):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update, no_update

    clicked_path = ctx.triggered_id["path"]
    path = Path(clicked_path)

    if path.is_dir():
        state["current_dir"] = str(path)
        # folder モードの場合のみディレクトリを selected_path に設定
        if state.get("mode") == "folder":
            state["selected_path"] = str(path)
    else:
        state["selected_path"] = str(path)
    return state, state.get("selected_path", "")


# モーダルの「選択」ボタン → 対応するInputに値を設定
# Dashでは動的にOutput先を変えることが難しいため、
# fb_state の caller_id を使って全対象フィールドの Output を一括定義し、
# 該当する1つだけ値を更新、残りは no_update を返す。

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


# ショートカットボタン押下 → fb_state.current_dir を指定パスに切替
# 既存の update_file_browser が fb_state を Input にしているため、
# state を更新するだけで一覧が再描画される。
@callback(
    Output("fb_state", "data", allow_duplicate=True),
    Input({"type": "fb_shortcut", "path": ALL}, "n_clicks"),
    State("fb_state", "data"),
    prevent_initial_call=True,
)
def handle_fb_shortcut(clicks, state):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update
    target_path = ctx.triggered_id.get("path")
    if not target_path or not Path(target_path).is_dir():
        return no_update
    new_state = dict(state or {})
    new_state["current_dir"] = target_path
    return new_state


# ---------------------------------------------------------------------------
# パス入力欄のファイル名バッジ（クライアントサイドコールバック）
# ---------------------------------------------------------------------------

_PATH_INPUT_IDS = [
    "data_folder", "rds_folder", "annotation_path", "output_dir",
    "reanalysis_data_folder", "rds_folder_reanalysis", "reanalysis_annotation_path",
    "resume_reanalysis_dir",
    "desi_v8_script_path", "desi_cluster_filter_script_path",
    "tims_v8_script_path", "tims_cluster_filter_script_path",
    "default_desi_data_folder", "default_annotation_file", "default_desi_output_dir",
    "default_tims_data_folder", "default_annotation_csv", "default_tims_output_dir",
    "default_output_dir",
    "reann_annotation_path",
]

clientside_callback(
    """function() {
        var args = Array.prototype.slice.call(arguments);
        return args.map(function(v) {
            if (!v) return '';
            var p = v.replace(/\\\\/g, '/').split('/');
            var name = p[p.length - 1] || p[p.length - 2] || '';
            return '\\ud83d\\udcc1 ' + name;
        });
    }""",
    [Output(f"{pid}_path_hint", "children") for pid in _PATH_INPUT_IDS],
    [Input(pid, "value") for pid in _PATH_INPUT_IDS],
)


# ---------------------------------------------------------------------------
# TIMS 追加データフォルダ管理
# ---------------------------------------------------------------------------

@callback(
    Output("extra_data_folders_store", "data"),
    Input("extra_folder_pending_store", "data"),
    State("extra_data_folders_store", "data"),
    prevent_initial_call=True,
)
def add_extra_folder(pending_path, current_folders):
    """ファイルブラウザで選択されたフォルダをリストに追加"""
    if not pending_path or not Path(pending_path).is_dir():
        return no_update
    folders = list(current_folders or [])
    if pending_path not in folders:
        folders.append(pending_path)
    return folders


@callback(
    Output("extra_data_folders_container", "children"),
    Input("extra_data_folders_store", "data"),
)
def render_extra_folders(folders):
    """追加フォルダリストをUI表示"""
    if not folders:
        return []
    items = []
    for i, folder in enumerate(folders):
        folder_name = Path(folder).name or folder
        items.append(
            dbc.ListGroupItem(
                className="d-flex justify-content-between align-items-center py-1 px-2",
                style={"fontSize": "0.85rem"},
                children=[
                    html.Span(f"\U0001f4c1 {folder_name}", title=folder),
                    dbc.Button(
                        "\u00d7", size="sm", color="danger", outline=True,
                        id={"type": "btn_remove_extra_folder", "index": i},
                        style={"padding": "0 6px", "lineHeight": "1.2"},
                    ),
                ],
            )
        )
    return dbc.ListGroup(items, flush=True, style={"marginTop": "5px"})


@callback(
    Output("extra_data_folders_store", "data", allow_duplicate=True),
    Input({"type": "btn_remove_extra_folder", "index": ALL}, "n_clicks"),
    State("extra_data_folders_store", "data"),
    prevent_initial_call=True,
)
def remove_extra_folder(n_clicks_list, current_folders):
    """×ボタンで追加フォルダを削除"""
    if not current_folders or not any(n for n in n_clicks_list if n):
        return no_update
    triggered = ctx.triggered_id
    if triggered and isinstance(triggered, dict):
        idx = triggered.get("index")
        if idx is not None and 0 <= idx < len(current_folders):
            folders = list(current_folders)
            folders.pop(idx)
            return folders
    return no_update
