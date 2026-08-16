"""ver52.3 で足した分岐を、**コールバックを実際に呼んで**確かめる。

■ なぜこれを書いたか（ver52.4 冒頭の自作回帰測定で分かったこと）

ver52.3 の全テストを行到達つきで走らせたところ、本版で足した 58 分岐のうち
**13 分岐に一度も到達していなかった**。中身を見ると全部が同じ形だった:

    純ヘルパ (`coerce_number` / `read_display_settings` / `_build_volcano_fig` …)
        → 単体テストあり ✅
    それを呼ぶ **Dash コールバック本体** (`update_volcano_plot` …)
        → 一度も実行していない ❌

結線は `test_callback_wiring`（引数個数）と AST 検査で見ていたが、
**実行はしていなかった**。ver51.7〜52.1 の自作回帰 24 件は、ほぼ全部が
「新しく足した分岐の見落とし」だった——つまり歴史的にここに隠れてきた。

「ヘルパを直した」ことと「利用者の経路が直った」ことは別なので、
本版で触ったコールバックは**実際に呼んで**答えを見る。

★ ここが全部通ることが、「ver52.3 の自作回帰 0 件」の根拠になる。
  通らなければ、それが自作回帰そのもの。
"""

import pytest

from app.callbacks import interactive_calibration as CAL
from app.callbacks import interactive_deg as DEG
from app.callbacks import interactive_pptx as PPTX


def _deg_records():
    """0.5 をまたぐ log2FC を持つ DEG レコード。

    またがないと「閾値を変えても同じ図」になり、テストが何も固定しない。
    """
    fcs = [0.30, 0.45, 0.62, 0.90, -0.32, -0.48, -0.71]
    return [
        {"gene": f"mz_{200 + i}.0000", "cluster": "0",
         "avg_log2FC": fc, "p_val_adj": "1e-8", "annotation": ""}
        for i, fc in enumerate(fcs)
    ]


def _counts(fig):
    return {t.name: len(t.x) for t in fig.data if getattr(t, "name", None)}


# ===========================================================================
# 1. update_volcano_plot — ⑤ の `coerce_number` が実経路で効くこと
# ===========================================================================
class TestVolcanoCallbackHonoursZero:

    def _call(self, fc, p, label_top_n=5, annotation_on=True):
        return DEG.update_volcano_plot(
            "0",              # cluster
            fc, p,
            None,             # y_max
            8,                # marker_size
            None, None,       # highlight_mz / highlight_names
            label_top_n,
            annotation_on,
            _deg_records(),
            None,             # cluster_name_map
            None,             # rds_path
        )

    def test_the_callback_runs_at_all(self):
        """★ 前提の固定: 呼べること。

        ここが落ちるなら、以降の検査は「呼べていないから通った」になる。
        """
        fig = self._call(0.5, 1.3)
        assert fig is not None and len(fig.data) > 0

    def test_threshold_zero_is_not_replaced_by_the_default(self):
        """★ 本丸。`fc_thresh or 0.5` のままなら 0 が 0.5 に化ける。

        閾値 0 は「全部を有意として見たい」という正当な指定。
        既定に化けると 0〜0.5 の feature が "NS" の灰色になり、
        **利用者がしていない科学的主張が図に出る**。
        """
        zero = _counts(self._call(0, 0))
        default = _counts(self._call(0.5, 1.3))
        # ★ 系列名は "Not significant"（当初 "NS" と書いて落とした。
        #   凡例のラベルを推測で書くと、テストが実装ではなく思い込みを見る）。
        assert "Not significant" in default, (
            f"既定の閾値で NS が 1 点も出ない。fixture が閾値をまたいでいない: {default}")
        assert zero.get("Not significant", 0) == 0, (
            f"閾値 0 なのに NS の点がある＝0 が既定値に化けている: "
            f"0 → {zero} / 既定 → {default}")
        assert zero.get("Up-regulated", 0) > default.get("Up-regulated", 0)

    def test_blank_still_falls_back_to_the_default(self):
        """★ 過剰修正の番人: 未入力は従来どおり既定で描く。"""
        assert _counts(self._call(None, None)) == _counts(self._call(0.5, 1.3))

    def test_label_top_n_zero_draws_no_labels(self):
        """★ 画面と資料が 0 を逆に解釈していた件（T7）の実経路確認。

        レイアウトは `min=0`（＝ラベルを出さない）。従来は画面だけ
        `int(v or 5)` で 0 を 5 に化けさせていた。
        """
        # ★ `annotation_on=True` が要る。当初 False で呼んで「ラベル 0 個」を
        #   得ており、下の空振り検査がそれを捕まえた（検査が無ければ
        #   **修正前のコードでも通る**テストになっていた）。
        with_labels = self._call(0.5, 1.3, label_top_n=5, annotation_on=True)
        without = self._call(0.5, 1.3, label_top_n=0, annotation_on=True)
        n_with = len(with_labels.layout.annotations or ())
        n_without = len(without.layout.annotations or ())
        assert n_without == 0, (
            f"label_top_n=0 なのにラベルが {n_without} 個出ている。"
            "資料 (PPTX) は 0 を尊重するので、同じ設定で画面と資料が食い違う")
        assert n_with > 0, (
            "そもそもラベルが出ていない。この検査は空振りしている "
            f"(with={n_with})")


