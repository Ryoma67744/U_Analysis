# =============================================================================
# MSI Analysis Application - Settings Tab UI
# 解析設定タブUI
# =============================================================================

from datetime import datetime

from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc

from app.config import (
    DEFAULT_DESI_DATA_FOLDER, OUTPUT_DATA_DIR,
    adducts_for_ion_mode,
    DEFAULT_CALIBRATION_ENABLE, DEFAULT_CALIBRATION_MATRIX,
    DEFAULT_CALIBRATION_SEARCH_WINDOW, DEFAULT_CALIBRATION_MIN_PEAKS,
    DEFAULT_CALIBRATION_REGRESSION,
    ANALYSIS_BUSY_POLL_INTERVAL_SEC,
)
from app.services.session_manager import load_last_settings
from app.services.calibration_preset_manager import list_calibration_presets
from app.layouts.tooltips import help_badge
from app.layouts.data_management_subtab import create_data_management_subtab


def _cal_preset_options():
    """キャリブレーションプリセットのドロップダウン選択肢を生成"""
    presets = list_calibration_presets()
    return [
        {
            "label": f"{p['name']}  [{p['matrix']} / {p['ion_mode']}]",
            "value": p["name"],
        }
        for p in presets
    ]


def _norm_scenario(v):
    """解析シナリオ値を正規化する。

    `within_slice`（同一切片）と `condition_compare`（群比較）は補正方針が同一
    （無補正PCA）のため UI のドロップダウンでは `within_slice` に統合した。
    旧セッションに `condition_compare` が保存されていても統合項目が選択表示される
    ように、復元時にここで吸収する（_SCENARIO_MAP は後方互換で両値を保持）。
    """
    if v == "condition_compare":
        return "within_slice"
    return v or "within_slice"



def _section_controls(ls, scope="initial"):
    """★ ver67.0: ROI の選択と補正単位を一つの画面・保存値で管理する。"""
    suffix = "" if scope == "initial" else "_reanalysis"
    return html.Div([
        html.H5("解析対象の切片／ROI", className="mt-3"),
        html.Div(id="section_selector" + suffix),
        dcc.Store(id="section_catalog_store" + suffix, data=[]),
        dcc.Store(id="section_manifest_store" + suffix, data=ls.get("section_manifest" + suffix)),
        html.Div(id="section_summary" + suffix, className="alert alert-light py-2 mt-2"),
        html.Details([
            html.Summary("群情報を設定", style={"cursor": "pointer", "fontWeight": "600"}),
            dbc.FormText("連続切片には同じ個体／独立試料IDを指定します。群名の変更で解析結果は再計算しません。"),
            dash_table.DataTable(id="section_group_table" + suffix,
                columns=[{"name": "切片", "id": "section", "editable": False},
                         {"name": "フォルダ", "id": "file", "editable": False},
                         {"name": "個体／独立試料ID", "id": "subject_id"},
                         {"name": "群", "id": "group"},
                         {"name": "section_id", "id": "section_id", "editable": False}],
                hidden_columns=["section_id"], data=[], editable=True,
                row_selectable="multi", selected_rows=[], style_table={"overflowX": "auto"},
                style_cell={"textAlign": "left", "fontSize": "12px", "padding": "6px",
                            "maxWidth": "180px", "overflow": "hidden", "textOverflow": "ellipsis"},
                css=[{"selector": ".show-hide", "rule": "display: none"}]),
            html.Div([dbc.Input(id="section_bulk_group" + suffix, placeholder="群名（例：Ctrl）", size="sm"),
                      dbc.Button("選択行に群を設定", id="section_apply_group" + suffix,
                                 size="sm", color="secondary", n_clicks=0)],
                     style={"display": "flex", "gap": "6px", "marginTop": "8px"}),
            html.Small(id="section_group_status" + suffix, className="d-block mt-1"),
        ]),
    ])


def create_settings_tab():
    """設定タブ本体: 「解析設定」「データ管理」のサブタブで構成"""
    return dbc.Tabs(
        id="settings_subtabs",
        active_tab="settings_subtab_analysis",
        className="mt-2",
        children=[
            dbc.Tab(
                label="解析設定",
                tab_id="settings_subtab_analysis",
                children=_create_analysis_settings_subtab(),
            ),
            dbc.Tab(
                label="データ管理",
                tab_id="settings_subtab_data",
                children=create_data_management_subtab(),
            ),
        ],
    )


