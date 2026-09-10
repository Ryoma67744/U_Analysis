"""★ ver66.3: m/z幾何再利用の値・世代・保存・上限を実データ構造で検証する。"""

from collections import OrderedDict
from types import SimpleNamespace
from weakref import WeakValueDictionary
import gc
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pytest
from dash import no_update

from app.callbacks import interactive_callbacks as IC
from app.callbacks import interactive_deg as ID
from app.utils import feature_geometry as FG


def graph_ids(node):
    found = []
    if isinstance(getattr(node, "id", None), dict) and node.id.get("type") == "feature_graph":
        found.append(node.id)
    children = getattr(node, "children", [])
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        found.extend(graph_ids(child))
    return found


def operations(patch):
    return {tuple(op["location"]): op["params"]["value"] for op in patch._operations}


@pytest.fixture
def scene(tmp_path, monkeypatch):
    x, y = np.meshgrid(np.arange(4), np.arange(3))
    n = x.size
    df = pd.DataFrame({
        "Sample": np.repeat(["WT_liver", "liver"], n),
        "CellID": [f"cell_{i}" for i in range(2*n)],
        "SpatialX": np.tile(x.ravel(), 2).astype(float),
        "SpatialY": np.tile(y.ravel(), 2).astype(float),
        "TotalCount": np.arange(2*n, dtype=float) + 40,
    })
    values = np.arange(2*n, dtype=float)
    pd.DataFrame({"CellID": df.CellID, "mz1": values, "mz2": values[::-1]}).to_parquet(
        tmp_path / "expression_matrix.parquet")
    rds = str(tmp_path / "input.rds")
    IC._set_active_key(rds)
    IC._interactive_data["plot_data"] = df
    monkeypatch.setattr(IC._bridge, "ensure_expression_matrix", lambda *args: None)
    state = SimpleNamespace(df=df, rds=rds, cache=str(tmp_path), values=values,
                            rotation={}, ids=[], session="geometry-test", view=None)

    def build(feature="mz1", names=None):
        monkeypatch.setattr(ID, "ctx", SimpleNamespace(triggered_id="feature_sample_select"))
        children, *_ = ID.update_feature_plot(
            feature, None, None, None, names or {}, 0, 0, False,
            "Plasma", [], rds, str(tmp_path), state.rotation, None, state.session, state.view)
        state.ids = graph_ids(children)
        assert len(state.ids) == 2, children
        return children

    def patch(feature="mz2", ids=None, names=None):
        ids = state.ids if ids is None else ids
        monkeypatch.setattr(ID, "ctx", SimpleNamespace(triggered_id="feature_select", outputs_list=[
            [{"id": oid, "property": "figure"} for oid in ids],
            [{"id": oid, "property": "config"} for oid in ids]]))
        return ID.patch_feature_intensity(feature, None, None, False, None, names or {},
                                          rds, str(tmp_path), state.session, state.rotation, state.view)

    state.build, state.patch = build, patch
    yield state
    IC._drop_state(rds)


@pytest.mark.parametrize("angle,flip_h,flip_v", [
    (0, False, False), (90, False, False), (180, True, False),
    (270, False, True), (0, True, True), (37, False, False)])
def test_warm_patch_matches_cold_geometry_and_reuses_it(scene, monkeypatch, angle, flip_h, flip_v):
    scene.rotation = {"__all__": {"angle": angle, "flip_h": flip_h, "flip_v": flip_v}}
    scene.build()
    calls = []
    original = ID._feature_grid_index
    monkeypatch.setattr(ID, "_feature_grid_index", lambda *a: (calls.append(1), original(*a))[1])
    first, _ = scene.patch()
    second, _ = scene.patch("mz1")
    assert len(calls) == 2  # 不規則格子のNoneも次m/zでは再計算しない。
    for i, oid in enumerate(scene.ids):
        rows = np.flatnonzero(scene.df.Sample.to_numpy() == oid["index"])
        vals = scene.values[rows]
        color, alpha, _ = ID._feature_intensity_style(vals, 0, len(scene.values)-1)
        gi = original(scene.df.iloc[rows], scene.rotation, oid["index"])
        got = operations(second[i])
        if gi is None:
            np.testing.assert_array_equal(got[("data", -1, "marker", "color")], color)
        else:
            expected = ID._raster.fill_grid(ID._raster.grid_shape(gi), gi[0], gi[1],
                                           np.where(alpha > 0, color, np.nan))
            np.testing.assert_array_equal(got[("data", -1, "z")], expected)
    assert all(p is not no_update for p in first)


