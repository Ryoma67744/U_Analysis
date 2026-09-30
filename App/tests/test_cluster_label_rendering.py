"""★ ver76.0: 最大領域への番号配置が表示・座標変換・出力で同じ意味を持つ。"""

import copy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.callbacks import interactive_hne_bg as hne
from app.callbacks import interactive_spatial as spatial
from app.callbacks import interactive_umap as umap
from app.utils.cluster_label_placement import LabelAnchor
from app.utils.display_helpers import apply_display_overrides, apply_umap_display_overrides


def _islands():
    # 左の大島25点と、離れた右の小島4点。全点平均は大島の右端に寄る。
    points = [(x, y) for x in range(5) for y in range(5)]
    points += [(x, y) for x in range(20, 22) for y in range(2)]
    return pd.DataFrame({
        "CellID": [f"cell-{i}" for i in range(len(points))],
        "Sample": "S1", "Cluster": "3", "TotalCount": 1.0,
        "SpatialX": [p[0] for p in points], "SpatialY": [p[1] for p in points],
        "UMAP_1": [p[0] for p in points], "UMAP_2": [p[1] for p in points],
    })


def _spatial(df, **kwargs):
    return spatial._create_single_spatial_fig(df, {"3": "red", "9": "blue"}, None, set(),
                                               show_labels=True, **kwargs)


def test_umap_integrated_and_sample_choose_large_island_without_mutating_points():
    first = _islands()
    second = first.assign(Sample="S2", CellID=first.CellID + "-2")
    df = pd.concat([first, second], ignore_index=True)
    before = df.copy(deep=True)
    integrated = umap._build_umap_integrated_fig(first, "Cluster", None, False, True)
    collected = []
    umap._build_umap_per_sample_graphs(df, {"3": "red"}, None, True,
                                      collect_figures=collected)
    anchor = integrated.layout.annotations[0]
    assert 0 < anchor.x < 4 and 0 < anchor.y < 4
    for _, figure in collected:
        ann = figure["layout"]["annotations"][0]
        assert (ann["x"], ann["y"]) == (anchor.x, anchor.y)
    pd.testing.assert_frame_equal(df, before)


def test_sample_callback_preserves_existing_points_clusters_and_colors(monkeypatch):
    from app.callbacks import interactive_callbacks as callbacks
    first = _islands()
    df = pd.concat([first, first.assign(Sample="S2")], ignore_index=True).assign(
        Cluster_merged="99", UMAP_1_merged=1000.0, UMAP_2_merged=2000.0)
    before = df.copy(deep=True)
    captured = {}

    def build(actual, colors, *_args, **kwargs):
        captured.update(df=actual.copy(deep=True), colors=colors, scope=kwargs["label_scope"],
                        positions=kwargs["saved_positions"])
        return []

    monkeypatch.setattr(callbacks, "_interactive_data", {"plot_data": df, "method": "Harmony"})
    monkeypatch.setattr(callbacks, "_set_active_key", lambda *args: None)
    monkeypatch.setattr(callbacks, "accordion_toggle_is_noop", lambda *args, **kwargs: False)
    monkeypatch.setattr(callbacks, "set_export_figures", lambda *args, **kwargs: None)
    monkeypatch.setattr(umap, "ctx", SimpleNamespace(triggered_id="label_positions_revision"))
    monkeypatch.setattr(umap, "_with_section_groups", lambda value, *args: value)
    monkeypatch.setattr(umap, "_get_merged_label_positions", lambda *args, **kwargs: {
        "umap_per_sample": {"S1": {"3": {"x": 2, "y": 2}}},
        "umap_per_sample_merged": {"S1": {"99": {"x": 1000, "y": 2000}}}})
    monkeypatch.setattr(umap, "_build_umap_per_sample_graphs", build)
    umap.update_umap_per_sample(
        "per_sample", None, True, 2, None, 11, "data.rds", True, {}, 0,
        {"3": "#FF0000"}, 0, {}, ["acc_umap"], "Sample", [], {}, {},
        load_token="loaded")
    pd.testing.assert_frame_equal(captured["df"], before)
    pd.testing.assert_frame_equal(df, before)
    assert captured["colors"] == {"3": "#FF0000"}
    assert captured["scope"]["section"] == "umap_per_sample"
    assert captured["positions"] == {"S1": {"3": {"x": 2, "y": 2}}}


