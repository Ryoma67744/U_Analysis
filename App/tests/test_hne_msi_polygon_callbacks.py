"""MSI に直接描いた領域の確定・表示・保存・集計を確認する。"""

import numpy as np
import pandas as pd
import pytest
from dash import no_update

from app.callbacks import hne_overlay_callbacks as cb
from app.services import hne_persistence as hp


@pytest.fixture
def sample_state(monkeypatch):
    state = {"plot_data": pd.DataFrame({
        "Sample": ["S1", "S1"], "SpatialX": [2., 8.], "SpatialY": [2., 8.],
        "Cluster": ["1", "2"], "TotalCount": [10., 20.],
    })}
    monkeypatch.setattr(cb, "_get_state", lambda _: state)
    return state


def _draft(path, rotation):
    return {"coord_space": "msi", "vertices": [[9, 1], [5, 1], [5, 5], [9, 5]],
            "sample": "S1", "rds_path": path,
            "context": cb._msi_drawing_context("S1", path, rotation)}


def test_commit_rotated_msi_is_saved_in_raw_coordinates(sample_state, tmp_path):
    path = str(tmp_path / "data.rds")
    rotation = {"angle": 90}
    polys, draft, rows = cb.hne_polygon_commit(1, _draft(path, rotation), [],
                                             "msi", rotation, "S1", path)
    assert draft == []
    assert rows[0]["source"] == "MSI"
    assert polys[0]["coord_space"] == "msi"
    assert np.allclose(polys[0]["vertices"], [[1, 1], [1, 5], [5, 5], [5, 1]])
    # H&E 画像もランドマークもない個体が保存・復元・集計できる。
    cb.hne_autosave({}, polys, rotation, "S1", path)
    restored = cb.hne_restore_sample("S1", path)
    assert restored[0] is None
    assert restored[2] == polys
    assert hp.load_hne_sample(path, "S1")["polygons"] == polys
    result = cb.hne_assign_and_summarize(1, "S1", polys, None, rotation, path)
    table = result.children[-1]
    assert table.data == [{"領域": "領域1", "クラスタ": "1", "spot数": 1, "領域内%": 100.0}]


def test_commit_rejects_stale_frame_and_wrong_target(sample_state):
    draft = _draft("a.rds", {"angle": 90})
    for target, rotation, sample, path in [
        ("msi", {"angle": 0}, "S1", "a.rds"),
        ("hne", {"angle": 90}, "S1", "a.rds"),
        ("msi", {"angle": 90}, "S2", "a.rds"),
        ("msi", {"angle": 90}, "S1", "b.rds"),
    ]:
        assert all(v is no_update for v in cb.hne_polygon_commit(
            1, draft, [], target, rotation, sample, path))


def test_legacy_hne_commit_and_table_edit_keep_coordinate_space(sample_state):
    vertices = [[0, 0], [1, 0], [1, 1]]
    polys, _, rows = cb.hne_polygon_commit(1, vertices, [], "hne", {}, "S1", "a.rds")
    assert polys[0]["vertices"] == vertices
    assert rows[0]["source"] == "H&E"
    polys.append({"name": "direct", "vertices": vertices, "coord_space": "msi"})
    rows = cb._polygon_rows(polys)
    rows[1]["name"] = "MSI領域"
    updated, _ = cb.hne_polygon_table_to_store(rows, polys)
    assert updated[1]["coord_space"] == "msi"
    assert updated[1]["vertices"] == vertices


def test_msi_figure_shows_native_roi_without_affine_and_rotates_it(sample_state):
    p = {"name": "direct", "coord_space": "msi", "vertices": [[1, 1], [1, 5], [5, 5]]}
    fig = cb.hne_tic_figure("S1", {}, None, [p], "polygon", {"angle": 90},
                            "msi", [], "a.rds")
    roi = next(t for t in fig.data if t.name == "direct")
    assert np.allclose(roi.x, [9, 5, 5, 9])
    assert np.allclose(roi.y, [1, 1, 5, 1])
    assert fig.layout.meta["hne_polygon_draw"] is True
    assert any(t.name == "下書き" for t in fig.data)


def test_hne_view_does_not_misinterpret_native_msi_as_image_pixels(sample_state):
    img = {"src": "data:image/png;base64,", "width": 20, "height": 20}
    p = {"name": "direct", "coord_space": "msi", "vertices": [[1, 1], [1, 5], [5, 5]]}
    fig = cb.hne_image_figure(img, {}, [p], .6, "polygon", None, {},
                              _draft("a.rds", {}), "S1", "a.rds")
    assert len(fig.layout.shapes) == 0
    assert len(next(t for t in fig.data if t.name == "下書き").x) == 0
    # H&E→MSI が +10 のとき、H&E 表示へは -10 で射影する。
    fig = cb.hne_image_figure(img, {}, [p], .6, "polygon",
                              {"M": [[1, 0, 10], [0, 1, 10]]}, {}, [], "S1", "a.rds")
    assert fig.layout.shapes[0].path == "M-9.0,-9.0L-9.0,-5.0L-5.0,-5.0Z"


def test_mixed_unregistered_hne_is_reported(sample_state):
    vertices = [[0, 0], [4, 0], [4, 4], [0, 4]]
    polys = [{"name": "old", "vertices": vertices},
             {"name": "direct", "coord_space": "msi", "vertices": vertices}]
    result = cb.hne_assign_and_summarize(1, "S1", polys, None, {}, "a.rds")
    assert "H&E 領域は未集計" in result.children[0].children
    assert result.children[-1].data[0]["領域"] == "direct"


def test_empty_spatial_slice_shows_message(sample_state):
    sample_state["plot_data"][["SpatialX", "SpatialY"]] = np.nan
    fig = cb.hne_tic_figure("S1", {}, None, [], "polygon", {}, "msi", [], "a.rds")
    assert "空間座標がありません" in fig.layout.annotations[0].text


def test_export_warning_counts_only_unregistered_hne():
    p = {"name": "region", "vertices": [[0, 0], [1, 0], [0, 1]]}
    landmarks = {"hne": [[0, 0], [1, 0], [0, 1]], "tic": [[0, 0], [1, 0], [0, 1]]}
    assert cb._unregistered_hne_counts({
        "msi": {"polygons": [{**p, "coord_space": "msi"}]},
        "mixed": {"polygons": [p, {**p, "coord_space": "msi"}]},
        "registered": {"polygons": [p], "landmarks": landmarks},
    }) == {"mixed": 1}
