"""対話キャリブレーションが「実際に当てた式」を報告すること (ver51.9)。

■ 何が起きていたか

ver51.8 で過学習を防ぐため次数を点数で抑えた:

    degree = max(1, min(degree, len(obs_arr) // 2 - 1))

`analysis_runner` 側は同時に `requested_degree` を返して
「指定 3 次を点数に合わせて下げた」と正直に出すようにしたが、
**対話側 (`interactive_calibration`) は表示を直していない**。

結果、利用者が poly3 を選ぶと

    キャリブレーション適用完了 (R²=1.0000, poly3)

と出る。実際に当てているのは **1 次**、しかも点数が足りず R² は
**構造的に 1.0**（残差が定義上ゼロ）。「3 次で完璧に当たった」と読める表示で、
実態は「直線 1 本を 2 点に通しただけ」。利用者が気づく手段は無い。

★ ここで固定するのは 2 点:
   - 戻り値が **実効次数**と**指定次数**を持つこと
   - 自由度が無いとき R² を数値で出さないこと
"""

import numpy as np
import pytest

from app.callbacks.interactive_calibration import (
    _calibrate_mz_from_pairs,
)


def _pairs(n, drift_ppm=3.0, jitter=0.0):
    """一定ドリフト + 微小ジッタの ref/obs ペア。"""
    refs = np.linspace(150.0, 380.0, n)
    out = []
    for i, r in enumerate(refs):
        sign = 1.0 if i % 2 == 0 else -1.0
        obs = r * (1 + drift_ppm / 1e6) + sign * jitter
        out.append({"ref_mz": float(r), "obs_mz": float(obs),
                    "ppm_drift": float((obs - r) / r * 1e6)})
    return out


def _features(n=8):
    return [f"m/z {v:.5f}" for v in np.linspace(150.0, 1000.0, n)]


class TestEffectiveDegreeIsReported:
    """★ 「当てた次数」を返すこと。"""

    def test_result_carries_the_effective_degree(self):
        res = _calibrate_mz_from_pairs(_features(), _pairs(4),
                                       regression_mode="poly3")
        assert res.get("calibrated")
        assert res.get("degree") == 1, (
            "実効次数を返していない。4 点では 3 次を当てられないのに "
            "poly3 と表示され続ける")

    def test_result_carries_the_requested_degree(self):
        res = _calibrate_mz_from_pairs(_features(), _pairs(4),
                                       regression_mode="poly3")
        assert res.get("requested_degree") == 3, \
            "指定次数が無いと『下げた』ことを表示できない"

    def test_no_downgrade_when_points_suffice(self):
        """★ 過剰修正の番人: 点数が足りていれば下げない。"""
        res = _calibrate_mz_from_pairs(_features(), _pairs(8, jitter=5e-4),
                                       regression_mode="poly3")
        assert res.get("degree") == 3
        assert res.get("requested_degree") == 3

    def test_linear_mode_is_consistent(self):
        """linear 経路も同じキーを持つこと（表示側が分岐しないで済む）。"""
        res = _calibrate_mz_from_pairs(_features(), _pairs(4),
                                       regression_mode="linear")
        assert res.get("degree") == 1
        assert res.get("requested_degree") == 1


class TestRSquaredIsHonest:
    """★ 自由度ゼロの R² を「当てはまり完璧」として出さない。

    `analysis_runner.compute_calibration_coefficients` は ver51.8 で
    None を返すようにした。対話側だけ 1.0 を出し続けていた。
    """

    def test_zero_dof_reports_none(self):
        res = _calibrate_mz_from_pairs(_features(), _pairs(2),
                                       regression_mode="poly3")
        assert res.get("calibrated")
        assert res.get("r_squared") is None, (
            f"自由度ゼロなのに R²={res.get('r_squared')} を出している。"
            "残差は定義上ゼロなので必ず 1.0 になる")

    def test_enough_dof_still_reports_a_number(self):
        """過剰修正の番人: 評価できるときは数値を出す。"""
        res = _calibrate_mz_from_pairs(_features(), _pairs(8, jitter=5e-4),
                                       regression_mode="linear")
        assert isinstance(res.get("r_squared"), float)


class TestWindowMatchingPathToo:
    """自動マッチ経路 (`_calibrate_mz`) にも同じ情報が載ること。

    表示側は経路を区別しないので、片方だけ直すと「たまに出ない」になる。
    """

    def test_auto_match_path_reports_degrees(self):
        from app.callbacks.interactive_calibration import _calibrate_mz

        refs = [150.0, 200.0, 300.0, 380.0]
        feats, spectrum = [], {}
        for i, r in enumerate(refs):
            obs = r * (1 + 3.0 / 1e6)
            name = f"m/z {obs:.5f}"
            feats.append(name)
            spectrum[name] = 100.0 + i

        res = _calibrate_mz(feats, spectrum, refs,
                            search_window=0.5, min_peaks=2,
                            regression_mode="poly3")
        assert res.get("calibrated")
        assert res.get("degree") == 1
        assert res.get("requested_degree") == 3


class TestStatusTextDoesNotLie:
    """★ 画面文言が実効次数を出すこと（ここが利用者の見る唯一の場所）。

    文言そのものではなく「指定と実効が食い違うとき、その事実が書かれるか」を見る。
    """

    @staticmethod
    def _render(res):
        """`_apply_int_calibration_inner` の状態文言生成と同じ形で組む。"""
        from app.callbacks.interactive_calibration import (
            format_calibration_status)
        return format_calibration_status(res)

    def test_downgrade_is_visible(self):
        res = _calibrate_mz_from_pairs(_features(), _pairs(4),
                                       regression_mode="poly3")
        text = self._render(res)
        assert "poly3" not in text or "1" in text, text
        assert "下げ" in text or "→" in text, \
            f"次数を下げたことが文言に出ていない: {text!r}"

    def test_unevaluable_r2_is_worded_not_numbered(self):
        res = _calibrate_mz_from_pairs(_features(), _pairs(2),
                                       regression_mode="poly3")
        text = self._render(res)
        assert "1.0000" not in text, f"評価不能なのに数値が出ている: {text!r}"
        assert "評価不能" in text, text

    def test_normal_case_is_unchanged(self):
        """過剰修正の番人: 何も問題が無いときは余計な注記を出さない。"""
        res = _calibrate_mz_from_pairs(_features(), _pairs(8, jitter=5e-4),
                                       regression_mode="poly3")
        text = self._render(res)
        assert "下げ" not in text, text
        assert "評価不能" not in text, text
        assert "poly3" in text, text
