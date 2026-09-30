"""クラスタ番号の配置が平均位置へ戻ると失敗する幾何回帰テスト。"""

import numpy as np
import pytest

from app.utils import cluster_label_placement as P


def _grid(nx, ny, sx=1.0, sy=1.0):
    x, y = np.meshgrid(np.arange(nx) * sx, np.arange(ny) * sy)
    return x.ravel(), y.ravel()


def _box(x0, x1, y0, y1, n):
    x, y = np.meshgrid(np.linspace(x0, x1, n), np.linspace(y0, y1, n))
    return x.ravel(), y.ravel()


def test_spatial_selects_largest_island_not_mean_between_islands():
    x, y = _grid(20, 10)
    labels = np.full(x.size, "background")
    labels[(x < 7) & (y < 7)] = "target"
    labels[(x > 15) & (y > 5)] = "target"
    anchor = P.compute_label_anchors(x, y, labels, kind="spatial")["target"]
    assert (anchor.x, anchor.y) == (3.0, 3.0)
    assert anchor.area == 49.0
    assert anchor.reason == "spatial_region"
    assert labels[anchor.index] == "target"
    assert x[labels == "target"].mean() > 6


@pytest.mark.parametrize("shape", ["ring", "crescent"])
def test_spatial_holes_and_concavity_do_not_receive_the_label(shape):
    x, y = _grid(15, 15)
    radius = np.hypot(x - 7, y - 7)
    target = (radius >= 4) & (radius <= 7)
    if shape == "crescent":
        target &= x < 10
    labels = np.where(target, "target", "hole")
    anchor = P.compute_label_anchors(x, y, labels, kind="spatial")["target"]
    assert target[anchor.index]
    assert np.hypot(anchor.x - 7, anchor.y - 7) >= 4


def test_spatial_diagonal_contact_does_not_join_two_islands():
    x, y = _grid(6, 6)
    labels = np.where(((x < 2) & (y < 2)) | ((x >= 2) & (x < 4) & (y >= 2) & (y < 4)),
                      "target", "other")
    anchor = P.compute_label_anchors(x, y, labels, kind="spatial")["target"]
    assert anchor.area == 4.0
    assert anchor.x < 2 and anchor.y < 2


def test_spatial_anisotropic_pitch_selects_physically_wider_lobe():
    ix, iy = _grid(17, 11)
    target = (((ix <= 8) & (iy >= 4) & (iy <= 6))
              | ((ix >= 13) & (ix <= 15) & (iy >= 1) & (iy <= 9))
              | ((ix >= 8) & (ix <= 13) & (iy == 5)))
    labels = np.where(target, "target", "other")
    anchor = P.compute_label_anchors(ix, iy * 4, labels, kind="spatial")["target"]
    # 左の9列×3行は物理的には幅9・高さ12。右の3列×9行より内側が広い。
    assert anchor.x <= 8
    assert anchor.y == 20
    assert anchor.area == int(target.sum()) * 4


def test_spatial_uses_reference_pitch_after_every_other_column_is_filtered():
    full_x, full_y = _grid(7, 5, sx=2, sy=3)
    keep = (full_x % 4) == 0
    x, y = full_x[keep], full_y[keep]
    labels = np.full(x.size, "target")
    anchor = P.compute_label_anchors(x, y, labels, kind="spatial",
                                      grid_reference=(full_x, full_y))["target"]
    # 空列を除外してもピッチが倍にならず、縦の5セルが1島。
    assert anchor.area == 5 * 2 * 3
    assert anchor.y == 6


def test_spatial_original_index_survives_finite_filter_and_all_transforms():
    x, y = _grid(5, 5)
    x, y = np.r_[np.nan, x], np.r_[0, y]
    labels = np.full(x.size, "target")
    anchor = P.compute_label_anchors(x, y, labels, kind="spatial")["target"]
    assert anchor.index == 13
    assert x[anchor.index] == anchor.x and y[anchor.index] == anchor.y
    # 呼び出し側は点配列と同じ回転・反転結果をindexで選べる。
    finite = np.isfinite(x) & np.isfinite(y)
    for angle in (0, 30, 90):
        for flip in (-1, 1):
            rad = np.deg2rad(angle)
            tx = flip * (x - 2) * np.cos(rad) - (y - 2) * np.sin(rad) + 2
            ty = flip * (x - 2) * np.sin(rad) + (y - 2) * np.cos(rad) + 2
            assert np.isfinite(tx[anchor.index]) and np.isfinite(ty[anchor.index])
            assert np.count_nonzero(finite) == 25


@pytest.mark.parametrize("kind", ["spatial", "umap"])
def test_row_shuffle_keeps_same_anchor_coordinates(kind):
    x, y = _grid(12, 8)
    labels = np.where(((x < 3) & (y < 3)) | ((x > 8) & (y > 4)), "target", "other")
    expected = P.compute_label_anchors(x, y, labels, kind=kind)
    order = np.random.default_rng(42).permutation(len(x))
    actual = P.compute_label_anchors(x[order], y[order], labels[order], kind=kind)
    for label in expected:
        assert actual[label].x == expected[label].x
        assert actual[label].y == expected[label].y
        assert actual[label].area == expected[label].area
        assert labels[order][actual[label].index] == label


def test_duplicate_spatial_points_do_not_inflate_area():
    x, y = _grid(5, 5)
    labels = np.full(x.size, "target")
    anchor = P.compute_label_anchors(np.r_[x, x[:15]], np.r_[y, y[:15]],
                                      np.r_[labels, labels[:15]], kind="spatial")["target"]
    assert anchor.area == 25
    assert (anchor.x, anchor.y) == (2, 2)


