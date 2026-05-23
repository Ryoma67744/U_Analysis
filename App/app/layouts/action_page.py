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
                    # ver3.16: クリックでプロジェクト一覧 (landing) に戻れるよう
                    # ボタン化 (id を _action 接尾辞で別にして DOM 重複回避)
                    dbc.Button(
                        html.H1("MSI Analysis Application",
                                className="m-0 p-0"),
                        id="header_title_home_btn_action",
                        color="link",
                        className="p-0 border-0 text-decoration-none",
                        style={"color": "inherit", "textAlign": "left"},
                        title="プロジェクト一覧に戻る",
                    ),
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

                    # 共有リンク管理セクション
                    html.Hr(className="my-4"),
                    html.Div(id="share_links_section", children=[
                        html.H5("共有リンク管理"),
                        html.Div(id="share_links_container",
                                 className="text-muted small",
                                 children="共有リンクはありません"),
                    ]),

                    # ver3.17: プロジェクト関連情報 (URL 3 種 + memo)
                    # 編集可能、保存ボタンで更新。フォントサイズも拡大。
                    html.Hr(className="my-4"),
                    html.Div(id="project_info_section", children=[
                        html.H5("プロジェクト関連情報"),
                        html.Div(
                            id="project_info_container",
                            style={"fontSize": "1rem"},
                            children=[
                                # 3 つの URL 入力欄
                                dbc.InputGroup([
                                    dbc.InputGroupText(
                                        "📝 Google Keep",
                                        style={"minWidth": "150px",
                                               "fontSize": "0.95rem"},
                                    ),
                                    dbc.Input(
                                        id="project_info_google_keep_url",
                                        placeholder="https://keep.google.com/...",
                                        type="url",
                                        style={"fontSize": "0.95rem"},
                                    ),
                                ], className="mb-2"),
                                dbc.InputGroup([
                                    dbc.InputGroupText(
                                        "🔗 MSI Share",
                                        style={"minWidth": "150px",
                                               "fontSize": "0.95rem"},
                                    ),
                                    dbc.Input(
                                        id="project_info_msi_share_url",
                                        placeholder="https://...",
                                        type="url",
                                        style={"fontSize": "0.95rem"},
                                    ),
                                ], className="mb-2"),
                                dbc.InputGroup([
                                    dbc.InputGroupText(
                                        "🌐 Other",
                                        style={"minWidth": "150px",
                                               "fontSize": "0.95rem"},
                                    ),
                                    dbc.Input(
                                        id="project_info_other_url",
                                        placeholder="https://...",
                                        type="url",
                                        style={"fontSize": "0.95rem"},
                                    ),
                                ], className="mb-2"),
                                # メモ
                                dbc.Label("📋 メモ", className="mt-2 fw-bold",
                                          style={"fontSize": "0.95rem"}),
                                dbc.Textarea(
                                    id="project_info_memo",
                                    placeholder="メモ（任意）",
                                    style={"height": "100px",
                                           "fontSize": "0.95rem"},
                                ),
                                # 保存ボタン + 状態
                                html.Div(
                                    className="mt-3 d-flex align-items-center gap-3",
                                    children=[
                                        dbc.Button(
                                            "💾 保存",
                                            id="project_info_save_btn",
                                            color="primary",
                                            size="sm",
                                        ),
                                        html.Span(
                                            id="project_info_status",
                                            className="small text-muted",
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ]),
                ],
            ),

            # 新規サブプロジェクト作成モーダル
            _create_new_sub_project_modal(),

            # サブプロジェクト編集モーダル
            _create_edit_sub_project_modal(),

            # サブプロジェクト削除確認モーダル
            _create_sub_delete_confirm_modal(),

            # 共有リンク作成モーダル
            _create_share_modal(),

            # 共有リンク削除確認モーダル
            _create_share_delete_modal(),
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
                    dbc.Col(width=6, children=[
                        dbc.Label("マトリックス"),
                        dbc.Select(
                            id="new_sub_matrix",
                            options=[
                                {"label": "-- 選択してください --", "value": ""},
                                {"label": "DHB", "value": "DHB"},
                                {"label": "CHCA", "value": "CHCA"},
                                {"label": "9AA", "value": "9AA"},
                                {"label": "Other", "value": "Other"},
                            ],
                            value="",
                        ),
                    ]),
                    dbc.Col(width=6, children=[
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
                    dbc.Col(width=6, children=[
                        dbc.Label("マトリックス"),
                        dbc.Select(
                            id="edit_sub_matrix",
                            options=[
                                {"label": "-- 選択してください --", "value": ""},
                                {"label": "DHB", "value": "DHB"},
                                {"label": "CHCA", "value": "CHCA"},
                                {"label": "9AA", "value": "9AA"},
                                {"label": "Other", "value": "Other"},
                            ],
                            value="",
                        ),
                    ]),
                    dbc.Col(width=6, children=[
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


def _create_share_modal():
    """共有リンク作成モーダル"""
    return dbc.Modal(
        id="share_create_modal",
        centered=True,
        size="lg",
        children=[
            dbc.ModalHeader(dbc.ModalTitle("共有リンク作成")),
            dbc.ModalBody([
                # 共有対象サブプロジェクトID（非表示ストア）
                dcc.Store(id="share_target_sub_id", data=""),

                html.Div(id="share_target_info", className="mb-3"),

                # 共有方式の選択 (期間付き = Tier B 認証 / 無期限 = 認証不要)
                dbc.Label("共有方式", className="fw-bold"),
                dbc.RadioItems(
                    id="share_kind_radio",
                    options=[
                        {
                            "label": "期間付き共有（共有先は共有パスワードでのログインが必要）",
                            "value": "expiring",
                        },
                        {
                            "label": "無期限共有（URL を知る人全員が認証なしで操作可。注意!）",
                            "value": "persistent",
                        },
                    ],
                    value="expiring",
                    className="mb-3",
                ),

                # 統合手法 (両方の共有方式で必要)
                dbc.Row([
                    # 期間付き共有のときだけ有効期限を表示する Col (wrap で制御)
                    dbc.Col(
                        id="share_expiry_wrapper",
                        width=6,
                        children=[
                            dbc.Label("有効期限"),
                            dbc.Select(
                                id="share_expiry_days",
                                options=[
                                    {"label": "7日", "value": "7"},
                                    {"label": "14日", "value": "14"},
                                    {"label": "30日（デフォルト）", "value": "30"},
                                    {"label": "90日", "value": "90"},
                                ],
                                value="30",
                            ),
                        ],
                    ),
                    dbc.Col(width=6, children=[
                        dbc.Label("統合手法"),
                        dbc.Select(
                            id="share_integration_method",
                            options=[
                                {"label": "Harmony", "value": "Harmony"},
                                {"label": "RPCA", "value": "RPCA"},
                                {"label": "PCA", "value": "PCA"},
                            ],
                            value="Harmony",
                        ),
                    ]),
                ], className="mb-3"),

                # 無期限共有時の警告
                html.Div(
                    id="share_persistent_warning",
                    style={"display": "none"},
                    children=[
                        dbc.Alert(
                            "⚠ 無期限共有は URL を知る人すべてが認証なしで"
                            "インタラクティブ解析を操作でき、色変更・クラスタ"
                            "マージ・ラベル編集などの変更は元プロジェクトに"
                            "保存されます。URL は token_urlsafe(16) で生成され"
                            "推測は困難ですが、メール本文や公開リポジトリへの"
                            "誤投稿など URL 漏洩に十分注意してください。",
                            color="warning",
                            className="mb-3 small",
                        ),
                    ],
                ),

                dbc.Label("メモ（任意）"),
                dbc.Textarea(
                    id="share_memo",
                    placeholder="共有メモ（任意）",
                    style={"height": "60px"},
                    className="mb-3",
                ),

                # 生成結果（URLの表示エリア）
                html.Div(
                    id="share_result_area",
                    style={"display": "none"},
                    children=[
                        dbc.Alert([
                            html.Strong("共有URL:"),
                            html.Br(),
                            html.Code(id="share_generated_url",
                                      style={"wordBreak": "break-all",
                                             "fontSize": "0.9rem"}),
                        ], color="success", className="mt-2"),
                    ],
                ),
            ]),
            dbc.ModalFooter([
                dbc.Button(
                    "閉じる",
                    id="close_share_modal",
                    color="secondary",
                ),
                dbc.Button(
                    "共有リンクを生成",
                    id="generate_share_link",
                    color="primary",
                ),
            ]),
        ],
    )


def _create_share_delete_modal():
    """共有リンク削除確認モーダル"""
    return dbc.Modal(
        id="share_delete_modal",
        centered=True,
        children=[
            dcc.Store(id="share_delete_target_token", data=""),
            dbc.ModalHeader(dbc.ModalTitle("共有リンク削除確認")),
            dbc.ModalBody("この共有リンクを削除しますか？"),
            dbc.ModalFooter([
                dbc.Button(
                    "キャンセル",
                    id="cancel_delete_share",
                    color="secondary",
                ),
                dbc.Button(
                    "削除",
                    id="confirm_delete_share",
                    color="danger",
                ),
            ]),
        ],
    )
