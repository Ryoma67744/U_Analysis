# =============================================================================
# MSI Analysis Application - Sidebar UI
# サイドバーUI
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc

from app.config import (
    DESI_V8_TEMPLATE_PATH, DESI_CLUSTER_FILTER_PATH,
    TIMS_V8_TEMPLATE_PATH, TIMS_CLUSTER_FILTER_PATH,
    DEFAULT_DESI_DATA_FOLDER, DEFAULT_ANNOTATION_FILE_PATH,
    DEFAULT_TIMS_DATA_FOLDER, DEFAULT_ANNOTATION_CSV_PATH,
    DESI_DATA_DIR, TIMS_DATA_DIR, APP_BASE_DIR,
)
from app.services.session_manager import load_last_settings



def _path_input_row(input_id: str, btn_id: str, value: str, placeholder: str):
    """テキスト入力 + 参照ボタンの行（パス名バッジ付き）"""
    from pathlib import PurePosixPath, PureWindowsPath
    basename = ""
    if value:
        try:
            basename = PureWindowsPath(value).name or PurePosixPath(value).name
        except Exception:
            basename = value.rstrip("/\\").rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    return html.Div(children=[
        html.Div(
            style={"display": "flex", "gap": "5px"},
            children=[
                dbc.Input(
                    id=input_id, value=value, placeholder=placeholder,
                    size="sm", style={"flex": "1"},
                ),
                dbc.Button(
                    "...", id=btn_id, size="sm", outline=True, color="secondary",
                ),
            ],
        ),
        html.Small(
            id=f"{input_id}_path_hint",
            children=f"📁 {basename}" if basename else "",
            style={"color": "#6c757d", "fontSize": "0.75rem",
                   "marginTop": "2px", "display": "block"},
        ),
    ])


def create_sidebar():
    ls = load_last_settings()  # 前回の設定を復元
    return html.Div(id="sidebar_content", className="sidebar", children=[
        # 解析手法選択（「解析設定」タブ以外では非表示）
        html.Div(
            id="method_selector_section",
            children=[
                html.H4(["🧪 解析手法"]),

                # DESI セクション
                html.Div(
                    "【DESI】",
                    style={
                        "color": "#667eea", "fontWeight": "bold",
                        "fontSize": "14px", "marginBottom": "5px",
                        "borderBottom": "1px solid #667eea",
                        "paddingBottom": "2px",
                    },
                ),
                dbc.RadioItems(
                    id="analysis_method",
                    options=[
                        {"label": html.Span("UMAP解析",
                                            style={"marginLeft": "15px"}),
                         "value": "desi_v8"},
                        {"label": html.Span("再解析",
                                            style={"marginLeft": "15px"}),
                         "value": "desi_cluster_filter"},
                    ],
                    value=ls.get("analysis_method", "desi_v8"),
                ),

                # TIMS セクション
                html.Div(
                    "【TIMS】",
                    style={
                        "color": "#f093fb", "fontWeight": "bold",
                        "fontSize": "14px", "marginTop": "10px",
                        "marginBottom": "5px",
                        "borderBottom": "1px solid #f093fb",
                        "paddingBottom": "2px",
                    },
                ),
                dbc.RadioItems(
                    id="analysis_method_tims",
                    options=[
                        {"label": html.Span("UMAP解析",
                                            style={"marginLeft": "15px"}),
                         "value": "tims_v8"},
                        {"label": html.Span("再解析",
                                            style={"marginLeft": "15px"}),
                         "value": "tims_cluster_filter"},
                    ],
                    value=ls.get("analysis_method_tims", None),
                ),
            ],
        ),

        # スクリプト設定（折りたたみ）
        _create_script_settings(ls),
        html.Hr(),

        # DESI初期設定
        _create_desi_settings(ls),

        # TIMS初期設定
        _create_tims_settings(ls),

        # 出力設定
        _create_output_settings(ls),
        html.Hr(),

        # プリセット / バックアップ / 変換ツール
        html.H4(["🗂 プリセット・バックアップ"]),
        html.Div(
            style={"display": "flex", "gap": "10px", "flexWrap": "wrap"},
            children=[
                dbc.Button(
                    ["📋 プリセット"], id="open_preset_modal",
                    size="sm", color="warning", outline=True,
                ),
                dbc.Button(
                    ["🗂 バックアップ"], id="open_backup_list_btn",
                    size="sm", color="secondary", outline=True,
                ),
                dbc.Button(
                    ["🔄 SCiLS 変換"], id="open_scils_converter_modal",
                    size="sm", color="primary", outline=True,
                ),
                dbc.Button(
                    ["🧹 RDS 軽量化"], id="open_rds_maintenance_modal",
                    size="sm", color="primary", outline=True,
                ),
                dbc.Button(
                    ["📦 Parquet 再パック"], id="open_parquet_maintenance_modal",
                    size="sm", color="primary", outline=True,
                ),
                dbc.Button(
                    ["⚙ 環境設定"], id="open_env_settings_modal",
                    size="sm", color="dark", outline=True,
                ),
            ],
        ),
    ])


