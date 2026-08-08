"""m/z キャリブレーションの数値的妥当性 (ver51.8)。

■ 何が起きていたか

`analysis_runner.compute_calibration_coefficients` に **自由度のクランプが無かった**。
対話側 (`interactive_calibration._calibrate_mz`) には
`degree = min(degree, len(obs_arr) - 1)  # 過学習防止` が元からあり、こちらだけ抜けていた。

既定は `DEFAULT_CALIBRATION_MIN_PEAKS = 2` / `DEFAULT_CALIBRATION_REGRESSION = "poly3"`。
同梱リファレンス (`config.MATRIX_REFERENCE_MZ`) は DHB Negative が 2 点、
DHB Positive が 4 点しかなく、いずれも m/z 136-379。補正対象の feature は
m/z 1000 超まであるので **全部外挿**になる。

さらに R² が `1.0 - ss_res/ss_tot` で、自由度が足りないと残差が定義上ゼロ →
**構造的に R² = 1.0**。利用者には「当てはまり完璧」と表示されながら、
真のドリフトが一定 +3 ppm でも m/z 1000 の補正が -20.8 Da になっていた。

係数はそのまま R テンプレートへ渡り、全 feature が `sprintf("m/z %.5f", new_mz)` で
改名される。つまり**全脂質のラベルが静かに書き換わる**。
"""

import warnings

import numpy as np
import pytest

from app.services.analysis_runner import compute_calibration_coefficients


def _table(refs, errors):
    return [{"use": True, "ref_mz": r, "obs_mz": r + e}
            for r, e in zip(refs, errors)]


class TestDegreeClamp:
    def test_two_points_do_not_get_a_cubic(self):
        """★ 2 点に 3 次を当てない (ランク落ちで係数が任意になる)。"""
        res = compute_calibration_coefficients(
            _table([136.0166, 153.0193], [0.0006, 0.0011]), "poly3", 2)
        assert res is not None
        assert res["degree"] == 1, res
        assert res["requested_degree"] == 3

    def test_no_rank_warning_is_emitted(self):
        """★ numpy の RankWarning が出ないこと（＝当てはめが定義可能）。"""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            compute_calibration_coefficients(
                _table([136.0166, 153.0193], [0.0006, 0.0011]), "poly3", 2)
        names = [x.category.__name__ for x in w]
        assert not any("Rank" in n for n in names), names

    @pytest.mark.parametrize("n,mode,expected", [
        # パラメータ数の 2 倍以上の点数を要求する: n >= 2*(degree+1)
        (2, "poly3", 1), (4, "poly3", 1), (6, "poly3", 2), (8, "poly3", 3),
        (12, "poly3", 3),
        (2, "poly2", 1), (4, "poly2", 1), (6, "poly2", 2), (10, "poly2", 2),
        (2, "linear", 1), (10, "linear", 1),
    ])
    def test_degree_requires_twice_as_many_points_as_parameters(
            self, n, mode, expected):
        """★ `n-1` クランプでは足りない。

        n-1 は「ちょうど完全内挿できる」次数で自由度ゼロ。誤差を一切吸収せず、
        参照範囲の外へ外挿した瞬間に発散する（4 点 + 3 次で m/z 1000 が
        -20,807 ppm）。パラメータ数の 2 倍の観測点を要求する。
        """
        refs = np.linspace(150.0, 400.0, n)
        res = compute_calibration_coefficients(
            _table(refs, [1e-4] * n), mode, 2)
        assert res["degree"] == expected, res

    def test_always_leaves_degrees_of_freedom(self):
        """どの点数でも自由度が 1 以上残ること（次数 1 の 2 点だけは例外）。"""
        for n in range(2, 20):
            refs = np.linspace(150.0, 400.0, n)
            res = compute_calibration_coefficients(
                _table(refs, [1e-4] * n), "poly3", 2)
            dof = n - (res["degree"] + 1)
            assert dof >= 0, (n, res["degree"])
            if n >= 4:
                assert dof >= 1, f"n={n} で自由度が無い (degree={res['degree']})"


