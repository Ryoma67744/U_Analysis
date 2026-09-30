"""★ ver76.0: 自動復帰・表示別保存・旧イベントによる位置復活の回帰試験。"""
import copy
import json

import pytest
import pandas as pd
from dash import no_update
from dash.exceptions import PreventUpdate

from app.utils import label_persistence as lp


@pytest.fixture
def stored(tmp_path):
    rds = str(tmp_path / "result.rds")
    data = {
        "umap_integrated": {"0": {"x": 90, "y": 91}, "1": {"x": 8}},
        "umap_integrated_merged": {"0": {"x": 70, "y": 71}},
        "umap_per_sample": {"S1": {"0": {"x": 30, "y": 31}}},
        "spatial": {"S1": {"0": {"x": 40, "y": 41}},
                    "S2": {"0": {"x": 50, "y": 51}}},
    }
    lp.get_label_positions_path(rds).write_text(json.dumps(data), encoding="utf-8")
    return rds, data


def scope(rds, *, section="umap_integrated", revision=0, kind="umap", sample=None):
    return dict(rds_path=rds, method="Harmony", load_token="loaded-1",
                revision=revision, section=section, kind=kind, sample=sample,
                labels=[{"index": 0, "cluster": "0"}], clusters=["0"])


def test_reset_materializes_legacy_and_keeps_other_positions(stored):
    rds, original = stored
    result = lp.reset_label_positions([scope(rds)], rds, "Harmony", expected_revision=0)
    assert "0" not in result["umap_integrated"]
    assert result["umap_integrated"]["1"] == {"x": 8}
    assert result["umap_integrated_merged"] == original["umap_integrated_merged"]
    assert result["spatial"] == original["spatial"]
    assert result["_label_revision"] == 1
    assert lp.load_label_positions(rds, "Harmony") == result
    assert lp.load_label_positions(rds) == original


def test_reset_removes_partial_coordinate_as_whole_leaf(stored):
    rds, _ = stored
    target = {**scope(rds), "clusters": ["1"]}
    got = lp.reset_label_positions([target], rds, "Harmony")
    assert "1" not in got["umap_integrated"]


def test_sample_reset_preserves_other_sample_and_merged(stored):
    rds, original = stored
    got = lp.reset_label_positions([scope(rds, section="spatial", sample="S1", kind="msi")],
                                  rds, "Harmony")
    assert got["spatial"]["S1"] == {}
    assert got["spatial"]["S2"] == original["spatial"]["S2"]
    assert got["umap_integrated"] == original["umap_integrated"]


def test_empty_method_file_does_not_fall_back_again(tmp_path):
    rds = str(tmp_path / "result.rds")
    lp.get_label_positions_path(rds).write_text('{"umap_integrated":{"0":{"x":1,"y":2}}}')
    got = lp.reset_label_positions([scope(rds)], rds, "Harmony")
    assert lp.get_label_positions_path(rds, "Harmony").exists()
    assert got["umap_integrated"] == {}
    assert lp.load_label_positions(rds, "Harmony")["umap_integrated"] == {}


def test_stale_save_cannot_restore_reset_position(stored):
    rds, _ = stored
    lp.reset_label_positions([scope(rds)], rds, "Harmony", expected_revision=0)
    assert lp.save_label_positions({"umap_integrated": {"0": {"x": 99, "y": 99}}},
                                   rds, "Harmony", expected_revision=0) is False
    assert "0" not in lp.load_label_positions(rds, "Harmony")["umap_integrated"]


def test_current_patch_saves_only_changed_cluster(stored):
    rds, _ = stored
    lp.reset_label_positions([scope(rds)], rds, "Harmony")
    assert lp.save_label_positions({"umap_integrated": {"1": {"x": 2, "y": 3}}},
                                  rds, "Harmony", expected_revision=1)
    assert lp.load_label_positions(rds, "Harmony")["umap_integrated"] == {"1": {"x": 2, "y": 3}}


def test_merged_integrated_is_flat_not_sample_nested(stored):
    rds, _ = stored
    assert lp.save_label_positions({"umap_integrated_merged": {"0": {"x": 7, "y": 8}}},
                                  rds, "Harmony")
    got = lp.load_label_positions(rds, "Harmony")
    assert got["umap_integrated_merged"]["0"] == {"x": 7, "y": 8}
    assert got["umap_integrated"]["0"] == {"x": 90, "y": 91}


def test_stale_reset_is_rejected(stored):
    rds, _ = stored
    lp.reset_label_positions([scope(rds)], rds, "Harmony", expected_revision=0)
    with pytest.raises(ValueError):
        lp.reset_label_positions([scope(rds)], rds, "Harmony", expected_revision=0)
    assert lp.label_revision(lp.load_label_positions(rds, "Harmony")) == 1


def test_reset_failure_preserves_original_file(stored, monkeypatch):
    rds, original = stored
    def fail(*args):
        raise OSError("read only")
    monkeypatch.setattr(lp, "_atomic_write_json", fail)
    with pytest.raises(OSError):
        lp.reset_label_positions([scope(rds)], rds, "Harmony")
    assert lp.load_label_positions(rds, "Harmony") == original


def test_corrupt_file_is_not_replaced_by_reset(stored):
    rds, _ = stored
    path = lp.get_label_positions_path(rds, "Harmony")
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        lp.reset_label_positions([scope(rds)], rds, "Harmony")
    assert path.read_text() == "broken"


@pytest.fixture
def callbacks(stored, monkeypatch):
    from app.callbacks import interactive_fullscreen as fs
    from app.callbacks import interactive_callbacks as shared
    rds, _ = stored
    monkeypatch.setattr(shared, "_set_active_key", lambda value: None)
    monkeypatch.setattr(fs, "_interactive_data", {"rds_path": rds, "method": "Harmony"})
    return fs


