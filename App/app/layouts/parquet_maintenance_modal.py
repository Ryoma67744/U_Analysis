# =============================================================================
# MSI Analysis Application - Parquet 再パックモーダル
#
# 既に変換済みの .parquet を「全行 1 row group」へ作り直す CLI ツール
# App/tools/repack_parquet_rowgroups.py を GUI から呼び出すためのモーダル。
#
# 対応オプション:
#   - 対象フォルダ      (必須)
#   - Dry-run           (書き込みなし、判定と見積りだけ表示)
#   - バックアップ       (<file>.parquet.bak を残す)
#   - include パターン  (既定: *.parquet)
#   - 検証省略 / 分割許可 (詳細設定; 通常は既定のまま)
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc


_DEFAULT_INCLUDE = "*.parquet"

# html.Pre のスタイル。コールバック側で display を block にした複製を持つため、
# 両者は同期を保つこと。
LOG_STYLE = {
    "backgroundColor": "#111",
    "color": "#d0d0d0",
    "padding": "8px",
    "borderRadius": "4px",
    "fontSize": "11px",
    "fontFamily": "Consolas, monospace",
    "maxHeight": "260px",
    "overflowY": "auto",
    "whiteSpace": "pre-wrap",
    "wordBreak": "break-all",
    "display": "none",
}
BAR_STYLE = {"height": "18px", "display": "none"}


def create_parquet_maintenance_modal():
    """Parquet 再パックモーダルのレイアウト"""
    return dbc.Modal(
        id="parquet_maintenance_modal",
        is_open=False,
        size="lg",
        centered=True,
        backdrop="static",  # 実行中に誤って閉じないようバックドロップで閉じない
        children=[
            dbc.ModalHeader(dbc.ModalTitle("📦 Parquet 再パック (row group)")),
            dbc.ModalBody([
                html.P(
                    "ver49.0 より前に変換した .parquet は 200 行ごとに区切られており、"
                    "開くたびに管理情報の解析へ時間がかかります。中身を変えずに"
                    "レイアウトだけ作り直して「全行 1 つ」にまとめます。"
                    "値は 1 ビットも変わらず、CSV からの再変換も不要です。"
                    "ファイルサイズはむしろ小さくなります。",
                    className="text-muted small mb-3",
                ),

                # ---------- 対象フォルダ ----------
                dbc.Label("対象フォルダ (再帰的にスキャン)", className="small fw-bold"),
                html.Div(
                    style={"display": "flex", "gap": "5px"},
                    children=[
                        dbc.Input(
                            id="parquet_maint_folder",
                            placeholder="例: C:\\Users\\...\\UMAP\\TIMS\\Data",
                            size="sm",
                            style={"flex": "1"},
                        ),
                        dbc.Button(
                            "...", id="browse_parquet_maint_folder",
                            size="sm", outline=True, color="secondary",
                        ),
                    ],
                ),
                html.Small(
                    "既に 1 row group のファイル、SCiLS 変換以外の parquet、"
                    "注釈サイドカーは自動でスキップします。"
                    "書き込みは全列を照合してからアトミックに置換します。",
                    className="text-muted",
                ),

                # ---------- トグル ----------
                html.Div(className="mt-3", children=[
                    dbc.Switch(
                        id="parquet_maint_dry_run",
                        label="Dry-run (書き込みせず対象と見積りだけ表示)",
                        value=True,  # 安全側デフォルト
                        className="small",
                    ),
                    dbc.Switch(
                        id="parquet_maint_backup",
                        label="バックアップ作成 (<file>.parquet.bak を残す)",
                        value=True,  # 安全側デフォルト
                        className="small mt-1",
                    ),
                ]),

                # ---------- include パターン ----------
                dbc.Label(
                    "対象ファイルパターン (カンマ区切り)",
                    className="small fw-bold mt-3",
                ),
                dbc.Input(
                    id="parquet_maint_include",
                    placeholder=_DEFAULT_INCLUDE,
                    value=_DEFAULT_INCLUDE,
                    size="sm",
                ),
                html.Small(
                    "空欄の場合は既定パターンを使用します。",
                    className="text-muted",
                ),

                # ---------- 詳細設定 (折りたたみ) ----------
                html.Hr(),
                dbc.Accordion(
                    id="parquet_maint_advanced_accordion",
                    start_collapsed=True,
                    flush=True,
                    children=[
                        dbc.AccordionItem(
                            title="詳細設定",
                            children=[
                                dbc.Switch(
                                    id="parquet_maint_allow_split",
                                    label="メモリが足りないとき分割を許可 (--allow-split)",
                                    value=False,
                                    className="small",
                                ),
                                dbc.Switch(
                                    id="parquet_maint_skip_verify",
                                    label="書き込み後の照合を省略 (--skip-verify、非推奨)",
                                    value=False,
                                    className="small mt-1",
                                ),
                                html.Small(
                                    "既定ではメモリが足りないファイルはスキップし、"
                                    "必要量をログに出します。分割を許可すると"
                                    "予算内で最大の row group にまとめます"
                                    "（1 つにはなりませんが効果はほぼ同じです）。"
                                    "照合の省略は推奨しません。",
                                    className="text-muted d-block mt-2",
                                ),
                            ],
                        ),
                    ],
                ),

                # ---------- 実行状況エリア ----------
                html.Hr(),
                html.Div(id="parquet_maint_alert", className="small"),
                dbc.Progress(
                    id="parquet_maint_progress_bar",
                    value=0,
                    max=100,
                    striped=True,
                    animated=True,
                    label="",
                    style=dict(BAR_STYLE),
                    className="mb-2",
                ),
                html.Pre(id="parquet_maint_log", style=dict(LOG_STYLE)),
                html.Div(id="parquet_maint_summary", className="small mt-2"),

                # ---------- 非表示要素 (状態保持・ポーリング) ----------
                dcc.Interval(
                    id="parquet_maint_interval",
                    interval=1500,
                    disabled=True,
                ),
                dcc.Store(id="parquet_maint_state", data={}),
            ]),
            dbc.ModalFooter([
                dbc.Button(
                    "停止", id="parquet_maint_stop_btn",
                    color="danger", size="sm", disabled=True,
                ),
                dbc.Button(
                    "閉じる", id="parquet_maint_close_btn",
                    color="secondary", size="sm",
                ),
                dbc.Button(
                    "実行", id="parquet_maint_run_btn",
                    color="primary", size="sm",
                ),
            ]),
        ],
    )
