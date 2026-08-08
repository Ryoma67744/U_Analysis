"""PPTX が手法ごとの改名を使うこと (ver51.9 / B-1)。

■ 何が起きていたか

クラスタの改名は**ディスク上、手法ごとに独立**している
(`label_persistence.cluster_name_map_key(method)` → `cluster_name_map::RPCA` 等)。
画面も手法を切り替えるたび `load_saved_cluster_name_map` でその手法の分を読み直す。

ところが PPTX エクスポートは `cluster_name_map_store` を State から **1 回だけ**読み、
その 1 つを**全手法のスライドへ渡していた**。`cluster_name_map_store` に入っているのは
**現在表示中の手法の分**なので、

    Harmony でクラスタ 3 を「腫瘍」と改名 → RPCA の無関係なクラスタ 3 も「腫瘍」

になる。手法比較のための資料で、比較対象のラベルが汚染される。
クラスタ ID は手法間で何の関係も無いため、**中身は完全に別物**。
資料だけを見る人には気づく手段が無い。

★ 正解は同じリポジトリの `interactive_data_export.py:380` にある
  (`load_cluster_name_map(rds_path, method_name)`)。ラベル位置は PPTX でも
  既に手法別に読み直しているので (`_load_label_positions_util(rds, method)`)、
  改名だけが取り残されていた。

■ 何を固定するか

`cb_export_report` を実際に走らせ、**各手法の図に渡された `cluster_name_map` が
その手法の保存分であること**を見る。画像描画 (kaleido) だけ差し替える。
"""

import base64
import json

import pandas as pd
import pytest

pytest.importorskip("dash")
pytest.importorskip("pptx")
# kaleido は実描画にしか要らない（本テストは _fig_to_png_bytes を差し替える）が、
# `cb_export_report` の冒頭で import チェックがあるためダミーを入れる。
pytest.importorskip("plotly")

from app.utils.label_persistence import save_cluster_name_map

# 1x1 透明 PNG（python-pptx が実画像を要求するため）
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _plot_df():
    """2 サンプル × 3 クラスタの最小 plot_data。"""
    rows = []
    for s in ("S1", "S2"):
        for c in ("0", "1", "3"):
            for i in range(3):
                rows.append({
                    "CellID": f"{s}_{c}_{i}",
                    "Sample": s, "Cluster": c,
                    "UMAP_1": float(i), "UMAP_2": float(i) + 1,
                    "SpatialX": float(i), "SpatialY": float(i),
                    "Annotation": s,
                })
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def _stub_kaleido(monkeypatch):
    """`cb_export_report` 冒頭の `import kaleido` を通す。

    本テストは画像描画そのものを差し替えるので kaleido は使わないが、
    未インストール環境では最初のチェックで早期 return してしまう。
    """
    import sys
    import types
    if "kaleido" not in sys.modules:
        monkeypatch.setitem(sys.modules, "kaleido", types.ModuleType("kaleido"))


@pytest.fixture
def project(tmp_path):
    """Harmony / RPCA の 2 手法。クラスタ 3 の改名が手法ごとに違う。"""
    rds_dir = tmp_path / "RDS_Files"
    rds_dir.mkdir(parents=True)
    rds_map = {}
    for m in ("Harmony", "RPCA"):
        p = rds_dir / f"seu_{m.lower()}.rds"
        p.write_bytes(b"x")
        rds_map[m] = str(p)

    # 改名は「同じ設定ファイル内の手法別キー」に入る
    save_cluster_name_map(rds_map["Harmony"], "Harmony", {"3": "腫瘍"})
    save_cluster_name_map(rds_map["RPCA"], "RPCA", {"3": "間質"})
    return rds_map


@pytest.fixture
def captured(monkeypatch, project):
    """cb_export_report を走らせ、図に渡された cluster_name_map を記録する。"""
    import app.callbacks.interactive_pptx as P

    seen = {"umap": [], "legend": [], "build_pptx": []}
    df = _plot_df()

    monkeypatch.setattr(
        P._bridge, "extract_data",
        lambda rds: {"plot_data": df.copy(),
                     "meta": {"n_cells": len(df), "n_clusters": 3},
                     "cache_dir": None})
    monkeypatch.setattr(P._bridge, "ensure_expression_matrix",
                        lambda *a, **k: None)
    monkeypatch.setattr(P, "_load_deg_results", lambda *a, **k: None)
    monkeypatch.setattr(P, "_fig_to_png_bytes", lambda *a, **k: _PNG_1X1)

    real_umap = P._build_umap_integrated_fig

    def _umap(*a, **k):
        seen["umap"].append(k.get("cluster_name_map"))
        return real_umap(*a, **k)

    real_legend = P._build_cluster_legend_fig

    def _legend(*a, **k):
        seen["legend"].append(k.get("cluster_name_map"))
        return real_legend(*a, **k)

    def _build_pptx(*a, **k):
        seen["build_pptx"].append(k.get("cluster_name_map"))
        return k.get("progress_offset", 0)

    monkeypatch.setattr(P, "_build_umap_integrated_fig", _umap)
    monkeypatch.setattr(P, "_build_cluster_legend_fig", _legend)
    monkeypatch.setattr(P, "_build_pptx", _build_pptx)

    P.cb_export_report(
        lambda *_: None,                 # set_progress
        1,                               # n_clicks
        {"data": [], "layout": {}},      # umap_fig
        None,                            # spatial_fig
        project["Harmony"],              # rds_path (= 表示中)
        [], None, None,                  # cluster_stats, sub_options, sub_value
        None, None, None,                # volcano, heatmap, deg_data
        {},                              # custom_colors
        None, None, 5, None, None,       # rotation, name_map, top_n, cache_dir, mrm
        project,                         # rds_map
        None,                            # result_folder
        "Harmony",                       # current_method (表示中)
        ["Harmony", "RPCA"],             # export_method_selection
        False,                           # include_deg
        {"3": "腫瘍"},                    # cluster_name_map_store (= Harmony の分)
        None,                            # accumulated_positions
    )
    return seen


