# =============================================================================
# MSI Analysis Application - Main Layout
# メインレイアウト定義
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc

from app.layouts.sidebar import create_sidebar
from app.layouts.settings_tab import create_settings_tab
from app.layouts.interactive_tab import create_interactive_tab
from app.layouts.hne_overlay_tab import create_hne_overlay_tab
from app.layouts.file_browser_modal import create_file_browser_modal
from app.layouts.scils_converter_modal import create_scils_converter_modal
from app.layouts.env_settings_modal import create_env_settings_modal
from app.layouts.rds_maintenance_modal import create_rds_maintenance_modal
from app.layouts.landing_page import create_landing_page
from app.layouts.action_page import create_action_page
from app.layouts.shared_view import create_shared_view_layout
from app.layouts.lite_view import create_lite_view_layout
from app.layouts.tooltips import (
    get_sidebar_tooltips, get_settings_tooltips,
    get_interactive_tooltips, get_results_tooltips,
)
from app.version import version_label


def _create_change_password_modal():
    """パスワード変更モーダル (ver4.0: Master + 共有用の 2 本立て)。

    ログイン済 (Tier A) なら Master 再入力は不要 (③)。
    Password A は廃止 (④)。
    """
    return dbc.Modal(
        id="change_password_modal",
        is_open=False,
        centered=True,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("パスワード変更")),
            dbc.ModalBody([
                html.Div(
                    "ログイン済みのため Master の再入力は不要です。"
                    "変更したいパスワードのみ入力してください。"
                    "空欄のフィールドは更新されません。",
                    className="text-muted small mb-3",
                ),
                # ver4.0: 現在 Master は任意 (③)。誤操作防止のため確認したい
                # 場合のみ入力。auth.js が State として参照するため残置。
                dbc.Label(
                    "現在の Master Password (任意・確認用)",
                    className="small fw-bold",
                ),
                dbc.Input(
                    id="cp_master",
                    type="text",
                    placeholder="(任意) 入力すると照合します",
                    size="sm",
                    className="mb-3",
                    autoComplete="off",
                ),
                html.Hr(className="my-2"),
                dbc.Label(
                    "新しい Master Password (空欄なら変更なし)",
                    className="small fw-bold",
                ),
                dbc.Input(
                    id="cp_new_master",
                    type="text",
                    placeholder="新しい Master (8 文字以上・ログイン用)",
                    size="sm",
                    className="mb-2",
                    autoComplete="off",
                ),
                dbc.Label(
                    "新しい 共有パスワード (空欄なら変更なし)",
                    className="small fw-bold",
                ),
                dbc.Input(
                    id="cp_new_b",
                    type="text",
                    placeholder="共有 URL 閲覧用",
                    size="sm",
                    className="mb-2",
                    autoComplete="off",
                ),
                html.Div(id="cp_status", className="mt-2 small"),
            ]),
            dbc.ModalFooter([
                dbc.Button(
                    "キャンセル",
                    id="cp_cancel_btn",
                    color="secondary",
                    outline=True,
                    size="sm",
                ),
                dbc.Button(
                    "保存",
                    id="cp_submit_btn",
                    color="primary",
                    size="sm",
                ),
            ]),
        ],
    )


def _create_preset_modal():
    """プリセット管理モーダル"""
    return dbc.Modal(
        id="preset_modal",
        is_open=False,
        centered=True,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("📋 パラメータプリセット")),
            dbc.ModalBody([
                dbc.Label("既存プリセット", className="small fw-bold"),
                dcc.Dropdown(
                    id="preset_select",
                    placeholder="プリセットを選択...",
                    clearable=True,
                ),
                html.Hr(style={"margin": "10px 0"}),
                dbc.Label("プリセット名", className="small fw-bold"),
                dbc.Input(
                    id="preset_name_input",
                    placeholder="新しいプリセット名を入力",
                    size="sm",
                ),
                html.Div(
                    style={"display": "flex", "gap": "8px", "marginTop": "12px"},
                    children=[
                        dbc.Button(
                            "💾 保存", id="preset_save_btn",
                            size="sm", color="success",
                        ),
                        dbc.Button(
                            "📂 読込", id="preset_load_btn",
                            size="sm", color="primary",
                        ),
                        dbc.Button(
                            "🗑 削除", id="preset_delete_btn",
                            size="sm", color="danger", outline=True,
                        ),
                    ],
                ),
                html.Div(
                    id="preset_status",
                    style={"marginTop": "10px", "fontSize": "13px"},
                ),
            ]),
        ],
    )


