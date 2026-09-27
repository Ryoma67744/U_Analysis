"""★ ver74.1: 実際の不足一覧から切片・入力場所を特定できることを検査する。"""
from copy import deepcopy

import pytest

from app.services.imzml_spatial_layout import build_spatial_layout, default_spatial_sections
from app.services.section_metadata import build_section_manifest, summarize_manifest
from app.callbacks.section_callbacks import (
    _registration_issue_cards, _selection_blocks, update_section_state,
)


def _nodes(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _nodes(item)
    elif hasattr(value, "to_plotly_json"):
        yield value
        yield from _nodes(getattr(value, "children", None))


def _text(value):
    if isinstance(value, (list, tuple)):
        return " ".join(_text(item) for item in value)
    if hasattr(value, "to_plotly_json"):
        return _text(getattr(value, "children", None))
    return "" if value is None else str(value)


@pytest.fixture
def scenario(tmp_path):
    path = str(tmp_path / "example.imzML")
    layout = build_spatial_layout([(i * 10 + x, 0, 1) for i in range(5) for x in (0, 1)],
                                 include_preview=False)
    sections = default_spatial_sections("file", layout)
    for i, row in enumerate(sections):
        row.update(group="Ctrl" if i == 0 else "toxo", subject_id="", metadata_confirmed=False)
    catalog = [{"path": path, "spatial_layout": layout, "spatial_sections": sections,
                "file_id": "file", "available_rois": [], "roi_role": "spatial"}]
    selected = [row["section_id"] for row in sections[:3]]
    manifest = build_section_manifest(catalog, {path: selected})
    return catalog, manifest, path, selected


@pytest.mark.parametrize("scope", ["initial", "reanalysis"])
def test_all_five_rows_are_visible_and_open_correct_file(scenario, scope):
    _, manifest, path, _ = scenario
    cards, represented = _registration_issue_cards(manifest, scope)
    text = _text(cards)
    assert len(cards) == 1 and len(represented) == 1
    assert "登録対象：全5切片" in text and "今回の解析対象：3切片" in text
    for i in range(1, 6):
        assert f"Section {i:02d}" in text
    assert text.count("個体／独立試料ID：未入力") == 5
    assert text.count("登録確認：未完了（確認ボタンで操作）") == 5
    buttons = [n for n in _nodes(cards) if isinstance(getattr(n, "id", None), dict)]
    assert len(buttons) == 1
    assert buttons[0].id == {"type": "imzml_registration_fix", "scope": scope, "index": path}
    assert "全切片を確認済みにする" in text and "適用" in text


def test_summary_replaces_only_the_represented_long_warning(scenario):
    catalog, manifest, path, selected = scenario
    ids = [{"index": path}]
    _, updated, ui, _ = update_section_state(catalog, [selected], [], [], ids, [], manifest,
                                             scope="reanalysis")
    assert len(updated["files"][0]["sections"]) == 3
    assert "登録対象：全5切片" in _text(ui)
    assert "今回の解析に使用しない切片も検査対象です。" not in _text(ui)
    assert next(n for n in _nodes(ui) if isinstance(getattr(n, "id", None), dict)).id["scope"] == "reanalysis"


def test_empty_selection_error_is_preserved_beside_registration_card(scenario):
    catalog, manifest, path, _ = scenario
    _, _, ui, _ = update_section_state(catalog, [[]], [], [], [{"index": path}], [], manifest)
    assert "解析対象の切片／ROIを選択してください。" in _text(ui)
    assert "登録対象：全5切片" in _text(ui)


def test_complete_registration_removes_warning_and_keeps_edit_button(scenario):
    catalog, manifest, _, _ = scenario
    complete = deepcopy(manifest)
    for row in complete["files"][0]["spatial_sections"]:
        row.update(subject_id=row["section_id"], metadata_confirmed=True)
    assert _registration_issue_cards(complete, "initial") == ([], set())
    button = next(n for n in _nodes(_selection_blocks(catalog, complete, "initial"))
                  if isinstance(getattr(n, "id", None), dict) and n.id.get("type") == "imzml_spatial_open")
    assert button.color == "info" and button.outline is True


def test_structural_errors_are_not_hidden(scenario):
    _, manifest, _, _ = scenario
    manifest["files"][0]["spatial_sections"][1]["section_display_name"] = "Section 01"
    cards, _ = _registration_issue_cards(manifest, "initial")
    assert "切片名が重複しています" in _text(cards)


def test_nonspatial_warning_is_not_replaced_with_inaccessible_editor(scenario):
    # 補助表では未選択切片を編集できないため、元の検証エラーを隠して誘導しない。
    _, manifest, _, _ = scenario
    entry = deepcopy(manifest["files"][0])
    entry["roi_role"] = "section"
    entry["registered_sections"] = entry["spatial_sections"]
    entry.pop("spatial_sections")
    cards, represented = _registration_issue_cards({"files": [entry]}, "initial")
    assert not cards and not represented


def test_blank_ids_do_not_report_zero_independent_samples(scenario):
    _, manifest, _, _ = scenario
    groups = summarize_manifest(manifest)[1]
    assert "Ctrl：1切片・独立試料数 未確定（ID未入力1切片）" in groups
    assert "toxo：2切片・独立試料数 未確定（ID未入力2切片）" in groups
    assert "独立試料0例" not in groups


def test_partial_ids_and_explicit_not_applicable_remain_distinct():
    catalog = [{"path": f"/data/s{i}", "available_rois": []} for i in range(3)]
    manifest = build_section_manifest(catalog)
    for entry, subject in zip(manifest["files"], ["M1", "", "該当なし"]):
        entry["sections"][0].update(group="Ctrl", subject_id=subject)
    groups = summarize_manifest(manifest)[1]
    assert "ID入力済み1例" in groups and "ID未入力1切片" in groups
    assert "ID「該当なし」1切片" in groups


def test_same_biological_subject_across_sections_is_counted_once():
    catalog = [{"path": f"/data/s{i}", "available_rois": []} for i in range(2)]
    manifest = build_section_manifest(catalog)
    for entry in manifest["files"]:
        entry["sections"][0].update(group="Ctrl", subject_id="M1")
    assert "Ctrl：2切片・独立試料1例" in summarize_manifest(manifest)[1]