@pytest.mark.parametrize("change", [
    {"rds_path": "different.rds"}, {"method": "RPCA"}, {"load_token": "old"},
    {"kind": "hne"}, {"section": "arbitrary"}, {"revision": None},
])
def test_drag_rejects_wrong_scope(stored, callbacks, change):
    rds, original = stored
    signal = {"label_scope": {**scope(rds), **change}, "positions": {"0": {"x": 1, "y": 2}}}
    with pytest.raises(PreventUpdate):
        callbacks._save_scoped_signal(signal, rds, "loaded-1")
    assert lp.load_label_positions(rds, "Harmony") == original


def test_drag_routes_actual_id_to_merged_section(stored, callbacks):
    rds, _ = stored
    signal = {"label_scope": scope(rds, section="umap_integrated_merged"),
              "positions": {"0": {"x": 11, "y": 12}, "display name": {"x": 1, "y": 1}}}
    got = callbacks._save_scoped_signal(signal, rds, "loaded-1")
    assert got["umap_integrated_merged"] == {"0": {"x": 11, "y": 12}}
    assert got["umap_integrated"]["0"] == {"x": 90, "y": 91}
    assert got["_label_context"]["rds_path"] == rds


def test_scope_patch_requires_complete_finite_coordinates(stored, callbacks):
    rds, _ = stored
    assert callbacks._scope_patch(scope(rds), {"0": {"x": 3}}) == {}
    assert callbacks._scope_patch(scope(rds), {"0": {"x": float("nan"), "y": 3}}) == {}


def test_callback_reset_returns_revision_and_cleared_store(stored, callbacks):
    rds, _ = stored
    request = dict(rds_path=rds, load_token="loaded-1", targets=[scope(rds)])
    store, refresh, message, opened = callbacks.reset_visible_label_positions(request, rds, "loaded-1", 4)
    assert "0" not in store["umap_integrated"]
    assert store["_label_revision"] == 1
    assert refresh == 5 and opened and "戻しました" in message
    assert store["_label_context"]["load_token"] == "loaded-1"


def test_callback_reset_rejects_changed_dataset(stored, callbacks):
    rds, _ = stored
    request = dict(rds_path=rds, load_token="old", targets=[scope(rds)])
    with pytest.raises(PreventUpdate):
        callbacks.reset_visible_label_positions(request, rds, "loaded-1")


def test_legacy_and_stale_snapshot_cannot_restore_deleted_position(stored, callbacks, monkeypatch):
    rds, original = stored
    monkeypatch.setattr(callbacks, "_get_label_positions_path", lambda: lp.get_label_positions_path(rds, "Harmony"))
    lp.reset_label_positions([scope(rds)], rds, "Harmony")
    old_store = {**copy.deepcopy(original), "_label_revision": 0,
                 "_label_context": dict(rds_path=rds, method="Harmony", load_token="loaded-1")}
    legacy_snapshot = {"timestamp": "old", "umap_integrated": [{"text": "0", "x": 90, "y": 91}]}
    result = callbacks._do_save_label_positions(old_store, legacy_snapshot)
    assert result[0] is no_update
    assert "0" not in lp.load_label_positions(rds, "Harmony")["umap_integrated"]


def _graphs(node):
    if isinstance(node, (tuple, list)):
        for child in node:
            yield from _graphs(child)
    else:
        if hasattr(node, "figure"):
            yield node
        yield from _graphs(getattr(node, "children", []))


def test_fullscreen_per_sample_keeps_original_points_and_has_distinct_ids(stored, callbacks, monkeypatch):
    from app.callbacks import interactive_umap as umap
    rds, _ = stored
    df = pd.DataFrame({"Sample": ["S1", "S2"], "CellID": ["a", "b"],
                       "Cluster": ["0", "0"], "Cluster_merged": ["5", "5"],
                       "UMAP_1": [0, 1], "UMAP_2": [0, 1],
                       "UMAP_1_merged": [20, 21], "UMAP_2_merged": [30, 31]})
    callbacks._interactive_data["plot_data"] = df
    monkeypatch.setattr(umap, "_with_section_groups", lambda value, *args: value)
    result = callbacks.update_fs_umap(
        display_mode="per_sample", color_by="Cluster", highlight=None, show_labels=True,
        show_legend=True, height_val=60, width_val=90, marker_size=2, exclude_clusters=None,
        label_size=12, legend_hidden=None, custom_color_map=None, rows=0,
        accumulated_positions={}, rds_path=rds, load_token="loaded-1")
    graphs = [g for g in _graphs(result) if isinstance(g.id, dict)]
    assert len(graphs) == 2
    assert all(g.id["type"] == "fs_umap_per_sample_graph" for g in graphs)
    assert all(g.figure.layout.meta["label_scope"]["section"] == "umap_per_sample" for g in graphs)
    assert all(g.figure.layout.annotations[0].text == "0" for g in graphs)
    assert [list(g.figure.data[0].x) for g in graphs] == [[0], [1]]


def test_callback_write_failure_does_not_clear_store(stored, callbacks, monkeypatch):
    rds, original = stored
    def fail(*args, **kwargs):
        raise OSError("read only")
    monkeypatch.setattr(callbacks, "reset_label_positions", fail)
    request = dict(rds_path=rds, load_token="loaded-1", targets=[scope(rds)])
    store, refresh, message, opened = callbacks.reset_visible_label_positions(request, rds, "loaded-1", 4)
    assert store is no_update and refresh is no_update
    assert opened and "保存できません" in message
    assert lp.load_label_positions(rds, "Harmony") == original
