"""★ ver74.0: 実callbackの確認・適用・再表示を検査する。"""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import dash
import pytest
from app.services.imzml_spatial_layout import build_spatial_layout, default_spatial_sections
from app.services.section_metadata import build_section_manifest

@pytest.fixture
def editor(monkeypatch, tmp_path):
    captured = []
    def callback(*args, **kwargs):
        def decorate(function):
            captured.append(function)
            return function
        return decorate
    monkeypatch.setattr(dash, "callback", callback)
    source = Path(__file__).resolve().parents[1] / "app/callbacks/imzml_spatial_callbacks.py"
    spec = importlib.util.spec_from_file_location("imzml_editor_transition_probe", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    layout = build_spatial_layout([(0, 0, 1), (1, 0, 1), (10, 0, 1), (11, 0, 1)], include_preview=False)
    path = str(tmp_path / "example.imzML")
    catalog = [{"path": path, "file_id": "file_a", "spatial_layout": layout,
                "spatial_sections": default_spatial_sections("file_a", layout),
                "available_rois": [], "roi_role": "spatial"}]
    manifest = build_section_manifest(catalog)
    functions = {scope: {name: [fn for fn in captured if fn.__name__ == name][index]
                         for name in ("open_panel", "act_on_panel")}
                 for index, scope in enumerate(("initial", "reanalysis", "conversion"))}
    return SimpleNamespace(module=module, catalog=catalog, manifest=manifest, path=path,
                           functions=functions, layout=layout)

def _suffix(scope):
    return "" if scope == "initial" else "_" + scope

def _open(editor, scope, overrides=None):
    trigger = {"type": "imzml_spatial_open", "scope": scope, "index": editor.path}
    editor.module.ctx = SimpleNamespace(triggered_id=trigger, triggered=[{"value": 1}])
    return editor.functions[scope]["open_panel"]([1], [trigger], editor.catalog,
                                                 editor.manifest, overrides or {})

def _act(editor, scope, action, draft, table, *, base=None, overrides=None, selected=None):
    editor.module.ctx = SimpleNamespace(triggered_id="imzml_spatial_" + action + _suffix(scope))
    return editor.functions[scope]["act_on_panel"](
        *([0] * 10), editor.path, draft, table, selected or [], None,
        overrides or {}, editor.catalog, editor.manifest, "imzml-spatial-float", base)

@pytest.mark.parametrize("scope", ["initial", "reanalysis", "conversion"])
def test_edited_values_can_be_confirmed_then_applied(editor, scope):
    opened = _open(editor, scope)
    table = deepcopy(opened[5])
    for index, row in enumerate(table):
        row.update(section_display_name=f"新名称{index}", subject_id=f"M{index}", group="Ctrl")
    confirmed = _act(editor, scope, "confirm_all", opened[3], table, base=opened[9])
    assert all(row["metadata_confirmed"] for row in confirmed[0])
    applied = _act(editor, scope, "apply", confirmed[0], confirmed[2], base=opened[9])
    assert applied[5] == {"display": "none"}
    rows = applied[4][editor.path]["registered_sections"]
    assert [(row["section_display_name"], row["subject_id"], row["group"], row["metadata_confirmed"])
            for row in rows] == [(f"新名称{i}", f"M{i}", "Ctrl", True) for i in range(2)]

@pytest.mark.parametrize("scope", ["initial", "reanalysis", "conversion"])
def test_edit_after_confirmation_requires_another_confirmation(editor, scope):
    opened = _open(editor, scope)
    table = deepcopy(opened[5])
    for index, row in enumerate(table):
        row.update(subject_id=f"M{index}", group="Ctrl")
    confirmed = _act(editor, scope, "confirm_all", opened[3], table, base=opened[9])
    edited = deepcopy(confirmed[2]); edited[0]["group"] = "KO"
    blocked = _act(editor, scope, "apply", confirmed[0], edited, base=opened[9])
    assert blocked[4] is dash.no_update
    assert "登録確認" in blocked[7]
    reconfirmed = _act(editor, scope, "confirm_all", confirmed[0], edited, base=opened[9])
    applied = _act(editor, scope, "apply", reconfirmed[0], reconfirmed[2], base=opened[9])
    assert applied[4][editor.path]["registered_sections"][0]["group"] == "KO"

def test_conversion_reopen_and_execution_spec_keep_applied_values(editor):
    opened = _open(editor, "conversion")
    table = deepcopy(opened[5])
    for index, row in enumerate(table):
        row.update(section_display_name=f"保存名{index}", subject_id=f"M{index}", group="G")
    confirmed = _act(editor, "conversion", "confirm_all", opened[3], table, base=opened[9])
    applied = _act(editor, "conversion", "apply", confirmed[0], confirmed[2], base=opened[9])
    reopened = _open(editor, "conversion", applied[4])
    assert reopened[5] == applied[2]
    from app.callbacks.imzml_io_callbacks import _registration_from_conversion_state
    registration = _registration_from_conversion_state(
        editor.path, editor.catalog, editor.manifest, applied[4])
    assert {row["section_display_name"] for row in registration["section_registry"]} == {"保存名0", "保存名1"}
    assert all(row["metadata_confirmed"] for row in registration["section_registry"])

def test_background_change_blocks_stale_editor_apply(editor):
    opened = _open(editor, "initial")
    table = deepcopy(opened[5])
    for index, row in enumerate(table): row.update(subject_id=f"M{index}", group="G")
    confirmed = _act(editor, "initial", "confirm_all", opened[3], table, base=opened[9])
    editor.manifest["files"][0]["registered_sections"][0]["group"] = "背景変更"
    blocked = _act(editor, "initial", "apply", confirmed[0], confirmed[2], base=opened[9])
    assert blocked[4] is dash.no_update
    assert "編集中に背景" in blocked[7]

def test_cancel_does_not_publish_draft(editor):
    opened = _open(editor, "conversion")
    cancelled = _act(editor, "conversion", "cancel", opened[3], opened[5], base=opened[9])
    assert cancelled[0] is None
    assert cancelled[4] is dash.no_update
    assert cancelled[5] == {"display": "none"}

def test_table_does_not_treat_false_string_as_confirmed(editor):
    rows = deepcopy(editor.manifest["files"][0]["registered_sections"])
    rows[0]["metadata_confirmed"] = "false"
    assert editor.module._table_rows(rows)[0]["metadata_confirmed"] == "未確認"

@pytest.mark.parametrize("scope", ["initial", "reanalysis"])
def test_subset_and_empty_selection_are_explicit(editor, scope):
    opened = _open(editor, scope)
    table = deepcopy(opened[5])
    for index, row in enumerate(table): row.update(subject_id=f"M{index}", group="G")
    confirmed = _act(editor, scope, "confirm_all", opened[3], table, base=opened[9])
    subset = _act(editor, scope, "toggle", confirmed[0], confirmed[2], selected=[1], base=opened[9])
    applied = _act(editor, scope, "apply", subset[0], subset[2], base=opened[9])
    assert applied[4][editor.path]["selected_section_ids"] == [subset[0][0]["section_id"]]
    empty = _act(editor, scope, "toggle", subset[0], subset[2], selected=[0], base=opened[9])
    applied_empty = _act(editor, scope, "apply", empty[0], empty[2], base=opened[9])
    assert applied_empty[4][editor.path]["selected_section_ids"] == []

@pytest.mark.parametrize("value", [None, "", -1, "bad", float("nan"), float("inf")])
def test_manual_ppm_rejects_invalid_before_starting_job(monkeypatch, tmp_path, value):
    from app.callbacks import imzml_io_callbacks as module
    from app.services import analysis_runner
    monkeypatch.setattr(module, "_active", {})
    monkeypatch.setattr(module, "_paths", lambda job: (tmp_path, tmp_path / "receipt.json"))
    def must_not_run(*args, **kwargs):
        pytest.fail("不正なppmで変換プロセスを起動した")
    monkeypatch.setattr(analysis_runner, "start_analysis_process", must_not_run)
    result = module.start_imzml_job(1, "import", "source.imzML", "target.parquet", None, value)
    assert result[0] is dash.no_update
    assert "danger" == result[2].color
    assert not (tmp_path / "registration.json").exists()

@pytest.mark.parametrize("value", [0, 5.0])
def test_manual_ppm_reaches_cli_without_changing_value(monkeypatch, tmp_path, value):
    from app.callbacks import imzml_io_callbacks as module
    from app.services import analysis_runner
    captured = []
    monkeypatch.setattr(module, "_active", {})
    monkeypatch.setattr(module, "_paths", lambda job: (tmp_path, tmp_path / "receipt.json"))
    monkeypatch.setattr(module, "_registration_from_conversion_state", lambda *args: {"test": True})
    def record(*args, **kwargs):
        captured.append(kwargs["extra_args"])
        return {"success": False, "message": "検査用プロセス未起動"}
    monkeypatch.setattr(analysis_runner, "start_analysis_process", record)
    module.start_imzml_job(1, "import", "source.imzML", "target.parquet", None, value)
    assert len(captured) == 1
    assert float(captured[0][captured[0].index("--alignment-ppm") + 1]) == value

def test_confirmation_status_is_read_only_and_has_explicit_action():
    from app.layouts.imzml_spatial_float import create_imzml_spatial_float
    def components(node):
        if isinstance(node, (list, tuple)):
            for item in node: yield from components(item)
        elif hasattr(node, "to_plotly_json"):
            yield node
            yield from components(getattr(node, "children", None))
    for scope in ("initial", "reanalysis", "conversion"):
        nodes = list(components(create_imzml_spatial_float(scope)))
        table = next(node for node in nodes if getattr(node, "id", "") == "imzml_spatial_table" + _suffix(scope))
        column = next(row for row in table.columns if row["id"] == "metadata_confirmed")
        assert column["editable"] is False
        assert "presentation" not in column
        button = next(node for node in nodes if getattr(node, "id", "") == "imzml_spatial_confirm_all" + _suffix(scope))
        assert button.children == "全切片を確認済みにする"


@pytest.mark.parametrize("action", ["toggle", "merge", "one", "reset"])
def test_reanalysis_does_not_add_sections_absent_from_source_rds(editor, action):
    from app.services.section_metadata import restore_registered_selection
    entry = editor.manifest["files"][0]
    first = entry["sections"][0]["section_id"]
    entry["selected_section_ids"] = [first]
    entry["source_selected_section_ids"] = [first]
    entry.update(restore_registered_selection(entry))
    opened = _open(editor, "reanalysis")
    blocked = _act(editor, "reanalysis", action, opened[3], opened[5],
                   selected=[1], base=opened[9])
    assert blocked[4] is dash.no_update
    assert "元解析RDS" in blocked[7]
    assert len(entry["registered_sections"]) == 2


def test_reanalysis_selector_uses_source_selection_cap(editor):
    from app.callbacks.section_callbacks import source_reanalysis_catalog, _selection_blocks
    from app.services.section_metadata import restore_registered_selection
    entry = editor.manifest["files"][0]
    first = entry["sections"][0]["section_id"]
    # 前回は2切片のRDSから開始しても、今回の元RDSへ残った1切片が新しい上限。
    entry["source_selected_section_ids"] = [row["section_id"] for row in entry["sections"]]
    entry["selected_section_ids"] = [first]
    entry.update(restore_registered_selection(entry))
    catalog = source_reanalysis_catalog(editor.manifest)
    assert catalog[0]["source_selected_section_ids"] == [first]
    manifest = build_section_manifest(catalog, previous=editor.manifest)
    assert manifest["files"][0]["source_selected_section_ids"] == [first]
    block = _selection_blocks(catalog, manifest, "reanalysis")[0]
    checklist = next(child for child in block.children if isinstance(getattr(child, "id", None), dict)
                     and child.id["type"] == "section_roi_check")
    assert [option["value"] for option in checklist.options] == [first]
    assert len(manifest["files"][0]["registered_sections"]) == 2
