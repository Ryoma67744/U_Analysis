# =============================================================================
# MSI Analysis Application - Results Tab UI
# 結果閲覧タブUI
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc


def create_results_tab():
    return html.Div(style={"marginTop": "15px"}, children=[
        html.Div(className="card", children=[
            html.H4(className="card-title", children=["🖼 結果閲覧"]),

            # ファイルブラウザからの値を受け取るStore
            dcc.Store(id="result_folder_manual", data=""),

            # フォルダ選択
            dbc.Row(className="mb-3", children=[
                dbc.Col(width=4, children=[
                    dbc.Label("結果フォルダ"),
                    dcc.Dropdown(id="result_folder_selector", placeholder="結果フォルダを選択"),
                ]),
                dbc.Col(width=2, children=[
                    dbc.Label(" "),
                    dbc.Button("参照...", id="browse_result_folder", size="sm", color="secondary",
                               style={"display": "block", "marginTop": "4px"}),
                ]),
                dbc.Col(width=3, children=[
                    dbc.Label("サブフォルダ"),
                    dcc.Dropdown(id="subfolder_selector", placeholder="サブフォルダ"),
                ]),
                dbc.Col(width=3, children=[
                    dbc.Label("カテゴリ"),
                    dcc.Dropdown(
                        id="image_category",
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
            ]),

            # クラスタフィルタ
            dbc.Row(className="mb-3", children=[
                dbc.Col(width=12, children=[
                    dbc.Label("クラスタ"),
                    dcc.Dropdown(id="cluster_selector", placeholder="クラスタでフィルタ"),
                ]),
            ]),

            # 画像ギャラリー
            html.Div(id="image_gallery", className="image-gallery"),

            # ページネーション
            html.Div(
                style={"display": "flex", "justifyContent": "center", "gap": "10px", "marginTop": "15px"},
                children=[
                    dbc.Button("< 前へ", id="prev_page", size="sm", outline=True, color="primary"),
                    html.Span(id="page_info", style={"alignSelf": "center"}),
                    dbc.Button("次へ >", id="next_page", size="sm", outline=True, color="primary"),
                ],
            ),
        ]),

        # 画像プレビューモーダル
        dbc.Modal(
            id="image_modal",
            size="xl",
            centered=True,
            children=[
                dbc.ModalHeader([
                    dbc.ModalTitle("画像プレビュー"),
                    html.Span(id="modal_filename", className="ms-3 text-muted small"),
                    dbc.Button("パスをコピー", id="copy_path_btn", size="sm",
                               outline=True, color="secondary", className="ms-2"),
                ]),
                dbc.ModalBody(
                    id="modal_body",
                    className="image-preview-body",
                ),
            ],
        ),

        # 現在クリックされた画像パスを保持するStore
        dcc.Store(id="clicked_image_store"),
        dcc.Store(id="gallery_page_store", data=1),
    ])