def _create_script_settings(ls: dict = None):
    if ls is None:
        ls = {}
    return html.Details([
        html.Summary(
            "⚙ スクリプト設定",
            style={"cursor": "pointer", "color": "#666", "fontSize": "12px", "marginTop": "10px"},
        ),
        html.Div(
            style={"background": "#f8f9fa", "padding": "10px", "borderRadius": "5px", "marginTop": "5px"},
            children=[
                # DESI
                html.H5("DESI", style={"color": "#667eea", "marginBottom": "8px", "fontWeight": "bold",
                                        "borderBottom": "1px solid #667eea", "paddingBottom": "3px"}),
                html.H6("UMAP解析 (v8 Template)"),
                _path_input_row("desi_v8_script_path", "browse_desi_v8_script",
                                ls.get("desi_v8_script_path", str(DESI_V8_TEMPLATE_PATH)),
                                "DESI v8スクリプトのパス"),

                html.H6("再解析 (Cluster Filter)", style={"marginTop": "10px"}),
                _path_input_row("desi_cluster_filter_script_path", "browse_desi_cluster_script",
                                ls.get("desi_cluster_filter_script_path", str(DESI_CLUSTER_FILTER_PATH)),
                                "DESI Cluster Filterスクリプトのパス"),

                # TIMS
                html.H5("TIMS", style={"color": "#f093fb", "marginTop": "15px", "marginBottom": "8px",
                                        "fontWeight": "bold", "borderBottom": "1px solid #f093fb",
                                        "paddingBottom": "3px"}),
                html.H6("UMAP解析"),
                _path_input_row("tims_v8_script_path", "browse_tims_v8_script",
                                ls.get("tims_v8_script_path", str(TIMS_V8_TEMPLATE_PATH)),
                                "TIMS UMAPスクリプトのパス"),

                html.H6("再解析 (Cluster Filter)", style={"marginTop": "10px"}),
                _path_input_row("tims_cluster_filter_script_path", "browse_tims_cluster_script",
                                ls.get("tims_cluster_filter_script_path", str(TIMS_CLUSTER_FILTER_PATH)),
                                "TIMS Cluster Filterスクリプトのパス"),

                html.Div(
                    style={"marginTop": "10px", "textAlign": "right"},
                    children=[
                        dbc.Button("デフォルトに戻す", id="reset_script_paths",
                                   size="sm", outline=True, color="secondary"),
                    ],
                ),
            ],
        ),
    ])


