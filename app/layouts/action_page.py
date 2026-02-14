# =============================================================================
# MSI Analysis Application - Sub-Project List Page
# サブプロジェクト一覧ページ（旧アクション選択ページ）
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc


def create_action_page():
    """プロジェクト選択後のサブプロジェクト一覧ページ"""
    return html.Div(
        id="page_action",
        style={"display": "none"},
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

            dbc.Container(
                fluid=True,
                className="mt-3",
                children=[
                    # 戻るボタン + プロジェクト名
                    dbc.Row(
                        className="mb-3",
                        children=[
                            dbc.Col(
                                width=12,
                                children=[
                                    dbc.Button(
                                        "< プロジェクト一覧に戻る",
                                        id="back_to_landing",
                                        color="link",
                                        className="ps-0",
                                    ),
                                    html.H3(
                                        id="action_page_project_name",
                                        className="mt-2",
                                    ),
                                    html.P(
                                        id="action_page_project_description",
                                        className="text-muted",
                                    ),
                                ],
                            ),
                        ],
                    ),

                    # サブプロジェクト管理ヘッダー
                    dbc.Row(
                        className="mb-3 align-items-center",
                        children=[
                            dbc.Col(
                                width=3,
                                children=[
                                    html.H4("サブプロジェクト（測定）一覧"),
                                ],
                            ),
                            dbc.Col(
                                width=3,
                                children=[
                                    dbc.Input(
                                        id="sub_project_search",
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
                                        id="sub_project_sort_order",
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
                                        "+ 新規サブプロジェクト",
                                        id="open_create_sub_project_modal",
                                        color="primary",
                                    ),
                                ],
                            ),
                        ],
                    ),

                    # サブプロジェクトカード一覧（コールバックで動的レンダリング）
                    html.Div(id="sub_project_cards_container"),
                ],
            ),

            # 新規サブプロジェクト作成モーダル
            _create_new_sub_project_modal(),

            # サブプロジェクト編集モーダル
            _create_edit_sub_project_modal(),

            # サブプロジェクト削除確認モーダル
            _create_sub_delete_confirm_modal(),
        ],
    )


def _create_new_sub_project_modal():
    """新規サブプロジェクト作成モーダル"""
    return dbc.Modal(
        id="create_sub_project_modal",
        centered=True,
        size="lg",
        children=[
            dbc.ModalHeader(dbc.ModalTitle("新規サブプロジェクト作成")),
            dbc.ModalBody([
                dbc.Row([
                    dbc.Col(width=6, children=[
                        dbc.Label("タイトル *"),
                        dbc.Input(
                            id="new_sub_name",
                            placeholder="例: 測定1",
                        ),
                    ]),
                    dbc.Col(width=6, children=[
                        dbc.Label("実験日"),
                        dbc.Input(
                            id="new_sub_experiment_date",
                            type="date",
                        ),
                    ]),
                ], className="mb-3"),

                dbc.Row([
                    dbc.Col(width=6, children=[
                        dbc.Label("対象化合物"),
                        dbc.Input(
                            id="new_sub_target_compound",
                            placeholder="例: リン脂質",
                        ),
                    ]),
                    dbc.Col(width=6, children=[
                        dbc.Label("使用MS"),
                        dbc.Select(
                            id="new_sub_ms_instrument",
                            options=[
                                {"label": "-- 選択してください --", "value": ""},
                                {"label": "TIMS", "value": "TIMS"},
                                {"label": "DESI", "value": "DESI"},
                                {"label": "LTQ", "value": "LTQ"},
                                {"label": "その他", "value": "Other"},
                            ],
                            value="",
                        ),
                    ]),
                ], className="mb-3"),

                dbc.Row([
                    dbc.Col(width=12, children=[
                        dbc.Label("極性"),
                        dbc.Checklist(
                            id="new_sub_polarity",
                            options=[
                                {"label": "Cation", "value": "Cation"},
                                {"label": "Anion", "value": "Anion"},
                                {"label": "Both", "value": "Both"},
                            ],
                            value=[],
                            inline=True,
                            className="mt-1",
                        ),
                    ]),
                ], className="mb-3"),

                dbc.Row([
                    dbc.Col(width=6, children=[
                        dbc.Label("データフォルダ"),
                        dbc.Input(
                            id="new_sub_data_folder",
                            placeholder="データフォルダのパス（任意）",
                        ),
                    ]),
                    dbc.Col(width=6, children=[
                        dbc.Label("出力先フォルダ"),
                        dbc.Input(
                            id="new_sub_output_dir",
                            placeholder="出力先フォルダのパス（任意）",
                        ),
                    ]),
                ], className="mb-3"),

                dbc.Label("メモ"),
                dbc.Textarea(
                    id="new_sub_memo",
                    placeholder="メモ（任意）",
                    style={"height": "80px"},
                ),
            ]),
            dbc.ModalFooter([
                dbc.Button(
                    "キャンセル",
                    id="cancel_create_sub_project",
                    color="secondary",
                ),
                dbc.Button(
                    "作成",
                    id="confirm_create_sub_project",
                    color="primary",
                ),
            ]),
        ],
    )


