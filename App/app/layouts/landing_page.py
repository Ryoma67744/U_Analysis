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
                    html.Div(
                        style={
                            "display": "flex",
                            "justifyContent": "space-between",
                            "alignItems": "center",
                        },
                        children=[
                            html.Div([
                                html.H1("MSI Analysis Application"),
                                html.P(
                                    className="subtitle",
                                    children="質量分析イメージング"
                                    "データ解析システム",
                                ),
                            ]),
                            html.Div(
                                style={
                                    "display": "flex",
                                    "gap": "8px",
                                    "alignItems": "center",
                                },
                                children=[
                                    html.Span(
                                        id="header_analyst_label_landing",
                                        className="text-muted small",
                                    ),
                                    dbc.Button(
                                        "パスワード変更",
                                        id="open_change_password_btn",
                                        color="warning",
                                        outline=True,
                                        size="sm",
                                    ),
                                    html.A(
                                        "❓ ヘルプ",
                                        href="/help/registration",
                                        target="_blank",
                                        rel="noopener noreferrer",
                                        title="登録画面の取扱説明書を別タブで開く",
                                        className=(
                                            "btn btn-outline-info btn-sm"
                                        ),
                                    ),
                                    html.A(
                                        "ログアウト",
                                        href="/logout",
                                        className=(
                                            "btn btn-outline-secondary"
                                            " btn-sm"
                                        ),
                                    ),
                                ],
                            ),
                        ],
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
                                width=2,
                                children=[
                                    html.H3("プロジェクト一覧"),
                                ],
                            ),
                            dbc.Col(
                                width=2,
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
                                            # ver3.16: 実験日ソートを新規追加 + デフォルトに
                                            {"label": "実験日 (新しい順)",
                                             "value": "experiment_date_desc"},
                                            {"label": "実験日 (古い順)",
                                             "value": "experiment_date_asc"},
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
                                        value="experiment_date_desc",
                                        size="sm",
                                    ),
                                ],
                            ),
                            dbc.Col(
                                width=5,
                                className="text-end",
                                style={"whiteSpace": "nowrap"},
                                children=[
                                    dbc.Button(
                                        "🧹 RDS 軽量化",
                                        id="open_rds_maintenance_modal_landing",
                                        color="primary",
                                        outline=True,
                                        className="me-1",
                                    ),
                                    dbc.Button(
                                        "⚙ 環境設定",
                                        id="open_env_settings_modal_landing",
                                        color="dark",
                                        outline=True,
                                        className="me-1",
                                    ),
                                    dbc.Button(
                                        "インタラクティブ解析",
                                        id="open_interactive_from_landing_btn",
                                        color="info",
                                        className="me-1",
                                    ),
                                    dbc.Button(
                                        "復元",
                                        id="open_restore_modal_btn",
                                        color="warning",
                                        className="me-1",
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

            # プロジェクト復元モーダル
            _create_restore_modal(),

            # 復元用ストア
            dcc.Store(id="restore_scan_data", data=None),
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
                    placeholder="例: 宮林Demo",
                ),
                dbc.Label("実験日 *", className="mt-2"),
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
                # ver3.16: 関連 URL 入力欄 3 つ (任意)
                dbc.Label("関連 URL (任意)", className="mt-3 fw-bold"),
                dbc.InputGroup([
                    dbc.InputGroupText("📝 Google Keep",
                                       style={"minWidth": "140px"}),
                    dbc.Input(id="new_project_google_keep_url",
                              placeholder="https://keep.google.com/...",
                              type="url"),
                ], size="sm", className="mb-2"),
                dbc.InputGroup([
                    dbc.InputGroupText("🔗 MSI Share",
                                       style={"minWidth": "140px"}),
                    dbc.Input(id="new_project_msi_share_url",
                              placeholder="https://ryoma67744.github.io/...",
                              type="url"),
                ], size="sm", className="mb-2"),
                dbc.InputGroup([
                    dbc.InputGroupText("🌐 Other",
                                       style={"minWidth": "140px"}),
                    dbc.Input(id="new_project_other_url",
                              placeholder="https://...",
                              type="url"),
                ], size="sm", className="mb-2"),
                # 入力エラー表示
                html.Div(id="new_project_error",
                         className="text-danger small mt-2"),
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
                dbc.Label("実験日 *", className="mt-2"),
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
                # ver3.16: 関連 URL 入力欄 3 つ (任意)
                dbc.Label("関連 URL (任意)", className="mt-3 fw-bold"),
                dbc.InputGroup([
                    dbc.InputGroupText("📝 Google Keep",
                                       style={"minWidth": "140px"}),
                    dbc.Input(id="edit_project_google_keep_url",
                              placeholder="https://keep.google.com/...",
                              type="url"),
                ], size="sm", className="mb-2"),
                dbc.InputGroup([
                    dbc.InputGroupText("🔗 MSI Share",
                                       style={"minWidth": "140px"}),
                    dbc.Input(id="edit_project_msi_share_url",
                              placeholder="https://ryoma67744.github.io/...",
                              type="url"),
                ], size="sm", className="mb-2"),
                dbc.InputGroup([
                    dbc.InputGroupText("🌐 Other",
                                       style={"minWidth": "140px"}),
                    dbc.Input(id="edit_project_other_url",
                              placeholder="https://...",
                              type="url"),
                ], size="sm", className="mb-2"),
                # ver3.9: サムネ画像のソースパスを指定 (省略時は自動検出)
                dbc.Label("サムネ画像 (省略時は自動検出)", className="mt-3"),
                dbc.InputGroup([
                    dbc.Input(
                        id="edit_project_thumbnail",
                        placeholder="/app/Data/.../UMAP_per_sample_harmony_ALLclusters.png",
                    ),
                    dbc.Button(
                        "...",
                        id="browse_edit_thumbnail",
                        color="secondary",
                    ),
                ]),
                html.Small(
                    "空欄なら最新サブプロの Harmony / RPCA / PCA フォルダから "
                    "UMAP png を自動選択します",
                    className="text-muted",
                ),
                # 入力エラー表示
                html.Div(id="edit_project_error",
                         className="text-danger small mt-2"),
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


def _create_restore_modal():
    """プロジェクト復元モーダル"""
    return dbc.Modal(
        id="restore_project_modal",
        size="lg",
        centered=True,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("プロジェクト復元")),
            dbc.ModalBody([
                html.P(
                    "結果フォルダに自動保存されたメタデータをスキャンし、"
                    "プロジェクト一覧を復元します。",
                    className="text-muted mb-3",
                ),
                # スキャンフォルダ選択
                dbc.Label("スキャンフォルダ"),
                dbc.InputGroup(
                    className="mb-3",
                    children=[
                        dbc.Input(
                            id="restore_scan_folder",
                            placeholder="スキャン対象のルートフォルダを指定...",
                        ),
                        dbc.Button(
                            "...",
                            id="browse_restore_scan_folder",
                            color="secondary",
                            size="sm",
                        ),
                        dbc.Button(
                            "スキャン開始",
                            id="restore_scan_btn",
                            color="info",
                            className="ms-2",
                        ),
                    ],
                ),
                # スキャン結果表示領域
                dcc.Loading(
                    type="circle",
                    children=html.Div(
                        id="restore_scan_results",
                        children=html.P(
                            "スキャンフォルダを指定して「スキャン開始」を"
                            "クリックしてください。",
                            className="text-muted text-center py-4",
                        ),
                    ),
                ),
                # 復元ステータス
                html.Div(id="restore_status", className="mt-2"),
            ]),
            dbc.ModalFooter([
                dbc.Button(
                    "閉じる",
                    id="close_restore_modal_btn",
                    color="secondary",
                ),
                dbc.Button(
                    "選択したプロジェクトを復元",
                    id="restore_execute_btn",
                    color="success",
                    disabled=True,
                ),
            ]),
        ],
    )
