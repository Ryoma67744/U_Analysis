# =============================================================================
# MSI Analysis Application - 解剖×クラスタ（H&E オーバーレイ）タブ
# =============================================================================
# インタラクティブ解析で読み込んだ解析(plot_data)を再利用し、個体ごとに H&E を
# アップロード → 対応点で位置合わせ → ポリゴンで解剖領域を指定 → 領域×クラスタを
# 集計・MetaboAnalyst 用にエクスポートする。
#
# フェーズ1（本タブ初期版）: 個体選択 / TIC 表示 / H&E アップロード・表示 / 不透明度。
# 位置合わせ・ポリゴン・エクスポートのコントロールは段階的に追加する（Store は先行設置）。
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc


def create_hne_overlay_tab():
    """解剖×クラスタ（H&E オーバーレイ）タブのレイアウト。"""
    return html.Div(className="p-2", children=[
        html.H5("解剖 × クラスタ（H&E オーバーレイ）", className="mb-1"),
        html.P(
            "インタラクティブ解析で解析を読み込んだ後に使用します。"
            "個体ごとに H&E をアップロードし、対応点で位置合わせ → ポリゴンで解剖領域を指定 → "
            "領域×クラスタを集計・MetaboAnalyst 用にエクスポートします。",
            className="text-muted small",
        ),
        dbc.Row([
            # --- 左: コントロール ---
            dbc.Col(width=3, children=[
                dbc.Label("個体 (Sample)", className="small fw-bold"),
                dcc.Dropdown(id="hne_sample_select",
                             placeholder="（解析を読み込むと表示）", clearable=False),
                html.Div(id="hne_data_status", className="small text-muted mt-1"),

                html.Hr(className="my-2"),
                dbc.Label("H&E 画像アップロード", className="small fw-bold"),
                dcc.Upload(
                    id="hne_image_upload", accept="image/*", multiple=False,
                    children=html.Div(["画像をドロップ または ", html.A("ファイル選択")]),
                    style={"border": "1px dashed #adb5bd", "borderRadius": "6px",
                           "padding": "12px", "textAlign": "center",
                           "fontSize": "0.85rem", "cursor": "pointer"},
                ),
                html.Div(id="hne_upload_info", className="small text-muted mt-1"),

                html.Div(className="mt-3", children=[
                    dbc.Label("H&E 不透明度", className="small fw-bold mb-0"),
                    dcc.Slider(id="hne_opacity", min=0, max=1, step=0.05, value=0.6,
                               marks={0: "0", 0.5: "0.5", 1: "1"},
                               tooltip={"placement": "bottom", "always_visible": False}),
                ]),

                html.Hr(className="my-2"),
                html.Div("※ 位置合わせ・ポリゴン・エクスポートは順次追加します。",
                         className="small text-muted"),
            ]),

            # --- 右: TIC と H&E ビュー ---
            dbc.Col(width=9, children=[
                dbc.Row([
                    dbc.Col(width=6, children=[
                        html.Div("TIC（MSI 空間）", className="small fw-bold text-center"),
                        dcc.Loading(dcc.Graph(
                            id="hne_tic_graph", style={"height": "66vh"},
                            config={"scrollZoom": True, "displaylogo": False})),
                    ]),
                    dbc.Col(width=6, children=[
                        html.Div("H&E", className="small fw-bold text-center"),
                        dcc.Loading(dcc.Graph(
                            id="hne_image_graph", style={"height": "66vh"},
                            config={"scrollZoom": True, "displaylogo": False})),
                    ]),
                ]),
                html.Div(id="hne_result_area", className="mt-2"),
            ]),
        ]),

        # --- Store（段階的に使用） ---
        dcc.Store(id="hne_image_store"),                         # {src, width, height, name}
        dcc.Store(id="hne_landmarks_store", data={"tic": [], "hne": []}),
        dcc.Store(id="hne_affine_store"),                        # M (2x3) list
        dcc.Store(id="hne_polygons_store", data=[]),             # [{name, vertices}]
        dcc.Download(id="hne_export_download"),
    ])