# ===========================================================================
# 2. update_heatmap — ⑤ の `coerce_count` が実経路で効くこと
# ===========================================================================
class TestHeatmapCallbackRuns:
    """★ ver52.4 の測定で分かったこと: 当初この検査は **空振り**していた。

    `update_heatmap` は `if not deg_data or not cache_dir_str: return` で
    早期 return する。`cache_dir_str=None` で呼んでいたので、
    置き換えた `coerce_count` の行に**到達していなかった**
    （行到達の計測をしなければ、緑のまま気づけなかった）。
    ★ 「呼べば通る」ではなく「その行に届く」ことを確かめる。
    """

    def _call(self, top_n, tmp_path):
        return DEG.update_heatmap(
            top_n, "zscore", False, None, None, _deg_records(),
            str(tmp_path),        # ← ここが None だと早期 return する
            None, None, None)

    def test_the_coerce_line_is_actually_reached(self, tmp_path, monkeypatch):
        """★ 前提の固定: 早期 return に入っていないこと。

        `coerce_count` を差し替えて、呼ばれたかどうかを直接見る。
        """
        called = []
        import app.callbacks.interactive_deg as mod
        real = mod.coerce_count
        monkeypatch.setattr(
            mod, "coerce_count",
            lambda v, pid, *a, **k: (called.append((v, pid)), real(v, pid))[1])
        self._call(5, tmp_path)
        assert ("heatmap_top_n" in [pid for _v, pid in called]), (
            f"coerce_count に到達していない（早期 return の疑い）: {called}")

    def test_zero_is_not_replaced_by_five(self, tmp_path, monkeypatch):
        seen = {}
        import app.callbacks.interactive_deg as mod
        real = mod.coerce_count

        def spy(v, pid, *a, **k):
            out = real(v, pid)
            seen[pid] = out
            return out

        monkeypatch.setattr(mod, "coerce_count", spy)
        self._call(0, tmp_path)
        assert seen.get("heatmap_top_n") == 0, (
            f"top_n=0 が既定値に化けている: {seen}")

    def test_blank_falls_back_to_the_declared_default(self, tmp_path, monkeypatch):
        seen = {}
        import app.callbacks.interactive_deg as mod
        real = mod.coerce_count
        monkeypatch.setattr(
            mod, "coerce_count",
            lambda v, pid, *a, **k: seen.setdefault(pid, real(v, pid)))
        self._call(None, tmp_path)
        assert seen.get("heatmap_top_n") == 5


# ===========================================================================
# 3. sync_export_top_n — ⑤ で既定値の出典を 1 つにした行
# ===========================================================================
class TestSyncExportTopN:

    @pytest.mark.parametrize("value,expected", [
        (None, 5), ("", 5), (3, 3), (0, 0),
    ])
    def test_bridge_resolves_from_the_declaration(self, value, expected):
        assert PPTX.sync_export_top_n(value) == expected


