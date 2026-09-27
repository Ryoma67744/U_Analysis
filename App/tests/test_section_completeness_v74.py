from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.input_preparation import InputPreparationError

from app.services.section_completeness import (
    format_section_issues,
    normalized_registered_sections,
    require_complete_sections,
    validate_registered_sections,
)


def _row(section_id="s1", name="A", subject="M1", group="ctrl", *,
         confirmed=True, selected=True, components=("c1",), pixels=2):
    return {
        "section_id": section_id,
        "section_display_name": name,
        "subject_id": subject,
        "group": group,
        "metadata_confirmed": confirmed,
        "selected": selected,
        "component_ids": list(components),
        "pixel_count": pixels,
    }


def test_unselected_section_is_still_required():
    rows = [
        _row(),
        _row("s2", "B", "", "case", confirmed=False,
             selected=False, components=("c2",), pixels=3),
    ]
    issues = validate_registered_sections(rows, components={"c1": 2, "c2": 3})
    assert any(issue.section_id == "s2" for issue in issues)
    fields = next(issue.missing_fields for issue in issues if issue.section_id == "s2")
    assert "個体／独立試料ID" in fields
    assert "登録確認" in fields
    assert "解析対象外" in format_section_issues(issues)


def test_complete_registry_and_explicit_not_applicable_values_pass():
    rows = [
        _row(),
        _row("s2", "B", "該当なし", "該当なし", selected=False,
             components=("c2",), pixels=3),
    ]
    normalized = require_complete_sections(rows, components={"c1": 2, "c2": 3})
    assert [row["section_display_name"] for row in normalized] == ["A", "B"]


def test_non_spatial_registry_does_not_require_component_ids():
    rows = [_row(components=(), pixels=10)]
    assert validate_registered_sections(rows) == []


def test_component_coverage_and_duplicate_names_are_rejected():
    rows = [
        _row(name="A", components=("c1",), pixels=2),
        _row("s2", "A", "M2", "case", components=("c1",), pixels=2),
    ]
    issues = validate_registered_sections(rows, components={"c1": 2, "c2": 1})
    messages = " / ".join(
        ",".join(issue.missing_fields) if issue.missing_fields else issue.reason
        for issue in issues
    )
    assert "重複" in messages
    assert "複数切片" in messages
    assert "どの切片にも登録されていません" in messages


def test_require_complete_sections_raises_with_all_missing_fields():
    rows = [_row(name="", subject="", group="", confirmed=False)]
    with pytest.raises(InputPreparationError) as exc:
        require_complete_sections(rows, components={"c1": 2}, filename="sample.imzML")
    message = str(exc.value)
    assert "sample.imzML" in message
    assert "切片名" in message
    assert "個体／独立試料ID" in message
    assert "群" in message
    assert "登録確認" in message



def test_string_false_confirmation_and_control_characters_are_rejected():
    rows = [_row(confirmed="false")]
    issues = validate_registered_sections(rows, components={"c1": 2})
    assert any("登録確認" in issue.missing_fields for issue in issues)

    rows = [_row(name="A\nB")]
    issues = validate_registered_sections(rows, components={"c1": 2})
    assert any("制御文字" in issue.reason for issue in issues)

def test_selection_flag_is_not_part_of_registered_normalization():
    first = normalized_registered_sections([_row(selected=True)])
    second = normalized_registered_sections([_row(selected=False)])
    assert first == second