def test_snapshot_is_fixed_at_publication_even_if_source_is_mutated(scene):
    publication = IC.get_feature_dataset(scene.rds)
    before = FG.feature_dataset_frame(publication).copy(deep=True)
    scene.df.loc[0, ["SpatialX", "TotalCount", "CellID"]] = [999, 999, "changed"]
    pd.testing.assert_frame_equal(FG.feature_dataset_frame(publication), before)
    for values in publication.columns.values():
        assert not values.flags.writeable
    with pytest.raises(ValueError):
        publication.columns["SpatialX"][0] = 22


def test_new_read_and_reordered_rows_reject_old_shell(scene):
    scene.build()
    old_ids = list(scene.ids)
    replacement = scene.df.iloc[::-1].reset_index(drop=True)
    IC._interactive_data["plot_data"] = replacement
    figures, configs = scene.patch(ids=old_ids)
    assert figures == [no_update] * 2 and configs == [no_update] * 2
    # 同じ集合・同じ行数でも順序が違えば新規キャッシュへの位置代入も拒否する。
    assert ID._expression_alignment_ok(scene.cache, replacement) is False


def test_reordered_new_dataset_uses_new_cellid_order(scene):
    scene.build()
    scene.patch()
    replacement = scene.df.sample(frac=1, random_state=5).reset_index(drop=True)
    by_cell = dict(zip(scene.df.CellID, scene.values))
    ordered = replacement.CellID.map(by_cell).to_numpy()
    pd.DataFrame({"CellID": replacement.CellID, "mz1": ordered,
                  "mz2": ordered[::-1]}).to_parquet(
        os.path.join(scene.cache, "expression_matrix.parquet"))
    IC._interactive_data["plot_data"] = replacement
    scene.df = replacement
    scene.build()
    scene.patch("mz1")
    actual, _ = scene.patch("mz1")
    for i, oid in enumerate(scene.ids):
        rows = np.flatnonzero(replacement.Sample.to_numpy() == oid["index"])
        gi = ID._feature_grid_index(replacement.iloc[rows], {}, oid["index"])
        color, alpha, _ = ID._feature_intensity_style(ordered[rows], 0, len(ordered)-1)
        expected = ID._raster.fill_grid(ID._raster.grid_shape(gi), gi[0], gi[1],
                                       np.where(alpha > 0, color, np.nan))
        np.testing.assert_array_equal(operations(actual[i])[("data", -1, "z")], expected)


@pytest.mark.parametrize("change", ["missing", "duplicate", "irregular"])
def test_nonstandard_coordinates_keep_original_raster_decision(scene, change):
    frame = scene.df.copy(deep=True)
    if change == "missing":
        frame.loc[0, "SpatialX"] = np.nan
    elif change == "duplicate":
        frame.loc[0, ["SpatialX", "SpatialY"]] = frame.loc[1, ["SpatialX", "SpatialY"]]
    else:
        frame.loc[0, "SpatialX"] = 0.123
    IC._interactive_data["plot_data"] = frame
    scene.build()
    figures, _ = scene.patch()
    for i, oid in enumerate(scene.ids):
        df_s = frame[frame.Sample == oid["index"]]
        expected_grid = ID._feature_grid_index(df_s, {}, oid["index"])
        locations = operations(figures[i])
        assert (("data", -1, "z") in locations) == (expected_grid is not None)


def test_changed_rotation_rejects_old_shell(scene):
    scene.build()
    scene.rotation = {"__all__": {"angle": 90}}
    figures, configs = scene.patch()
    assert figures == [no_update] * 2 and configs == [no_update] * 2