@pytest.mark.parametrize("kind", ["spatial", "umap"])
def test_conflicting_duplicate_has_explicit_fallback(kind):
    x, y = _grid(5, 5)
    labels = np.full(x.size, "a")
    actual = P.compute_label_anchors(np.r_[x, x[0]], np.r_[y, y[0]],
                                     np.concatenate((labels, ["b"])), kind=kind)
    assert actual["a"].reason == "fallback_conflicting_coordinates"
    assert actual["b"].reason == "fallback_conflicting_coordinates"


def test_umap_selects_larger_lower_density_island_not_most_points():
    dense_x, dense_y = _box(0, 2, 0, 2, 21)  # 441点、面積4
    sparse_x, sparse_y = _box(8, 14, 0, 6, 13)  # 169点、面積36
    x, y = np.r_[dense_x, sparse_x], np.r_[dense_y, sparse_y]
    anchor = P.compute_label_anchors(x, y, np.full(x.size, "target"))["target"]
    assert 8 <= anchor.x <= 14
    assert 0 <= anchor.y <= 6
    assert anchor.reason == "umap_region"
    assert anchor.area > 25
    # 旧来の全点平均は二つの島の間に落ちる。
    assert 2 < x.mean() < 8


def test_umap_sparse_outliers_do_not_make_an_artificial_large_island():
    bx, by = _box(0, 4, 0, 4, 15)
    # 外れ値が10%以上あっても、90百分位だけで支持半径を巨大化させない。
    out_x = 20 + np.arange(35) * 3
    out_y = 10 + np.arange(35) % 3 * 4
    x, y = np.r_[bx, out_x], np.r_[by, out_y]
    anchor = P.compute_label_anchors(x, y, np.full(x.size, "target"))["target"]
    assert 0 <= anchor.x <= 4 and 0 <= anchor.y <= 4


def test_umap_does_not_bridge_another_clusters_region():
    x, y = _grid(19, 19)
    radius = np.hypot(x - 9, y - 9)
    labels = np.where((radius >= 5) & (radius <= 8), "ring", "other")
    anchor = P.compute_label_anchors(x, y, labels)["ring"]
    assert labels[anchor.index] == "ring"
    assert 5 <= np.hypot(anchor.x - 9, anchor.y - 9) <= 8


def test_umap_coordinate_scale_and_cluster_names_do_not_change_selected_points():
    x, y = _grid(13, 9)
    labels = np.where(x < 6, "1", "9")
    expected = P.compute_label_anchors(x, y, labels)
    renamed = np.where(labels == "1", "renamed_z", "renamed_a")
    actual = P.compute_label_anchors(x * 37 + 200, y * 37 - 400, renamed)
    for old, new in (("1", "renamed_z"), ("9", "renamed_a")):
        assert actual[new].index == expected[old].index
        assert actual[new].area == pytest.approx(expected[old].area * 37 ** 2)


@pytest.mark.parametrize("kind", ["spatial", "umap"])
def test_degenerate_cases_return_existing_point_or_omit_nonfinite_cluster(kind):
    labels = np.array(["invalid", "one", "line", "line", "line"])
    x, y = np.array([np.nan, 3, 1, 2, 4]), np.zeros(5)
    result = P.compute_label_anchors(x, y, labels, kind=kind)
    assert "invalid" not in result
    assert result["one"].index == 1
    assert result["line"].index == 3
    assert result["line"].reason.startswith("fallback_")
    assert P.compute_label_anchors([], [], [], kind=kind) == {}


def test_fallback_is_not_reused_for_a_different_dataset():
    first = P.compute_label_anchors([1], [2], ["0"])["0"]
    second = P.compute_label_anchors([100], [200], ["0"])["0"]
    assert (first.x, first.y) == (1, 2)
    assert (second.x, second.y) == (100, 200)


def test_fallback_is_translation_and_scale_independent():
    for shift, scale in ((0, 1e-20), (1e12, 1), (-1e12, 1000)):
        anchor = P.compute_label_anchors(
            np.array([0, 2, 5]) * scale + shift, [0, 0, 0], ["x"] * 3,
        )["x"]
        assert anchor.index == 1


def test_resource_limits_return_explicit_existing_point(monkeypatch):
    x, y = _grid(7, 7)
    labels = np.full(len(x), "target")
    monkeypatch.setattr(P, "_MAX_POINTS", 10)
    anchor = P.compute_label_anchors(x, y, labels)["target"]
    assert anchor.reason == "fallback_point_limit"
    assert (anchor.x, anchor.y) == (x[anchor.index], y[anchor.index])
    monkeypatch.setattr(P, "_MAX_POINTS", 100)
    monkeypatch.setattr(P, "_MAX_MASK_WORK", 10)
    anchor = P.compute_label_anchors(x, y, labels, kind="spatial")["target"]
    assert anchor.reason == "fallback_work_limit"


def test_spatial_irregular_coordinates_use_inferred_region():
    rng = np.random.default_rng(45)
    x, y = _grid(8, 8)
    x = x + rng.normal(0, 0.02, len(x))
    anchor = P.compute_label_anchors(x, y, np.full(len(x), "target"), kind="spatial")["target"]
    assert anchor.reason == "spatial_inferred_region"


def test_invalid_api_arguments_are_not_silently_hidden():
    with pytest.raises(ValueError, match="同じ長さ"):
        P.compute_label_anchors([1, 2], [1], ["x"])
    with pytest.raises(ValueError, match="kind"):
        P.compute_label_anchors([1], [1], ["x"], kind="unknown")
    with pytest.raises(ValueError, match="grid_reference"):
        P.compute_label_anchors([1], [1], ["x"], kind="spatial", grid_reference=([1, 2], [1]))