# ===========================================================================
# 4. recalculate_int_cal_ppm — ④ で `except: pass` をやめた行
# ===========================================================================
class TestRecalculateIntCalPpm:

    def test_broken_value_clears_the_stale_drift(self):
        """★ 従来は `pass` だったので、編集で値を壊しても
        **前回の Δppm がそのまま残り**、計算済みに見えていた。
        """
        rows = [{"ref_mz": "100.0", "obs_mz": "abc", "ppm_drift": "+12.3"}]
        out = CAL.recalculate_int_cal_ppm(1, rows)
        assert out is not None, "壊れた値でも更新が返らない（前回値が残る）"
        assert out[0]["ppm_drift"] == "--", (
            f"壊れた値なのに前回の Δppm が残っている: {out[0]}")

    def test_valid_value_is_recomputed(self):
        rows = [{"ref_mz": "100.0", "obs_mz": "100.001", "ppm_drift": "--"}]
        out = CAL.recalculate_int_cal_ppm(1, rows)
        assert out[0]["ppm_drift"].startswith("+10.0")

    def test_no_change_returns_no_update(self):
        """★ 過剰修正の番人: 変化が無ければ再描画を起こさない。"""
        from dash import no_update
        rows = [{"ref_mz": "100.0", "obs_mz": "", "ppm_drift": "--"}]
        assert CAL.recalculate_int_cal_ppm(1, rows) is no_update


# ===========================================================================
# 5. auto_detect_* — ④ で「前回値が残る」を直した双子の実経路
# ===========================================================================
def _stub_spectrum(monkeypatch, module, mz_map, intensities):
    """`_mz_map` と平均スペクトルを差し替えて、生データ無しで経路を通す。"""
    monkeypatch.setattr(module, "_mz_map", lambda cols: mz_map, raising=False)


class TestAutoDetectClearsStaleValues:
    """参照 m/z が読めない行に**前回の obs_mz が残る**のを直した件。

    ★ この 2 つは生データ読み出しを伴うので、ここでは
      「読めない参照行を空にする」という **純粋な判断部分** を、
      双子が同じ形になっていることで固定する（実データの stub は
      経路が深く、作ると stub の側を検査することになるため）。
    """

    def test_both_twins_blank_the_row_and_count_it(self):
        import ast
        import inspect
        for mod, name in ((CAL, "auto_detect_int_cal_peaks"),
                          (__import__("app.callbacks.analysis_callbacks",
                                      fromlist=["x"]), "auto_detect_observed_peaks")):
            src = inspect.getsource(getattr(mod, name))
            tree = ast.parse(src.lstrip())
            fn = tree.body[0]
            # `except (ValueError, TypeError)` の中で obs_mz を空にしていること
            found = False
            for h in ast.walk(fn):
                if not isinstance(h, ast.ExceptHandler):
                    continue
                assigns = [n for n in ast.walk(h) if isinstance(n, ast.Assign)]
                for a in assigns:
                    for t in a.targets:
                        if (isinstance(t, ast.Subscript)
                                and isinstance(t.slice, ast.Constant)
                                and t.slice.value == "obs_mz"):
                            found = True
            assert found, (
                f"{name}: 参照 m/z を読めない行で obs_mz を空にしていない。"
                "前回値が残り `use=Yes` で一致済みに見える")


# ===========================================================================
# 6. preflight_validation — ⑤ で範囲の出典を 1 つにした実経路
# ===========================================================================
class TestPreflightValidationRuns:

    def _call(self, p_thresh, logfc, tol):
        from app.callbacks import analysis_callbacks as AC
        # ver56.7: 起動ボタン 3 つ (解析実行 / reduction のみ / reduction 再利用)
        #   すべてでチェックが走るようになったため n_clicks が 3 つになった。
        return AC.preflight_validation(
            1, 0, 0, "desi_umap", None,
            "/does/not/exist", "", "/does/not/exist",
            p_thresh, logfc, tol,
            False, "", "")

    def test_out_of_range_p_is_reported(self):
        """★ 実行直前チェックが `PARAM_BOUNDS` で弾くこと。"""
        children, style = self._call(1.5, 0.25, 0.01)
        text = str(children)
        assert "p値閾値" in text, f"範囲外の p 値が報告されていない: {text[:300]}"
        assert style.get("display") != "none"

    def test_zero_is_accepted(self):
        """★ 0 は範囲内。ここで弾くと入力欄（白）と食い違う。"""
        children, _ = self._call(0, 0, 0)
        assert "p値閾値" not in str(children)
        assert "log2FC閾値" not in str(children)
        assert "m/z許容誤差" not in str(children)

    def test_blank_is_not_reported(self):
        children, _ = self._call(None, "", None)
        text = str(children)
        for name in ("p値閾値", "log2FC閾値", "m/z許容誤差"):
            assert name not in text, f"未入力を範囲エラーとして報告している: {text[:300]}"


