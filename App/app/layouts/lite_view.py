# =============================================================================
# MSI Analysis Application - Lite View Layout
# 軽量ビューア（/lite/<project_id>/<sub_project_id>）読み取り専用
# =============================================================================
# 解析結果フォルダから直接 URL で開けるローカル軽量共有ビュー。
# share_layout.py のサブセット（インタラクティブ機能のみ、ギャラリー無し）。
# =============================================================================

from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc


def create_lite_view_layout():
    """軽量ビューア（プロジェクト ID から直接アクセス、認証なし読み取り専用）"""
    return html.Div([
        # --- 内部ストア ---
        dcc.Store(id="lite_target_store", data={}),
        dcc.Store(id="lv_rds_path", data=""),
        dcc.Store(id="lv_integration_method", data=""),
        dcc.Store(id="lv_deg_data_store", data=None),

        # --- ヘッダー ---
        html.Div(className="card mb-3", children=[
            html.Div(
                className="d-flex justify-content-between align-items-center",
                children=[
                    html.H4("MSI Analysis — 軽量ビューア", className="mb-0"),
                    html.Span("読み取り専用", className="badge bg-secondary"),
                ],
            ),
            html.Div(id="lv_metadata", className="mt-2 text-muted small"),
            html.Div(id="lv_error", style={"display": "none"},
                     className="alert alert-danger mt-2"),
        ]),

        # --- メインコンテンツ ---
        html.Div(id="lv_content", style={"display": "none"}, children=[
            html.Div(id="lv_data_info", className="text-muted small mb-2"),

            # --- UMAP ---
            html.Div(className="card mb-3", children=[
                html.H5("UMAP"),
                dbc.Row(className="mb-2 align-items-center", children=[
                    dbc.Col(width=2, children=[
                        dbc.Label("色分け", className="small"),
                        dbc.RadioItems(
                            id="lv_umap_color_by",
                            options=[
                                {"label": "Cluster", "value": "Cluster"},
                                {"label": "Sample", "value": "Sample"},
                            ],
                            value="Cluster",
                            inline=True,
                        ),
                    ]),
                    dbc.Col(width=3, children=[
                        dbc.Label("ハイライト", className="small"),
                        dcc.Dropdown(
                            id="lv_umap_highlight_cluster",
                            multi=True,
                            placeholder="ハイライトクラスタ",
                        ),
                    ]),
                    dbc.Col(width=2, children=[
                        dbc.Label("マーカーサイズ", className="small"),
                        dcc.Slider(
                            id="lv_umap_marker_size",
                            min=1, max=10, step=1, value=2,
                            marks={1: "1", 5: "5", 10: "10"},
                        ),
                    ]),
                    dbc.Col(width=1, children=[
                        dbc.Checkbox(id="lv_umap_show_labels",
                                     label="ラベル", value=False),
                    ]),
                    dbc.Col(width=1, children=[
                        dbc.Checkbox(id="lv_umap_show_legend",
                                     label="凡例", value=True),
                    ]),
                ]),
                dcc.Loading(
                    dcc.Graph(
                        id="lv_umap_plot",
                        style={"height": "500px"},
                        config={
                            "scrollZoom": True,
                            "toImageButtonOptions": {"format": "png", "scale": 3},
                        },
                    ),
                ),
            ]),

            # --- Volcano（DEG が利用可能な場合のみ表示） ---
            html.Div(id="lv_volcano_section", style={"display": "none"}, children=[
                html.Div(className="card mb-3", children=[
                    html.H5("Volcano Plot"),
                    dbc.Row(className="mb-2", children=[
                        dbc.Col(width=4, children=[
                            dcc.Dropdown(
                                id="lv_volcano_cluster_select",
                                placeholder="クラスタを選択",
                                clearable=True,
                            ),
                        ]),
                    ]),
                    dcc.Loading(
                        dcc.Graph(
                            id="lv_volcano_plot",
                            style={"height": "450px"},
                            config={
                                "scrollZoom": True,
                                "toImageButtonOptions": {"format": "png", "scale": 3},
                            },
                        ),
                    ),
                ]),
            ]),

            # --- Feature Plot ---
            html.Div(className="card mb-3", children=[
                html.H5("Feature Plot"),
                dbc.Row(className="mb-2", children=[
                    dbc.Col(width=4, children=[
                        dcc.Dropdown(
                            id="lv_feature_select",
                            placeholder="Feature (m/z) を選択",
                            searchable=True,
                        ),
                    ]),
                ]),
                dcc.Loading(
                    dcc.Graph(
                        id="lv_feature_plot",
                        style={"height": "400px"},
                        config={"scrollZoom": True},
                    ),
                ),
            ]),

            # --- クラスタ統計 ---
            html.Div(className="card mb-3", children=[
                html.H5("クラスタ統計"),
                dash_table.DataTable(
                    id="lv_cluster_stats_table",
                    sort_action="native",
                    style_table={"overflowX": "auto"},
                    style_cell={"textAlign": "left", "padding": "6px",
                                "fontSize": "0.85rem"},
                    style_header={"backgroundColor": "#f8f9fa",
                                  "fontWeight": "600"},
                    page_size=20,
                ),
            ]),
        ]),
    ])