def create_main_layout():
    return html.Div([
        # ========== グローバルバージョン表示 (全画面の右上に固定) ==========
        # ユーザーが今見ているページが最新の修正反映後の版か即座に
        # 判別できるよう、landing/action/analysis/shared/lite すべてで
        # 常に右上に表示する。文字は小さく薄く、pointer-events: none で
        # 既存ボタンのクリックを邪魔しない。
        html.Div(
            version_label(),
            style={
                "position": "fixed",
                "top": "4px",
                "right": "12px",
                "fontSize": "0.7em",
                "color": "#6c757d",
                "fontFamily": "monospace",
                "background": "rgba(255,255,255,0.85)",
                "padding": "2px 6px",
                "borderRadius": "3px",
                "boxShadow": "0 1px 2px rgba(0,0,0,0.05)",
                "zIndex": 9999,
                "pointerEvents": "none",
            },
        ),

        # ========== URLルーティング ==========
        dcc.Location(id="url_bar", refresh=False),
        dcc.Store(id="share_token", data=""),
        # /app/<tab_id> deep link 用の中間 Store (url_bar.pathname と
        # current_page.data を直接結ぶと share_callbacks.route_share_url と
        # Dash の allow_duplicate ハッシュ衝突で実行時エラーになるため、
        # lite_target_store / navigate_to_lite_page と同じ二段パターンを採用)
        dcc.Store(id="app_path_target_store", data=None),
        # /open/<pid>/<sid> deep link 用の中間 Store。ChatGPT が返す解析ページ
        # リンクをブラウザが開いたとき、指定プロジェクトをフル解析画面へ自動ロード
        # する。app_path_target_store と同じ二段パターン（url_bar.pathname と
        # current_page.data を直接結ばない）で allow_duplicate 衝突を避ける。
        dcc.Store(id="open_target_store", data=None),

        # ========== 認証情報 (clientside callback で /api/whoami から読み込み) ==========
        dcc.Store(id="current_analyst", data={"name": "", "tier": ""}),

        # ========== パスワード変更モーダル (Tier A のみ操作可) ==========
        _create_change_password_modal(),

        # ========== ページ状態管理 ==========
        dcc.Store(id="current_page", data="landing"),
        # ver4.0: 共有セッション (インタラクティブ全機能を共有モードで表示)
        dcc.Store(id="shared_session", data={}),
        dcc.Store(id="selected_project", data={}),
        dcc.Store(id="delete_target_project_id", data=""),
        dcc.Store(id="delete_target_sub_project_id", data=""),
        dcc.Store(id="project_list_refresh", data=0),
        dcc.Store(id="sub_project_list_refresh", data=0),
        dcc.Store(id="edit_target_project_id", data=""),
        dcc.Store(id="edit_target_sub_project_id", data=""),
        dcc.Store(id="current_sub_project_id", data=""),
        dcc.Store(id="interactive_entry_mode", data=""),

        # ========== グローバルに表示可能なモーダル ==========
        # 環境設定 (.env) は landing からも analysis からも開けるよう最上位に配置
        create_env_settings_modal(),

        # 通知用 Toast（削除等の成否を全ページで表示するため最上位に配置）
        dbc.Toast(
            "",
            id="notification_toast",
            header="通知",
            is_open=False,
            dismissable=True,
            duration=4000,
            icon="info",
            style={
                "position": "fixed",
                "top": 10,
                "right": 10,
                "width": 350,
                "zIndex": 9999,
            },
        ),

        # ========== Page 1: Landing (プロジェクト一覧) ==========
        create_landing_page(),

        # ========== Page 2: Action Selection ==========
        create_action_page(),

        # ========== Page 3: Analysis (既存レイアウト) ==========
        html.Div(
            id="page_analysis",
            style={"display": "none"},
            children=[
                dbc.Container(
                    fluid=True,
                    className="main-container",
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
                                            # ver3.9: クリックでプロジェクト一覧 (landing) に戻る
                                            # 元の H1 をボタンでラップし、見た目はそのまま保つ
                                            dbc.Button(
                                                html.H1(
                                                    "MSI Analysis Application",
                                                    className="m-0 p-0",
                                                ),
                                                id="header_title_home_btn",
                                                color="link",
                                                className="p-0 border-0 text-decoration-none",
                                                style={
                                                    "color": "inherit",
                                                    "textAlign": "left",
                                                },
                                                title="プロジェクト一覧に戻る",
                                            ),
                                            html.P(
                                                className="subtitle",
                                                children="質量分析イメージング"
                                                "データ解析システム",
                                            ),
                                        ]),
                                        html.Div(
                                            # ver4.0: 共有モード時に非表示にするため id 付与
                                            id="header_analysis_buttons",
                                            style={
                                                "display": "flex",
                                                "gap": "8px",
                                                "alignItems": "center",
                                            },
                                            children=[
                                                html.Span(
                                                    id="header_analyst_label_analysis",
                                                    className="text-muted small",
                                                ),
                                                html.A(
                                                    "❓ ヘルプ",
                                                    href="/help/analysis",
                                                    target="_blank",
                                                    rel="noopener noreferrer",
                                                    title="解析画面の取扱説明書を別タブで開く",
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
                                                dbc.Button(
                                                    "< プロジェクトに戻る",
                                                    id="back_to_action_from_analysis",
                                                    color="light",
                                                    size="sm",
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ],
                        ),

                        # メインレイアウト: サイドバー(3) + メインパネル(9)
                        dbc.Row([
                            dbc.Col(id="sidebar_col", width=3, children=[create_sidebar()]),
                            dbc.Col(id="main_content_col", width=9, children=[
                                html.Div(
                                    id="main_tabs_wrapper",
                                    children=[
                                        dbc.Tabs(
                                            id="main_tabs",
                                            active_tab="settings",
                                            children=[
                                                dbc.Tab(
                                                    label="解析設定",
                                                    tab_id="settings",
                                                    children=[create_settings_tab()],
                                                ),
                                                dbc.Tab(
                                                    label="インタラクティブ解析",
                                                    tab_id="interactive",
                                                    children=[
                                                        create_interactive_tab()
                                                    ],
                                                ),
                                                dbc.Tab(
                                                    label="解剖×クラスタ (H&E)",
                                                    tab_id="hne",
                                                    children=[create_hne_overlay_tab()],
                                                ),
                                            ],
                                        ),
                                    ],
                                ),
                            ]),
                        ]),

                        # ファイルブラウザモーダル（共通）
                        create_file_browser_modal(),

                        # プリセット管理モーダル
                        _create_preset_modal(),

                        # SCiLS 変換モーダル
                        create_scils_converter_modal(),

                        # RDS 軽量化モーダル
                        create_rds_maintenance_modal(),

                        # ファイルブラウザの状態
                        dcc.Store(id="fb_state", data={
                            "current_dir": "",
                            "mode": "folder",
                            "caller_id": "",
                            "selected_path": "",
                        }),

                        # アプリ全体の状態を保持する Store
                        dcc.Store(id="app_state", data={
                            "is_running": False,
                            "process_pid": None,
                            "progress_file": None,
                            "log_file": None,
                            "status_file": None,
                            "full_output_dir": None,
                        }),

                        # 進捗監視用のインターバルタイマー (2秒ごと)
                        dcc.Interval(
                            id="progress_interval",
                            interval=2000,
                            disabled=True,
                        ),

                        # バックアップ一覧モーダル
                        dbc.Modal([
                            dbc.ModalHeader(dbc.ModalTitle("バックアップ一覧")),
                            dbc.ModalBody(id="backup_list_body"),
                            dbc.ModalFooter(
                                dbc.Button("閉じる", id="close_backup_list_btn",
                                           className="ms-auto", size="sm"),
                            ),
                        ], id="backup_list_modal", size="lg", is_open=False),

                        # ツールチップ（タブ外に配置して常にDOM上に存在させる）
                        *get_sidebar_tooltips(),
                        *get_settings_tooltips(),
                        *get_interactive_tooltips(),
                        *get_results_tooltips(),
                    ],
                ),
            ],
        ),

        # ========== Page 4: Shared View (共有専用・読み取り専用) ==========
        html.Div(
            id="page_shared",
            style={"display": "none"},
            children=[
                dbc.Container(
                    fluid=True,
                    className="main-container",
                    style={"maxWidth": "1400px", "padding": "20px"},
                    children=[create_shared_view_layout()],
                ),
            ],
        ),

        # ========== Page 5: Lite View (軽量ビューア・認証なし) ==========
        html.Div(
            id="page_lite",
            style={"display": "none"},
            children=[
                dbc.Container(
                    fluid=True,
                    className="main-container",
                    style={"maxWidth": "1400px", "padding": "20px"},
                    children=[create_lite_view_layout()],
                ),
            ],
        ),
    ])
