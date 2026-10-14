"""★ ver78.0: 一画面登録で古い選択・未完成資産が解析へ混ざらないことを検査する。"""
from copy import deepcopy
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from dash import no_update


@pytest.fixture
def ui(monkeypatch):
    import dash
    monkeypatch.setattr(dash, "callback", lambda *a, **k: lambda fn: fn)
    module = importlib.import_module("app.callbacks.imzml_registration_callbacks")
    monkeypatch.setattr(module, "get_display_name", lambda: "tester")
    return module


def _walk(component):
    if isinstance(component, (list, tuple)):
        for child in component:
            yield from _walk(child)
    elif hasattr(component, "to_plotly_json"):
        yield component
        yield from _walk(getattr(component, "children", None))


def test_new_and_legacy_layout_ids_are_unique_and_defaults_separate(monkeypatch):
    from app.services import session_manager
    from app.layouts.imzml_io_modal import create_imzml_io_modal
    monkeypatch.setattr(session_manager, "load_last_settings", lambda: {})
    nodes = list(_walk(create_imzml_io_modal()))
    ids = [n.id for n in nodes if isinstance(getattr(n, "id", None), str)]
    assert len(ids) == len(set(ids))
    by_id = {n.id: n for n in nodes if isinstance(getattr(n, "id", None), str)}
    assert by_id["imzml_batch_ppm"].value == 0
    assert by_id["imzml_alignment_ppm"].value == 5
    assert "imzml_spatial_overrides_registration" in by_id
    assert "imzml_spatial_overrides_conversion" in by_id
    assert by_id["imzml_batch_table"].data == []


def test_saved_ppm_is_restored_per_owner_without_changing_legacy(ui, monkeypatch, tmp_path):
    monkeypatch.setattr(ui.queue, "queue_root", lambda: tmp_path)
    ui.queue.registration_preferences("tester", ppm=5)
    assert ui.restore_registration_ppm(True) == 5
    assert ui.queue.registration_preferences("other")["alignment_ppm"] == 0
    assert ui.restore_registration_ppm(False) is no_update


def test_browser_shows_server_files_and_disables_missing_pair(ui, tmp_path):
    (tmp_path / "a.imzML").write_text("fixture")
    (tmp_path / "a.ibd").write_bytes(b"fixture")
    (tmp_path / "b.imzML").write_text("fixture")
    (tmp_path / "child").mkdir()
    folders, options, selected, message = ui.browse_registration_files(str(tmp_path), 1)
    assert folders == [{"label": "child", "value": str(tmp_path / "child")}]
    assert {Path(o["value"]).name: o["disabled"] for o in options} == {"a.imzML": False, "b.imzML": True}
    assert selected == []
    assert str(tmp_path) in message


def test_add_preserves_same_name_different_folder_and_deduplicates_exact(ui, tmp_path):
    a, b = [str(tmp_path / name / "same.imzML") for name in ("one", "two")]
    options, paths = ui.add_registration_files(1, [a, b], [a])
    assert paths == [a, b]
    assert [o["label"] for o in options] == paths


def test_paste_keeps_identity_and_requires_all_rows(ui):
    rows = [{"section_id": "s1", "file_path": "/a", "file": "a"},
            {"section_id": "s2", "file_path": "/b", "file": "b"}]
    result = ui.paste_registration(rows, "Slice 1\tMouse1\tControl\nSlice 2\tMouse1\tControl")
    assert result[1]["section_id"] == "s2"
    assert result[1]["subject_id"] == "Mouse1"
    assert "subject_id" not in rows[0]
    with pytest.raises(ValueError, match="2 切片"):
        ui.paste_registration(rows, "wrong\trow")


def test_stale_inspection_cannot_populate_new_selection(ui):
    result = ui.render_registration_inspection({"id": "old"}, {"id": "new"}, {})
    assert result[0] == []
    assert result[1] == {}


def test_poll_does_not_replace_edited_table_of_same_revision(ui, monkeypatch):
    draft = {"id": "generation", "revision": "r1", "state": "ready"}
    monkeypatch.setattr(ui.queue, "get_draft", lambda *args: deepcopy(draft))
    result = ui.render_registration_inspection(draft, {"id": "generation"},
                                               {"id": "generation", "revision": "r1"})
    assert all(value is no_update for value in result)


