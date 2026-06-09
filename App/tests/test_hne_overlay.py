"""H&E オーバーレイ純ロジック（hne_overlay）のテスト。"""

import numpy as np
import pandas as pd

from app.services import hne_overlay as hn


# --- アフィン ---
def test_estimate_affine_recovers_known_transform():
    # 既知のアフィン（回転45度・スケール2・平行移動(10,-5)）を作り、対応点から復元
    th = np.radians(45.0)
    A = 2.0 * np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    t = np.array([10.0, -5.0])
    src = np.array([[0, 0], [1, 0], [0, 1], [2, 3], [5, 1]], dtype=float)
    dst = (A @ src.T).T + t
    M = hn.estimate_affine(src, dst)
    assert np.allclose(M[:, :2], A, atol=1e-6)
    assert np.allclose(M[:, 2], t, atol=1e-6)
    # apply_affine が一致
    assert np.allclose(hn.apply_affine(src, M), dst, atol=1e-6)
    # 残差ほぼ0
    assert hn.affine_residual(src, dst, M) < 1e-6


def test_invert_affine_roundtrip():
    M = np.array([[2.0, 0.3, 5.0], [-0.1, 1.5, -2.0]])
    Minv = hn.invert_affine(M)
    pts = np.array([[1.0, 2.0], [3.0, -4.0], [0.0, 0.0]])
    fwd = hn.apply_affine(pts, M)
    back = hn.apply_affine(fwd, Minv)
    assert np.allclose(back, pts, atol=1e-9)


def test_estimate_affine_requires_3_points():
    import pytest
    with pytest.raises(ValueError):
        hn.estimate_affine([[0, 0], [1, 1]], [[0, 0], [1, 1]])


# --- 点-内包判定 ---
def test_points_in_polygon_square():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    xs = np.array([5, -1, 11, 5, 0.001, 9.999])
    ys = np.array([5, 5, 5, -1, 0.001, 9.999])
    inside = hn.points_in_polygon(xs, ys, sq)
    assert list(inside) == [True, False, False, False, True, True]


def test_points_in_polygon_triangle_and_degenerate():
    tri = [(0, 0), (4, 0), (0, 4)]
    assert bool(hn.points_in_polygon([1], [1], tri)[0]) is True
    assert bool(hn.points_in_polygon([3], [3], tri)[0]) is False
    # 頂点 < 3 は全 False
    assert not hn.points_in_polygon([1, 2], [1, 2], [(0, 0), (1, 1)]).any()


# --- 領域割当 ---
def test_assign_regions_first_match_and_na():
    df = pd.DataFrame({
        "SpatialX": [1.0, 5.0, 100.0, np.nan],
        "SpatialY": [1.0, 5.0, 100.0, 1.0],
    })
    polygons = [
        {"name": "脳", "vertices": [(0, 0), (3, 0), (3, 3), (0, 3)]},
        {"name": "心臓", "vertices": [(4, 4), (6, 4), (6, 6), (4, 6)]},
    ]
    reg = hn.assign_regions(df, polygons)
    assert reg.iloc[0] == "脳"      # (1,1) in 脳
    assert reg.iloc[1] == "心臓"    # (5,5) in 心臓
    assert reg.iloc[2] is None      # (100,100) どこにも無い
    assert reg.iloc[3] is None      # NA 空間は除外


def test_assign_regions_first_polygon_wins_on_overlap():
    df = pd.DataFrame({"SpatialX": [5.0], "SpatialY": [5.0]})
    polygons = [
        {"name": "A", "vertices": [(0, 0), (10, 0), (10, 10), (0, 10)]},
        {"name": "B", "vertices": [(0, 0), (10, 0), (10, 10), (0, 10)]},
    ]
    assert hn.assign_regions(df, polygons).iloc[0] == "A"


def test_transform_polygons():
    # 2倍スケール＋平行移動
    M = np.array([[2.0, 0.0, 1.0], [0.0, 2.0, -1.0]])
    polys = [{"name": "x", "vertices": [(0, 0), (1, 0), (1, 1)]}]
    out = hn.transform_polygons(polys, M)
    assert out[0]["name"] == "x"
    assert np.allclose(out[0]["vertices"], [[1, -1], [3, -1], [3, 1]])


# --- 領域×クラスタ集計 ---
def test_region_cluster_counts():
    df = pd.DataFrame({
        "region": ["脳", "脳", "脳", "心臓", None],
        "Cluster": ["1", "1", "3", "1", "2"],
    })
    g = hn.region_cluster_counts(df)
    brain1 = g[(g.region == "脳") & (g.Cluster == "1")].iloc[0]
    assert brain1["count"] == 2
    assert brain1["pct_in_region"] == round(2 / 3 * 100, 2)
    # None 領域は集計に含めない
    assert (g["region"].isna()).sum() == 0


# --- MetaboAnalyst エクスポート ---
def test_build_region_cluster_export_group_means_and_compound_names():
    df = pd.DataFrame({
        "CellID": ["c1", "c2", "c3", "c4"],
        "region": ["脳", "脳", "心臓", None],
        "Cluster": ["1", "1", "2", "1"],
    })
    expr = pd.DataFrame({
        "CellID": ["c1", "c2", "c3", "c4"],
        "m/z 419.25720": [10.0, 20.0, 100.0, 999.0],
        "m/z 885.54940": [1.0, 3.0, 50.0, 999.0],
    })
    name_map = {"m/z 419.25720": "ADP", "m/z 885.54940": "PI 38:4"}
    out = hn.build_region_cluster_export(df, expr, feature_name_map=name_map)
    # 群: 脳_cluster1（c1,c2 平均）, 心臓_cluster2（c3）。None 領域は除外。
    assert set(out["Group"]) == {"脳_cluster1", "心臓_cluster2"}
    brain = out[out["Group"] == "脳_cluster1"].iloc[0]
    assert brain["ADP"] == 15.0       # mean(10,20)
    assert brain["PI 38:4"] == 2.0     # mean(1,3)
    heart = out[out["Group"] == "心臓_cluster2"].iloc[0]
    assert heart["ADP"] == 100.0
    # 列名が化合物名になっている
    assert "ADP" in out.columns and "PI 38:4" in out.columns


def test_build_region_cluster_export_empty_when_no_region():
    df = pd.DataFrame({"CellID": ["c1"], "region": [None], "Cluster": ["1"]})
    expr = pd.DataFrame({"CellID": ["c1"], "m/z 1": [1.0]})
    assert hn.build_region_cluster_export(df, expr).empty


def test_parse_plotly_path():
    pts = hn.parse_plotly_path("M100,200L150,250L120,300Z")
    assert pts == [(100.0, 200.0), (150.0, 250.0), (120.0, 300.0)]
    assert hn.parse_plotly_path("") == []
    # 実描画に近い（小数・スペース混在）
    pts2 = hn.parse_plotly_path("M 10.5,20.0 L30,40.25 L5,6 Z")
    assert pts2 == [(10.5, 20.0), (30.0, 40.25), (5.0, 6.0)]
