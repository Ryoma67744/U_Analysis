"""ver52.3 ⑤: 利用者が入れた 0 が既定値に化けないこと。

■ 何が起きていたか

    interactive_deg.py:1127   fc_thresh = fc_thresh or 0.5
    interactive_deg.py:1128   p_thresh  = p_thresh  or 1.3
    interactive_deg.py:1163   _top_n    = int(label_top_n or 5)

0 は falsy なので、**利用者が入れた 0 が黙って既定値になる**。
「FC 閾値 0 で全部見たい」が「閾値 0.5 で描画」に変わり、
0〜0.5 の feature が "Not significant" の灰色になる ——
**利用者がしていない科学的主張が図に出る**。しかも警告は無い。

■ ★ さらに悪い形: 画面と資料が 0 を逆に解釈していた

    画面 (interactive_deg)   int(label_top_n or 5)                → 0 が 5 になりラベルが出る
    資料 (interactive_pptx)  5 if v is None else max(0, int(v))   → 0 のままラベルが出ない

レイアウトは `min=0`（interactive_tab.py:1597）なので 0 は正当な入力で、
PPTX 側が正しかった。ver51.9 B-2 で PPTX だけを直した取りこぼし。

■ 直し方（式を 2 箇所に書かない）

「同じ式を画面側にも書く」は**採らなかった**。同じ判断が 2 箇所にあること
自体がこの食い違いを生んだ原因なので、`app.utils.validation.coerce_count` /
`coerce_number` に集約して両方から呼ぶ。既定値も `PARAM_BOUNDS` 由来にする
（`tolerance_mz` は 0.1 と 0.01 の 2 通りが実在した）。
"""

import ast
from pathlib import Path

import pytest

import app.utils.validation as V
from app.utils.validation import PARAM_BOUNDS, validate_param

# ★ `coerce_number` / `coerce_count` / `param_default` は ver52.3 ⑤ で足した関数。
#   `from ... import coerce_count` と書くと **修正前のコードでは収集ごと失敗**し、
#   「どのテストが振る舞いを固定しているか」を `git stash` で確認できなくなる
#   （ImportError は「直っていない」ではなく「まだ無い」としか言わない）。
#   モジュールとして持ち、属性参照はテストの中で行う。こうすると修正前でも
#   収集は通り、**各テストが個別に落ちる**ので何が守られているかが読める。
_coerce_number = lambda *a, **k: V.coerce_number(*a, **k)      # noqa: E731
_coerce_count = lambda *a, **k: V.coerce_count(*a, **k)        # noqa: E731
_param_default = lambda *a, **k: V.param_default(*a, **k)      # noqa: E731

APP = Path(__file__).resolve().parent.parent / "app"


class TestCoerceNumberKeepsZero:

    def test_zero_survives(self):
        """★ 本丸。`float(0 or 0.5)` は 0.5 になる。"""
        assert _coerce_number(0, "volcano_fc_threshold") == 0.0
        assert _coerce_number(0.0, "volcano_p_threshold") == 0.0
        assert _coerce_number(0, "mz_align_ppm") == 0.0

    def test_blank_falls_back_to_the_declared_default(self):
        assert _coerce_number(None, "volcano_fc_threshold") == 0.5
        assert _coerce_number("", "volcano_p_threshold") == 1.3

    def test_unparseable_falls_back_not_crashes(self):
        assert _coerce_number("abc", "volcano_fc_threshold") == 0.5

    def test_default_comes_from_param_bounds_not_a_literal(self):
        """★ 既定値の出典が 1 つであること。

        `PARAM_BOUNDS` を書き換えれば `coerce_number` の答えも変わる、
        という関係が保たれていれば、既定値が二重定義になっていない。
        """
        assert _coerce_number(None, "tolerance_mz") == PARAM_BOUNDS["tolerance_mz"][2]
        assert _param_default("tolerance_mz") == 0.01

    def test_unknown_id_uses_the_explicit_fallback(self):
        assert _coerce_number(None, "no_such_input", 7) == 7
        assert _coerce_number(None, "no_such_input") is None