def test_request_cannot_borrow_new_rds_or_new_revision(scene):
    publication = IC.get_feature_dataset(scene.rds)
    request = {"view_id": "tab", "rds_path": scene.rds,
               "dataset_revision": publication.revision}
    assert ID._feature_request_matches_dataset(request, scene.rds)
    assert not ID._feature_request_matches_dataset(request, "/other.rds")
    IC._interactive_data["plot_data"] = scene.df.copy(deep=True)
    assert not ID._feature_request_matches_dataset(request, scene.rds)
    request["dataset_revision"] = IC.get_feature_dataset(scene.rds).revision
    assert ID._feature_request_matches_dataset(request, scene.rds)


def test_reload_during_expression_read_cannot_publish_old_patch_or_export(scene, monkeypatch):
    scene.build()
    original_exports = IC.get_export_figures("feature", scene.session, scene.rds)
    original = IC._bridge.get_feature_expression_fast

    def delayed(cache, feature):
        result = original(cache, feature)
        IC._interactive_data["plot_data"] = scene.df.copy(deep=True)
        return result

    monkeypatch.setattr(IC._bridge, "get_feature_expression_fast", delayed)
    figures, configs = scene.patch()
    assert figures == [no_update] * 2 and configs == [no_update] * 2
    assert IC.get_export_figures("feature", scene.session, scene.rds) is original_exports


def test_parquet_replaced_during_read_rejects_patch(scene, monkeypatch):
    scene.build()
    original = IC._bridge.get_feature_expression_fast

    def replaced(cache, feature):
        result = original(cache, feature)
        path = os.path.join(scene.cache, "expression_matrix.parquet")
        replacement = pd.read_parquet(path)
        replacement[feature] = replacement[feature] + 1000
        replacement.to_parquet(path + ".new")
        os.replace(path + ".new", path)
        return result

    monkeypatch.setattr(IC._bridge, "get_feature_expression_fast", replaced)
    figures, configs = scene.patch()
    assert figures == [no_update] * 2 and configs == [no_update] * 2


def test_unknown_alignment_uses_original_calculation(scene, monkeypatch):
    scene.build()
    monkeypatch.setattr(IC._bridge, "expression_row_order_matches", lambda *args: None)
    monkeypatch.setattr(ID, "cached_feature_grid", lambda *args: pytest.fail("判定不能を再利用した"))
    figures, _ = scene.patch()
    assert all(p is not no_update for p in figures)


def test_export_uses_real_sample_id_and_keeps_previous_arrays_unchanged(scene):
    scene.build(names={"WT_liver": "same", "liver": "same"})
    before = IC.get_export_figures("feature", scene.session, scene.rds)
    before_z = [np.array(f[1]["data"][-1]["z"], copy=True) for f in before]
    figures, _ = scene.patch(names={"WT_liver": "same", "liver": "same"})
    after = IC.get_export_figures("feature", scene.session, scene.rds)
    for i, (_name, fd) in enumerate(after):
        screen = operations(figures[i])[("data", -1, "z")]
        np.testing.assert_array_equal(fd["data"][-1]["z"], screen)
        assert not np.shares_memory(fd["data"][-1]["z"], screen)
        np.testing.assert_array_equal(before[i][1]["data"][-1]["z"], before_z[i])
    saved_z = [np.array(f[1]["data"][-1]["z"], copy=True) for f in after]
    scene.patch("mz1")
    for i, (_name, fd) in enumerate(after):
        np.testing.assert_array_equal(fd["data"][-1]["z"], saved_z[i])


