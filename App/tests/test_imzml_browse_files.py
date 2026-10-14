"""参照で選んだ原本だけを登録へ渡し、他の入力や形式を混ぜない。"""
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from dash import no_update


@pytest.fixture
def browser(monkeypatch):
    module = import_module("app.callbacks.file_handlers")
    monkeypatch.setattr(module, "get_available_drives", lambda: [])
    return module


def _open(browser, monkeypatch, button, values, folder, action="import"):
    monkeypatch.setattr(browser, "ctx", SimpleNamespace(triggered_id=button))
    return browser.open_file_browser(
        *[1 if key == button else 0 for key in browser._BROWSE_BUTTONS], [], {},
        *[values.get(key) for key in browser._ALL_TARGET_IDS], [], str(folder), action)


def test_batch_reference_starts_from_current_folder_then_last_file(browser, monkeypatch, tmp_path):
    current = tmp_path / "current"
    current.mkdir()
    prior = tmp_path / "previous"
    prior.mkdir()
    opened, state, _, _ = _open(browser, monkeypatch, "browse_imzml_batch_file", {}, current)
    assert opened and Path(state["current_dir"]) == current
    assert state["selected_path"] == ""
    assert browser.file_browser_selection_disabled(state)
    _, state, _, _ = _open(browser, monkeypatch, "browse_imzml_batch_file", {
        "imzml_batch_browse_selection": {"path": str(prior / "last.imzML"), "selection_id": "old"}
    }, current)
    assert Path(state["current_dir"]) == prior


@pytest.mark.parametrize("caller,action,expected", [
    ("imzml_batch_browse_selection", "import", {"sample.imzML"}),
    ("imzml_source", "import", {"sample.imzML"}),
    ("imzml_source", "export", {"sample.parquet"}),
    ("annotation_path", "import", {"sample.imzML", "sample.parquet", "sample.ibd", "notes.csv"}),
])
def test_reference_filters_only_conversion_inputs(browser, monkeypatch, tmp_path, caller, action, expected):
    for name in ("sample.imzML", "sample.parquet", "sample.ibd", "notes.csv"):
        (tmp_path / name).write_text("fixture")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.setattr(browser, "ctx", SimpleNamespace(triggered_id="fb_state"))
    items, _, _ = browser.update_file_browser({
        "current_dir": str(tmp_path), "mode": "file", "caller_id": caller, "imzml_action": action
    }, None, None, None)
    paths = [Path(item.id["path"]) for item in items if isinstance(getattr(item, "id", None), dict)]
    assert {p.name for p in paths if p.is_file()} == expected
    assert child in paths


def test_batch_selection_is_a_repeatable_event_and_keeps_other_targets_unchanged(browser, tmp_path):
    path = tmp_path / "sample.IMZML"
    path.write_text("fixture")
    state = {"caller_id": "imzml_batch_browse_selection", "selected_path": str(path)}
    index = browser._ALL_TARGET_IDS.index(state["caller_id"])
    first = browser.apply_file_browser_selection(1, state)
    second = browser.apply_file_browser_selection(2, state)
    assert first[index]["path"] == second[index]["path"] == str(path)
    assert first[index]["selection_id"] != second[index]["selection_id"]
    assert first[-1] is False
    assert all(value is no_update for i, value in enumerate(first[:-1]) if i != index)


@pytest.mark.parametrize("name,is_file", [("wrong.ibd", True), ("missing.imzML", False), ("folder.imzML", False)])
def test_non_source_selection_cannot_replace_input(browser, tmp_path, name, is_file):
    path = tmp_path / name
    if is_file:
        path.write_bytes(b"fixture")
    elif name.startswith("folder"):
        path.mkdir()
    state = {"caller_id": "imzml_source", "imzml_action": "import", "selected_path": str(path)}
    assert browser.file_browser_selection_disabled(state)
    assert all(value is no_update for value in browser.apply_file_browser_selection(1, state))


def test_legacy_export_reference_updates_only_input(browser, monkeypatch, tmp_path):
    path = tmp_path / "converted.parquet"
    path.write_bytes(b"fixture")
    opened, state, _, _ = _open(browser, monkeypatch, "browse_imzml_source", {
        "imzml_source": str(path)
    }, tmp_path, "export")
    assert opened and state["imzml_action"] == "export"
    state["selected_path"] = str(path)
    results = browser.apply_file_browser_selection(1, state)
    index = browser._ALL_TARGET_IDS.index("imzml_source")
    assert results[index] == str(path)
    assert results[-1] is False
    assert all(value is no_update for i, value in enumerate(results[:-1]) if i != index)


def test_navigation_clears_previous_file_selection(browser, monkeypatch, tmp_path):
    folder = tmp_path / "next"
    folder.mkdir()
    state = {"caller_id": "imzml_batch_browse_selection", "mode": "file",
             "selected_path": str(tmp_path / "old.imzML")}
    monkeypatch.setattr(browser, "ctx", SimpleNamespace(triggered_id={"type": "fb_item", "path": str(folder)}))
    changed, label = browser.handle_fb_item_click([1], state)
    assert changed["current_dir"] == str(folder)
    assert changed["selected_path"] == label == ""
    assert browser.file_browser_selection_disabled(changed)


def test_shortcut_clears_selection_and_display_together(browser, monkeypatch, tmp_path):
    state = {"caller_id": "imzml_batch_browse_selection", "mode": "file",
             "selected_path": str(tmp_path / "old.imzML")}
    monkeypatch.setattr(browser, "ctx", SimpleNamespace(triggered_id={"type": "fb_shortcut", "path": str(tmp_path)}))
    changed, label = browser.handle_fb_shortcut([1], state)
    assert changed["current_dir"] == str(tmp_path)
    assert changed["selected_path"] == label == ""
    assert browser.file_browser_selection_disabled(changed)


def test_file_selection_keeps_folder_reached_by_path_input(browser, monkeypatch, tmp_path):
    folder = tmp_path / "new-folder"
    folder.mkdir()
    source = folder / "sample.imzML"
    source.write_text("fixture")
    state = {"caller_id": "imzml_batch_browse_selection", "mode": "file",
             "current_dir": str(tmp_path), "selected_path": ""}
    monkeypatch.setattr(browser, "ctx", SimpleNamespace(triggered_id={"type": "fb_item", "path": str(source)}))
    changed, label = browser.handle_fb_item_click([1], state)
    assert changed["current_dir"] == str(folder)
    assert changed["selected_path"] == label == str(source)