class TestCoerceCountKeepsZero:

    def test_zero_means_zero(self):
        assert _coerce_count(0, "volcano_label_top_n") == 0

    def test_blank_uses_the_default(self):
        assert _coerce_count(None, "volcano_label_top_n") == 5

    def test_negative_is_clamped_not_defaulted(self):
        """負値は 0（＝出さない）にする。既定値へ戻すと利用者の意図が反転する。"""
        assert _coerce_count(-3, "volcano_label_top_n") == 0

    def test_float_is_truncated(self):
        assert _coerce_count(3.9, "volcano_label_top_n") == 3


class TestScreenAndReportAgreeOnZero:
    """★ 同じ入力に対し、画面と資料が同じ判断をすること。"""

    @pytest.mark.parametrize("value,expected", [
        (0, 0),        # ラベルを出さない（レイアウト min=0 の正当な指定）
        (None, 5),     # 未入力 → 既定
        (3, 3),
    ])
    def test_label_top_n_resolves_identically(self, value, expected):
        assert _coerce_count(value, "volcano_label_top_n") == expected

    def test_both_paths_call_the_same_helper(self):
        """式を書き写していないことを構造で固定する。

        文字列で `max(0, int(...))` を探す形にはしない——書き方を変えれば
        すり抜けるし、番人を形で近似したときの失敗そのものになる。
        `coerce_count` の呼び出しが両方に在ることを AST で見る。
        """
        def calls(rel):
            tree = ast.parse((APP / rel).read_text(encoding="utf-8"))
            out = set()
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id == "coerce_count" and n.args
                        and len(n.args) >= 2
                        and isinstance(n.args[1], ast.Constant)):
                    out.add(n.args[1].value)
            return out

        screen = calls("callbacks/interactive_deg.py")
        report = calls("callbacks/interactive_pptx.py")
        assert "volcano_label_top_n" in screen, (
            "画面側がラベル件数を共通ヘルパで解決していない。"
            "式を書き写すと、また画面と資料で 0 の解釈が割れる")
        assert "volcano_label_top_n" in report, "資料側も同上"


class TestPreflightAgreesWithParamBounds:
    """★ 罠 2: 検証機構が 2 つあり、食い違うと利用者に矛盾が見える。

    入力欄は `validate_param`（`PARAM_BOUNDS`）で赤くなり、
    実行直前は `preflight_validation` が別途チェックする。範囲がずれると
    「欄は赤いのに実行は通る」「欄は白いのに実行が弾かれる」——
    それ自体が T7（表示 ≠ 計算）。
    """

    @pytest.mark.parametrize("param_id,bad_value", [
        ("p_thresh", 1.5),          # 0..1 の外
        ("p_thresh", -0.1),
        ("logfc_thresh", -1),       # 0 以上
        ("tolerance_mz", -0.01),
    ])
    def test_out_of_range_is_rejected_by_param_bounds(self, param_id, bad_value):
        ok, msg = validate_param(param_id, bad_value)
        assert not ok and msg, f"{param_id}={bad_value} が通ってしまう"

    @pytest.mark.parametrize("param_id,good_value", [
        ("p_thresh", 0), ("p_thresh", 1), ("p_thresh", 0.05),
        ("logfc_thresh", 0), ("tolerance_mz", 0),
    ])
    def test_boundary_values_are_accepted(self, param_id, good_value):
        ok, msg = validate_param(param_id, good_value)
        assert ok, f"{param_id}={good_value} を誤って弾いている: {msg}"

    def test_preflight_reads_param_bounds_instead_of_its_own_literals(self):
        """`preflight_validation` が範囲リテラルを持っていないこと。

        以前は `(p_thresh, "p値閾値", 0, 1)` のような一覧を関数内に持っており、
        レイアウト・`PARAM_BOUNDS` と合わせて **3 つの独立した出典**があった。

        ver56.7: 検査の本体は `_collect_preflight_errors` へ移った
        (表示側と実行側で同じ検査を使うため)。見る先をそこへ移す。
        """
        src = (APP / "callbacks/analysis_callbacks.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_collect_preflight_errors")
        names = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "validate_param" in names, (
            "_collect_preflight_errors が `PARAM_BOUNDS` を参照していない。"
            "範囲の出典が再び 2 つに分かれている")
        assert "validate_numeric_param" not in names, (
            "範囲を関数内に持つ旧経路が残っている")


