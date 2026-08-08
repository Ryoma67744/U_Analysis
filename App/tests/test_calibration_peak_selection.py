"""窓内ピークの選択が「強度不明」を「強度 0」と同一視する (ver52.3 で発見)。

■ 何が起きるか

`_calibrate_mz` は参照 m/z ごとに探索窓内の feature を集め、
**最も強いピーク**をその参照の観測値として採る:

    best_idx = None
    best_intensity = -1
    for idx in within_window:
        intensity = avg_spectrum.get(feature_names[idx], 0.0)   # ← 欠損も 0.0
        if intensity > best_intensity:                          # ← 0.0 > -1 は真
            best_intensity = intensity
            best_idx = idx

`avg_spectrum` に無い feature は `0.0` になり、番兵が `-1` なので
**最初の要素が必ず採用される**。`mz_array` は `features_list` の順なので、
実質「窓内で最初に現れた feature（≒最小 m/z）」が選ばれる。

強度が 1 つも分からない窓では、**強度を見ずに選んだ観測値**が
そのまま ppm ずれとして較正回帰に入り、その係数がデータセット全 m/z に適用される。

■ 到達可能性

`_calibrate_mz` の docstring 自身が
「avg_spectrum は **参照窓の外は入っていなくてよい**」と契約している。
窓**内**が欠ける経路も在る: `_features_within_windows`（同ファイル :360）は
参照 m/z の数値化に失敗した参照を握りつぶして飛ばすので、
その参照の窓は読み出し対象から外れる。一方 `_calibrate_mz` 側は
`features_list` 全体を走査するため、**窓は存在するのに強度だけ無い**状態になる。

（`_features_within_windows` の握りつぶしは現状どの呼び出し元からも到達しない
 ——全て `float` 済みのリストを渡している——が、この番兵は
 「そこが到達可能になった瞬間に誤った較正を出す」形で待っている。）

■ 直し方

「強度が無い」と「強度が 0」を区別する。`avg_spectrum` に無い feature は
候補から外し、窓内に候補が 1 つも無ければその参照は採用しない
（`continue`）。ver52.3 ④ で直す。
"""

import pytest

from app.callbacks import interactive_calibration as IC

FEATURES = ["m/z 100.0000", "m/z 100.3000", "m/z 200.0000", "m/z 200.4000"]


def test_intensity_is_used_when_it_is_known():
    """前提の固定: 強度が分かっていれば正しく最大を選ぶ。"""
    res = IC._calibrate_mz(
        FEATURES,
        avg_spectrum={"m/z 100.0000": 1.0, "m/z 100.3000": 9.0},
        reference_mz=[100.2], search_window=0.5, min_peaks=1,
        regression_mode="linear")
    obs = [r["obs_mz"] for r in res["report"]]
    assert obs == [100.3], f"最大強度のピークが選ばれていない: {obs}"


class TestUnknownIntensityIsNotTreatedAsZero:

    @pytest.mark.xfail(strict=True, reason=(
        "ver52.3 ④ で直す。番兵 -1 と既定 0.0 のせいで、強度が 1 つも"
        "分からない窓でも最初の feature が『最大強度』として採用される"))
    def test_window_without_any_known_intensity_is_skipped(self):
        """★ 本丸: 強度が全く分からない窓は、較正の根拠に使わないこと。"""
        res = IC._calibrate_mz(
            FEATURES, avg_spectrum={},
            reference_mz=[100.2, 200.2], search_window=0.5, min_peaks=1,
            regression_mode="linear")
        assert not res["report"], (
            "強度が 1 つも分からない窓から観測 m/z を採用している: "
            f"{[(r['ref_mz'], r['obs_mz'], r['avg_intensity']) for r in res['report']]}。"
            "この ppm ずれが較正回帰に入り、係数が全 m/z に適用される")

    def test_the_symptom_is_real(self):
        """★ 症状の実測（xfail 側が直るまでの記録）。

        直ったらここも見直す必要があるので、症状そのものを固定しておく。
        """
        res = IC._calibrate_mz(
            FEATURES, avg_spectrum={},
            reference_mz=[100.2], search_window=0.5, min_peaks=1,
            regression_mode="linear")
        if not res["report"]:
            pytest.skip("すでに修正済み（xfail 側が xpass して知らせる）")
        r = res["report"][0]
        assert r["avg_intensity"] == 0.0, "強度不明が 0.0 として記録されている"
        assert r["obs_mz"] == 100.0, (
            "窓内の最小 m/z が『最大強度のピーク』として選ばれている "
            f"(選ばれたのは {r['obs_mz']})")
        assert abs(r["ppm_drift"]) > 1000, (
            "強度を見ずに選んだ観測値から、大きな ppm ずれが回帰へ渡っている")
