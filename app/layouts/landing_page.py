# =============================================================================
# MSI Analysis Application - Landing Page (Project Selection)
# プロジェクト選択ランディングページ
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc


def create_landing_page():
    """プロジェクト一覧ランディングページ"""
    return html.Div(
        id="page_landing",
        children=[
            # ヘッダー
            html.Div(
                className="app-header",
                children=[
                    html.H1("MSI Analysis Application"),
                    html.P(
                        className="subtitle",
                        children="質量分析イメージングデータ解析システム",
                    ),
                ],
            ),

            # プロジェクト管理エリア
            dbc.Container(
                fluid=True,
                className="mt-3",
                children=[
                    dbc.Row(
                        className="mb-3 align-items-center",
                        children=[
                            dbc.Col(
                                width=3,
                                children=[
                                    html.H3("プロジェクト一覧"),
                                ],
                            ),
                            dbc.Col(
                                width=3,
                                children=[
                                    dbc.Input(
                                        id="project_search",
                                        type="text",
                                        placeholder="名前で検索...",
                                        size="sm",
                                    ),
                                ],
                            ),
                            dbc.Col(
                                width=3,
                                children=[
                                    dbc.Select(
                                        id="project_sort_order",
                                        options=[
                                            {"label": "更新日 (新しい順)",
                                             "value": "modified_desc"},
                                            {"label": "更新日 (古い順)",
                                             "value": "modified_asc"},
                                            {"label": "名前 (昇順)",
                                             "value": "name_asc"},
                                            {"label": "名前 (降順)",
                                             "value": "name_desc"},
                                            {"label": "作成日 (新しい順)",
                                             "value": "created_desc"},
                                            {"label": "作成日 (古い順)",
                                             "value": "created_asc"},
                                        ],
                                        value="modified_desc",
                                        size="sm",
                                    ),
                                ],
                            ),
                            dbc.Col(
                                width=3,
                                className="text-end",
                                children=[
                                    dbc.Button(
                                        "インタラクティブ解析",
                                        id="open_interactive_from_landing_btn",
                                        color="info",
                                        className="me-2",
                                    ),
                                    dbc.Button(
                                        "+ 新規プロジェクト",
                                        id="open_create_project_modal",
                                        color="primary",
                                    ),
                                ],
                            ),
                        ],
                    ),

                    # プロジェクトカード一覧（コールバックで動的レンダリング）
                    html.Div(id="project_cards_container"),
                ],
            ),

            # 新規プロジェクト作成モーダル
            _create_new_project_modal(),

            # プロジェクト編集モーダル
            _create_edit_project_modal(),

            # 削除確認モーダル
            _create_delete_confirm_modal(),
        ],
    )


def _create_new_project_modal():
    """新規プロジェクト作成モーダル"""
    return dbc.Modal(
        id="create_project_modal",
        centered=True,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("新規プロジェクト作成")),
            dbc.ModalBody([
                dbc.Label("プロジェクトタイトル *"),
                dbc.Input(
                    id="new_project_name",
                    placeholder="例: 大橋プロジェクト",
                ),
                dbc.Label("実験日", className="mt-2"),
                dbc.Input(
                    id="new_project_experiment_date",
                    type="date",
                ),
                dbc.Label("メモ", className="mt-2"),
                dbc.Textarea(
                    id="new_project_memo",
                    placeholder="メモ（任意）",
                    style={"height": "80px"},
                ),
            ]),
            dbc.ModalFooter([
                dbc.Button(
                    "キャンセル", id="cancel_create_project", color="secondary",
                ),
                dbc.Button(
                    "作成", id="confirm_create_project", color="primary",
                ),
            ]),
        ],
    )


def _create_edit_project_modal():
    """プロジェクト編集モーダル"""
    return dbc.Modal(
        id="edit_project_modal",
        centered=True,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("プロジェクト編集")),
            dbc.ModalBody([
                dbc.Label("プロジェクトタイトル *"),
                dbc.Input(
                    id="edit_project_name",
                    placeholder="プロジェクト名",
                ),
                dbc.Label("実験日", className="mt-2"),
                dbc.Input(
                    id="edit_project_experiment_date",
                    type="date",
                ),
                dbc.Label("メモ", className="mt-2"),
                dbc.Textarea(
                    id="edit_project_memo",
                    placeholder="メモ（任意）",
                    style={"height": "80px"},
                ),
            ]),
            dbc.ModalFooter([
                dbc.Button(
                    "キャンセル", id="cancel_edit_project", color="secondary",
                ),
                dbc.Button(
                    "保存", id="confirm_edit_project", color="primary",
                ),
            ]),
        ],
    )


def _create_delete_confirm_modal():
    """削除確認モーダル"""
    return dbc.Modal(
        id="delete_project_modal",
        centered=True,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("プロジェクト削除確認")),
            dbc.ModalBody(
                "このプロジェクトを削除しますか？（解析データは削除されません）"
            ),
            dbc.ModalFooter([
                dbc.Button(
                    "キャンセル", id="cancel_delete_project", color="secondary",
                ),
                dbc.Button(
                    "削除", id="confirm_delete_project", color="danger",
                ),
            ]),
        ],
    )
