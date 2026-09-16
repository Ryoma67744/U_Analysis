"""MSI 直接描画ポリゴンの座標系・保存・混在割当の回帰テスト。"""

import copy
import json

import numpy as np
import pandas as pd
import pytest

from app.services import hne_overlay as hn


def _square(x, y, half=0.6):
    return [[x - half, y - half], [x + half, y - half],
            [x + half, y + half], [x - half, y + half]]


@pytest.mark.parametrize("angle", [0, 90, -37, 180])
@pytest.mark.parametrize("flip_h,flip_v", [(False, False), (True, False),
                                         (False, True), (True, True)])
def test_arbitrary_msi_vertices_roundtrip_uses_whole_slice_center(angle, flip_h, flip_v):
    """非対称な切片／端の頂点でも、表示用 spot と同じ変換から raw に戻る。"""
    x = np.array([2.0, 5.0, 20.0, 3.0])
    y = np.array([-3.0, 7.0, 10.0, 22.0])
    rot = {"angle": angle, "flip_h": flip_h, "flip_v": flip_v}
    center = hn.rotation_center(x, y)
    displayed = hn.transform_msi_points(np.column_stack([x, y]), rot, center)
    rx, ry = hn.apply_rotation(x, y, rot)
    np.testing.assert_allclose(displayed, np.column_stack([rx, ry]), atol=1e-12)
    # 頂点自身の重心を使う誤実装ではこの一致を満たさない。
    raw = np.array(_square(2.0, -3.0))
    forward = hn.transform_msi_points(raw, rot, center)
    back = hn.transform_msi_points(forward, rot, center, inverse=True)
    np.testing.assert_allclose(back, raw, atol=1e-12)
    np.testing.assert_array_equal(raw, _square(2.0, -3.0))


def test_msi_regions_work_without_hne_and_ignore_display_rotation_and_bad_landmarks():
    df = pd.DataFrame({"SpatialX": [1.0, 8.0, np.nan],
                       "SpatialY": [2.0, 9.0, 2.0]}, index=[7, 12, 18])
    entry = {"polygons": [{"name": "MSI 領域", "coord_space": "msi",
                           "vertices": _square(1.0, 2.0)}]}
    expected = pd.Series(["MSI 領域", None, None], index=df.index)
    pd.testing.assert_series_equal(hn.regions_from_overlay(df, entry), expected)
    entry["rotation"] = {"angle": 37, "flip_h": True, "flip_v": True}
    entry["landmarks"] = {"hne": [["bad", 0]] * 3, "tic": [[0, 0]] * 3}
    pd.testing.assert_series_equal(hn.regions_from_overlay(df, entry), expected)


def _mixed_example():
    df = pd.DataFrame({"SpatialX": [1.0, 5.0, 9.0],
                       "SpatialY": [2.0, 5.0, 8.0]}, index=[11, 21, 31])
    rotation = {"angle": 37, "flip_h": True, "flip_v": False}
    matrix = np.array([[2.0, 0.4, 30.0], [0.2, 3.0, -20.0]])
    center = hn.rotation_center(df.SpatialX, df.SpatialY)
    hne_vertices = hn.apply_affine(
        hn.transform_msi_points(_square(5, 5), rotation, center),
        hn.invert_affine(matrix)).tolist()
    polygons = [
        {"name": "MSI", "coord_space": "msi", "vertices": _square(1, 2)},
        {"name": "H&E", "vertices": hne_vertices},
    ]
    hne_lm = [[0, 0], [1, 0], [0, 1]]
    entry = {"polygons": polygons, "rotation": rotation,
             "landmarks": {"hne": hne_lm,
                           "tic": hn.apply_affine(hne_lm, matrix).tolist()}}
    return df, entry, matrix


def test_mixed_legacy_hne_and_msi_regions_share_summary_and_saved_export_assignment():
    """現在の画面と JSON 保存後の出力が、同じ領域名・spot 順で一致する。"""
    df, entry, matrix = _mixed_example()
    original = copy.deepcopy(entry)
    expected = pd.Series(["MSI", "H&E", None], index=df.index)
    assigned = hn.assign_regions_from_polygons(
        df, entry["polygons"], M=matrix, rotation=entry["rotation"])
    restored = json.loads(json.dumps(entry))
    pd.testing.assert_series_equal(assigned, expected)
    pd.testing.assert_series_equal(hn.regions_from_overlay(df, restored), expected)
    assert entry == original


@pytest.mark.parametrize("landmarks", [{}, {"hne": [[0, 0]], "tic": [[0, 0]]},
                                      {"hne": [["bad", 0]] * 3,
                                       "tic": [[0, 0]] * 3}])
def test_missing_or_broken_hne_registration_only_skips_hne_polygons(landmarks):
    df, entry, _ = _mixed_example()
    entry["landmarks"] = landmarks
    expected = pd.Series(["MSI", None, None], index=df.index)
    pd.testing.assert_series_equal(hn.regions_from_overlay(df, entry), expected)


def test_first_polygon_priority_is_preserved_across_coordinate_spaces():
    df = pd.DataFrame({"SpatialX": [1.0], "SpatialY": [2.0]})
    msi = {"name": "MSI", "coord_space": "msi", "vertices": _square(1, 2)}
    # 恒等アフィンでも coord_space の相違で先勝ち順が変わらない。
    hne = {"name": "H&E", "coord_space": "hne", "vertices": _square(1, 2)}
    matrix = np.array([[1, 0, 0], [0, 1, 0]])
    assert hn.assign_regions_from_polygons(df, [msi, hne], M=matrix).iloc[0] == "MSI"
    assert hn.assign_regions_from_polygons(df, [hne, msi], M=matrix).iloc[0] == "H&E"


def test_grouping_merges_coordinate_spaces_and_preserves_metadata():
    df, entry, _ = _mixed_example()
    entry["polygons"][0].update(group="3", source_id="sample-1")
    entry["polygons"][1].update(group="3")
    grouped = hn.apply_region_groups(entry["polygons"])
    assert grouped[0]["coord_space"] == "msi"
    assert grouped[0]["source_id"] == "sample-1"
    assert grouped[0]["vertices"] == entry["polygons"][0]["vertices"]
    assert grouped[1]["name"] == "MSI"
    pd.testing.assert_series_equal(
        hn.regions_from_overlay(df, entry), pd.Series(["MSI", "MSI", None], index=df.index))


def test_legacy_hne_assignment_matches_original_transform_then_assign():
    df, entry, matrix = _mixed_example()
    polygons = entry["polygons"][1:]
    displayed = df.copy()
    displayed["SpatialX"], displayed["SpatialY"] = hn.apply_rotation(
        df.SpatialX, df.SpatialY, entry["rotation"])
    expected = hn.assign_regions(displayed, hn.transform_polygons(polygons, matrix))
    pd.testing.assert_series_equal(
        hn.assign_regions_from_polygons(df, polygons, M=matrix, rotation=entry["rotation"]),
        expected)


def test_unknown_coordinate_space_is_not_silently_interpreted_as_hne():
    df = pd.DataFrame({"SpatialX": [1.0], "SpatialY": [2.0]})
    poly = {"name": "unknown", "coord_space": "future-space", "vertices": _square(1, 2)}
    matrix = np.array([[1, 0, 0], [0, 1, 0]])
    assert hn.assign_regions_from_polygons(df, [poly], M=matrix).isna().all()
