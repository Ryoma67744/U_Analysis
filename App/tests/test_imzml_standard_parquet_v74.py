from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
pytest.importorskip("pyimzml")
from pyimzml.ImzMLWriter import ImzMLWriter

from app.services.imzml_registration import build_registration_spec, rewrite_registered_parquet
from app.services.imzml_spatial_layout import inspect_imzml_spatial_layout
from app.services.imzml_validation import checked_import, validate_converted_table
from app.services.section_metadata import stable_file_id


def _write_processed_pair(tmp_path: Path) -> Path:
    path = tmp_path / "multi.imzML"
    axes = [
        np.asarray([100.0, 200.0], dtype=np.float64),
        np.asarray([100.0002, 300.0], dtype=np.float64),
        np.asarray([200.0, 400.0], dtype=np.float64),
        np.asarray([300.0, 400.0], dtype=np.float64),
    ]
    intensities = [
        np.asarray([1.0, 2.0], dtype=np.float32),
        np.asarray([3.0, 4.0], dtype=np.float32),
        np.asarray([5.0, 6.0], dtype=np.float32),
        np.asarray([7.0, 8.0], dtype=np.float32),
    ]
    coords = [(0, 0, 1), (1, 0, 1), (10, 0, 1), (11, 0, 1)]
    with ImzMLWriter(str(path), mode="processed", spec_type="centroid") as writer:
        for mz, intensity, coord in zip(axes, intensities, coords):
            writer.addSpectrum(mz, intensity, coord)
    tree = ET.parse(path)
    for elem in tree.iter():
        if elem.get("accession") == "MS:1000511" and elem.get("value") == "0":
            elem.set("value", "1")
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return path


def _entry(path: Path, *, second_selected=False):
    layout = inspect_imzml_spatial_layout(path)
    components = layout["components"]
    assert len(components) == 2
    rows = []
    for index, component in enumerate(components, 1):
        rows.append({
            "section_id": f"s{index}",
            "section_display_name": f"Slice_{index}",
            "subject_id": f"Mouse_{index}",
            "group": "Control" if index == 1 else "Toxo",
            "metadata_confirmed": True,
            "component_ids": [component["component_id"]],
            "pixel_count": component["pixel_count"],
            "selected": index == 1 or second_selected,
        })
    return {
        "file_id": stable_file_id(str(path)), "path": str(path),
        "spatial_layout": layout, "registered_sections": rows,
        "spatial_sections": deepcopy(rows),
        "sections": [deepcopy(row) for row in rows if row["selected"]],
        "selected_section_ids": [row["section_id"] for row in rows if row["selected"]],
    }


def test_all_sections_are_written_even_when_one_is_not_selected(tmp_path):
    source = _write_processed_pair(tmp_path)
    entry = _entry(source, second_selected=False)
    registration = build_registration_spec(entry)
    output = tmp_path / "registered.parquet"
    result = checked_import(source, output, registration=registration, alignment_ppm=5.0)
    assert result["validation_summary"]["all_sections_registered"] is True

    pf = pq.ParquetFile(output)
    assert pf.schema_arrow.names[0:3] == ["id", "x", "y"]
    assert pf.schema_arrow.names[-1] == "annotation"
    assert "ua_coordinate_component" not in pf.schema_arrow.names
    feature_names = pf.schema_arrow.names[3:-1]
    assert feature_names and all(len(name.partition(".")[2]) == 6 for name in feature_names)
    table = pf.read()
    annotations = table.column("annotation").to_pylist()
    assert annotations.count("Slice_1") == 2
    assert annotations.count("Slice_2") == 2
    assert table.num_rows == 4
    registry = json.loads(pf.schema_arrow.metadata[b"ua_section_registry"].decode())
    assert {row["section_display_name"] for row in registry} == {"Slice_1", "Slice_2"}
    assert all(row["subject_id"] and row["group"] and row["metadata_confirmed"] for row in registry)


def test_registration_only_rewrite_changes_annotation_not_intensity(tmp_path):
    source = _write_processed_pair(tmp_path)
    entry = _entry(source)
    registration = build_registration_spec(entry)
    output = tmp_path / "registered.parquet"
    checked_import(source, output, registration=registration, alignment_ppm=5.0)

    changed = _entry(source)
    changed["registered_sections"][1]["section_display_name"] = "Toxo_new"
    changed["registered_sections"][1]["group"] = "Case"
    changed["spatial_sections"] = deepcopy(changed["registered_sections"])
    next_registration = build_registration_spec(changed)
    rewritten = tmp_path / "registered_v2.parquet"
    rewrite_registered_parquet(
        output, output.with_suffix(".imzml.json"), next_registration, rewritten
    )
    summary = validate_converted_table(rewritten)
    assert summary["all_sections_registered"] is True

    old = pq.read_table(output)
    new = pq.read_table(rewritten)
    feature_columns = old.column_names[3:-1]
    for name in feature_columns:
        assert old.column(name).equals(new.column(name))
    assert "Toxo_new" in new.column("annotation").to_pylist()