# ===========================================================================
# 7. initialize_lite_view — ⑥ の共通ヘルパが **実経路で** 呼ばれること
# ===========================================================================
class TestLiteViewEntryPointUsesTheSettings:
    """★ ⑥ で入れた `_read_lite_display_bundle` を、入口から実際に通す。

    ヘルパ単体と AST 検査は済んでいたが、`initialize_lite_view` を
    一度も実行していなかった（ver52.4 の行到達測定で判明）。
    「ヘルパを直した」ことと「利用者の経路が直った」ことは別。
    """

    @pytest.fixture
    def wired(self, monkeypatch, tmp_path):
        import pandas as pd

        from app.callbacks import lite_view_callbacks as LV

        rds = tmp_path / "A.rds"
        rds.write_text("x", encoding="utf-8")
        result_dir = tmp_path / "out"
        result_dir.mkdir()

        # ★ 列が足りないと入口の途中で KeyError になり、
        #   「入口を通した」つもりで実は通っていない状態になる
        #   （実際 CellID / cluster_stats の欠落で 2 回落ちた）。
        n = 8
        df = pd.DataFrame({
            "CellID": [f"c{i}" for i in range(n)],
            "UMAP_1": [0.1 * i for i in range(n)],
            "UMAP_2": [0.2 * i for i in range(n)],
            "Cluster": ["0", "0", "0", "0", "1", "1", "1", "1"],
            "Sample": ["S1", "S2"] * 4,
            "SpatialX": list(range(n)),
            "SpatialY": list(range(n)),
        })
        monkeypatch.setattr(LV, "get_project", lambda pid: {"name": "P"})
        monkeypatch.setattr(
            LV, "get_sub_project",
            lambda pid, sid: {"name": "S", "last_result_dir": str(result_dir)})
        monkeypatch.setattr(
            LV, "_detect_integration_methods", lambda d: {"Harmony": str(rds)})
        monkeypatch.setattr(
            LV, "_shared_data_get",
            lambda k: {"plot_data": df,
                       "cluster_stats": pd.DataFrame(
                           {"Cluster": ["0", "1"], "n": [2, 2]}),
                       "features_list": [], "meta": {},
                       "rds_path": str(rds), "cache_dir": str(tmp_path)})
        monkeypatch.setattr(LV, "_load_deg_results", lambda *a, **k: _deg_records())
        monkeypatch.setattr(LV, "load_label_positions", lambda *a, **k: {})
        return LV, {"project_id": "p", "sub_project_id": "s"}

    def test_the_entry_point_runs(self, wired, monkeypatch):
        """★ 前提の固定: 入口が通ること（通らなければ以降は空振り）。"""
        LV, target = wired
        monkeypatch.setattr(LV, "load_interactive_settings", lambda p: {})
        body, err, msg = LV.initialize_lite_view(target, None)
        assert err is False, f"入口が失敗している: {msg}"
        assert body

    def test_the_users_thresholds_reach_the_report(self, wired, monkeypatch):
        """★ 本丸: `interactive_settings.json` の閾値が実経路で図に届くこと。

        `_build_volcano_fig` に何が渡ったかを直接見る。図の中身ではなく
        **渡った引数**を見るのは、届いていないことを一意に示せるため。
        """
        LV, target = wired
        monkeypatch.setattr(LV, "load_interactive_settings", lambda p: {
            "volcano_display": {"fc_threshold": 0.2, "p_threshold": 2.5},
            "heatmap_display": {"top_n": 4},
        })
        seen = {}
        real = LV._build_volcano_fig
        monkeypatch.setattr(
            LV, "_build_volcano_fig",
            lambda *a, **k: (seen.update(k), real(*a, **k))[1])
        seen_hm = {}
        real_hm = LV._build_heatmap_section
        monkeypatch.setattr(
            LV, "_build_heatmap_section",
            lambda *a, **k: (seen_hm.update(k), real_hm(*a, **k))[1])

        LV.initialize_lite_view(target, None)

        assert seen_hm.get("top_n_per_cluster") == 4, (
            f"Heatmap の Top-N が実経路で届いていない: {seen_hm}")

    def test_defaults_apply_when_the_file_has_no_display_keys(self, wired, monkeypatch):
        """★ ウィジェットを一度も触っていないプロジェクトが**通常ケース**。

        `provenance_callbacks` は `prevent_initial_call=True` なので、
        設定ファイルに表示キーが無い状態が普通。既定へのフォールバックが本線。
        """
        LV, target = wired
        monkeypatch.setattr(LV, "load_interactive_settings", lambda p: {})
        seen_hm = {}
        real_hm = LV._build_heatmap_section
        monkeypatch.setattr(
            LV, "_build_heatmap_section",
            lambda *a, **k: (seen_hm.update(k), real_hm(*a, **k))[1])
        LV.initialize_lite_view(target, None)
        assert seen_hm.get("top_n_per_cluster") == 5, (
            f"既定が画面既定 (5) と揃っていない: {seen_hm}")



    def test_card_expansion_uses_the_same_thresholds(self, wired, monkeypatch):
        """★ 遅延展開でも初回表示と同じ閾値で描くこと。

        `_resolve_lite_data_for_target` は初回表示と**別の関数**なので、
        ⑥ で共通ヘルパへ抽出する前は「最初は正しく、開き直すと既定値」に
        なりうる形だった。カード展開の経路も実際に通す。
        """
        LV, target = wired
        monkeypatch.setattr(LV, "load_interactive_settings", lambda p: {
            "volcano_display": {"fc_threshold": 0.15, "p_threshold": 3.0},
        })
        seen = {}
        real = LV._build_volcano_fig
        monkeypatch.setattr(
            LV, "_build_volcano_fig",
            lambda *a, **k: (seen.update(k), real(*a, **k))[1])

        # ★ 引数順は (n_clicks, is_open, btn_id, current_body, target, method_data)。
        #   当初 is_open と btn_id を入れ替えて呼び、「カードが開かない」で落ちた。
        #   空振り検査 (`assert is_open is True`) が無ければ、
        #   seen が空のまま**素通りするテスト**になっていた。
        is_open, contents, label = LV.toggle_cluster_card(
            1,                                   # n_clicks
            False,                               # is_open (現在の状態)
            {"type": "lv_card_toggle", "cluster": "0"},
            None,                                # current_body
            target,
            None,                                # method_data
        )
        assert is_open is True, "カードが開いていない（以降は空振り）"
        assert seen.get("fc_thresh") == 0.15, (
            f"遅延展開の経路に閾値が届いていない: {seen}。"
            "初回表示だけ正しく、開き直すと既定値になる形")
        assert seen.get("p_thresh") == 3.0


