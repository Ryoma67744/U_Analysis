"""★ ver66.3: 軽いUMAP外観変更でも画面・保存・画素対応の契約を保つ。"""
import copy
import inspect
import json

import pandas as pd
import pytest
from plotly.utils import PlotlyJSONEncoder

from app.callbacks import interactive_umap as um
from app.callbacks import interactive_batch_save as bs
from app.utils.display_helpers import apply_umap_display_overrides


def _df():
    return pd.DataFrame({
        "CellID": ["a1", "a2", "a3", "b1", "b2", "b3"],
        "Sample": ["A"] * 3 + ["B"] * 3,
        "Cluster": ["0", "1", "1", "0", "1", "2"],
        "UMAP_1": [0.1, 0.2, 0.3, 1.1, 1.2, 1.3],
        "UMAP_2": [1.1, 1.2, 1.3, 2.1, 2.2, 2.3],
    })


def _json(value):
    return json.dumps(value, cls=PlotlyJSONEncoder, sort_keys=True)


def _figures(kind, marker, label, highlight):
    df = _df()
    original = df.copy(deep=True)
    figures = []
    if kind == "integrated":
        figures = [("integrated", um._build_umap_integrated_fig(
            df, "Cluster", highlight, True, True,
            marker_size=marker, label_size=label).to_dict())]
    elif kind == "sample":
        um._build_umap_per_sample_graphs(
            df, {"0": "red", "1": "blue", "2": "green"}, highlight, True,
            marker_size=marker, label_size=label, collect_figures=figures)
    else:
        um._build_umap_facet_graphs(
            df, [("one", {"a1", "b2"}), ("two", {"a2", "b3"})],
            {"0": "red", "1": "blue", "2": "green"},
            marker_size=marker, collect_figures=figures)
    pd.testing.assert_frame_equal(df, original)
    return figures


@pytest.mark.parametrize("kind", ["integrated", "sample", "facet"])
@pytest.mark.parametrize("highlight", [None, ["1"]])
@pytest.mark.parametrize("before,after", [(1, 5), (7, 1)])
def test_overrides_equal_fresh_build_and_keep_input_values(kind, highlight, before, after):
    """下限1の背景規則を含め、従来どおり再構築した図と一致する。"""
    before_figures = _figures(kind, before, 11, highlight)
    expected = _figures(kind, after, 19, highlight)
    for (_, figure), (_, reference) in zip(before_figures, expected):
        before_data = copy.deepcopy(figure["data"])
        apply_umap_display_overrides(figure, marker_size=after, label_size=19)
        # JSONの整数/浮動小数点表記差は表示値の差ではない。許容誤差は使わない。
        assert json.loads(_json(figure)) == json.loads(_json(reference))
        for old, updated in zip(before_data, figure["data"]):
            for field in ("x", "y", "text", "meta", "hovertemplate", "name", "uid"):
                assert _json(old.get(field)) == _json(updated.get(field))


def test_label_only_changes_keep_dragged_coordinates_and_fixed_roles():
    figure = _figures("integrated", 2, 14, ["1"])[0][1]
    figure["layout"]["annotations"][0].update(x=99.5, y=-13.25)
    before = copy.deepcopy(figure)
    apply_umap_display_overrides(figure, marker_size=8, label_size=25)
    assert figure["layout"]["annotations"][0]["x"] == 99.5
    assert figure["layout"]["annotations"][0]["y"] == -13.25
    assert figure["data"][-1]["marker"]["size"] == 7
    assert before["data"][-1] == figure["data"][-1]
    assert [tr["marker"]["size"] for tr in figure["data"][:-1]] == [7, 9]
    sample = _figures("sample", 2, 14, ["1"])[0][1]
    apply_umap_display_overrides(sample, marker_size=8)
    fixed = [sample["data"][rule["index"]]["marker"]["size"]
             for rule in sample["layout"]["meta"]["umap_style"]["markers"]
             if rule["role"] == "legend"]
    assert fixed and set(fixed) == {10}


@pytest.mark.parametrize("kind", ["integrated", "sample"])
def test_export_restores_dragged_positions_by_real_ids(kind):
    fig = _figures(kind, 2, 11, None)[0][1]
    positions = {
        "umap_integrated": {"1": {"x": 44.5, "y": -8}},
        "umap_per_sample": {"A": {"1": {"x": 81, "y": -9}}},
    }
    apply_umap_display_overrides(fig, marker_size=7, label_size=20,
                                 label_positions=positions)
    rule = next(rule for rule in fig["layout"]["meta"]["umap_style"]["labels"]
                if rule["cluster"] == "1")
    ann = fig["layout"]["annotations"][rule["index"]]
    assert (ann["x"], ann["y"]) == ((44.5, -8) if kind == "integrated" else (81, -9))


