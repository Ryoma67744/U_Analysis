# =============================================================================
# MSI Analysis Application - Interactive Analysis Tab UI
# インタラクティブ解析タブUI
# =============================================================================

from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc

from app.config import EDIT_LOCK_HEARTBEAT_INTERVAL_SEC
from app.layouts.tooltips import help_badge
from app.services.session_manager import load_last_settings



def create_interactive_tab():
    _ls = load_last_settings()
    return html.Div(style={"marginTop": "15px"}, children=[
        # データソース選択
        html.Details(open=True, className="card", children=[
            html.Summary(
                html.H4("🔬 インタラクティブ解析", className="card-title",
                         style={"display": "inline", "cursor": "pointer"}),
            ),

            # プロジェクト / サブプロジェクト選択（主な選択手段）
            dbc.Row(className="mb-3", children=[
                dbc.Col(width=5, children=[
                    html.Div(id="interactive_project_row", children=[
                        dbc.Label("プロジェクト", className="small fw-bold"),
                        dcc.Dropdown(
                            id="interactive_project_select",
                            placeholder="プロジェクトを選択",
                            clearable=True,
                        ),
                    ]),
                ]),
                dbc.Col(width=5, children=[
                    dbc.Label("サブプロジェクト", className="small fw-bold"),
                    dcc.Dropdown(
                        id="interactive_sub_project_select",
                        placeholder="サブプロジェクトを選択",
                        clearable=True,
                    ),
                ]),
            ]),
            html.Hr(className="my-2"),

            dbc.Row([
                dbc.Col(width=6, children=[
                    html.Div(className="param-group", children=[
                        html.H5("結果フォルダ"),
                        html.Div(
                            style={"display": "flex", "gap": "5px"},
                            children=[
                                dbc.Input(id="interactive_result_folder", placeholder="結果フォルダのパス"),
                                dbc.Button("参照...", id="browse_interactive_result",
                                           size="sm", color="secondary"),
                                dbc.Button("スキャン", id="scan_result_folder",
                                           size="sm", color="info"),
                            ],
                        ),
                    ]),
                ]),
                dbc.Col(width=6, children=[
                    html.Div(className="param-group", style={"display": "none"}, children=[
                        html.H5("MSIデータフォルダ (オプション)"),
                        html.Div(
                            style={"display": "flex", "gap": "5px"},
                            children=[
                                dbc.Input(id="interactive_msi_folder", placeholder="MSIデータフォルダ"),
                                dbc.Button("参照...", id="browse_interactive_msi",
                                           size="sm", color="secondary"),
                            ],
                        ),
                    ]),
                ]),
            ]),
            html.Div(
                style={"display": "flex", "gap": "10px", "marginTop": "10px",
                       "alignItems": "center", "flexWrap": "wrap"},
                children=[
                    dbc.Button(
                        "データを読み込む", id="load_interactive_data",
                        color="primary",
                    ),
                    dbc.Button(
                        "キャンセル",
                        id="btn_cancel_load",
                        color="danger", outline=True, size="sm",
                        style={"display": "none"},
                        n_clicks=0,
                    ),
                    html.Div(
                        id="sap_btn_wrapper",
                        style={"display": "none"},
                        children=[
                            dbc.Button(
                                "プロジェクトとして保存",
                                id="open_save_as_project_modal",
                                color="success", outline=True,
                            ),
                        ],
                    ),
                ],
            ),
            # ロード進捗 UI（background_callback の running により表示制御）
            html.Div(
                id="load_progress_container",
                style={"display": "none", "marginTop": "10px"},
                children=[
                    dbc.Progress(
                        id="load_progress_bar",
                        value=0, max=100,
                        striped=True, animated=True,
                        style={"height": "20px"},
                    ),
                    html.Div(
                        id="load_progress_label",
                        className="text-muted small mt-1",
                        children="準備中...",
                    ),
                ],
            ),

            # --- イオンモード / m/z キャリブレーション（折りたたみパネル） ---
            html.Details(
                open=False,
                style={"marginTop": "10px"},
                children=[
                    html.Summary(
                        "イオンモード / m/z キャリブレーション",
                        style={"cursor": "pointer", "fontSize": "13px",
                               "color": "#555", "fontWeight": "600"},
                    ),
                    html.Div(
                        style={"background": "#f8f9fa", "padding": "12px",
                               "borderRadius": "5px", "marginTop": "5px"},
                        children=[
                            # キャリブレーション有効化チェック
                            dbc.Checkbox(
                                id="int_cal_enable",
                                label="m/z キャリブレーションを有効にする",
                                value=False,
                                className="mb-2",
                            ),
                            # 詳細パネル（enable時に表示）
                            html.Div(
                                id="int_cal_detail_panel",
                                style={"display": "none"},
                                children=[
                                    # PR-G3: UI ロック表示（他ユーザーが編集中の表示）
                                    html.Div(
                                        id="calibration_panel_lock_indicator",
                                        className="text-warning small mb-2",
                                        children="",
                                    ),
                                    # Row: イオンモード + 付加イオン + マトリクス
                                    dbc.Row(className="mb-2", children=[
                                        dbc.Col(width=3, children=[
                                            dbc.Label("イオンモード", className="small fw-bold"),
                                            dbc.RadioItems(
                                                id="int_cal_ion_mode",
                                                options=[
                                                    {"label": "Positive", "value": "Positive"},
                                                    {"label": "Negative", "value": "Negative"},
                                                ],
                                                value="Positive",
                                                inline=True,
                                                className="small",
                                            ),
                                        ]),
                                        dbc.Col(width=5, children=[
                                            dbc.Label("付加イオン", className="small fw-bold"),
                                            dbc.Checklist(
                                                id="int_cal_adduct_filter",
                                                options=[
                                                    {"label": "+H", "value": "+H"},
                                                    {"label": "+Na", "value": "+Na"},
                                                    {"label": "+NH4", "value": "+NH4"},
                                                    {"label": "+K", "value": "+K"},
                                                    {"label": "-H", "value": "-H"},
                                                ],
                                                value=["+H", "+Na", "+NH4", "+K"],
                                                inline=True,
                                                className="small",
                                            ),
                                        ]),
                                        dbc.Col(width=4, children=[
                                            dbc.Label("マトリクス種", className="small fw-bold"),
                                            dbc.Select(
                                                id="int_cal_matrix",
                                                options=[
                                                    {"label": "DHB", "value": "DHB"},
                                                    {"label": "CHCA", "value": "CHCA"},
                                                    {"label": "9-AA", "value": "9AA"},
                                                    {"label": "カスタム", "value": "custom"},
                                                ],
                                                value="DHB",
                                                className="form-select-sm",
                                            ),
                                        ]),
                                    ]),
                                    # キャリブレーションテーブル
                                    dbc.Label("リファレンス / 実測値 対応表", className="small fw-bold"),
                                    dash_table.DataTable(
                                        id="int_cal_table",
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
                                        data=[],
                                        row_selectable="multi",
                                        selected_rows=[],
                                        style_table={"overflowX": "auto", "maxHeight": "200px",
                                                     "overflowY": "auto"},
                                        style_cell={"fontSize": "12px", "padding": "4px 8px"},
                                        style_header={"fontWeight": "bold", "fontSize": "12px"},
                                    ),
                                    # ボタン行
                                    html.Div(
                                        className="d-flex gap-2 mt-2",
                                        children=[
                                            dbc.Button("行追加", id="int_cal_add_row",
                                                       size="sm", color="secondary", outline=True),
                                            dbc.Button("選択行削除", id="int_cal_delete_rows",
                                                       size="sm", color="danger", outline=True),
                                            dbc.Button("ピーク自動検出", id="int_cal_auto_detect",
                                                       size="sm", color="info", outline=True),
                                            dbc.Button("List保存", id="int_cal_save_list",
                                                       size="sm", color="secondary", outline=True),
                                        ],
                                    ),
                                    html.Div(id="int_cal_status_text",
                                             className="small text-muted mt-1"),
                                    # 詳細設定（折りたたみ）
                                    html.Details(
                                        className="mt-2",
                                        children=[
                                            html.Summary("詳細設定",
                                                         style={"fontSize": "12px", "cursor": "pointer"}),
                                            html.Div(style={"padding": "8px"}, children=[
                                                dbc.Row(className="mb-1", children=[
                                                    dbc.Col(width=4, children=[
                                                        dbc.Label("検索ウィンドウ (Da)", className="small"),
                                                        dbc.Input(id="int_cal_search_window",
                                                                  type="number", value=0.5,
                                                                  min=0.01, step=0.1, size="sm"),
                                                    ]),
                                                    dbc.Col(width=4, children=[
                                                        dbc.Label("最低マッチピーク数", className="small"),
                                                        dbc.Input(id="int_cal_min_peaks",
                                                                  type="number", value=2,
                                                                  min=1, step=1, size="sm"),
                                                    ]),
                                                    dbc.Col(width=4, children=[
                                                        dbc.Label("回帰モデル", className="small"),
                                                        dbc.Select(id="int_cal_regression_mode",
                                                                   options=[
                                                                       {"label": "Linear", "value": "linear"},
                                                                       {"label": "Poly2", "value": "poly2"},
                                                                       {"label": "Poly3", "value": "poly3"},
                                                                   ],
                                                                   value="poly3",
                                                                   className="form-select-sm"),
                                                    ]),
                                                ]),
                                            ]),
                                        ],
                                    ),
                                    html.Hr(className="my-2"),
                                    # アノテーションファイル（DESIのみ表示）
                                    html.Div(
                                        id="int_cal_annotation_section",
                                        style={"display": "none"},
                                        children=[
                                            dbc.Row(className="mb-2", children=[
                                                dbc.Col(width=12, children=[
                                                    dbc.Label("アノテーションファイル", className="small fw-bold"),
                                                    html.Div(
                                                        style={"display": "flex", "gap": "5px"},
                                                        children=[
                                                            dbc.Input(id="int_cal_annotation_path",
                                                                      placeholder="アノテーションファイル",
                                                                      size="sm"),
                                                            dbc.Button("参照...", id="browse_int_cal_annotation",
                                                                       size="sm", color="secondary"),
                                                        ],
                                                    ),
                                                ]),
                                            ]),
                                        ],
                                    ),
                                    # 適用ボタン
                                    dbc.Button(
                                        "キャリブレーション適用", id="int_cal_apply",
                                        color="warning", size="sm", className="mt-2",
                                    ),
                                ],
                            ),
                            # 適用結果メッセージ
                            html.Div(id="int_cal_apply_status",
                                     className="small mt-1"),
                        ],
                    ),
                ],
            ),

            # --- 再アノテーション（折りたたみパネル） ---
            html.Details(
                open=False,
                style={"marginTop": "10px"},
                children=[
                    html.Summary(
                        "再アノテーション (m/z → 化合物名 再照合)",
                        style={"cursor": "pointer", "fontSize": "13px",
                               "color": "#555", "fontWeight": "600"},
                    ),
                    html.Div(
                        style={"background": "#f8f9fa", "padding": "12px",
                               "borderRadius": "5px", "marginTop": "5px"},
                        children=[
                            # Row 1: アノテーションファイル + 参照ボタン
                            dbc.Row(className="mb-2", children=[
                                dbc.Col(width=12, children=[
                                    dbc.Label("アノテーションファイル",
                                              className="small fw-bold"),
                                    html.Div(
                                        style={"display": "flex", "gap": "5px"},
                                        children=[
                                            dbc.Input(
                                                id="reann_annotation_path",
                                                placeholder="アノテーションファイル (.csv / .xlsx)",
                                                size="sm",
                                            ),
                                            dbc.Button(
                                                "参照...",
                                                id="browse_reann_annotation",
                                                size="sm", color="secondary",
                                            ),
                                        ],
                                    ),
                                    html.Small(
                                        id="reann_annotation_path_path_hint",
                                        children="",
                                        style={"color": "#6c757d",
                                               "fontSize": "0.75rem",
                                               "marginTop": "2px",
                                               "display": "block"},
                                    ),
                                    dbc.FormText(
                                        "TIMS: TraceFinder/HMDB形式 CSV | "
                                        "DESI: MRM形式 Excel (.xlsx)"
                                    ),
                                ]),
                            ]),
                            # Row 2: イオンモード + 付加イオン + m/z許容誤差
                            dbc.Row(className="mb-2", children=[
                                dbc.Col(width=3, children=[
                                    dbc.Label("イオンモード",
                                              className="small fw-bold"),
                                    dbc.RadioItems(
                                        id="reann_ion_mode",
                                        options=[
                                            {"label": "Positive",
                                             "value": "Positive"},
                                            {"label": "Negative",
                                             "value": "Negative"},
                                        ],
                                        value="Positive",
                                        inline=True,
                                        className="small",
                                    ),
                                ]),
                                dbc.Col(width=5, children=[
                                    dbc.Label("付加イオン",
                                              className="small fw-bold"),
                                    dbc.Checklist(
                                        id="reann_adduct_filter",
                                        options=[
                                            {"label": "+H", "value": "+H"},
                                            {"label": "+Na", "value": "+Na"},
                                            {"label": "+NH4", "value": "+NH4"},
                                            {"label": "+K", "value": "+K"},
                                            {"label": "-H", "value": "-H"},
                                        ],
                                        value=["+H", "+Na", "+NH4", "+K"],
                                        inline=True,
                                        className="small",
                                    ),
                                ]),
                                dbc.Col(width=4, children=[
                                    dbc.Label("m/z 許容誤差 (Da)",
                                              className="small fw-bold"),
                                    dbc.Input(
                                        id="reann_tolerance",
                                        type="number",
                                        value=0.01,
                                        min=0, step=0.001,
                                        size="sm",
                                        style={"width": "120px"},
                                    ),
                                ]),
                            ]),
                            # Row 3: CSV上書きチェック + 実行ボタン + ステータス
                            dbc.Row(className="align-items-center", children=[
                                dbc.Col(width=5, children=[
                                    dbc.Checkbox(
                                        id="reann_overwrite_csv",
                                        label="markers_annotated.csv を上書き保存",
                                        value=False,
                                        className="small",
                                    ),
                                ]),
                                dbc.Col(width=3, children=[
                                    dbc.Button(
                                        "再アノテーション実行",
                                        id="reann_execute_btn",
                                        color="warning",
                                        size="sm",
                                    ),
                                ]),
                                dbc.Col(width=4, children=[
                                    html.Div(
                                        id="reann_status_text",
                                        className="small text-muted",
                                    ),
                                ]),
                            ]),
                        ],
                    ),
                ],
            ),

            html.Div(id="interactive_data_info", className="mt-2 text-muted"),
        ]),

        # 可視化エリア
        html.Div(
            id="interactive_viz_container",
            style={"display": "none"},
            children=[
                # 統合手法ヘッダーバー（結果エリア上部 — 折りたたみ可能）
                html.Div([
                    dbc.Button(
                        "解析手法 ▼",
                        id="toggle_integration_method",
                        color="light",
                        className="w-100 text-start fw-bold",
                        size="sm",
                    ),
                    dbc.Collapse(
                        html.Div(
                            style={"display": "flex", "flexDirection": "row",
                                   "alignItems": "center", "gap": "15px",
                                   "padding": "8px 0"},
                            children=[
                                dbc.RadioItems(
                                    id="interactive_integration_method",
                                    options=[],
                                    value=None,
                                    inline=True,
                                ),
                                help_badge("interactive_integration_method"),
                            ],
                        ),
                        id="integration_method_collapse",
                        is_open=True,
                    ),
                ], className="mb-2", id="integration_method_wrapper"),

                # 結果の読み方（差と共通性）— 折りたたみガイド
                html.Details([
                    html.Summary(
                        "📚 結果の読み方（UMAP/PCA・差と共通性）",
                        style={"cursor": "pointer", "fontWeight": "600",
                               "fontSize": "0.85rem", "color": "#495057"},
                    ),
                    html.Div(
                        className="text-muted small",
                        style={"marginTop": "6px", "paddingLeft": "10px",
                               "borderLeft": "3px solid #dee2e6"},
                        children=[
                            html.Ul(className="mb-0", children=[
                                html.Li(
                                    "UMAP は近傍構造の可視化です。クラスタ(共通性)の把握には有効ですが、"
                                    "島の間の距離＝差の大きさは定量的ではありません"
                                    "（見た目の分離を生物差の証拠にしない）。"),
                                html.Li(
                                    "未補正(PCA)＝『差』の面（技術＋生物が交絡）。"
                                    "Harmony/RPCA＝『共通性』の面（段階差は除去済み）。"),
                                html.Li(
                                    "解剖学的に同じ領域なのに色(クラスタ/発現)が違う場合は生物差の『候補』。"
                                    "確証には反復切片や直交検証が必要です。"),
                                html.Li(
                                    "差の定量は UMAP の幾何ではなく、PCA分散や統計検定(DE等)で行ってください。"),
                            ]),
                        ],
                    ),
                ], className="mb-2"),

                # アコーディオン（各セクション折りたたみ可能）
                # 初期表示は全セクション折りたたみ。ユーザーが必要な
                # セクションだけ展開することで、Plotly グラフ群の
                # レイアウト計算負荷を遅延させる。
                dbc.Accordion(
                    id="interactive_accordion",
                    always_open=True,
                    start_collapsed=True,
                    flush=True,
                    className="mt-3",
                    children=[
                        # --- エクスポート ---
                        dbc.AccordionItem(title="エクスポート", item_id="acc_export", className="accordion-export", children=[
                            dbc.Row(className="align-items-center", children=[
                                dbc.Col(width="auto", children=[
                                    dbc.Button(
                                        "📊 レポート出力 (.pptx)",
                                        id="btn_export_report",
                                        color="success", size="sm",
                                        n_clicks=0,
                                    ),
                                ]),
                                dbc.Col(width="auto", children=[
                                    html.Div(className="d-flex align-items-center gap-2", children=[
                                        dbc.Label("Top N:", className="small mb-0"),
                                        dbc.Input(
                                            id="input_export_top_n",
                                            type="number", min=1, max=20,
                                            step=1, value=5, size="sm",
                                            style={"width": "70px", "fontSize": "0.85rem"},
                                        ),
                                    ]),
                                ]),
                            ]),
                            # 出力対象手法セレクタ
                            dbc.Row(className="align-items-center mt-2", children=[
                                dbc.Col(width="auto", children=[
                                    dbc.Label("出力対象:", className="small mb-0"),
                                ]),
                                dbc.Col(children=[
                                    dbc.RadioItems(
                                        id="export_method_selector",
                                        options=[{"label": "All", "value": "all"}],
                                        value="all",
                                        inline=True,
                                        className="small",
                                    ),
                                ]),
                            ]),
                            # プログレスバー（生成中のみ表示）
                            html.Div(id="export_progress_container",
                                     style={"display": "none"}, children=[
                                dbc.Progress(id="export_progress_bar", value=0,
                                             max=100, striped=True, animated=True,
                                             className="mt-2",
                                             style={"height": "20px"}),
                                html.Div(id="export_progress_label",
                                         className="text-center small text-muted"),
                            ]),
                            html.Div(id="div_export_status", className="mt-1 text-muted",
                                     style={"fontSize": "0.85rem"}),

                            # --- データ出力 (UMAP cluster) ---
                            html.Hr(className="my-2"),
                            dbc.Row(className="g-2 align-items-center", children=[
                                dbc.Col(width="auto", children=[
                                    dbc.Button(
                                        "📥 データ出力 (UMAP cluster)",
                                        id="btn_export_data",
                                        color="primary",
                                        size="sm",
                                    ),
                                ]),
                                dbc.Col(
                                    width="auto",
                                    id="data_export_format_wrapper",
                                    style={"display": "none"},
                                    children=[
                                        html.Div(
                                            className="d-flex align-items-center gap-2",
                                            children=[
                                                dbc.Label("出力形式:", className="small mb-0"),
                                                dbc.Select(
                                                    id="data_export_format",
                                                    value="xlsx",
                                                    size="sm",
                                                    options=[
                                                        {"label": "Excel (.xlsx)", "value": "xlsx"},
                                                        {"label": "CSV (.csv)", "value": "csv"},
                                                        {"label": "Parquet (.parquet)", "value": "parquet"},
                                                    ],
                                                    style={"width": "180px"},
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ]),
                            html.Div(
                                id="div_data_export_status",
                                className="mt-1 text-muted",
                                style={"fontSize": "0.85rem"},
                            ),
                            # データ出力の進捗表示（出力中のみ表示）
                            html.Div(
                                id="data_export_progress_container",
                                style={"display": "none", "marginTop": "8px"},
                                children=[
                                    dbc.Progress(
                                        id="data_export_progress_bar",
                                        value=0, max=100,
                                        striped=True, animated=True,
                                        style={"height": "18px"},
                                    ),
                                    html.Div(
                                        id="data_export_progress_label",
                                        className="text-center small text-muted mt-1",
                                        children="",
                                    ),
                                ],
                            ),

                            # --- 軽量ビューア ---
                            html.Hr(className="my-2"),
                            dbc.Row(className="g-2 align-items-center", children=[
                                dbc.Col(width="auto", children=[
                                    dbc.Button(
                                        "🔗 軽量ビューアを開く（新タブ）",
                                        id="btn_open_lite_viewer",
                                        color="info", outline=True, size="sm",
                                        n_clicks=0,
                                    ),
                                ]),
                                dbc.Col(width="auto", children=[
                                    html.Span(
                                        "PPTX出力よりも高速・URL共有可能",
                                        className="small text-muted",
                                    ),
                                ]),
                            ]),
                        ]),

                        # --- クラスタ情報 ---
                        dbc.AccordionItem(title="クラスタ情報", item_id="acc_cluster", className="accordion-cluster", children=[
                            html.Pre(id="cluster_info_text",
                                     style={"fontSize": "0.85rem", "maxHeight": "120px",
                                            "overflowY": "auto"}),
                            # --- クラスタ名変更 ---
                            html.Hr(className="my-2"),
                            html.H6("✏️ クラスタ名変更", className="small fw-bold"),
                            html.Div(
                                id="cluster_rename_panel",
                                style={"maxHeight": "200px", "overflowY": "auto"},
                                children=[html.P("データ読み込み後に表示されます",
                                                 className="text-muted small")],
                            ),
                            html.Div(
                                style={"display": "flex", "gap": "8px", "marginTop": "6px"},
                                children=[
                                    dbc.Button("適用", id="cluster_rename_apply_btn",
                                               size="sm", color="primary"),
                                    dbc.Button("リセット", id="cluster_rename_reset_btn",
                                               size="sm", color="secondary", outline=True),
                                ],
                            ),
                            html.Div(id="cluster_rename_status",
                                     className="small text-muted mt-1"),
                            html.Hr(className="my-2"),
                            dbc.Row(className="mt-2", children=[
                                dbc.Col(width=6, children=[
                                    html.H5("クラスタ統計"),
                                    html.Div(
                                        id="cluster_stats_container",
                                        style={"maxHeight": "300px", "overflowY": "auto"},
                                        children=[
                                            dash_table.DataTable(
                                                id="cluster_stats_table",
                                                columns=[
                                                    {"name": "Cluster", "id": "Cluster"},
                                                    {"name": "Pixels", "id": "Pixels"},
                                                    {"name": "%", "id": "Percent"},
                                                ],
                                                data=[],
                                                row_selectable="single",
                                                style_table={"overflowX": "auto"},
                                                style_cell={"textAlign": "left", "padding": "8px",
                                                             "fontSize": "0.85rem"},
                                                style_header={"backgroundColor": "#f8f9fa",
                                                               "fontWeight": "600"},
                                                page_size=15,
                                            ),
                                        ],
                                    ),
                                ]),
                                dbc.Col(width=6, children=[
                                    html.H5("クラスタ比率"),
                                    dcc.Graph(
                                        id="cluster_proportion_chart",
                                        style={"height": "300px"},
                                        config={"displayModeBar": False},
                                    ),
                                ]),
                            ]),
                            html.Hr(),
                            html.H6("クラスタ別 Top 5 マーカー"),
                            html.Div(
                                id="cluster_top_markers_panel",
                                style={"maxHeight": "400px", "overflowY": "auto"},
                            ),
                        ]),

                        # --- UMAP プロット ---
                        dbc.AccordionItem(title="UMAP", item_id="acc_umap", className="accordion-umap", children=[
                            html.Div(className="d-flex justify-content-end gap-2", children=[
                                dbc.Button("📷 一括保存", id="btn_batch_save_umap", size="sm",
                                           color="outline-secondary",
                                           style={"fontSize": "0.75rem"}),
                                # ver3.10: 現在の UMAP をプロジェクトサムネに登録
                                dbc.Button("📌 サムネ登録", id="btn_set_thumbnail_umap",
                                           size="sm", color="outline-info",
                                           style={"fontSize": "0.75rem"},
                                           title="現在の UMAP プロット (統合 or per-sample 結合) をプロジェクトのサムネとして登録"),
                                dbc.Button("⤢", id="expand_umap_btn", size="sm", color="light",
                                           style={"fontSize": "1.2rem", "padding": "2px 8px", "lineHeight": "1"}),
                            ]),
                            dbc.Row(className="mt-2", children=[
                                dbc.Col(width=2, children=[
                                    dbc.Label(["表示", help_badge("umap_display_mode")]),
                                    dbc.RadioItems(
                                        id="umap_display_mode",
                                        options=[
                                            {"label": "統合", "value": "integrated"},
                                            {"label": "サンプル別", "value": "per_sample"},
                                        ],
                                        value="integrated", inline=True,
                                    ),
                                ]),
                                dbc.Col(width=2, children=[
                                    dbc.Label(["色分け", help_badge("umap_color_by")]),
                                    dbc.RadioItems(
                                        id="umap_color_by",
                                        options=[
                                            {"label": "Cluster", "value": "Cluster"},
                                            {"label": "Sample", "value": "Sample"},
                                        ],
                                        value="Cluster", inline=True,
                                    ),
                                ]),
                                dbc.Col(width=3, children=[
                                    dbc.Label(["ハイライト", help_badge("umap_highlight_cluster")]),
                                    dcc.Dropdown(
                                        id="umap_highlight_cluster",
                                        multi=True, placeholder="クラスタを選択",
                                    ),
                                ]),
                                dbc.Col(width=3, children=[
                                    dbc.Label(["除去", help_badge("umap_exclude_cluster")]),
                                    dcc.Dropdown(
                                        id="umap_exclude_cluster",
                                        multi=True,
                                        placeholder="除去するクラスタ",
                                    ),
                                ]),
                                dbc.Col(width=1, children=[
                                    dbc.Checkbox(id="umap_show_labels", label="ラベル", value=False),
                                ]),
                                dbc.Col(width=1, children=[
                                    dbc.Checkbox(id="umap_show_legend", label="凡例", value=True),
                                ]),
                            ]),
                            dbc.Row(className="mt-1", children=[
                                dbc.Col(width=2, children=[
                                    dbc.Label("マーカーサイズ", className="small mb-0"),
                                    dcc.Slider(
                                        id="umap_marker_size",
                                        min=1, max=10, step=1, value=2,
                                        marks={1: "1", 2: "2", 5: "5", 10: "10"},
                                        tooltip={"placement": "bottom", "always_visible": False},
                                    ),
                                ]),
                                dbc.Col(width=2, children=[
                                    dbc.Label("ラベルサイズ", className="small mb-0"),
                                    dcc.Slider(
                                        id="umap_label_size",
                                        min=6, max=24, step=1, value=14,
                                        marks={6: "6", 10: "10", 14: "14", 20: "20", 24: "24"},
                                        tooltip={"placement": "bottom", "always_visible": False},
                                    ),
                                ]),
                                dbc.Col(width=2, children=[
                                    dbc.Label("横並び", className="small mb-0"),
                                    dcc.Dropdown(
                                        id="umap_columns_per_row",
                                        options=[
                                            {"label": "自動", "value": 0},
                                            {"label": "1列", "value": 1},
                                            {"label": "2列", "value": 2},
                                            {"label": "3列", "value": 3},
                                            {"label": "4列", "value": 4},
                                            {"label": "5列", "value": 5},
                                            {"label": "6列", "value": 6},
                                            {"label": "7列", "value": 7},
                                            {"label": "8列", "value": 8},
                                        ],
                                        value=0, clearable=False,
                                        style={"fontSize": "12px"},
                                    ),
                                ]),
                            ]),
                            # マージ統合 切替コントロール（マージデータがある場合のみ表示）
                            html.Div(
                                id="umap_merge_controls_wrapper",
                                style={"display": "none"},  # 初期非表示、コールバックで制御
                                className="mt-1",
                                children=[
                                    dbc.Row(className="align-items-center", children=[
                                        dbc.Col(width=3, children=[
                                            dbc.Label("クラスタ表示", className="small mb-0 fw-bold"),
                                            dbc.RadioItems(
                                                id="umap_merge_toggle",
                                                options=[
                                                    {"label": "元のクラスタ", "value": "original"},
                                                    {"label": "マージ統合", "value": "merged"},
                                                ],
                                                value="original", inline=True,
                                                className="small",
                                            ),
                                        ]),
                                        dbc.Col(width=3, children=[
                                            dbc.Label("カラーモード", className="small mb-0 fw-bold"),
                                            dbc.RadioItems(
                                                id="umap_merge_color_mode",
                                                options=[
                                                    {"label": "親クラスタ濃淡", "value": "shade"},
                                                    {"label": "独立色", "value": "independent"},
                                                ],
                                                value="shade", inline=True,
                                                className="small",
                                            ),
                                        ]),
                                    ]),
                                ],
                            ),
                            html.Div(children=[
                                # UMAP側サンプル名変更コンテナ（グラフの上に配置）
                                html.Div(id="umap_name_controls_container"),
                                html.Div(id="umap_integrated_wrapper", children=[
                                    dcc.Loading(
                                        dcc.Graph(id="interactive_umap_plot",
                                                  style={"height": "450px"},
                                                  config={
                                                      "scrollZoom": True,
                                                      "edits": {"annotationPosition": True},
                                                      "toImageButtonOptions": {
                                                          "format": "png",
                                                          "filename": "UMAP_plot",
                                                          "scale": 3,
                                                      },
                                                  }),
                                    ),
                                ]),
                                # サンプル別 UMAP 表示コンテナ
                                html.Div(id="umap_per_sample_container"),
                            ]),
                            # --- 選択範囲のライブ統計 (P1: Loupe 風 即時集計) ---
                            html.Div([
                                html.Strong("選択範囲の統計", className="small d-block mb-1"),
                                html.Div(id="selection_summary_card"),
                            ], className="mt-2 border rounded p-2 bg-light"),
                        ]),

                        # --- Spatial Mapping ---
                        dbc.AccordionItem(title="Spatial Mapping", item_id="acc_spatial", className="accordion-spatial", children=[
                            html.Div(className="d-flex justify-content-end gap-2", children=[
                                dbc.Button("📷 一括保存", id="btn_batch_save_spatial", size="sm",
                                           color="outline-secondary",
                                           style={"fontSize": "0.75rem"}),
                                # ver3.10: 現在の Spatial をプロジェクトサムネに登録
                                dbc.Button("📌 サムネ登録", id="btn_set_thumbnail_spatial",
                                           size="sm", color="outline-info",
                                           style={"fontSize": "0.75rem"},
                                           title="現在の Spatial Mapping (横結合画像) をプロジェクトのサムネとして登録"),
                                dbc.Button("⤢", id="expand_spatial_btn", size="sm", color="light",
                                           style={"fontSize": "1.2rem", "padding": "2px 8px", "lineHeight": "1"}),
                            ]),
                            dbc.Row(className="mt-2 align-items-center", children=[
                                dbc.Col(width=2, children=[
                                    dbc.Label(["サンプル", help_badge("interactive_sample")]),
                                    dcc.Dropdown(id="interactive_sample",
                                                 placeholder="サンプル（空=全表示）",
                                                 clearable=True),
                                ]),
                                dbc.Col(width=3, children=[
                                    dbc.Label(["ハイライト", help_badge("spatial_highlight_cluster")]),
                                    dcc.Dropdown(
                                        id="spatial_highlight_cluster",
                                        multi=True,
                                        placeholder="ハイライトクラスタ",
                                    ),
                                ]),
                                dbc.Col(width=3, children=[
                                    dbc.Label(["除去", help_badge("spatial_exclude_cluster")]),
                                    dcc.Dropdown(
                                        id="spatial_exclude_cluster",
                                        multi=True,
                                        placeholder="除去するクラスタ",
                                    ),
                                ]),
                                dbc.Col(width=1, children=[
                                    dbc.Checkbox(id="spatial_show_labels", label="番号", value=False),
                                ]),
                                dbc.Col(width=2, children=[
                                    html.Div(style={"display": "flex", "alignItems": "center", "gap": "4px"}, children=[
                                        dbc.Label(["マーカーサイズ", help_badge("spatial_marker_size")], className="small mb-0"),
                                        dbc.Button("Auto", id="spatial_marker_auto_btn",
                                                   size="sm", outline=True, color="info",
                                                   style={"padding": "0 5px", "fontSize": "10px",
                                                          "lineHeight": "1.2"}),
                                    ]),
                                    dcc.Slider(
                                        id="spatial_marker_size",
                                        min=0, max=30, step=1, value=0,
                                        marks={0: "自動", 5: "5", 10: "10", 15: "15", 30: "30"},
                                        tooltip={"placement": "bottom", "always_visible": False},
                                    ),
                                ]),
                            ]),
                            dbc.Row(className="mt-1 align-items-center", children=[
                                dbc.Col(width=2, children=[
                                    dbc.Label("ラベルサイズ", className="small mb-0"),
                                    dcc.Slider(
                                        id="spatial_label_size",
                                        min=6, max=24, step=1, value=10,
                                        marks={6: "6", 10: "10", 14: "14", 20: "20", 24: "24"},
                                        tooltip={"placement": "bottom", "always_visible": False},
                                    ),
                                ]),
                                dbc.Col(width=2, children=[
                                    dbc.Label("横並び", className="small mb-0"),
                                    dcc.Dropdown(
                                        id="spatial_columns_per_row",
                                        options=[
                                            {"label": "自動", "value": 0},
                                            {"label": "1列", "value": 1},
                                            {"label": "2列", "value": 2},
                                            {"label": "3列", "value": 3},
                                            {"label": "4列", "value": 4},
                                            {"label": "5列", "value": 5},
                                            {"label": "6列", "value": 6},
                                            {"label": "7列", "value": 7},
                                            {"label": "8列", "value": 8},
                                        ],
                                        value=0, clearable=False,
                                        style={"fontSize": "12px"},
                                    ),
                                ]),
                            ]),
                            html.Div(id="spatial_controls_container"),
                            dcc.Loading(html.Div(id="spatial_plots_container")),
                        ]),

                        # --- Feature Plot ---
                        dbc.AccordionItem(title="Feature Plot", item_id="acc_feature", className="accordion-feature", children=[
                            html.Div(className="d-flex justify-content-end gap-2", children=[
                                dbc.Button("📷 一括保存", id="btn_batch_save_feature", size="sm",
                                           color="outline-secondary",
                                           style={"fontSize": "0.75rem"}),
                                dbc.Button("⤢", id="expand_feature_btn", size="sm", color="light",
                                           style={"fontSize": "1.2rem", "padding": "2px 8px", "lineHeight": "1"}),
                            ]),
                            dbc.Row(className="mt-2 align-items-center", children=[
                                dbc.Col(width=3, children=[
                                    dcc.Dropdown(
                                        id="feature_select",
                                        placeholder="m/z を入力して検索（例: 100.5）...",
                                        search_value="",
                                        optionHeight=50,
                                    ),
                                ]),
                                dbc.Col(width=2, children=[
                                    dcc.Dropdown(
                                        id="feature_sample_select",
                                        placeholder="サンプル（空=全表示）",
                                        clearable=True,
                                    ),
                                ]),
                                dbc.Col(width=4, children=[
                                    dbc.Label("ブックマーク", className="small mb-0"),
                                    html.Div(className="d-flex align-items-center gap-1", children=[
                                        dbc.Button("★ 追加", id="add_feature_bookmark_btn",
                                                   size="sm", color="warning", className="flex-shrink-0",
                                                   style={"whiteSpace": "nowrap"}),
                                        html.Div(style={"flex": "1 1 auto", "minWidth": "0"}, children=[
                                            dcc.Dropdown(
                                                id="feature_history_select",
                                                placeholder="ブックマークした Feature",
                                                clearable=True,
                                            ),
                                        ]),
                                        dbc.Button("✕", id="remove_feature_bookmark_btn",
                                                   size="sm", color="outline-danger", className="flex-shrink-0",
                                                   title="選択中のブックマークを削除"),
                                    ]),
                                ]),
                            ]),
                            dbc.Row(className="mt-1", children=[
                                dbc.Col(width="auto", children=[
                                    dbc.Switch(
                                        id="feature_show_compound_names",
                                        label="化合物名で表示（m/z ⇄ 化合物名）",
                                        value=True,
                                    ),
                                ]),
                            ]),
                            dbc.Row(className="mt-1 align-items-center", children=[
                                dbc.Col(width=4, children=[
                                    html.Div(style={"display": "flex", "alignItems": "center", "gap": "4px"}, children=[
                                        dbc.Label("マーカーサイズ", className="small mb-0"),
                                        dbc.Button("Auto", id="feature_marker_auto_btn",
                                                   size="sm", outline=True, color="info",
                                                   style={"padding": "0 5px", "fontSize": "10px",
                                                          "lineHeight": "1.2"}),
                                    ]),
                                    dcc.Slider(
                                        id="feature_marker_size",
                                        min=0, max=15, step=1, value=0,
                                        marks={0: "自動", 3: "3", 5: "5", 10: "10", 15: "15"},
                                        tooltip={"placement": "bottom", "always_visible": False},
                                    ),
                                ]),
                                dbc.Col(width=2, children=[
                                    dbc.Label("横並び", className="small mb-0"),
                                    dcc.Dropdown(
                                        id="feature_columns_per_row",
                                        options=[
                                            {"label": "自動", "value": 0},
                                            {"label": "1列", "value": 1},
                                            {"label": "2列", "value": 2},
                                            {"label": "3列", "value": 3},
                                            {"label": "4列", "value": 4},
                                            {"label": "5列", "value": 5},
                                            {"label": "6列", "value": 6},
                                            {"label": "7列", "value": 7},
                                            {"label": "8列", "value": 8},
                                        ],
                                        value=0, clearable=False,
                                        style={"fontSize": "12px"},
                                    ),
                                ]),
                            ]),
                            dbc.Row(className="mt-1 align-items-center", children=[
                                dbc.Col(width=2, children=[
                                    dbc.Label("m/z 最小値", className="small mb-0"),
                                    dbc.Input(id="feature_mz_min", type="number",
                                              placeholder="例: 100", size="sm"),
                                ]),
                                dbc.Col(width=2, children=[
                                    dbc.Label("m/z 最大値", className="small mb-0"),
                                    dbc.Input(id="feature_mz_max", type="number",
                                              placeholder="例: 900", size="sm"),
                                ]),
                                dbc.Col(width=2, className="d-flex align-items-end", children=[
                                    dbc.Button("絞り込み", id="apply_feature_mz_filter",
                                               size="sm", color="info", className="mb-1"),
                                ]),
                            ]),
                            dbc.Row(className="mt-1 align-items-center", children=[
                                dbc.Col(width=2, children=[
                                    dbc.Label("クラスタフィルタ", className="small mb-0"),
                                    dcc.Dropdown(
                                        id="feature_cluster_filter",
                                        placeholder="全クラスタ",
                                        clearable=True,
                                    ),
                                ]),
                                dbc.Col(width=3, children=[
                                    dbc.RadioItems(
                                        id="feature_filter_mode",
                                        options=[
                                            {"label": "全 m/z", "value": "all"},
                                            {"label": "DEGマーカー", "value": "deg"},
                                        ],
                                        value="all",
                                        inline=True,
                                        className="mt-3",
                                    ),
                                ]),
                                dbc.Col(width=2, children=[
                                    dbc.Label("強度 最小値 (%)", className="small mb-0"),
                                    dbc.Input(id="feature_intensity_min", type="number",
                                              placeholder="0", size="sm"),
                                ]),
                                dbc.Col(width=2, children=[
                                    dbc.Label("強度 最大値 (%)", className="small mb-0"),
                                    dbc.Input(id="feature_intensity_max", type="number",
                                              placeholder="100", size="sm"),
                                ]),
                            ]),
                            # --- カラースケール制御 (P1) ---
                            dbc.Row(className="mt-1 align-items-center", children=[
                                dbc.Col(width=3, children=[
                                    dbc.Label("カラースケール", className="small mb-0"),
                                    dcc.Dropdown(
                                        id="feature_colorscale",
                                        options=[{"label": c, "value": c} for c in
                                                 ["Plasma", "Viridis", "Magma", "Inferno",
                                                  "Cividis", "Turbo", "Hot", "Jet"]],
                                        value="Plasma", clearable=False,
                                        style={"fontSize": "12px"},
                                    ),
                                ]),
                                dbc.Col(width="auto", children=[
                                    dbc.Switch(id="feature_log_scale",
                                               label="log10 表示", value=False),
                                ]),
                                dbc.Col(width="auto", children=[
                                    dbc.Switch(id="feature_reverse_scale",
                                               label="色反転", value=False),
                                ]),
                            ]),
                            dcc.Loading(html.Div(id="feature_plot_container")),
                            # --- Feature 分布 (violin) パネル (P1) ---
                            html.Hr(className="my-2"),
                            dbc.Row(className="align-items-center", children=[
                                dbc.Col(width="auto", children=[
                                    dbc.Label("分布表示 (Violin)",
                                              className="small mb-0 fw-bold"),
                                ]),
                                dbc.Col(width=4, children=[
                                    dbc.RadioItems(
                                        id="feature_violin_group_by",
                                        options=[
                                            {"label": "クラスタ別", "value": "Cluster"},
                                            {"label": "サンプル別", "value": "Sample"},
                                        ],
                                        value="Cluster", inline=True,
                                    ),
                                ]),
                            ]),
                            dcc.Loading(dcc.Graph(
                                id="feature_violin_plot",
                                style={"height": "320px"},
                                config={"toImageButtonOptions": {
                                    "format": "png", "filename": "Feature_violin",
                                    "scale": 3}},
                            )),
                        ]),

                        # --- DEG マーカー ---
                        dbc.AccordionItem(title="DEG マーカー", item_id="acc_deg", className="accordion-deg", children=[
                            html.Div(
                                id="deg_results_section",
                                style={"display": "none"},
                                children=[
                                    html.Div(id="deg_no_data_message", style={"display": "none"}, children=[
                                        dbc.Alert(
                                            "DEGマーカーデータが見つかりません。結果フォルダに "
                                            "deg_markers.csv、markers_annotated.csv、"
                                            "または deg_FindAllMarkers_raw_*.rds が必要です。",
                                            color="info", className="mt-2",
                                        ),
                                    ]),
                                    html.Div(className="d-flex justify-content-end gap-2 mb-2", children=[
                                        dbc.Button("📷 一括保存", id="btn_batch_save_deg", size="sm",
                                                   color="outline-secondary",
                                                   style={"fontSize": "0.75rem"}),
                                        dbc.Button("⤢", id="expand_deg_btn", size="sm", color="light",
                                                   style={"fontSize": "1.2rem", "padding": "2px 8px", "lineHeight": "1"}),
                                    ]),
                                    dbc.Tabs(id="deg_viz_tabs", active_tab="deg_volcano_tab", children=[
                                        # --- Volcano Plot タブ ---
                                        dbc.Tab(label="Volcano Plot", tab_id="deg_volcano_tab", children=[
                                            dbc.Row(className="mt-2 mb-2 align-items-end", children=[
                                                dbc.Col(width=2, children=[
                                                    dcc.Dropdown(
                                                        id="volcano_cluster_select",
                                                        placeholder="クラスタ (空=全体)",
                                                        clearable=True,
                                                    ),
                                                ]),
                                                dbc.Col(width=2, children=[
                                                    dbc.Label(["FC 閾値", help_badge("volcano_fc_threshold")], className="small mb-0"),
                                                    dbc.Input(id="volcano_fc_threshold", type="number",
                                                              value=0.5, step=0.1, size="sm"),
                                                ]),
                                                dbc.Col(width=2, children=[
                                                    dbc.Label(["-log10(p) 閾値", help_badge("volcano_p_threshold")], className="small mb-0"),
                                                    dbc.Input(id="volcano_p_threshold", type="number",
                                                              value=1.3, step=0.1, size="sm"),
                                                ]),
                                                dbc.Col(width=2, children=[
                                                    dbc.Label("Y軸上限", className="small mb-0"),
                                                    dbc.Input(id="volcano_y_max", type="number",
                                                              placeholder="auto", step=1, size="sm"),
                                                ]),
                                                dbc.Col(width=3, children=[
                                                    dbc.Label("点サイズ", className="small mb-0"),
                                                    dcc.Slider(
                                                        id="volcano_marker_size",
                                                        min=2, max=20, step=1, value=8,
                                                        marks={2: "2", 8: "8", 14: "14", 20: "20"},
                                                        tooltip={"placement": "bottom", "always_visible": False},
                                                    ),
                                                ]),
                                                dbc.Col(width=2, children=[
                                                    dbc.Label("Top N ラベル", className="small mb-0"),
                                                    dbc.Input(
                                                        id="volcano_label_top_n",
                                                        type="number",
                                                        value=5, min=0, max=50, step=1,
                                                        size="sm",
                                                    ),
                                                    dbc.FormText("UP/DOWN各N個"),
                                                ]),
                                                dbc.Col(width=2, children=[
                                                    dbc.Switch(
                                                        id="volcano_annotation_switch",
                                                        label="アノテーション",
                                                        value=True,
                                                    ),
                                                ]),
                                            ]),
                                            # ハイライト行
                                            dbc.Row(className="mb-2 align-items-end", children=[
                                                dbc.Col(width=4, children=[
                                                    dbc.Label("🔍 m/z ハイライト", className="small mb-0"),
                                                    dbc.Input(
                                                        id="volcano_highlight_mz",
                                                        placeholder="例: 785.55, 810.60",
                                                        size="sm",
                                                    ),
                                                ]),
                                                dbc.Col(width=8, children=[
                                                    dbc.Label("🔍 化合物名ハイライト", className="small mb-0"),
                                                    dcc.Dropdown(
                                                        id="volcano_highlight_name",
                                                        placeholder="化合物名を選択...",
                                                        multi=True,
                                                    ),
                                                ]),
                                            ]),
                                            dcc.Loading(
                                                dcc.Graph(
                                                    id="volcano_plot",
                                                    style={"height": "500px"},
                                                    config={
                                                        "scrollZoom": True,
                                                        "toImageButtonOptions": {
                                                            "format": "png",
                                                            "filename": "Volcano_plot",
                                                            "scale": 3,
                                                        },
                                                    },
                                                ),
                                            ),
                                        ]),
                                        # --- Heatmap タブ ---
                                        dbc.Tab(label="Heatmap", tab_id="deg_heatmap_tab", children=[
                                            dbc.Row(className="mt-2 mb-2 align-items-end", children=[
                                                dbc.Col(width=2, children=[
                                                    dbc.Label("Top N", className="small mb-0"),
                                                    dbc.Input(id="heatmap_top_n", type="number",
                                                              value=5, min=1, max=20, step=1, size="sm"),
                                                ]),
                                                dbc.Col(width=3, children=[
                                                    dbc.Label(["スケール", help_badge("heatmap_scale")], className="small mb-0"),
                                                    dbc.RadioItems(
                                                        id="heatmap_scale",
                                                        options=[
                                                            {"label": "Z-score", "value": "zscore"},
                                                            {"label": "Raw", "value": "raw"},
                                                        ],
                                                        value="zscore", inline=True,
                                                    ),
                                                ]),
                                                dbc.Col(width=3, children=[
                                                    dbc.Switch(
                                                        id="heatmap_annotation_switch",
                                                        label=html.Span(["化合物名アノテーション", help_badge("heatmap_annotation_switch")]),
                                                        value=True,
                                                    ),
                                                ]),
                                                dbc.Col(width=3, children=[
                                                    dbc.Label("フォーカスクラスタ", className="small mb-0"),
                                                    dcc.Dropdown(
                                                        id="heatmap_cluster_select",
                                                        placeholder="全クラスタ",
                                                        clearable=True,
                                                    ),
                                                ]),
                                            ]),
                                            dcc.Loading(
                                                dcc.Graph(
                                                    id="heatmap_plot",
                                                    style={"height": "600px"},
                                                    config={
                                                        "toImageButtonOptions": {
                                                            "format": "png",
                                                            "filename": "Heatmap",
                                                            "scale": 3,
                                                        },
                                                    },
                                                ),
                                            ),
                                        ]),
                                        # --- マーカー表 タブ (P1: ソート可能 + Top-N CSV) ---
                                        dbc.Tab(label="マーカー表", tab_id="deg_table_tab", children=[
                                            dbc.Row(className="mt-2 mb-2 align-items-end", children=[
                                                dbc.Col(width=3, children=[
                                                    dbc.Label("クラスタ", className="small mb-0"),
                                                    dcc.Dropdown(
                                                        id="deg_markers_cluster_filter",
                                                        placeholder="全クラスタ", clearable=True),
                                                ]),
                                                dbc.Col(width=2, children=[
                                                    dbc.Label("Top N 出力", className="small mb-0"),
                                                    dcc.Dropdown(
                                                        id="marker_table_top_n",
                                                        options=[{"label": lbl, "value": v} for lbl, v in
                                                                 [("10", 10), ("20", 20), ("50", 50),
                                                                  ("100", 100), ("全件", 0)]],
                                                        value=50, clearable=False,
                                                        style={"fontSize": "12px"},
                                                    ),
                                                ]),
                                                dbc.Col(width="auto", className="d-flex align-items-end", children=[
                                                    dbc.Button("CSV 出力", id="btn_export_marker_table",
                                                               size="sm", color="success", className="mb-1"),
                                                ]),
                                                dbc.Col(width="auto", className="d-flex align-items-center", children=[
                                                    dbc.FormText("現在の並び替え/絞り込みを反映"),
                                                ]),
                                            ]),
                                            dash_table.DataTable(
                                                id="deg_markers_table",
                                                columns=[],
                                                data=[],
                                                sort_action="native",
                                                filter_action="native",
                                                page_size=20,
                                                style_table={"overflowX": "auto"},
                                                style_cell={"fontSize": "12px", "padding": "4px",
                                                            "fontFamily": "monospace"},
                                                style_header={"fontWeight": "bold",
                                                              "backgroundColor": "#f1f3f5"},
                                            ),
                                        ]),
                                        # --- 選択 DE タブ (P2: アプリ内 on-the-fly DE) ---
                                        dbc.Tab(label="選択 DE", tab_id="deg_onthefly_tab", children=[
                                            dbc.Alert(
                                                "UMAP で投げ縄/ボックス選択 → モードを選んで「DE 実行」。"
                                                "Globally=選択 vs 全体 / Locally=選択 vs 指定クラスタ。"
                                                "（検定: Wilcoxon, 補正: BH。~30-60秒）",
                                                color="light", className="small mt-2 mb-2 py-2"),
                                            dbc.Row(className="mb-2 align-items-end", children=[
                                                dbc.Col(width=3, children=[
                                                    dbc.Label("比較モード", className="small mb-0"),
                                                    dbc.RadioItems(
                                                        id="onthefly_de_mode",
                                                        options=[
                                                            {"label": "Globally (vs 全体)", "value": "global"},
                                                            {"label": "Locally (vs 指定群)", "value": "local"},
                                                        ],
                                                        value="global",
                                                    ),
                                                ]),
                                                dbc.Col(width=4, children=[
                                                    dbc.Label("比較対象クラスタ (Locally)", className="small mb-0"),
                                                    dcc.Dropdown(id="onthefly_de_target", multi=True,
                                                                 placeholder="ident.2 のクラスタ"),
                                                ]),
                                                dbc.Col(width="auto", className="d-flex align-items-end", children=[
                                                    dbc.Button("DE 実行", id="btn_run_onthefly_de",
                                                               size="sm", color="primary", className="mb-1"),
                                                ]),
                                                dbc.Col(width=4, className="d-flex align-items-center", children=[
                                                    dcc.Loading(html.Div(id="onthefly_de_status")),
                                                ]),
                                            ]),
                                            dbc.Row(className="mb-2 align-items-end", children=[
                                                dbc.Col(width=2, children=[
                                                    dbc.Label("FC 閾値", className="small mb-0"),
                                                    dbc.Input(id="onthefly_de_fc", type="number",
                                                              value=0.5, step=0.1, size="sm"),
                                                ]),
                                                dbc.Col(width=2, children=[
                                                    dbc.Label("-log10(p) 閾値", className="small mb-0"),
                                                    dbc.Input(id="onthefly_de_p", type="number",
                                                              value=1.3, step=0.1, size="sm"),
                                                ]),
                                                dbc.Col(width=2, children=[
                                                    dbc.Label("Top N 出力", className="small mb-0"),
                                                    dcc.Dropdown(
                                                        id="onthefly_de_top_n",
                                                        options=[{"label": lbl, "value": v} for lbl, v in
                                                                 [("10", 10), ("20", 20), ("50", 50),
                                                                  ("100", 100), ("全件", 0)]],
                                                        value=50, clearable=False, style={"fontSize": "12px"}),
                                                ]),
                                                dbc.Col(width="auto", className="d-flex align-items-end", children=[
                                                    dbc.Button("CSV 出力", id="btn_export_onthefly_de",
                                                               size="sm", color="success", className="mb-1"),
                                                ]),
                                            ]),
                                            dcc.Loading(dcc.Graph(
                                                id="onthefly_de_volcano", style={"height": "420px"},
                                                config={"toImageButtonOptions": {
                                                    "format": "png", "filename": "onthefly_DE_volcano",
                                                    "scale": 3}})),
                                            dash_table.DataTable(
                                                id="onthefly_de_table", columns=[], data=[],
                                                sort_action="native", filter_action="native", page_size=20,
                                                style_table={"overflowX": "auto"},
                                                style_cell={"fontSize": "12px", "padding": "4px",
                                                            "fontFamily": "monospace"},
                                                style_header={"fontWeight": "bold",
                                                              "backgroundColor": "#f1f3f5"},
                                            ),
                                        ]),
                                    ]),
                                ],
                            ),
                        ]),
                    ],
                ),

            ],
        ),

        # フルスクリーン拡大モーダル
        dbc.Modal(
            id="fullscreen_plot_modal", size="xl", fullscreen=True, centered=True,
            children=[
                dbc.ModalHeader(dbc.ModalTitle(id="fullscreen_modal_title"), close_button=True),
                dbc.ModalBody(id="fullscreen_modal_body", style={"padding": "10px"}),
            ],
        ),

        # PR-F: UI ロック用 Store + Interval (複数ユーザー同時編集対応)
        dcc.Store(id="session_id_store", data=None),
        dcc.Store(id="edit_lock_state", data={}),
        dcc.Interval(
            id="edit_lock_heartbeat",
            interval=EDIT_LOCK_HEARTBEAT_INTERVAL_SEC * 1000,
            n_intervals=0,
        ),

        # Seuratブリッジのキャッシュパスを保持
        dcc.Store(id="seurat_cache_dir_store"),
        dcc.Store(id="seurat_rds_path_store"),
        # ロード段階チェーンの制御信号（A→B→C→D。各段の進捗メッセージ表示用）
        dcc.Store(id="load_stage_trigger", data=None),
        dcc.Store(id="load_stage_trigger_2", data=None),
        dcc.Store(id="load_stage_trigger_3", data=None),
        # キャンセル用トークン（Stage A で発行。キャンセルボタンが参照） ver4.19
        dcc.Store(id="load_token_store", data=None),
        # 統合手法 → RDSパスのマッピング
        dcc.Store(id="interactive_rds_map", data=None),
        # DEGデータのキャッシュ
        dcc.Store(id="deg_data_store", data=None),
        # Spatial代表figureの保持（HTMLエクスポート用）
        dcc.Store(id="last_spatial_figure_store", data=None),
        # Spatial回転角度の保持（サンプル別）
        dcc.Store(id="spatial_rotation_store", data={}),
        # サンプル名の表示名マッピング（{"元名": "表示名", ...}）
        dcc.Store(id="sample_name_map_store", data={}),
        # アノテーション位置の蓄積（relayoutData イベントからリアルタイム蓄積）
        dcc.Store(id="accumulated_label_positions", data={}),
        # Feature Plot m/zフィルタ結果リスト
        dcc.Store(id="feature_mz_filtered_list", data=None),
        # カスタムクラスタ色マッピング（{"0": "#FF0000", ...}）
        dcc.Store(id="custom_color_map_store", data={}),
        # クラスタ名マッピング（{"0": "Epithelial", "1": "Stromal", ...}）
        dcc.Store(id="cluster_name_map_store", data={}),
        # Feature Plot 閲覧履歴
        dcc.Store(id="feature_history_store", data=[]),
        # フルスクリーン閉じトリガー
        dcc.Store(id="fullscreen_closed_trigger", data=0),
        # 軽量ビューア「開く」前の設定 flush 完了シグナル（タイムスタンプ）
        # clientside_callback はこの Store の data 変化で window.open を発火する
        dcc.Store(id="lite_viewer_open_signal", data=0),
        # 軽量ビューア clientside_callback の dummy Output (Dash の循環依存
        # 検出を回避するため、btn_open_lite_viewer.n_clicks ではなくこの
        # ダミー Store に向けて書込む)
        dcc.Store(id="lite_viewer_open_dummy", data=0),
        # キャリブレーション対応表データ（settings_tab / interactive 共有）
        dcc.Store(id="calibration_table_data",
                  data=_ls.get("calibration_table_data", [])),
        # キャリブレーション自動保存トリガー（ダミー出力先）
        dcc.Store(id="calibration_save_trigger", data=None),
        # UMAP表示設定の自動保存トリガー（ダミー出力先）
        dcc.Store(id="umap_display_save_trigger", data=None),
        # Spatial表示設定の自動保存トリガー（ダミー出力先）
        dcc.Store(id="spatial_display_save_trigger", data=None),
        # 再解析キャリブレーション回帰データ（analysis_params.jsonから読込）
        dcc.Store(id="reanalysis_calibration_data", data=None),
        # エクスポート Top N 値ブリッジ用
        dcc.Store(id="export_top_n_store", data=5),
        # PPTXダウンロード用
        dcc.Download(id="dl_report_pptx"),
        # 一括保存用 Store / Download
        dcc.Store(id="batch_umap_figures_store", data=[]),
        dcc.Store(id="batch_spatial_figures_store", data=[]),
        dcc.Store(id="batch_feature_figures_store", data=[]),
        dcc.Download(id="dl_batch_zip"),
        # データ出力 (UMAP cluster) 用
        dcc.Download(id="dl_data_export"),
        dcc.Store(id="data_export_trigger", data=None),
        # インタラクティブキャリブレーション用
        dcc.Store(id="int_cal_table_data", data=[]),
        dcc.Store(id="int_cal_save_trigger", data=None),
        dcc.Store(id="int_cal_ms_instrument", data="TIMS"),
        dcc.Store(id="int_cal_restore_pending", data=False),
        # プロジェクトとして保存: リセット抑止フラグ
        dcc.Store(id="sap_skip_reset", data=False),

        # --- Loupe 風 追加機能 (P1) 用 Store / Download ---
        # lasso/box 選択の単一ソース (選択統計・将来の逆リンク/選択グループの土台)
        dcc.Store(id="selected_cell_ids_store", data=[]),
        # マーカー表 Top-N CSV ダウンロード
        dcc.Download(id="dl_marker_table_csv"),
        # --- P2: アプリ内 on-the-fly DE 用 ---
        dcc.Store(id="onthefly_de_store", data=None),
        dcc.Download(id="dl_onthefly_de_csv"),

        # プロジェクトとして保存モーダル
        _create_save_as_project_modal(),
    ])


def _create_save_as_project_modal():
    """プロジェクトとして保存モーダル"""
    return dbc.Modal(
        id="save_as_project_modal",
        size="lg",
        centered=True,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("プロジェクトとして保存")),
            dbc.ModalBody([
                # アクション選択
                dbc.Label("アクション", className="fw-bold"),
                dbc.RadioItems(
                    id="sap_action_type",
                    options=[
                        {"label": "新規プロジェクト + 新規サブプロジェクト作成",
                         "value": "new_all"},
                        {"label": "既存プロジェクトにサブプロジェクト追加",
                         "value": "add_sub"},
                        {"label": "既存サブプロジェクトに紐付け",
                         "value": "link_existing"},
                    ],
                    value="new_all",
                    className="mb-3",
                ),

                # --- 新規プロジェクト入力 (new_all) ---
                html.Div(id="sap_new_project_section", children=[
                    dbc.Label("プロジェクト名", className="fw-bold"),
                    dbc.Input(id="sap_project_name",
                              placeholder="プロジェクト名を入力"),
                    dbc.Label("実験日", className="fw-bold mt-2"),
                    dbc.Input(id="sap_project_date", type="date"),
                ]),

                # --- 既存プロジェクト選択 (add_sub / link_existing) ---
                html.Div(id="sap_existing_project_section",
                         style={"display": "none"}, children=[
                    dbc.Label("プロジェクト", className="fw-bold"),
                    dcc.Dropdown(id="sap_project_select",
                                 placeholder="プロジェクトを選択"),
                ]),

                # --- 新規サブプロジェクト入力 (new_all / add_sub) ---
                html.Div(id="sap_new_sub_section", children=[
                    html.Hr(className="my-3"),
                    dbc.Label("サブプロジェクト名", className="fw-bold"),
                    dbc.Input(id="sap_sub_name",
                              placeholder="サブプロジェクト名を入力"),
                    dbc.Row(className="mt-2", children=[
                        dbc.Col(width=4, children=[
                            dbc.Label("実験日", className="small fw-bold"),
                            dbc.Input(id="sap_sub_date", type="date"),
                        ]),
                        dbc.Col(width=4, children=[
                            dbc.Label("対象化合物", className="small fw-bold"),
                            dbc.Input(id="sap_target_compound"),
                        ]),
                        dbc.Col(width=4, children=[
                            dbc.Label("MS装置", className="small fw-bold"),
                            dbc.Select(
                                id="sap_ms_instrument",
                                options=[
                                    {"label": "TIMS", "value": "TIMS"},
                                    {"label": "DESI", "value": "DESI"},
                                ],
                                value="TIMS",
                            ),
                        ]),
                    ]),
                    dbc.Row(className="mt-2", children=[
                        dbc.Col(width=6, children=[
                            dbc.Label("極性", className="small fw-bold"),
                            dbc.Checklist(
                                id="sap_polarity",
                                options=[
                                    {"label": "Positive",
                                     "value": "Positive"},
                                    {"label": "Negative",
                                     "value": "Negative"},
                                ],
                                value=["Positive"],
                                inline=True,
                            ),
                        ]),
                    ]),
                ]),

                # --- 既存サブプロジェクト選択 (link_existing) ---
                html.Div(id="sap_existing_sub_section",
                         style={"display": "none"}, children=[
                    html.Hr(className="my-3"),
                    dbc.Label("サブプロジェクト", className="fw-bold"),
                    dcc.Dropdown(id="sap_sub_select",
                                 placeholder="サブプロジェクトを選択"),
                ]),

                # --- 自動入力パス表示 ---
                html.Hr(className="my-3"),
                dbc.Label("自動入力パス", className="fw-bold text-muted"),
                html.Div(className="small text-muted", children=[
                    html.Div(id="sap_result_folder_display"),
                    html.Div(id="sap_msi_folder_display"),
                ]),

                # ステータス
                html.Div(id="sap_status", className="mt-2"),
            ]),
            dbc.ModalFooter([
                dbc.Button("キャンセル",
                           id="close_save_as_project_modal",
                           color="secondary"),
                dbc.Button("保存",
                           id="execute_save_as_project",
                           color="success"),
            ]),
        ],
    )
