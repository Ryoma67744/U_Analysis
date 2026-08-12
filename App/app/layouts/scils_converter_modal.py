# =============================================================================
# MSI Analysis Application - SCiLS Converter Modal
# SCiLS Lab 出力フォルダ (Intensity + Spot + Annotation CSV) を Parquet に変換する UI
# =============================================================================

from dash import html, dcc
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
                    "SCiLS Lab で Export した Intensity CSV + Spot CSV "
                    "(+ 任意の Annotation CSV / Feature list CSV) が入ったフォルダを"
                    "指定してください。ヘッダ構造とサイズで役割を自動検出し、Parquet に変換します。",
                    className="text-muted small mb-3",
                ),
                # ★ ver55.0: Feature list (peak-list) は「同じフォルダに置いてあれば
                #   黙って採用され、化合物名が列名に焼き込まれる」挙動だったのに、
                #   説明文はこれに一言も触れていなかった。何がどう使われるかを書く。
                html.P(
                    [
                        "・Annotation CSV（SpotIndex / X / Y を持つもの）は領域ラベルになります。"
                        "無ければ全 spot が Unannotated です。",
                        html.Br(),
                        "・測定全体の座標を持つ Spot CSV が無く、切片ごとの座標 CSV しか"
                        "無い場合は、それらを統合して座標表を作り、ファイル名を"
                        "そのまま領域ラベルにします。",
                        html.Br(),
                        "・Feature list CSV（m/z と Name を持つもの）があれば化合物名を"
                        "サイドカーに登録します。列名は m/z のままなので、"
                        "解析時に表示を切り替えられます。",
                    ],
                    className="text-muted small mb-3",
                ),

                dbc.Label("入力フォルダ (SCiLS 出力)", className="small fw-bold"),
                html.Div(
                    style={"display": "flex", "gap": "5px"},
                    children=[
                        dbc.Input(
                            id="scils_input_folder",
                            placeholder="例: /path/to/260402_SCiLS_Export",
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
                dbc.Accordion(
                    id="scils_advanced_accordion",
                    start_collapsed=True,
                    flush=True,
                    children=[
                        dbc.AccordionItem(
                            title="詳細設定",
                            children=[
                                dbc.Checklist(
                                    id="scils_organize_check",
                                    options=[{
                                        "label": "変換後に元 CSV を <BASE>_Transform/ サブフォルダに移動",
                                        "value": "on",
                                    }],
                                    value=["on"],
                                    switch=True,
                                    className="small",
                                ),
                                dbc.Checklist(
                                    id="scils_float32_check",
                                    options=[{
                                        "label": "Parquet を float32 で保存 (容量半減)",
                                        "value": "on",
                                    }],
                                    value=["on"],
                                    switch=True,
                                    className="small mt-2",
                                ),
                                dbc.Checklist(
                                    id="scils_drop_uncovered_check",
                                    options=[{
                                        "label": "座標 CSV に無い spot を除外して変換する",
                                        "value": "on",
                                    }],
                                    value=[],
                                    switch=True,
                                    className="small mt-2",
                                ),
                                html.Small(
                                    "測定全体の Intensity に対して切片ごとの座標 CSV しか"
                                    "無いとき用。OFF（既定）なら spot 数が合わない時点で"
                                    "エラーにします。ON にすると座標のある spot だけを"
                                    "変換し、除外した件数を結果に表示します。",
                                    className="text-muted",
                                ),
                                dbc.Label(
                                    "spot ブロックサイズ (読み込み単位)",
                                    className="small fw-bold mt-3",
                                ),
                                dbc.Input(
                                    id="scils_spot_block",
                                    type="number",
                                    min=10,
                                    max=10000,
                                    step=10,
                                    value=200,
                                    size="sm",
                                ),
                                html.Small(
                                    "1 回に読み込む spot 列数。大ファイルではメモリ使用量に"
                                    "影響します（出力の row group サイズとは無関係）。既定 200。"
                                    "出力の row group は常に全行 1 つです"
                                    "（メモリが足りない場合のみ自動分割）。",
                                    className="text-muted",
                                ),
                            ],
                        ),
                    ],
                ),

                html.Hr(),
                dcc.Loading(html.Div(id="scils_conversion_result", className="small")),
            ]),
            dbc.ModalFooter([
                dbc.Button("キャンセル", id="scils_cancel_btn", color="secondary", size="sm"),
                dbc.Button(
                    "変換実行", id="scils_run_btn", color="primary", size="sm",
                ),
            ]),
        ],
    )
