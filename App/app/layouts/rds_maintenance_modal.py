# =============================================================================
# MSI Analysis Application - RDS Maintenance Modal
#
# 既存の .rds ファイルを DietSeurat + qs で一括軽量化する
# CLI ツール slim_existing_rds.R を GUI から呼び出すためのモーダル。
#
# 対応オプション:
#   - 対象フォルダ          (必須)
#   - Dry-run               (書き込みなし、削減見込みだけ表示)
#   - バックアップ           (.rds.bak を残す)
#   - include パターン      (既定: Step1*.rds,Step2*.rds,Step3*.rds,*_seurat*.rds)
#   - keep-scale / keep-graphs (詳細設定; 通常は OFF)
# =============================================================================

from dash import html, dcc
import dash_bootstrap_components as dbc


_DEFAULT_INCLUDE = "Step1*.rds,Step2*.rds,Step3*.rds,*_seurat*.rds"


def create_rds_maintenance_modal():
    """RDS 軽量化モーダルのレイアウト"""
    return dbc.Modal(
        id="rds_maintenance_modal",
        is_open=False,
        size="lg",
        centered=True,
        backdrop="static",  # 実行中に誤って閉じないようバックドロップで閉じない
        children=[
            dbc.ModalHeader(dbc.ModalTitle("🧹 RDS 軽量化 (slim)")),
            dbc.ModalBody([
                html.P(
                    "過去に解析した .rds ファイルを一括で軽量化します。"
                    "DietSeurat で scale.data を削除し、qs で高圧縮保存に変換します。"
                    "旧形式の .rds はマジックバイト判定で透過的に読めるため、"
                    "読み込み互換性は完全に保たれます。",
                    className="text-muted small mb-3",
                ),

                # ---------- 対象フォルダ ----------
                dbc.Label("対象フォルダ (再帰的にスキャン)", className="small fw-bold"),
                html.Div(
                    style={"display": "flex", "gap": "5px"},
                    children=[
                        dbc.Input(
                            id="rds_maint_folder",
                            placeholder="例: C:\\Users\\...\\UMAP\\TIMS\\Data",
                            size="sm",
                            style={"flex": "1"},
                        ),
                        dbc.Button(
                            "...", id="browse_rds_maint_folder",
                            size="sm", outline=True, color="secondary",
                        ),
                    ],
                ),
                html.Small(
                    "指定フォルダ以下の全 .rds を再帰スキャンします。既に qs 形式の"
                    "ファイルは自動スキップ、書き込みはアトミック置換で行います。",
                    className="text-muted",
                ),

                # ---------- トグル ----------
                html.Div(className="mt-3", children=[
                    dbc.Switch(
                        id="rds_maint_dry_run",
                        label="Dry-run (書き込みせず削減見込みだけ表示)",
                        value=True,  # 安全側デフォルト
                        className="small",
                    ),
                    dbc.Switch(
                        id="rds_maint_backup",
                        label="バックアップ作成 (<file>.rds.bak を残す)",
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
                    id="rds_maint_include",
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
                    id="rds_maint_advanced_accordion",
                    start_collapsed=True,
                    flush=True,
                    children=[
                        dbc.AccordionItem(
                            title="詳細設定",
                            children=[
                                dbc.Switch(
                                    id="rds_maint_keep_scale",
                                    label="scale.data を残す (--keep-scale、削減率は下がる)",
                                    value=False,
                                    className="small",
                                ),
                                dbc.Switch(
                                    id="rds_maint_keep_graphs",
                                    label="Graphs を残す (--keep-graphs)",
                                    value=False,
                                    className="small mt-1",
                                ),
                                html.Small(
                                    "通常はどちらも OFF のままで問題ありません。"
                                    "再解析時に ScaleData / Graphs は再構築されます。",
                                    className="text-muted d-block mt-2",
                                ),
                            ],
                        ),
                    ],
                ),

                # ---------- 実行状況エリア ----------
                html.Hr(),
                html.Div(id="rds_maint_alert", className="small"),
                dbc.Progress(
                    id="rds_maint_progress_bar",
                    value=0,
                    max=100,
                    striped=True,
                    animated=True,
                    label="",
                    style={"height": "18px", "display": "none"},
                    className="mb-2",
                ),
                html.Pre(
                    id="rds_maint_log",
                    style={
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
                    },
                ),
                html.Div(id="rds_maint_summary", className="small mt-2"),

                # ---------- 非表示要素 (状態保持・ポーリング) ----------
                dcc.Interval(
                    id="rds_maint_interval",
                    interval=1500,
                    disabled=True,
                ),
                dcc.Store(id="rds_maint_state", data={}),
            ]),
            dbc.ModalFooter([
                dbc.Button(
                    "停止", id="rds_maint_stop_btn",
                    color="danger", size="sm", disabled=True,
                ),
                dbc.Button(
                    "閉じる", id="rds_maint_close_btn",
                    color="secondary", size="sm",
                ),
                dbc.Button(
                    "実行", id="rds_maint_run_btn",
                    color="primary", size="sm",
                ),
            ]),
        ],
    )