@pytest.mark.parametrize("kind", ["integrated", "sample"])
@pytest.mark.parametrize("action", ["zip", "thumbnail"])
def test_save_click_uses_current_sizes_without_mutating_cached_figure(monkeypatch, kind, action):
    from app.callbacks import interactive_callbacks as ic
    source = _figures(kind, 2, 11, ["1"])
    original = _json(source)
    positions = {"umap_integrated": {"1": {"x": 45, "y": -7}},
                 "umap_per_sample": {"A": {"1": {"x": 70, "y": -8}},
                                     "B": {"1": {"x": 80, "y": -9}}}}
    monkeypatch.setattr(ic, "get_export_figures", lambda *args: source if kind == "sample" else [])
    monkeypatch.setattr(bs, "_conditions_for", lambda *args: {})
    captured = []
    if action == "zip":
        monkeypatch.setattr(bs, "_create_zip_from_figures",
                            lambda figures, **kwargs: captured.extend(figures) or b"ZIP")
        bs.cb_batch_save_umap(1, source[0][1],
                             "per_sample" if kind == "sample" else "integrated",
                             "session", "/tmp/data.rds", 6, 23, positions)
    else:
        monkeypatch.setattr(bs, "_save_figure_as_thumbnail",
                            lambda figures, *args: (captured.extend(figures) or True, "ok"))
        bs.cb_set_thumbnail_umap(1, source[0][1],
                                "per_sample" if kind == "sample" else "integrated",
                                "project", 0, "session", "/tmp/data.rds", 6, 23, positions)
    assert captured
    for _, fig in captured:
        assert all(ann["font"]["size"] == 23 for ann in fig["layout"]["annotations"])
        style = fig["layout"]["meta"]["umap_style"]
        expected_position = (positions["umap_integrated"] if kind == "integrated" else
                             positions["umap_per_sample"][style["sample"]])["1"]
        label_index = next(rule["index"] for rule in style["labels"] if rule["cluster"] == "1")
        annotation = fig["layout"]["annotations"][label_index]
        assert {axis: annotation[axis] for axis in ("x", "y")} == expected_position
        for rule in fig["layout"]["meta"]["umap_style"]["markers"]:
            size = fig["data"][rule["index"]]["marker"]["size"]
            if rule["role"] in ("point", "highlight", "background"):
                assert size == max(1, 6 + rule["delta"])
    assert _json(source) == original


def test_size_controls_do_not_trigger_full_server_rebuilds():
    """修正を戻すと失敗する: 更新処理の入力に巨大図再構築の起点を残さない。"""
    for function in (um.update_umap_plot, um.update_umap_per_sample):
        source = inspect.getsource(function)
        for control in ("umap_marker_size", "umap_label_size"):
            assert f'State("{control}", "value")' in source
            assert f'Input("{control}", "value")' not in source


@pytest.fixture
def isolated_exports(monkeypatch):
    from collections import OrderedDict
    from app.callbacks import interactive_callbacks as ic
    monkeypatch.setattr(ic, "_export_figures", OrderedDict())
    monkeypatch.setattr(ic, "_export_figures_time", {})


def test_two_tabs_keep_their_own_umap_export_values(monkeypatch, isolated_exports):
    """同cookie/RDSでも、他タブのハイライトや除外の図を保存しない。"""
    from app.callbacks import interactive_callbacks as ic
    first = _figures("sample", 2, 11, ["1"])
    second = _figures("sample", 3, 12, ["0"])
    ic.set_export_figures("umap", "same-session", "/same.rds", first, view_id="tab-a")
    ic.set_export_figures("umap", "same-session", "/same.rds", second, view_id="tab-b")
    captured = []
    monkeypatch.setattr(bs, "_conditions_for", lambda *args: {})
    monkeypatch.setattr(bs, "_create_zip_from_figures",
                        lambda figs, **kwargs: captured.extend(figs) or b"ZIP")
    bs.cb_batch_save_umap(1, {}, "per_sample", "same-session", "/same.rds",
                         8, 20, {}, view_id="tab-a")
    assert captured
    for (_, figure), (_, original) in zip(captured, first):
        for actual, expected in zip(figure["data"], original["data"]):
            assert _json(actual.get("x")) == _json(expected.get("x"))
            assert _json(actual.get("y")) == _json(expected.get("y"))
    assert ic.get_export_figures("umap", "same-session", "/same.rds", view_id="tab-b") is second


def test_umap_export_never_falls_back_to_another_tab_when_expired(monkeypatch, isolated_exports):
    from app.callbacks import interactive_callbacks as ic
    ic.set_export_figures("umap", "expired-session", "/expired.rds",
                          _figures("sample", 2, 11, ["1"]), view_id="other-tab")
    captured = []
    monkeypatch.setattr(bs, "_create_zip_from_figures",
                        lambda figs, **kwargs: captured.extend(figs) or b"ZIP")
    result = bs.cb_batch_save_umap(1, {}, "per_sample", "expired-session", "/expired.rds",
                                  8, 20, {}, view_id="expired-tab")
    assert not captured
    assert result[0] is bs.no_update


def test_client_updates_handle_late_mount_and_late_figures():
    """実Plotlyの代わりに非同期イベント順序を固定する独立契約試験。"""
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.jsがないためJS非同期契約試験を実行できない")
    result = subprocess.run(
        [node, str(Path(__file__).parent / "js" / "umap_restyle_contract.js")],
        capture_output=True, text=True, timeout=15, check=True)
    assert json.loads(result.stdout)["passed"] is True
