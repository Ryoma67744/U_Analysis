"""座標component、切片名、解析署名の契約。"""
from copy import deepcopy

from app.services.execution_policy import analysis_signature
from app.services.imzml_spatial_layout import build_spatial_layout, strip_runtime_layout, merge_spatial_sections
from app.services.section_metadata import (
    build_section_manifest,
    manifest_group_rows,
    summarize_manifest,
    validate_section_manifest,
)


def layout():
    coords = []
    for ox, oy in ((0, 0), (20, 0), (8, 15)):
        coords.extend((ox + x, oy + y, 1) for y in range(2) for x in range(3))
    return strip_runtime_layout(build_spatial_layout(coords), strip_preview=True)


def catalog(spatial_sections=None):
    row = {"path": "/tmp/three.imzML", "available_rois": [], "spatial_layout": layout()}
    if spatial_sections is not None:
        row["spatial_sections"] = spatial_sections
    return [row]


def test_three_components_become_three_analysis_sections():
    manifest = build_section_manifest(catalog())
    entry = manifest["files"][0]
    assert entry["roi_role"] == "spatial"
    assert len(entry["sections"]) == len(entry["spatial_sections"]) == 3
    assert summarize_manifest(manifest)[0] == "解析対象：3切片 ／ 算出する結果：PCA・Harmony・RPCA"
    assert not validate_section_manifest(manifest)
    assert [r["section_display_name"] for r in manifest_group_rows(manifest)] == ["Section 01", "Section 02", "Section 03"]


def test_main_card_selection_excludes_one_section_without_losing_component_contract():
    first = build_section_manifest(catalog())
    path = first["files"][0]["path"]
    ids = [s["section_id"] for s in first["files"][0]["sections"][:2]]
    second = build_section_manifest(catalog(), selections={path: ids}, previous=first)
    assert len(second["files"][0]["sections"]) == 2
    assert len(second["files"][0]["spatial_sections"]) == 3
    assert not validate_section_manifest(second)


def test_name_and_group_changes_do_not_change_numeric_signature():
    manifest = build_section_manifest(catalog())
    before = analysis_signature({"section_manifest": manifest})
    changed = deepcopy(manifest)
    changed["files"][0]["sections"][0].update(
        section_display_name="Control 1", subject_id="M1", group="ctrl")
    changed["files"][0]["spatial_sections"][0].update(
        section_display_name="Control 1", subject_id="M1", group="ctrl")
    assert analysis_signature({"section_manifest": changed}) == before


def test_explicit_float_metadata_override_wins_previous_manifest():
    first = build_section_manifest(catalog())
    edited = deepcopy(first["files"][0]["spatial_sections"])
    edited[0].update(
        section_display_name="Control 1",
        subject_id="M1",
        group="ctrl",
    )
    second = build_section_manifest(catalog(edited), previous=first)
    spatial = second["files"][0]["spatial_sections"][0]
    selected = second["files"][0]["sections"][0]
    for row in (spatial, selected):
        assert row["section_display_name"] == "Control 1"
        assert row["subject_id"] == "M1"
        assert row["group"] == "ctrl"


def test_component_merge_changes_numeric_signature():
    manifest = build_section_manifest(catalog())
    before = analysis_signature({"section_manifest": manifest})
    entry = manifest["files"][0]
    merged = merge_spatial_sections(entry["file_id"], entry["spatial_layout"], entry["spatial_sections"], [0, 1])
    changed = build_section_manifest(catalog(merged), previous=manifest)
    assert len(changed["files"][0]["sections"]) == 2
    assert analysis_signature({"section_manifest": changed}) != before


def test_invalid_duplicate_component_is_rejected():
    manifest = build_section_manifest(catalog())
    entry = manifest["files"][0]
    entry["spatial_sections"][1]["component_ids"] = list(entry["spatial_sections"][0]["component_ids"])
    assert any("重複" in e or "欠落" in e for e in validate_section_manifest(manifest))


def test_execution_table_edits_update_selected_and_full_spatial_rows():
    from app.services.execution_policy import apply_group_rows
    manifest = build_section_manifest(catalog())
    sid = manifest["files"][0]["sections"][0]["section_id"]
    edited = apply_group_rows(manifest, [{
        "section_id": sid,
        "section_display_name": "Control A",
        "subject_id": "M1",
        "group": "ctrl",
    }])
    selected = next(s for s in edited["files"][0]["sections"] if s["section_id"] == sid)
    full = next(s for s in edited["files"][0]["spatial_sections"] if s["section_id"] == sid)
    assert selected["section_display_name"] == full["section_display_name"] == "Control A"
    assert selected["subject_id"] == full["subject_id"] == "M1"
    assert not validate_section_manifest(edited)


def test_postanalysis_name_edit_updates_selected_and_full_spatial_rows(tmp_path, monkeypatch):
    """RDS後の切片名編集でもcomponent構成と数値IDを変えない。"""
    import json
    from app.services import section_group_metadata as sgm

    manifest = build_section_manifest(catalog())
    root = tmp_path / "result"
    root.mkdir()
    target = root / "section_manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sgm, "_result_dir", lambda _rds: root)

    rows = []
    for index, row in enumerate(manifest_group_rows(manifest), 1):
        rows.append({
            "section_id": row["section_id"],
            "section_display_name": f"Slice {index}",
            "subject_id": f"M{index}",
            "group": "ctrl" if index < 3 else "treated",
        })
    saved = sgm.save_group_edits("dummy.rds", rows)
    entry = saved["files"][0]
    selected = {r["section_id"]: r for r in entry["sections"]}
    full = {r["section_id"]: r for r in entry["spatial_sections"]}
    assert set(selected) == set(full)
    for index, sid in enumerate(selected, 1):
        assert selected[sid]["section_display_name"] == full[sid]["section_display_name"] == f"Slice {index}"
        assert selected[sid]["component_ids"] == full[sid]["component_ids"]
    assert not validate_section_manifest(saved)
