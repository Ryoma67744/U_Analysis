"""残り時間の推定が実際のパイプラインの形に合っていること (ver65.0)。

--------------------------------------------------------------------------
症状: 「残り時間」が一切あてにならない
--------------------------------------------------------------------------
従来の推定は `analysis_callbacks._format_remaining_time()` の 3 行だった。

    progress_ratio = step_current / step_total       # 13 段を等重量とみなす
    remaining      = elapsed / progress_ratio * (1 - progress_ratio)

実測 (CHANGELOG ver63.3 (3): 68 分の内訳 DEG 61% / 作図 22%) では DEG 1 段が
所要の 61% を占めるのに、この式は 1/13 = 7.7% としか数えない。結果:

  - DEG に入った瞬間: 実際は残り 61 分なのに「残り約 12 分」(5 倍のずれ)
  - その 41 分のあいだ、表示は 11 分 → 78 分へ **増え続ける**（実際は減っている）
  - DEG を抜けた瞬間: 実際は残り 19 分なのに「残り約 57 分」(3 倍の逆方向)

さらに段 4-10 は R の `run_downstream_analysis()` の中にあり、この関数は既定で
最大 3 回呼ばれる（harmony / pca_uncorrected / rpca）。等重量モデルには巡回の
概念が無く、1 巡目が終わった時点でバーが 77% を指したまま、残り 2 巡（全体の
半分以上）を動かずに待たせていた。

加えて段の検出は末尾 600 行の**窓**で行っていたため、1 つの段が 600 行を超える
出力を出すと（`FindAllMarkers` は `verbose=FALSE` を渡していない）その段の行が
窓から押し出され、進捗が後退したり「準備中」へ巻き戻った。

--------------------------------------------------------------------------
直し方
--------------------------------------------------------------------------
(1) 段に重みを持たせる (2) R が出す `[stage] <名> (前段 N 秒) [t=<ISO>]` から
この実行の実測ペースを取る (3) `[plan]/[pass]` 行で巡数を数える。
段の記録が無いログ（DESI テンプレート / ver63.3 以前）は従来どおりの部分一致に
落ちるが、そちらも重み付きになる。

このファイルは「修正を戻すと落ちる」ことを確認済み。
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.services import progress_estimator as pe
from app.services.analysis_runner import get_analysis_log_markers


T0 = datetime(2026, 9, 8, 10, 0, 0)

# 実測配分 (DEG 61% / 作図 22%) を持つ 68 分の TIMS 実行。
# 段名は R テンプレートが実際に `.stage_mark()` へ渡している文字列。
_RUN_68MIN = [
    ("Reading Parquet / input files", 208),
    ("Preprocessing (variable features / scaling / PCA)", 83),
    ("Harmony correction", 83),
    ("FindClusters", 69),
    ("Finding Markers", 2489),          # 61%
    ("Annotating", 41),
    ("Generating Heatmap", 251),
    ("Volcano Plots", 107),
    ("MSI Images (Top5)", 404),
    ("TIC Overlay", 134),
    ("Running RPCA", 152),
    ("Saving outputs / finalizing", 48),
]
_TOTAL_68MIN = sum(d for _n, d in _RUN_68MIN)


def _log_upto(stage_index: int, extra_sec: float = 0.0, passes: int = 1,
              durations=None) -> tuple[str, datetime]:
    """`stage_index` 段目まで印が出ている状態のログと、その時点の時刻を返す。"""
    durations = durations or _RUN_68MIN
    lines = [f"[plan] downstream_passes={passes}"]
    t = T0
    prev = None
    for i, (name, dur) in enumerate(durations):
        if i > stage_index:
            break
        stamp = t.isoformat(timespec="seconds")
        if prev is None:
            lines.append(f"[stage] {name} [t={stamp}]")
        else:
            lines.append(f"[stage] {name} (前段 {prev:.1f} 秒) [t={stamp}]")
        prev = dur
        if i < stage_index:
            t += timedelta(seconds=dur)
    return "\n".join(lines), t + timedelta(seconds=extra_sec)


def _old_formula(elapsed: float, step_current: int, step_total: int = 13) -> float:
    """ver64.1 までの式。比較のためだけに置く。"""
    ratio = step_current / step_total
    return elapsed / ratio * (1 - ratio)


# ---------------------------------------------------------------------------
# 段の重み
# ---------------------------------------------------------------------------

class TestStageWeights:
    @pytest.mark.parametrize("analysis_type",
                             ["desi_v8", "tims_v8",
                              "desi_cluster_filter", "tims_cluster_filter"])
    def test_weights_sum_to_one_per_pass(self, analysis_type):
        """1 巡ぶんの重みの合計が 1.00 であること（配分の出典を保つ）。"""
        total = sum(w for _n, _k, w, _d in pe.stage_table(analysis_type))
        assert total == pytest.approx(1.0, abs=0.001), analysis_type

    def test_the_stages_are_not_equally_weighted(self):
        """★ 等重量に戻っていないこと。

        これが等しくなると、DEG (実測 61%) を 1/13 = 7.7% と数える
        従来の壊れたモデルに逆戻りする。
        """
        ws = [w for _n, _k, w, _d in pe.stage_table("tims_v8")]
        mean = sum(ws) / len(ws)          # 等重量ならどの段もこの値になる
        assert max(ws) > 5 * mean, "段が等重量になっている"

    def test_deg_carries_the_measured_share(self):
        """DEG の重みは実測 (61%) と一致していること。"""
        table = {n: w for n, _k, w, _d in pe.stage_table("tims_v8")}
        assert table["Markers"] == pytest.approx(0.61, abs=0.01)
        plots = sum(table[n] for n in
                    ("Heatmap", "Volcano", "MSI Images", "TIC Overlay"))
        assert plots == pytest.approx(0.22, abs=0.01)

    def test_downstream_stages_are_flagged(self):
        """段 4-10 は `run_downstream_analysis()` の中（巡回する）と印が付くこと。"""
        inside = [n for n, _k, _w, d in pe.stage_table("tims_v8") if d]
        assert inside == ["Clustering", "Markers", "Annotation",
                          "Heatmap", "Volcano", "MSI Images", "TIC Overlay"]


# ---------------------------------------------------------------------------
# 実測 68 分の実行での精度
# ---------------------------------------------------------------------------

class TestAccuracyOnTheMeasuredRun:
    def test_entering_deg_no_longer_understates_by_five_times(self):
        """★ DEG に入った瞬間のずれが桁で縮むこと。

        従来: 実際 61 分 / 表示 12 分（5 倍の過小）。
        """
        log, now = _log_upto(4)                      # Finding Markers に入った
        est = pe.estimate("tims_v8", log, T0, now=now)
        actual = _TOTAL_68MIN - sum(d for _n, d in _RUN_68MIN[:4])

        old = _old_formula(sum(d for _n, d in _RUN_68MIN[:4]), 5)
        assert old < actual / 4, "前提が崩れている（従来式はもっと外れていたはず）"
        assert abs(est.remaining_sec - actual) < 15 * 60, (
            f"残り {actual/60:.0f} 分に対し {est.remaining_sec/60:.0f} 分")

    def test_the_estimate_falls_while_waiting(self):
        """★ 待っているあいだ表示が **増えない** こと。

        従来は DEG の 41 分で 11 分 → 78 分へ増え続けた（向きが逆）。
        """
        seen = []
        for frac in (0.0, 0.1, 0.25, 0.5, 0.75, 0.95):
            log, now = _log_upto(4, extra_sec=_RUN_68MIN[4][1] * frac)
            seen.append(pe.estimate("tims_v8", log, T0, now=now).remaining_sec)
        assert all(b <= a + 1 for a, b in zip(seen, seen[1:])), seen
        assert seen[0] > seen[-1], "まったく減っていない"

    def test_the_second_half_lands_within_a_couple_of_minutes(self):
        """DEG を抜けたあとは実測ペースが効いて数分の誤差に収まること。"""
        for i in range(5, len(_RUN_68MIN)):
            log, now = _log_upto(i)
            est = pe.estimate("tims_v8", log, T0, now=now)
            actual = _TOTAL_68MIN - sum(d for _n, d in _RUN_68MIN[:i])
            assert abs(est.remaining_sec - actual) < 3 * 60, (
                f"{_RUN_68MIN[i][0]}: 実際 {actual:.0f}s / 推定 {est.remaining_sec:.0f}s")

    def test_the_progress_bar_tracks_work_not_step_count(self):
        """★ バーが「段の数」ではなく「作業量」で進むこと。

        DEG に入った時点は作業量では 1 割ほどしか終わっていない。
        従来のモデルはここで 5/13 = 38% を指していた。
        """
        log, now = _log_upto(4)
        est = pe.estimate("tims_v8", log, T0, now=now)
        assert est.fraction < 0.20, est.fraction


# ---------------------------------------------------------------------------
# downstream の巡回
# ---------------------------------------------------------------------------

class TestDownstreamPasses:
    def test_one_finished_pass_is_not_reported_as_almost_done(self):
        """★ 3 巡構成で 1 巡目を終えても「もうすぐ完了」にしないこと。

        従来は段 10/13 = 77% を指し、残り 2 巡（全体の半分以上）のあいだ
        バーが一切動かなかった。
        """
        log = "\n".join([
            "[plan] downstream_passes=3",
            "[stage] Reading Parquet / input files [t=2026-09-08T10:00:00]",
            "[pass] downstream 1/3 (harmony)",
            "[stage] FindClusters (前段 300.0 秒) [t=2026-09-08T10:05:00]",
            "[stage] Finding Markers (前段 60.0 秒) [t=2026-09-08T10:06:00]",
            "[stage] TIC Overlay (前段 1800.0 秒) [t=2026-09-08T10:36:00]",
            "[stage] Done (前段 60.0 秒) [t=2026-09-08T10:37:00]",
        ])
        est = pe.estimate("tims_v8", log, T0,
                          now=datetime(2026, 9, 8, 10, 37, 30))
        assert est.pass_total == 3
        assert est.fraction < 0.55, f"1 巡目終了で {est.fraction:.0%} を指している"

    def test_a_skipped_pass_is_taken_back(self):
        """★ 走らなかった巡は予定から引くこと。

        引かないと、RPCA が skip された実行で「まだ 1 巡ある」と信じたまま
        終わってしまう（残り時間が過大になる）。
        """
        base = ["[plan] downstream_passes=3",
                "[stage] Reading Parquet [t=2026-09-08T10:00:00]",
                "[pass] downstream 1/3 (harmony)",
                "[stage] FindClusters (前段 60.0 秒) [t=2026-09-08T10:01:00]"]
        now = datetime(2026, 9, 8, 10, 1, 30)
        keep = pe.estimate("tims_v8", "\n".join(base), T0, now=now)
        drop = pe.estimate("tims_v8", "\n".join(base + ["[pass] skip rpca"]),
                           T0, now=now)
        assert keep.pass_total == 3 and drop.pass_total == 2
        assert drop.remaining_sec < keep.remaining_sec

    def test_the_pass_number_is_shown(self):
        log = "\n".join([
            "[plan] downstream_passes=3",
            "[pass] downstream 1/3 (harmony)",
            "[stage] FindClusters [t=2026-09-08T10:00:00]",
            "[pass] downstream 2/3 (pca_uncorrected)",
            "[stage] FindClusters (前段 60.0 秒) [t=2026-09-08T10:01:00]",
            "[stage] Finding Markers (前段 30.0 秒) [t=2026-09-08T10:01:30]",
        ])
        est = pe.estimate("tims_v8", log, T0,
                          now=datetime(2026, 9, 8, 10, 2, 0))
        assert est.pass_current == 2 and est.pass_total == 3
        assert "2巡目/3" in pe.format_step(est)


# ---------------------------------------------------------------------------
# ログの読み方
# ---------------------------------------------------------------------------

class TestMarkerParsing:
    def test_an_unknown_stage_still_carries_its_measurement(self):
        """★ 段の定義に無い印 (`[stage] Done`) でも「前段 N 秒」を落とさないこと。

        落とすと、巡の最後の段 (TIC Overlay) の実測が毎回失われる。
        """
        log = "\n".join([
            "[stage] TIC Overlay [t=2026-09-08T10:00:00]",
            "[stage] Done (前段 120.0 秒) [t=2026-09-08T10:02:00]",
            "[stage] Saving outputs (前段 5.0 秒) [t=2026-09-08T10:02:05]",
        ])
        est = pe.estimate("tims_v8", log, T0,
                          now=datetime(2026, 9, 8, 10, 2, 10))
        assert est.stage_seconds.get("TIC Overlay") == pytest.approx(120.0)
        assert est.step_name == "Saving"

    def test_a_log_without_timestamps_still_works(self):
        """ver63.3 の実行（[t=] が無い）でも落ちないこと。"""
        log = "\n".join([
            "[stage] Reading Parquet",
            "[stage] Finding Markers (前段 300.0 秒)",
        ])
        est = pe.estimate("tims_v8", log, T0,
                          now=T0 + timedelta(seconds=400))
        assert est.step_name == "Markers"
        assert est.remaining_sec is not None

    def test_markers_are_read_from_the_whole_log_not_a_window(self, tmp_path):
        """★ 段の行を末尾 N 行の窓で探さないこと。

        窓だと、1 つの段が窓より多く出力した時点でその段の行が押し出され、
        進捗が後退したり「準備中」へ巻き戻る。
        """
        log = tmp_path / "analysis_log.txt"
        log.write_text(
            "[stage] Finding Markers (前段 10.0 秒) [t=2026-09-08T10:00:00]\n"
            + "Calculating cluster noise\n" * 5000,
            encoding="utf-8")
        markers = get_analysis_log_markers(str(log))
        assert "[stage] Finding Markers" in markers
        assert "Calculating cluster" not in markers, "マーカー以外を混ぜている"
        assert len(markers.splitlines()) == 1

    def test_missing_log_file_is_harmless(self, tmp_path):
        assert get_analysis_log_markers(str(tmp_path / "nope.txt")) == ""


# ---------------------------------------------------------------------------
# 段の記録が無いログ（DESI / 旧実行）へのフォールバック
# ---------------------------------------------------------------------------

class TestFallback:
    def test_desi_uses_keyword_detection_with_weights(self):
        log = "Reading DESI data from: /x/a.txt\nspot filtering done\nRunning PCA\n"
        est = pe.estimate("desi_v8", log, T0, now=T0 + timedelta(seconds=600))
        assert est.step_name == "PCA"
        assert est.remaining_sec is not None
        assert not est.measured, "実測が無いのに measured を立てている"

    def test_reaching_a_step_does_not_count_it_as_finished(self):
        """★ 到達した段を完了扱いにしないこと（従来の 1 段ぶんの過大評価）。

        段 3/12 に入った時点で終わっているのは 2 段ぶん。
        """
        log = "Reading DESI data\nspot filtering\nRunning PCA\n"
        est = pe.estimate("desi_v8", log, T0, now=T0 + timedelta(seconds=600))
        ws = [w for _n, _k, w, _d in pe.stage_table("desi_v8")]
        assert est.fraction == pytest.approx(sum(ws[:2]) / sum(ws), abs=1e-6)

    def test_nothing_detected_is_not_a_number(self):
        est = pe.estimate("tims_v8", "starting up...\n", T0,
                          now=T0 + timedelta(seconds=30))
        assert est.step_current == 0
        assert est.remaining_sec is None
        assert pe.format_remaining(est) == "残り時間: 推定中..."


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------

class TestDisplay:
    def test_a_thin_estimate_is_shown_as_a_range(self):
        """★ 裏づけが薄いうちは 1 つの数字に言い切らないこと。

        従来は「残り約 12 分」と断言して 61 分待たせていた。
        """
        log, now = _log_upto(1)          # 1 段しか実測できていない
        est = pe.estimate("tims_v8", log, T0, now=now)
        text = pe.format_remaining(est)
        assert "〜" in text and "目安" in text, text
        assert est.confidence < pe._CONFIDENT_AT

    def test_the_range_brackets_the_truth_early_on(self):
        """幅で出しているあいだは、その幅が実際の残りを含むこと。"""
        for i in (1, 2, 3, 4):
            log, now = _log_upto(i)
            est = pe.estimate("tims_v8", log, T0, now=now)
            if est.confidence >= pe._CONFIDENT_AT:
                continue
            actual = _TOTAL_68MIN - sum(d for _n, d in _RUN_68MIN[:i])
            spread = 1.0 + 1.2 * (1.0 - est.confidence / pe._CONFIDENT_AT)
            assert est.remaining_sec / spread <= actual <= est.remaining_sec * spread, (
                f"{_RUN_68MIN[i][0]}: 実際 {actual/60:.0f} 分が幅の外")

    def test_a_confident_estimate_is_a_single_number(self):
        log, now = _log_upto(6)
        est = pe.estimate("tims_v8", log, T0, now=now)
        assert est.confidence >= pe._CONFIDENT_AT
        assert pe.format_remaining(est).startswith("残り約 ")

    def test_step_label_keeps_the_old_shape(self):
        log, now = _log_upto(4)
        est = pe.estimate("tims_v8", log, T0, now=now)
        assert pe.format_step(est) == "Markers (5/13)"


# ---------------------------------------------------------------------------
# 呼び出し側との接続
# ---------------------------------------------------------------------------

class TestWiredIntoTheCallback:
    def test_the_callback_uses_the_estimator(self):
        import inspect
        import app.callbacks.analysis_callbacks as ac
        src = inspect.getsource(ac.update_progress)
        assert "_estimator.estimate(" in src
        assert "get_analysis_log_markers" in src
        assert "step_current / step_total" not in src, (
            "等重量モデルが復活している")

    def test_the_back_compat_wrapper_still_answers(self):
        cur, total, name = __import__(
            "app.callbacks.analysis_callbacks", fromlist=["x"]
        )._detect_current_step("Reading DESI data\nRunning PCA\n", "desi_v8")
        assert (cur, total, name) == (3, 12, "PCA")


# ---------------------------------------------------------------------------
# R テンプレート側の出力
# ---------------------------------------------------------------------------

class TestRTemplateEmitsWhatWeParse:
    @pytest.fixture
    def tims_r(self):
        p = (Path(__file__).resolve().parent.parent / "Script" / "TIMS"
             / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R")
        return p.read_text(encoding="utf-8")

    def test_stage_lines_carry_a_timestamp(self, tims_r):
        """★ 段の行に時刻が入ること。

        時刻が無いと「いまの段に入って何秒か」が引けず、段の途中を
        滑らかに詰められない（＝41 分間ずっと同じ数字か、逆向きに動く）。
        """
        assert "[t=%s]" in tims_r
        assert 'format(.now, "%Y-%m-%dT%H:%M:%S")' in tims_r

    def test_the_stage_name_and_prev_seconds_are_preserved(self, tims_r):
        """既存のキーワード判定を壊さないこと（段名と「(前段 N 秒)」を残す）。"""
        assert "[stage] %s (前段 %.1f 秒) [t=%s]" in tims_r

    def test_the_pass_plan_is_declared(self, tims_r):
        assert "[plan] downstream_passes=%d" in tims_r
        assert "[pass] downstream %d/%d (%s)" in tims_r
        assert "[pass] skip %s" in tims_r

    def test_a_skipped_pass_is_taken_back_in_r(self, tims_r):
        """予定した巡が走らない 2 経路（無補正PCA / RPCA）で取り消すこと。"""
        assert tims_r.count('.skip_downstream("pca_uncorrected")') >= 1
        assert '.skip_downstream("rpca")' in tims_r

    def test_loading_is_marked(self, tims_r):
        """★ 読み込みにも段の印があること。

        無いと、実測で 1 割前後を占めるこの区間が「準備中」のまま動かない。
        """
        assert '.stage_mark("Reading Parquet / input files")' in tims_r

    def test_every_keyword_has_a_producer(self, tims_r):
        """段の定義のキーワードが R 側の出力に実在すること。

        ver63.3 で `findclusters` と `saving` が**恒久的に到達不能**だった
        のと同じ穴を作らない。
        """
        low = tims_r.lower()
        for name, keyword, _w, _d in pe.stage_table("tims_v8"):
            assert keyword in low, f"{name}: '{keyword}' を出す行が R に無い"
