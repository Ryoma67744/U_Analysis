# =============================================================================
# MSI Analysis Application - Interactive Analysis Tab UI
# インタラクティブ解析タブUI
# =============================================================================

from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc

from app.layouts.tooltips import help_badge



def create_interactive_tab():
    return html.Div(style={"marginTop": "15px"}, children=[
        # データソース選択
        html.Div(className="card", children=[
            html.H4(className="card-title", children=["🔬 インタラクティブ解析"]),

            # プロジェクト / サブプロジェクト選択（主な選択手段）
            dbc.Row(className="mb-3", children=[
                dbc.Col(width=5, children=[
                    dbc.Label("プロジェクト", className="small fw-bold"),
                    dcc.Dropdown(
                        id="interactive_project_select",
                        placeholder="プロジェクトを選択",
                        clearable=True,
                    ),
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
            dbc.Button(
                "データを読み込む", id="load_interactive_data",
                color="primary", style={"marginTop": "10px"},
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
                ], className="mb-2"),

                dbc.Row(className="mt-3", children=[
                    # UMAP プロット
                    dbc.Col(width=7, children=[
                        html.Div(className="card", children=[
                            html.Div(className="d-flex justify-content-between align-items-center", children=[
                                html.H5("UMAP", className="mb-0"),
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
                    ]),

                    # クラスタ情報 + 統計
                    dbc.Col(width=5, children=[
                        html.Div(className="card", children=[
                            html.H5("クラスタ情報"),
                            html.Pre(id="cluster_info_text",
                                     style={"fontSize": "0.85rem", "maxHeight": "120px",
                                            "overflowY": "auto"}),
                            html.H5("クラスタ統計", className="mt-2"),
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
                                        style_header={"backgroundColor": "#f8f9fa", "fontWeight": "600"},
                                        page_size=15,
                                    ),
                                ],
                            ),
                        ]),
                    ]),
                ]),

                # Spatial Mapping（全幅 — 複数切片を横並びで表示）
                dbc.Row(className="mt-3", children=[
                    dbc.Col(width=12, children=[
                        html.Div(className="card", children=[
                            html.Div(className="d-flex justify-content-between align-items-center", children=[
                                html.H5("Spatial Mapping", className="mb-0"),
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
                                    dbc.Label(["マーカーサイズ", help_badge("spatial_marker_size")], className="small mb-0"),
                                    dcc.Slider(
                                        id="spatial_marker_size",
                                        min=0, max=15, step=1, value=0,
                                        marks={0: "自動", 4: "4", 8: "8", 15: "15"},
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
                            ]),
                            html.Div(id="spatial_controls_container"),
                            dcc.Loading(html.Div(id="spatial_plots_container")),
                        ]),
                    ]),
                ]),

                # Feature プロット（Spatial表示）
                dbc.Row(className="mt-3", children=[
                    dbc.Col(width=12, children=[
                        html.Div(className="card", children=[
                            html.Div(className="d-flex justify-content-between align-items-center", children=[
                                html.H5("Feature Plot", className="mb-0"),
                                dbc.Button("⤢", id="expand_feature_btn", size="sm", color="light",
                                           style={"fontSize": "1.2rem", "padding": "2px 8px", "lineHeight": "1"}),
                            ]),
                            dbc.Row(className="mt-2 align-items-center", children=[
                                dbc.Col(width=3, children=[
                                    dcc.Dropdown(
                                        id="feature_select",
                                        placeholder="m/z Feature を検索・選択",
                                        search_value="",
                                    ),
                                ]),
                                dbc.Col(width=2, children=[
                                    dcc.Dropdown(
                                        id="feature_sample_select",
                                        placeholder="サンプル（空=全表示）",
                                        clearable=True,
                                    ),
                                ]),
                                dbc.Col(width=3, children=[
                                    dbc.Label("マーカーサイズ", className="small mb-0"),
                                    dcc.Slider(
                                        id="feature_marker_size",
                                        min=1, max=15, step=1, value=3,
                                        marks={1: "1", 3: "3", 5: "5", 10: "10", 15: "15"},
                                        tooltip={"placement": "bottom", "always_visible": False},
                                    ),
                                ]),
                                dbc.Col(width=1, children=[
                                    dbc.Button("表示", id="show_feature_plot",
                                               size="sm", color="primary"),
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
                            dcc.Loading(html.Div(id="feature_plot_container")),
                        ]),
                    ]),
                ]),

                # DEG 結果（テーブル / Volcano Plot / Heatmap）
                dbc.Row(className="mt-3", children=[
                    dbc.Col(width=12, children=[
                        html.Div(
                            id="deg_results_section",
                            className="card",
                            style={"display": "none"},
                            children=[
                                html.Div(className="d-flex justify-content-between align-items-center mb-2", children=[
                                    html.H5("DEG マーカー", className="mb-0"),
                                    dbc.Button("⤢", id="expand_deg_btn", size="sm", color="light",
                                               style={"fontSize": "1.2rem", "padding": "2px 8px", "lineHeight": "1"}),
                                ]),
                                dbc.Tabs(id="deg_viz_tabs", active_tab="deg_table_tab", children=[
                                    # --- テーブルタブ ---
                                    dbc.Tab(label="テーブル", tab_id="deg_table_tab", children=[
                                        html.P(
                                            "行をクリックすると Feature Plot に表示されます。",
                                            className="text-muted small mt-2",
                                        ),
                                        html.Div(
                                            id="deg_table_container",
                                            style={"maxHeight": "300px", "overflowY": "auto"},
                                            children=[
                                                dash_table.DataTable(
                                                    id="deg_results_table",
                                                    columns=[
                                                        {"name": "Gene/m/z", "id": "gene"},
                                                        {"name": "Cluster", "id": "cluster"},
                                                        {"name": "avg_log2FC", "id": "avg_log2FC"},
                                                        {"name": "p_val_adj", "id": "p_val_adj"},
                                                        {"name": "pct.1", "id": "pct.1"},
                                                        {"name": "pct.2", "id": "pct.2"},
                                                    ],
                                                    data=[],
                                                    row_selectable="single",
                                                    sort_action="native",
                                                    filter_action="native",
                                                    style_table={"overflowX": "auto"},
                                                    style_cell={
                                                        "textAlign": "left",
                                                        "padding": "6px",
                                                        "fontSize": "0.8rem",
                                                    },
                                                    style_header={
                                                        "backgroundColor": "#f8f9fa",
                                                        "fontWeight": "600",
                                                    },
                                                    page_size=20,
                                                ),
                                            ],
                                        ),
                                    ]),
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
                ]),

                # HTML エクスポート
                dbc.Row(className="mt-3 mb-3", children=[
                    dbc.Col(width=12, children=[
                        html.Div(className="card", children=[
                            html.H5("エクスポート"),
                            dbc.Row([
                                dbc.Col(width="auto", children=[
                                    dbc.Button(
                                        "📄 HTML レポート出力",
                                        id="export_html_report",
                                        color="success", size="sm",
                                    ),
                                ]),
                                dbc.Col(width="auto", children=[
                                    dbc.Button(
                                        "📊 データ CSV 出力",
                                        id="export_csv_data",
                                        color="info", size="sm",
                                    ),
                                ]),
                            ]),
                            html.Div(id="export_status", className="mt-2 text-muted"),
                        ]),
                    ]),
                ]),
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
        # Feature Plot m/zフィルタ結果リスト
        dcc.Store(id="feature_mz_filtered_list", data=None),
        # カスタムクラスタ色マッピング（{"0": "#FF0000", ...}）
        dcc.Store(id="custom_color_map_store", data={}),
        # フルスクリーン閉じトリガー
        dcc.Store(id="fullscreen_closed_trigger", data=0),
        # CSVダウンロード用
        dcc.Download(id="download_csv"),
        dcc.Download(id="download_html"),
    ])
