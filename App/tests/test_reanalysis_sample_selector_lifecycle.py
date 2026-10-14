"""再解析の固定Inputは初期表示・空フォルダでも存在し、選択を取り違えない。"""
from copy import deepcopy

import pytest

from app.callbacks import file_handlers
from app.callbacks.section_callbacks import update_reanalysis_section_selector
from app.services.section_metadata import build_section_manifest, validate_section_manifest


def _components(node):
    if isinstance(node, (list, tuple)):
        for child in node:
            yield from _components(child)
    elif hasattr(node, "to_plotly_json"):
        yield node
        yield from _components(getattr(node, "children", None))


def _selector(folder, method="tims_cluster_filter", **kwargs):
    desi, tims = (method, None) if method.startswith("desi") else (None, method)
    return file_handlers.update_reanalysis_sample_selector(folder, desi, tims, **kwargs)


def _saved_source(tmp_path):
    folder = str(tmp_path / "moved-originals")
    paths = [str(tmp_path / directory / "same.parquet") for directory in ("one", "two")]
    manifest = build_section_manifest([
        {"path": path, "available_rois": ["ROI1", "ROI2"], "roi_role": "section"}
        for path in paths
    ])
    manifest.update(source_rds_path=str(tmp_path / "result" / "source.rds"),
                    source_data_folder=folder)
    source = {"manifest": manifest, "data_folder": folder, "rds_path": manifest["source_rds_path"]}
    return source, paths


def test_initial_layout_contains_one_reanalysis_input_even_when_panel_is_hidden(monkeypatch):
    from app.layouts import settings_tab

    monkeypatch.setattr(settings_tab, "load_last_settings", lambda: {})
    monkeypatch.setattr(settings_tab, "_cal_preset_options", lambda: [])
    components = list(_components(settings_tab._create_analysis_settings_subtab()))
    selectors = [c for c in components if getattr(c, "id", None) == "selected_samples_reanalysis"]
    assert len(selectors) == 1
    assert selectors[0].options == []
    assert selectors[0].value == []
    assert len([c for c in components
                if getattr(c, "id", None) == "sample_selector_reanalysis_message"]) == 1
    panel = next(c for c in components if getattr(c, "id", None) == "reanalysis_settings_panel")
    assert panel.style == {"display": "none"}


@pytest.mark.parametrize("folder_kind", ["unset", "blank", "missing", "file", "empty", "unsupported"])
def test_unavailable_folder_clears_options_and_selection(tmp_path, folder_kind):
    folder = tmp_path / "data"
    if folder_kind in ("empty", "unsupported"):
        folder.mkdir()
        if folder_kind == "unsupported":
            (folder / "notes.md").write_text("Not analysis data", encoding="utf-8")
    elif folder_kind == "file":
        folder.write_text("Not a directory", encoding="utf-8")
    value = None if folder_kind == "unset" else "" if folder_kind == "blank" else str(folder)

    options, selected, style, message = _selector(value)

    assert options == []
    assert selected == []  # Never None: an empty selection must not mean all files.
    assert style == {"display": "none"}
    expected = "対応ファイルが見つかりません" if folder_kind in ("empty", "unsupported") else "データフォルダを指定してください"
    assert message.children == expected


@pytest.mark.parametrize("method,extension", [("desi_cluster_filter", ".txt"),
                                               ("tims_cluster_filter", ".parquet")])
def test_valid_to_empty_to_valid_restores_only_current_candidates(tmp_path, method, extension):
    first, empty, second = [tmp_path / name for name in ("first", "empty", "second")]
    for folder in (first, empty, second):
        folder.mkdir()
    (first / ("old" + extension)).touch()
    (second / ("new" + extension)).touch()

    assert _selector(str(first), method)[1] == ["old"]
    assert _selector(str(empty), method)[:2] == ([], [])
    options, selected, style, message = _selector(str(second), method)
    assert options == [{"label": "new", "value": "new"}]
    assert selected == ["new"]
    assert style == {}
    assert message == []


@pytest.mark.parametrize("use_previous", [False, True])
def test_saved_source_survives_missing_folder_and_same_named_files(tmp_path, monkeypatch, use_previous):
    source, paths = _saved_source(tmp_path)
    before = deepcopy(source)

    def no_folder_scan(*args):
        pytest.fail("Saved source must be used before checking or scanning its old folder")

    monkeypatch.setattr(file_handlers, "list_tims_files", no_folder_scan)
    previous = source["manifest"] if use_previous else None
    actual_source = None if use_previous else source
    options, selected, style, message = _selector(
        source["data_folder"], source=actual_source,
        rds_path=source["rds_path"], previous=previous)
    assert selected == ["same"]
    assert options == [{"label": "same", "value": "same"}]
    assert style == {"display": "none"}
    assert "2 ファイル" in message.children

    catalog, _ = update_reanalysis_section_selector(
        selected, source["data_folder"], None, "tims_cluster_filter",
        source=actual_source, rds_path=source["rds_path"], previous=previous)
    assert {item["path"] for item in catalog} == set(paths)
    assert len({item["file_id"] for item in catalog}) == 2
    assert source == before


def test_saved_source_does_not_restore_excluded_files_or_rois(tmp_path):
    source, paths = _saved_source(tmp_path)
    manifest = source["manifest"]
    manifest["files"][0]["rois"] = ["ROI1"]
    manifest["files"][0]["sections"] = manifest["files"][0]["sections"][:1]
    manifest["files"][1]["selection_mode"] = "none"
    selected = _selector(source["data_folder"], source=source, rds_path=source["rds_path"])[1]

    catalog, _ = update_reanalysis_section_selector(
        selected, source["data_folder"], None, "tims_cluster_filter",
        source=source, rds_path=source["rds_path"])
    assert [item["path"] for item in catalog] == paths[:1]
    assert catalog[0]["available_rois"] == ["ROI1"]


def test_unrelated_source_does_not_override_new_folder(tmp_path):
    source, _ = _saved_source(tmp_path)
    folder = tmp_path / "new"
    folder.mkdir()
    (folder / "current.parquet").touch()

    _, selected, style, message = _selector(str(folder), source=source, rds_path=source["rds_path"])

    assert selected == ["current"]
    assert style == {}
    assert message == []


def test_explicit_empty_selection_blocks_execution_instead_of_restoring_previous_files(tmp_path):
    previous = build_section_manifest([
        {"path": str(tmp_path / "old.parquet"), "available_rois": [], "roi_role": "region"}
    ])
    catalog, _ = update_reanalysis_section_selector(
        [], str(tmp_path), None, "tims_cluster_filter", previous=previous)
    current = build_section_manifest(catalog, previous=previous)

    assert catalog == []
    assert current["files"] == []
    assert "解析対象の切片／ROIを選択してください。" in validate_section_manifest(current)


def test_saved_source_with_no_selected_files_remains_empty(tmp_path):
    source, _ = _saved_source(tmp_path)
    for item in source["manifest"]["files"]:
        item["selection_mode"] = "none"

    options, selected, style, message = _selector(
        source["data_folder"], source=source, rds_path=source["rds_path"])
    catalog, _ = update_reanalysis_section_selector(
        selected, source["data_folder"], None, "tims_cluster_filter",
        source=source, rds_path=source["rds_path"])

    assert options == []
    assert selected == []
    assert style == {"display": "none"}
    assert "0 ファイル" in message.children
    assert catalog == []