def test_global_budget_includes_state_owned_snapshots(monkeypatch):
    monkeypatch.setattr(FG, "_CACHE", OrderedDict())
    monkeypatch.setattr(FG, "_DATASETS", WeakValueDictionary())
    monkeypatch.setattr(FG, "_GEOMETRIES", WeakValueDictionary())
    monkeypatch.setattr(FG, "_MAX_BYTES", 1500)
    df = pd.DataFrame({"Sample": ["s"]*20, "CellID": list(map(str, range(20))),
                       "SpatialX": np.arange(20.), "SpatialY": np.arange(20.)})
    first = FG.new_feature_dataset(df)
    second = FG.new_feature_dataset(df)
    assert first.columns and second.columns
    third = FG.new_feature_dataset(df)
    assert not third.columns  # 古いstateが持つsnapshotを未解放として数える。
    assert FG.feature_geometry_cache_info()["retained_bytes"] <= 1500
    assert FG.get_feature_geometry(third) is None
    del first
    gc.collect()
    fourth = FG.new_feature_dataset(df)
    assert fourth.columns
    assert FG.feature_geometry_cache_info()["retained_bytes"] <= 1500


def test_budget_counts_multiple_inflight_geometries_for_one_revision(monkeypatch):
    monkeypatch.setattr(FG, "_CACHE", OrderedDict())
    monkeypatch.setattr(FG, "_DATASETS", WeakValueDictionary())
    monkeypatch.setattr(FG, "_GEOMETRIES", WeakValueDictionary())
    monkeypatch.setattr(FG, "_MAX_BYTES", 1200)
    frame = pd.DataFrame({"Sample": ["s"]*20, "CellID": list(map(str, range(20))),
                          "SpatialX": np.arange(20.), "SpatialY": np.arange(20.)})
    dataset = FG.new_feature_dataset(frame)
    inflight = []
    for _ in range(5):
        geometry = FG.get_feature_geometry(dataset)
        if geometry is not None:
            inflight.append(geometry)
        FG.forget_feature_geometry(dataset)
    expected = sum(v.nbytes for v in dataset.columns.values()) + sum(g.nbytes for g in inflight)
    assert FG.feature_geometry_cache_info()["retained_bytes"] == expected
    assert expected <= 1200


@pytest.mark.parametrize("finishes_first", ["old", "new"])
def test_latest_browser_request_wins_both_completion_orders(scene, monkeypatch, finishes_first):
    scene.view = "one-tab"
    scene.build()
    local = threading.local()
    barrier = threading.Barrier(2)
    release_later = threading.Event()
    original_get = IC.get_export_figures
    original_style = ID._feature_intensity_style
    outputs = [[{"id": oid, "property": prop} for oid in scene.ids]
               for prop in ("figure", "config")]
    monkeypatch.setattr(ID, "ctx", SimpleNamespace(outputs_list=outputs, triggered_id="feature_select"))
    entered_old = threading.Event()
    real_read = IC._bridge.get_feature_expression_fast

    def read(*args):
        if local.role == "old":
            entered_old.set()
        else:
            assert entered_old.wait(5)
        return real_read(*args)

    def same_prior(*args, **kwargs):
        result = original_get(*args, **kwargs)
        if getattr(local, "role", None):
            barrier.wait(timeout=5)
        return result

    def ordered_completion(*args):
        if local.role != finishes_first:
            assert release_later.wait(5)
        return original_style(*args)

    monkeypatch.setattr(IC._bridge, "get_feature_expression_fast", read)
    monkeypatch.setattr(IC, "get_export_figures", same_prior)
    monkeypatch.setattr(ID, "_feature_intensity_style", ordered_completion)

    def run(role, sequence, feature):
        local.role = role
        return ID.patch_feature_intensity(feature, None, None, False, None, {}, scene.rds,
                                          scene.cache, scene.session, {}, scene.view, sequence)

    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(run, "old", 1, "mz1")
        assert entered_old.wait(5)
        new = pool.submit(run, "new", 2, "mz2")
        first, later = (old, new) if finishes_first == "old" else (new, old)
        first.result(timeout=5)
        release_later.set()
        later.result(timeout=5)
        assert old.result()[0] == [no_update] * 2
        assert all(f is not no_update for f in new.result()[0])
    saved = original_get("feature", scene.session, scene.rds, scene.view)
    assert all("mz2" in name for name, _fd in saved)


