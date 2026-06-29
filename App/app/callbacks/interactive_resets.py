# =============================================================================
# MSI Analysis Application - Reset buttons (Inc.1: “調整には必ずリセット”)
# 各調整コントロールを既定値へ戻す。値の出力は他の復元系 (preset/session) と
# 競合しうるため allow_duplicate=True で出力する。
# =============================================================================

import logging

from dash import Input, Output, callback
from dash.exceptions import PreventUpdate

logger = logging.getLogger("msi.interactive.resets")


@callback(
    [Output("feature_colorscale", "value", allow_duplicate=True),
     Output("feature_intensity_min", "value", allow_duplicate=True),
     Output("feature_intensity_max", "value", allow_duplicate=True)],
    Input("feature_colorscale_reset", "n_clicks"),
    prevent_initial_call=True,
)
def reset_feature_colorscale(n_clicks):
    if not n_clicks:
        raise PreventUpdate
    return "Plasma", None, None


@callback(
    [Output("volcano_fc_threshold", "value", allow_duplicate=True),
     Output("volcano_p_threshold", "value", allow_duplicate=True),
     Output("volcano_y_max", "value", allow_duplicate=True)],
    Input("volcano_reset", "n_clicks"),
    prevent_initial_call=True,
)
def reset_volcano(n_clicks):
    if not n_clicks:
        raise PreventUpdate
    return 0.5, 1.3, None


@callback(
    [Output("hne_overlay_opacity", "value", allow_duplicate=True),
     Output("hne_overlay_marker_size", "value", allow_duplicate=True)],
    Input("hne_overlay_reset", "n_clicks"),
    prevent_initial_call=True,
)
def reset_hne_overlay(n_clicks):
    if not n_clicks:
        raise PreventUpdate
    return 100, 5
