"""受領書に載る on-the-fly DE の条件が、実際に走る条件と一致すること。

`provenance.ONTHEFLY_DE_FIXED_PARAMS` は「GUI に出ていない固定条件」を
受領書と Methods に載せるための表で、コメントにも

    seurat_bridge.SeuratBridge.run_differential_expression のシグネチャ既定値と
    一致させること（interactive_de.py は上書きしていない）

と書いてある。ところが ver58.0 (A-3) で検定前の足切りを外したとき、
`min_pct` は 0.0 に直したのに **`logfc_threshold` が 0.25 のまま残った**。

実際に走るのは `logfc=0.0`（＝倍率で足切りしない）なので、
受領書だけが「|log2FC| ≥ 0.25 で絞ってから検定した」と主張する。
多重比較補正の分母が変わる話なので、この食い違いは結果の解釈を直接誤らせる。

この検査は 2 つの定数を**突き合わせる**ので、片方だけ動かすと落ちる。
"""

import inspect

from app.services.provenance import ONTHEFLY_DE_FIXED_PARAMS
from app.services.seurat_bridge import SeuratBridge


def _defaults():
    sig = inspect.signature(SeuratBridge.run_differential_expression)
    return {k: v.default for k, v in sig.parameters.items()}


def test_min_pct_matches_the_signature():
    assert ONTHEFLY_DE_FIXED_PARAMS["min_pct"] == _defaults()["min_pct"], (
        "受領書の min_pct が実処理と違う")


def test_logfc_threshold_matches_the_signature():
    """★ 本丸: 記録された log2FC 閾値が実処理と一致すること。"""
    assert ONTHEFLY_DE_FIXED_PARAMS["logfc_threshold"] == _defaults()["logfc"], (
        f"受領書は logfc_threshold={ONTHEFLY_DE_FIXED_PARAMS['logfc_threshold']} と"
        f"主張するが、実際は logfc={_defaults()['logfc']} で走る。"
        "検定対象の広さ＝多重比較補正の分母が変わるので、解釈を直接誤らせる")


def test_the_test_and_adjust_method_are_recorded():
    """前提の固定: 検定法と補正法の記録が消えていないこと。"""
    assert ONTHEFLY_DE_FIXED_PARAMS["test"] == "wilcox"
    assert ONTHEFLY_DE_FIXED_PARAMS["p_adjust_method"] == "BH"
