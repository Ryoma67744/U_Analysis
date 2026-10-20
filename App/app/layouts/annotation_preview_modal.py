# =============================================================================
# MSI Analysis Application - Annotation Preview Modal
# 登録済みデータの「化合物名（注釈）の有無・件数・例」を、生データを開かずに表示する。
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc


def create_annotation_preview_modal():
    """化合物名アノテーションのプレビュー・モーダル。

    本体は annotation_preview_callbacks が `annotation_preview_body` に注入する。
    """
    return dbc.Modal(
        id="annotation_preview_modal",
        is_open=False,
        size="lg",
        centered=True,
        scrollable=True,
        children=[
            # クリックされたサブプロジェクト（project_id/sub_id）を後追いの
            # populate コールバックへ渡す。オープンは即時・重い判定は populate 側。
            dcc.Store(id="annotation_preview_target"),
            dcc.Store(id="annotation_preview_ready"),
            dcc.Store(id="annotation_export_result"),
            dcc.Store(id="annotation_export_busy", data=False),
            dcc.Download(id="annotation_export_download"),
            dbc.ModalHeader(dbc.ModalTitle("🧪 化合物名アノテーション")),
            dbc.ModalBody(
                dcc.Loading(
                    [
                        dcc.Store(id="annotation_preview_result"),
                        html.Div(id="annotation_preview_body", className="small"),
                    ],
                    target_components={"annotation_preview_result": "data"},
                ),
            ),
            dbc.ModalFooter([
                html.Div([
                    html.Label("ファイル形式", htmlFor="annotation_export_format",
                               className="small me-2"),
                    dcc.Dropdown(
                        id="annotation_export_format",
                        options=[
                            {"label": "TXT（1行に1化合物名）", "value": "txt"},
                            {"label": "CSV（化合物名1列）", "value": "csv"},
                        ],
                        value="txt", clearable=False, searchable=False,
                        style={"minWidth": "230px"},
                    ),
                    dcc.Checklist(
                        id="annotation_export_unique",
                        options=[{"label": " 同じ化合物名の重複を除く", "value": "unique"}],
                        value=["unique"], className="small mt-2",
                    ),
                    html.Div(id="annotation_export_count", className="small mt-2",
                             **{"aria-live": "polite"}),
                    html.Div(id="annotation_export_status", className="small mt-1",
                             role="status", **{"aria-live": "polite"}),
                ], className="me-auto"),
                dbc.Button(
                    "化合物名リストをダウンロード", id="annotation_export_btn",
                    color="primary", size="sm", disabled=True,
                ),
                dbc.Button("閉じる", id="annotation_preview_close_btn",
                           color="secondary", size="sm"),
            ]),
        ],
    )
