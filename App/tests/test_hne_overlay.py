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


def test_build_region_cluster_export_sample_label_combines_sections():
    """sample_col 指定で群ラベルが `{切片}_{ROI}_{クラスタ}`（例 E15_Brain_23）になり、
    全切片が1つに統合される。ROI 無し（region=None）は除外。"""
    df = pd.DataFrame({
        "CellID": ["c1", "c2", "c3", "c4"],
        "Sample": ["E15", "E15", "E16", "E15"],
        "region": ["Brain", "Brain", "Brain", None],
        "Cluster": ["23", "23", "23", "1"],
    })
    expr = pd.DataFrame({
        "CellID": ["c1", "c2", "c3", "c4"],
        "m/z 1": [10.0, 20.0, 100.0, 999.0],
    })
    out = hn.build_region_cluster_export(df, expr, sample_col="Sample")
    assert set(out["Group"]) == {"E15_Brain_cluster23", "E16_Brain_cluster23"}
    assert out[out["Group"] == "E15_Brain_cluster23"].iloc[0]["m/z 1"] == 15.0  # mean(10,20)
    assert out[out["Group"] == "E16_Brain_cluster23"].iloc[0]["m/z 1"] == 100.0


# --- B経路: groups table / 列名置換 / 同値性 ---
def test_build_groups_table_label_and_na_exclusion():
    df = pd.DataFrame({
        "CellID": ["c1", "c2", "c3", "c4"],
        "Sample": ["E15", "E15", "E16", "E15"],
        "region": ["Brain", "Brain", "Brain", None],
        "Cluster": ["23", "23", "23", "1"],
    })
    g = hn.build_groups_table(df)
    assert list(g.columns) == ["CellID", "Group"]
    assert set(g["CellID"]) == {"c1", "c2", "c3"}      # region=None の c4 は除外
    assert dict(zip(g["CellID"], g["Group"])) == {
        "c1": "E15_Brain_cluster23", "c2": "E15_Brain_cluster23",
        "c3": "E16_Brain_cluster23"}


def test_build_groups_table_empty_when_no_region():
    df = pd.DataFrame({"CellID": ["c1"], "Sample": ["E15"],
                       "region": [None], "Cluster": ["1"]})
    g = hn.build_groups_table(df)
    assert g.empty and list(g.columns) == ["CellID", "Group"]


def test_rename_export_columns():
    df = pd.DataFrame({"Group": ["E15_Brain_23"], "m/z 419.25720": [15.0],
                       "m/z 885.54940": [2.0]})
    out = hn.rename_export_columns(df, {"m/z 419.25720": "ADP",
                                        "m/z 885.54940": "PI 38:4"})
    assert list(out.columns) == ["Group", "ADP", "PI 38:4"]
    assert out.iloc[0]["ADP"] == 15.0
    assert hn.rename_export_columns(df, None) is df      # map 無しはそのまま


def test_groups_table_aggregation_matches_build_region_cluster_export():
    """B経路（groups_table→群平均）と現行 build_region_cluster_export が同一数値。"""
    df = pd.DataFrame({
        "CellID": ["c1", "c2", "c3", "c4"],
        "Sample": ["E15", "E15", "E16", "E15"],
        "region": ["Brain", "Brain", "Heart", None],
        "Cluster": ["1", "1", "2", "3"],
    })
    expr = pd.DataFrame({
        "CellID": ["c1", "c2", "c3", "c4"],
        "m/z 1": [10.0, 20.0, 100.0, 999.0],
        "m/z 2": [1.0, 3.0, 50.0, 999.0],
    })
    ref = hn.build_region_cluster_export(df, expr, sample_col="Sample")
    # B相当（R が返す値を Python で模擬）: groups_table を expr に結合し群平均
    g = hn.build_groups_table(df)
    merged = g.merge(expr, on="CellID")
    feat = [c for c in expr.columns if c != "CellID"]
    sim = merged.groupby("Group")[feat].mean().reset_index()
    assert set(ref["Group"]) == set(sim["Group"])
    for grp in ref["Group"]:
        r = ref[ref["Group"] == grp].iloc[0]
        s = sim[sim["Group"] == grp].iloc[0]
        for f in feat:
            assert abs(float(r[f]) - float(s[f])) < 1e-9


