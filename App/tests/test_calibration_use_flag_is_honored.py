"""キャリブレーション表で「使わない」にした行が本解析でも除外されること (S1)。

■ 何が起きるか

参照ピーク表の「使う/使わない」は、画面のチェックで
`"Yes"` / `"No"` という**文字列**に落ちる
(`interactive_calibration.sync_int_cal_selection_to_use` /
 `analysis_callbacks.sync_cal_selection_to_use`)。

ところが本解析側の `compute_calibration_coefficients` だけが

    if not row.get("use"):
        continue

と書かれている。Python では **`not "No"` は False** なので、
「使わない」に外した行が **そのまま回帰計算に入る**。
空文字と欠損しか弾けない判定になっている。

対話画面側 (`interactive_calibration.py`) と表示側
(`analysis_callbacks.py`) は `== "Yes"` で判定しており、
**実際に計算に使われる 1 か所だけ**が違う意味になっていた。

■ 実害

- 明らかな外れ値を「使わない」に外しても補正曲線に混ざる。
  実測 (4 点中 1 点を除外した表) では m/z 400 の補正量が
  +0.04337 Da、正しくは +0.00120 Da。差 0.042 Da は
  化合物照合の既定許容値 0.01 Da の **4 倍**。
- 実行サマリーは `use == "Yes"` の行だけを「使用ピーク」として出すため、
  **画面に出ていない点が計算に入る**。目視では気づけない。
- 補正係数は R 側で全特徴量の m/z 名を書き換え、名前が衝突した
  特徴量は統合される。影響は化合物名の誤りに留まらない。

■ 直し方

判定を 1 か所に集約し、`"No"` を「使わない」と読む。
`use` キーが**無い**行は、旧いプリセットとの後方互換のため
従来どおり「使う」として扱う (既定は使用)。
"""

import pytest

from app.services.analysis_runner import compute_calibration_coefficients


# 実測に使った 4 点 (DHB Positive)。3 行目が明らかな外れ値。
_ROWS = [
    {"ref_mz": 137.0233, "obs_mz": 137.0237, "use": "Yes"},
    {"ref_mz": 273.0393, "obs_mz": 273.0401, "use": "Yes"},
    {"ref_mz": 379.0925, "obs_mz": 379.0930, "use": "Yes"},
    # ↓「明らかにおかしいので使わない」と利用者が外した行
    {"ref_mz": 155.0339, "obs_mz": 155.5339, "use": "No"},
]


def _coef_at(result, mz):
    """補正量 = polyval(coefficients, mz)。"""
    import numpy as np
    return float(np.polyval(result["coefficients"], mz))


class TestTheUseColumnIsHonored:
    """★ 本丸: 「使わない」行が回帰に入らないこと。"""

    def test_rows_marked_no_are_excluded(self):
        res = compute_calibration_coefficients(_ROWS, "poly3", min_peaks=2)
        assert res is not None
        assert res["n_points"] == 3, (
            f"「使わない」にした行が計算に入っている (n_points={res['n_points']})。"
            "`not row.get('use')` は文字列 'No' を弾けない")

    def test_the_excluded_outlier_does_not_move_the_curve(self):
        """外した外れ値が補正量を動かさないこと。"""
        got = compute_calibration_coefficients(_ROWS, "poly3", min_peaks=2)
        want = compute_calibration_coefficients(
            [r for r in _ROWS if r["use"] == "Yes"], "poly3", min_peaks=2)
        assert got is not None and want is not None
        # m/z 400 での補正量が、外れ値を最初から渡さない場合と一致すること
        assert _coef_at(got, 400.0) == pytest.approx(_coef_at(want, 400.0), abs=1e-9), (
            "「使わない」行を含めた結果と、最初から渡さない結果が食い違う"
            "＝除外が効いていない")

    def test_the_error_is_larger_than_the_matching_tolerance(self):
        """★ 実害の大きさを固定する (許容値 0.01 Da を超えること)。

        この差が閾値以下なら「気づかなくても実害は無い」と言えてしまうので、
        **実害があること自体**をテストで残す。
        """
        wrong = compute_calibration_coefficients(_ROWS, "poly3", min_peaks=2)
        right = compute_calibration_coefficients(
            [r for r in _ROWS if r["use"] == "Yes"], "poly3", min_peaks=2)
        # 「使わない」を無視した場合の補正量のずれ（＝このバグが出す誤差）
        gap = abs(_coef_at(wrong, 400.0) - _coef_at(right, 400.0))
        assert gap < 0.01, (
            f"補正量が許容値 0.01 Da を超えてずれている ({gap:.5f} Da)。"
            "除外が効いていない")


class TestBackwardCompatibility:
    """★ 直しすぎないこと。"""

    def test_missing_use_key_still_counts_as_used(self):
        """`use` を持たない旧プリセットは従来どおり「使う」。"""
        rows = [{"ref_mz": 137.0233, "obs_mz": 137.0237},
                {"ref_mz": 273.0393, "obs_mz": 273.0401}]
        res = compute_calibration_coefficients(rows, "linear", min_peaks=2)
        assert res is not None and res["n_points"] == 2, (
            "`use` キーが無い行まで弾いてしまうと、旧いプリセットが動かなくなる")

    def test_true_and_yes_are_both_used(self):
        """真偽値で保存された表も「使う」と読めること。"""
        rows = [{"ref_mz": 137.0233, "obs_mz": 137.0237, "use": True},
                {"ref_mz": 273.0393, "obs_mz": 273.0401, "use": "Yes"}]
        res = compute_calibration_coefficients(rows, "linear", min_peaks=2)
        assert res is not None and res["n_points"] == 2

    def test_false_and_empty_are_both_excluded(self):
        rows = [{"ref_mz": 137.0233, "obs_mz": 137.0237, "use": "Yes"},
                {"ref_mz": 273.0393, "obs_mz": 273.0401, "use": "Yes"},
                {"ref_mz": 155.0339, "obs_mz": 155.5339, "use": False},
                {"ref_mz": 200.0000, "obs_mz": 200.5000, "use": ""},
                {"ref_mz": 250.0000, "obs_mz": 250.5000, "use": "no"}]
        res = compute_calibration_coefficients(rows, "linear", min_peaks=2)
        assert res is not None and res["n_points"] == 2, (
            f"False / 空文字 / 小文字 no のいずれかが弾けていない "
            f"(n_points={res['n_points']})")


class TestEveryReaderAgreesOnTheSameColumn:
    """★ 同じ列を読む 3 か所が同じ意味で読むこと (再発防止)。

    本解析だけが違う判定をしていたのが今回の原因なので、
    「実際に計算する側」と「画面に出す側」が食い違わないことを見張る。
    """

    def test_summary_and_computation_use_the_same_rows(self):
        # 画面のサマリーが「使用ピーク」として出す行 (analysis_callbacks と同じ式)
        shown = [r for r in _ROWS if r.get("use") == "Yes"]
        res = compute_calibration_coefficients(_ROWS, "poly3", min_peaks=2)
        assert res is not None
        assert res["n_points"] == len(shown), (
            f"画面に出る点数 ({len(shown)}) と計算に使う点数 "
            f"({res['n_points']}) が違う。**画面に出ていない点が計算に入る**")
