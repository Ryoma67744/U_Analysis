# =============================================================================
# MSI Analysis Application - Settings Tab UI
# 解析設定タブUI
# =============================================================================

from datetime import datetime

from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc

from app.config import (
    DEFAULT_DESI_DATA_FOLDER, APP_BASE_DIR,
    DEFAULT_CALIBRATION_ENABLE, DEFAULT_CALIBRATION_MATRIX,
    DEFAULT_CALIBRATION_SEARCH_WINDOW, DEFAULT_CALIBRATION_MIN_PEAKS,
    DEFAULT_CALIBRATION_REGRESSION,
)
from app.services.session_manager import load_last_settings
from app.services.calibration_preset_manager import list_calibration_presets
from app.layouts.tooltips import help_badge


def _cal_preset_options():
    """キャリブレーションプリセットのドロップダウン選択肢を生成"""
    presets = list_calibration_presets()
    return [
        {
            "label": f"{p['name']}  [{p['matrix']} / {p['ion_mode']}]",
            "value": p["name"],
        }
        for p in presets
    ]



def create_settings_tab():
    """設定タブ本体: 「解析設定」「データ管理」のサブタブで構成"""
    return dbc.Tabs(
        id="settings_subtabs",
        active_tab="settings_subtab_analysis",
        className="mt-2",
        children=[
            dbc.Tab(
                label="解析設定",
                tab_id="settings_subtab_analysis",
                children=_create_analysis_settings_subtab(),
            ),
            dbc.Tab(
                label="データ管理",
                tab_id="settings_subtab_data",
                children=html.Div(
                    "（Step 5 で実装予定）",
                    className="p-3 text-muted",
                ),
            ),
        ],
    )


