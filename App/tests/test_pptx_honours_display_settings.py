"""PPTX の図が「解析条件」スライドと一致すること (ver51.9 / B-2)。

■ 何が起きていたか

同じ 1 つの資料の中で、**条件スライドと図が食い違っていた**。

  - 条件スライドは `provenance_callbacks.save_volcano_settings` が
    `interactive_settings.json` に記録した利用者の閾値を出す
    （「Volcano 閾値（表示用）: log2FC = 1.0 / -log10(p) = 3.0」）
  - Volcano の図は `_build_volcano_fig_for_cluster(deg_data, cl_str)` と
    **既定値のまま**呼ばれるので、破線は常に 0.5 / 1.3 に引かれ、
    有意色分けもその閾値で行われる

利用者が閾値を厳しくして図を確認し、その資料を配る。受け取った人は
条件スライドの数字を信じて図を読む。**両方が同じファイルの中にあるのに
一致していない**ので、どちらが本当かを外から知る方法が無い。

同種の取りこぼし:
  - Heatmap の `scale` (Z-score / Raw) を無視 → 「何をプロットしたか」が変わる
  - Volcano のラベル件数 `label_top_n` を無視
  - Feature plot の強度レンジ (`intensity_min` / `intensity_max`) を無視
    → 色域が変わるので、同じ図に見えて別のことを示す

★ 直し方は「条件スライドと図が**同じ 1 つの dict** を見る」。
  クリック時点で確定した `conditions` から取り出せば、
  構造上ずれようが無い（別々に読むと、また片方だけ直し忘れる）。
"""

import pytest

from app.callbacks.interactive_pptx import (
    _build_volcano_fig_for_cluster,
    _display_settings,
)


def _deg_rows():
    """閾値 0.5/1.3 と 1.0/3.0 で有意判定が変わる 4 点。"""
    return [
        # gene,      log2FC, p_adj      -log10(p)
        {"gene": "A", "cluster": "0", "avg_log2FC": 0.7, "p_val_adj": 1e-2},   # 2.0
        {"gene": "B", "cluster": "0", "avg_log2FC": 2.0, "p_val_adj": 1e-5},   # 5.0
        {"gene": "C", "cluster": "0", "avg_log2FC": -0.6, "p_val_adj": 1e-2},
        {"gene": "D", "cluster": "0", "avg_log2FC": 0.1, "p_val_adj": 0.5},
    ]


def _conditions(**inter):
    return {"interactive": inter}


# ---------------------------------------------------------------------------
# 1. 条件からの取り出し
# ---------------------------------------------------------------------------

class TestDisplaySettingsExtraction:
    def test_reads_the_users_volcano_thresholds(self):
        s = _display_settings(_conditions(volcano_display={
            "fc_threshold": 1.0, "p_threshold": 3.0, "label_top_n": 20}))
        assert s["volcano_fc"] == 1.0
        assert s["volcano_p"] == 3.0
        assert s["volcano_top_n"] == 20

    def test_reads_heatmap_scale(self):
        s = _display_settings(_conditions(heatmap_display={"scale": "raw"}))
        assert s["heatmap_scale"] == "raw"

    def test_reads_feature_intensity_range(self):
        s = _display_settings(_conditions(feature_display={
            "intensity_min": 10, "intensity_max": 90}))
        assert s["feature_intensity_min"] == 10
        assert s["feature_intensity_max"] == 90

    @pytest.mark.parametrize("conditions", [None, {}, {"interactive": {}},
                                            {"interactive": {"volcano_display": None}}])
    def test_missing_settings_fall_back_to_the_old_defaults(self, conditions):
        """★ 過剰修正の番人: 記録が無いときは従来と同じ図になること。

        ここを None のまま流すと、条件を一度も触っていない
        プロジェクトで図が空になったり例外になったりする。
        """
        s = _display_settings(conditions)
        assert s["volcano_fc"] == 0.5
        assert s["volcano_p"] == 1.3
        assert s["heatmap_scale"] is None      # None = 従来の自動判定

    def test_garbage_values_do_not_crash(self):
        """記録が壊れていても既定に落ちること（長時間のエクスポートを守る）。"""
        s = _display_settings(_conditions(volcano_display={
            "fc_threshold": "たくさん", "p_threshold": None}))
        assert s["volcano_fc"] == 0.5
        assert s["volcano_p"] == 1.3


# ---------------------------------------------------------------------------
# 2. 図が実際にその閾値で描かれること
# ---------------------------------------------------------------------------

def _dashed_lines(fig):
    """破線 (閾値ライン) の x / y を集める。"""
    shapes = fig.layout.shapes or ()
    xs, ys = set(), set()
    for sh in shapes:
        if getattr(sh, "line", None) is None or sh.line.dash != "dash":
            continue
        if sh.x0 == sh.x1:
            xs.add(round(float(sh.x0), 6))
        if sh.y0 == sh.y1:
            ys.add(round(float(sh.y0), 6))
    return xs, ys