def _create_desi_settings(ls: dict = None):
    if ls is None:
        ls = {}
    return html.Details([
        html.Summary(
            "⚙ DESI初期設定",
            style={"cursor": "pointer", "color": "#666", "fontSize": "12px"},
        ),
        html.Div(
            style={"background": "#f8f9fa", "padding": "10px", "borderRadius": "5px", "marginTop": "5px"},
            children=[
                html.H6("デフォルトデータフォルダ"),
                _path_input_row("default_desi_data_folder", "browse_default_desi_folder",
                                ls.get("default_desi_data_folder", DEFAULT_DESI_DATA_FOLDER),
                                "DESIデータフォルダ"),

                html.H6("アノテーションファイル (.xlsx)", style={"marginTop": "10px"}),
                _path_input_row("default_annotation_file", "browse_default_annotation_desi",
                                ls.get("default_annotation_file", ls.get("default_mrm_file", DEFAULT_ANNOTATION_FILE_PATH)),
                                "アノテーションファイルのパス"),

                html.H6("デフォルト出力先", style={"marginTop": "10px"}),
                _path_input_row("default_desi_output_dir", "browse_default_desi_output",
                                ls.get("default_desi_output_dir", str(DESI_DATA_DIR)),
                                "DESI出力先フォルダ"),

                html.Div(
                    style={"marginTop": "10px", "textAlign": "right"},
                    children=[
                        dbc.Button("リセット", id="reset_desi_defaults",
                                   size="sm", outline=True, color="secondary"),
                        dbc.Button("適用", id="apply_desi_defaults",
                                   size="sm", color="primary", style={"marginLeft": "5px"}),
                    ],
                ),
            ],
        ),
    ])


def _create_tims_settings(ls: dict = None):
    if ls is None:
        ls = {}
    return html.Details(
        style={"marginTop": "10px"},
        children=[
            html.Summary(
                "⚙ TIMS初期設定",
                style={"cursor": "pointer", "color": "#666", "fontSize": "12px"},
            ),
            html.Div(
                style={"background": "#f8f9fa", "padding": "10px", "borderRadius": "5px", "marginTop": "5px"},
                children=[
                    html.H6("デフォルトデータフォルダ"),
                    _path_input_row("default_tims_data_folder", "browse_default_tims_folder",
                                    ls.get("default_tims_data_folder", DEFAULT_TIMS_DATA_FOLDER),
                                    "TIMSデータフォルダ"),

                    html.H6("アノテーションファイル (.csv)", style={"marginTop": "10px"}),
                    _path_input_row("default_annotation_csv", "browse_default_annotation",
                                    ls.get("default_annotation_csv", DEFAULT_ANNOTATION_CSV_PATH),
                                    "アノテーションファイルのパス"),

                    html.H6("デフォルト出力先", style={"marginTop": "10px"}),
                    _path_input_row("default_tims_output_dir", "browse_default_tims_output",
                                    ls.get("default_tims_output_dir", str(TIMS_DATA_DIR)),
                                    "TIMS出力先フォルダ"),

                    html.Div(
                        style={"marginTop": "10px", "textAlign": "right"},
                        children=[
                            dbc.Button("リセット", id="reset_tims_defaults",
                                       size="sm", outline=True, color="secondary"),
                            dbc.Button("適用", id="apply_tims_defaults",
                                       size="sm", color="primary", style={"marginLeft": "5px"}),
                        ],
                    ),
                ],
            ),
        ],
    )


def _create_output_settings(ls: dict = None):
    if ls is None:
        ls = {}
    return html.Details(
        style={"marginTop": "10px"},
        children=[
            html.Summary(
                "📁 出力設定",
                style={"cursor": "pointer", "color": "#666", "fontSize": "12px"},
            ),
            html.Div(
                style={"background": "#f8f9fa", "padding": "10px", "borderRadius": "5px", "marginTop": "5px"},
                children=[
                    html.H6("デフォルト出力先"),
                    _path_input_row("default_output_dir", "browse_default_output",
                                    ls.get("default_output_dir", str(APP_BASE_DIR)),
                                    "出力先フォルダのパス"),
                    html.Div(
                        style={"marginTop": "10px", "textAlign": "right"},
                        children=[
                            dbc.Button("リセット", id="reset_output_defaults",
                                       size="sm", outline=True, color="secondary"),
                            dbc.Button("適用", id="apply_output_defaults",
                                       size="sm", color="primary", style={"marginLeft": "5px"}),
                        ],
                    ),
                ],
            ),
        ],
    )
