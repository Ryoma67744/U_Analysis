"""依存がある環境だけで標準ParquetとimzML往復を確認。R/GUIの試験ではない。"""
from pathlib import Path
import json
import zipfile
import xml.etree.ElementTree as ET
import numpy as np
import pytest


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("mode", ["continuous", "processed"])
def test_real_four_pixel_registration_and_selected_export(tmp_path, dtype, mode):
    pq = pytest.importorskip("pyarrow.parquet", reason="actual Parquet backend is unavailable")
    pytest.importorskip("pyimzml", reason="actual imzML backend is unavailable")
    from pyimzml.ImzMLWriter import ImzMLWriter
    from pyimzml.ImzMLParser import ImzMLParser
    from app.services.input_preparation import prepare_imzml, validate_asset, InputPreparationError
    from app.services.imzml_validation import inspect_binary_contract
    from app.services.imzml_spatial_layout import (
        inspect_imzml_spatial_layout, default_spatial_sections,
    )
    from app.services.section_metadata import build_section_manifest, stable_file_id
    from app.services.imzml_io import export_imzml

    xml = tmp_path / "sample.imzML"
    coordinates = [(6, 10, 1), (5, 9, 1), (6, 9, 1), (5, 10, 1)]
    axis = np.asarray([100.123456789, 201.234567891, 302.345678912], dtype=np.float64)
    values = np.asarray([[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]], dtype=dtype)
    with ImzMLWriter(str(xml), mode=mode, spec_type="profile",
                     mz_dtype=np.float64, intensity_dtype=dtype) as writer:
        for coordinate, intensity in zip(coordinates, values):
            writer.addSpectrum(axis, intensity, coordinate)

    tree = ET.parse(xml)
    levels = [element for element in tree.iter() if element.get("accession") == "MS:1000511"]
    assert levels
    if any(element.get("value") == "0" for element in levels):
        with pytest.raises(InputPreparationError, match="MS level=0"):
            inspect_binary_contract(xml)
    for level in levels:
        level.set("value", "1")
    ET.register_namespace("", "http://psi.hupo.org/ms/mzml")
    tree.write(xml, encoding="utf-8", xml_declaration=True)

    layout = inspect_imzml_spatial_layout(xml)
    file_id = stable_file_id(str(xml))
    sections = default_spatial_sections(file_id, layout)
    assert len(sections) == 1
    sections[0].update(
        section_display_name="Whole_section", subject_id="Mouse_01",
        group="Control", metadata_confirmed=True, selected=True,
    )
    catalog = [{
        "path": str(xml), "file_id": file_id, "available_rois": [],
        "roi_role": "spatial", "spatial_layout": layout,
        "spatial_sections": sections, "registered_sections": sections,
    }]
    entry = build_section_manifest(catalog)["files"][0]
    prepared = prepare_imzml(entry, cache_root=tmp_path / "cache")
    validate_asset(prepared)

    df = pq.read_table(prepared["runtime_path"]).to_pandas()
    assert df[["id", "x", "y"]].values.tolist() == [
        [1, 5, 9], [2, 6, 9], [3, 5, 10], [4, 6, 10]
    ]
    assert "ua_coordinate_component" not in df.columns
    assert df["annotation"].tolist() == ["Whole_section"] * 4
    columns = [f"{mass:.6f}" for mass in axis]
    expected = values[[1, 2, 3, 0]].astype(np.float32)
    np.testing.assert_array_equal(df[columns].values, expected)
    assert df[columns].values.dtype == np.dtype(np.float32)

    manifest = json.loads(Path(prepared["conversion_manifest_path"]).read_text())
    assert manifest["schema_version"] == 4
    np.testing.assert_array_equal(np.asarray(manifest["mz_axis"]), axis)
    assert [mapping["source_index"] for mapping in manifest["source_coordinates"]] == [1, 2, 3, 0]
    assert manifest["spatial_layout"]["component_count"] == 1
    assert manifest["section_registry"][0]["metadata_confirmed"] is True

    receipt = export_imzml(prepared["runtime_path"], tmp_path / "out.zip", pixel_ids=[1, 4])
    assert receipt["pixels"] == 2
    out = tmp_path / "export"
    out.mkdir()
    with zipfile.ZipFile(tmp_path / "out.zip") as archive:
        archive.extractall(out)
    with (out / "sample.ibd").open("rb") as binary:
        parser = ImzMLParser(str(out / "sample.imzML"), ibd_file=binary)
        assert parser.coordinates == [(5, 9, 1), (6, 10, 1)]
        for index, original in enumerate([1, 0]):
            mz, intensity = parser.getspectrum(index)
            np.testing.assert_array_equal(mz, axis)
            np.testing.assert_array_equal(intensity, values[original].astype(np.float32))

    reused = prepare_imzml(entry, cache_root=tmp_path / "cache")
    assert reused["conversion_key"] == prepared["conversion_key"]
