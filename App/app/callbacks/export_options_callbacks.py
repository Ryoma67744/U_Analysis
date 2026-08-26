"""「⚙ 出力内容の設定」モーダルのコールバック（ver61.0）。

役割は 3 つだけに絞ってある。

1. モーダルの開閉
2. 選択内容を `data_export_options` Store へ書く
3. 選択が矛盾しているとき（平均なのにキー未選択、列が 1 つも無い）に警告を出す

実際の出力の中身は `services/export_options.py` が決める。ここは UI の配線のみ。
判定ロジックをこちらへ書くと、Dash 無しではテストできない場所に仕様が入り込む。
"""

import logging

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, html, no_update

from app.services.export_options import (
    LEGACY_CATEGORIES,
    MODE_GROUP,
    MODE_PIXEL,
    describe,
    wants_mzlist,
    wants_spot_table,
)

logger = logging.getLogger(__name__)

_SHOW = {"display": "block", "marginTop": "10px", "paddingLeft": "18px",
         "borderLeft": "3px solid #dee2e6"}
_HIDE = {"display": "none"}


@callback(
    Output("export_options_modal", "is_open"),
    [Input("btn_export_options", "n_clicks"),
     Input("export_opt_close", "n_clicks")],
    State("export_options_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_export_options_modal(open_click, close_click, is_open):
    """設定ボタンで開き、閉じるボタンで閉じる。"""
    return not is_open


@callback(
    Output("export_opt_group_wrapper", "style"),
    Input("export_opt_mode", "value"),
)
def toggle_group_key_section(mode):
    """「グループ平均」を選んだときだけキー選択を出す。

    1 ピクセル単位のときにキー選択が見えていると、設定したつもりで効かない。
    """
    return _SHOW if mode == MODE_GROUP else _HIDE


@callback(
    [Output("export_opt_categories", "value"),
     Output("export_opt_mode", "value"),
     Output("export_opt_group_keys", "value")],
    Input("export_opt_reset", "n_clicks"),
    prevent_initial_call=True,
)
def reset_to_default(_n):
    """既定（従来と同じ出力）へ戻す。"""
    return list(LEGACY_CATEGORIES), MODE_PIXEL, ["section", "cluster"]


@callback(
    [Output("data_export_options", "data"),
     Output("export_opt_warning", "children"),
     Output("div_data_export_options_summary", "children")],
    [Input("export_opt_categories", "value"),
     Input("export_opt_mode", "value"),
     Input("export_opt_group_keys", "value")],
)
def store_export_options(categories, mode, group_keys):
    """選択内容を Store に書き、要約と警告を更新する。

    矛盾した設定は **保存はする** が警告を出す。ここで黙って直すと、利用者は
    自分が選んだつもりの設定と違うものが出た理由を追えない。実際に出力しようと
    した時点で `_export_tims` が理由付きで止める。
    """
    opts = {"categories": list(categories or []),
            "mode": mode or MODE_PIXEL,
            "group_keys": list(group_keys or [])}

    warnings = []
    if not categories:
        warnings.append("出力する列が 1 つも選ばれていません。")
    if mode == MODE_GROUP and not group_keys:
        warnings.append("まとめるキーが 1 つも選ばれていません。")
    # ★ ver62.0: m/z 一覧を他の項目と併用すると csv/parquet では出せない。
    #   出力ボタンを押してから弾かれるより、選んだ時点で分かる方がよい。
    if wants_mzlist(opts) and wants_spot_table(opts):
        warnings.append(
            "「m/z 一覧」を他の項目と併用する場合、出力形式に Excel (.xlsx) が"
            "必要です（CSV / Parquet は 1 ファイルに 1 表しか持てません）。")
    if mode == MODE_GROUP and "roi" in (group_keys or []):
        warnings.append(
            "「領域名」をキーにすると、H&E で ROI を割り当てていないスポットは"
            "空欄グループにまとまります。")

    alert = (dbc.Alert([html.Div(w) for w in warnings], color="warning",
                       className="small mb-0 py-2")
             if warnings else None)
    return opts, alert, describe(opts)
