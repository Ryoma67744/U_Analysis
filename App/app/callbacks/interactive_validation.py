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
#
# ★ ver52.3 ⑤: 8 → 26 に増やした。番人 `test_numeric_input_bounds` が
#   「画面の数値入力 28 個のうち 20 個が無検証」と測った分を全部結線する。
#   仕組み（`PARAM_BOUNDS` + この一覧 + `_register`）は最初から在ったのに、
#   一部にしか適用されていなかった——ver52.1 の GPT API で
#   「`top` は直したが `limit` は直さなかった」と同じ形。
#
#   ここに載せる id は **すべて `dbc.Input`** であること（`invalid` プロパティを
#   持つのは dbc 側だけ。`dcc.Input` に出力すると実行時に落ちる）。
#   番人 `test_validated_inputs_are_dbc_inputs` が全数を確認する。
_VALIDATED_INPUTS = [
    # --- ver52.2 以前から結線済み ---
    "volcano_fc_threshold",
    "volcano_p_threshold",
    "volcano_y_max",
    "heatmap_top_n",
    "onthefly_de_fc",
    "onthefly_de_p",
    "feature_intensity_min",
    "feature_intensity_max",
    # --- ver52.3 ② でキー名を実 id に直した UMAP 条件（結線は本コミット）---
    "umap_n_neighbors_input",
    "umap_min_dist_input",
    "umap_dims_input",
    # --- ver52.3 ⑤ で新たに結線 ---
    "p_thresh",
    "logfc_thresh",
    "reanalysis_p_thresh",
    "reanalysis_logfc_thresh",
    "tolerance_mz",
    "reanalysis_tolerance_mz",
    "reann_tolerance",
    "mz_align_ppm",
    "calibration_search_window",
    "calibration_min_peaks",
    "int_cal_search_window",
    "int_cal_min_peaks",
    "volcano_label_top_n",
    "input_export_top_n",
    "scils_spot_block",
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