def test_backend_hydrates_pinned_imzml_runtime_registry(tmp_path, monkeypatch):
    from app.services import input_preparation as prep

    raw = tmp_path / "source.imzML"
    raw.write_text("xml")
    runtime = tmp_path / "registered.parquet"
    runtime.write_bytes(b"placeholder")
    registry = [_row(selected=False, components=("c1",), pixels=10)]
    monkeypatch.setattr(
        "app.services.data_manager.read_parquet_section_registry",
        lambda path: deepcopy(registry),
    )
    manifest = {
        "files": [{
            "file_id": "f1", "path": str(raw), "runtime_path": str(runtime),
            "conversion_key": "revision", "selection_mode": "selected",
            "roi_role": "spatial", "sections": [],
            "spatial_sections": deepcopy(registry),
            "registered_sections": deepcopy(registry),
            "selected_section_ids": ["s1"],
        }]
    }
    hydrated = prep._hydrate_parquet_registries(manifest)
    entry = hydrated["files"][0]
    assert entry["roi_role"] == "spatial"
    assert entry["registered_sections"] == registry
    assert entry["sections"][0]["section_id"] == "s1"


def test_backend_rejects_pinned_runtime_registry_tampering(tmp_path, monkeypatch):
    from app.services import input_preparation as prep

    raw = tmp_path / "source.imzML"
    raw.write_text("xml")
    runtime = tmp_path / "registered.parquet"
    runtime.write_bytes(b"placeholder")
    registry = [_row(selected=False, components=("c1",), pixels=10)]
    monkeypatch.setattr(
        "app.services.data_manager.read_parquet_section_registry",
        lambda path: deepcopy(registry),
    )
    tampered = deepcopy(registry)
    tampered[0]["group"] = "tampered"
    manifest = {
        "files": [{
            "file_id": "f1", "path": str(raw), "runtime_path": str(runtime),
            "conversion_key": "revision", "selection_mode": "selected",
            "roi_role": "spatial", "sections": [],
            "spatial_sections": tampered, "registered_sections": tampered,
            "selected_section_ids": ["s1"],
        }]
    }
    with pytest.raises(InputPreparationError, match="Parquet内registry"):
        prep._hydrate_parquet_registries(manifest)

def test_backend_parquet_registry_hydration_rejects_client_tampering(tmp_path, monkeypatch):
    from app.services import input_preparation as prep

    parquet = tmp_path / "registered.parquet"
    parquet.write_bytes(b"placeholder")
    registry = [_row(selected=False, components=(), pixels=10)]
    monkeypatch.setattr(
        "app.services.data_manager.read_parquet_section_registry",
        lambda path: deepcopy(registry),
    )
    manifest = {
        "files": [{
            "file_id": "f1", "path": str(parquet), "selection_mode": "selected",
            "roi_role": "section", "sections": [],
            "registered_sections": [{**deepcopy(registry[0]), "group": "tampered"}],
            "selected_section_ids": ["s1"],
        }]
    }
    with pytest.raises(InputPreparationError, match="Parquet内registry"):
        prep._hydrate_parquet_registries(manifest)


def test_backend_parquet_registry_hydration_uses_footer_as_source_of_truth(tmp_path, monkeypatch):
    from app.services import input_preparation as prep

    parquet = tmp_path / "registered.parquet"
    parquet.write_bytes(b"placeholder")
    registry = [_row(selected=False, components=(), pixels=10)]
    monkeypatch.setattr(
        "app.services.data_manager.read_parquet_section_registry",
        lambda path: deepcopy(registry),
    )
    manifest = {
        "files": [{
            "file_id": "f1", "path": str(parquet), "selection_mode": "selected",
            "roi_role": "section", "sections": [],
            "selected_section_ids": ["s1"],
        }]
    }
    hydrated = prep._hydrate_parquet_registries(manifest)
    entry = hydrated["files"][0]
    assert entry["registered_sections"] == registry
    assert entry["selected_section_ids"] == ["s1"]
    assert entry["sections"][0]["section_display_name"] == "A"


def test_metadata_confirmation_parser_rejects_false_strings():
    from app.services.section_completeness import is_metadata_confirmed
    assert is_metadata_confirmed(True)
    assert is_metadata_confirmed("true")
    assert is_metadata_confirmed("確認済")
    assert not is_metadata_confirmed(False)
    assert not is_metadata_confirmed("false")
    assert not is_metadata_confirmed("0")
