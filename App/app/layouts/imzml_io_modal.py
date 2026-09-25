"""Explicit imzML import/export controls."""

from dash import dcc, html
import dash_bootstrap_components as dbc

from app.config import TIMS_DATA_DIR


def create_imzml_io_modal():
    return dbc.Modal(
        id="imzml_io_modal", size="lg", is_open=False,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("imzML 登録・出力")),
            dbc.ModalBody([
                html.P("対応範囲: 共通 m/z 軸・単一 z 面。元データは変更しません。"
                       " 出力画素 ID は登録した Parquet の id です。", className="small text-muted"),
                dbc.Label("操作"),
                dbc.Select(id="imzml_action", value="import", options=[
                    {"label": "imzML + ibd → Parquet 登録", "value": "import"},
                    {"label": "登録済み Parquet → imzML + ibd ZIP", "value": "export"},
                ]),
                dbc.Label("入力 .imzML または登録済み .parquet", className="mt-2"),
                dbc.Input(id="imzml_source", placeholder="/path/to/sample.imzML"),
                dbc.Label("出力 .parquet または .zip", className="mt-2"),
                dbc.Input(id="imzml_target", placeholder=str(TIMS_DATA_DIR / "sample.parquet")),
                dbc.Label("出力画素 ID（任意、カンマ区切り）", className="mt-2"),
                dbc.Input(id="imzml_pixel_ids", placeholder="例: 1,2,3,4。空欄なら全画素"),
                dbc.Progress(id="imzml_progress", value=0, className="mt-3"),
                html.Div(id="imzml_result", className="small mt-2"),
                dcc.Interval(id="imzml_interval", interval=1500, disabled=True),
                dcc.Store(id="imzml_job", data={}),
            ]),
            dbc.ModalFooter([
                dbc.Button("停止", id="imzml_stop", color="danger", disabled=True),
                dbc.Button("閉じる", id="imzml_close", color="secondary"),
                dbc.Button("実行", id="imzml_run", color="primary"),
            ]),
        ],
    )
