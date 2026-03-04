# =============================================================================
# MSI Analysis Application - Interactive Analysis Tab UI
# インタラクティブ解析タブUI
# =============================================================================

from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc

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
                    html.Div(className="param-group", children=[
                        html.H5("MSIデータフォルダ (オプション)"),
                        html.Div(
                            style={"display": "flex", "gap": "5px"},
                            children=[
                                dbc.Input(id="interactive_msi_folder", placeholder="MSIデータフォルダ"),
                                dbc.Button("参照...", id="browse_interactive_msi",
                                           size="sm", color="secondary"),
                                dbc.Button("スキャン", id="scan_msi_folder",
                                           size="sm", color="info"),
                            ],
                        ),
                        html.Div(id="interactive_msi_samples", style={"marginTop": "10px"}),
                    ]),
                ]),
            ]),
            html.Div(
                style={"display": "flex", "gap": "10px", "marginTop": "10px",
                       "alignItems": "center"},
                children=[
                    dbc.Button(
                        "データを読み込む", id="load_interactive_data",
                        color="primary",
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
                                    # MRMファイル（DESIのみ表示）
                                    html.Div(
                                        id="int_cal_mrm_section",
                                        style={"display": "none"},
                                        children=[
                                            dbc.Row(className="mb-2", children=[
                                                dbc.Col(width=12, children=[
                                                    dbc.Label("MRMファイル", className="small fw-bold"),
                                                    html.Div(
                                                        style={"display": "flex", "gap": "5px"},
                                                        children=[
                                                            dbc.Input(id="int_cal_mrm_path",
                                                                      placeholder="MRM.xlsx",
                                                                      size="sm"),
                                                            dbc.Button("参照...", id="browse_int_cal_mrm",
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
                            ],
                        ),
                        id="integration_method_collapse",
                        is_open=True,
                    ),
                ], className="mb-2", id="integration_method_wrapper"),

                # アコーディオン（各セクション折りたたみ可能）
                dbc.Accordion(
                    always_open=True,
                    start_collapsed=False,
                    flush=True,
                    className="mt-3",
                    children=[
                        # --- エクスポート ---
                        dbc.AccordionItem(title="エクスポート", className="accordion-export", children=[
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
                        ]),

                        # --- クラスタ情報 ---
                        dbc.AccordionItem(title="クラスタ情報", className="accordion-cluster", children=[
                            html.Pre(id="cluster_info_text",
                                     style={"fontSize": "0.85rem", "maxHeight": "120px",
                                            "overflowY": "auto"}),
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
                        dbc.AccordionItem(title="UMAP", className="accordion-umap", children=[
                            html.Div(className="d-flex justify-content-end", children=[
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
                                dbc.Col(width=2, className="d-flex align-items-end", children=[
                                    dbc.Button("ラベル位置保存", id="save_label_pos_btn",
                                               size="sm", color="secondary", className="mb-1"),
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
                        ]),

                        # --- Spatial Mapping ---
                        dbc.AccordionItem(title="Spatial Mapping", className="accordion-spatial", children=[
                            html.Div(className="d-flex justify-content-end", children=[
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
                                dbc.Col(width=2, className="d-flex align-items-end", children=[
                                    dbc.Button("ラベル位置保存", id="save_spatial_label_pos_btn",
                                               size="sm", color="secondary", className="mb-1"),
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
                        dbc.AccordionItem(title="Feature Plot", className="accordion-feature", children=[
                            html.Div(className="d-flex justify-content-end", children=[
                                dbc.Button("⤢", id="expand_feature_btn", size="sm", color="light",
                                           style={"fontSize": "1.2rem", "padding": "2px 8px", "lineHeight": "1"}),
                            ]),
                            dbc.Row(className="mt-2 align-items-center", children=[
                                dbc.Col(width=3, children=[
                                    dcc.Dropdown(
                                        id="feature_select",
                                        placeholder="m/z Feature を検索・選択",
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
                            dcc.Loading(html.Div(id="feature_plot_container")),
                        ]),

                        # --- DEG マーカー ---
                        dbc.AccordionItem(title="DEG マーカー", className="accordion-deg", children=[
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
                                    html.Div(className="d-flex justify-content-end mb-2", children=[
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

        # Seuratブリッジのキャッシュパスを保持
        dcc.Store(id="seurat_cache_dir_store"),
        dcc.Store(id="seurat_rds_path_store"),
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
        # ラベル位置保存ステータス
        dcc.Store(id="label_pos_save_status", data=None),
        # ラベル位置スナップショット（clientside callback → save callback ブリッジ）
        dcc.Store(id="annotation_snapshot_store", data=None),
        # アノテーション位置の蓄積（relayoutData イベントからリアルタイム蓄積）
        dcc.Store(id="accumulated_label_positions", data={}),
        # Feature Plot m/zフィルタ結果リスト
        dcc.Store(id="feature_mz_filtered_list", data=None),
        # カスタムクラスタ色マッピング（{"0": "#FF0000", ...}）
        dcc.Store(id="custom_color_map_store", data={}),
        # Feature Plot 閲覧履歴
        dcc.Store(id="feature_history_store", data=[]),
        # フルスクリーン閉じトリガー
        dcc.Store(id="fullscreen_closed_trigger", data=0),
        # キャリブレーション対応表データ（settings_tab / interactive 共有）
        dcc.Store(id="calibration_table_data",
                  data=_ls.get("calibration_table_data", [])),
        # キャリブレーション自動保存トリガー（ダミー出力先）
        dcc.Store(id="calibration_save_trigger", data=None),
        # エクスポート Top N 値ブリッジ用
        dcc.Store(id="export_top_n_store", data=5),
        # PPTXダウンロード用
        dcc.Download(id="dl_report_pptx"),
        # インタラクティブキャリブレーション用
        dcc.Store(id="int_cal_table_data", data=[]),
        dcc.Store(id="int_cal_save_trigger", data=None),
        dcc.Store(id="int_cal_ms_instrument", data="TIMS"),
        dcc.Store(id="int_cal_restore_pending", data=False),
        # プロジェクトとして保存: リセット抑止フラグ
        dcc.Store(id="sap_skip_reset", data=False),

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
