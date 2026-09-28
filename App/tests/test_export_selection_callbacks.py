"""★ ver75.1: 数値出力の対象がUI遷移で黙って広がらないことを検証する。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.callbacks import export_selection_callbacks as callbacks


@pytest.fixture
def catalog():
    return {"scope": "/result/A", "signature": "revision-1", "units": [
        {"id": "ctrl1", "label": "切片1", "sample": "S1", "section_id": "ctrl1",
         "group": "Ctrl", "subject_id": "C1", "pixels": 4, "methods": ["Harmony"]},
        {"id": "ctrl2", "label": "切片2", "sample": "S2", "section_id": "ctrl2",
         "group": "Ctrl", "subject_id": "C1", "pixels": 5, "methods": ["Harmony"]},
        {"id": "ko1", "label": "切片3", "sample": "S3", "section_id": "ko1",
         "group": "KO1", "subject_id": "K1", "pixels": 6, "methods": ["Harmony"]},
        {"id": "ko2", "label": "切片4", "sample": "S4", "section_id": "ko2",
         "group": "KO2", "subject_id": "K2", "pixels": 7, "methods": ["Harmony"]},
    ]}


def run(monkeypatch, catalog, previous=None, *, trigger="data_export_selection_mode",
        mode="selected", checked=None, groups=None, subjects=None):
    monkeypatch.setattr(callbacks, "ctx", SimpleNamespace(triggered_id=trigger))
    return callbacks.update_export_selection(mode, catalog, checked, 0, 0, 0, 0, 0, 0,
                                             groups, subjects, previous)


def test_ctrl_ko2_group_choice_then_individual_section(monkeypatch, catalog):
    first = run(monkeypatch, catalog)
    assert first[7]["ids"] == ["ctrl1", "ctrl2", "ko1", "ko2"]
    assert first[11] is True  # 未解析除外checkboxは選択モードで無効
    chosen = run(monkeypatch, catalog, first[7], trigger="data_export_group_remove", groups=["KO1"])
    assert chosen[7]["ids"] == ["ctrl1", "ctrl2", "ko2"]
    assert "16解析済み画素" in chosen[8]
    assert "Ctrl、KO2" in chosen[8]
    assert "独立試料 2" in chosen[8]
    refined = run(monkeypatch, catalog, chosen[7], trigger="data_export_units", checked=["ctrl1", "ko2"])
    assert refined[7]["ids"] == ["ctrl1", "ko2"]
    assert "11解析済み画素" in refined[8]


def test_clear_group_add_subject_remove_are_one_id_set(monkeypatch, catalog):
    first = run(monkeypatch, catalog)
    cleared = run(monkeypatch, catalog, first[7], trigger="data_export_select_none")
    assert cleared[7]["mode"] == "selected"
    assert cleared[7]["ids"] == [] and "0件" in cleared[9]
    added = run(monkeypatch, catalog, cleared[7], trigger="data_export_group_add", groups=["Ctrl", "KO2"])
    assert added[7]["ids"] == ["ctrl1", "ctrl2", "ko2"]
    removed = run(monkeypatch, catalog, added[7], trigger="data_export_subject_remove", subjects=["C1"])
    assert removed[7]["ids"] == ["ko2"]
    restored = run(monkeypatch, catalog, removed[7], trigger="data_export_subject_add", subjects=["C1"])
    assert restored[7]["ids"] == ["ctrl1", "ctrl2", "ko2"]


def test_empty_does_not_reset_on_mode_toggle_or_metadata_refresh(monkeypatch, catalog):
    first = run(monkeypatch, catalog)
    empty = run(monkeypatch, catalog, first[7], trigger="data_export_units", checked=[])
    all_mode = run(monkeypatch, catalog, empty[7], mode="all")
    assert all_mode[11] is False
    assert run(monkeypatch, catalog, all_mode[7])[7]["ids"] == []
    changed = deepcopy(catalog)
    changed["signature"] = "revision-2"
    changed["units"][0]["group"] = "Control"
    assert run(monkeypatch, changed, empty[7], trigger="data_export_catalog")[7]["ids"] == []


def test_same_result_metadata_refresh_keeps_stable_ids(monkeypatch, catalog):
    first = run(monkeypatch, catalog)
    chosen = run(monkeypatch, catalog, first[7], trigger="data_export_units", checked=["ctrl1", "ko2"])
    changed = deepcopy(catalog)
    changed["signature"] = "revision-2"
    changed["units"][0]["group"] = "Control renamed"
    result = run(monkeypatch, changed, chosen[7], trigger="data_export_catalog")
    assert result[7]["ids"] == ["ctrl1", "ko2"]
    assert result[7]["signature"] == "revision-2"
    assert "Control renamed" in result[0][0]["label"]


def test_scope_change_clears_selection_and_bulk_picks(monkeypatch, catalog):
    first = run(monkeypatch, catalog)
    changed = {**catalog, "scope": "/result/B", "signature": "revision-B"}
    result = run(monkeypatch, changed, first[7], trigger="data_export_catalog")
    assert result[7]["mode"] == "selected"
    assert result[7]["ids"] == [] and "0件" in result[9]
    assert result[13:] == ([], [])


def test_result_change_during_first_catalog_load_does_not_select_everything(monkeypatch, catalog):
    loading = {"scope": catalog["scope"], "signature": None, "units": [], "error": "読込中"}
    first = run(monkeypatch, loading)
    assert first[7]["initialized"] is False
    changed = {**catalog, "scope": "/result/B", "signature": "revision-B"}
    result = run(monkeypatch, changed, first[7], trigger="data_export_catalog")
    assert result[7]["ids"] == []
    assert "0件" in result[9]


def test_reload_disables_payload_but_keeps_same_result_draft(monkeypatch, catalog):
    first = run(monkeypatch, catalog)
    chosen = run(monkeypatch, catalog, first[7], trigger="data_export_units", checked=["ko2"])
    loading = {"scope": catalog["scope"], "signature": None, "units": [], "error": "読込中"}
    paused = run(monkeypatch, loading, chosen[7], trigger="data_export_catalog")
    assert paused[7]["signature"] is None and paused[7]["ids"] == ["ko2"]
    assert paused[4:7] == (True, True, True)
    assert paused[9] == "読込中"
    resumed = run(monkeypatch, catalog, paused[7], trigger="data_export_catalog")
    assert resumed[7]["ids"] == ["ko2"]


def test_removed_ids_are_not_replaced_with_all(monkeypatch, catalog):
    first = run(monkeypatch, catalog)
    chosen = run(monkeypatch, catalog, first[7], trigger="data_export_units", checked=["ko2"])
    changed = {**catalog, "units": catalog["units"][:-1], "signature": "revision-2"}
    result = run(monkeypatch, changed, chosen[7], trigger="data_export_catalog")
    assert result[7]["ids"] == [] and "0件" in result[9]


def test_loading_never_calls_catalog_loader(monkeypatch):
    result = callbacks.refresh_export_catalog("old.rds", {}, "Harmony", ["Harmony"],
                                              "/result/B", 0, {"display": "block"})
    assert result["scope"] == "/result/B"
    assert result["signature"] is None and "読み込み中" in result["error"]


def test_all_mode_does_not_load_other_method_data(monkeypatch):
    from app.callbacks import interactive_data_export
    def unexpected(*_args):
        pytest.fail("全体出力のままで追加のRDS抽出を行わない")
    monkeypatch.setattr(interactive_data_export, "load_export_catalog", unexpected, raising=False)
    result = callbacks.refresh_export_catalog("current.rds", {"Harmony": "current.rds"},
        "Harmony", ["Harmony"], "/result/A", 0, {"display": "none"}, "all")
    assert result["signature"] is None


def test_load_success_and_mismatch_are_explicit(monkeypatch, catalog):
    from app.callbacks import interactive_data_export
    calls = []
    def load(*args):
        calls.append(args)
        return catalog
    monkeypatch.setattr(interactive_data_export, "load_export_catalog", load, raising=False)
    args = ("current.rds", {"Harmony": "current.rds"}, "Harmony", ["Harmony"], "/result/A", 2,
            {"display": "none"})
    assert callbacks.refresh_export_catalog(*args) == catalog
    assert calls == [args[:5]]
    def mismatch(*_args):
        raise ValueError("読み込んだ結果が選択中の結果と一致しません")
    monkeypatch.setattr(interactive_data_export, "load_export_catalog", mismatch)
    invalid = callbacks.refresh_export_catalog(*args)
    assert invalid["signature"] is None and "一致しません" in invalid["error"]


def test_panel_stores_and_controls_are_present_outside_desi_column_options():
    from app.layouts.interactive_tab import create_export_selection_panel
    def collect(component):
        found = {component.id: component} if getattr(component, "id", None) else {}
        children = getattr(component, "children", None)
        for child in (children if isinstance(children, (list, tuple)) else [children]):
            if hasattr(child, "to_plotly_json"):
                found.update(collect(child))
        return found
    components = collect(create_export_selection_panel())
    assert components["data_export_selection"].data["mode"] == "all"
    assert components["data_export_units"].value == []
    assert "data_export_format_wrapper" not in components
    assert "data_export_options_wrapper" not in components