def _create_analysis_settings_subtab():
    """解析設定サブタブ本体 (旧 create_settings_tab の中身)"""
    ls = load_last_settings()  # 前回の設定を復元
    return html.Div(className="card", style={"marginTop": "15px"}, children=[
        # UMAP解析設定（DESI/TIMS共通）
        html.Div(
            id="umap_settings_panel",
            children=[
                html.H4(className="card-title", children=["📊 UMAP解析設定"]),
                # --- 手法別の「標準フロー」推奨バナー（常時表示・選択手法のみ） ---
                #     表示切替は file_handlers.py の toggle_settings_panels で制御。
                #     TIMS選択時=TIMS用のみ / DESI選択時=DESI用のみ（両方同時には出さない）。
                html.Div(
                    id="tims_recommended_banner",
                    style={"display": "none"},
                    children=[dbc.Alert(color="info", className="mb-3", children=[
                        html.H6("💡 標準の使い方（TIMS × SCiLS RMS）",
                                className="alert-heading mb-2"),
                        html.Ol(className="mb-1", children=[
                            html.Li("TIMS データを SCiLS RMS で出力"),
                            html.Li(["正規化 ", html.B("「OFF」"), " ＋ 変換 ", html.B("「log1p」"),
                                     "（RMS×TIC の二重正規化を回避。TIMS では既定でこの設定）"]),
                            html.Li("切片を選択：PCAを必ず保存し、2切片以上ならHarmony・RPCAも計算"),
                            html.Li("UMAP・クラスタリング"),
                        ]),
                        html.Small("各結果は解析後に切り替えて閲覧できます。",
                                   className="text-muted"),
                        html.Br(),
                        html.Small(["詳細は ",
                                    html.A("説明書の『標準フロー』",
                                           href="/help/analysis#standard-flow", target="_blank"),
                                    " を参照。"], className="text-muted"),
                    ])],
                ),
                html.Div(
                    id="desi_recommended_banner",
                    style={"display": "none"},
                    children=[dbc.Alert(color="info", className="mb-3", children=[
                        html.H6("💡 標準の使い方（DESI・生データ）",
                                className="alert-heading mb-2"),
                        html.Ol(className="mb-1", children=[
                            html.Li("DESI データ（生データ）を入力"),
                            html.Li(["正規化 ", html.B("「ON」"),
                                     "（LogNormalize＝TIC正規化＋log。DESI では既定でこの設定）"]),
                            html.Li("切片を選択：PCAを必ず保存し、2切片以上ならHarmony・RPCAも計算"),
                            html.Li("UMAP・クラスタリング"),
                        ]),
                        html.Small("各結果は解析後に切り替えて閲覧できます。",
                                   className="text-muted"),
                        html.Br(),
                        html.Small(["詳細は ",
                                    html.A("説明書の『標準フロー』",
                                           href="/help/analysis#standard-flow", target="_blank"),
                                    " を参照。"], className="text-muted"),
                    ])],
                ),
                dbc.Row(align="start", children=[
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("データフォルダ・サンプル選択"),
                            html.Div(
                                style={"display": "flex", "gap": "5px", "marginBottom": "4px"},
                                children=[
                                    dbc.Input(id="data_folder",
                                              value=ls.get("data_folder", DEFAULT_DESI_DATA_FOLDER),
                                              placeholder="データフォルダのパス"),
                                    dbc.Button("参照...", id="browse_folder", size="sm", color="secondary"),
                                ],
                            ),
                            html.Span(id="data_folder_badge", children="", style={"fontSize": "0.8rem"}),
                            html.Small(
                                id="data_folder_path_hint", children="",
                                style={"color": "#6c757d", "fontSize": "0.75rem",
                                       "marginTop": "2px", "display": "block"},
                            ),
                            html.Div(id="sample_selector"),
                            dcc.Store(id="selected_samples_store", data=[]),
                            # ★ ver64.0: チェックしたファイルの「フルパス」。
                            #   サンプル名 (stem) だけでは別フォルダの同名ファイルを
                            #   区別できず、片方を外せない / 両方が黙って解析に入る、
                            #   という食い違いが起きていた。解析対象はこちらを正とする。
                            dcc.Store(id="selected_sample_paths_store", data=[]),
                            dbc.FormText("チェックを入れたサンプルが解析対象になります"),
                            # ★ ver67.0: 旧設定の復元先だけ残し、実行方針は切片数から自動決定する。
                            html.Div(style={"display": "none"}, children=[
                                dbc.Input(id="desi_scenario", value="correct"),
                                dbc.Checkbox(id="desi_use_roi_as_sample", value=False),
                                html.Div(id="desi_roi_selector"),
                                dcc.Store(id="desi_roi_filter_store", data=None),
                            ]),
                            # --- 追加データフォルダ (TIMS複数フォルダ) ---
                            html.Div(
                                id="extra_folders_section",
                                style={"display": "none", "marginTop": "10px"},
                                children=[
                                    html.Hr(className="my-2"),
                                    html.Small("追加データフォルダ (TIMS)", className="fw-bold"),
                                    html.Div(id="extra_data_folders_container"),
                                    dbc.Button(
                                        "＋ フォルダ追加",
                                        id="btn_add_extra_folder",
                                        size="sm", color="info", outline=True,
                                        style={"marginTop": "5px"},
                                    ),
                                    # ★ ver64.0: 上の「データフォルダ」と同じく前回値を復元する。
                                    #   従来この Store だけ既定 (memory) の空リストで、
                                    #   ブラウザを再読込しただけで追加フォルダが消えていた。
                                    #   消えたことは画面のどこにも出ないので、利用者は
                                    #   1 フォルダ分だけで解析したことに気づけない。
                                    dcc.Store(id="extra_data_folders_store",
                                              data=ls.get("extra_data_folders", [])),
                                    dcc.Store(id="extra_folder_pending_store", data=""),
                                ],
                            ),
                            html.Div(id="annotation_selector", style={"display": "none"}),
                            _section_controls(ls),
                            dcc.Store(id="annotation_filter_store", data=None),
                        ]),
                        # RDS途中再開
                        html.Div(className="param-group", style={"marginTop": "15px"}, children=[
                            html.H5("RDSファイル"),
                            dbc.Checkbox(id="resume_rds", label=html.Span(["途中再開 (RDSから)", help_badge("resume_rds")]),
                                        value=ls.get("resume_rds", False)),
                            html.Div(
                                id="resume_rds_panel",
                                style={"display": "none", "marginTop": "10px"},
                                children=[
                                    dbc.Input(id="rds_folder", value=ls.get("rds_folder", ""),
                                              placeholder="RDSファイルが入っているフォルダ"),
                                    dbc.Button("参照...", id="browse_rds_folder", size="sm", color="secondary",
                                               style={"marginTop": "5px"}),
                                    html.Span(id="rds_folder_badge", children="",
                                              style={"fontSize": "0.8rem", "display": "block", "marginTop": "2px"}),
                                    html.Small(
                                        id="rds_folder_path_hint", children="",
                                        style={"color": "#6c757d", "fontSize": "0.75rem",
                                               "marginTop": "2px", "display": "block"},
                                    ),
                                ],
                            ),
                        ]),
                    ]),
                    dbc.Col(width=6, children=[
                        # ★ ver67.0: 登録済み分子名は自動使用し、追加 DB 照合だけ明示選択する。
                        html.Details(className="param-group", children=[
                            html.Summary("分子情報の追加", style={"cursor": "pointer", "fontWeight": "600"}),
                            dbc.Checklist(id="use_annotation_check",
                                options=[{"label": "代謝物データベースで追加照合する", "value": "db"}],
                                value=["db"] if "db" in ls.get("use_annotation_check", []) else [],
                                switch=True, className="mt-2"),
                            html.Div(id="db_annotation_settings", children=[
                                dbc.Input(id="annotation_path", value=ls.get("annotation_path", ls.get("mrm_path", "")),
                                          placeholder="代謝物データベース CSV (TIMS) / MRM .xlsx (DESI)", className="mt-2"),
                                dbc.Button("参照...", id="browse_annotation", size="sm", color="secondary", style={"marginTop": "5px"}),
                                html.Small(id="annotation_path_path_hint", children="",
                                           style={"color": "#6c757d", "fontSize": "0.75rem", "display": "block"}),
                            ]),
                            dbc.FormText("SCiLS登録済み名称を自動使用します。追加DB照合は名称のない特徴量だけを補完します。"),
                        ]),
                        # 正規化設定（DESI/TIMS共通・UMAP解析時のみ表示）
                        html.Div(className="param-group", style={"marginTop": "15px"}, children=[
                            html.H5(["正規化 (LogNormalize)", help_badge("normalize_input")]),
                            dbc.RadioItems(
                                id="normalize_input",
                                options=[
                                    {"label": "ON（LogNormalize を実行）", "value": "ON"},
                                    {"label": "OFF（正規化済み入力: SCiLS RMS 等）", "value": "OFF"},
                                ],
                                value=ls.get("normalize_input", "OFF" if ls.get("analysis_method_tims") == "tims_v8" else "ON"),
                            ),
                            # ★ ver57.5: 直前に「自動切替」で入れた既定値の控え。
                            #   現在値がこれと違えば利用者が選んだ値なので、
                            #   解析法を変えても書き戻さない (画面の「手動変更可」)。
                            #   初期値は復元済みの解析法から決まる既定値。
                            dcc.Store(
                                id="normalize_default_owner",
                                data=("OFF" if ls.get("analysis_method_tims") == "tims_v8"
                                      else "ON"),
                            ),
                            html.Div(style={"marginTop": "8px"}, children=[
                                html.Small("OFF時の変換 (NORM_MODE)", className="fw-bold"),
                                dbc.Select(
                                    id="norm_mode",
                                    options=[
                                        {"label": "log1p（log変換・推奨）", "value": "log1p"},
                                        {"label": "sqrt（平方根）", "value": "sqrt"},
                                        {"label": "none（変換なし・生RMS）", "value": "none"},
                                    ],
                                    value=ls.get("norm_mode", "log1p"),
                                    style={"width": "70%"},
                                ),
                            ]),
                            dbc.FormText(
                                "TIMS(SCiLS RMS等で正規化済み)は既定OFF＝二重正規化を回避。"
                                "DESI(生データ)は既定ON。解析法に応じて自動切替（手動変更可）。",
                                className="text-muted small",
                            ),
                            html.Details([
                                html.Summary(
                                    "📚 正規化の考え方（RMS／二重正規化）",
                                    style={"cursor": "pointer", "fontWeight": "600",
                                           "fontSize": "0.85rem", "color": "#495057",
                                           "marginTop": "6px"},
                                ),
                                html.Div(
                                    className="text-muted small",
                                    style={"marginTop": "6px", "paddingLeft": "10px",
                                           "borderLeft": "3px solid #dee2e6"},
                                    children=[
                                        html.P(
                                            "RMS等で正規化済みの入力（SCiLS RMS など）は"
                                            "「正規化 OFF」(INPUT_NORMALIZED=TRUE)＋NORM_MODE=log1p "
                                            "が正しい設定です。",
                                            className="mb-1",
                                        ),
                                        html.Ul(className="mb-0", children=[
                                            html.Li(
                                                "「正規化 ON」＝LogNormalize＝各スポットを総量(TIC)で"
                                                "割る＋log。RMS済みに ON すると RMS×TIC の二重正規化です。"),
                                            html.Li(
                                                "RMS が消すのは「全体強度（明るさ）」のみ。m/z個別の差や"
                                                "ドリフト等の構造的バッチは残ります（RMS はバッチ補正ではない）。"),
                                            html.Li(
                                                "UMAP には『RMS＋log1p』を使用。生データの直入れは避けてください。"),
                                        ]),
                                    ],
                                ),
                            ], style={"marginTop": "4px"}),
                        ]),
                        # TIMS イオンモード設定（TIMS選択時のみ表示）
                        html.Div(
                            id="tims_ion_settings",
                            style={"display": "none", "marginTop": "15px"},
                            children=[
                                html.Div(style={"display": "none"}, children=[
                                    dbc.Input(id="tims_scenario", value="integrate_correct"),
                                ]),
                                html.Div(className="param-group", children=[
                                    html.Div(id="ion_mode_panel", children=[
                                    html.H5(["イオンモード", help_badge("ion_mode")]),
                                    dbc.RadioItems(
                                        id="ion_mode",
                                        options=[
                                            {"label": "Positive", "value": "Positive"},
                                            {"label": "Negative", "value": "Negative"},
                                        ],
                                        value=ls.get("ion_mode", "Positive"), inline=True,
                                    ),
                                    ]),
                                    html.Div(id="db_tolerance_panel", children=[
                                    html.H5(["DB照合のm/z許容誤差 (Da)", help_badge("tolerance_mz")], style={"marginTop": "10px"}),
                                    dbc.Input(id="tolerance_mz", type="number",
                                              value=ls.get("tolerance_mz", 0.01), min=0, step=0.001,
                                              style={"width": "50%"}),
                                    ]),
                                    html.H5(["m/z アライメント (ppm)", help_badge("mz_align_ppm")], style={"marginTop": "10px"}),
                                    dbc.Input(id="mz_align_ppm", type="number",
                                              value=ls.get("mz_align_ppm", 0),
                                              min=0, max=500, step=1,
                                              style={"width": "50%"}),
                                    dbc.FormText("0 = 無効。複数サンプル間でm/z値を統一する許容誤差 (ppm)"),
                                    html.Div(id="db_adduct_panel", children=[
                                    html.H5(["Adductフィルター", help_badge("adduct_filter")], style={"marginTop": "10px"}),
                                    dbc.Checklist(
                                        id="adduct_filter",
                                        options=[
                                            {"label": "+H", "value": "+H"},
                                            {"label": "+Na", "value": "+Na"},
                                            {"label": "+NH4", "value": "+NH4"},
                                            {"label": "+K", "value": "+K"},
                                            {"label": "-H", "value": "-H"},
                                        ],
                                        # 復元したイオンモードに合わせる。固定値だと
                                        # Negative 復元時に Positive 4 種のまま残る
                                        # (自動切替は prevent_initial_call で働かない)
                                        value=(ls.get("adduct_filter")
                                               or adducts_for_ion_mode(
                                                   ls.get("ion_mode", "Positive"))),
                                        inline=True,
                                    ),
                                    ]),
                                    # --- m/z キャリブレーション ---
                                    html.Hr(style={"marginTop": "15px", "marginBottom": "10px"}),
                                    html.H5(["m/z キャリブレーション", help_badge("calibration")],
                                            style={"marginTop": "5px"}),
                                    dbc.Checkbox(
                                        id="calibration_enable",
                                        label="マトリクスピークでキャリブレーション",
                                        value=ls.get("calibration_enable", DEFAULT_CALIBRATION_ENABLE),
                                    ),
                                    html.Div(
                                        id="calibration_detail_panel",
                                        style={"display": "none", "marginTop": "10px",
                                               "padding": "10px", "background": "#f8f9fa",
                                               "borderRadius": "5px"},
                                        children=[
                                            dbc.Label("マトリクス種"),
                                            dbc.Select(
                                                id="calibration_matrix",
                                                options=[
                                                    {"label": "DHB (2,5-Dihydroxybenzoic acid)", "value": "DHB"},
                                                    {"label": "CHCA (α-Cyano-4-hydroxycinnamic acid)", "value": "CHCA"},
                                                    {"label": "9-AA (9-Aminoacridine)", "value": "9AA"},
                                                    {"label": "カスタム (手動入力)", "value": "custom"},
                                                ],
                                                value=ls.get("calibration_matrix", DEFAULT_CALIBRATION_MATRIX),
                                            ),
                                            # ---- キャリブレーション プリセット ----
                                            html.Hr(style={"margin": "8px 0"}),
                                            dbc.Row([
                                                dbc.Col(width=7, children=[
                                                    dcc.Dropdown(
                                                        id="cal_preset_select",
                                                        options=_cal_preset_options(),
                                                        placeholder="過去のキャリブレーションを選択...",
                                                        clearable=True,
                                                        style={"fontSize": "13px"},
                                                    ),
                                                ]),
                                                dbc.Col(width=5, children=[
                                                    dbc.InputGroup(size="sm", children=[
                                                        dbc.Input(
                                                            id="cal_preset_name_input",
                                                            placeholder="プリセット名",
                                                            style={"fontSize": "12px"},
                                                        ),
                                                        dbc.Button(
                                                            "保存", id="cal_preset_save_btn",
                                                            color="success", outline=True, size="sm",
                                                        ),
                                                        dbc.Button(
                                                            "削除", id="cal_preset_delete_btn",
                                                            color="danger", outline=True, size="sm",
                                                        ),
                                                    ]),
                                                ]),
                                            ], className="mb-2"),
                                            html.Small(id="cal_preset_status",
                                                       style={"color": "gray"}),
                                            dcc.Store(id="cal_per_sample_store", data={}),
                                            dcc.Store(id="cal_sample_selector_prev", data="__all__"),
                                            html.Hr(style={"margin": "8px 0"}),
                                            dbc.Label("キャリブレーション対象",
                                                      className="small mt-1"),
                                            dcc.Dropdown(
                                                id="cal_sample_selector",
                                                options=[{"label": "全サンプル共通",
                                                          "value": "__all__"}],
                                                value="__all__",
                                                clearable=False,
                                                style={"fontSize": "13px",
                                                       "marginBottom": "8px"},
                                            ),
                                            html.Div(
                                                style={"marginTop": "10px"},
                                                children=[
                                                    dbc.Label("リファレンス / 実測値 対応表"),
                                                    dash_table.DataTable(
                                                        id="calibration_table",
                                                        columns=[
                                                            {"name": "Reference m/z", "id": "ref_mz",
                                                             "editable": True, "type": "numeric"},
                                                            {"name": "Formula", "id": "formula",
                                                             "editable": True, "type": "text"},
                                                            {"name": "Observed m/z", "id": "obs_mz",
                                                             "editable": True, "type": "numeric"},
                                                            {"name": "Δppm", "id": "ppm_drift",
                                                             "editable": False, "type": "text"},
                                                        ],
                                                        editable=True,
                                                        data=ls.get("calibration_table_data", []),
                                                        row_selectable="multi",
                                                        style_table={"overflowX": "auto"},
                                                        style_cell={
                                                            "textAlign": "center",
                                                            "padding": "5px",
                                                            "fontSize": "0.85rem",
                                                            "minWidth": "90px",
                                                        },
                                                        style_header={
                                                            "backgroundColor": "#f8f9fa",
                                                            "fontWeight": "600",
                                                        },
                                                        style_data_conditional=[
                                                            {"if": {"filter_query": '{obs_mz} eq ""'},
                                                             "backgroundColor": "#f5f5f5",
                                                             "color": "#aaa"},
                                                        ],
                                                    ),
                                                    html.Div(
                                                        className="d-flex gap-2 mt-2",
                                                        children=[
                                                            dbc.Button(
                                                                "行追加",
                                                                id="calibration_add_row",
                                                                size="sm", color="secondary",
                                                                outline=True,
                                                            ),
                                                            dbc.Button(
                                                                "選択行削除",
                                                                id="calibration_delete_rows",
                                                                size="sm", color="danger",
                                                                outline=True,
                                                            ),
                                                            dbc.Button(
                                                                "ピーク自動検出",
                                                                id="calibration_auto_detect",
                                                                size="sm", color="info",
                                                            ),
                                                            dbc.Button(
                                                                "List保存",
                                                                id="calibration_save_list",
                                                                size="sm", color="success",
                                                                outline=True,
                                                            ),
                                                            dbc.Button(
                                                                "リセット",
                                                                id="calibration_reset_list",
                                                                size="sm", color="warning",
                                                                outline=True,
                                                            ),
                                                        ],
                                                    ),
                                                    html.Div(
                                                        id="calibration_status_text",
                                                        style={"marginTop": "8px",
                                                               "fontSize": "12px",
                                                               "color": "#666"},
                                                    ),
                                                    dbc.FormText(
                                                        "マトリクス種変更でリファレンス値リセット。"
                                                        "データ読込後「ピーク自動検出」で実測値を検索。"
                                                    ),
                                                ],
                                            ),
                                            html.Details([
                                                html.Summary("詳細設定",
                                                             style={"cursor": "pointer",
                                                                    "fontSize": "12px",
                                                                    "marginTop": "8px"}),
                                                html.Div(style={"marginTop": "5px"}, children=[
                                                    dbc.Row([
                                                        dbc.Col(width=6, children=[
                                                            dbc.Label("検索ウィンドウ (Da)",
                                                                      className="small"),
                                                            dbc.Input(
                                                                id="calibration_search_window",
                                                                type="number",
                                                                value=ls.get("calibration_search_window",
                                                                             DEFAULT_CALIBRATION_SEARCH_WINDOW),
                                                                min=0.01, max=2.0, step=0.01,
                                                            ),
                                                        ]),
                                                        dbc.Col(width=6, children=[
                                                            dbc.Label("最低マッチピーク数",
                                                                      className="small"),
                                                            dbc.Input(
                                                                id="calibration_min_peaks",
                                                                type="number",
                                                                value=ls.get("calibration_min_peaks",
                                                                             DEFAULT_CALIBRATION_MIN_PEAKS),
                                                                min=1, max=10, step=1,
                                                            ),
                                                        ]),
                                                    ]),
                                                    dbc.Row([
                                                        dbc.Col(width=5, children=[
                                                            dbc.Label("回帰モデル",
                                                                      className="small"),
                                                        ]),
                                                        dbc.Col(width=7, children=[
                                                            dbc.Select(
                                                                id="calibration_regression_mode",
                                                                options=[
                                                                    {"label": "線形 (1次)",
                                                                     "value": "linear"},
                                                                    {"label": "多項式 (2次)",
                                                                     "value": "poly2"},
                                                                    {"label": "多項式 (3次)",
                                                                     "value": "poly3"},
                                                                ],
                                                                value=ls.get(
                                                                    "calibration_regression_mode",
                                                                    DEFAULT_CALIBRATION_REGRESSION),
                                                            ),
                                                        ]),
                                                    ], className="mt-2"),
                                                ]),
                                            ]),
                                        ],
                                    ),
                                ]),
                            ],
                        ),
                    ]),
                ]),

                # 詳細設定（折りたたみ）
                html.Details([
                    html.Summary(
                        "🎛 詳細設定（p値閾値・log2FC閾値）",
                        style={"cursor": "pointer", "color": "#666", "fontSize": "13px", "marginTop": "10px"},
                    ),
                    html.Div(
                        style={"background": "#f8f9fa", "padding": "15px", "borderRadius": "5px", "marginTop": "5px"},
                        children=[
                            dbc.Row([
                                dbc.Col(width=6, children=[
                                    dbc.Label(["p値閾値", help_badge("p_thresh")]),
                                    dbc.Input(id="p_thresh", type="number",
                                              value=ls.get("p_thresh", 0.05), min=0, max=1, step=0.01),
                                ]),
                                dbc.Col(width=6, children=[
                                    dbc.Label(["log2FC閾値", help_badge("logfc_thresh")]),
                                    dbc.Input(id="logfc_thresh", type="number",
                                              value=ls.get("logfc_thresh", 0.25), min=0, step=0.05),
                                ]),
                            ]),
                        ],
                    ),
                ]),

            ],
        ),

        # 再解析設定（DESI/TIMS共通）
        html.Div(
            id="reanalysis_settings_panel",
            style={"display": "none"},
            children=[
                html.H4(className="card-title", children=["🔍 再解析設定"]),
                # ★ ver58.0 (デバッグ総点検 A-6/A-7/A-10): この再解析で実際に使う条件。
                #   正規化・UMAP・m/z アライメント・化合物名の由来を決める入力欄は
                #   umap_settings_panel の中にあり、再解析中は画面から隠れる。
                #   隠れた欄の値が黙って使われる状態を避けるため、
                #   何が使われるのかをここに実値で出す（表示専用）。
                html.Div(id="reanalysis_inherited_note", style={"display": "none"}),
                dbc.Row([
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("データフォルダ・サンプル選択"),
                            html.Div(
                                style={"display": "flex", "gap": "5px", "marginBottom": "10px"},
                                children=[
                                    dbc.Input(id="reanalysis_data_folder",
                                              value=ls.get("reanalysis_data_folder", DEFAULT_DESI_DATA_FOLDER),
                                              placeholder="データフォルダのパス"),
                                    dbc.Button("参照...", id="browse_reanalysis_folder",
                                               size="sm", color="secondary"),
                                ],
                            ),
                            html.Small(
                                id="reanalysis_data_folder_path_hint", children="",
                                style={"color": "#6c757d", "fontSize": "0.75rem",
                                       "marginTop": "2px", "display": "block"},
                            ),
                            html.Div(id="sample_selector_reanalysis"),
                            dbc.FormText("チェックを入れたサンプルが再解析対象になります"),
                            html.Div(id="annotation_selector_reanalysis", style={"display": "none"}),
                            _section_controls(ls, "reanalysis"),
                            dcc.Store(id="reanalysis_source_manifest_store", data=(
                                {"manifest": ls["section_manifest_reanalysis"],
                                 "data_folder": ls["section_manifest_reanalysis"].get("source_data_folder", ""),
                                 "rds_path": ls["section_manifest_reanalysis"].get("source_rds_path", "")}
                                if (ls.get("section_manifest_reanalysis") or {}).get("source_rds_path") else None)),
                            dcc.Store(id="annotation_filter_store_reanalysis", data=None),
                        ]),
                        # --- フィルタモード（Row 1 左カラムに統合）---
                        html.Div(className="param-group", style={"marginTop": "10px"}, children=[
                            html.H5(["フィルタモード", help_badge("filter_mode")]),
                            dbc.RadioItems(
                                id="filter_mode",
                                options=[
                                    {"label": "除外 (exclude)", "value": "exclude"},
                                    {"label": "抽出 (keep)", "value": "keep"},
                                ],
                                value=ls.get("filter_mode", "exclude"), inline=True,
                            ),
                        ]),
                        # --- 対象クラスタ（Row 1 左カラムに統合）---
                        html.Div(className="param-group", style={"marginTop": "10px"}, children=[
                            html.H5("対象クラスタ"),
                            dbc.Input(id="target_clusters", placeholder="例: 0, 1, 5, 7",
                                      value=ls.get("target_clusters", "")),
                            dbc.FormText("カンマ区切りでクラスタ番号を入力"),
                        ]),
                    ]),
                    dbc.Col(width=6, children=[
                        html.Div(className="param-group", children=[
                            html.H5("RDS指定"),
                            html.H6("RDSフォルダ"),
                            html.Div(
                                style={"display": "flex", "gap": "5px", "marginBottom": "5px"},
                                children=[
                                    dbc.Input(id="rds_folder_reanalysis",
                                              value=ls.get("rds_folder_reanalysis", ""),
                                              placeholder="RDS_Filesフォルダのパス"),
                                    dbc.Button("参照...", id="browse_rds_folder_reanalysis",
                                               size="sm", color="secondary"),
                                ],
                            ),
                            html.Small(
                                id="rds_folder_reanalysis_path_hint", children="",
                                style={"color": "#6c757d", "fontSize": "0.75rem",
                                       "marginTop": "2px", "display": "block"},
                            ),
                            html.Span(id="rds_detection_badge"),
                            html.Div(
                                id="cluster_source_container",
                                style={"display": "none"},
                                children=[
                                    html.H6(["クラスタソース", help_badge("cluster_source")], style={"marginTop": "5px"}),
                                    dbc.RadioItems(
                                        id="cluster_source",
                                        options=[
                                            {"label": "PCA", "value": "pca"},
                                            {"label": "Harmony", "value": "harmony"},
                                            {"label": "RPCA", "value": "rpca"},
                                        ],
                                        value=ls.get("cluster_source", "rpca"),
                                        inline=True,
                                    ),
                                    html.Details([
                                        html.Summary(
                                            "📚 補正手法（Harmony/RPCA）と交絡の注意",
                                            style={"cursor": "pointer", "fontWeight": "600",
                                                   "fontSize": "0.85rem", "color": "#495057",
                                                   "marginTop": "6px"},
                                        ),
                                        html.Div(
                                            className="text-muted small",
                                            style={"marginTop": "6px", "paddingLeft": "10px",
                                                   "borderLeft": "3px solid #dee2e6"},
                                            children=[
                                                html.P(
                                                    "Harmony/RPCA は切片間の差を調整した結果です。"
                                                    "生物学的な差も変わり得るためPCAと比較してください。",
                                                    className="mb-1",
                                                ),
                                                html.Ul(className="mb-0", children=[
                                                    html.Li(
                                                        "各条件が1切片のみ（バッチ=条件が交絡）の場合、"
                                                        "補正は技術差と一緒に生物差も除去します（過補正）。"),
                                                    html.Li(
                                                        "補正の影響はデータと設定に依存します。"
                                                        "技術差と生物学的な差を完全に分離できるとは限りません。"),
                                                    html.Li(
                                                        "段階差の比較が目的なら、未補正(PCA)の結果も必ず併用を。"),
                                                ]),
                                            ],
                                        ),
                                    ], style={"marginTop": "4px"}),
                                ],
                            ),
                            # 後方互換: rds_path を非表示で維持（既存State参照用）
                            dbc.Input(id="rds_path", value=ls.get("rds_path", ""),
                                      style={"display": "none"}),
                        ]),
                        # --- 途中から再開（上の「RDS指定」とは別物）---
                        # 「RDS指定」は "どのクラスタリングの番号で除外するか" を選ぶ欄であり、
                        # 再開地点ではない。混同されやすいため独立した param-group として離し、
                        # ラベルと説明文で明確に区別する。
                        html.Div(className="param-group", style={"marginTop": "15px"}, children=[
                            html.H5(["途中から再開", help_badge("resume_reanalysis")]),
                            dbc.Checkbox(
                                id="resume_reanalysis",
                                label="前回の中間結果を使う（Step1/Step2 をやり直さない）",
                                value=ls.get("resume_reanalysis", False),
                            ),
                            html.Div(
                                id="resume_reanalysis_panel",
                                style={"display": "none", "marginTop": "10px"},
                                children=[
                                    html.Div(
                                        style={"display": "flex", "gap": "5px", "marginBottom": "5px"},
                                        children=[
                                            dbc.Input(
                                                id="resume_reanalysis_dir",
                                                value=ls.get("resume_reanalysis_dir", ""),
                                                placeholder="前回実行の RDS_Files フォルダ",
                                            ),
                                            dbc.Button("参照...", id="browse_resume_reanalysis_dir",
                                                       size="sm", color="secondary"),
                                        ],
                                    ),
                                    html.Small(
                                        id="resume_reanalysis_dir_path_hint", children="",
                                        style={"color": "#6c757d", "fontSize": "0.75rem",
                                               "marginTop": "2px", "display": "block"},
                                    ),
                                    dbc.Alert(
                                        color="warning",
                                        className="py-2 px-3 mt-2 mb-0",
                                        style={"fontSize": "0.8rem"},
                                        children=[
                                            html.Div("上の「RDS指定」とは別の設定です。",
                                                     style={"fontWeight": "600"}),
                                            html.Div("「RDS指定」＝どのクラスタリングの番号で除外するか。"),
                                            html.Div("ここ＝どこまで計算済みの結果を再利用するか。"),
                                            html.Div("主に動作検証用です。通常の解析では OFF のままにしてください。",
                                                     style={"marginTop": "4px"}),
                                        ],
                                    ),
                                ],
                            ),
                        ]),
                        # TIMS 再解析イオンモード
                        html.Div(
                            id="tims_reanalysis_ion_settings",
                            style={"display": "none", "marginTop": "15px"},
                            children=[
                                html.Div(style={"display": "none"}, children=[
                                    dbc.Input(id="reanalysis_tims_scenario", value="integrate_correct"),
                                ]),
                                html.Div(className="param-group", children=[
                                    html.Div(id="reanalysis_ion_mode_panel", children=[
                                    html.H5("イオンモード"),
                                    dbc.RadioItems(
                                        id="reanalysis_ion_mode",
                                        options=[
                                            {"label": "Positive", "value": "Positive"},
                                            {"label": "Negative", "value": "Negative"},
                                        ],
                                        value=ls.get("reanalysis_ion_mode", "Positive"), inline=True,
                                    ),
                                    ]),
                                    html.Div(id="reanalysis_db_match_panel", children=[
                                    html.H5("DB照合のm/z許容誤差 (Da)", style={"marginTop": "10px"}),
                                    dbc.Input(id="reanalysis_tolerance_mz", type="number",
                                              value=ls.get("reanalysis_tolerance_mz", 0.01),
                                              min=0, step=0.001, style={"width": "50%"}),
                                    html.H5("Adductフィルター", style={"marginTop": "10px"}),
                                    dbc.Checklist(
                                        id="reanalysis_adduct_filter",
                                        options=[
                                            {"label": "+H", "value": "+H"},
                                            {"label": "+Na", "value": "+Na"},
                                            {"label": "+NH4", "value": "+NH4"},
                                            {"label": "+K", "value": "+K"},
                                            {"label": "-H", "value": "-H"},
                                        ],
                                        value=(ls.get("reanalysis_adduct_filter")
                                               or adducts_for_ion_mode(
                                                   ls.get("reanalysis_ion_mode",
                                                          "Positive"))),
                                        inline=True,
                                    ),
                                    ]),
                                ]),
                                # --- 正規化（再解析・TIMSはRMS正規化済みのため既定OFF=二重回避） ---
                                html.Div(className="param-group", style={"marginTop": "15px"}, children=[
                                    html.H5("正規化 (LogNormalize)"),
                                    dbc.RadioItems(
                                        id="normalize_input_reanalysis",
                                        options=[
                                            {"label": "ON（LogNormalize を実行）", "value": "ON"},
                                            {"label": "OFF（正規化済み入力: SCiLS RMS 等）", "value": "OFF"},
                                        ],
                                        value=ls.get("normalize_input_reanalysis", "OFF"),
                                    ),
                                    html.Div(style={"marginTop": "8px"}, children=[
                                        html.Small("OFF時の変換 (NORM_MODE)", className="fw-bold"),
                                        dbc.Select(
                                            id="norm_mode_reanalysis",
                                            options=[
                                                {"label": "log1p（log変換・推奨）", "value": "log1p"},
                                                {"label": "sqrt（平方根）", "value": "sqrt"},
                                                {"label": "none（変換なし・生RMS）", "value": "none"},
                                            ],
                                            value=ls.get("norm_mode_reanalysis", "log1p"),
                                            style={"width": "70%"},
                                        ),
                                    ]),
                                    dbc.FormText(
                                        "TIMS再解析は元データがRMS正規化済みのため既定OFF（二重正規化を回避）。",
                                        className="text-muted small",
                                    ),
                                ]),
                                # --- m/z キャリブレーション（再解析） ---
                                html.Hr(style={"marginTop": "15px"}),
                                html.H5("m/z キャリブレーション"),
                                dbc.Checkbox(
                                    id="reanalysis_calibration_use_previous",
                                    label="前回の解析の回帰式でキャリブレーション",
                                    value=ls.get("reanalysis_calibration_use_previous", False),
                                ),
                                html.Div(
                                    id="reanalysis_calibration_info",
                                    style={"display": "none"},
                                    children=[
                                        html.Div(
                                            id="reanalysis_calibration_details",
                                            style={
                                                "background": "#f0f9f0",
                                                "padding": "10px",
                                                "borderRadius": "5px",
                                                "marginTop": "5px",
                                                "fontSize": "13px",
                                            },
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ]),
                ]),
                html.Details(className="param-group", style={"marginTop": "15px"}, children=[
                    html.Summary("分子情報の追加", style={"cursor": "pointer", "fontWeight": "600"}),
                    dbc.Checklist(id="reanalysis_use_annotation_check",
                                  options=[{"label": "代謝物データベースで追加照合する", "value": "db"}],
                                  value=["db"] if "db" in ls.get("reanalysis_use_annotation_check", []) else [],
                                  switch=True, className="mt-2"),
                    html.Div(id="reanalysis_db_annotation_settings", children=[
                        html.Div(style={"display": "flex", "gap": "5px"}, children=[
                            dbc.Input(id="reanalysis_annotation_path", value=ls.get("reanalysis_annotation_path", ""),
                                      placeholder="追加照合用データベースのパス"),
                            dbc.Button("参照...", id="browse_reanalysis_annotation", size="sm", color="secondary"),
                        ]),
                        html.Small(id="reanalysis_annotation_path_path_hint", children="",
                                   style={"color": "#6c757d", "fontSize": "0.75rem", "display": "block"}),
                        dbc.FormText(".xlsx (DESI) / .csv (TIMS)"),
                    ]),
                ]),
                # 再解析 詳細設定
                html.Details([
                    html.Summary(
                        "🎛 詳細設定（p値閾値・log2FC閾値）",
                        style={"cursor": "pointer", "color": "#666", "fontSize": "13px", "marginTop": "10px"},
                    ),
                    html.Div(
                        style={"background": "#f8f9fa", "padding": "15px", "borderRadius": "5px", "marginTop": "5px"},
                        children=[
                            dbc.Row([
                                dbc.Col(width=6, children=[
                                    dbc.Label("p値閾値"),
                                    dbc.Input(id="reanalysis_p_thresh", type="number",
                                              value=ls.get("reanalysis_p_thresh", 0.05),
                                              min=0, max=1, step=0.01),
                                ]),
                                dbc.Col(width=6, children=[
                                    dbc.Label("log2FC閾値"),
                                    dbc.Input(id="reanalysis_logfc_thresh", type="number",
                                              value=ls.get("reanalysis_logfc_thresh", 0.25),
                                              min=0, step=0.05),
                                ]),
                            ]),
                        ],
                    ),
                ]),
            ],
        ),
        html.Hr(),

        # 出力設定
        html.H5(["📁 出力設定"]),
        dbc.Row([
            dbc.Col(width=4, children=[
                html.H6(["出力フォルダー", help_badge("output_subfolder")]),
                dbc.Input(
                    id="output_subfolder",
                    value=datetime.now().strftime("Analysis_%Y%m%d_%H%M%S"),
                    placeholder="例: Analysis_20260109",
                ),
            ]),
            dbc.Col(width=4, children=[
                html.H6("出力先"),
                # 既定は永続化された解析出力ディレクトリ。以前は APP_BASE_DIR
                # (=コンテナ内の /app) が既定で、結果が書き込み層に残り
                # コンテナ再作成で消える・SFTP から見えない状態になっていた。
                dbc.Input(id="output_dir", value=ls.get("output_dir", str(OUTPUT_DATA_DIR))),
            ]),
            dbc.Col(width=4, children=[
                dbc.Button("参照...", id="browse_output", size="sm", color="secondary",
                           style={"marginTop": "25px"}),
            ]),
        ]),
        dbc.FormText("出力先の下にサブフォルダーとして作成されます"),
        html.Span(id="output_dir_badge", children="", style={"fontSize": "0.8rem"}),
        html.Small(
            id="output_dir_path_hint", children="",
            style={"color": "#6c757d", "fontSize": "0.75rem",
                   "marginTop": "2px", "display": "block"},
        ),
        html.Hr(),

        # ★ ver58.1 (デバッグ総点検 B-1〜B-3): 「いま復元している最中」の目印。
        #   プリセット読込・サブプロジェクトの「解析」・「再解析へ送る」は、
        #   保存値を書き戻すのと同じレスポンスで analysis_method や ion_mode も書く。
        #   すると**それを Input に持つ自動切替コールバックが発火し、復元された
        #   ばかりの値を既定値で塗り潰す**（Dash は同じ値を書いても下流を発火させる）。
        #   復元中はその自動切替を黙らせる。同型の対処が
        #   `int_cal_restore_pending` (interactive_calibration.py) に既にある。
        dcc.Store(id="settings_restore_pending", data=False),

        # プリフライトバリデーション結果
        html.Div(id="validation_summary", children="", style={"display": "none"}),

        # PreFlight 診断 ＋ UMAP ハイパーパラメータ
        _create_preflight_section(),

        # 実行ボタンエリア
        _create_run_button(),
    ])


