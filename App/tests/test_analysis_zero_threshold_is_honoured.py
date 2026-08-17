"""解析実行時、閾値に 0 を入れたら 0 が使われることの番人。

★ ver56.5 / デバッグ総点検 §4.2 (C03-1):

  「log2FC 閾値」に **0**（= 倍率で絞り込まない、という正当な指定。画面の
  `min=0` も 0 を許している）を入れて「解析実行」を押しても、実際の計算には
  0.25 が使われていた。`float(x) if x else 既定` という書き方は **0 が falsy**
  なため既定値へすり替わる。画面の入力欄は 0 のままなので、利用者は自分の指定
  どおりに解析されたと信じてしまう。

  再解析側 (`analysis_callbacks.py:754-756`) は `is not None` で 0 を正しく
  通しており、**同じ入力欄が経路によって別の解釈になっていた**。

  既定値は `PARAM_BOUNDS` を単一出典とする `coerce_number()` に委ねる
  (ver52.3 ⑤ が「未入力」と「0」を区別するために作った仕組み)。
"""
import ast
import inspect

import pytest

from app.utils.validation import coerce_number, param_default


class TestCoerceNumberSemantics:
    """土台となる `coerce_number` が 0 と未入力を区別すること。"""

    @pytest.mark.parametrize("param_id", ["p_thresh", "logfc_thresh", "tolerance_mz"])
    def test_zero_is_kept(self, param_id):
        assert coerce_number(0, param_id) == 0
        assert coerce_number("0", param_id) == 0

    @pytest.mark.parametrize("param_id", ["p_thresh", "logfc_thresh", "tolerance_mz"])
    def test_blank_falls_back_to_declared_default(self, param_id):
        assert coerce_number(None, param_id) == param_default(param_id)
        assert coerce_number("", param_id) == param_default(param_id)


class TestRunAnalysisDoesNotSwallowZero:
    """★ 本丸: 解析実行の params 構築で 0 が既定値に化けないこと。"""

    def test_no_falsy_default_pattern_remains(self):
        """`float(x) if x else 定数` の形が閾値まわりに残っていないこと。

        この形が 1 つでも残ると、その入力だけ 0 が既定値に化ける。
        """
        import app.callbacks.analysis_callbacks as ac
        tree = ast.parse(inspect.getsource(ac))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.IfExp):
                continue
            # `float(X) if X else <定数>` を検出
            if not (isinstance(node.orelse, ast.Constant)
                    and isinstance(node.orelse.value, (int, float))):
                continue
            if isinstance(node.test, ast.Name) and isinstance(node.body, ast.Call):
                fn = getattr(node.body.func, "id", None)
                if fn in ("float", "int"):
                    offenders.append((node.lineno, node.test.id))
        assert not offenders, (
            "`float(x) if x else 既定` が残っている。0 が既定値に化ける:\n  "
            + "\n  ".join(f"analysis_callbacks.py:{ln}  {name}"
                          for ln, name in offenders))

    def test_thresholds_use_the_single_source_of_defaults(self):
        """閾値が `coerce_number` 経由で組まれていること。"""
        import app.callbacks.analysis_callbacks as ac
        src = inspect.getsource(ac)
        for expr in ('coerce_number(p_thresh, "p_thresh")',
                     'coerce_number(logfc_thresh, "logfc_thresh")',
                     'coerce_number(tolerance_mz, "tolerance_mz")'):
            assert expr in src, f"{expr} が使われていない"
