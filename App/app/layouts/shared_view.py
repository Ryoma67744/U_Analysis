# =============================================================================
# MSI Analysis Application - Shared View Layout
# 共有専用ページ（読み取り専用）
# =============================================================================

from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc

from app.services.caveats import banner_text as _caveat_banner


def create_shared_view_layout():
    """共有リンクからアクセスした際の読み取り専用ページ"""
    return html.Div([
        # --- 内部ストア（ファイルパスはサーバー側のみ） ---
        dcc.Store(id="sv_result_dir_store", data=""),
        dcc.Store(id="sv_rds_path_store", data=""),
        dcc.Store(id="sv_integration_method_store", data=""),
        dcc.Store(id="sv_gallery_page_store", data=1),
        dcc.Store(id="sv_clicked_image_store", data=""),
        dcc.Store(id="sv_deg_data_store", data=None),

        # --- ヘッダー ---
        html.Div(className="card mb-3", children=[
            html.Div(
                className="d-flex justify-content-between align-items-center",
                children=[
                    html.Div(
                        style={"display": "flex", "alignItems": "center",
                               "gap": "12px"},
                        children=[
                            html.H4("MSI Analysis — 共有結果",
                                    className="mb-0"),
                            html.Span(
                                "Viewer モード (閲覧専用)",
                                className="badge bg-secondary",
                                style={"fontSize": "0.75rem"},
                            ),
                        ],
                    ),
                    html.Div(
                        style={"display": "flex", "alignItems": "center",
                               "gap": "10px"},
                        children=[
                            html.Span(
                                id="header_analyst_label_shared",
                                className="text-muted small",
                            ),
                            html.A(
                                "❓ ヘルプ",
                                href="/help/analysis",
                                target="_blank",
                                rel="noopener noreferrer",
                                title="解析結果の見方の取扱説明書を別タブで開く",
                                className="btn btn-outline-info btn-sm",
                            ),
                            html.A(
                                "ログアウト",
                                href="/logout",
                                className="btn btn-outline-secondary btn-sm",
                            ),
                        ],
                    ),
                ],
            ),
            html.Div(id="sv_share_info",
                     className="text-muted small mt-2"),
            # メタデータカード（プロジェクト名・サブプロジェクト名・統合手法・解析日時）
            html.Div(id="sv_metadata_card", className="mt-2"),
            html.Div(id="sv_share_error", style={"display": "none"},
                     className="alert alert-danger mt-2"),
        ]),

        # --- メインコンテンツ（トークン検証後に表示） ---
        html.Div(id="sv_content", style={"display": "none"}, children=[
            dbc.Tabs(
                id="sv_tabs",
                active_tab="sv_results",
                children=[
                    # ========== 結果ギャラリータブ ==========
                    dbc.Tab(label="結果ギャラリー", tab_id="sv_results", children=[
                        html.Div(style={"marginTop": "15px"}, children=[
                            html.Div(className="card", children=[
                                # フィルタ行
                                dbc.Row(className="mb-3", children=[
                                    dbc.Col(width=3, children=[
                                        dbc.Label("サブフォルダ"),
                                        dcc.Dropdown(id="sv_subfolder_selector",
                                                     placeholder="サブフォルダ"),
                                    ]),
                                    dbc.Col(width=3, children=[
                                        dbc.Label("カテゴリ"),
                                        dcc.Dropdown(
                                            id="sv_image_category",
                                            options=[
                                                {"label": "すべて", "value": "all"},
                                                {"label": "UMAP", "value": "UMAP"},
                                                {"label": "Volcano", "value": "Volcano"},
                                                {"label": "MSI", "value": "MSI"},
                                                {"label": "Spatial", "value": "Spatial"},
                                                {"label": "Heatmap", "value": "Heatmap"},
                                            ],
                                            value="all",
                                        ),
                                    ]),
                                    dbc.Col(width=3, children=[
                                        dbc.Label("クラスタ"),
                                        dcc.Dropdown(id="sv_cluster_selector",
                                                     placeholder="クラスタでフィルタ"),
                                    ]),
                                ]),

                                # 画像ギャラリー
                                html.Div(id="sv_image_gallery", className="image-gallery"),

                                # ページネーション
                                html.Div(
                                    style={"display": "flex", "justifyContent": "center",
                                           "gap": "10px", "marginTop": "15px"},
                                    children=[
                                        dbc.Button("< 前へ", id="sv_prev_page",
                                                   size="sm", outline=True, color="primary"),
                                        html.Span(id="sv_page_info",
                                                  style={"alignSelf": "center"}),
                                        dbc.Button("次へ >", id="sv_next_page",
                                                   size="sm", outline=True, color="primary"),
                                    ],
                                ),
                            ]),
                        ]),
                    ]),

                    # ========== インタラクティブ解析タブ ==========
                    dbc.Tab(label="インタラクティブ解析", tab_id="sv_interactive", children=[
                        html.Div(style={"marginTop": "15px"}, children=[
                            # データ読み込み状態
                            html.Div(id="sv_data_info", className="text-muted small mb-2"),

                            # --- UMAP セクション ---
                            html.Div(className="card mb-3", children=[
                                html.H5("UMAP"),
                                dbc.Row(className="mb-2 align-items-center", children=[
                                    dbc.Col(width=2, children=[
                                        dbc.Label("色分け", className="small"),
                                        dbc.RadioItems(
                                            id="sv_umap_color_by",
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
                                            id="sv_umap_highlight_cluster",
                                            multi=True,
                                            placeholder="ハイライトクラスタ",
                                        ),
                                    ]),
                                    dbc.Col(width=2, children=[
                                        dbc.Label("マーカーサイズ", className="small"),
                                        dcc.Slider(
                                            id="sv_umap_marker_size",
                                            min=1, max=10, step=1, value=2,
                                            marks={1: "1", 5: "5", 10: "10"},
                                        ),
                                    ]),
                                    dbc.Col(width=1, children=[
                                        dbc.Checkbox(id="sv_umap_show_labels",
                                                     label="ラベル", value=False),
                                    ]),
                                    dbc.Col(width=1, children=[
                                        dbc.Checkbox(id="sv_umap_show_legend",
                                                     label="凡例", value=True),
                                    ]),
                                ]),
                                dcc.Loading(
                                    dcc.Graph(id="sv_umap_plot",
                                              style={"height": "450px"},
                                              config={"scrollZoom": True}),
                                ),
                            ]),

                            # --- Spatial Mapping セクション ---
                            html.Div(className="card mb-3", children=[
                                html.H5("Spatial Mapping"),
                                dbc.Row(className="mb-2 align-items-center", children=[
                                    dbc.Col(width=3, children=[
                                        dbc.Label("ハイライト", className="small"),
                                        dcc.Dropdown(
                                            id="sv_spatial_highlight_cluster",
                                            multi=True,
                                            placeholder="ハイライトクラスタ",
                                        ),
                                    ]),
                                    dbc.Col(width=3, children=[
                                        dbc.Label("サンプル", className="small"),
                                        dcc.Dropdown(
                                            id="sv_spatial_sample",
                                            placeholder="サンプル（空=全表示）",
                                            clearable=True,
                                        ),
                                    ]),
                                ]),
                                dcc.Loading(
                                    html.Div(id="sv_spatial_container"),
                                ),
                            ]),

                            # --- Feature Plot セクション ---
                            html.Div(className="card mb-3", children=[
                                html.H5("Feature Plot"),
                                dbc.Row(className="mb-2", children=[
                                    dbc.Col(width=4, children=[
                                        dcc.Dropdown(
                                            id="sv_feature_select",
                                            placeholder="Feature (m/z or gene) を選択",
                                            searchable=True,
                                        ),
                                    ]),
                                ]),
                                dcc.Loading(
                                    dcc.Graph(id="sv_feature_plot",
                                              style={"height": "400px"},
                                              config={"scrollZoom": True}),
                                ),
                            ]),

                            # --- クラスタ統計 ---
                            html.Div(className="card mb-3", children=[
                                html.H5("クラスタ統計"),
                                dash_table.DataTable(
                                    id="sv_cluster_stats_table",
                                    sort_action="native",
                                    style_table={"overflowX": "auto"},
                                    style_cell={"textAlign": "left", "padding": "6px",
                                                "fontSize": "0.85rem"},
                                    style_header={"backgroundColor": "#f8f9fa",
                                                  "fontWeight": "600"},
                                    page_size=20,
                                ),
                            ]),

                            # --- DEG 結果 ---
                            html.Div(id="sv_deg_section", style={"display": "none"},
                                     children=[
                                dbc.Alert(_caveat_banner("ja"), color="warning",
                                          className="py-2 mb-2 small"),
                                # Volcano Plot（クラスタ選択 + ホバーで化合物名表示）
                                html.Div(className="card mb-3", children=[
                                    html.H5("Volcano Plot"),
                                    dbc.Row(className="mb-2", children=[
                                        dbc.Col(width=4, children=[
                                            dcc.Dropdown(
                                                id="sv_volcano_cluster_select",
                                                placeholder="クラスタを選択",
                                                clearable=True,
                                            ),
                                        ]),
                                    ]),
                                    dcc.Loading(
                                        dcc.Graph(
                                            id="sv_volcano_plot",
                                            style={"height": "450px"},
                                            config={
                                                "scrollZoom": True,
                                                "toImageButtonOptions": {
                                                    "format": "png", "scale": 3,
                                                },
                                            },
                                        ),
                                    ),
                                ]),

                                html.Div(className="card mb-3", children=[
                                    html.H5("DEG マーカー"),
                                    dash_table.DataTable(
                                        id="sv_deg_table",
                                        columns=[
                                            {"name": "Gene/m/z", "id": "gene"},
                                            {"name": "Cluster", "id": "cluster"},
                                            {"name": "avg_log2FC", "id": "avg_log2FC"},
                                            {"name": "p_val_adj", "id": "p_val_adj"},
                                            {"name": "pct.1", "id": "pct.1"},
                                            {"name": "pct.2", "id": "pct.2"},
                                        ],
                                        sort_action="native",
                                        filter_action="native",
                                        style_table={"overflowX": "auto"},
                                        style_cell={"textAlign": "left", "padding": "6px",
                                                    "fontSize": "0.85rem"},
                                        style_header={"backgroundColor": "#f8f9fa",
                                                      "fontWeight": "600"},
                                        page_size=50,
                                    ),
                                ]),
                            ]),
                        ]),
                    ]),
                ],
            ),
        ]),

        # 画像プレビューモーダル
        dbc.Modal(
            id="sv_image_modal",
            size="xl",
            centered=True,
            children=[
                dbc.ModalHeader(dbc.ModalTitle("画像プレビュー")),
                dbc.ModalBody(id="sv_modal_body",
                              className="image-preview-body"),
            ],
        ),

        # ダウンロード用
        dcc.Download(id="sv_download"),
    ])