def test_two_tabs_keep_independent_feature_exports(scene, monkeypatch):
    scene.view = "tab-A"
    scene.build("mz1")
    ids_a = list(scene.ids)
    scene.view = "tab-B"
    scene.build("mz2")
    ids_b = list(scene.ids)
    local = threading.local()
    barrier = threading.Barrier(2)
    original_read = IC._bridge.get_feature_expression_fast

    class RequestContext:
        @property
        def outputs_list(self):
            return [[{"id": oid, "property": prop} for oid in local.ids]
                    for prop in ("figure", "config")]

    monkeypatch.setattr(ID, "ctx", RequestContext())

    def concurrent_read(*args):
        barrier.wait(timeout=5)
        return original_read(*args)

    monkeypatch.setattr(IC._bridge, "get_feature_expression_fast", concurrent_read)

    def run(view, ids, feature):
        local.ids = ids
        return ID.patch_feature_intensity(feature, None, None, False, None, {}, scene.rds,
                                          scene.cache, scene.session, {}, view, 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, "tab-A", ids_a, "mz2")
        b = pool.submit(run, "tab-B", ids_b, "mz1")
        assert all(f is not no_update for f in a.result(timeout=5)[0])
        assert all(f is not no_update for f in b.result(timeout=5)[0])
    assert all("mz2" in name for name, _fd in IC.get_export_figures("feature", scene.session, scene.rds, "tab-A"))
    assert all("mz1" in name for name, _fd in IC.get_export_figures("feature", scene.session, scene.rds, "tab-B"))


@pytest.mark.parametrize("latest_kind", ["full", "patch"])
def test_delayed_full_shell_cannot_overwrite_newer_full_or_patch(scene, monkeypatch, latest_kind):
    scene.view = "shell-tab"
    scene.build()
    prior_ids = list(scene.ids)
    reached_finish = threading.Event()
    release_old = threading.Event()
    local = threading.local()
    original_publish = IC.set_feature_exports_if_current
    outputs = [[{"id": oid, "property": prop} for oid in prior_ids]
               for prop in ("figure", "config")]
    monkeypatch.setattr(ID, "ctx", SimpleNamespace(triggered_id="feature_select", outputs_list=outputs))

    def delayed_publish(*args, **kwargs):
        if getattr(local, "old", False):
            reached_finish.set()
            assert release_old.wait(5)
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(IC, "set_feature_exports_if_current", delayed_publish)

    def full(feature, seq, old=False):
        local.old = old
        return ID.update_feature_plot(
            feature, None, None, None, {}, 0, 0, False, "Plasma", [],
            scene.rds, scene.cache, {}, None, scene.session, scene.view,
            seq, "feature_select")

    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(full, "mz1", 1, True)
        assert reached_finish.wait(5)
        if latest_kind == "full":
            latest = pool.submit(full, "mz2", 2)
            assert latest.result(timeout=5)[0] is not no_update
        else:
            latest = pool.submit(ID.patch_feature_intensity,
                                 "mz2", None, None, False, None, {}, scene.rds,
                                 scene.cache, scene.session, {}, scene.view, 2)
            assert all(p is not no_update for p in latest.result(timeout=5)[0])
        release_old.set()
        assert old.result(timeout=5)[0] is no_update
    assert all("mz2" in name for name, _fd in IC.get_export_figures(
        "feature", scene.session, scene.rds, scene.view))


def test_same_size_mtime_replacement_invalidates_parquet_cache(scene):
    from app.services.seurat_bridge import _parquet_file_sig
    path = os.path.join(scene.cache, "expression_matrix.parquet")
    before = _parquet_file_sig(__import__("pathlib").Path(path))
    st = os.stat(path)
    with open(path, "rb") as stream:
        content = stream.read()
    with open(path + ".new", "wb") as stream:
        stream.write(content)
    os.utime(path + ".new", ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(path + ".new", path)
    after = _parquet_file_sig(__import__("pathlib").Path(path))
    assert before[:3] == after[:3]
    assert before != after
