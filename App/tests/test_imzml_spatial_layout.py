"""imzML座標切片の純Python契約。ibd・強度・Dashを必要としない。"""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.services.imzml_spatial_layout import (
    SpatialLayoutError,
    build_spatial_layout,
    inspect_imzml_spatial_layout,
    merge_spatial_sections,
    normalize_spatial_sections,
    strip_runtime_layout,
)


def write_xml(path: Path, coordinates):
    root = ET.Element("mzML")
    refs = ET.SubElement(root, "referenceableParamGroupList")
    group = ET.SubElement(refs, "referenceableParamGroup", id="position")
    ET.SubElement(group, "cvParam", accession="TEST:position", value="")
    spectra = ET.SubElement(ET.SubElement(root, "run"), "spectrumList", count=str(len(coordinates)))
    for index, (x, y, z) in enumerate(coordinates):
        spectrum = ET.SubElement(spectra, "spectrum", index=str(index))
        scan = ET.SubElement(ET.SubElement(spectrum, "scanList"), "scan")
        ET.SubElement(scan, "referenceableParamGroupRef", ref="position")
        for term, value in (("IMS:1000050", x), ("IMS:1000051", y), ("IMS:1000052", z)):
            ET.SubElement(scan, "cvParam", accession=term, value=str(value))
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def three_sections():
    coords = []
    for ox, oy in ((0, 0), (20, 0), (8, 15)):
        coords.extend((ox + x, oy + y, 1) for y in range(3) for x in range(4))
    return coords


def test_xml_only_inspection_detects_three_sections(tmp_path):
    path = tmp_path / "three.imzML"
    write_xml(path, three_sections())
    layout = inspect_imzml_spatial_layout(path, max_preview_points=12)
    assert layout["component_count"] == 3
    assert [c["default_name"] for c in layout["components"]] == ["Section 01", "Section 02", "Section 03"]
    assert [c["pixel_count"] for c in layout["components"]] == [12, 12, 12]
    assert sum(len(c["preview"]) for c in layout["components"]) <= 12
    assert not path.with_suffix(".ibd").exists()  # 座標previewはibdを要求しない。


def test_component_ids_do_not_depend_on_spectrum_order():
    forward = strip_runtime_layout(build_spatial_layout(three_sections()), strip_preview=True)
    reverse = strip_runtime_layout(build_spatial_layout(list(reversed(three_sections()))), strip_preview=True)
    assert forward["coordinate_hash"] == reverse["coordinate_hash"]
    assert {c["component_id"] for c in forward["components"]} == {c["component_id"] for c in reverse["components"]}


def test_eight_neighbour_and_small_component_are_retained():
    layout = build_spatial_layout([(0, 0, 1), (1, 1, 1), (20, 20, 1)], include_preview=False)
    assert layout["component_count"] == 2
    assert sorted(c["pixel_count"] for c in layout["components"]) == [1, 2]
    assert layout["warnings"]


def test_merge_and_normalize_cover_every_component_once():
    layout = strip_runtime_layout(build_spatial_layout(three_sections()), strip_preview=True)
    rows = normalize_spatial_sections("file_a", layout)
    merged = merge_spatial_sections("file_a", layout, rows, [0, 1])
    assert len(merged) == 2
    assert sorted(len(r["component_ids"]) for r in merged) == [1, 2]
    assert sum(r["pixel_count"] for r in merged) == 36
    with pytest.raises(SpatialLayoutError, match="重複"):
        normalize_spatial_sections("file_a", layout, [
            {"component_ids": [layout["components"][0]["component_id"]], "section_display_name": "A"},
            {"component_ids": [layout["components"][0]["component_id"]], "section_display_name": "B"},
        ])



def test_grid_pitch_uses_repeated_scan_step_not_cross_section_offset():
    coords = []
    # 20刻みの2切片。開始xが1だけずれていても、全体最小差=1を採らない。
    for ox, oy in ((0, 0), (101, 0)):
        coords.extend((ox + 20 * x, oy + 20 * y, 1) for y in range(3) for x in range(4))
    result = build_spatial_layout(coords, include_preview=False)
    assert result["grid_pitch"] == {"x": 20, "y": 20}
    assert result["component_count"] == 2

def test_duplicate_or_multiple_z_is_rejected(tmp_path):
    path = tmp_path / "bad.imzML"
    write_xml(path, [(1, 1, 1), (1, 1, 1)])
    with pytest.raises(SpatialLayoutError, match="重複"):
        inspect_imzml_spatial_layout(path)
    path2 = tmp_path / "z.imzML"
    write_xml(path2, [(1, 1, 1), (2, 1, 2)])
    with pytest.raises(SpatialLayoutError, match="z 面"):
        inspect_imzml_spatial_layout(path2)


def test_reset_after_merge_preserves_selection_and_group_without_reusing_merged_name():
    from app.services.imzml_spatial_layout import reset_spatial_sections
    layout = strip_runtime_layout(build_spatial_layout(three_sections()), strip_preview=True)
    rows = normalize_spatial_sections("file_a", layout)
    rows[0].update(subject_id="M1", group="ctrl", section_display_name="Control A")
    rows[1].update(subject_id="M1", group="ctrl", section_display_name="Control B")
    merged = merge_spatial_sections("file_a", layout, rows, [0, 1])
    restored = reset_spatial_sections("file_a", layout, merged)
    assert len(restored) == 3
    assert restored[0]["subject_id"] == restored[1]["subject_id"] == "M1"
    assert restored[0]["group"] == restored[1]["group"] == "ctrl"
    assert restored[0]["section_display_name"] == "Section 01"
    assert restored[1]["section_display_name"] == "Section 02"


def test_many_isolated_components_are_handled_without_repeated_global_minimum():
    x = 0
    coords = [(x, 0, 1)]
    for gap in range(2, 1001):
        x += gap
        coords.append((x, 0, 1))
    result = build_spatial_layout(coords, include_preview=False)
    # 最小gap=2の最初の2点だけが接続し、残りは孤立する。
    assert result["component_count"] == 999
    assert sum(c["pixel_count"] for c in result["components"]) == 1000
