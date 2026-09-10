"""★ ver66.3: 同じ節の二つの描画処理が、互いの再開イベントを奪わない。"""

import inspect
from types import SimpleNamespace

import pandas as pd
import pytest
from dash import no_update


@pytest.fixture
def viewer(monkeypatch):
    import app.callbacks.interactive_callbacks as ic
    import app.callbacks.interactive_umap as um

    ic._accordion_seen.clear()
    frame = pd.DataFrame({
        "CellID": ["a", "b", "c", "d"],
        "Sample": ["S1", "S1", "S2", "S2"],
        "Cluster": ["0", "1", "0", "1"],
        "UMAP_1": [0.0, 1.0, 2.0, 3.0],
        "UMAP_2": [3.0, 2.0, 1.0, 0.0],
    })
    monkeypatch.setattr(ic, "_interactive_data", {"plot_data": frame, "method": "RPCA"})
    monkeypatch.setattr(ic, "_set_active_key", lambda _path: None)
    monkeypatch.setattr(um, "ctx", SimpleNamespace(triggered_id="interactive_accordion"))
    monkeypatch.setattr(um, "_get_merged_label_positions", lambda *a, **kw: {})
    saved = []
    monkeypatch.setattr(ic, "set_export_figures", lambda kind, session, path, figures:
                        saved.append((kind, list(figures))))
    yield um, saved
    ic._accordion_seen.clear()


def _render(um, consumer, mode, opened, names):
    fn = um.update_umap_plot if consumer == "integrated" else um.update_umap_per_sample
    values = {
        "color_by": "Cluster", "highlight_clusters": [], "show_legend": ["show"],
        "show_labels": ["show"], "display_mode": mode, "marker_size": 2,
        "exclude_clusters": [], "label_size": 12, "rds_path": "/synthetic/reopen.rds",
        "_fs_trigger": None, "custom_colors": {}, "cluster_name_map": names,
        "merge_toggle": "original", "merge_color_mode": "shade",
        "active_items": ["acc_umap"] if opened else [], "accumulated_positions": {},
        "session_id": "session-reopen", "name_map": {}, "rows": 0,
        "facet_by": "Sample", "legend_hidden": [], "selection_groups": {},
    }
    kwargs = {name: values[name] for name in inspect.signature(fn).parameters if name in values}
    return fn(**kwargs)


@pytest.mark.parametrize("order", [("integrated", "facet"), ("facet", "integrated")])
@pytest.mark.parametrize("mode,visible", [("integrated", "integrated"), ("per_sample", "facet")])
def test_reopen_reaches_visible_builder_in_either_callback_order(viewer, order, mode, visible):
    """実際の二callbackを呼び、非表示側が先でも最新の名称が画面・保存へ届く。"""
    um, saved = viewer
    for consumer in order:
        _render(um, consumer, mode, True, {"0": "before"})
    for consumer in order:
        assert _render(um, consumer, mode, False, {"0": "after"}) is no_update
    saved.clear()
    results = {consumer: _render(um, consumer, mode, True, {"0": "after"})
               for consumer in order}
    assert results[visible] is not no_update
    if visible == "integrated":
        fig = results[visible].to_plotly_json()
        assert any("after" in str(trace.get("name", "")) for trace in fig["data"])
    else:
        assert saved and saved[-1][0] == "umap" and len(saved[-1][1]) == 2
        assert "after" in str(saved[-1][1])

    # 同じ開状態で別の節を開いた場合は、両方とも重い生成を省く。
    for consumer in order:
        assert _render(um, consumer, mode, True, {"0": "after"}) is no_update


def test_consumer_keys_keep_section_session_and_dataset_isolation():
    import app.callbacks.interactive_callbacks as ic

    ic._accordion_seen.clear()
    try:
        for consumer in ("integrated", "facet"):
            assert ic.accordion_toggle_is_noop(
                "acc_umap", "s1", "/r1", ["acc_umap"], "interactive_accordion",
                consumer=consumer) is False
        for session, path in (("s2", "/r1"), ("s1", "/r2")):
            assert ic.accordion_toggle_is_noop(
                "acc_umap", session, path, ["acc_umap"], "interactive_accordion",
                consumer="integrated") is False
        ic.accordion_record_closed("acc_umap", "s1", "/r1", consumer="integrated")
        assert ic.accordion_toggle_is_noop(
            "acc_umap", "s1", "/r1", ["acc_umap"], "interactive_accordion",
            consumer="integrated") is False
        assert ic.accordion_toggle_is_noop(
            "acc_umap", "s1", "/r1", ["acc_umap"], "interactive_accordion",
            consumer="facet") is True
    finally:
        ic._accordion_seen.clear()