@pytest.mark.parametrize("angle,flip_h,flip_v", [(0, False, False), (30, True, False), (90, False, True)])
def test_spatial_anchor_follows_selected_observation_through_whole_cloud_transform(angle, flip_h, flip_v):
    df = _islands()
    before = df.copy(deep=True)
    fig = _spatial(df, rotation_deg=angle, flip_h=flip_h, flip_v=flip_v)
    px, py = spatial._transform_coords(df.SpatialX.to_numpy(), -df.SpatialY.to_numpy(),
                                       angle, flip_h=flip_h, flip_v=flip_v)
    px, py = spatial._round_for_display(px, py)
    middle = np.flatnonzero((df.SpatialX == 2) & (df.SpatialY == 2))[0]
    annotation = fig.layout.annotations[0]
    assert (annotation.x, annotation.y) == (px[middle], py[middle])
    pd.testing.assert_frame_equal(df, before)


def test_spatial_passes_pre_exclusion_grid_and_post_exclusion_point_index(monkeypatch):
    df = _islands()
    df.loc[0, "Cluster"] = "9"
    seen = {}

    def anchor(x, y, clusters, *, kind, grid_reference):
        seen.update(reference=grid_reference, points=(x, y), clusters=clusters)
        return {"3": LabelAnchor(1, float(x[1]), float(y[1]), 1, "spatial_region")}

    monkeypatch.setattr(spatial, "compute_label_anchors", anchor)
    fig = _spatial(df, exclude_clusters=["9"])
    assert len(seen["reference"][0]) == len(df)
    assert len(seen["points"][0]) == len(df) - 1
    assert "9" not in seen["clusters"]
    assert fig.layout.annotations[0].y == -df.iloc[2].SpatialY


def test_umap_geometry_uses_unrounded_input(monkeypatch):
    df = _islands().astype({"UMAP_1": float, "UMAP_2": float})
    df.loc[0, "UMAP_1"] = 0.123456789
    original = df.copy(deep=True)
    seen = []

    def anchor(x, y, clusters):
        seen.append(float(x.iloc[0]))
        return {"3": LabelAnchor(0, float(x.iloc[0]), float(y.iloc[0]), 1, "umap_region")}

    monkeypatch.setattr(umap, "compute_label_anchors", anchor)
    fig = umap._build_umap_integrated_fig(df, "Cluster", None, False, True)
    assert seen == [0.123456789]
    assert fig.layout.annotations[0].x == 0.123456789
    pd.testing.assert_frame_equal(df, original)


def test_umap_excluding_other_cluster_keeps_original_geometry_and_skips_its_label(monkeypatch):
    df = _islands()
    df.loc[0, "Cluster"] = "9"
    seen = []

    def anchor(x, y, clusters):
        seen.append(set(clusters))
        return {"3": LabelAnchor(1, 2, 2, 25, "umap_region"),
                "9": LabelAnchor(0, 0, 0, 1, "umap_region")}

    monkeypatch.setattr(umap, "compute_label_anchors", anchor)
    integrated = umap._build_umap_integrated_fig(df, "Cluster", None, False, True, exclude_clusters=["9"])
    figures = []
    umap._build_umap_per_sample_graphs(
        pd.concat([df, df.assign(Sample="S2")]), {"3": "red", "9": "blue"}, None, True,
        exclude_clusters=["9"], collect_figures=figures)
    assert seen == [{"3", "9"}] * 3
    assert len(integrated.layout.annotations) == 1
    assert all(len(figure["layout"]["annotations"]) == 1 for _, figure in figures)