class TestCoercionAlwaysHasAVisibleSignal:
    """★ ver52.3 ⑤ で新たに作った分岐の言語化（副次調査 手順 5）。

    `coerce_number` は **数値化できない値も既定値へ倒す**。従来の
    `float(x or D)` は `float("abc")` で例外になり、外側の `except` が
    「キャリブレーションエラー」として利用者に見せていた。
    黙って既定値に倒すだけなら、これは T5（部分的失敗を成功として報告）を
    自分で作ることになる。

    倒してよいのは **入力欄が赤くなる**（`validate_param` に結線されている）
    場合だけ。この対応が崩れていないことをここで固定する。
    """

    def _coerce_call_sites(self):
        sites = []
        for path in sorted(APP.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id in ("coerce_number", "coerce_count")
                        and len(n.args) >= 2
                        and isinstance(n.args[1], ast.Constant)):
                    sites.append((f"{path.relative_to(APP.parent)}:{n.lineno}",
                                  n.args[1].value))
        return sites

    def test_the_scan_finds_the_call_sites(self):
        """★ 番人が空振りしていないこと。"""
        sites = self._coerce_call_sites()
        assert len(sites) >= 15, (
            f"`coerce_*` の呼び出しが {len(sites)} 件しか見つからない。走査が壊れている疑い")

    def test_every_coerced_input_is_wired_to_inline_validation(self):
        from app.callbacks.interactive_validation import _VALIDATED_INPUTS
        bad = sorted({(loc, pid) for loc, pid in self._coerce_call_sites()
                      if pid not in _VALIDATED_INPUTS})
        assert not bad, (
            "既定値へ倒しているのに、入力欄が赤くならない入力がある。\n"
            "不正な入力が**何の合図も無く**既定値で処理される（T5 そのもの）。\n"
            "`_VALIDATED_INPUTS` に結線するか、倒さず例外にすること:\n  "
            + "\n  ".join(f"{loc}  ← {pid}" for loc, pid in bad))

    def test_every_coerced_input_has_a_declared_default(self):
        """既定値が宣言されていないと `None` に倒れ、下流で TypeError になる。"""
        missing = sorted({pid for _loc, pid in self._coerce_call_sites()
                          if PARAM_BOUNDS.get(pid, (None,) * 3)[2] is None})
        assert not missing, (
            "`coerce_*` の倒し先（既定値）が `PARAM_BOUNDS` に宣言されていない。"
            "空欄のとき None が下流へ流れる:\n  " + "\n  ".join(missing))


class TestCalibrationTabsShareTheSameBounds:
    """★ 同じアルゴリズムなのにタブ間で境界が違っていた。

    設定タブと対話タブの探索窓・最低点数は
    `analysis_runner:299` / `interactive_calibration:318` の
    **同じ次数クランプ式**を通るのに、対話側だけ上限が無かった。
    """

    @pytest.mark.parametrize("a,b", [
        ("calibration_search_window", "int_cal_search_window"),
        ("calibration_min_peaks", "int_cal_min_peaks"),
    ])
    def test_pair_has_identical_range(self, a, b):
        lo_a, hi_a, _, _ = PARAM_BOUNDS[a]
        lo_b, hi_b, _, _ = PARAM_BOUNDS[b]
        assert (lo_a, hi_a) == (lo_b, hi_b), (
            f"{a} と {b} の範囲が違う: {(lo_a, hi_a)} vs {(lo_b, hi_b)}。"
            "同じクランプ式を通るので、片方だけ弾かれるのは説明が付かない")
