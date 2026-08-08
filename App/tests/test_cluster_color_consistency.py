"""クラスタ色がパネル間で一致すること (ver51.9 / C-2, C-8)。

■ C-2: クラスタ除外で統合 UMAP だけ色がずれる

色は「存在するクラスタを並べたときの添字」で決まる
(`color_utils.get_cluster_color_map`)。ところが

  - 統合 UMAP (`interactive_umap._build_umap_integrated_fig`) は
    **除外してから** 色マップを作る
  - サンプル別 UMAP / Spatial / 凡例 / Lite / PPTX は **除外前** に作る

ので、クラスタを 1 つ除外した瞬間、それより後ろのクラスタの色が
統合 UMAP でだけ 1 つずつずれる。**各パネルは内部的に整合している**ので
見た目では気づけない。「Spatial の緑」と「UMAP の緑」が別のクラスタになる。

■ C-8: shade モードでサブクラスタが 1 つだけだと親と同じ色

`factor = 1.0` になり `adjust_color_lightness(base, 1.0)` は元の色。
親クラスタ `3` とサブクラスタ `3-a` が両方あると **同じ色**で描かれる。
凡例は 2 行に見えるのに図では区別できない。
"""

import pytest

from app.utils.color_utils import (
    get_cluster_color_map,
    get_merged_cluster_color_map,
)


# ---------------------------------------------------------------------------
# C-2 除外と色の対応
# ---------------------------------------------------------------------------

ALL_CLUSTERS = ["0", "1", "2", "3", "4"]


class TestExclusionDoesNotShiftColours:
    """★ 除外しても、残ったクラスタの色が変わらないこと。"""

    def test_color_map_is_stable_under_exclusion(self):
        """前提の固定: 色は並び順の添字で決まるので、母集団が変われば変わる。"""
        full = get_cluster_color_map(ALL_CLUSTERS)
        without_1 = get_cluster_color_map(["0", "2", "3", "4"])
        assert full["2"] != without_1["2"], (
            "母集団を変えても色が動かないなら、この修正の前提が変わった")

    def test_integrated_umap_uses_the_unfiltered_palette(self):
        """★ 本題。統合 UMAP が除外**前**の母集団で色を決めること。"""
        pytest.importorskip("dash")
        import pandas as pd
        from app.callbacks.interactive_umap import _build_umap_integrated_fig

        df = pd.DataFrame([
            {"CellID": f"c{i}", "Sample": "S1", "Cluster": c,
             "UMAP_1": float(i), "UMAP_2": float(i)}
            for i, c in enumerate(ALL_CLUSTERS * 2)])

        expected = get_cluster_color_map(df["Cluster"])   # 他パネルと同じ作り方

        fig = _build_umap_integrated_fig(
            df, "Cluster", None, True, False, exclude_clusters=["1"])

        got = {}
        for tr in fig.data:
            name = tr.name
            color = getattr(getattr(tr, "marker", None), "color", None)
            if name in ALL_CLUSTERS and isinstance(color, str):
                got[name] = color
        assert got, f"クラスタ別トレースが見つからない: {[t.name for t in fig.data]}"

        mismatched = {k: (v, expected[k]) for k, v in got.items()
                      if v.lower() != expected[k].lower()}
        assert not mismatched, (
            "除外後の母集団で色を決めているため、他パネルと色がずれている "
            f"(クラスタ: 実際 vs 期待) {mismatched}")

    def test_without_exclusion_nothing_changes(self):
        """★ 過剰修正の番人: 除外なしの色は従来どおり。"""
        pytest.importorskip("dash")
        import pandas as pd
        from app.callbacks.interactive_umap import _build_umap_integrated_fig

        df = pd.DataFrame([
            {"CellID": f"c{i}", "Sample": "S1", "Cluster": c,
             "UMAP_1": float(i), "UMAP_2": float(i)}
            for i, c in enumerate(ALL_CLUSTERS * 2)])
        expected = get_cluster_color_map(df["Cluster"])

        fig = _build_umap_integrated_fig(df, "Cluster", None, True, False)
        for tr in fig.data:
            color = getattr(getattr(tr, "marker", None), "color", None)
            if tr.name in ALL_CLUSTERS and isinstance(color, str):
                assert color.lower() == expected[tr.name].lower(), tr.name

    def test_excluded_cluster_is_still_removed(self):
        """★ 過剰修正の番人: 除外そのものは効いていること。"""
        pytest.importorskip("dash")
        import pandas as pd
        from app.callbacks.interactive_umap import _build_umap_integrated_fig

        df = pd.DataFrame([
            {"CellID": f"c{i}", "Sample": "S1", "Cluster": c,
             "UMAP_1": float(i), "UMAP_2": float(i)}
            for i, c in enumerate(ALL_CLUSTERS * 2)])

        fig = _build_umap_integrated_fig(
            df, "Cluster", None, True, False, exclude_clusters=["1"])
        names = {tr.name for tr in fig.data}
        assert "1" not in names, names


# ---------------------------------------------------------------------------
# C-8 shade モードの親子同色
# ---------------------------------------------------------------------------

class TestShadeModeDistinguishesParentAndChild:
    def test_single_subcluster_differs_from_its_parent(self):
        """★ 親 3 と唯一のサブクラスタ 3-a が同じ色にならないこと。"""
        cmap = get_merged_cluster_color_map(["0", "3", "3-a"], mode="shade")
        # ★ 大文字小文字で比較すると **見た目が同じでも通ってしまう**
        #   (プリセットは "#4DBBD5"、adjust_color_lightness の出力は "#4dbbd5")。
        assert cmap["3"].lower() != cmap["3-a"].lower(), (
            f"親と子が同じ色 ({cmap['3']})。凡例は 2 行あるのに図で区別できない")

    def test_multiple_subclusters_still_spread(self):
        """過剰修正の番人: 複数サブクラスタは従来どおり濃淡で展開される。"""
        cmap = get_merged_cluster_color_map(["3-a", "3-b", "3-c"], mode="shade")
        assert len({cmap[k].lower() for k in ("3-a", "3-b", "3-c")}) == 3, cmap

    def test_lone_subcluster_without_parent_keeps_the_base_colour(self):
        """親がリストに居なければ、従来どおり親の色でよい（衝突しない）。"""
        cmap = get_merged_cluster_color_map(["0", "3-a"], mode="shade")
        assert cmap["3-a"], cmap

    def test_independent_mode_untouched(self):
        cmap = get_merged_cluster_color_map(["3", "3-a"], mode="independent")
        assert cmap["3"].lower() != cmap["3-a"].lower()

    def test_custom_colors_still_win(self):
        cmap = get_merged_cluster_color_map(
            ["3", "3-a"], mode="shade", custom_colors={"3-a": "#123456"})
        assert cmap["3-a"] == "#123456"
