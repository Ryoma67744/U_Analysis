"""Public browsing must never replace fixed internal project/asset identities."""
from copy import deepcopy
from types import SimpleNamespace

import pytest


@pytest.fixture
def roots(monkeypatch, tmp_path):
    from app import config
    for name in ("TIMS", "DESI", "OUTPUT"):
        monkeypatch.delenv(f"MSI_HOST_{name}_DATA_DIR", raising=False)
    monkeypatch.delenv("MSI_HOST_IMZML_ASSETS_DIR", raising=False)
    tims = tmp_path / "tims"
    assets = tmp_path / "assets"
    tims.mkdir()
    assets.mkdir()
    monkeypatch.setattr(config, "TIMS_DATA_DIR", tims)
    monkeypatch.setattr(config, "IMZML_CACHE_DIR", assets)
    monkeypatch.setenv("MSI_HOST_TIMS_DATA_DIR", "/srv/msi/tims_data")
    monkeypatch.setenv("MSI_HOST_IMZML_ASSETS_DIR", "/srv/msi/imzml_assets")
    return tims, assets


def test_virtual_parent_and_mapped_root_keep_filesystem_identity(roots):
    from app.layouts.file_browser_modal import browser_directory, browser_parent
    tims, _ = roots
    source = tims / "日本語 sample.imzML"
    source.write_bytes(b"fixture")
    current, items = browser_directory("/srv/msi")
    assert current == "/srv/msi"
    assert {r["path"] for r in items} == {"/srv/msi/tims_data", "/srv/msi/imzml_assets"}
    current, items = browser_directory("/srv/msi/tims_data")
    assert current == str(tims)
    assert items[0]["path"] == str(source)
    assert browser_parent(current) == "/srv/msi"
    assert browser_parent("/srv/msi/tims_data") == "/srv/msi"
    assert browser_directory(str(tims)) == (current, items)


def test_reference_shows_public_path_but_handoff_retains_internal(roots, monkeypatch):
    from app.callbacks import file_handlers as fh
    tims, _ = roots
    source = tims / "one.imzML"
    source.write_bytes(b"fixture")
    state = {"current_dir": str(tims), "mode": "file", "caller_id": "imzml_batch_browse_selection"}
    monkeypatch.setattr(fh, "ctx", SimpleNamespace(triggered_id="fb_go_btn"))
    items, _, shown = fh.update_file_browser(state, None, 1, "/srv/msi/tims_data")
    assert shown == "/srv/msi/tims_data"
    assert any(item.id["path"] == str(source) for item in items)
    monkeypatch.setattr(fh, "ctx", SimpleNamespace(triggered_id={"type": "fb_item", "path": str(source)}))
    selected, label = fh.handle_fb_item_click([1], state)
    assert label == "/srv/msi/tims_data/one.imzML"
    assert selected["selected_path"] == str(source)
    outputs = fh.apply_file_browser_selection(1, selected)
    assert outputs[fh._ALL_TARGET_IDS.index("imzml_batch_browse_selection")]["path"] == str(source)
    monkeypatch.setattr(fh, "ctx", SimpleNamespace(triggered_id={"type": "fb_item", "path": "/srv/msi"}))
    parent, label = fh.handle_fb_item_click([1], selected)
    assert parent["selected_path"] == label == ""
    assert fh.file_browser_selection_disabled(parent)


def test_completed_output_and_existing_handoff_do_not_mutate_descriptors(roots, monkeypatch):
    from app.callbacks import imzml_registration_callbacks as ui
    tims, assets = roots
    runtime = str(assets / "old-project" / "revision" / "source.parquet")
    descriptor = {"runtime_path": runtime, "conversion_key": "old-key"}
    original = deepcopy(descriptor)
    monkeypatch.setattr(ui, "get_display_name", lambda: "tester")
    monkeypatch.setattr(ui.queue, "completed_assets", lambda *a: {
        "old-key": {"source": str(tims / "source.imzML"), "entry": descriptor}})
    output = ui.registration_output_paths([{"value": "old-key"}], [], {"id": "old-project"})
    code, copy = output[1].children
    assert code.children == copy.content == "/srv/msi/imzml_assets/old-project/revision/source.parquet"
    assert descriptor == original
    fixed = {"paths": [runtime], "folders": [str(assets / "old-project" / "revision")],
             "manifest": {"files": [descriptor]}, "id": "old"}
    monkeypatch.setattr(ui.queue, "handoff_assets", lambda *a: fixed)
    selected = ui.registration_handoff(1, ["old-key"], {"id": "old-project"})
    assert selected[0] == fixed
    assert selected[1] == fixed["folders"][0]
    assert selected[8]["files"][0]["runtime_path"] == runtime


def test_registration_browser_accepts_both_namespaces_and_virtual_ancestors(roots, monkeypatch):
    from app.callbacks import imzml_registration_callbacks as ui
    tims, _ = roots
    (tims / "source.imzML").write_bytes(b"fixture")
    (tims / "source.ibd").write_bytes(b"fixture")
    public = ui.browse_registration_files("/srv/msi/tims_data", 1)
    internal = ui.browse_registration_files(str(tims), 1)
    assert public == internal
    assert public[1][0]["value"] == str(tims / "source.imzML")
    assert public[3].startswith("/srv/msi/tims_data")
    ancestors = ui.browse_registration_files("/srv/msi", 1)
    assert {f["value"] for f in ancestors[0]} == {"/srv/msi/tims_data", "/srv/msi/imzml_assets"}
    monkeypatch.setattr(ui, "ctx", SimpleNamespace(triggered_id="imzml_batch_up"))
    assert ui.navigate_registration_folder(1, None, str(tims)) == "/srv/msi"
    monkeypatch.setattr(ui, "ctx", SimpleNamespace(triggered_id="imzml_batch_subfolder"))
    assert ui.navigate_registration_folder(1, "/srv/msi/tims_data", "/srv/msi") == str(tims)
