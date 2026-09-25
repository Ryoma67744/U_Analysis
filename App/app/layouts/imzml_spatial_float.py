"""imzML 座標配置を必要時だけ表示するモデルレスfloat。"""
from dash import dcc, html, dash_table
import dash_bootstrap_components as dbc


def create_imzml_spatial_float(scope="initial"):
    suffix = "" if scope == "initial" else "_reanalysis"
    panel_id = "imzml_spatial_float" + suffix
    return html.Div([
        dcc.Store(id="imzml_spatial_overrides" + suffix, data={}),
        dcc.Store(id="imzml_spatial_active_path" + suffix, data=""),
        dcc.Store(id="imzml_spatial_draft" + suffix, data=None),
        html.Div(
            id=panel_id,
            className="imzml-spatial-float",
            style={"display": "none"},
            children=[
                html.Div(className="imzml-spatial-float-header", children=[
                    html.Div(id="imzml_spatial_title" + suffix,
                             children="imzML切片配置", className="fw-semibold text-truncate"),
                    html.Div(className="imzml-spatial-float-window-buttons", children=[
                        dbc.Button("－", id="imzml_spatial_minimize" + suffix,
                                   size="sm", color="link", n_clicks=0,
                                   title="最小化／復元"),
                        dbc.Button("×", id="imzml_spatial_close" + suffix,
                                   size="sm", color="link", n_clicks=0,
                                   title="未適用の変更を破棄して閉じる"),
                    ]),
                ]),
                html.Div(className="imzml-spatial-float-body", children=[
                    html.Div("x–y測定座標のみを表示します。イオン強度画像ではありません。",
                             className="small text-muted mb-1"),
                    html.Div(id="imzml_spatial_message" + suffix,
                             className="small mb-1"),
                    dcc.Graph(id="imzml_spatial_graph" + suffix,
                              className="imzml-spatial-graph",
                              figure={"data": [], "layout": {}},
                              config={"displayModeBar": False, "scrollZoom": True,
                                      "responsive": True}),
                    dash_table.DataTable(
                        id="imzml_spatial_table" + suffix,
                        columns=[
                            {"name": "切片", "id": "section_display_name"},
                            {"name": "成分", "id": "component_count"},
                            {"name": "pixels", "id": "pixel_count", "type": "numeric"},
                            {"name": "解析", "id": "selected"},
                            {"name": "section_id", "id": "section_id"},
                        ],
                        hidden_columns=["section_id"], data=[],
                        row_selectable="multi", selected_rows=[], editable=False,
                        style_table={"overflowX": "auto", "maxHeight": "150px", "overflowY": "auto"},
                        style_cell={"fontSize": "12px", "padding": "5px", "textAlign": "left",
                                    "whiteSpace": "nowrap", "maxWidth": "180px",
                                    "overflow": "hidden", "textOverflow": "ellipsis"},
                    ),
                    html.Div(className="d-flex flex-wrap gap-1 mt-2", children=[
                        dbc.Button("選択行を対象／除外", id="imzml_spatial_toggle" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0),
                        dbc.Button("選択切片を結合", id="imzml_spatial_merge" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0),
                        dbc.Button("すべて1切片", id="imzml_spatial_one" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0),
                        dbc.Button("自動検出へ戻す", id="imzml_spatial_reset" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0),
                    ]),
                    html.Div(className="d-flex justify-content-end gap-2 mt-2", children=[
                        dbc.Button("閉じる", id="imzml_spatial_cancel" + suffix,
                                   size="sm", color="secondary", n_clicks=0),
                        dbc.Button("適用", id="imzml_spatial_apply" + suffix,
                                   size="sm", color="primary", n_clicks=0),
                    ]),
                ]),
                html.Div(className="imzml-spatial-resize-handle", title="ドラッグしてサイズ変更"),
            ],
        ),
    ])