def test_spatial_edit_applies_only_active_file_and_preserves_table_edits(ui, monkeypatch):
    captured = []
    monkeypatch.setattr(ui.queue, "revise_draft", lambda *args, **kwargs: captured.append((args, kwargs)) or "updated")
    overrides = {"/a": {"revision": "old"}, "/b": {"revision": "new"}}
    rows = [{"file_path": "/a", "section_id": "a1", "subject_id": "edited"}]
    assert ui.apply_registration_spatial_edits(overrides, {"id": "g"},
        {"id": "g", "revision": "r"}, "/b", rows) == ("updated", no_update)
    assert captured[0][0][-1] == {"/b": overrides["/b"]}
    assert captured[0][1]["rows"] == rows
    assert ui.apply_registration_spatial_edits(overrides, {"id": "new"},
        {"id": "old", "revision": "r"}, "/b", rows) == (no_update, no_update)


def test_stale_spatial_revision_shows_message_without_replacing_table(ui, monkeypatch):
    monkeypatch.setattr(ui.queue, "revise_draft",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("現在の表を開き直してください。")))
    data, message = ui.apply_registration_spatial_edits({"/a": {}}, {"id": "g"},
                                                      {"id": "g", "revision": "old"}, "/a", [])
    assert data is no_update
    assert "現在の表" in message.children


def test_handoff_clears_resume_and_uses_fresh_exact_manifest(ui, monkeypatch):
    selected = {"id": "new", "paths": ["/cache/a.parquet"], "folders": ["/cache"],
                "manifest": {"files": [{"path": "/cache/a.parquet"}]}}
    monkeypatch.setattr(ui.queue, "handoff_assets", lambda *args: selected)
    outputs = ui.registration_handoff(1, ["key"], {"id": "p"})
    assert outputs[:5] == (selected, "/cache", [], None, "tims_v8")
    assert outputs[5:8] == (False, False, True)
    assert outputs[8] == selected["manifest"]
    assert outputs[9:11] == ("settings", False)


def test_handoff_failure_does_not_change_current_analysis(ui, monkeypatch):
    monkeypatch.setattr(ui.queue, "handoff_assets", lambda *args: (_ for _ in ()).throw(ValueError("未完成")))
    outputs = ui.registration_handoff(1, ["key"], {})
    assert all(value is no_update for value in outputs[:-1])


def test_registered_paths_do_not_scan_or_add_raw_and_other_revisions(tmp_path, monkeypatch):
    from app.callbacks import file_handlers as fh
    from dash import dcc
    parquet = tmp_path / "same.parquet"
    parquet.write_bytes(b"fixture")
    (tmp_path / "same.imzML").write_bytes(b"fixture")
    (tmp_path / "unselected.parquet").write_bytes(b"fixture")
    monkeypatch.setattr(fh, "list_tims_folder_groups", lambda *args: pytest.fail("managed handoff must not scan"))
    selection = {"paths": [str(parquet)], "folders": [str(tmp_path)], "project_id": "p"}
    children = fh.update_sample_selector(str(tmp_path), None, "tims_v8", [],
                                        registered_selection=selection, project={"id": "p"})
    checklist = next(n for n in _walk(children) if isinstance(getattr(n, "id", None), dict))
    assert [o["value"] for o in checklist.options] == [str(parquet)]
    assert checklist.value == [str(parquet)]


def test_shared_inspection_cache_invalidates_for_ibd_change(tmp_path, monkeypatch):
    from app.services import imzml_input_inspection as cache
    from app.services import imzml_validation as validation, imzml_spatial_layout as spatial
    raw = tmp_path / "a.imzML"
    raw.write_text("fixture")
    binary = raw.with_suffix(".ibd")
    binary.write_bytes(b"one")
    calls = []
    monkeypatch.setattr(validation, "inspect_spectral_preflight", lambda p: calls.append(p) or {"status": "ok"})
    monkeypatch.setattr(spatial, "inspect_imzml_spatial_layout", lambda p: {"coordinate_hash": "c", "components": []})
    cache._inspect.cache_clear()
    first = cache.inspect_selected_input(raw)
    first["spatial_layout"]["coordinate_hash"] = "edited"
    assert cache.inspect_selected_input(raw)["spatial_layout"]["coordinate_hash"] == "c"
    assert len(calls) == 1
    binary.write_bytes(b"different content")
    cache.inspect_selected_input(raw)
    assert len(calls) == 2