@pytest.mark.parametrize("module", [umap, spatial])
def test_labels_off_skips_geometry(monkeypatch, module):
    def unexpected(*args, **kwargs):
        pytest.fail("番号OFF時に領域計算を実行した")

    monkeypatch.setattr(module, "compute_label_anchors", unexpected)
    df = _islands()
    if module is umap:
        umap._build_umap_integrated_fig(df, "Cluster", None, False, False)
        umap._build_umap_per_sample_graphs(
            pd.concat([df, df.assign(Sample="S2")]), {"3": "red"}, None, False)
    else:
        spatial._create_single_spatial_fig(df, {"3": "red"}, None, set(), show_labels=False)


def test_saved_positions_win_and_real_cluster_ids_survive_rename():
    saved = {"3": {"x": 400, "y": 500}}
    scope = dict(rds_path="data.rds", method="Harmony", load_token="loaded",
                 revision=2, section="spatial_merged", sample="S1")
    fig = _spatial(_islands(), saved_positions=saved, cluster_name_map={"3": "腫瘍"},
                   label_scope=scope)
    assert (fig.layout.annotations[0].x, fig.layout.annotations[0].y) == (400, 500)
    assert fig.layout.annotations[0].text == "3: 腫瘍" or "腫瘍" in fig.layout.annotations[0].text
    assert fig.layout.meta["label_scope"] == dict(scope, kind="msi", labels=[{"index": 0, "cluster": "3"}])


@pytest.mark.parametrize("height", [5, 2])
def test_hne_uses_original_cell_anchor_and_cannot_be_mistaken_for_msi(monkeypatch, height):
    df = _islands()
    df = df.loc[df.SpatialY < height]
    monkeypatch.setattr(hne.hp, "load_hne_sample", lambda *args: {
        "image": {"file": "image.png"}, "landmarks": {
            "tic": [[0, 0], [1, 0], [0, 1]], "hne": [[0, 0], [1, 0], [0, 1]]}})
    monkeypatch.setattr(hne, "_load_hne_display_image", lambda *args, **kwargs: (
        "data:image/png;base64,", 0.5, 100, 100))
    monkeypatch.setattr(hne, "msi_to_hne_px", lambda x, y, *args: (100 + 2*x + y, 200 + x + 3*y))
    fig = hne.build_hne_overlay_fig(df, "data.rds", "S1", show_labels=True,
                                    color_map={"3": "red"}, label_scope={"section": "spatial"})
    msi = _spatial(df).layout.annotations[0]
    # 偶数幅で内点候補が同距離でも、Y反転に左右されず同じ観測セルを射影する。
    assert (fig.layout.annotations[0].x, fig.layout.annotations[0].y) == (
        (100 + 2 * msi.x - msi.y) * 0.5, (200 + msi.x - 3 * msi.y) * 0.5)
    assert fig.layout.meta["label_scope"]["kind"] == "hne"
    assert fig.layout.meta["label_scope"]["labels"] == [{"index": 0, "cluster": "3"}]


def test_export_override_uses_merged_scope_instead_of_original_positions():
    fig = umap._build_umap_integrated_fig(_islands(), "Cluster", None, False, True,
        label_scope={"section": "umap_integrated_merged"}).to_dict()
    apply_umap_display_overrides(fig, label_positions={
        "umap_integrated": {"3": {"x": -99, "y": -99}},
        "umap_integrated_merged": {"3": {"x": 7, "y": 8}},
    })
    annotation = fig["layout"]["annotations"][0]
    assert (annotation["x"], annotation["y"]) == (7, 8)


@pytest.mark.parametrize("kind", ["umap", "msi", "hne"])
@pytest.mark.parametrize("changed", [None, "revision", "rds_path", "method", "load_token", "sample", "section"])
def test_export_positions_require_matching_scope_and_reject_hne(kind, changed):
    scope = {"rds_path": "data.rds", "method": "Harmony", "load_token": "loaded", "revision": 2,
             "section": "spatial_merged", "sample": "S1", "kind": kind,
             "labels": [{"index": 0, "cluster": "3"}]}
    positions = {"_label_revision": 2, "_label_context": {
        "rds_path": "data.rds", "method": "Harmony", "load_token": "loaded"},
        "spatial_merged": {"S1": {"3": {"x": 7, "y": 8}}}}
    figure = {"data": [], "layout": {"meta": {"kind": kind, "label_scope": scope},
                                      "annotations": [{"x": 1, "y": 2}]}}
    if changed == "revision":
        positions["_label_revision"] = 1
    elif changed in ("rds_path", "method", "load_token"):
        positions["_label_context"][changed] = "other"
    elif changed:
        scope[changed] = "other"
    apply = apply_umap_display_overrides if kind == "umap" else apply_display_overrides
    apply(figure, label_positions=positions)
    ann = figure["layout"]["annotations"][0]
    assert (ann["x"], ann["y"]) == ((7, 8) if changed is None and kind != "hne" else (1, 2))


