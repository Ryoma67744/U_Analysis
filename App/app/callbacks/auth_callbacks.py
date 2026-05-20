"""認証 UI コールバック (ヘッダー表示 / パスワード変更モーダル)。

サーバーサイド callback:
- url_bar pathname の変化を契機に Flask session から現在の解析者情報を読む

clientside callback:
- current_analyst Store を各ヘッダー span に反映
- パスワード変更モーダルの開閉
- 「保存」クリック時に /api/admin/change-password を fetch
"""
from __future__ import annotations

import logging

from dash import (
    Input, Output, State, callback, no_update,
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

clientside_callback(
    """
    function(data) {
        if (!data || !data.name) {
            return ["", "", ""];
        }
        const tier = data.tier || "?";
        const label = "解析者: " + data.name + " (" + tier + ")";
        return [label, label, label];
    }
    """,
    Output("header_analyst_label_landing", "children"),
    Output("header_analyst_label_analysis", "children"),
    Output("header_analyst_label_shared", "children"),
    Input("current_analyst", "data"),
)


# ---- clientside: パスワード変更モーダル開閉 ----

clientside_callback(
    """
    function(open_clicks, cancel_clicks, is_open) {
        const trigger = (window.dash_clientside.callback_context.triggered || [])[0];
        if (!trigger) return window.dash_clientside.no_update;
        if (trigger.prop_id.indexOf("open_change_password_btn") === 0) {
            return true;
        }
        if (trigger.prop_id.indexOf("cp_cancel_btn") === 0) {
            return false;
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("change_password_modal", "is_open"),
    Input("open_change_password_btn", "n_clicks"),
    Input("cp_cancel_btn", "n_clicks"),
    State("change_password_modal", "is_open"),
    prevent_initial_call=True,
)


# ---- clientside: 「保存」クリックで /api/admin/change-password を fetch ----

clientside_callback(
    ClientsideFunction(
        namespace="auth",
        function_name="submitChangePassword",
    ),
    Output("cp_status", "children"),
    Output("cp_master", "value"),
    Output("cp_new_a", "value"),
    Output("cp_new_b", "value"),
    Input("cp_submit_btn", "n_clicks"),
    State("cp_master", "value"),
    State("cp_new_a", "value"),
    State("cp_new_b", "value"),
    prevent_initial_call=True,
)
