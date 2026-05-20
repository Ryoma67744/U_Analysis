# =============================================================================
# MSI Analysis Application - Data Management Subtab UI
# 設定タブ内「データ管理」サブタブ
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc


def create_data_management_subtab():
    """データ管理サブタブのレイアウト

    4セクション構成:
      1. データ配置サマリー (環境変数の現在値表示)
      2. サーバーデータブラウザ (場所切替 + ツリービュー + パンくず)
      3. 検出済みプロジェクト一覧 (メタデータスキャン + ワンクリック復元)
      4. ストレージ統計 + バックアップ世代一覧
    """
    return html.Div(className="p-3", children=[
        # ---- セクション1: データ配置サマリー ----
        html.H5("\U0001F4C1 データ配置"),
        html.P(
            "サーバー上のディレクトリと、コンテナ内パス・環境変数の現在値です。"
            "全PCで同じパスを参照するため、ラボメンバー間で表示が共通化されます。",
            className="text-muted small mb-2",
        ),
        html.Div(id="dm_layout_summary", className="mb-4"),
        html.Hr(),

        # ---- セクション2: サーバーデータブラウザ ----
        html.H5("\U0001F50D サーバーデータブラウザ"),
        html.P(
            "場所を切り替えて、サーバー上のフォルダを直接閲覧します。",
            className="text-muted small mb-2",
        ),
        html.Div(className="mb-2", children=[
            dbc.Button(
                "DESI生データ",
                id={"type": "dm_loc_btn", "key": "desi"},
                color="primary", outline=True, size="sm", className="me-1",
            ),
            dbc.Button(
                "TIMS生データ",
                id={"type": "dm_loc_btn", "key": "tims"},
                color="primary", outline=True, size="sm", className="me-1",
            ),
            dbc.Button(
                "解析出力",
                id={"type": "dm_loc_btn", "key": "output"},
                color="primary", outline=True, size="sm", className="me-1",
            ),
            dbc.Button(
                "アプリ内部データ",
                id={"type": "dm_loc_btn", "key": "internal"},
                color="primary", outline=True, size="sm", className="me-1",
            ),
            dbc.Button(
                "\U0001F504 再読込",
                id="dm_refresh_btn",
                color="secondary", outline=True, size="sm", className="ms-2",
            ),
        ]),
        html.Div(id="dm_breadcrumb", className="mb-2", style={"fontSize": "0.9rem"}),
        html.Div(
            id="dm_directory_listing",
            className="mb-4",
            style={
                "maxHeight": "350px", "overflowY": "auto",
                "border": "1px solid #dee2e6", "borderRadius": "4px",
                "padding": "10px", "backgroundColor": "#fafafa",
            },
            children=html.Div(
                "場所を選択してください",
                className="text-muted",
                style={"padding": "10px"},
            ),
        ),
        html.Hr(),

        # ---- セクション3: 検出済みプロジェクト + ワンクリック復元 ----
        html.H5("\U0001F4E5 検出済みプロジェクトと復元"),
        html.P(
            "「解析出力」配下を再帰スキャンし、_project_meta.json を持つフォルダを検出します。"
            "「復元」ボタンで projects.json に取り込み、ランディングページから開けるようにします。",
            className="text-muted small mb-2",
        ),
        html.Div(className="mb-2", children=[
            dbc.Button(
                "\U0001F50D 出力フォルダをスキャン",
                id="dm_scan_btn",
                color="info", size="sm", className="me-2",
            ),
            html.Span(id="dm_scan_summary", className="text-muted small"),
        ]),
        html.Div(
            id="dm_scan_results",
            className="mb-4",
            style={"maxHeight": "400px", "overflowY": "auto"},
        ),
        html.Hr(),

        # ---- セクション4: ストレージ統計 + バックアップ世代 ----
        dbc.Row(children=[
            dbc.Col(width=7, children=[
                html.H5("\U0001F4CA ストレージ統計"),
                html.P(
                    "各場所のファイル数・使用容量と、ディスク全体の空き容量。",
                    className="text-muted small mb-2",
                ),
                html.Div(id="dm_storage_stats"),
            ]),
            dbc.Col(width=5, children=[
                html.H5("\U0001F4BE バックアップ世代"),
                html.P(
                    "起動時に作成された projects.json のバックアップ (新しい順)。",
                    className="text-muted small mb-2",
                ),
                html.Div(
                    id="dm_backup_list",
                    style={"maxHeight": "300px", "overflowY": "auto"},
                ),
            ]),
        ]),

        # ---- 状態管理 & 通知 ----
        # 選択中の場所キー (desi/tims/output/internal) と相対サブパスを保持
        dcc.Store(id="dm_state", data={"location_key": "desi", "subpath": ""}),
        # スキャン結果キャッシュ (復元時に参照)
        dcc.Store(id="dm_scan_cache", data=[]),
        # トースト通知
        dbc.Toast(
            id="dm_toast",
            header="データ管理",
            is_open=False,
            duration=4000,
            dismissable=True,
            icon="primary",
            style={
                "position": "fixed", "top": 70, "right": 20,
                "minWidth": 320, "zIndex": 9999,
            },
        ),
    ])