class TestRSquaredHonesty:
    def test_r_squared_is_none_when_it_cannot_be_evaluated(self):
        """★ 完全内挿になる点数では R² を出さない。

        2 点は次数 1 でぴったり通るので R² は必ず 1.0 になる。それを
        「当てはまり完璧」として見せると、外挿での破綻に気づけない。
        （4 点 + poly3 も従来はここに該当したが、次数クランプで 1 次に落ちる）
        """
        res = compute_calibration_coefficients(
            _table([136.0166, 153.0193], [0.0006, 0.0011]), "poly3", 2)
        assert res["degree"] == 1
        assert res["r_squared"] is None, res

    def test_r_squared_is_reported_when_there_is_slack(self):
        """自由度が足りていれば従来どおり数値を返す。"""
        refs = np.linspace(150.0, 400.0, 10)
        res = compute_calibration_coefficients(
            _table(refs, 3e-6 * refs), "linear", 2)
        assert res["r_squared"] is not None
        assert 0.0 <= res["r_squared"] <= 1.0

    def test_zero_variance_is_not_reported_as_perfect(self):
        """誤差が完全に一定なら R² は 0.0（対話側と同じ）。1.0 にしない。

        ★ 誤差を厳密に一定にするため、浮動小数で誤差なく表せる値を使う
        （`r + 0.001` は r ごとに丸めが違い、ss_tot が 0 にならない）。
        """
        refs = [128.0, 256.0, 512.0, 1024.0, 2048.0, 4096.0]
        table = [{"use": True, "ref_mz": r, "obs_mz": r + 0.5} for r in refs]
        assert len({t["obs_mz"] - t["ref_mz"] for t in table}) == 1, "誤差が一定でない"
        res = compute_calibration_coefficients(table, "linear", 2)
        assert res["r_squared"] == 0.0, res


class TestExtrapolationIsSurfaced:
    def test_reference_range_is_returned(self):
        """★ 参照ピークの範囲を返し、呼び出し側が外挿を警告できること。

        同梱リファレンスは m/z 136-379 しか無いのに補正対象は 1000 超まである。
        """
        res = compute_calibration_coefficients(
            _table([137.0233, 155.0339, 177.0158, 273.0399], [1e-4] * 4),
            "poly3", 2)
        assert res["ref_mz_min"] == pytest.approx(137.0233)
        assert res["ref_mz_max"] == pytest.approx(273.0399)


class TestCorrectionStaysSane:
    def test_constant_ppm_drift_is_not_amplified_by_extrapolation(self):
        """★ 本丸。真のドリフトが一定 +3 ppm のとき、外挿で桁違いにならないこと。

        修正前は 4 点 + poly3 で m/z 1000 の補正が -20.8 Da (-20,807 ppm) だった
        （真値は +0.003 Da / +3 ppm）。ピーク検出のばらつきを 3 次で拾ってしまうため。
        クランプ後は 3 次にならないので、この暴れが起きない。
        """
        rng = np.random.default_rng(3)
        refs = np.array([137.0233, 155.0339, 177.0158, 273.0399])
        errors = refs * 3e-6 + rng.normal(0, 0.0005, refs.size)

        res = compute_calibration_coefficients(_table(refs, errors), "poly3", 2)
        coefs = np.array(res["coefficients"])

        for mz in (400.0, 600.0, 800.0, 1000.0):
            corr_ppm = float(np.polyval(coefs, mz)) / mz * 1e6
            assert abs(corr_ppm) < 100.0, (
                f"m/z {mz} の補正が {corr_ppm:.1f} ppm と暴れている "
                f"(degree={res['degree']}, coefs={res['coefficients']})")

    def test_below_min_peaks_returns_none(self):
        assert compute_calibration_coefficients(
            _table([200.0], [1e-4]), "poly3", 2) is None