# --- グループ統合（同じ # で複数ポリゴンを1 ROI に） ---
def test_apply_region_groups_merges_by_group_and_keeps_ungrouped():
    polys = [
        {"name": "lung-L", "group": "1", "vertices": [[0, 0]]},
        {"name": "lung-R", "group": "1", "vertices": [[1, 1]]},
        {"name": "heart", "group": "2", "vertices": [[2, 2]]},
        {"name": "brain", "vertices": [[3, 3]]},            # group 未設定
        {"name": "", "group": "9", "vertices": [[4, 4]]},   # group 内に非空名なし
    ]
    out = hn.apply_region_groups(polys)
    assert out[0]["name"] == "lung-L"   # group1 代表 = 最初の非空名
    assert out[1]["name"] == "lung-L"   # 同 group → 同名（=1 ROI に統合）
    assert out[2]["name"] == "heart"
    assert out[3]["name"] == "brain"    # group 未設定は自分の名前
    assert out[4]["name"] == "領域9"    # 非空名が無ければ 領域{group}
    assert out[1]["vertices"] == [[1, 1]]  # vertices は保持


def test_apply_region_groups_merges_in_assignment():
    """同 group の2ポリゴンに別名を付けても、割当後の領域名が1つに揃う。"""
    polys = [
        {"name": "A-left", "group": "7", "vertices": [(0, 0), (2, 0), (2, 2), (0, 2)]},
        {"name": "A-right", "group": "7", "vertices": [(10, 10), (12, 10), (12, 12), (10, 12)]},
    ]
    df = pd.DataFrame({"SpatialX": [1.0, 11.0], "SpatialY": [1.0, 11.0]})
    reg = hn.assign_regions(df, hn.apply_region_groups(polys))
    assert reg.iloc[0] == "A-left" and reg.iloc[1] == "A-left"  # 両 spot が同一 ROI


# --- 回転（純関数）---
def test_apply_rotation_identity_and_flip():
    x = np.array([0.0, 10.0]); y = np.array([0.0, 0.0])
    ix, iy = hn.apply_rotation(x, y, None)
    assert np.allclose(ix, x) and np.allclose(iy, y)
    # flip_h: 中心(5,0)で左右反転 → x: 0->10, 10->0
    fx, fy = hn.apply_rotation(x, y, {"flip_h": True})
    assert np.allclose(fx, [10.0, 0.0]) and np.allclose(fy, y)


def test_apply_rotation_matches_transform_coords():
    """本番の `_transform_coords` と一致（dash 未導入環境ではスキップ）。"""
    try:
        from app.callbacks.interactive_spatial import _transform_coords
    except Exception:
        import pytest
        pytest.skip("plotly/dash 未導入のためスキップ")
    x = np.array([1.0, 5.0, 9.0, 3.0]); y = np.array([2.0, 8.0, 4.0, 6.0])
    ax, ay = hn.apply_rotation(x, y, {"angle": 37.0, "flip_h": True, "flip_v": False})
    bx, by = _transform_coords(x, y, 37.0, flip_h=True, flip_v=False)
    assert np.allclose(ax, bx) and np.allclose(ay, by)


# --- overlay 保存状態 → 領域割当（A・C 共通基盤）---
def test_regions_from_overlay_assigns_with_identity_landmarks():
    entry = {
        "landmarks": {"hne": [[0, 0], [10, 0], [0, 10]],
                      "tic": [[0, 0], [10, 0], [0, 10]]},  # 恒等アフィン
        "polygons": [{"name": "脳", "vertices": [[0, 0], [10, 0], [10, 10], [0, 10]]}],
        "rotation": {"angle": 0, "flip_h": False, "flip_v": False},
    }
    sub = pd.DataFrame({"SpatialX": [5.0, 100.0], "SpatialY": [5.0, 100.0]})
    reg = hn.regions_from_overlay(sub, entry)
    assert reg.iloc[0] == "脳"      # 領域内
    assert reg.iloc[1] is None      # 領域外


def test_regions_from_overlay_none_without_polygons_or_landmarks():
    sub = pd.DataFrame({"SpatialX": [5.0], "SpatialY": [5.0]})
    # polygon 無し
    assert hn.regions_from_overlay(
        sub, {"landmarks": {"hne": [[0, 0], [1, 0], [0, 1]],
                            "tic": [[0, 0], [1, 0], [0, 1]]}, "polygons": []}).isna().all()
    # 対応点が3対未満
    assert hn.regions_from_overlay(
        sub, {"landmarks": {"hne": [[0, 0]], "tic": [[0, 0]]},
              "polygons": [{"name": "x", "vertices": [[0, 0], [9, 0], [9, 9], [0, 9]]}]}).isna().all()


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
