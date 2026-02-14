# =============================================================================
# MSI Analysis Application - Main Layout
# メインレイアウト定義
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc

from app.layouts.sidebar import create_sidebar
from app.layouts.settings_tab import create_settings_tab
from app.layouts.results_tab import create_results_tab
from app.layouts.history_tab import create_history_tab
from app.layouts.interactive_tab import create_interactive_tab
from app.layouts.file_browser_modal import create_file_browser_modal
from app.layouts.landing_page import create_landing_page
from app.layouts.action_page import create_action_page


def create_main_layout():
    return html.Div([
        # ========== ページ状態管理 ==========
        dcc.Store(id="current_page", data="landing"),
        dcc.Store(id="selected_project", data={}),
        dcc.Store(id="delete_target_project_id", data=""),
        dcc.Store(id="delete_target_sub_project_id", data=""),
        dcc.Store(id="project_list_refresh", data=0),
        dcc.Store(id="sub_project_list_refresh", data=0),
        dcc.Store(id="edit_target_project_id", data=""),
        dcc.Store(id="edit_target_sub_project_id", data=""),
        dcc.Store(id="current_sub_project_id", data=""),

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
                                            html.H1(
                                                "MSI Analysis Application"
                                            ),
                                            html.P(
                                                className="subtitle",
                                                children="質量分析イメージング"
                                                "データ解析システム",
                                            ),
                                        ]),
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

                        # メインレイアウト: サイドバー(3) + メインパネル(9)
                        dbc.Row([
                            dbc.Col(width=3, children=[create_sidebar()]),
                            dbc.Col(width=9, children=[
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
                                            label="結果閲覧",
                                            tab_id="results",
                                            children=[create_results_tab()],
                                        ),
                                        dbc.Tab(
                                            label="セッション履歴",
                                            tab_id="history",
                                            children=[create_history_tab()],
                                        ),
                                        dbc.Tab(
                                            label="インタラクティブ解析",
                                            tab_id="interactive",
                                            children=[
                                                create_interactive_tab()
                                            ],
                                        ),
                                    ],
                                ),
                            ]),
                        ]),

                        # ファイルブラウザモーダル（共通）
                        create_file_browser_modal(),

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

                        # 通知用 Toast
                        dbc.Toast(
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
                    ],
                ),
            ],
        ),
    ])
