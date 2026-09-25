"""imzML 座標配置と切片メタデータを必要時だけ表示するモデルレスfloat。"""
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
                             children="imzML切片配置・切片情報",
                             className="fw-semibold text-truncate"),
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
                    html.Div(
                        "x–y測定座標のみを表示します。イオン強度画像ではありません。"
                        " 切片名・個体ID・群は下表で直接編集できます。",
                        className="small text-muted",
                    ),
                    html.Div(id="imzml_spatial_message" + suffix,
                             className="small"),
                    html.Div(className="imzml-spatial-map-section", children=[
                        dcc.Graph(
                            id="imzml_spatial_graph" + suffix,
                            className="imzml-spatial-graph",
                            figure={"data": [], "layout": {}},
                            config={"displayModeBar": False, "scrollZoom": True,
                                    "responsive": True},
                        ),
                    ]),
                    html.Div(className="imzml-spatial-editor-section", children=[
                        html.Div(className="imzml-spatial-editor-heading", children=[
                            html.Strong("切片情報"),
                            html.Small(
                                "行を選択して結合・除外・群の一括設定を行います。",
                                className="text-muted",
                            ),
                        ]),
                        dash_table.DataTable(
                            id="imzml_spatial_table" + suffix,
                            columns=[
                                {"name": "切片名", "id": "section_display_name", "editable": True},
                                {"name": "個体／独立試料ID", "id": "subject_id", "editable": True},
                                {"name": "群", "id": "group", "editable": True},
                                {"name": "成分", "id": "component_count", "type": "numeric",
                                 "editable": False},
                                {"name": "pixels", "id": "pixel_count", "type": "numeric",
                                 "editable": False},
                                {"name": "解析", "id": "selected", "editable": False},
                                {"name": "section_id", "id": "section_id", "editable": False},
                            ],
                            hidden_columns=["section_id"],
                            data=[],
                            row_selectable="multi",
                            selected_rows=[],
                            editable=True,
                            style_table={
                                "overflowX": "auto",
                                "maxHeight": "240px",
                                "overflowY": "auto",
                                "border": "1px solid #dee2e6",
                            },
                            style_cell={
                                "fontSize": "12px",
                                "padding": "6px",
                                "textAlign": "left",
                                "whiteSpace": "nowrap",
                                "overflow": "hidden",
                                "textOverflow": "ellipsis",
                                "minWidth": "70px",
                            },
                            style_cell_conditional=[
                                {"if": {"column_id": "section_display_name"},
                                 "minWidth": "150px", "width": "190px", "maxWidth": "240px"},
                                {"if": {"column_id": "subject_id"},
                                 "minWidth": "130px", "width": "165px", "maxWidth": "210px"},
                                {"if": {"column_id": "group"},
                                 "minWidth": "100px", "width": "130px", "maxWidth": "170px"},
                                {"if": {"column_id": "component_count"},
                                 "width": "68px", "maxWidth": "68px"},
                                {"if": {"column_id": "pixel_count"},
                                 "width": "88px", "maxWidth": "88px"},
                                {"if": {"column_id": "selected"},
                                 "width": "72px", "maxWidth": "72px"},
                            ],
                            style_data_conditional=[
                                {
                                    "if": {"filter_query": '{selected} = "除外"'},
                                    "opacity": 0.55,
                                    "backgroundColor": "#f1f3f5",
                                },
                            ],
                            css=[{"selector": ".show-hide", "rule": "display: none"}],
                        ),
                        html.Div(className="imzml-spatial-bulk-group", children=[
                            dbc.Input(
                                id="imzml_spatial_bulk_group" + suffix,
                                placeholder="選択行へ一括設定する群名（例：ctrl、KO）",
                                size="sm",
                            ),
                            dbc.Button(
                                "選択行に群を設定",
                                id="imzml_spatial_bulk_apply" + suffix,
                                size="sm",
                                color="secondary",
                                outline=True,
                                n_clicks=0,
                            ),
                        ]),
                    ]),
                    html.Div(className="d-flex flex-wrap gap-1", children=[
                        dbc.Button("選択行を対象／除外", id="imzml_spatial_toggle" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0),
                        dbc.Button("選択切片を結合", id="imzml_spatial_merge" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0),
                        dbc.Button("すべて1切片", id="imzml_spatial_one" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0),
                        dbc.Button("自動検出へ戻す", id="imzml_spatial_reset" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0),
                    ]),
                    html.Div(className="d-flex justify-content-end gap-2", children=[
                        dbc.Button("閉じる", id="imzml_spatial_cancel" + suffix,
                                   size="sm", color="secondary", n_clicks=0),
                        dbc.Button("適用", id="imzml_spatial_apply" + suffix,
                                   size="sm", color="primary", n_clicks=0),
                    ]),
                ]),
                html.Div(className="imzml-spatial-resize-handle",
                         title="ドラッグしてサイズ変更"),
            ],
        ),
    ])