def _create_edit_sub_project_modal():
    """サブプロジェクト編集モーダル"""
    return dbc.Modal(
        id="edit_sub_project_modal",
        centered=True,
        size="lg",
        children=[
            dbc.ModalHeader(dbc.ModalTitle("サブプロジェクト編集")),
            dbc.ModalBody([
                dbc.Row([
                    dbc.Col(width=6, children=[
                        dbc.Label("タイトル *"),
                        dbc.Input(
                            id="edit_sub_name",
                            placeholder="タイトル",
                        ),
                    ]),
                    dbc.Col(width=6, children=[
                        dbc.Label("実験日"),
                        dbc.Input(
                            id="edit_sub_experiment_date",
                            type="date",
                        ),
                    ]),
                ], className="mb-3"),

                dbc.Row([
                    dbc.Col(width=6, children=[
                        dbc.Label("対象化合物"),
                        dbc.Input(
                            id="edit_sub_target_compound",
                            placeholder="対象化合物",
                        ),
                    ]),
                    dbc.Col(width=6, children=[
                        dbc.Label("使用MS"),
                        dbc.Select(
                            id="edit_sub_ms_instrument",
                            options=[
                                {"label": "-- 選択してください --", "value": ""},
                                {"label": "TIMS", "value": "TIMS"},
                                {"label": "DESI", "value": "DESI"},
                                {"label": "LTQ", "value": "LTQ"},
                                {"label": "その他", "value": "Other"},
                            ],
                            value="",
                        ),
                    ]),
                ], className="mb-3"),

                dbc.Row([
                    dbc.Col(width=12, children=[
                        dbc.Label("極性"),
                        dbc.Checklist(
                            id="edit_sub_polarity",
                            options=[
                                {"label": "Cation", "value": "Cation"},
                                {"label": "Anion", "value": "Anion"},
                                {"label": "Both", "value": "Both"},
                            ],
                            value=[],
                            inline=True,
                            className="mt-1",
                        ),
                    ]),
                ], className="mb-3"),

                dbc.Row([
                    dbc.Col(width=6, children=[
                        dbc.Label("データフォルダ"),
                        dbc.Input(
                            id="edit_sub_data_folder",
                            placeholder="データフォルダのパス",
                        ),
                    ]),
                    dbc.Col(width=6, children=[
                        dbc.Label("出力先フォルダ"),
                        dbc.Input(
                            id="edit_sub_output_dir",
                            placeholder="出力先フォルダのパス",
                        ),
                    ]),
                ], className="mb-3"),

                dbc.Label("メモ"),
                dbc.Textarea(
                    id="edit_sub_memo",
                    placeholder="メモ（任意）",
                    style={"height": "80px"},
                ),
            ]),
            dbc.ModalFooter([
                dbc.Button(
                    "キャンセル",
                    id="cancel_edit_sub_project",
                    color="secondary",
                ),
                dbc.Button(
                    "保存",
                    id="confirm_edit_sub_project",
                    color="primary",
                ),
            ]),
        ],
    )


def _create_sub_delete_confirm_modal():
    """サブプロジェクト削除確認モーダル"""
    return dbc.Modal(
        id="delete_sub_project_modal",
        centered=True,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("サブプロジェクト削除確認")),
            dbc.ModalBody(
                "このサブプロジェクトを削除しますか？"
                "（解析データは削除されません）"
            ),
            dbc.ModalFooter([
                dbc.Button(
                    "キャンセル",
                    id="cancel_delete_sub_project",
                    color="secondary",
                ),
                dbc.Button(
                    "削除",
                    id="confirm_delete_sub_project",
                    color="danger",
                ),
            ]),
        ],
    )