def _create_analysis_settings_subtab():
    """解析設定サブタブ本体 (旧 create_settings_tab の中身)"""
    ls = load_last_settings()  # 前回の設定を復元
    return html.Div(className="card", style={"marginTop": "15px"}, children=[
        # UMAP解析設定（DESI/TIMS共通）
        html.Div(
            id="umap_settings_panel",
            children=[
                html.H4(className="card-title", children=["📊 UMAP解析設定"]),
                dbc.Row(align="start", children=[
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("データフォルダ・サンプル選択"),
                            html.Div(
                                style={"display": "flex", "gap": "5px", "marginBottom": "4px"},
                                children=[
                                    dbc.Input(id="data_folder",
                                              value=ls.get("data_folder", DEFAULT_DESI_DATA_FOLDER),
                                              placeholder="データフォルダのパス"),
                                    dbc.Button("参照...", id="browse_folder", size="sm", color="secondary"),
                                ],
                            ),
                            html.Span(id="data_folder_badge", children="", style={"fontSize": "0.8rem"}),
                            html.Small(
                                id="data_folder_path_hint", children="",
                                style={"color": "#6c757d", "fontSize": "0.75rem",
                                       "marginTop": "2px", "display": "block"},
                            ),
                            html.Div(id="sample_selector"),
                            dcc.Store(id="selected_samples_store", data=[]),
                            dbc.FormText("チェックを入れたサンプルが解析対象になります"),
                            # --- 追加データフォルダ (TIMS複数フォルダ) ---
                            html.Div(
                                id="extra_folders_section",
                                style={"display": "none", "marginTop": "10px"},
                                children=[
                                    html.Hr(className="my-2"),
                                    html.Small("追加データフォルダ (TIMS)", className="fw-bold"),
                                    html.Div(id="extra_data_folders_container"),
                                    dbc.Button(
                                        "＋ フォルダ追加",
                                        id="btn_add_extra_folder",
                                        size="sm", color="info", outline=True,
                                        style={"marginTop": "5px"},
                                    ),
                                    dcc.Store(id="extra_data_folders_store", data=[]),
                                    dcc.Store(id="extra_folder_pending_store", data=""),
                                ],
                            ),
                            html.Div(id="annotation_selector", className="mt-2"),
                            dcc.Store(id="annotation_filter_store", data=None),
                        ]),
                        # RDS途中再開
                        html.Div(className="param-group", style={"marginTop": "15px"}, children=[
                            html.H5("RDSファイル"),
                            dbc.Checkbox(id="resume_rds", label=html.Span(["途中再開 (RDSから)", help_badge("resume_rds")]),
                                        value=ls.get("resume_rds", False)),
                            html.Div(
                                id="resume_rds_panel",
                                style={"display": "none", "marginTop": "10px"},
                                children=[
                                    dbc.Input(id="rds_folder", value=ls.get("rds_folder", ""),
                                              placeholder="RDSファイルが入っているフォルダ"),
                                    dbc.Button("参照...", id="browse_rds_folder", size="sm", color="secondary",
                                               style={"marginTop": "5px"}),
                                    html.Span(id="rds_folder_badge", children="",
                                              style={"fontSize": "0.8rem", "display": "block", "marginTop": "2px"}),
                                    html.Small(
                                        id="rds_folder_path_hint", children="",
                                        style={"color": "#6c757d", "fontSize": "0.75rem",
                                               "marginTop": "2px", "display": "block"},
                                    ),
                                    html.Div(style={"marginTop": "10px"}, children=[
                                        html.H6("RDSファイル選択"),
                                        html.Div(id="rds_file_selector"),
                                        dbc.FormText("チェックを入れたRDSファイルを使用します"),
                                    ]),
                                ],
                            ),
                        ]),
                    ]),
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("アノテーションファイル (オプション)"),
                            dbc.Input(id="annotation_path",
                                      value=ls.get("annotation_path", ls.get("mrm_path", "")),
                                      placeholder="アノテーションファイルのパス"),
                            dbc.Button("参照...", id="browse_annotation", size="sm", color="secondary",
                                       style={"marginTop": "5px"}),
                            html.Small(
                                id="annotation_path_path_hint", children="",
                                style={"color": "#6c757d", "fontSize": "0.75rem",
                                       "marginTop": "2px", "display": "block"},
                            ),
                        ]),
                        # TIMS イオンモード設定（TIMS選択時のみ表示）
                        html.Div(
                            id="tims_ion_settings",
                            style={"display": "none", "marginTop": "15px"},
                            children=[
                                html.Div(className="param-group", children=[
                                    html.H5(["イオンモード", help_badge("ion_mode")]),
                                    dbc.RadioItems(
                                        id="ion_mode",
                                        options=[
                                            {"label": "Positive", "value": "Positive"},
                                            {"label": "Negative", "value": "Negative"},
                                        ],
                                        value=ls.get("ion_mode", "Positive"), inline=True,
                                    ),
                                    html.H5(["m/z許容誤差", help_badge("tolerance_mz")], style={"marginTop": "10px"}),
                                    dbc.Input(id="tolerance_mz", type="number",
                                              value=ls.get("tolerance_mz", 0.01), min=0, step=0.001,
                                              style={"width": "50%"}),
                                    html.H5(["m/z アライメント (ppm)", help_badge("mz_align_ppm")], style={"marginTop": "10px"}),
                                    dbc.Input(id="mz_align_ppm", type="number",
                                              value=ls.get("mz_align_ppm", 0),
                                              min=0, max=500, step=1,
                                              style={"width": "50%"}),
                                    dbc.FormText("0 = 無効。複数サンプル間でm/z値を統一する許容誤差 (ppm)"),
                                    html.H5(["Adductフィルター", help_badge("adduct_filter")], style={"marginTop": "10px"}),
                                    dbc.Checklist(
                                        id="adduct_filter",
                                        options=[
                                            {"label": "+H", "value": "+H"},
                                            {"label": "+Na", "value": "+Na"},
                                            {"label": "+NH4", "value": "+NH4"},
                                            {"label": "+K", "value": "+K"},
                                            {"label": "-H", "value": "-H"},
                                        ],
                                        value=["+H", "+Na", "+NH4", "+K"],
                                        inline=True,
                                    ),
                                    # --- m/z キャリブレーション ---
                                    html.Hr(style={"marginTop": "15px", "marginBottom": "10px"}),
                                    html.H5(["m/z キャリブレーション", help_badge("calibration")],
                                            style={"marginTop": "5px"}),
                                    dbc.Checkbox(
                                        id="calibration_enable",
                                        label="マトリクスピークでキャリブレーション",
                                        value=ls.get("calibration_enable", DEFAULT_CALIBRATION_ENABLE),
                                    ),
                                    html.Div(
                                        id="calibration_detail_panel",
                                        style={"display": "none", "marginTop": "10px",
                                               "padding": "10px", "background": "#f8f9fa",
                                               "borderRadius": "5px"},
                                        children=[
                                            dbc.Label("マトリクス種"),
                                            dbc.Select(
                                                id="calibration_matrix",
                                                options=[
                                                    {"label": "DHB (2,5-Dihydroxybenzoic acid)", "value": "DHB"},
                                                    {"label": "CHCA (α-Cyano-4-hydroxycinnamic acid)", "value": "CHCA"},
                                                    {"label": "9-AA (9-Aminoacridine)", "value": "9AA"},
                                                    {"label": "カスタム (手動入力)", "value": "custom"},
                                                ],
                                                value=ls.get("calibration_matrix", DEFAULT_CALIBRATION_MATRIX),
                                            ),
                                            # ---- キャリブレーション プリセット ----
                                            html.Hr(style={"margin": "8px 0"}),
                                            dbc.Row([
                                                dbc.Col(width=7, children=[
                                                    dcc.Dropdown(
                                                        id="cal_preset_select",
                                                        options=_cal_preset_options(),
                                                        placeholder="過去のキャリブレーションを選択...",
                                                        clearable=True,
                                                        style={"fontSize": "13px"},
                                                    ),
                                                ]),
                                                dbc.Col(width=5, children=[
                                                    dbc.InputGroup(size="sm", children=[
                                                        dbc.Input(
                                                            id="cal_preset_name_input",
                                                            placeholder="プリセット名",
                                                            style={"fontSize": "12px"},
                                                        ),
                                                        dbc.Button(
                                                            "保存", id="cal_preset_save_btn",
                                                            color="success", outline=True, size="sm",
                                                        ),
                                                        dbc.Button(
                                                            "削除", id="cal_preset_delete_btn",
                                                            color="danger", outline=True, size="sm",
                                                        ),
                                                    ]),
                                                ]),
                                            ], className="mb-2"),
                                            html.Small(id="cal_preset_status",
                                                       style={"color": "gray"}),
                                            dcc.Store(id="cal_preset_loading_flag", data=False),
                                            dcc.Store(id="cal_per_sample_store", data={}),
                                            dcc.Store(id="cal_sample_selector_prev", data="__all__"),
                                            html.Hr(style={"margin": "8px 0"}),
                                            dbc.Label("キャリブレーション対象",
                                                      className="small mt-1"),
                                            dcc.Dropdown(
                                                id="cal_sample_selector",
                                                options=[{"label": "全サンプル共通",
                                                          "value": "__all__"}],
                                                value="__all__",
                                                clearable=False,
                                                style={"fontSize": "13px",
                                                       "marginBottom": "8px"},
                                            ),
                                            html.Div(
                                                style={"marginTop": "10px"},
                                                children=[
                                                    dbc.Label("リファレンス / 実測値 対応表"),
                                                    dash_table.DataTable(
                                                        id="calibration_table",
                                                        columns=[
                                                            {"name": "Reference m/z", "id": "ref_mz",
                                                             "editable": True, "type": "numeric"},
                                                            {"name": "Formula", "id": "formula",
                                                             "editable": True, "type": "text"},
                                                            {"name": "Observed m/z", "id": "obs_mz",
                                                             "editable": True, "type": "numeric"},
                                                            {"name": "Δppm", "id": "ppm_drift",
                                                             "editable": False, "type": "text"},
                                                        ],
                                                        editable=True,
                                                        data=ls.get("calibration_table_data", []),
                                                        row_selectable="multi",
                                                        style_table={"overflowX": "auto"},
                                                        style_cell={
                                                            "textAlign": "center",
                                                            "padding": "5px",
                                                            "fontSize": "0.85rem",
                                                            "minWidth": "90px",
                                                        },
                                                        style_header={
                                                            "backgroundColor": "#f8f9fa",
                                                            "fontWeight": "600",
                                                        },
                                                        style_data_conditional=[
                                                            {"if": {"filter_query": '{obs_mz} eq ""'},
                                                             "backgroundColor": "#f5f5f5",
                                                             "color": "#aaa"},
                                                        ],
                                                    ),
                                                    html.Div(
                                                        className="d-flex gap-2 mt-2",
                                                        children=[
                                                            dbc.Button(
                                                                "行追加",
                                                                id="calibration_add_row",
                                                                size="sm", color="secondary",
                                                                outline=True,
                                                            ),
                                                            dbc.Button(
                                                                "選択行削除",
                                                                id="calibration_delete_rows",
                                                                size="sm", color="danger",
                                                                outline=True,
                                                            ),
                                                            dbc.Button(
                                                                "ピーク自動検出",
                                                                id="calibration_auto_detect",
                                                                size="sm", color="info",
                                                            ),
                                                            dbc.Button(
                                                                "List保存",
                                                                id="calibration_save_list",
                                                                size="sm", color="success",
                                                                outline=True,
                                                            ),
                                                            dbc.Button(
                                                                "リセット",
                                                                id="calibration_reset_list",
                                                                size="sm", color="warning",
                                                                outline=True,
                                                            ),
                                                        ],
                                                    ),
                                                    html.Div(
                                                        id="calibration_status_text",
                                                        style={"marginTop": "8px",
                                                               "fontSize": "12px",
                                                               "color": "#666"},
                                                    ),
                                                    dbc.FormText(
                                                        "マトリクス種変更でリファレンス値リセット。"
                                                        "データ読込後「ピーク自動検出」で実測値を検索。"
                                                    ),
                                                ],
                                            ),
                                            html.Details([
                                                html.Summary("詳細設定",
                                                             style={"cursor": "pointer",
                                                                    "fontSize": "12px",
                                                                    "marginTop": "8px"}),
                                                html.Div(style={"marginTop": "5px"}, children=[
                                                    dbc.Row([
                                                        dbc.Col(width=6, children=[
                                                            dbc.Label("検索ウィンドウ (Da)",
                                                                      className="small"),
                                                            dbc.Input(
                                                                id="calibration_search_window",
                                                                type="number",
                                                                value=ls.get("calibration_search_window",
                                                                             DEFAULT_CALIBRATION_SEARCH_WINDOW),
                                                                min=0.01, max=2.0, step=0.01,
                                                            ),
                                                        ]),
                                                        dbc.Col(width=6, children=[
                                                            dbc.Label("最低マッチピーク数",
                                                                      className="small"),
                                                            dbc.Input(
                                                                id="calibration_min_peaks",
                                                                type="number",
                                                                value=ls.get("calibration_min_peaks",
                                                                             DEFAULT_CALIBRATION_MIN_PEAKS),
                                                                min=1, max=10, step=1,
                                                            ),
                                                        ]),
                                                    ]),
                                                    dbc.Row([
                                                        dbc.Col(width=5, children=[
                                                            dbc.Label("回帰モデル",
                                                                      className="small"),
                                                        ]),
                                                        dbc.Col(width=7, children=[
                                                            dbc.Select(
                                                                id="calibration_regression_mode",
                                                                options=[
                                                                    {"label": "線形 (1次)",
                                                                     "value": "linear"},
                                                                    {"label": "多項式 (2次)",
                                                                     "value": "poly2"},
                                                                    {"label": "多項式 (3次)",
                                                                     "value": "poly3"},
                                                                ],
                                                                value=ls.get(
                                                                    "calibration_regression_mode",
                                                                    DEFAULT_CALIBRATION_REGRESSION),
                                                            ),
                                                        ]),
                                                    ], className="mt-2"),
                                                ]),
                                            ]),
                                        ],
                                    ),
                                ]),
                            ],
                        ),
                    ]),
                ]),

                # 詳細設定（折りたたみ）
                html.Details([
                    html.Summary(
                        "🎛 詳細設定（p値閾値・log2FC閾値）",
                        style={"cursor": "pointer", "color": "#666", "fontSize": "13px", "marginTop": "10px"},
                    ),
                    html.Div(
                        style={"background": "#f8f9fa", "padding": "15px", "borderRadius": "5px", "marginTop": "5px"},
                        children=[
                            dbc.Row([
                                dbc.Col(width=6, children=[
                                    dbc.Label(["p値閾値", help_badge("p_thresh")]),
                                    dbc.Input(id="p_thresh", type="number",
                                              value=ls.get("p_thresh", 0.05), min=0, max=1, step=0.01),
                                ]),
                                dbc.Col(width=6, children=[
                                    dbc.Label(["log2FC閾値", help_badge("logfc_thresh")]),
                                    dbc.Input(id="logfc_thresh", type="number",
                                              value=ls.get("logfc_thresh", 0.25), min=0, step=0.05),
                                ]),
                            ]),
                        ],
                    ),
                ]),

            ],
        ),

        # 再解析設定（DESI/TIMS共通）
        html.Div(
            id="reanalysis_settings_panel",
            style={"display": "none"},
            children=[
                html.H4(className="card-title", children=["🔍 再解析設定"]),
                dbc.Row([
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("データフォルダ・サンプル選択"),
                            html.Div(
                                style={"display": "flex", "gap": "5px", "marginBottom": "10px"},
                                children=[
                                    dbc.Input(id="reanalysis_data_folder",
                                              value=ls.get("reanalysis_data_folder", DEFAULT_DESI_DATA_FOLDER),
                                              placeholder="データフォルダのパス"),
                                    dbc.Button("参照...", id="browse_reanalysis_folder",
                                               size="sm", color="secondary"),
                                ],
                            ),
                            html.Small(
                                id="reanalysis_data_folder_path_hint", children="",
                                style={"color": "#6c757d", "fontSize": "0.75rem",
                                       "marginTop": "2px", "display": "block"},
                            ),
                            html.Div(id="sample_selector_reanalysis"),
                            dbc.FormText("チェックを入れたサンプルが再解析対象になります"),
                            html.Div(id="annotation_selector_reanalysis", className="mt-2"),
                            dcc.Store(id="annotation_filter_store_reanalysis", data=None),
                        ]),
                        # --- フィルタモード（Row 1 左カラムに統合）---
                        html.Div(className="param-group", style={"marginTop": "10px"}, children=[
                            html.H5(["フィルタモード", help_badge("filter_mode")]),
                            dbc.RadioItems(
                                id="filter_mode",
                                options=[
                                    {"label": "除外 (exclude)", "value": "exclude"},
                                    {"label": "抽出 (keep)", "value": "keep"},
                                ],
                                value=ls.get("filter_mode", "exclude"), inline=True,
                            ),
                        ]),
                        # --- 対象クラスタ（Row 1 左カラムに統合）---
                        html.Div(className="param-group", style={"marginTop": "10px"}, children=[
                            html.H5("対象クラスタ"),
                            dbc.Input(id="target_clusters", placeholder="例: 0, 1, 5, 7",
                                      value=ls.get("target_clusters", "")),
                            dbc.FormText("カンマ区切りでクラスタ番号を入力"),
                        ]),
                    ]),
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("RDS指定"),
                            html.H6("RDSフォルダ"),
                            html.Div(
                                style={"display": "flex", "gap": "5px", "marginBottom": "5px"},
                                children=[
                                    dbc.Input(id="rds_folder_reanalysis",
                                              value=ls.get("rds_folder_reanalysis", ""),
                                              placeholder="RDS_Filesフォルダのパス"),
                                    dbc.Button("参照...", id="browse_rds_folder_reanalysis",
                                               size="sm", color="secondary"),
                                ],
                            ),
                            html.Small(
                                id="rds_folder_reanalysis_path_hint", children="",
                                style={"color": "#6c757d", "fontSize": "0.75rem",
                                       "marginTop": "2px", "display": "block"},
                            ),
                            html.Span(id="rds_detection_badge"),
                            html.Div(
                                id="cluster_source_container",
                                style={"display": "none"},
                                children=[
                                    html.H6("クラスタソース", style={"marginTop": "5px"}),
                                    dbc.RadioItems(
                                        id="cluster_source",
                                        options=[
                                            {"label": "Harmony/PCA", "value": "harmony"},
                                            {"label": "RPCA", "value": "rpca"},
                                        ],
                                        value=ls.get("cluster_source", "harmony"),
                                        inline=True,
                                    ),
                                ],
                            ),
                            # 後方互換: rds_path を非表示で維持（既存State参照用）
                            dbc.Input(id="rds_path", value="",
                                      style={"display": "none"}),
                        ]),
                        # TIMS 再解析イオンモード
                        html.Div(
                            id="tims_reanalysis_ion_settings",
                            style={"display": "none", "marginTop": "15px"},
                            children=[
                                html.Div(className="param-group", children=[
                                    html.H5("イオンモード"),
                                    dbc.RadioItems(
                                        id="reanalysis_ion_mode",
                                        options=[
                                            {"label": "Positive", "value": "Positive"},
                                            {"label": "Negative", "value": "Negative"},
                                        ],
                                        value=ls.get("reanalysis_ion_mode", "Positive"), inline=True,
                                    ),
                                    html.H5("m/z許容誤差", style={"marginTop": "10px"}),
                                    dbc.Input(id="reanalysis_tolerance_mz", type="number",
                                              value=ls.get("reanalysis_tolerance_mz", 0.01),
                                              min=0, step=0.001, style={"width": "50%"}),
                                    html.H5("Adductフィルター", style={"marginTop": "10px"}),
                                    dbc.Checklist(
                                        id="reanalysis_adduct_filter",
                                        options=[
                                            {"label": "+H", "value": "+H"},
                                            {"label": "+Na", "value": "+Na"},
                                            {"label": "+NH4", "value": "+NH4"},
                                            {"label": "+K", "value": "+K"},
                                            {"label": "-H", "value": "-H"},
                                        ],
                                        value=["+H", "+Na", "+NH4", "+K"],
                                        inline=True,
                                    ),
                                ]),
                                # --- m/z キャリブレーション（再解析） ---
                                html.Hr(style={"marginTop": "15px"}),
                                html.H5("m/z キャリブレーション"),
                                dbc.Checkbox(
                                    id="reanalysis_calibration_use_previous",
                                    label="前回の解析の回帰式でキャリブレーション",
                                    value=ls.get("reanalysis_calibration_use_previous", False),
                                ),
                                html.Div(
                                    id="reanalysis_calibration_info",
                                    style={"display": "none"},
                                    children=[
                                        html.Div(
                                            id="reanalysis_calibration_details",
                                            style={
                                                "background": "#f0f9f0",
                                                "padding": "10px",
                                                "borderRadius": "5px",
                                                "marginTop": "5px",
                                                "fontSize": "13px",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ]),
                ]),
                # アノテーションファイル（オプション）
                html.Div(className="param-group", style={"marginTop": "15px"}, children=[
                    html.H5("アノテーションファイル (オプション)"),
                    html.Div(
                        style={"display": "flex", "gap": "5px"},
                        children=[
                            dbc.Input(id="reanalysis_annotation_path",
                                      value=ls.get("reanalysis_annotation_path", ""),
                                      placeholder="アノテーションファイルのパス"),
                            dbc.Button("参照...", id="browse_reanalysis_annotation",
                                       size="sm", color="secondary"),
                        ],
                    ),
                    html.Small(
                        id="reanalysis_annotation_path_path_hint", children="",
                        style={"color": "#6c757d", "fontSize": "0.75rem",
                               "marginTop": "2px", "display": "block"},
                    ),
                    dbc.FormText(".xlsx (DESI) / .csv (TIMS)"),
                ]),
                # 再解析 詳細設定
                html.Details([
                    html.Summary(
                        "🎛 詳細設定（p値閾値・log2FC閾値）",
                        style={"cursor": "pointer", "color": "#666", "fontSize": "13px", "marginTop": "10px"},
                    ),
                    html.Div(
                        style={"background": "#f8f9fa", "padding": "15px", "borderRadius": "5px", "marginTop": "5px"},
                        children=[
                            dbc.Row([
                                dbc.Col(width=6, children=[
                                    dbc.Label("p値閾値"),
                                    dbc.Input(id="reanalysis_p_thresh", type="number",
                                              value=ls.get("reanalysis_p_thresh", 0.05),
                                              min=0, max=1, step=0.01),
                                ]),
                                dbc.Col(width=6, children=[
                                    dbc.Label("log2FC閾値"),
                                    dbc.Input(id="reanalysis_logfc_thresh", type="number",
                                              value=ls.get("reanalysis_logfc_thresh", 0.25),
                                              min=0, step=0.05),
                                ]),
                            ]),
                        ],
                    ),
                ]),
            ],
        ),
        html.Hr(),

        # 出力設定
        html.H5(["📁 出力設定"]),
        dbc.Row([
            dbc.Col(width=4, children=[
                html.H6(["出力フォルダー", help_badge("output_subfolder")]),
                dbc.Input(
                    id="output_subfolder",
                    value=datetime.now().strftime("Analysis_%Y%m%d_%H%M%S"),
                    placeholder="例: Analysis_20260109",
                ),
            ]),
            dbc.Col(width=4, children=[
                html.H6("出力先"),
                dbc.Input(id="output_dir", value=ls.get("output_dir", str(APP_BASE_DIR))),
            ]),
            dbc.Col(width=4, children=[
                dbc.Button("参照...", id="browse_output", size="sm", color="secondary",
                           style={"marginTop": "25px"}),
            ]),
        ]),
        dbc.FormText("出力先の下にサブフォルダーとして作成されます"),
        html.Span(id="output_dir_badge", children="", style={"fontSize": "0.8rem"}),
        html.Small(
            id="output_dir_path_hint", children="",
            style={"color": "#6c757d", "fontSize": "0.75rem",
                   "marginTop": "2px", "display": "block"},
        ),
        html.Hr(),

        # プリフライトバリデーション結果
        html.Div(id="validation_summary", children="", style={"display": "none"}),

        # 実行ボタンエリア
        _create_run_button(),
    ])


