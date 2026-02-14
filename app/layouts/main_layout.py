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


def create_main_layout():
    return dbc.Container(
        fluid=True,
        className="main-container",
        children=[
            # ヘッダー
            html.Div(
                className="app-header",
                children=[
                    html.H1("🔬 MSI Analysis Application"),
                    html.P(className="subtitle",
                           children="質量分析イメージングデータ解析システム"),
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
                                label="解析設定", tab_id="settings",
                                children=[create_settings_tab()],
                            ),
                            dbc.Tab(
                                label="結果閲覧", tab_id="results",
                                children=[create_results_tab()],
                            ),
                            dbc.Tab(
                                label="セッション履歴", tab_id="history",
                                children=[create_history_tab()],
                            ),
                            dbc.Tab(
                                label="インタラクティブ解析", tab_id="interactive",
                                children=[create_interactive_tab()],
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
                "mode": "folder",        # "folder" or "file"
                "caller_id": "",         # どのボタンから呼ばれたか
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
                interval=2000,  # ms
                disabled=True,   # 解析中のみ有効
            ),

            # 通知用 Toast
            dbc.Toast(
                id="notification_toast",
                header="通知",
                is_open=False,
                dismissable=True,
                duration=4000,
                icon="info",
                style={"position": "fixed", "top": 10, "right": 10, "width": 350, "zIndex": 9999},
            ),
        ],
    )
