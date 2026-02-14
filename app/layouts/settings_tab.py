# =============================================================================
# MSI Analysis Application - Settings Tab UI
# 解析設定タブUI
# =============================================================================

from datetime import datetime

from dash import html, dcc
import dash_bootstrap_components as dbc

from app.config import DEFAULT_DESI_DATA_FOLDER, APP_BASE_DIR


def create_settings_tab():
    return html.Div(className="card", style={"marginTop": "15px"}, children=[
        # UMAP解析設定（DESI/TIMS共通）
        html.Div(
            id="umap_settings_panel",
            children=[
                html.H4(className="card-title", children=["📊 UMAP解析設定"]),
                dbc.Row([
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("データフォルダ・サンプル選択"),
                            html.Div(
                                style={"display": "flex", "gap": "5px", "marginBottom": "10px"},
                                children=[
                                    dbc.Input(id="data_folder", value=DEFAULT_DESI_DATA_FOLDER,
                                              placeholder="データフォルダのパス"),
                                    dbc.Button("参照...", id="browse_folder", size="sm", color="secondary"),
                                ],
                            ),
                            html.Div(id="sample_selector"),
                            dbc.FormText("チェックを入れたサンプルが解析対象になります"),
                        ]),
                    ]),
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("MRMファイル (オプション)"),
                            dbc.Input(id="mrm_path", placeholder="MRM.xlsx のパス"),
                            dbc.Button("参照...", id="browse_mrm", size="sm", color="secondary",
                                       style={"marginTop": "5px"}),
                        ]),
                        # TIMS イオンモード設定（TIMS選択時のみ表示）
                        html.Div(
                            id="tims_ion_settings",
                            style={"display": "none", "marginTop": "15px"},
                            children=[
                                html.Div(className="param-group", children=[
                                    html.H5("イオンモード"),
                                    dbc.RadioItems(
                                        id="ion_mode",
                                        options=[
                                            {"label": "Positive", "value": "Positive"},
                                            {"label": "Negative", "value": "Negative"},
                                        ],
                                        value="Positive", inline=True,
                                    ),
                                    html.H5("m/z許容誤差", style={"marginTop": "10px"}),
                                    dbc.Input(id="tolerance_mz", type="number",
                                              value=0.01, min=0, step=0.001,
                                              style={"width": "50%"}),
                                    html.H5("Adductフィルター", style={"marginTop": "10px"}),
                                    dbc.Checklist(
                                        id="adduct_filter",
                                        options=[
                                            {"label": "+H", "value": "+H"},
                                            {"label": "+Na", "value": "+Na"},
                                            {"label": "+NH4", "value": "+NH4"},
                                            {"label": "-H", "value": "-H"},
                                        ],
                                        value=["+H", "+Na", "+NH4"],
                                        inline=True,
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
                                    dbc.Label("p値閾値"),
                                    dbc.Input(id="p_thresh", type="number",
                                              value=0.05, min=0, max=1, step=0.01),
                                ]),
                                dbc.Col(width=6, children=[
                                    dbc.Label("log2FC閾値"),
                                    dbc.Input(id="logfc_thresh", type="number",
                                              value=0.10, min=0, step=0.05),
                                ]),
                            ]),
                        ],
                    ),
                ]),

                # RDS途中再開
                dbc.Row(className="mt-3", children=[
                    dbc.Col(width=12, children=[
                        html.Div(className="param-group", children=[
                            html.H5("RDSファイル"),
                            dbc.Checkbox(id="resume_rds", label="途中再開 (RDSから)", value=False),
                            html.Div(
                                id="resume_rds_panel",
                                style={"display": "none", "marginTop": "10px"},
                                children=[
                                    dbc.Input(id="rds_folder", placeholder="RDSファイルが入っているフォルダ"),
                                    dbc.Button("参照...", id="browse_rds_folder", size="sm", color="secondary",
                                               style={"marginTop": "5px"}),
                                    html.Div(style={"marginTop": "10px"}, children=[
                                        html.H6("RDSファイル選択"),
                                        html.Div(id="rds_file_selector"),
                                        dbc.FormText("チェックを入れたRDSファイルを使用します"),
                                    ]),
                                ],
                            ),
                        ]),
                    ]),
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
                                    dbc.Input(id="reanalysis_data_folder", value=DEFAULT_DESI_DATA_FOLDER,
                                              placeholder="データフォルダのパス"),
                                    dbc.Button("参照...", id="browse_reanalysis_folder",
                                               size="sm", color="secondary"),
                                ],
                            ),
                            html.Div(id="sample_selector_reanalysis"),
                            dbc.FormText("チェックを入れたサンプルが再解析対象になります"),
                        ]),
                    ]),
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("RDSファイル"),
                            dbc.Input(id="rds_path", placeholder="解析済みRDSファイルのパス"),
                            dbc.Button("参照...", id="browse_rds", size="sm", color="secondary",
                                       style={"marginTop": "5px"}),
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
                                        value="Positive", inline=True,
                                    ),
                                    html.H5("m/z許容誤差", style={"marginTop": "10px"}),
                                    dbc.Input(id="reanalysis_tolerance_mz", type="number",
                                              value=0.01, min=0, step=0.001, style={"width": "50%"}),
                                    html.H5("Adductフィルター", style={"marginTop": "10px"}),
                                    dbc.Checklist(
                                        id="reanalysis_adduct_filter",
                                        options=[
                                            {"label": "+H", "value": "+H"},
                                            {"label": "+Na", "value": "+Na"},
                                            {"label": "+NH4", "value": "+NH4"},
                                            {"label": "-H", "value": "-H"},
                                        ],
                                        value=["+H", "+Na", "+NH4"],
                                        inline=True,
                                    ),
                                ]),
                            ],
                        ),
                    ]),
                ]),
                dbc.Row([
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("フィルタモード"),
                            dbc.RadioItems(
                                id="filter_mode",
                                options=[
                                    {"label": "除外 (exclude)", "value": "exclude"},
                                    {"label": "抽出 (keep)", "value": "keep"},
                                ],
                                value="exclude", inline=True,
                            ),
                        ]),
                    ]),
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("対象クラスタ"),
                            dbc.Input(id="target_clusters", placeholder="例: 0, 1, 5, 7", value=""),
                            dbc.FormText("カンマ区切りでクラスタ番号を入力"),
                        ]),
                    ]),
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
                                              value=0.05, min=0, max=1, step=0.01),
                                ]),
                                dbc.Col(width=6, children=[
                                    dbc.Label("log2FC閾値"),
                                    dbc.Input(id="reanalysis_logfc_thresh", type="number",
                                              value=0.10, min=0, step=0.05),
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
                html.H6("出力フォルダー"),
                dbc.Input(
                    id="output_subfolder",
                    value=datetime.now().strftime("Analysis_%Y%m%d_%H%M%S"),
                    placeholder="例: Analysis_20260109",
                ),
            ]),
            dbc.Col(width=4, children=[
                html.H6("出力先"),
                dbc.Input(id="output_dir", value=str(APP_BASE_DIR)),
            ]),
            dbc.Col(width=4, children=[
                dbc.Button("参照...", id="browse_output", size="sm", color="secondary",
                           style={"marginTop": "25px"}),
            ]),
        ]),
        dbc.FormText("出力先の下にサブフォルダーとして作成されます"),
        html.Hr(),

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
                html.H5("⏳ 解析中..."),
                html.Pre(
                    id="analysis_log",
                    className="progress-log",
                    children="",
                ),
            ],
        ),
    ])