def _create_preflight_section():
    """PreFlight 診断ボタン＋結果表示＋UMAP ハイパーパラメータ入力。

    - 診断: 完了済み解析の reduction RDS に run_diagnostics.R を実行し、
      推奨 dims / n.neighbors・許容域・推奨度・警告・交絡判定を表示（提案のみ）。
    - UMAP ハイパラ入力: 次回の「解析実行」へ注入される（analysis_runner 既存機構）。
    """
    metric_opts = [
        {"label": "cosine", "value": "cosine"},
        {"label": "euclidean", "value": "euclidean"},
    ]
    return html.Details([
        html.Summary(
            "🩺 PreFlight 診断 / UMAP ハイパーパラメータ",
            style={"cursor": "pointer", "fontWeight": "600",
                   "fontSize": "0.95rem", "marginBottom": "8px"},
        ),
        html.Div(className="param-group", children=[
            # UMAP ハイパーパラメータ入力（次回解析へ注入）
            dbc.Row([
                dbc.Col(width=3, children=[
                    dbc.Label("n.neighbors", html_for="umap_n_neighbors_input"),
                    dbc.Input(id="umap_n_neighbors_input", type="number",
                              value=30, min=2, max=100, step=1, size="sm"),
                    html.Small(
                        "近傍数。小=局所（細かいクラスタ）/大=大域（全体配置）を重視。"
                        "構造に効くため PreFlight が推奨を算出。",
                        className="text-muted d-block mt-1",
                    ),
                ]),
                dbc.Col(width=3, children=[
                    dbc.Label("min.dist", html_for="umap_min_dist_input"),
                    dbc.Input(id="umap_min_dist_input", type="number",
                              value=0.3, min=0, max=1, step=0.05, size="sm"),
                    html.Small(
                        "2D配置の密集度（見た目）のみ調整。近傍グラフ・クラスタは不変。"
                        "最適値はデータから決まらず既定0.3固定（好みで調整）。",
                        className="text-muted d-block mt-1",
                    ),
                ]),
                dbc.Col(width=3, children=[
                    dbc.Label("metric", html_for="umap_metric_input"),
                    dcc.Dropdown(id="umap_metric_input", options=metric_opts,
                                 value="cosine", clearable=False,
                                 style={"fontSize": "0.85rem"}),
                    html.Small(
                        "点間距離の測り方（cosine=方向/角度, euclidean=直線距離）。"
                        "高次元の PCA/Harmony 埋め込みは既定 cosine が無難。",
                        className="text-muted d-block mt-1",
                    ),
                ]),
                dbc.Col(width=3, children=[
                    dbc.Label("dims", html_for="umap_dims_input"),
                    dbc.Input(id="umap_dims_input", type="number",
                              value=30, min=2, max=50, step=1, size="sm"),
                    html.Small(
                        "UMAP に渡す reduction(PCA/Harmony) の次元数。多い=情報↑/ノイズ↑。"
                        "近傍の安定性に効くため PreFlight が推奨を算出。",
                        className="text-muted d-block mt-1",
                    ),
                ]),
            ]),
            dbc.FormText(
                "これらの設定は、新しく開始する通常解析と①に使用します。"
                "④と中断再開は元の実行に保存された条件を使用します。"
                "自動推奨は dims・n.neighbors のみ。③反映は手法間の最大値を採用"
                "（全手法が安定する共通値）。min.dist と metric は自動推奨せず既定"
                "（0.3 / cosine）を使用します（手動変更可）。"
            ),
            html.Div(
                style={"marginTop": "10px", "display": "flex", "gap": "10px",
                       "alignItems": "center", "flexWrap": "wrap"},
                children=[
                    dbc.Button("① reduction のみ作成（条件を保存）", id="btn_make_reduction",
                               size="sm", color="primary", outline=True),
                    dbc.Button("② 🩺 PreFlight 診断を実行", id="btn_preflight_run",
                               size="sm", color="info"),
                    dbc.Button("③ 推奨値を新しい解析へ反映", id="btn_preflight_apply",
                               size="sm", color="secondary", outline=True),
                    dbc.Button("④ 保存条件で続きを実行", id="btn_run_downstream",
                               size="sm", color="primary"),
                    dbc.Button("📂 前回の診断を表示（再計算なし）", id="btn_preflight_load",
                               size="sm", color="secondary", outline=True),
                ],
            ),
            # ★ ver67.0: ③→④では変更値を使わないため、推奨値の適用と保存条件の継続を分ける。
            dbc.FormText([
                html.B("診断: "),
                "①でreductionと条件を保存し、②で診断します。既存結果があれば②から診断できます。 ",
                html.B("保存条件のまま完成させる: "),
                "④で保存済みreductionを再利用し、保存されたUMAP・クラスタ条件で下流処理を実行します。 ",
                html.B("推奨値を使って新しく解析する: "),
                "③で入力欄を更新し、通常の「解析実行」または①から新しく実行してください。 ",
                "④の出力先には _continued を付けます。既存結果がある場合は従来どおり上書きを確認します。"
                "解析中は①・④とも実行できません。",
            ]),
            html.Details([
                html.Summary(
                    "📚 PreFlight 診断結果の読み方（交絡判定）",
                    style={"cursor": "pointer", "fontWeight": "600",
                           "fontSize": "0.85rem", "color": "#495057",
                           "marginTop": "6px"},
                ),
                html.Div(
                    className="text-muted small",
                    style={"marginTop": "6px", "paddingLeft": "10px",
                           "borderLeft": "3px solid #dee2e6"},
                    children=[
                        html.Ul(className="mb-0", children=[
                            html.Li([
                                html.B("設計(交絡): "),
                                "not_identifiable＝技術差と生物差が分離不能（各条件が1バッチのみ）。"
                                "この場合、補正結果を『生物差』として読まないでください。",
                            ]),
                            html.Li([
                                html.B("推奨度(confidence): "),
                                "high＞medium＞low。推奨 dims / n.neighbors の信頼度です。",
                            ]),
                            html.Li([
                                html.B("iLISI: "),
                                "バッチ混合の程度（高いほどよく混ざる＝バッチ差が小さい）。",
                            ]),
                            html.Li(
                                "交絡を解くには、各条件に反復切片(≥2)、または全ランで測る"
                                "共有QC・内部標準が必要です。"),
                        ]),
                    ],
                ),
            ], style={"marginTop": "10px"}),
            dcc.Loading(html.Div(id="preflight_results_container",
                                 style={"marginTop": "10px"})),
            dcc.Store(id="preflight_store"),
            dcc.Interval(id="preflight_poll", interval=1500, disabled=True),
        ]),
    ], style={"marginTop": "15px", "marginBottom": "10px"})


