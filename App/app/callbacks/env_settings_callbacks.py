# =============================================================================
# MSI Analysis Application - Environment Settings Callbacks
# .env 編集モーダルの開閉・保存ロジック。
# =============================================================================

import logging

from dash import callback, Input, Output, State, no_update, html
import dash_bootstrap_components as dbc

from app.services.env_file_manager import (
    EDITABLE_KEYS, env_file_path, read_env_values,
    write_env_values, get_effective_values,
)

logger = logging.getLogger("msi.env_settings_callbacks")


# モーダル内の dbc.Input ID をキーと同順で固定 (UI と一致させる)
_INPUT_IDS = {
    "TIMS_DATA_DIR": "env_tims_data_dir",
    "DESI_DATA_DIR": "env_desi_data_dir",
    "R_HOME": "env_r_home",
    "SHARE_BASE_URL": "env_share_base_url",
    "APP_PORT": "env_app_port",
    "APP_HOST": "env_app_host",
}
# EDITABLE_KEYS と _INPUT_IDS の整合性を起動時に検証
assert set(_INPUT_IDS) == set(EDITABLE_KEYS)


# ---------------------------------------------------------------------------
# モーダル開閉 + 現在値の流し込み
# ---------------------------------------------------------------------------

@callback(
    Output("env_settings_modal", "is_open"),
    Output("env_settings_result", "children", allow_duplicate=True),
    *[Output(_INPUT_IDS[k], "value") for k in EDITABLE_KEYS],
    Input("open_env_settings_modal", "n_clicks"),
    Input("env_settings_cancel_btn", "n_clicks"),
    State("env_settings_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_env_settings_modal(open_clicks, cancel_clicks, is_open):
    n_inputs = len(EDITABLE_KEYS)
    if not (open_clicks or cancel_clicks):
        return (no_update, no_update) + (no_update,) * n_inputs

    if not is_open:
        values = read_env_values()
        return (True, "") + tuple(values[k] for k in EDITABLE_KEYS)
    return (False, no_update) + (no_update,) * n_inputs


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------

@callback(
    Output("env_settings_result", "children"),
    Input("env_settings_save_btn", "n_clicks"),
    *[State(_INPUT_IDS[k], "value") for k in EDITABLE_KEYS],
    prevent_initial_call=True,
)
def save_env_settings(n_clicks, *values):
    if not n_clicks:
        return no_update

    updates = {k: v for k, v in zip(EDITABLE_KEYS, values)}
    try:
        path = write_env_values(updates)
    except Exception as exc:  # noqa: BLE001 — UI にエラー表示して継続
        logger.exception(".env 書き込みに失敗")
        return dbc.Alert(f"保存エラー: {exc}", color="danger")

    # 起動時点の値と比較し、再起動が必要なキーを列挙
    effective = get_effective_values()
    changed = [
        k for k, v in updates.items()
        if (v or "").strip() and (v or "").strip() != effective.get(k, "")
    ]

    children = [
        dbc.Alert(f"✅ 保存しました: {path}", color="success", className="mb-2 py-2"),
    ]
    if changed:
        children.append(html.Div([
            html.Strong("再起動が必要な変更:"),
            html.Ul([html.Li(html.Code(k)) for k in changed]),
            html.Small("アプリを再起動すると反映されます。",
                       className="text-muted"),
        ]))
    else:
        children.append(html.Small(
            "起動時点の値と同一のため、再起動なしでも影響はありません。",
            className="text-muted",
        ))
    return html.Div(children)
