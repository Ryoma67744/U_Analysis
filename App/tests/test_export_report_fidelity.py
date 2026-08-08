"""出力ファイルが画面と食い違わないこと (ver51.9 / Phase B の残り)。

■ 共通する形

画面は正しいのに、**出力ファイルだけが間違っている**。利用者は出力を見て
判断するので、画面が正しいことは救いにならない。

  B-4  一括保存用 figure を「表示名の suffix 一致」で引く
       → `WT_liver` と `liver` のように片方が他方の `_` 付き接尾辞だと
         **別サンプルの figure に書き込む**
  B-5  PPTX のラベル位置が背景ワーカーで解決できない
       → 現在の手法だけラベルが既定に戻る（他手法は正しい＝1 資料で不揃い）
  B-8  発現量の長さ検証が PPTX 側に無い
       → 数十分の背景エクスポートが最後に落ちる
  B-9  DESI で `.txt` の無いサンプルを無言で飛ばす
       → **シートが 1 つも無い「成功した」Excel**
  B-10 `_build_region_lookup` が常に dict を返す
       → H&E 未設定でも**常に空の「領域名」列**が付き、
         「ROI 未使用」と「どの ROI にも入らなかった」が区別できない
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# B-10 領域名 (ROI) 列
# ---------------------------------------------------------------------------

class TestRegionLookupDistinguishesUnused:
    """★ 「ROI 未使用」を None で表すこと。"""

    @staticmethod
    def _plot_data():
        return pd.DataFrame({
            "Sample": ["S1", "S1"],
            "SpatialX": [1.0, 2.0],
            "SpatialY": [1.0, 2.0],
        })

    # ★ ver52.3 ④: 戻り値を (lookup, 割当に失敗したサンプル名) に変えた。
    #   失敗を呼び出し側へ伝えないと「ROI 未使用」と「割当に失敗」を
    #   利用者が区別できない（後者は解析の見落としを意味する）。

    def test_no_rds_returns_none(self):
        from app.callbacks.interactive_data_export import _build_region_lookup
        lookup, failed = _build_region_lookup(self._plot_data(), None)
        assert lookup is None and failed == []

    def test_missing_columns_returns_none(self):
        from app.callbacks.interactive_data_export import _build_region_lookup
        lookup, failed = _build_region_lookup(
            pd.DataFrame({"Sample": ["S1"]}), "/x.rds")
        assert lookup is None and failed == []

    def test_no_roi_assigned_returns_none(self, tmp_path, monkeypatch):
        """★ H&E は設定済みでも ROI に 1 つも入らなければ None。

        ここが本題。従来は `{}` を返すため
        `add_region = region_lookup is not None` が True になり、
        中身が全部空の列が付いていた。
        """
        import app.callbacks.interactive_data_export as DE

        monkeypatch.setattr(DE.hp, "load_hne_sample", lambda *a, **k: {"x": 1})
        monkeypatch.setattr(
            DE.hn, "regions_from_overlay",
            lambda sub, entry: pd.Series([None] * len(sub)))

        lookup, failed = DE._build_region_lookup(
            self._plot_data(), str(tmp_path / "a.rds"))
        assert lookup is None
        assert failed == [], "割当は成功しているので失敗リストは空"

    def test_assigned_roi_still_returns_a_mapping(self, tmp_path, monkeypatch):
        """過剰修正の番人: 実際に ROI があるときは従来どおり dict。"""
        import app.callbacks.interactive_data_export as DE

        monkeypatch.setattr(DE.hp, "load_hne_sample", lambda *a, **k: {"x": 1})
        monkeypatch.setattr(
            DE.hn, "regions_from_overlay",
            lambda sub, entry: pd.Series(["腫瘍"] * len(sub)))

        got, failed = DE._build_region_lookup(
            self._plot_data(), str(tmp_path / "a.rds"))
        assert got == {("S1", 1.0, 1.0): "腫瘍", ("S1", 2.0, 2.0): "腫瘍"}
        assert failed == []

    def test_failed_assignment_is_reported_not_silent(self, tmp_path, monkeypatch):
        """★ ver52.3 ④ の本丸: 割当に失敗したサンプル名を返すこと。

        従来はログだけで飛ばしていたので、そのスライスの「領域名」が
        空欄になり、利用者には **「どの ROI にも入らなかった」
        （＝実データ上の所見）** と読めた。
        """
        import app.callbacks.interactive_data_export as DE

        def _boom(*a, **k):
            raise OSError("overlay state unreadable")

        monkeypatch.setattr(DE.hp, "load_hne_sample", _boom)
        lookup, failed = DE._build_region_lookup(
            self._plot_data(), str(tmp_path / "a.rds"))
        assert failed == ["S1"], (
            "ROI 割当に失敗したサンプルが報告されていない。"
            "領域名が空欄になった理由を利用者が知る手立てが無い")
        assert lookup is None


# ---------------------------------------------------------------------------
# B-9 DESI の無言スキップ
# ---------------------------------------------------------------------------

class TestDesiSkippedSamplesAreVisible:
    @staticmethod
    def _folder(tmp_path, with_txt):
        tmp_path.mkdir(parents=True, exist_ok=True)
        # 解析前のサンプル (.csv のみ) — list_msi_files はこれも stem として返す
        (tmp_path / "not_converted.csv").write_text("x\n", encoding="utf-8")
        if with_txt:
            (tmp_path / "ok.txt").write_text(
                "name\tx\ty\tv\nok_p1\t1.0\t1.0\t1\n", encoding="utf-8")
        return tmp_path

    def test_all_missing_raises_instead_of_empty_workbook(self, tmp_path):
        """★ 空の Excel を「成功」として返さないこと。"""
        from collections import OrderedDict
        from app.callbacks.interactive_data_export import _export_desi

        folder = self._folder(tmp_path / "d", with_txt=False)
        with pytest.raises(ValueError, match="txt"):
            _export_desi(str(folder), OrderedDict({"Harmony": {}}))

    def test_partial_skip_is_recorded_in_the_workbook(self, tmp_path):
        """★ 一部だけ落ちたとき、理由がファイルの中に残ること。"""
        openpyxl = pytest.importorskip("openpyxl")
        import io
        from collections import OrderedDict
        from app.callbacks.interactive_data_export import _export_desi

        folder = self._folder(tmp_path / "d", with_txt=True)
        data, _ = _export_desi(str(folder), OrderedDict({"Harmony": {}}))
        wb = openpyxl.load_workbook(io.BytesIO(data))

        assert "Skipped" in wb.sheetnames, (
            f"飛ばしたサンプルが資料に残っていない: {wb.sheetnames}")
        rows = list(wb["Skipped"].iter_rows(min_row=2, values_only=True))
        assert any("not_converted" in str(r[0]) for r in rows), rows

    def test_no_skipped_sheet_when_nothing_was_skipped(self, tmp_path):
        """過剰修正の番人: 全部出せたなら余計なシートを足さない。"""
        openpyxl = pytest.importorskip("openpyxl")
        import io
        from collections import OrderedDict
        from app.callbacks.interactive_data_export import _export_desi

        folder = tmp_path / "d"
        folder.mkdir()
        (folder / "ok.txt").write_text(
            "name\tx\ty\tv\nok_p1\t1.0\t1.0\t1\n", encoding="utf-8")

        data, _ = _export_desi(str(folder), OrderedDict({"Harmony": {}}))
        wb = openpyxl.load_workbook(io.BytesIO(data))
        assert "Skipped" not in wb.sheetnames, wb.sheetnames


# ---------------------------------------------------------------------------
# B-8 発現量の長さ検証 (PPTX)
# ---------------------------------------------------------------------------

class TestPptxFeatureLengthGuard:
    """★ 数十分の背景エクスポートを最後に落とさない。

    画面側 (`interactive_deg.py`) には ver51.6 で入れた番人が
    PPTX 側にだけ無かった。
    """

    def test_mismatched_length_returns_none_instead_of_raising(self, monkeypatch):
        import app.callbacks.interactive_pptx as P

        df = pd.DataFrame({
            "CellID": ["a", "b", "c"],
            "Sample": ["S1"] * 3,
            "SpatialX": [0.0, 1.0, 2.0],
            "SpatialY": [0.0, 0.0, 0.0],
        })
        # R フォールバックがヘッダ 1 行ぶん長い Series を返す状況
        monkeypatch.setattr(P._bridge, "get_feature_expression_fast",
                            lambda *a, **k: None)
        monkeypatch.setattr(P._bridge, "get_feature_expression",
                            lambda *a, **k: pd.Series([1.0, 2.0, 3.0, 4.0]))

        got = P._build_feature_plot_fig(df, "m/z 100", None, "/x.rds")
        assert got is None, "長さ不一致を素通りさせている（代入で例外になる）"

    def test_matching_length_still_builds(self, monkeypatch):
        """過剰修正の番人: 正しい長さなら従来どおり描く。"""
        import app.callbacks.interactive_pptx as P

        df = pd.DataFrame({
            "CellID": ["a", "b", "c"],
            "Sample": ["S1"] * 3,
            "SpatialX": [0.0, 1.0, 2.0],
            "SpatialY": [0.0, 0.0, 0.0],
        })
        monkeypatch.setattr(P._bridge, "get_feature_expression_fast",
                            lambda *a, **k: None)
        monkeypatch.setattr(P._bridge, "get_feature_expression",
                            lambda *a, **k: pd.Series([1.0, 2.0, 3.0]))

        assert P._build_feature_plot_fig(df, "m/z 100", None, "/x.rds") is not None


# ---------------------------------------------------------------------------
# B-5 PPTX のラベル位置 (背景ワーカー)
# ---------------------------------------------------------------------------

class TestPptxResolvesLabelPositionsExplicitly:
    """★ `background=True` の fork 先では `_interactive_data` が空。

    引数なしで `_get_merged_label_positions` を呼ぶと JSON の解決先が
    None になり、**現在の手法だけ**ラベル位置が既定へ戻る。
    他手法は `_load_label_positions_util(rds, method)` で正しく出るので、
    1 つの資料の中でラベルの付き方が不揃いになる。
    """

    def test_called_with_rds_path_and_method(self, monkeypatch, tmp_path):
        pytest.importorskip("pptx")
        import base64
        import sys
        import types

        import app.callbacks.interactive_pptx as P

        if "kaleido" not in sys.modules:
            monkeypatch.setitem(sys.modules, "kaleido", types.ModuleType("kaleido"))

        seen = {}

        def _spy(accumulated=None, rds_path=None, method=None):
            seen["rds_path"] = rds_path
            seen["method"] = method
            return {}

        monkeypatch.setattr(P, "_get_merged_label_positions", _spy)
        monkeypatch.setattr(P, "_build_pptx", lambda *a, **k: b"PPTX")
        monkeypatch.setattr(
            P, "_fig_to_png_bytes",
            lambda *a, **k: base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
                "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="))
        monkeypatch.setattr(P._bridge, "ensure_expression_matrix",
                            lambda *a, **k: None)

        rds = tmp_path / "RDS_Files" / "seu_harmony.rds"
        rds.parent.mkdir(parents=True)
        rds.write_bytes(b"x")

        P.cb_export_report(
            lambda *_: None, 1, {"data": [], "layout": {}}, None, str(rds),
            [], None, None, None, None, None, {}, None, None, 5, None, None,
            None, None, "Harmony", [], False, {}, None,
        )

        assert seen.get("rds_path") == str(rds), (
            "ラベル位置を rds_path 無しで解決している。"
            "background=True の fork 先では _interactive_data が空なので"
            f"既定位置に戻る: {seen}")
        assert seen.get("method") == "Harmony", seen


# ---------------------------------------------------------------------------
# B-4 一括保存 figure の対応付け
# ---------------------------------------------------------------------------

class TestFeatureFigureMatchingIsPositional:
    """★ 表示名の suffix 一致をやめること。

    `Feature_{file_label}_{display_s}` は file_label / display_s の
    どちらも `_` を含みうるため、`endswith(f"_{display_s}")` では境界が
    決まらない。`liver` を探すと `WT_liver` にも当たる。
    """

    def test_suffix_collision_is_reproducible(self):
        """欠陥そのものの再現（前提の固定）。"""
        stored = ["Feature_mz100_WT_liver", "Feature_mz100_liver"]
        hit = next((k for k in stored if k.endswith("_liver")), None)
        assert hit == "Feature_mz100_WT_liver", (
            "suffix 一致が曖昧でないなら、この修正の前提が変わった")

    def test_source_no_longer_uses_endswith_for_lookup(self):
        """位置対応が入り、名前照合は件数不一致時の退避に限ること。"""
        import ast

        src = (Path(__file__).resolve().parent.parent / "app" / "callbacks"
               / "interactive_deg.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "patch_feature_intensity")

        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        assert "stored_index_of" in names, (
            "位置での対応付けが入っていない。表示名の接尾辞一致のままだと"
            "別サンプルの figure に書き込む")


# ---------------------------------------------------------------------------
# B-6 クラスタ統計の表示名
# ---------------------------------------------------------------------------

class TestClusterStatsUseDisplayNames:
    """★ 表は生 ID・見出しは改名後、という不整合を無くす。"""

    def test_stats_rows_are_renamed(self, monkeypatch, tmp_path):
        pytest.importorskip("pptx")
        import base64
        import sys
        import types

        import app.callbacks.interactive_pptx as P
        from app.utils.label_persistence import save_cluster_name_map

        if "kaleido" not in sys.modules:
            monkeypatch.setitem(sys.modules, "kaleido", types.ModuleType("kaleido"))

        rds_dir = tmp_path / "RDS_Files"
        rds_dir.mkdir(parents=True)
        rds_map = {}
        for m in ("Harmony", "RPCA"):
            p = rds_dir / f"seu_{m.lower()}.rds"
            p.write_bytes(b"x")
            rds_map[m] = str(p)
        save_cluster_name_map(rds_map["RPCA"], "RPCA", {"3": "間質"})

        df = pd.DataFrame([
            {"CellID": f"c{i}", "Sample": "S1", "Cluster": c,
             "UMAP_1": float(i), "UMAP_2": float(i), "Annotation": "S1"}
            for i, c in enumerate(["0", "3", "3", "0"])])

        seen = []
        monkeypatch.setattr(
            P._bridge, "extract_data",
            lambda r: {"plot_data": df.copy(),
                       "meta": {"n_cells": 4, "n_clusters": 2},
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
            seen.append(a[3])          # cluster_stats_data
            return k.get("progress_offset", 0)

        monkeypatch.setattr(P, "_build_pptx", _fake_build)

        P.cb_export_report(
            lambda *_: None, 1, {"data": [], "layout": {}}, None,
            rds_map["Harmony"], [], None, None, None, None, None, {},
            None, None, 5, None, None, rds_map, None, "Harmony",
            ["Harmony", "RPCA"], False, {}, None,
        )

        rpca_stats = seen[1]
        labels = {r["Cluster"] for r in rpca_stats}
        assert "間質" in labels, (
            f"統計表が生のクラスタ ID のまま。見出しは改名後なので対応が取れない: {labels}")
        assert "0" in labels, f"改名していないクラスタは ID のままであること: {labels}"


# ---------------------------------------------------------------------------
# B-7 マージ表示 (Cluster_merged)
# ---------------------------------------------------------------------------

class TestMergedClusterView:
    """★ 画面が「マージ統合」なら資料もそれを出すこと。

    画面は `Cluster_merged` / `UMAP_*_merged` で描くのに、PPTX には
    `Cluster_merged` への参照が 1 つも無かった。マージ表示のまま出力すると
    **別のクラスタリングの資料**が出る。見出しも凡例も同じ形なので、
    受け取った人には区別が付かない。
    """

    @staticmethod
    def _df():
        return pd.DataFrame({
            "CellID": ["a", "b", "c", "d"],
            "Sample": ["S1"] * 4,
            "Cluster": ["0", "0", "1", "1"],
            "Cluster_merged": ["0-a", "0-b", "1", "1"],
            "UMAP_1": [0.0, 1.0, 2.0, 3.0],
            "UMAP_2": [0.0, 1.0, 2.0, 3.0],
            "UMAP_1_merged": [10.0, 11.0, 12.0, 13.0],
            "UMAP_2_merged": [10.0, 11.0, 12.0, 13.0],
        })

    def test_merged_toggle_swaps_the_clusters(self):
        from app.callbacks.interactive_pptx import _apply_merge_view

        out, colors, merged = _apply_merge_view(
            self._df(), {"merge_toggle": "merged", "merge_color_mode": "shade"},
            {})
        assert merged is True
        assert sorted(out["Cluster"].unique()) == ["0-a", "0-b", "1"], \
            out["Cluster"].unique()
        assert list(out["UMAP_1"]) == [10.0, 11.0, 12.0, 13.0]
        assert colors, "マージ用のカラーマップが作られていない"

    def test_original_toggle_is_untouched(self):
        """★ 過剰修正の番人: 「元のクラスタ」表示は従来のまま。"""
        from app.callbacks.interactive_pptx import _apply_merge_view

        df = self._df()
        out, colors, merged = _apply_merge_view(
            df, {"merge_toggle": "original"}, {"0": "#ff0000"})
        assert merged is False
        assert out is df
        assert colors == {"0": "#ff0000"}

    def test_missing_merged_columns_fall_back(self):
        """マージ列を持たないプロジェクトで壊れないこと。"""
        from app.callbacks.interactive_pptx import _apply_merge_view

        df = self._df().drop(columns=["Cluster_merged"])
        out, _c, merged = _apply_merge_view(df, {"merge_toggle": "merged"}, {})
        assert merged is False
        assert out is df

    def test_display_settings_carries_the_toggle(self):
        """条件記録 (`umap_view`) から読めること。"""
        from app.callbacks.interactive_pptx import _display_settings

        s = _display_settings({"interactive": {"umap_view": {
            "merge_toggle": "merged", "merge_color_mode": "independent"}}})
        assert s["merge_toggle"] == "merged"
        assert s["merge_color_mode"] == "independent"

    def test_export_uses_the_merged_view(self, monkeypatch, tmp_path):
        """★ エクスポート経路が実際に差し替えること。"""
        pytest.importorskip("pptx")
        import base64
        import sys
        import types

        import app.callbacks.interactive_pptx as P

        if "kaleido" not in sys.modules:
            monkeypatch.setitem(sys.modules, "kaleido", types.ModuleType("kaleido"))

        rds_dir = tmp_path / "RDS_Files"
        rds_dir.mkdir(parents=True)
        rds = rds_dir / "seu_harmony.rds"
        rds.write_bytes(b"x")
        # 条件記録に「マージ統合」を入れる
        from app.utils.label_persistence import save_interactive_settings
        save_interactive_settings(
            "umap_view", {"merge_toggle": "merged", "merge_color_mode": "shade"},
            str(rds))

        seen = []
        monkeypatch.setattr(
            P._bridge, "extract_data",
            lambda r: {"plot_data": self._df(),
                       "meta": {"n_cells": 4, "n_clusters": 3},
                       "cache_dir": None})
        monkeypatch.setattr(P._bridge, "ensure_expression_matrix",
                            lambda *a, **k: None)
        monkeypatch.setattr(P, "_load_deg_results", lambda *a, **k: None)
        monkeypatch.setattr(
            P, "_fig_to_png_bytes",
            lambda *a, **k: base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
                "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="))
        monkeypatch.setattr(
            P, "_build_pptx",
            lambda *a, **k: (seen.append(k.get("df")),
                             k.get("progress_offset", 0))[1])

        P.cb_export_report(
            lambda *_: None, 1, {"data": [], "layout": {}}, None, str(rds),
            [], None, None, None, None, None, {}, None, None, 5, None, None,
            {"Harmony": str(rds)}, None, "Harmony", ["Harmony"], False, {}, None,
        )

        assert seen and seen[0] is not None
        assert sorted(seen[0]["Cluster"].unique()) == ["0-a", "0-b", "1"], (
            "資料が元のクラスタリングのまま。画面はマージ統合を表示している: "
            f"{sorted(seen[0]['Cluster'].unique())}")
