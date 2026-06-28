# =============================================================================
# MSI Analysis Application - Inline input validation callbacks (Inc.1)
# 数値入力が有効範囲外なら入力欄を赤く（invalid）する。dbc.Input の invalid
# プロパティに出力するだけなのでレイアウト変更は不要。範囲は utils.validation。
# =============================================================================

import logging

from dash import Input, Output, callback

from app.utils.validation import validate_param

logger = logging.getLogger("msi.interactive.validation")

# 検証対象の入力 id（= PARAM_BOUNDS のキー）
_VALIDATED_INPUTS = [
    "volcano_fc_threshold",
    "volcano_p_threshold",
    "volcano_y_max",
    "heatmap_top_n",
    "onthefly_de_fc",
    "onthefly_de_p",
    "feature_intensity_min",
    "feature_intensity_max",
]


def _register(input_id):
    @callback(
        Output(input_id, "invalid"),
        Input(input_id, "value"),
        prevent_initial_call=False,
    )
    def _validate(value, _pid=input_id):
        ok, _msg = validate_param(_pid, value)
        return not ok


for _iid in _VALIDATED_INPUTS:
    _register(_iid)
