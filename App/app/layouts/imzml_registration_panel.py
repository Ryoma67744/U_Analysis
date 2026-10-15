"""★ ver78.0: ファイル選択・全切片登録・変換完了を一画面へまとめる。"""
from dash import dcc, html, dash_table
import dash_bootstrap_components as dbc


def create_registration_panel():
    from app.config import DEFAULT_TIMS_DATA_FOLDER
    from app.layouts.imzml_spatial_float import create_imzml_spatial_float
    ppm = 0  # 実行者別の保存値はモーダルを開くcallbackで復元する。
    return html.Div([
        dcc.Store(id="imzml_batch_selection", data={}),
        dcc.Store(id="imzml_batch_inspection", data={}),
        dcc.Store(id="imzml_batch_rendered", data={}),
        dcc.Store(id="imzml_batch_browse_selection", data=None),
        dcc.Store(id="imzml_registered_selection", data=None),
        dcc.Store(id="section_catalog_store_registration", data=[]),
        dcc.Store(id="section_manifest_store_registration", data={}),
        dcc.Interval(id="imzml_batch_poll", interval=2000),
        html.H5("① サーバー上のimzMLを選択"),
        html.P("共有フォルダにあるファイルを複数選択できます。同名のibdは自動で対応付けます。"
               "この操作はPCからのアップロードではありません。", className="small text-muted"),
        dbc.Button("参照…", id="browse_imzml_batch_file", color="secondary", className="mb-2"),
        html.Small(" imzMLを選ぶと登録するファイルに追加されます。", className="text-muted"),
        dbc.InputGroup([
            dbc.Input(id="imzml_batch_folder", value=DEFAULT_TIMS_DATA_FOLDER,
                      placeholder="サーバー上のフォルダ"),
            dbc.Button("移動", id="imzml_batch_go", outline=True),
            dbc.Button("上のフォルダ", id="imzml_batch_up", outline=True),
        ], className="mb-2"),
        dcc.Dropdown(id="imzml_batch_subfolder", placeholder="サブフォルダを開く", className="mb-2"),
        html.Div(id="imzml_batch_browser_message", className="small"),
        dbc.Checklist(id="imzml_batch_files", options=[], value=[],
                      style={"maxHeight": "180px", "overflowY": "auto"}),
        dbc.Button("選択したファイルを追加", id="imzml_batch_add", color="secondary", size="sm", className="my-2"),
        dcc.Dropdown(id="imzml_batch_paths", options=[], value=[], multi=True,
                     placeholder="登録するファイル（×で解除）"),
        html.Div(id="imzml_batch_inspection_message", className="small my-2"),
        html.H5("② 全切片の登録情報を確認", className="mt-3"),
        html.P("自動の切片名はそのまま使えます。個体／独立試料IDと群を入力してください。"
               "同じ個体の連続切片には同じIDを指定します。", className="small"),
        dash_table.DataTable(id="imzml_batch_table", columns=[
            {"name": "ファイル", "id": "file", "editable": False},
            {"name": "切片名", "id": "section_display_name", "editable": True},
            {"name": "個体／独立試料ID", "id": "subject_id", "editable": True},
            {"name": "群", "id": "group", "editable": True},
            {"name": "pixels", "id": "pixels", "editable": False},
            {"name": "パス", "id": "file_path", "editable": False},
            {"name": "切片ID", "id": "section_id", "editable": False},
        ], data=[], editable=True, row_selectable="multi", selected_rows=[],
           hidden_columns=["file_path", "section_id"],
           style_table={"overflowX": "auto", "maxHeight": "350px", "overflowY": "auto"},
           style_cell={"fontFamily": "sans-serif", "fontSize": "12px", "textAlign": "left"},
           style_data_conditional=[{"if": {"filter_query": '{' + key + '} = ""', "column_id": key},
                                    "backgroundColor": "#fff0f0", "border": "1px solid #dc3545"}
                                   for key in ("section_display_name", "subject_id", "group")]),
        dbc.Row([
            dbc.Col(dcc.Dropdown(id="imzml_batch_bulk_field", options=[
                {"label": "個体／独立試料ID", "value": "subject_id"},
                {"label": "群", "value": "group"}], value="group", clearable=False), md=4),
            dbc.Col(dbc.Input(id="imzml_batch_bulk_value", placeholder="選択行に設定する値"), md=4),
            dbc.Col(dbc.Button("選択行へ一括設定", id="imzml_batch_bulk", outline=True, size="sm"), md=4),
        ], className="my-2"),
        html.Div(id="imzml_batch_edit_message", className="small"),
        html.Details([
            html.Summary("表を貼り付ける／切片の検出を修正"),
            html.P("貼付は表示順に1切片1行、切片名・個体ID・群の3列（タブ区切り）。全行を置換します。",
                   className="small mt-2"),
            dbc.Textarea(id="imzml_batch_paste", rows=3),
            dbc.Button("貼り付けた表を反映", id="imzml_batch_apply_paste", size="sm", outline=True, className="my-2"),
            html.Div(id="imzml_batch_spatial_buttons"),
        ], className="my-2"),
        html.Details([
            html.Summary("変換の詳細設定"),
            dbc.Label("m/zアライメント（ppm）", className="mt-2"),
            dbc.Input(id="imzml_batch_ppm", type="number", value=ppm, min=0, step=0.1),
            html.Small("新規登録の既定は0（完全一致のみ）。従来の手動変換は5 ppmです。"
                       "既存の解析・登録値は変更しません。", className="text-muted"),
            html.Small("近いm/zをまとめる設定です。既にピークを整理したデータをそのまま変換する場合は0を選びます。"
                       "分子名による判定は行いません。", className="d-block text-muted"),
        ]),
        html.Div(id="imzml_batch_summary", className="small fw-semibold my-2"),
        dbc.Button("内容を確認してParquetを作成", id="imzml_batch_submit", color="primary"),
        html.Div(id="imzml_batch_action_message", className="small my-2"),
        html.H5("③ 変換状況・完成データ", className="mt-3"),
        dcc.Dropdown(id="imzml_batch_job", placeholder="登録ジョブ"),
        html.Div(id="imzml_batch_job_status", className="small my-2"),
        dbc.ButtonGroup([
            dbc.Button("停止", id="imzml_batch_cancel", color="danger", outline=True),
            dbc.Button("失敗・停止したファイルを再試行", id="imzml_batch_retry", outline=True),
        ], size="sm"),
        dbc.Checklist(id="imzml_batch_completed", options=[], value=[], className="my-3"),
        html.Div(id="imzml_batch_output_paths", className="small my-2"),
        dbc.Button("選択した完成データで解析設定へ", id="imzml_batch_handoff", color="success"),
        html.Div(id="imzml_batch_handoff_message", className="small my-2"),
        html.Small("登録だけで終了する場合は閉じてください。変換完了だけでは解析は始まりません。",
                   className="d-block text-muted mt-2"),
        create_imzml_spatial_float("registration"),
    ])
