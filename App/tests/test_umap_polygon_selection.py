"""ver27.0: UMAP ポリゴン選択の幾何ロジック回帰テスト。

interactive_loupe.umap_polygon_commit は Dash コールバックだが、その中核は
hne_overlay.points_in_polygon を UMAP_1/UMAP_2 に適用して内包セルの CellID を
取り出す純ロジック。ここでは同じ式を直接検証する（コールバック本体は Dash 依存）。
"""

import numpy as np
import pandas as pd

from app.services.hne_overlay import points_in_polygon


def _select(df, polygon, xcol="UMAP_1", ycol="UMAP_2"):
    """umap_polygon_commit と同じ内包判定 → CellID 抽出。"""
    xs = pd.to_numeric(df[xcol], errors="coerce").to_numpy(dtype=float)
    ys = pd.to_numeric(df[ycol], errors="coerce").to_numpy(dtype=float)
    inside = points_in_polygon(xs, ys, polygon) & ~(np.isnan(xs) | np.isnan(ys))
    return df.loc[inside, "CellID"].astype(str).tolist()


def _df():
    return pd.DataFrame({
        "CellID": ["a", "b", "c", "d"],
        "UMAP_1": [0.5, 5.0, 0.5, np.nan],
        "UMAP_2": [0.5, 5.0, 0.4, 0.5],
        "Cluster": ["1", "2", "1", "1"],
        "Sample": ["s1", "s1", "s2", "s2"],
    })


def test_polygon_selects_interior_points():
    # 単位正方形 (0,0)-(1,1) の内側にあるのは a, c のみ（b は外、d は NaN）
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]
    assert sorted(_select(_df(), square)) == ["a", "c"]


def test_polygon_excludes_nan_coords():
    # NaN 座標の d は常に除外される
    big = [[-10, -10], [10, -10], [10, 10], [-10, 10]]
    ids = _select(_df(), big)
    assert "d" not in ids
    assert sorted(ids) == ["a", "b", "c"]


def test_degenerate_polygon_selects_nothing():
    # 2頂点（線分）は points_in_polygon が全 False → 選択なし
    assert _select(_df(), [[0, 0], [1, 1]]) == []


def test_merged_columns_path():
    # マージ表示時は *_merged 列で判定する経路の確認
    df = _df()
    df["UMAP_1_merged"] = [9.0, 0.5, 9.0, 0.5]
    df["UMAP_2_merged"] = [9.0, 0.5, 9.0, 0.5]
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]
    # merged 座標で内側にあるのは b, d。ただし d は元 UMAP_1 が NaN でも
    # merged 列は有効なので選択される（commit は表示中の列で NaN 判定する）。
    ids = _select(df, square, xcol="UMAP_1_merged", ycol="UMAP_2_merged")
    assert sorted(ids) == ["b", "d"]
