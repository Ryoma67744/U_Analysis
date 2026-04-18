# =============================================================================
# MSI Analysis Application - SCiLS Converter Modal
# SCiLS Lab 出力フォルダ (CSV) を Parquet に変換する UI
# =============================================================================

from dash import html
import dash_bootstrap_components as dbc

from app.config import TIMS_DATA_DIR


def create_scils_converter_modal():
    """SCiLS データ変換モーダルのレイアウト"""
    return dbc.Modal(
        id="scils_converter_modal",
        is_open=False,
        size="lg",
        centered=True,
        children=[
            dbc.ModalHeader(dbc.ModalTitle("🔄 SCiLS データ変換")),
            dbc.ModalBody([
                html.P(
                    "SCiLS Lab で Export → Feature list (CSV) した "
                    "フォルダをアプリの読込形式 (.parquet) に変換します。",
                    className="text-muted small mb-3",
                ),

                dbc.Label("入力フォルダ (SCiLS 出力)", className="small fw-bold"),
                html.Div(
                    style={"display": "flex", "gap": "5px"},
                    children=[
                        dbc.Input(
                            id="scils_input_folder",
                            placeholder="例: /path/to/260402_SCiLS_Annotation",
                            size="sm",
                            style={"flex": "1"},
                        ),
                        dbc.Button(
                            "...", id="browse_scils_input_folder",
                            size="sm", outline=True, color="secondary",
                        ),
                    ],
                ),

                dbc.Label("出力先フォルダ", className="small fw-bold mt-3"),
                html.Div(
                    style={"display": "flex", "gap": "5px"},
                    children=[
                        dbc.Input(
                            id="scils_output_folder",
                            value=str(TIMS_DATA_DIR),
                            size="sm",
                            style={"flex": "1"},
                        ),
                        dbc.Button(
                            "...", id="browse_scils_output_folder",
                            size="sm", outline=True, color="secondary",
                        ),
                    ],
                ),
                html.Small(
                    "既定: TIMS_DATA_DIR。変換後すぐに解析タブのデータフォルダ候補に表示されます。",
                    className="text-muted",
                ),

                dbc.Label("サンプル名 (ファイル名)", className="small fw-bold mt-3"),
                dbc.Input(
                    id="scils_sample_name",
                    placeholder="例: 260402_Liver_Positive (拡張子は自動付与)",
                    size="sm",
                ),

                html.Hr(),
                html.Div(id="scils_conversion_result", className="small"),
            ]),
            dbc.ModalFooter([
                dbc.Button("キャンセル", id="scils_cancel_btn", color="secondary", size="sm"),
                dbc.Button(
                    "変換実行", id="scils_run_btn", color="primary", size="sm",
                ),
            ]),
        ],
    )