def test_spatial_batch_png_applies_dragged_positions_without_changing_server_figure(monkeypatch):
    from app.callbacks import interactive_batch_save as batch
    from app.callbacks import interactive_callbacks as callbacks
    scope = {"rds_path": "data.rds", "method": "Harmony", "load_token": "loaded", "revision": 0,
             "section": "spatial", "sample": "S1"}
    figure = _spatial(_islands(), label_scope=scope).to_dict()
    original = copy.deepcopy(figure)
    monkeypatch.setattr(callbacks, "get_export_figures", lambda *args, **kwargs: [("S1", figure)])
    positions = {"spatial": {"S1": {"3": {"x": 70, "y": 80}}}}
    exported = batch._get_export_figures("spatial", "session", "data.rds", label_positions=positions)
    assert exported[0][1]["layout"]["annotations"][0]["x"] == 70
    assert figure["layout"]["annotations"] == original["layout"]["annotations"]


def test_stale_or_other_dataset_store_cannot_restore_cleared_positions(monkeypatch):
    from app.utils import label_persistence as persistence
    monkeypatch.setattr(persistence, "load_label_positions", lambda *args: {"_label_revision": 2})
    saved = {"umap_integrated": {"3": {"x": 123, "y": 456}}, "_label_revision": 1}
    assert not umap._get_merged_label_positions(saved, "data.rds", "Harmony").get("umap_integrated")
    saved.update(_label_revision=2, _label_context={"rds_path": "other.rds", "method": "Harmony"})
    assert not umap._get_merged_label_positions(saved, "data.rds", "Harmony").get("umap_integrated")
    saved["_label_context"]["rds_path"] = "data.rds"
    assert umap._get_merged_label_positions(saved, "data.rds", "Harmony")["umap_integrated"]["3"]["x"] == 123


@pytest.mark.parametrize("old_token", [None, "old-load"])
def test_reloading_same_dataset_does_not_overlay_previous_load_store(monkeypatch, old_token):
    from app.utils import label_persistence as persistence
    disk = {"_label_revision": 2, "umap_integrated": {"3": {"x": 2, "y": 2}}}
    monkeypatch.setattr(persistence, "load_label_positions", lambda *args: copy.deepcopy(disk))
    context = {"rds_path": "data.rds", "method": "Harmony"}
    if old_token is not None:
        context["load_token"] = old_token
    store = {"_label_revision": 2, "_label_context": context,
             "umap_integrated": {"3": {"x": 99, "y": 99}}}
    result = umap._get_merged_label_positions(store, "data.rds", "Harmony", load_token="new-load")
    assert result["umap_integrated"]["3"] == {"x": 2, "y": 2}
    context["load_token"] = "new-load"
    result = umap._get_merged_label_positions(store, "data.rds", "Harmony", load_token="new-load")
    assert result["umap_integrated"]["3"] == {"x": 99, "y": 99}


def test_pptx_sample_positions_are_separate_from_integrated_and_original():
    from app.callbacks.interactive_pptx import _pptx_label_positions
    positions = {
        "umap_integrated": {"3": {"x": 100}},
        "umap_per_sample": {"S1": {"3": {"x": 200}}},
        "umap_per_sample_merged": {"S1": {"3": {"x": 300}}},
    }
    assert _pptx_label_positions(positions, "umap_per_sample", merged=True, sample="S1")["3"]["x"] == 300
    assert _pptx_label_positions(positions, "umap_per_sample", sample="S2") == {}