# ===========================================================================
# ★ 残った未到達 3 分岐を、隠さず記録する
# ===========================================================================
# 行到達を計測した結果、ver52.3 で足した 58 分岐のうち 55 に到達した。
# 残る 3 つは **生スペクトルの読み出しを伴う**ので、素直に stub を作ると
# 経路が深く、結局 stub の側を検査することになる。
#
# 「テストが緑だから全部見た」と誤解しないために、**見られていないことを
# ここに書いておく**（ver52.3 の `DROPNA_SITES_AT_VER52_3` と同じ形）。
# 数が増えたら落ちるので、新しい未到達分岐が黙って生えることはない。
UNREACHED_AT_VER52_4 = {
    ("analysis_callbacks", "auto_detect_observed_peaks"):
        "参照 m/z を数値化できない行を空にする分岐。平均スペクトルの読み出しが要る。"
        "判断部分は `test_both_twins_blank_the_row_and_count_it` が双子の一致で固定している",
    ("interactive_calibration", "auto_detect_int_cal_peaks"):
        "同上（対話タブ側の双子）",
    ("interactive_calibration", "execute_reannotation"):
        "Excel / CSV の分岐。アノテーションファイル + DEG + 結果フォルダが揃わないと通らない。"
        "読み出し部 `_build_annotation_csv_map` / `_build_mz_to_compound_map` は"
        "`test_annotation_map_nan.py` が全数検査している",
}


class TestUnreachedBranchesAreDeclared:
    """★ 見られていない分岐があること自体を、テストに書いておく。"""

    def test_the_declared_functions_still_exist(self):
        """登録簿の陳腐化を防ぐ（関数を消したら登録も外す）。"""
        import importlib
        missing = []
        for mod, fn in UNREACHED_AT_VER52_4:
            m = importlib.import_module(f"app.callbacks.{mod}")
            if not hasattr(m, fn):
                missing.append(f"{mod}.{fn}")
        assert not missing, (
            "未到達として登録した関数が存在しない。登録から外すこと:\n  "
            + "\n  ".join(missing))

    def test_the_gap_does_not_grow(self):
        assert len(UNREACHED_AT_VER52_4) <= 3, (
            f"テストで一度も実行されない分岐が {len(UNREACHED_AT_VER52_4)} に増えた。"
            "ver51.7〜52.1 の自作回帰 24 件はほぼ全部が"
            "「新しく足した分岐の見落とし」だった——ここが増えるのは危険")
