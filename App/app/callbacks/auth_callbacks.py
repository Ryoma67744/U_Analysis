"""認証 UI コールバック (ヘッダー表示 / パスワード変更モーダル)。

サーバーサイド callback:
- url_bar pathname の変化を契機に Flask session から現在の解析者情報を読む
- パスワード変更モーダルの開閉

clientside callback:
- current_analyst Store を各ヘッダー span に反映
- 「保存」クリック時に /api/admin/change-password を fetch
"""
from __future__ import annotations

import logging

from dash import (
    Input, Output, State, callback, ctx, no_update,
    ClientsideFunction, clientside_callback,
)
from flask import session

logger = logging.getLogger("msi.auth_callbacks")


@callback(
    Output("current_analyst", "data"),
    Input("url_bar", "pathname"),
)
def populate_current_analyst(_pathname):
    """全 URL 遷移で Flask session から解析者情報を取得して Store に書き込む。"""
    try:
        return {
            "name": session.get("analyst_name", ""),
            "tier": session.get("access_tier", ""),
        }
    except RuntimeError:
        return {"name": "", "tier": ""}


# ---- clientside: current_analyst -> 各ヘッダー span に反映 ----

# ★ ver56.4: 3 つ目の出力先 `header_analyst_label_shared` を削除した。
#   この id は ver52.3 で共有ページごとレイアウトから消えていたが、
#   この callback だけが取り残されていた。Dash は
#   suppress_callback_exceptions=True のため起動時には落ちず、
#   **実行時にこの callback 全体が停止**していたため、実在する
#   landing / analysis の 2 つにも解析者名が入らず、
#   ログインしてもヘッダーが常に空欄だった (実ブラウザで確認済)。
#   JS の返り値も 2 要素へ揃える (個数がずれると同じ症状に戻る)。
clientside_callback(
    """
    function(data) {
        if (!data || !data.name) {
            return ["", ""];
        }
        const tier = data.tier || "?";
        const label = "解析者: " + data.name + " (" + tier + ")";
        return [label, label];
    }
    """,
    Output("header_analyst_label_landing", "children"),
    Output("header_analyst_label_analysis", "children"),
    Input("current_analyst", "data"),
)


# ---- server-side: パスワード変更モーダル開閉 ----
# clientside_callback だと callback_context の挙動が不安定なので
# server-side @callback で確実に triggered を判定する。

@callback(
    Output("change_password_modal", "is_open"),
    Input("open_change_password_btn", "n_clicks"),
    Input("cp_cancel_btn", "n_clicks"),
    State("change_password_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_change_password_modal(open_clicks, cancel_clicks, is_open):
    triggered = ctx.triggered_id
    if triggered == "open_change_password_btn":
        return True
    if triggered == "cp_cancel_btn":
        return False
    return no_update


# ---- clientside: 「保存」クリックで /api/admin/change-password を fetch ----

# ver4.0: cp_new_a (Password A) を削除。Master + 共有 (B) の 2 本のみ。
clientside_callback(
    ClientsideFunction(
        namespace="auth",
        function_name="submitChangePassword",
    ),
    Output("cp_status", "children"),
    Output("cp_master", "value"),
    Output("cp_new_master", "value"),
    Output("cp_new_b", "value"),
    Input("cp_submit_btn", "n_clicks"),
    State("cp_master", "value"),
    State("cp_new_master", "value"),
    State("cp_new_b", "value"),
    prevent_initial_call=True,
)
