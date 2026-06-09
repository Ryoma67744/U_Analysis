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


def test_committed_polygon_store_format_flows_through_assignment():
    """確定ポリゴンの store 形 [{"name","vertices"}] が transform→assign を通り、名前が割当に出る。

    クリックで頂点配置 → 確定すると hne_polygons_store に {"name","vertices"} で積まれる。
    その形のまま transform_polygons / assign_regions に渡せることを担保する。
    """
    M = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])  # 恒等（H&E画素=MSI座標とみなす）
    polys = [{"name": "脳", "vertices": [[0, 0], [10, 0], [10, 10], [0, 10]]}]
    polys_msi = hn.transform_polygons(polys, M)
    assert polys_msi[0]["name"] == "脳"  # 変換後も名前が保持される
    df = pd.DataFrame({"SpatialX": [5.0, 100.0], "SpatialY": [5.0, 100.0]})
    reg = hn.assign_regions(df, polys_msi)
    assert reg.iloc[0] == "脳"     # (5,5) は領域内
    assert reg.iloc[1] is None     # (100,100) は領域外


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


# --- MSI 回転に対する割当の不変性（H&E タブ回転機能） ---
def test_assign_regions_invariant_under_shared_rotation():
    """spot とポリゴンに同じ回転+反転を適用すると領域割当は不変であることを確認。

    H&E タブの MSI 回転は spot にもポリゴン（アフィン経由）にも同量かかるため、
    `assign_regions` の結果は回転に依らない（設計の中核を純ロジックで担保）。
    """
    from app.callbacks.interactive_spatial import _transform_coords

    rng = np.random.default_rng(0)
    df = pd.DataFrame({
        "SpatialX": rng.uniform(0, 20, 200),
        "SpatialY": rng.uniform(0, 20, 200),
    })
    polygons = [
        {"name": "脳", "vertices": [(2, 2), (8, 2), (8, 8), (2, 8)]},
        {"name": "心臓", "vertices": [(10, 10), (18, 10), (18, 18), (10, 18)]},
    ]
    region0 = hn.assign_regions(df, polygons)

    # spot とポリゴン頂点を同一フレームに積み、同じ回転+反転を一括適用（共通中心）
    poly_lens = [len(p["vertices"]) for p in polygons]
    allx = np.concatenate(
        [df["SpatialX"].to_numpy(float)]
        + [np.array([v[0] for v in p["vertices"]], float) for p in polygons])
    ally = np.concatenate(
        [df["SpatialY"].to_numpy(float)]
        + [np.array([v[1] for v in p["vertices"]], float) for p in polygons])
    rx, ry = _transform_coords(allx, ally, 37.0, flip_h=True, flip_v=False)

    n = len(df)
    df_rot = pd.DataFrame({"SpatialX": rx[:n], "SpatialY": ry[:n]})
    polys_rot, off = [], n
    for p, L in zip(polygons, poly_lens):
        polys_rot.append({"name": p["name"],
                          "vertices": list(zip(rx[off:off + L], ry[off:off + L]))})
        off += L
    region1 = hn.assign_regions(df_rot, polys_rot)

    pd.testing.assert_series_equal(region0, region1, check_names=False)


def test_parse_plotly_path():
    pts = hn.parse_plotly_path("M100,200L150,250L120,300Z")
    assert pts == [(100.0, 200.0), (150.0, 250.0), (120.0, 300.0)]
    assert hn.parse_plotly_path("") == []
    # 実描画に近い（小数・スペース混在）
    pts2 = hn.parse_plotly_path("M 10.5,20.0 L30,40.25 L5,6 Z")
    assert pts2 == [(10.5, 20.0), (30.0, 40.25), (5.0, 6.0)]
