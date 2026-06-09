# =============================================================================
# MSI Analysis Application - 解剖×クラスタ（H&E オーバーレイ）タブ
# =============================================================================
# インタラクティブ解析で読み込んだ解析(plot_data)を再利用し、個体ごとに H&E を
# アップロード → 対応点（ランドマーク）で位置合わせ → ポリゴンで解剖領域を指定 →
# 領域×クラスタを集計・MetaboAnalyst 用にエクスポートする。
# =============================================================================

from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc


def create_hne_overlay_tab():
    """解剖×クラスタ（H&E オーバーレイ）タブのレイアウト。"""
    return html.Div(className="p-2", children=[
        html.H5("解剖 × クラスタ（H&E オーバーレイ）", className="mb-1"),
        html.P(
            "インタラクティブ解析で解析を読み込んだ後に使用します。"
            "個体ごとに H&E をアップロード → 対応点で位置合わせ → ポリゴンで解剖領域を指定 → "
            "領域×クラスタを集計・MetaboAnalyst 用にエクスポートします。",
            className="text-muted small",
        ),
        dbc.Row([
            # ===== 左: コントロール =====
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
                           "padding": "10px", "textAlign": "center",
                           "fontSize": "0.85rem", "cursor": "pointer"},
                ),
                html.Div(id="hne_upload_info", className="small text-muted mt-1"),

                html.Hr(className="my-2"),
                dbc.Label("操作モード", className="small fw-bold"),
                dbc.RadioItems(
                    id="hne_mode",
                    options=[
                        {"label": " 対応点（位置合わせ）", "value": "landmark"},
                        {"label": " 領域を描く（ポリゴン）", "value": "polygon"},
                        {"label": " 操作（拡大/移動）", "value": "pan"},
                    ],
                    value="landmark", className="small",
                ),
                html.Div(className="mt-2", children=[
                    dbc.Label("H&E 不透明度", className="small fw-bold mb-0"),
                    dcc.Slider(id="hne_opacity", min=0, max=1, step=0.05, value=0.6,
                               marks={0: "0", 0.5: "0.5", 1: "1"},
                               tooltip={"placement": "bottom", "always_visible": False}),
                ]),

                html.Hr(className="my-2"),
                dbc.Label("MSI 回転（粗い向き合わせ）", className="small fw-bold"),
                html.Div("TIC の向きを H&E に大まかに合わせる。細かい位置は対応点が吸収。",
                         className="small text-muted"),
                dcc.Slider(id="hne_rotation_angle", min=0, max=360, step=1, value=0,
                           marks={0: "0°", 90: "90", 180: "180", 270: "270", 360: "360°"},
                           tooltip={"placement": "bottom", "always_visible": False}),
                dbc.Checklist(
                    id="hne_rotation_flip",
                    options=[{"label": " 左右反転", "value": "flip_h"},
                             {"label": " 上下反転", "value": "flip_v"}],
                    value=[], inline=True, className="small mt-1",
                ),
                html.Div("※回転・反転を変えると対応点はクリアされます。",
                         className="small text-muted"),

                html.Hr(className="my-2"),
                dbc.Label("① 位置合わせ（対応点）", className="small fw-bold"),
                html.Div("「対応点」モードで、TIC と H&E に対応する点を同じ順番で交互にクリック"
                         "（3点以上）。", className="small text-muted"),
                html.Div(id="hne_landmark_info", className="small mt-1"),
                dbc.Button("対応点をクリア", id="hne_landmark_clear", size="sm",
                           color="outline-secondary", className="mt-1"),

                html.Hr(className="my-2"),
                dbc.Label("② 領域（ポリゴン）", className="small fw-bold"),
                html.Div("「領域を描く（ポリゴン）」モードで H&E 上をクリックして頂点を順に置き、"
                         "「領域を確定」で閉じます。下表で名前変更・行削除ができます。",
                         className="small text-muted"),
                html.Div(id="hne_polygon_draft_info", className="small mt-1"),
                dbc.ButtonGroup([
                    dbc.Button("頂点を取り消し", id="hne_polygon_undo", size="sm",
                               color="outline-secondary"),
                    dbc.Button("下書きクリア", id="hne_polygon_clear_draft", size="sm",
                               color="outline-secondary"),
                    dbc.Button("領域を確定", id="hne_polygon_commit", size="sm",
                               color="outline-primary"),
                ], className="mt-1 w-100"),
                dash_table.DataTable(
                    id="hne_polygon_table",
                    columns=[{"name": "#", "id": "idx", "editable": False},
                             {"name": "領域名", "id": "name", "editable": True},
                             {"name": "頂点数", "id": "nv", "editable": False}],
                    data=[], editable=True, row_deletable=True,
                    style_cell={"fontSize": "0.8rem", "padding": "2px"},
                    style_table={"maxHeight": "160px", "overflowY": "auto", "marginTop": "6px"},
                ),
                dbc.Button("③ 領域を spot に割当 → 集計", id="hne_assign_btn",
                           size="sm", color="primary", className="mt-2 w-100"),

                html.Hr(className="my-2"),
                dbc.Button("④ MetaboAnalyst 用 CSV 出力", id="hne_export_btn",
                           size="sm", color="success", className="w-100"),
                html.Div(id="hne_export_info", className="small text-muted mt-1"),
            ]),

            # ===== 右: TIC と H&E ビュー + 結果 =====
            dbc.Col(width=9, children=[
                dbc.Row([
                    dbc.Col(width=6, children=[
                        html.Div("TIC（MSI 空間）", className="small fw-bold text-center"),
                        dcc.Loading(dcc.Graph(
                            id="hne_tic_graph", style={"height": "60vh"},
                            config={"scrollZoom": True, "displaylogo": False})),
                    ]),
                    dbc.Col(width=6, children=[
                        html.Div("H&E", className="small fw-bold text-center"),
                        dcc.Loading(dcc.Graph(
                            id="hne_image_graph", style={"height": "60vh"},
                            config={"scrollZoom": True, "displaylogo": False})),
                    ]),
                ]),
                dcc.Loading(html.Div(id="hne_result_area", className="mt-2")),
            ]),
        ]),

        # ===== Store =====
        dcc.Store(id="hne_image_store"),                         # {src, width, height, name}
        dcc.Store(id="hne_landmarks_store", data={"tic": [], "hne": []}),
        dcc.Store(id="hne_rotation_store",                       # {"angle":0,"flip_h":False,"flip_v":False}
                  data={"angle": 0, "flip_h": False, "flip_v": False}),
        dcc.Store(id="hne_affine_store"),                        # {"M": [[...],[...]], "rms": float}
        dcc.Store(id="hne_polygons_store", data=[]),             # [{name, vertices(px)}]
        dcc.Store(id="hne_polygon_draft_store", data=[]),        # 下書き頂点 [[x,y],...]
        dcc.Download(id="hne_export_download"),
    ])
