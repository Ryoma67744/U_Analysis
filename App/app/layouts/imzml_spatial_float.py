"""imzML座標配置と全切片登録情報を必要時だけ表示するモデルレスfloat。"""
from dash import dcc, html, dash_table
import dash_bootstrap_components as dbc


def create_imzml_spatial_float(scope="initial"):
    suffix = {"initial": "", "reanalysis": "_reanalysis", "conversion": "_conversion"}.get(scope)
    if suffix is None:
        raise ValueError(f"未知のimzML float scopeです: {scope}")
    panel_id = "imzml_spatial_float" + suffix
    conversion_mode = scope == "conversion"
    title = ("imzML全切片登録" if conversion_mode
             else "imzML全切片登録・今回の解析選択")
    return html.Div([
        dcc.Store(id="imzml_spatial_overrides" + suffix, data={}),
        dcc.Store(id="imzml_spatial_active_path" + suffix, data=""),
        dcc.Store(id="imzml_spatial_draft" + suffix, data=None),
        # ★ ver74.0: 編集元とdraftを分離し、背景変更との競合を適用前に検出する。
        dcc.Store(id="imzml_spatial_base" + suffix, data=None),
        # ★ ver74.1: 次の未入力セルへの移動先を表示用状態として分離する。
        dcc.Store(id="imzml_spatial_missing_cells" + suffix, data=[]),
        html.Div(
            id=panel_id,
            className="imzml-spatial-float",
            style={"display": "none"},
            children=[
                html.Div(className="imzml-spatial-float-header", children=[
                    html.Div(id="imzml_spatial_title" + suffix,
                             children=title,
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
                    dbc.Alert([
                        html.Div("ParquetにはimzML内の全切片・全pixelを保存します。", className="fw-semibold"),
                        html.Div(
                            ("全切片の切片名・個体／独立試料ID・群・登録確認が必須です。"
                             "解析対象の選択は変換後の解析画面で行います。"
                             if conversion_mode else
                             "今回の解析に使用しない切片も、切片名・個体／独立試料ID・群・登録確認が必須です。"
                             "解析対象のチェックはParquetの内容を削除しません。"),
                            className="small",
                        ),
                    ], color="info", className="py-2 px-2 mb-1"),
                    html.Div(
                        "x–y測定座標のみを表示します。イオン強度画像ではありません。",
                        className="small text-muted",
                    ),
                    html.Div(id="imzml_spatial_message" + suffix, className="small"),
                    html.Div(className="imzml-spatial-editor-section", children=[
                        html.Div(className="imzml-spatial-editor-heading", children=[
                            html.Strong("① 全切片の必須項目を入力"),
                            html.Small(
                                "個体／独立試料IDの例：Mouse01。同じ個体の連続切片には同じIDを入力します。"
                                "不明なIDを推測して付けず、試料情報を確認してください。"
                                "項目自体が当てはまらない場合は「該当なし」と明示します。",
                                className="text-muted",
                            ),
                        ]),
                        html.Div(id="imzml_spatial_guidance" + suffix,
                                 className="imzml-registration-guidance", role="status",
                                 **{"aria-live": "polite"}),
                        dbc.Button("次に入力する項目へ", id="imzml_spatial_next_missing" + suffix,
                                   size="sm", color="danger", outline=True, n_clicks=0,
                                   className="mb-2", disabled=True),
                        dash_table.DataTable(
                            id="imzml_spatial_table" + suffix,
                            columns=[
                                {"name": "切片名", "id": "section_display_name", "editable": True},
                                {"name": "個体／独立試料ID", "id": "subject_id", "editable": True},
                                {"name": "群", "id": "group", "editable": True},
                                # ★ ver74.0: 確認は編集済みdraftを受け取る専用ボタンで行う。
                                # セルの文字だけを「確認済」に変える経路は確認対象を特定できない。
                                {"name": "登録確認", "id": "metadata_confirmed", "editable": False},
                                {"name": "成分", "id": "component_count", "type": "numeric",
                                 "editable": False},
                                {"name": "pixels", "id": "pixel_count", "type": "numeric",
                                 "editable": False},
                                {"name": "今回の解析に使用", "id": "selected", "editable": False},
                                {"name": "section_id", "id": "section_id", "editable": False},
                            ],
                            hidden_columns=["section_id"] + (["selected"] if conversion_mode else []),
                            data=[],
                            row_selectable="multi",
                            selected_rows=[],
                            editable=True,
                            style_table={
                                "overflowX": "auto", "maxHeight": "260px",
                                "overflowY": "auto", "border": "1px solid #dee2e6",
                            },
                            style_cell={
                                # ★ ver74.1: DataTable既定monospaceで日本語が欠けないよう本文系書体を使う。
                                "fontFamily": "sans-serif",
                                "fontSize": "12px", "padding": "6px", "textAlign": "left",
                                "whiteSpace": "nowrap", "overflow": "hidden",
                                "textOverflow": "ellipsis", "minWidth": "70px",
                            },
                            style_cell_conditional=[
                                {"if": {"column_id": "section_display_name"},
                                 "minWidth": "150px", "width": "190px", "maxWidth": "240px"},
                                {"if": {"column_id": "subject_id"},
                                 "minWidth": "130px", "width": "165px", "maxWidth": "210px"},
                                {"if": {"column_id": "group"},
                                 "minWidth": "100px", "width": "130px", "maxWidth": "170px"},
                                {"if": {"column_id": "metadata_confirmed"},
                                 "width": "90px", "maxWidth": "100px"},
                                {"if": {"column_id": "selected"},
                                 "width": "120px", "maxWidth": "130px"},
                            ],
                            style_data_conditional=[
                                {"if": {"filter_query": '{metadata_confirmed} = "未確認"',
                                        "column_id": "metadata_confirmed"},
                                 "backgroundColor": "#fff3cd"},
                            ],
                            tooltip_duration=None,
                            css=[{"selector": ".show-hide", "rule": "display: none"}],
                        ),
                        html.Div(className="imzml-spatial-bulk-group", children=[
                            dbc.Input(
                                id="imzml_spatial_bulk_group" + suffix,
                                placeholder="選択行へ一括設定する群名（例：ctrl、KO）",
                                size="sm",
                            ),
                            dbc.Button(
                                "選択行に群を設定", id="imzml_spatial_bulk_apply" + suffix,
                                size="sm", color="secondary", outline=True, n_clicks=0,
                            ),
                        ]),
                        html.Div(className="imzml-registration-confirm-step", children=[
                            html.Strong("② 入力内容を確認", className="me-2"),
                            html.Small("全切片の必須項目を入力してから押してください。", className="me-2"),
                            dbc.Button(
                                "全切片を確認済みにする", id="imzml_spatial_confirm_all" + suffix,
                                size="sm", color="secondary", outline=True, n_clicks=0,
                            ),
                        ]),
                    ]),
                    # ★ ver74.1: 不足入力を開いてすぐ見える位置に置き、座標図はその下に表示する。
                    html.Div(className="imzml-spatial-map-section", children=[
                        dcc.Graph(
                            id="imzml_spatial_graph" + suffix,
                            className="imzml-spatial-graph",
                            figure={"data": [], "layout": {}},
                            config={"displayModeBar": False, "scrollZoom": True,
                                    "responsive": True},
                        ),
                    ]),
                    html.Div(className="d-flex flex-wrap gap-1", children=[
                        dbc.Button("今回の解析に使用／使用しない", id="imzml_spatial_toggle" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0,
                                   style={"display": "none"} if conversion_mode else {}),
                        dbc.Button("選択切片を結合", id="imzml_spatial_merge" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0),
                        dbc.Button("すべて1切片", id="imzml_spatial_one" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0),
                        dbc.Button("自動検出へ戻す", id="imzml_spatial_reset" + suffix,
                                   size="sm", color="secondary", outline=True, n_clicks=0),
                    ]),
                ]),
                # ★ ver74.1: 図や全切片表をスクロールしても最終操作を見失わない固定footer。
                html.Div(className="imzml-spatial-apply-bar d-flex justify-content-end gap-2", children=[
                    dbc.Button("閉じる", id="imzml_spatial_cancel" + suffix,
                               size="sm", color="secondary", n_clicks=0),
                    dbc.Button("③ 適用", id="imzml_spatial_apply" + suffix,
                               size="sm", color="primary", n_clicks=0),
                ]),
                html.Div(className="imzml-spatial-resize-handle", title="ドラッグしてサイズ変更"),
            ],
        ),
    ])