class TestVolcanoUsesTheThresholds:
    def test_default_thresholds_unchanged(self):
        """過剰修正の番人: 引数を渡さない従来の呼び方は従来どおり。"""
        fig = _build_volcano_fig_for_cluster(_deg_rows(), "0")
        xs, ys = _dashed_lines(fig)
        assert xs == {0.5, -0.5}, xs
        assert ys == {1.3}, ys

    def test_user_thresholds_move_the_lines(self):
        """★ 利用者の閾値で破線が引かれること。"""
        fig = _build_volcano_fig_for_cluster(
            _deg_rows(), "0", fc_thresh=1.0, p_thresh=3.0)
        xs, ys = _dashed_lines(fig)
        assert xs == {1.0, -1.0}, xs
        assert ys == {3.0}, ys

    def test_significance_colouring_follows_the_thresholds(self):
        """★ 線だけでなく**有意判定**も変わること。

        破線だけ動かして色分けが既定のままだと、
        「線の内側なのに有意色」という別種の嘘になる。
        """
        loose = _build_volcano_fig_for_cluster(
            _deg_rows(), "0", fc_thresh=0.5, p_thresh=1.3)
        strict = _build_volcano_fig_for_cluster(
            _deg_rows(), "0", fc_thresh=1.0, p_thresh=3.0)

        def _n_coloured(fig):
            """既定色 (灰) 以外の点の数。"""
            n = 0
            for tr in fig.data:
                name = (tr.name or "").lower()
                if "not" in name or "non" in name:
                    continue
                n += 0 if tr.x is None else len(tr.x)
            return n

        assert _n_coloured(loose) > _n_coloured(strict), (
            "閾値を厳しくしても有意点の数が減っていない"
            f" (loose={_n_coloured(loose)}, strict={_n_coloured(strict)})")


# ---------------------------------------------------------------------------
# 3. エクスポート経路が実際にそれを通ること
# ---------------------------------------------------------------------------

class TestExportPathWiresItThrough:
    """★ ヘルパを足しても _build_pptx へ渡さなければ意味が無い。

    B-3 で学んだとおり「呼んでいるか」を AST で見る番人は元の欠陥を
    捕まえられないことがあるので、**呼び出しの実引数**を記録して見る。
    """

    def test_build_pptx_receives_display_settings(self, monkeypatch, tmp_path):
        pytest.importorskip("pptx")
        import base64
        import sys
        import types

        import pandas as pd

        import app.callbacks.interactive_pptx as P

        if "kaleido" not in sys.modules:
            monkeypatch.setitem(sys.modules, "kaleido", types.ModuleType("kaleido"))

        rds = tmp_path / "RDS_Files" / "seu_harmony.rds"
        rds.parent.mkdir(parents=True)
        rds.write_bytes(b"x")

        df = pd.DataFrame([
            {"CellID": f"c{i}", "Sample": "S1", "Cluster": "0",
             "UMAP_1": float(i), "UMAP_2": float(i), "Annotation": "S1"}
            for i in range(4)])

        seen = {}
        monkeypatch.setattr(
            P._bridge, "extract_data",
            lambda r: {"plot_data": df.copy(),
                       "meta": {"n_cells": 4, "n_clusters": 1},
                       "cache_dir": None})
        monkeypatch.setattr(P._bridge, "ensure_expression_matrix",
                            lambda *a, **k: None)
        monkeypatch.setattr(P, "_load_deg_results", lambda *a, **k: None)
        monkeypatch.setattr(
            P, "_fig_to_png_bytes",
            lambda *a, **k: base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
                "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="))

        def _fake_build(*a, **k):
            seen.update(k)
            return b"PPTX"

        monkeypatch.setattr(P, "_build_pptx", _fake_build)

        P.cb_export_report(
            lambda *_: None, 1, {"data": [], "layout": {}}, None, str(rds),
            [], None, None, None, None, None, {}, None, None, 5, None, None,
            None,            # rds_map なし → 単一手法経路
            None, "Harmony", [], False, {}, None,
        )

        assert "display_settings" in seen, (
            "_build_pptx に表示設定が渡っていない。"
            f"渡されたキー: {sorted(seen)}")


class TestVolcanoInsideBuildPptx:
    """★ 計画の受入基準そのもの:
    「Volcano の破線が『解析条件』スライドの数値と一致すること」。

    `_build_pptx` を条件付きで走らせ、**中で作られる Volcano** に
    どの閾値が渡ったかを見る。ここが一致しなければ、資料は自己矛盾する。
    """

    def _run(self, monkeypatch, conditions):
        pytest.importorskip("pptx")
        import base64
        import pandas as pd

        import app.callbacks.interactive_pptx as P

        seen = []

        def _spy(deg_data, cluster, **k):
            seen.append(k)
            return None                      # 図は要らない（配置処理を飛ばす）

        monkeypatch.setattr(P, "_build_volcano_fig_for_cluster", _spy)
        monkeypatch.setattr(
            P, "_fig_to_png_bytes",
            lambda *a, **k: base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
                "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="))
        monkeypatch.setattr(P, "_build_heatmap_for_cluster",
                            lambda *a, **k: None)

        df = pd.DataFrame([
            {"CellID": f"c{i}", "Sample": "S1", "Cluster": "0",
             "UMAP_1": float(i), "UMAP_2": float(i), "Annotation": "S1"}
            for i in range(4)])

        P._build_pptx(
            {"data": [], "layout": {}}, None, {"n_cells": 4, "n_clusters": 1},
            [], None, df=df, deg_data=_deg_rows(), include_deg=True,
            conditions=conditions)
        return seen

    def test_uses_the_recorded_thresholds(self, monkeypatch):
        seen = self._run(monkeypatch, _conditions(volcano_display={
            "fc_threshold": 1.0, "p_threshold": 3.0, "label_top_n": 20}))
        assert seen, "Volcano が 1 度も作られていない（テストの前提が崩れた）"
        assert seen[0].get("fc_thresh") == 1.0, seen[0]
        assert seen[0].get("p_thresh") == 3.0, seen[0]
        assert seen[0].get("label_top_n") == 20, seen[0]

    def test_without_conditions_the_old_defaults_are_used(self, monkeypatch):
        """★ 過剰修正の番人: 条件が無ければ従来と同じ図になること。"""
        seen = self._run(monkeypatch, None)
        assert seen
        assert seen[0].get("fc_thresh") == 0.5, seen[0]
        assert seen[0].get("p_thresh") == 1.3, seen[0]
