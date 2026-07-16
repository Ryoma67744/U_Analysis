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
            dbc.ModalHeader(dbc.ModalTitle("🧪 化合物名アノテーション")),
            dbc.ModalBody(
                dcc.Loading(html.Div(id="annotation_preview_body", className="small")),
            ),
            dbc.ModalFooter(
                dbc.Button("閉じる", id="annotation_preview_close_btn",
                           color="secondary", size="sm"),
            ),
        ],
    )
