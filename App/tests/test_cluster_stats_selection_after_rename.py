"""クラスタを改名しても統計表示が壊れないことの番人。

★ ver56.5 / デバッグ総点検 §4.2 で確定した不具合 (C04-2):

  クラスタに「Tumor」などの名前を付けたあと、「クラスタ統計」の表でその行を選ぶと、
  右側の説明が **「Tumor: 0 pixels (0.0%)」** になり、サンプルごとの内訳も空になる。
  同じ画面の表には「100 pixels / 33.3%」と正しい数字が出ているため、
  どちらが本当なのか分からなくなる。

■ 原因

  - 表を作る `update_cluster_stats` は "Cluster" 列に **表示名**を入れる
    (`cluster_display_name()` の結果 = 改名済みならユーザーが付けた名前)
  - 行選択を受ける `update_cluster_info` は `table_data[i]["Cluster"]` を
    そのまま `df["Cluster"]`(= **生のクラスタ番号**)と比較する

  改名すると両者は必ず食い違い、`mask` が全 False になって n=0 になる。
  改名していないクラスタでは表示名 = 生 ID なので偶然一致し、症状が出ない。
"""
import pandas as pd
import pytest


@pytest.fixture
def cluster_mod(monkeypatch):
    import app.callbacks.interactive_cluster as module

    df = pd.DataFrame({
        "Cluster": ["0", "0", "0", "1", "1", "2"],
        "Sample": ["S1", "S1", "S2", "S1", "S2", "S2"],
    })
    monkeypatch.setitem(module._interactive_data, "plot_data", df)
    monkeypatch.setitem(module._interactive_data, "meta",
                        {"n_cells": 6, "n_clusters": 3, "samples": ["S1", "S2"]})
    monkeypatch.setattr(
        "app.callbacks.interactive_callbacks._set_active_key", lambda *a, **k: None)
    return module


NAME_MAP = {"0": "Tumor", "1": "Stroma"}


class TestSelectionAfterRename:
    """★ 本丸: 改名済みクラスタの行を選んでも正しい件数が出ること。"""

    def test_renamed_cluster_shows_real_pixel_count(self, cluster_mod):
        table = cluster_mod.update_cluster_stats("/rds", NAME_MAP)
        # 表には表示名が出る（画面の見た目は従来どおり）
        assert table[0]["Cluster"] == "Tumor"
        assert table[0]["Pixels"] == 3

        info = cluster_mod.update_cluster_info(
            [0], None, table, NAME_MAP, "/rds")

        assert "0 pixels" not in info, (
            f"改名済みクラスタの選択で 0 pixels と表示されている: {info!r}")
        assert "3 pixels" in info, f"実際の件数が出ていない: {info!r}"
        assert "Tumor" in info, "表示名が出ていない"

    def test_renamed_cluster_shows_per_sample_breakdown(self, cluster_mod):
        """サンプルごとの内訳も空にならないこと。"""
        table = cluster_mod.update_cluster_stats("/rds", NAME_MAP)
        info = cluster_mod.update_cluster_info([0], None, table, NAME_MAP, "/rds")
        assert "S1" in info and "S2" in info, (
            f"サンプル内訳が空になっている: {info!r}")

    def test_unrenamed_cluster_still_works(self, cluster_mod):
        """改名していないクラスタ（従来から動いていた経路）も壊さないこと。"""
        table = cluster_mod.update_cluster_stats("/rds", NAME_MAP)
        row = next(i for i, r in enumerate(table) if r["Cluster"] == "2")
        info = cluster_mod.update_cluster_info([row], None, table, NAME_MAP, "/rds")
        assert "1 pixels" in info

    def test_no_name_map_at_all(self, cluster_mod):
        """名前マップが無い場合も従来どおり動くこと。"""
        table = cluster_mod.update_cluster_stats("/rds", None)
        info = cluster_mod.update_cluster_info([0], None, table, None, "/rds")
        assert "3 pixels" in info

    def test_highlight_path_still_works(self, cluster_mod):
        """UMAP のハイライト選択からの経路（生 ID が来る）も動くこと。"""
        table = cluster_mod.update_cluster_stats("/rds", NAME_MAP)
        info = cluster_mod.update_cluster_info(None, ["1"], table, NAME_MAP, "/rds")
        assert "2 pixels" in info
        assert "Stroma" in info

    def test_nothing_selected_shows_summary(self, cluster_mod):
        """未選択なら全体サマリーを出すこと。"""
        table = cluster_mod.update_cluster_stats("/rds", NAME_MAP)
        info = cluster_mod.update_cluster_info(None, None, table, NAME_MAP, "/rds")
        assert "Total cells" in info


class TestRenameCollisionSafety:
    """改名によって別クラスタの生 ID と衝突しても取り違えないこと。"""

    def test_display_name_equal_to_another_raw_id_is_not_confused(self, cluster_mod):
        """クラスタ 0 を "2" という名前にしても、クラスタ 2 と混同しないこと。

        表示名からの逆引きだけに頼ると、この場合に取り違える。
        """
        tricky = {"0": "2"}
        table = cluster_mod.update_cluster_stats("/rds", tricky)
        row0 = next(i for i, r in enumerate(table) if r.get("_cluster_id") == "0")
        info = cluster_mod.update_cluster_info([row0], None, table, tricky, "/rds")
        # クラスタ 0 は 3 pixels、クラスタ 2 は 1 pixel
        assert "3 pixels" in info, (
            f"表示名 '2' に引きずられてクラスタ 2 を見ている: {info!r}")