def _create_run_button():
    return html.Div([
        html.Div(
            className="run-section",
            style={"marginTop": "20px", "display": "flex", "alignItems": "center", "gap": "20px"},
            children=[
                html.Div(
                    style={"flex": "0 0 auto"},
                    children=[
                        dbc.Button(
                            ["▶ 解析実行"], id="run_analysis",
                            size="lg", color="primary",
                            style={"padding": "15px 50px", "fontSize": "1.2rem"},
                        ),
                        # [ver51.5] 他の解析が実行中のときに理由を出す。
                        #   停止ボタン側の analysis_owner_note と対になる。
                        html.Div(
                            id="analysis_busy_note", children="",
                            style={"fontSize": "0.8rem", "color": "#b8860b",
                                   "marginTop": "4px", "maxWidth": "320px"},
                        ),
                        # 実行中かどうかを定期確認する。編集ロックの
                        # edit_lock_heartbeat と同じく常時動かす（disabled にしない）
                        # ので、開いたままの画面にも他人の解析開始が伝わる。
                        dcc.Interval(
                            id="analysis_busy_poll",
                            interval=ANALYSIS_BUSY_POLL_INTERVAL_SEC * 1000,
                        ),
                    ],
                ),
                html.Div(
                    id="stop_button_container",
                    style={"flex": "0 0 auto", "display": "none"},
                    children=[
                        dbc.Button(
                            ["⏹ 実行停止"], id="stop_analysis",
                            size="lg", color="danger",
                            style={"padding": "15px 30px", "fontSize": "1.2rem"},
                        ),
                        # [ver51.2] 他人の解析に再接続したときの所有者表示。
                        #   自分の解析なら空のまま。
                        html.Div(
                            id="analysis_owner_note", children="",
                            style={"fontSize": "0.8rem", "color": "#6c757d",
                                   "marginTop": "4px", "textAlign": "center"},
                        ),
                    ],
                ),
                html.Div(
                    id="progress_container",
                    style={"flex": "1", "display": "none"},
                    children=[
                        html.Div(
                            style={"display": "flex", "justifyContent": "space-between", "alignItems": "center"},
                            children=[
                                html.H6(style={"margin": "0"}, children="解析進捗"),
                                html.Span(
                                    id="section_progress_text",
                                    style={"fontWeight": "bold", "fontSize": "0.9rem"},
                                    children="0/0 セクション",
                                ),
                            ],
                        ),
                        dbc.Progress(
                            id="analysis_progress_bar",
                            value=0, striped=True, animated=True,
                            style={"height": "25px", "marginTop": "5px"},
                        ),
                    ],
                ),
            ],
        ),

        # 上書き確認モーダル（出力先に既存結果があるときだけ表示）＋ 保留モード保持
        dbc.Modal(
            id="overwrite_results_modal",
            centered=True,
            children=[
                dbc.ModalHeader(dbc.ModalTitle("⚠️ 既存の解析結果があります")),
                dbc.ModalBody([
                    html.Div(id="overwrite_results_detail", className="mb-2"),
                    html.P(
                        "続行すると同名ファイルは上書きされ、新旧の結果が混在する可能性があります。"
                        "本当に実行しますか？",
                        className="text-danger fw-bold mb-0",
                    ),
                ]),
                dbc.ModalFooter([
                    dbc.Button("キャンセル", id="cancel_overwrite_results",
                               color="secondary", outline=True),
                    dbc.Button("実行する", id="confirm_overwrite_results",
                               color="danger"),
                ]),
            ],
        ),
        dcc.Store(id="overwrite_pending_mode", data="run"),

        # 進捗ログ表示
        html.Div(
            id="log_container",
            className="progress-container",
            style={"marginTop": "20px", "display": "none"},
            children=[
                html.H5("⏳ 解析中...", id="log_header"),
                # ログフィルタコントロール
                dbc.Row(className="mb-2 g-2", children=[
                    dbc.Col(width=4, children=[
                        dbc.Input(
                            id="log_search_input",
                            placeholder="ログ検索...",
                            size="sm",
                            debounce=True,
                        ),
                    ]),
                    dbc.Col(width=3, children=[
                        dcc.Dropdown(
                            id="log_level_filter",
                            options=[
                                {"label": "すべて", "value": "all"},
                                {"label": "Error", "value": "error"},
                                {"label": "Warning", "value": "warning"},
                            ],
                            value="all",
                            clearable=False,
                            style={"fontSize": "0.85rem"},
                        ),
                    ]),
                    dbc.Col(width=3, children=[
                        dcc.Dropdown(
                            id="log_lines_count",
                            options=[
                                {"label": "50行", "value": 50},
                                {"label": "100行", "value": 100},
                                {"label": "200行", "value": 200},
                                {"label": "全行", "value": 0},
                            ],
                            value=50,
                            clearable=False,
                            style={"fontSize": "0.85rem"},
                        ),
                    ]),
                ]),
                # ログ出力（html.Div に変更: styled html.Span をchildren に受ける）
                html.Div(
                    id="analysis_log",
                    className="progress-log",
                    style={
                        "maxHeight": "400px",
                        "overflowY": "auto",
                        "fontFamily": "monospace",
                        "fontSize": "0.8rem",
                        "backgroundColor": "#1e1e1e",
                        "color": "#d4d4d4",
                        "padding": "10px",
                        "borderRadius": "4px",
                    },
                    children="",
                ),
            ],
        ),
    ])
