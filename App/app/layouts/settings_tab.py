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
from app.layouts.data_management_subtab import create_data_management_subtab


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
                children=create_data_management_subtab(),
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
                # --- 手法別の「標準フロー」推奨バナー（常時表示・選択手法のみ） ---
                #     表示切替は file_handlers.py の toggle_settings_panels で制御。
                #     TIMS選択時=TIMS用のみ / DESI選択時=DESI用のみ（両方同時には出さない）。
                html.Div(
                    id="tims_recommended_banner",
                    style={"display": "none"},
                    children=[dbc.Alert(color="info", className="mb-3", children=[
                        html.H6("💡 標準の使い方（TIMS × SCiLS RMS）",
                                className="alert-heading mb-2"),
                        html.Ol(className="mb-1", children=[
                            html.Li("TIMS データを SCiLS RMS で出力"),
                            html.Li(["正規化 ", html.B("「OFF」"), " ＋ 変換 ", html.B("「log1p」"),
                                     "（RMS×TIC の二重正規化を回避。TIMS では既定でこの設定）"]),
                            html.Li("サンプル数と『解析シナリオ』で自動分岐：既定=無補正PCA／"
                                    "連続切片→RPCA統合／複数サンプル→Harmony・RPCA"),
                            html.Li("UMAP・クラスタリング"),
                        ]),
                        html.Small("※ 段階比較が目的なら未補正(PCA)も併用／各条件1切片は交絡に注意。",
                                   className="text-muted"),
                        html.Br(),
                        html.Small(["詳細は ",
                                    html.A("説明書の『標準フロー』",
                                           href="/help/analysis#standard-flow", target="_blank"),
                                    " を参照。"], className="text-muted"),
                    ])],
                ),
                html.Div(
                    id="desi_recommended_banner",
                    style={"display": "none"},
                    children=[dbc.Alert(color="info", className="mb-3", children=[
                        html.H6("💡 標準の使い方（DESI・生データ）",
                                className="alert-heading mb-2"),
                        html.Ol(className="mb-1", children=[
                            html.Li("DESI データ（生データ）を入力"),
                            html.Li(["正規化 ", html.B("「ON」"),
                                     "（LogNormalize＝TIC正規化＋log。DESI では既定でこの設定）"]),
                            html.Li("サンプル数で自動分岐：1サンプル→PCAのみ／複数→Harmony・RPCA"
                                    "（両方算出。下の『クラスタソース』で使用手法を選択。"
                                    "ROIモードで各ROIを別サンプル化すると複数統合）"),
                            html.Li("UMAP・クラスタリング"),
                        ]),
                        html.Small("※ 段階比較が目的なら未補正(PCA)も併用／各条件1切片は交絡に注意。",
                                   className="text-muted"),
                        html.Br(),
                        html.Small(["詳細は ",
                                    html.A("説明書の『標準フロー』",
                                           href="/help/analysis#standard-flow", target="_blank"),
                                    " を参照。"], className="text-muted"),
                    ])],
                ),
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
                            # --- DESI ROI 設定 (TIMS の annotation_selector と同じ ---
                            #     pattern-matching 構造。データフォルダとサンプルから
                            #     ROI 候補を自動列挙、チェックボックスで選択。
                            #     DESI モード時のみ意味あり (callback で非表示制御)。
                            html.Div(
                                style={"marginTop": "10px"},
                                children=[
                                    html.Hr(className="my-2"),
                                    dbc.Switch(
                                        id="desi_use_roi_as_sample",
                                        label="ROI 列があれば各 ROI を別サンプルとして解析",
                                        value=ls.get("desi_use_roi_as_sample", False),
                                        className="mb-1",
                                    ),
                                    html.Div(id="desi_roi_selector",
                                             className="mt-1"),
                                    dcc.Store(id="desi_roi_filter_store",
                                              data=None),
                                    dbc.FormText(
                                        "Switch ON でチェック ROI が「別サンプル」と"
                                        "して統合解析されます (Harmony/RPCA)。"
                                        "OFF または ROI 列なしの場合はファイル全体を"
                                        " 1 サンプル扱い (従来挙動)。",
                                        className="text-muted small",
                                    ),
                                ],
                            ),
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
                        # 正規化設定（DESI/TIMS共通・UMAP解析時のみ表示）
                        html.Div(className="param-group", style={"marginTop": "15px"}, children=[
                            html.H5(["正規化 (LogNormalize)", help_badge("normalize_input")]),
                            dbc.RadioItems(
                                id="normalize_input",
                                options=[
                                    {"label": "ON（LogNormalize を実行）", "value": "ON"},
                                    {"label": "OFF（正規化済み入力: SCiLS RMS 等）", "value": "OFF"},
                                ],
                                value=ls.get("normalize_input", "OFF" if ls.get("analysis_method_tims") == "tims_v8" else "ON"),
                            ),
                            html.Div(style={"marginTop": "8px"}, children=[
                                html.Small("OFF時の変換 (NORM_MODE)", className="fw-bold"),
                                dbc.Select(
                                    id="norm_mode",
                                    options=[
                                        {"label": "log1p（log変換・推奨）", "value": "log1p"},
                                        {"label": "sqrt（平方根）", "value": "sqrt"},
                                        {"label": "none（変換なし・生RMS）", "value": "none"},
                                    ],
                                    value=ls.get("norm_mode", "log1p"),
                                    style={"width": "70%"},
                                ),
                            ]),
                            dbc.FormText(
                                "TIMS(SCiLS RMS等で正規化済み)は既定OFF＝二重正規化を回避。"
                                "DESI(生データ)は既定ON。解析法に応じて自動切替（手動変更可）。",
                                className="text-muted small",
                            ),
                            html.Details([
                                html.Summary(
                                    "📚 正規化の考え方（RMS／二重正規化）",
                                    style={"cursor": "pointer", "fontWeight": "600",
                                           "fontSize": "0.85rem", "color": "#495057",
                                           "marginTop": "6px"},
                                ),
                                html.Div(
                                    className="text-muted small",
                                    style={"marginTop": "6px", "paddingLeft": "10px",
                                           "borderLeft": "3px solid #dee2e6"},
                                    children=[
                                        html.P(
                                            "RMS等で正規化済みの入力（SCiLS RMS など）は"
                                            "「正規化 OFF」(INPUT_NORMALIZED=TRUE)＋NORM_MODE=log1p "
                                            "が正しい設定です。",
                                            className="mb-1",
                                        ),
                                        html.Ul(className="mb-0", children=[
                                            html.Li(
                                                "「正規化 ON」＝LogNormalize＝各スポットを総量(TIC)で"
                                                "割る＋log。RMS済みに ON すると RMS×TIC の二重正規化です。"),
                                            html.Li(
                                                "RMS が消すのは「全体強度（明るさ）」のみ。m/z個別の差や"
                                                "ドリフト等の構造的バッチは残ります（RMS はバッチ補正ではない）。"),
                                            html.Li(
                                                "UMAP には『RMS＋log1p』を使用。生データの直入れは避けてください。"),
                                        ]),
                                    ],
                                ),
                            ], style={"marginTop": "4px"}),
                        ]),
                        # TIMS イオンモード設定（TIMS選択時のみ表示）
                        html.Div(
                            id="tims_ion_settings",
                            style={"display": "none", "marginTop": "15px"},
                            children=[
                                # 解析シナリオ（切片アノテーションの意味）→ 補正方法を自動設定。
                                # 値は analysis_callbacks で ANNOTATION_ROLE/BATCH_VAR/
                                # ALLOW_CONDITION_CORRECTION に変換し analysis_runner で注入。
                                html.Div(className="param-group", children=[
                                    html.H5(["解析シナリオ（切片アノテーションの意味）",
                                             help_badge("tims_scenario")]),
                                    dbc.Select(
                                        id="tims_scenario",
                                        options=[
                                            {"label": "同一切片の中のクラスタを見る（1切片・部分構造）",
                                             "value": "within_slice"},
                                            {"label": "群比較：条件ごとに別アノテーション（例 Ctrl vs KO）",
                                             "value": "condition_compare"},
                                            {"label": "連続切片を技術反復としてまとめる（同一個体の連続切片）",
                                             "value": "serial_section"},
                                            {"label": "切片間の測定差(バッチ)を補正【非推奨・過補正注意】",
                                             "value": "batch_correct"},
                                        ],
                                        value=ls.get("tims_scenario", "within_slice"),
                                    ),
                                    dbc.FormText(
                                        "選んだシナリオに応じて補正方法を自動設定（既定=無補正PCA）。"
                                        "連続切片→RPCA統合／バッチ補正→Harmony。R既定値は変更しません。",
                                        className="text-muted small",
                                    ),
                                ]),
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
                                    html.H6(["クラスタソース", help_badge("cluster_source")], style={"marginTop": "5px"}),
                                    dbc.RadioItems(
                                        id="cluster_source",
                                        options=[
                                            {"label": "Harmony/PCA", "value": "harmony"},
                                            {"label": "RPCA", "value": "rpca"},
                                        ],
                                        value=ls.get("cluster_source", "harmony"),
                                        inline=True,
                                    ),
                                    html.Details([
                                        html.Summary(
                                            "📚 補正手法（Harmony/RPCA）と交絡の注意",
                                            style={"cursor": "pointer", "fontWeight": "600",
                                                   "fontSize": "0.85rem", "color": "#495057",
                                                   "marginTop": "6px"},
                                        ),
                                        html.Div(
                                            className="text-muted small",
                                            style={"marginTop": "6px", "paddingLeft": "10px",
                                                   "borderLeft": "3px solid #dee2e6"},
                                            children=[
                                                html.P(
                                                    "Harmony/RPCA は段階をまたぐ『共通性（共有構造）』"
                                                    "を見るための統合です。",
                                                    className="mb-1",
                                                ),
                                                html.Ul(className="mb-0", children=[
                                                    html.Li(
                                                        "各条件が1切片のみ（バッチ=条件が交絡）の場合、"
                                                        "補正は技術差と一緒に生物差も除去します（過補正）。"),
                                                    html.Li(
                                                        "補正強度は概ね Harmony＞RPCA（パラメータ依存）。"
                                                        "ただし強度差は技術差と生物差の『分離器』ではありません。"),
                                                    html.Li(
                                                        "段階差の比較が目的なら、未補正(PCA)の結果も必ず併用を。"),
                                                ]),
                                            ],
                                        ),
                                    ], style={"marginTop": "4px"}),
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
                                # 再解析の解析シナリオ。既定は初回(tims_scenario)を引き継ぐ。
                                html.Div(className="param-group", children=[
                                    html.H5(["解析シナリオ（切片アノテーションの意味）",
                                             help_badge("reanalysis_tims_scenario")]),
                                    dbc.Select(
                                        id="reanalysis_tims_scenario",
                                        options=[
                                            {"label": "同一切片の中のクラスタを見る（1切片・部分構造）",
                                             "value": "within_slice"},
                                            {"label": "群比較：条件ごとに別アノテーション（例 Ctrl vs KO）",
                                             "value": "condition_compare"},
                                            {"label": "連続切片を技術反復としてまとめる（同一個体の連続切片）",
                                             "value": "serial_section"},
                                            {"label": "切片間の測定差(バッチ)を補正【非推奨・過補正注意】",
                                             "value": "batch_correct"},
                                        ],
                                        value=ls.get("reanalysis_tims_scenario",
                                                     ls.get("tims_scenario", "within_slice")),
                                    ),
                                    dbc.FormText(
                                        "既定は初回解析のシナリオを引き継ぎます（変更可）。",
                                        className="text-muted small",
                                    ),
                                ]),
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
                                # --- 正規化（再解析・TIMSはRMS正規化済みのため既定OFF=二重回避） ---
                                html.Div(className="param-group", style={"marginTop": "15px"}, children=[
                                    html.H5("正規化 (LogNormalize)"),
                                    dbc.RadioItems(
                                        id="normalize_input_reanalysis",
                                        options=[
                                            {"label": "ON（LogNormalize を実行）", "value": "ON"},
                                            {"label": "OFF（正規化済み入力: SCiLS RMS 等）", "value": "OFF"},
                                        ],
                                        value=ls.get("normalize_input_reanalysis", "OFF"),
                                    ),
                                    html.Div(style={"marginTop": "8px"}, children=[
                                        html.Small("OFF時の変換 (NORM_MODE)", className="fw-bold"),
                                        dbc.Select(
                                            id="norm_mode_reanalysis",
                                            options=[
                                                {"label": "log1p（log変換・推奨）", "value": "log1p"},
                                                {"label": "sqrt（平方根）", "value": "sqrt"},
                                                {"label": "none（変換なし・生RMS）", "value": "none"},
                                            ],
                                            value=ls.get("norm_mode_reanalysis", "log1p"),
                                            style={"width": "70%"},
                                        ),
                                    ]),
                                    dbc.FormText(
                                        "TIMS再解析は元データがRMS正規化済みのため既定OFF（二重正規化を回避）。",
                                        className="text-muted small",
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

        # PreFlight 診断 ＋ UMAP ハイパーパラメータ
        _create_preflight_section(),

        # 実行ボタンエリア
        _create_run_button(),
    ])


def _create_preflight_section():
    """PreFlight 診断ボタン＋結果表示＋UMAP ハイパーパラメータ入力。

    - 診断: 完了済み解析の reduction RDS に run_diagnostics.R を実行し、
      推奨 dims / n.neighbors・許容域・推奨度・警告・交絡判定を表示（提案のみ）。
    - UMAP ハイパラ入力: 次回の「解析実行」へ注入される（analysis_runner 既存機構）。
    """
    metric_opts = [
        {"label": "cosine", "value": "cosine"},
        {"label": "euclidean", "value": "euclidean"},
    ]
    return html.Details([
        html.Summary(
            "🩺 PreFlight 診断 / UMAP ハイパーパラメータ",
            style={"cursor": "pointer", "fontWeight": "600",
                   "fontSize": "0.95rem", "marginBottom": "8px"},
        ),
        html.Div(className="param-group", children=[
            # UMAP ハイパーパラメータ入力（次回解析へ注入）
            dbc.Row([
                dbc.Col(width=3, children=[
                    dbc.Label("n.neighbors", html_for="umap_n_neighbors_input"),
                    dbc.Input(id="umap_n_neighbors_input", type="number",
                              value=30, min=2, max=100, step=1, size="sm"),
                    html.Small(
                        "近傍数。小=局所（細かいクラスタ）/大=大域（全体配置）を重視。"
                        "構造に効くため PreFlight が推奨を算出。",
                        className="text-muted d-block mt-1",
                    ),
                ]),
                dbc.Col(width=3, children=[
                    dbc.Label("min.dist", html_for="umap_min_dist_input"),
                    dbc.Input(id="umap_min_dist_input", type="number",
                              value=0.3, min=0, max=1, step=0.05, size="sm"),
                    html.Small(
                        "2D配置の密集度（見た目）のみ調整。近傍グラフ・クラスタは不変。"
                        "最適値はデータから決まらず既定0.3固定（好みで調整）。",
                        className="text-muted d-block mt-1",
                    ),
                ]),
                dbc.Col(width=3, children=[
                    dbc.Label("metric", html_for="umap_metric_input"),
                    dcc.Dropdown(id="umap_metric_input", options=metric_opts,
                                 value="cosine", clearable=False,
                                 style={"fontSize": "0.85rem"}),
                    html.Small(
                        "点間距離の測り方（cosine=方向/角度, euclidean=直線距離）。"
                        "高次元の PCA/Harmony 埋め込みは既定 cosine が無難。",
                        className="text-muted d-block mt-1",
                    ),
                ]),
                dbc.Col(width=3, children=[
                    dbc.Label("dims", html_for="umap_dims_input"),
                    dbc.Input(id="umap_dims_input", type="number",
                              value=30, min=2, max=50, step=1, size="sm"),
                    html.Small(
                        "UMAP に渡す reduction(PCA/Harmony) の次元数。多い=情報↑/ノイズ↑。"
                        "近傍の安定性に効くため PreFlight が推奨を算出。",
                        className="text-muted d-block mt-1",
                    ),
                ]),
            ]),
            dbc.FormText(
                "PreFlight 推奨値を参考に設定。これらは次回の「解析実行」に反映されます"
                "（dims の自動反映は DESI のみ。TIMS は別途）。"
                "自動推奨は dims・n.neighbors のみ。③反映は手法間の最大値を採用"
                "（全手法が安定する共通値）。min.dist と metric は自動推奨せず既定"
                "（0.3 / cosine）を使用します（手動変更可）。"
            ),
            html.Div(
                style={"marginTop": "10px", "display": "flex", "gap": "10px",
                       "alignItems": "center", "flexWrap": "wrap"},
                children=[
                    dbc.Button("① reduction のみ作成（診断用）", id="btn_make_reduction",
                               size="sm", color="primary", outline=True),
                    dbc.Button("② 🩺 PreFlight 診断を実行", id="btn_preflight_run",
                               size="sm", color="info"),
                    dbc.Button("③ 推奨値を入力欄へ反映", id="btn_preflight_apply",
                               size="sm", color="secondary", outline=True),
                    dbc.Button("④ 続きを実行（reduction再利用）", id="btn_run_downstream",
                               size="sm", color="primary"),
                    dbc.Button("📂 前回の診断を表示（再計算なし）", id="btn_preflight_load",
                               size="sm", color="secondary", outline=True),
                ],
            ),
            dbc.FormText([
                "推奨フロー: ",
                html.B("① reduction のみ作成"),
                "（UMAP 前で停止する軽量実行。フル解析は不要。進捗は下の"
                "「解析実行」と同じ進捗バーに表示）→ ",
                html.B("② PreFlight 診断"),
                "（生成された reduction RDS を診断）→ ",
                html.B("③ 推奨値を反映"),
                " → ",
                html.B("④ 続きを実行"),
                "（①の reduction を再利用し UMAP 以降のみ実行。重い再計算なし）。"
                "既に完了済み解析がある場合は ① を省略して ② から実行できます。"
                "解析中は ①・④ とも実行できません。"
                "④はUMAPハイパラ値で出力フォルダを自動命名（例 _nn15_md0p3_dim20）するため、"
                "上書きせず複数設定を並べて比較できます。",
            ]),
            html.Details([
                html.Summary(
                    "📚 PreFlight 診断結果の読み方（交絡判定）",
                    style={"cursor": "pointer", "fontWeight": "600",
                           "fontSize": "0.85rem", "color": "#495057",
                           "marginTop": "6px"},
                ),
                html.Div(
                    className="text-muted small",
                    style={"marginTop": "6px", "paddingLeft": "10px",
                           "borderLeft": "3px solid #dee2e6"},
                    children=[
                        html.Ul(className="mb-0", children=[
                            html.Li([
                                html.B("設計(交絡): "),
                                "not_identifiable＝技術差と生物差が分離不能（各条件が1バッチのみ）。"
                                "この場合、補正結果を『生物差』として読まないでください。",
                            ]),
                            html.Li([
                                html.B("推奨度(confidence): "),
                                "high＞medium＞low。推奨 dims / n.neighbors の信頼度です。",
                            ]),
                            html.Li([
                                html.B("iLISI: "),
                                "バッチ混合の程度（高いほどよく混ざる＝バッチ差が小さい）。",
                            ]),
                            html.Li(
                                "交絡を解くには、各条件に反復切片(≥2)、または全ランで測る"
                                "共有QC・内部標準が必要です。"),
                        ]),
                    ],
                ),
            ], style={"marginTop": "10px"}),
            dcc.Loading(html.Div(id="preflight_results_container",
                                 style={"marginTop": "10px"})),
            dcc.Store(id="preflight_store"),
            dcc.Interval(id="preflight_poll", interval=1500, disabled=True),
        ]),
    ], style={"marginTop": "15px", "marginBottom": "10px"})


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

        # 上書き確認モーダル（出力先に既存結果があるときだけ表示）＋ 保留モード保持
        dbc.Modal(
            id="overwrite_results_modal",
            centered=True,
            children=[
                dbc.ModalHeader(dbc.ModalTitle("⚠️ 既存の解析結果があります")),
                dbc.ModalBody([
                    html.Div(id="overwrite_results_detail", className="mb-2"),
                    html.P(
                        "続行すると同名ファイルは上書きされ、新旧の結果が混在する可能性があります。"
                        "本当に実行しますか？",
                        className="text-danger fw-bold mb-0",
                    ),
                ]),
                dbc.ModalFooter([
                    dbc.Button("キャンセル", id="cancel_overwrite_results",
                               color="secondary", outline=True),
                    dbc.Button("実行する", id="confirm_overwrite_results",
                               color="danger"),
                ]),
            ],
        ),
        dcc.Store(id="overwrite_pending_mode", data="run"),

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
