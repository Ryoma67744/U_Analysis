"""imzMLを全切片登録済みTIMS Parquetへ変換／再出力する画面。"""
from dash import dcc, html
import dash_bootstrap_components as dbc

from app.layouts.imzml_spatial_float import create_imzml_spatial_float
from app.utils.validation import PARAM_BOUNDS, param_default


def create_imzml_io_modal():
    return dbc.Modal(
        id="imzml_io_modal",
        is_open=False,
        size="xl",
        scrollable=True,
        children=[
            dcc.Store(id="imzml_job", data=None),
            dcc.Store(id="section_catalog_store_conversion", data=[]),
            dcc.Store(id="section_manifest_store_conversion", data={}),
            dcc.Interval(id="imzml_interval", interval=1000, disabled=True),
            dbc.ModalHeader(dbc.ModalTitle("imzML 登録／Parquet変換／再出力")),
            dbc.ModalBody([
                dbc.Alert(
                    "importでは、imzML内の全切片・全pixelをParquetへ保存します。"
                    "解析対象外の切片を含め、全切片の切片名・個体ID・群・登録確認が必須です。",
                    color="info", className="py-2",
                ),
                dbc.Label("操作"),
                dcc.Dropdown(
                    id="imzml_action",
                    options=[
                        {"label": "imzML → 標準TIMS Parquet", "value": "import"},
                        {"label": "Parquet → imzML ZIP", "value": "export"},
                    ],
                    value="import", clearable=False, className="mb-2",
                ),
                dbc.Label("入力ファイル（サーバー上の絶対パス）"),
                dbc.Input(id="imzml_source", type="text", className="mb-2"),
                dbc.Label("出力ファイル（.parquet または .zip）"),
                dbc.Input(id="imzml_target", type="text", className="mb-2"),
                dbc.Row([
                    dbc.Col([
                        dbc.Label("m/zアライメント（ppm）"),
                        # ★ ver74.0: 画面と受付で同じ既定・境界を使う。0は完全一致。
                        dbc.Input(id="imzml_alignment_ppm", type="number",
                                  min=PARAM_BOUNDS["imzml_alignment_ppm"][0], step=0.1,
                                  value=param_default("imzml_alignment_ppm")),
                        dbc.FormText("既定5 ppm。0は完全一致m/zのみを対応付けます。空欄では実行しません。"),
                    ], md=4),
                    dbc.Col([
                        dbc.Label("出力画素ID（exportのみ、任意）"),
                        dbc.Input(id="imzml_pixel_ids", type="text",
                                  placeholder="例: 1,2,3"),
                    ], md=8),
                ], className="mb-3"),
                html.Div(className="d-flex gap-2 flex-wrap mb-2", children=[
                    dbc.Button("座標切片を読み込む", id="imzml_prepare_registration",
                               color="secondary", outline=True, size="sm"),
                    html.Div(id="imzml_conversion_spatial_open_container"),
                ]),
                html.Div(id="imzml_registration_status", className="small mb-2"),
                dbc.Progress(id="imzml_progress", value=0, striped=True, animated=True,
                             className="mb-2"),
                html.Div(id="imzml_result", className="small"),
                create_imzml_spatial_float("conversion"),
            ]),
            dbc.ModalFooter([
                dbc.Button("停止", id="imzml_stop", color="danger", outline=True,
                           disabled=True),
                dbc.Button("実行", id="imzml_run", color="primary"),
                dbc.Button("閉じる", id="imzml_close", color="secondary"),
            ]),
        ],
    )