class TestEachMethodGetsItsOwnNames:
    def test_build_pptx_receives_per_method_maps(self, captured):
        """★ 各手法の本体スライドに、その手法の改名が渡ること。"""
        maps = [m for m in captured["build_pptx"] if m is not None]
        assert len(maps) == 2, f"手法 2 つぶん呼ばれていない: {captured['build_pptx']}"
        assert maps[0].get("3") == "腫瘍", maps[0]
        assert maps[1].get("3") == "間質", (
            f"RPCA のスライドに Harmony の改名が渡っている: {maps[1]}")

    def test_no_method_sees_another_methods_rename(self, captured):
        """★ どの図にも「自分以外の手法の改名」が混ざらないこと。

        比較セクション・凡例・本体を区別せず、渡された値の集合で見る。
        片方だけ直すと「比較スライドだけ汚染」のような半端な状態になる。
        """
        all_maps = [m for m in
                    captured["umap"] + captured["legend"] + captured["build_pptx"]
                    if m]
        names = {m.get("3") for m in all_maps}
        assert names == {"腫瘍", "間質"}, (
            f"手法をまたいで改名が漏れている (期待 2 種): {names}")

    def test_current_method_uses_the_in_memory_value(self, tmp_path, monkeypatch,
                                                     project, captured):
        """★ 過剰修正の番人: 表示中の手法は**画面の値**を使うこと。

        全手法をディスクから読むように直すと、まだ保存されていない
        改名 (Store にはあるがファイルに無い) が資料に出なくなる。
        """
        maps = [m for m in captured["build_pptx"] if m is not None]
        assert maps[0].get("3") == "腫瘍"


class TestUnsavedRenameOfCurrentMethodSurvives:
    """表示中の手法の未保存の改名が反映されること（上の番人の実効版）。"""

    def test_store_wins_over_disk_for_current_method(self, monkeypatch, project):
        import app.callbacks.interactive_pptx as P

        seen = []
        df = _plot_df()
        monkeypatch.setattr(
            P._bridge, "extract_data",
            lambda rds: {"plot_data": df.copy(),
                         "meta": {"n_cells": len(df), "n_clusters": 3},
                         "cache_dir": None})
        monkeypatch.setattr(P._bridge, "ensure_expression_matrix",
                            lambda *a, **k: None)
        monkeypatch.setattr(P, "_load_deg_results", lambda *a, **k: None)
        monkeypatch.setattr(P, "_fig_to_png_bytes", lambda *a, **k: _PNG_1X1)
        monkeypatch.setattr(
            P, "_build_pptx",
            lambda *a, **k: (seen.append(k.get("cluster_name_map")),
                             k.get("progress_offset", 0))[1])

        P.cb_export_report(
            lambda *_: None, 1, {"data": [], "layout": {}}, None,
            project["Harmony"], [], None, None, None, None, None, {},
            None, None, 5, None, None, project, None, "Harmony",
            ["Harmony", "RPCA"], False,
            {"3": "まだ保存していない名前"},          # Store だけにある
            None,
        )
        assert seen and seen[0].get("3") == "まだ保存していない名前", seen
        assert seen[1].get("3") == "間質", seen


class TestSharedColorsAreNotBrokenUp:
    """★ 色は手法別ではない、という設計の固定。

    B-1 の当初の想定では `custom_colors` も手法別に読み直す予定だったが、
    調べたところ **色は仕様として手法共有**だった:
      - `update_custom_color_map` は `_save_interactive_settings("custom_color_map", ...)`
        と **手法サフィックス無しのキー**で保存する
      - 全手法の RDS は同じ `RDS_Files/` に置かれ、`interactive_settings.json` は
        **1 ファイル共有**（だからこそ改名側に `cluster_name_map::<method>` がある）
    つまり画面も全手法で同じ色を使う。PPTX が State の値を全手法へ渡すのは正しい。

    ここでは「うっかり手法別にしてしまわない」ことを固定する。
    """

    def test_color_map_key_has_no_method_suffix(self):
        from app.utils import label_persistence as LP
        assert not hasattr(LP, "custom_color_map_key"), (
            "色が手法別キーになった。もしそれが意図した変更なら、"
            "PPTX / 画面 / Lite の全経路を同時に揃えること")

    def test_rename_key_is_method_scoped(self):
        from app.utils.label_persistence import cluster_name_map_key
        assert cluster_name_map_key("RPCA") != cluster_name_map_key("Harmony")
        assert cluster_name_map_key(None) == "cluster_name_map"   # 旧形式互換
