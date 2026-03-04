# =============================================================================
# MSI Analysis Application - Preset Callbacks
# プリセット管理コールバック
# =============================================================================

from dash import callback, Input, Output, State, no_update, ctx
from app.services.preset_manager import (
    list_presets, save_preset, load_preset, delete_preset, PRESET_KEYS,
)


# ---------------------------------------------------------------------------
# モーダル開閉 + ドロップダウン更新
# ---------------------------------------------------------------------------

@callback(
    Output("preset_modal", "is_open"),
    Output("preset_select", "options"),
    Output("preset_select", "value"),
    Output("preset_status", "children"),
    Input("open_preset_modal", "n_clicks"),
    State("preset_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_preset_modal(n_clicks, is_open):
    if not n_clicks:
        return no_update, no_update, no_update, no_update
    new_open = not is_open
    if new_open:
        options = [
            {"label": f"{p['name']}  ({p['created_at']})", "value": p["name"]}
            for p in list_presets()
        ]
        return True, options, None, ""
    return False, no_update, no_update, ""


# ---------------------------------------------------------------------------
# プリセット保存
# ---------------------------------------------------------------------------

@callback(
    Output("preset_select", "options", allow_duplicate=True),
    Output("preset_status", "children", allow_duplicate=True),
    Input("preset_save_btn", "n_clicks"),
    State("preset_name_input", "value"),
    # 保存対象パラメータ（PRESET_KEYS と同順）
    State("analysis_method", "value"),
    State("analysis_method_tims", "value"),
    State("ion_mode", "value"),
    State("tolerance_mz", "value"),
    State("adduct_filter", "value"),
    State("p_thresh", "value"),
    State("logfc_thresh", "value"),
    State("calibration_enable", "value"),
    State("calibration_matrix", "value"),
    State("calibration_search_window", "value"),
    State("calibration_min_peaks", "value"),
    State("calibration_regression_mode", "value"),
    State("filter_mode", "value"),
    State("target_clusters", "value"),
    State("reanalysis_ion_mode", "value"),
    State("reanalysis_tolerance_mz", "value"),
    State("reanalysis_adduct_filter", "value"),
    State("reanalysis_p_thresh", "value"),
    State("reanalysis_logfc_thresh", "value"),
    prevent_initial_call=True,
)
def save_preset_cb(n_clicks, name, *param_values):
    if not n_clicks or not name or not name.strip():
        return no_update, "プリセット名を入力してください"

    params = dict(zip(PRESET_KEYS, param_values))
    save_preset(name.strip(), params)

    options = [
        {"label": f"{p['name']}  ({p['created_at']})", "value": p["name"]}
        for p in list_presets()
    ]
    return options, f"✅ 「{name.strip()}」を保存しました"


# ---------------------------------------------------------------------------
# プリセット読込
# ---------------------------------------------------------------------------

@callback(
    Output("preset_status", "children", allow_duplicate=True),
    Output("analysis_method", "value", allow_duplicate=True),
    Output("analysis_method_tims", "value", allow_duplicate=True),
    Output("ion_mode", "value", allow_duplicate=True),
    Output("tolerance_mz", "value", allow_duplicate=True),
    Output("adduct_filter", "value", allow_duplicate=True),
    Output("p_thresh", "value", allow_duplicate=True),
    Output("logfc_thresh", "value", allow_duplicate=True),
    Output("calibration_enable", "value", allow_duplicate=True),
    Output("calibration_matrix", "value", allow_duplicate=True),
    Output("calibration_search_window", "value", allow_duplicate=True),
    Output("calibration_min_peaks", "value", allow_duplicate=True),
    Output("calibration_regression_mode", "value", allow_duplicate=True),
    Output("filter_mode", "value", allow_duplicate=True),
    Output("target_clusters", "value", allow_duplicate=True),
    Output("reanalysis_ion_mode", "value", allow_duplicate=True),
    Output("reanalysis_tolerance_mz", "value", allow_duplicate=True),
    Output("reanalysis_adduct_filter", "value", allow_duplicate=True),
    Output("reanalysis_p_thresh", "value", allow_duplicate=True),
    Output("reanalysis_logfc_thresh", "value", allow_duplicate=True),
    Input("preset_load_btn", "n_clicks"),
    State("preset_select", "value"),
    prevent_initial_call=True,
)
def load_preset_cb(n_clicks, selected):
    if not n_clicks or not selected:
        return (no_update,) * (1 + len(PRESET_KEYS))

    params = load_preset(selected)
    if params is None:
        return ("プリセットが見つかりません",) + (no_update,) * len(PRESET_KEYS)

    values = [params.get(k, no_update) for k in PRESET_KEYS]
    return (f"✅ 「{selected}」を読み込みました", *values)


# ---------------------------------------------------------------------------
# プリセット削除
# ---------------------------------------------------------------------------

@callback(
    Output("preset_select", "options", allow_duplicate=True),
    Output("preset_select", "value", allow_duplicate=True),
    Output("preset_status", "children", allow_duplicate=True),
    Input("preset_delete_btn", "n_clicks"),
    State("preset_select", "value"),
    prevent_initial_call=True,
)
def delete_preset_cb(n_clicks, selected):
    if not n_clicks or not selected:
        return no_update, no_update, "削除するプリセットを選択してください"

    ok = delete_preset(selected)
    if not ok:
        return no_update, no_update, "プリセットが見つかりません"

    options = [
        {"label": f"{p['name']}  ({p['created_at']})", "value": p["name"]}
        for p in list_presets()
    ]
    return options, None, f"🗑 「{selected}」を削除しました"