def _create_run_button():
    return html.Div([
        html.Div(
            className="run-section",
            style={"marginTop": "20px", "display": "flex", "alignItems": "center", "gap": "20px"},
            children=[
                html.Div(
                    style={"flex": "0 0 auto"},
                    children=[
                        dbc.Button(
                            ["▶ 解析実行"], id="run_analysis",
                            size="lg", color="primary",
                            style={"padding": "15px 50px", "fontSize": "1.2rem"},
                        ),
                    ],
                ),
                html.Div(
                    id="stop_button_container",
                    style={"flex": "0 0 auto", "display": "none"},
                    children=[
                        dbc.Button(
                            ["⏹ 実行停止"], id="stop_analysis",
                            size="lg", color="danger",
                            style={"padding": "15px 30px", "fontSize": "1.2rem"},
                        ),
                    ],
                ),
                html.Div(
                    id="progress_container",
                    style={"flex": "1", "display": "none"},
                    children=[
                        html.Div(
                            style={"display": "flex", "justifyContent": "space-between", "alignItems": "center"},
                            children=[
                                html.H6(style={"margin": "0"}, children="解析進捗"),
                                html.Span(
                                    id="section_progress_text",
                                    style={"fontWeight": "bold", "fontSize": "0.9rem"},
                                    children="0/0 セクション",
                                ),
                            ],
                        ),
                        dbc.Progress(
                            id="analysis_progress_bar",
                            value=0, striped=True, animated=True,
                            style={"height": "25px", "marginTop": "5px"},
                        ),
                    ],
                ),
            ],
        ),

        # 進捗ログ表示
        html.Div(
            id="log_container",
            className="progress-container",
            style={"marginTop": "20px", "display": "none"},
            children=[
                html.H5("⏳ 解析中...", id="log_header"),
                # ログフィルタコントロール
                dbc.Row(className="mb-2 g-2", children=[
                    dbc.Col(width=4, children=[
                        dbc.Input(
                            id="log_search_input",
                            placeholder="ログ検索...",
                            size="sm",
                            debounce=True,
                        ),
                    ]),
                    dbc.Col(width=3, children=[
                        dcc.Dropdown(
                            id="log_level_filter",
                            options=[
                                {"label": "すべて", "value": "all"},
                                {"label": "Error", "value": "error"},
                                {"label": "Warning", "value": "warning"},
                            ],
                            value="all",
                            clearable=False,
                            style={"fontSize": "0.85rem"},
                        ),
                    ]),
                    dbc.Col(width=3, children=[
                        dcc.Dropdown(
                            id="log_lines_count",
                            options=[
                                {"label": "50行", "value": 50},
                                {"label": "100行", "value": 100},
                                {"label": "200行", "value": 200},
                                {"label": "全行", "value": 0},
                            ],
                            value=50,
                            clearable=False,
                            style={"fontSize": "0.85rem"},
                        ),
                    ]),
                ]),
                # ログ出力（html.Div に変更: styled html.Span をchildren に受ける）
                html.Div(
                    id="analysis_log",
                    className="progress-log",
                    style={
                        "maxHeight": "400px",
                        "overflowY": "auto",
                        "fontFamily": "monospace",
                        "fontSize": "0.8rem",
                        "backgroundColor": "#1e1e1e",
                        "color": "#d4d4d4",
                        "padding": "10px",
                        "borderRadius": "4px",
                    },
                    children="",
                ),
            ],
        ),
    ])
