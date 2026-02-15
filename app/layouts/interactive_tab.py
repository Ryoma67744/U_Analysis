# =============================================================================
# MSI Analysis Application - Interactive Analysis Tab UI
# インタラクティブ解析タブUI
# =============================================================================

from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc


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
                # 統合手法ヘッダーバー（結果エリア上部）
                html.Div(
                    className="card mb-2",
                    style={"padding": "10px 15px", "display": "flex",
                           "flexDirection": "row", "alignItems": "center", "gap": "15px"},
                    children=[
                        html.H5("統合手法", className="mb-0",
                                style={"whiteSpace": "nowrap"}),
                        dbc.RadioItems(
                            id="interactive_integration_method",
                            options=[],
                            value=None,
                            inline=True,
                        ),
                    ],
                ),

                dbc.Row(className="mt-3", children=[
                    # UMAP プロット
                    dbc.Col(width=7, children=[
                        html.Div(className="card", children=[
                            html.H5("UMAP"),
                            dbc.Row([
                                dbc.Col(width=4, children=[
                                    dbc.Label("色分け"),
                                    dbc.RadioItems(
                                        id="umap_color_by",
                                        options=[
                                            {"label": "Cluster", "value": "Cluster"},
                                            {"label": "Sample", "value": "Sample"},
                                        ],
                                        value="Cluster", inline=True,
                                    ),
                                ]),
                                dbc.Col(width=6, children=[
                                    dbc.Label("ハイライト"),
                                    dcc.Dropdown(
                                        id="umap_highlight_cluster",
                                        multi=True, placeholder="クラスタを選択",
                                    ),
                                ]),
                                dbc.Col(width=2, children=[
                                    dbc.Checkbox(id="umap_show_legend", label="凡例", value=True),
                                ]),
                            ]),
                            dcc.Loading(
                                dcc.Graph(id="interactive_umap_plot",
                                          style={"height": "450px"},
                                          config={"scrollZoom": True}),
                            ),
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

                # Feature プロット + Spatial
                dbc.Row(className="mt-3", children=[
                    dbc.Col(width=6, children=[
                        html.Div(className="card", children=[
                            html.H5("Feature Plot"),
                            dbc.Row([
                                dbc.Col(width=8, children=[
                                    dcc.Dropdown(
                                        id="feature_select",
                                        placeholder="m/z Feature を検索・選択",
                                        search_value="",
                                    ),
                                ]),
                                dbc.Col(width=4, children=[
                                    dbc.Button("表示", id="show_feature_plot",
                                               size="sm", color="primary"),
                                ]),
                            ]),
                            dcc.Loading(
                                dcc.Graph(id="feature_plot",
                                          style={"height": "350px"},
                                          config={"scrollZoom": True}),
                            ),
                        ]),
                    ]),
                    dbc.Col(width=6, children=[
                        html.Div(className="card", children=[
                            html.H5("Spatial Mapping"),
                            dbc.Row([
                                dbc.Col(width=6, children=[
                                    dcc.Dropdown(id="interactive_sample", placeholder="サンプル"),
                                ]),
                            ]),
                            dcc.Loading(
                                dcc.Graph(id="spatial_mapping_plot",
                                          style={"height": "350px"},
                                          config={"scrollZoom": True}),
                            ),
                        ]),
                    ]),
                ]),

                # DEG 結果テーブル
                dbc.Row(className="mt-3", children=[
                    dbc.Col(width=12, children=[
                        html.Div(
                            id="deg_results_section",
                            className="card",
                            style={"display": "none"},
                            children=[
                                html.H5("DEG マーカー"),
                                html.P(
                                    "クラスタを選択すると、マーカー遺伝子/m/z の一覧を表示します。"
                                    "行をクリックすると Feature Plot に表示されます。",
                                    className="text-muted small",
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

        # Seuratブリッジのキャッシュパスを保持
        dcc.Store(id="seurat_cache_dir_store"),
        dcc.Store(id="seurat_rds_path_store"),
        # 統合手法 → RDSパスのマッピング
        dcc.Store(id="interactive_rds_map", data=None),
        # DEGデータのキャッシュ
        dcc.Store(id="deg_data_store", data=None),
        # CSVダウンロード用
        dcc.Download(id="download_csv"),
        dcc.Download(id="download_html"),
    ])
