# =============================================================================
# MSI Analysis Application - Environment Settings Modal
# .env 編集用モーダル。
# =============================================================================

from dash import html
import dash_bootstrap_components as dbc

from app.services.env_file_manager import env_file_path


def _row(label: str, input_id: str, browse_id: str | None, placeholder: str,
         help_text: str | None = None):
    input_group = [
        dbc.Input(id=input_id, placeholder=placeholder, size="sm",
                  style={"flex": "1"}),
    ]
    if browse_id:
        input_group.append(
            dbc.Button("...", id=browse_id, size="sm", outline=True,
                       color="secondary"),
        )
    return html.Div(style={"marginBottom": "12px"}, children=[
        dbc.Label(label, className="small fw-bold"),
        html.Div(style={"display": "flex", "gap": "5px"}, children=input_group),
        html.Small(help_text or "", className="text-muted") if help_text else None,
    ])


def create_env_settings_modal():
    return dbc.Modal(
        id="env_settings_modal",
        is_open=False,
        size="lg",
        centered=True,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("⚙ 環境設定 (.env)")),
            dbc.ModalBody([
                html.P([
                    "生データの保存先や R 実行環境を ",
                    html.Code(str(env_file_path())),
                    " に書き込みます。",
                ], className="text-muted small mb-2"),
                dbc.Alert(
                    "変更を有効化するには アプリの再起動が必要 です。",
                    color="warning", className="small py-2",
                ),

                _row("TIMS 生データフォルダ (TIMS_DATA_DIR)",
                     "env_tims_data_dir", "browse_env_tims_data_dir",
                     "例: D:/MSI_Data/TIMS",
                     "TIMS 解析の既定データフォルダ / プロジェクト復元時のルート"),

                _row("DESI 生データフォルダ (DESI_DATA_DIR)",
                     "env_desi_data_dir", "browse_env_desi_data_dir",
                     "例: D:/MSI_Data/DESI",
                     "DESI 解析の既定データフォルダ / プロジェクト復元時のルート"),

                _row("R 実行環境 (R_HOME)",
                     "env_r_home", "browse_env_r_home",
                     "例: C:/Program Files/R/R-4.4.2",
                     "Rscript が bin/ 配下にあるディレクトリ"),

                _row("共有 URL ベース (SHARE_BASE_URL)",
                     "env_share_base_url", None,
                     "例: https://xxxx.trycloudflare.com (空で LAN IP 自動)",
                     "共有リンク生成時のベース URL"),

                _row("アプリ待受ポート (APP_PORT)",
                     "env_app_port", None,
                     "3838"),

                _row("アプリ待受ホスト (APP_HOST)",
                     "env_app_host", None,
                     "0.0.0.0"),

                html.Hr(),
                html.Div(id="env_settings_result", className="small"),
            ]),
            dbc.ModalFooter([
                dbc.Button("キャンセル", id="env_settings_cancel_btn",
                           color="secondary", size="sm"),
                dbc.Button("保存", id="env_settings_save_btn",
                           color="primary", size="sm"),
            ]),
        ],
    )
