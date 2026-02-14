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
                        dcc.Dropdown(
                            id="interactive_rds_select",
                            placeholder="RDSファイルを選択",
                            style={"marginTop": "10px"},
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
                                        placeholder="m/z Feature を選択",
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
            ],
        ),

        # Seuratブリッジのキャッシュパスを保持
        dcc.Store(id="seurat_cache_dir_store"),
        dcc.Store(id="seurat_rds_path_store"),
    ])
