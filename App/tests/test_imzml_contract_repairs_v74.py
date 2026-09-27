"""★ ver74.0: 単体converterの成功だけでは見逃した登録・数値・再出力境界。"""
from copy import deepcopy
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
pytest.importorskip("pyimzml")
from pyimzml.ImzMLParser import ImzMLParser
from pyimzml.ImzMLWriter import ImzMLWriter

from app.services.imzml_io import import_imzml, export_imzml, ImzMLContractError
from app.services.imzml_processed import import_processed_imzml
from app.services.imzml_registration import build_registration_spec
from app.services.imzml_spatial_layout import (
    default_spatial_sections, inspect_imzml_spatial_layout, normalize_spatial_sections,
)
from app.services.imzml_validation import checked_import, inspect_binary_contract, inspect_spectral_preflight
from app.services.input_preparation import InputPreparationError
from app.services.tims_parquet_contract import (
    TimsParquetContractError, canonical_registry_hash, read_registered_sections,
)


def _source(tmp_path, *, axes=None, dtype=np.float32, intensities=None):
    path = tmp_path / "source.imzML"
    axes = axes or [[100.0, 200.0]] * 4
    intensities = intensities or [[i + 0.125, i + 10.125] for i in range(4)]
    coords = [(0, 0, 1), (1, 0, 1), (10, 0, 1), (11, 0, 1)]
    with ImzMLWriter(str(path), mode="processed", spec_type="centroid",
                     mz_dtype=np.float64, intensity_dtype=dtype) as writer:
        for mz, values, coord in zip(axes, intensities, coords):
            writer.addSpectrum(np.asarray(mz), np.asarray(values, dtype=dtype), coord)
    tree = ET.parse(path)
    for element in tree.iter():
        if element.get("accession") == "MS:1000511":
            element.set("value", "1")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    layout = inspect_imzml_spatial_layout(path)
    rows = default_spatial_sections("file_source", layout)
    for i, row in enumerate(rows):
        row.update(section_display_name=f"Slice_{i}", subject_id=f"Mouse_{i}",
                   group="Control", metadata_confirmed=True)
    registration = build_registration_spec({
        "path": str(path), "file_id": "file_source", "spatial_layout": layout,
        "registered_sections": rows,
    })
    return path, registration


@pytest.mark.parametrize("entrypoint", [import_imzml, import_processed_imzml, checked_import])
@pytest.mark.parametrize("missing", ["subject_id", "group", "metadata_confirmed"])
def test_unconfirmed_registry_is_rejected_before_source_io(tmp_path, monkeypatch, entrypoint, missing):
    source, registration = _source(tmp_path)
    registration["section_registry"][-1][missing] = "false" if missing == "metadata_confirmed" else ""
    touched = []

    def forbidden(*args, **kwargs):
        touched.append(True)
        raise AssertionError("登録不足なのに原本I/Oを開始した")

    monkeypatch.setattr("app.services.imzml_io._pair", forbidden)
    monkeypatch.setattr("app.services.imzml_processed._pair", forbidden)
    monkeypatch.setattr("app.services.imzml_validation.inspect_binary_contract", forbidden)
    output = tmp_path / "unregistered.parquet"
    with pytest.raises((ImzMLContractError, InputPreparationError), match="必須情報"):
        entrypoint(source, output, registration=registration)
    assert touched == []
    assert not output.exists()
    assert not output.with_suffix(".imzml.json").exists()


@pytest.mark.parametrize("value", [False, "false", "0", None, ""])
def test_spatial_normalization_preserves_unconfirmed_values(tmp_path, value):
    source, registration = _source(tmp_path)
    rows = deepcopy(registration["section_registry"])
    rows[0]["metadata_confirmed"] = value
    normalized = normalize_spatial_sections("file_source", inspect_imzml_spatial_layout(source), rows)
    assert normalized[0]["metadata_confirmed"] is False


@pytest.mark.parametrize("processed", [False, True])
def test_six_decimal_features_remain_distinct_through_export(tmp_path, processed):
    masses = [100.000001, 100.000002]
    source, registration = _source(tmp_path, axes=[masses] * 4)
    output = tmp_path / "standard.parquet"
    converter = import_processed_imzml if processed else import_imzml
    converter(source, output, registration=registration)
    table = pq.read_table(output)
    assert table.column_names[3:-1] == ["100.000001", "100.000002"]
    assert table.column("100.000001").to_pylist() == [0.125, 1.125, 2.125, 3.125]
    assert table.column("100.000002").to_pylist() == [10.125, 11.125, 12.125, 13.125]
    archive = tmp_path / "export.zip"
    export_imzml(output, archive)
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(tmp_path / "exported")
    with (tmp_path / "exported/standard.ibd").open("rb") as binary:
        parser = ImzMLParser(str(tmp_path / "exported/standard.imzML"), ibd_file=binary)
        mz, values = parser.getspectrum(0)
        np.testing.assert_array_equal(mz, masses)
        np.testing.assert_array_equal(values, [0.125, 10.125])


def test_processed_ppm_zero_does_not_silently_merge_six_decimal_collision(tmp_path):
    source, registration = _source(tmp_path, axes=[[100.0000001, 100.0000002]] * 4)
    output = tmp_path / "collision.parquet"
    with pytest.raises(ImzMLContractError, match="衝突"):
        import_processed_imzml(source, output, registration=registration, alignment_ppm=0)
    assert not output.exists()


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("processed", [False, True])
@pytest.mark.parametrize("subset", [False, True])
def test_export_is_strict_ms1_and_records_precision_and_sparse_history(tmp_path, dtype, processed, subset):
    axes = [[100.0, 200.0], [100.0, 201.0], [100.0, 200.0], [100.0, 201.0]] if processed else None
    source, registration = _source(tmp_path, axes=axes, dtype=dtype)
    output = tmp_path / "matrix.parquet"
    checked_import(source, output, registration=registration)
    archive = tmp_path / "export.zip"
    receipt = export_imzml(output, archive, pixel_ids=[1, 4] if subset else None)
    assert receipt["schema_version"] == 2
    assert receipt["lossless_to_source"] is (not processed and dtype == np.float32)
    assert receipt["source_intensity_dtype"] == np.dtype(dtype).name
    assert receipt["export_intensity_dtype"] == "float32"
    assert receipt["zero_semantics"] == ("not_recorded_in_exported_centroid_spectrum" if processed else None)
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(tmp_path / "exported")
        assert json.loads(zipped.read("export_manifest.json"))["lossless_to_source"] == receipt["lossless_to_source"]
    # writerが生成した出力XMLはここで手修正しない。本番validatorと再変換を通す。
    exported = tmp_path / "exported/matrix.imzML"
    contract = inspect_binary_contract(exported)
    assert contract["ms_level_interpretation"]["status"] == "explicit_ms1"
    layout = inspect_imzml_spatial_layout(exported)
    rows = default_spatial_sections("exported", layout)
    for index, row in enumerate(rows):
        row.update(subject_id=f"E{index}", group="Export", metadata_confirmed=True)
    new_registration = build_registration_spec({
        "file_id": "exported", "path": str(exported), "spatial_layout": layout,
        "registered_sections": rows,
    })
    restored = tmp_path / "restored.parquet"
    checked_import(exported, restored, registration=new_registration)
    expected = pq.read_table(output)
    if subset:
        expected = expected.take(pa.array([0, 3]))
    actual = pq.read_table(restored)
    for name in expected.column_names[3:-1]:
        assert expected.column(name).equals(actual.column(name))
    assert actual.column("x").equals(expected.column("x"))
    assert actual.column("y").equals(expected.column("y"))


@pytest.mark.parametrize("change", ["missing", "empty", "json", "hash", "flag", "pixel_count",
                                   "annotation", "swapped_annotation", "coordinates", "id"])
def test_registered_parquet_corruption_never_becomes_legacy(tmp_path, change):
    source, registration = _source(tmp_path)
    output = tmp_path / "valid.parquet"
    checked_import(source, output, registration=registration)
    assert read_registered_sections(output) == registration["section_registry"]
    table = pq.read_table(output)
    md = dict(table.schema.metadata)
    if change == "missing":
        del md[b"ua_section_registry"]
    elif change == "empty":
        md[b"ua_section_registry"] = b"[]"
    elif change == "json":
        md[b"ua_section_registry"] = b"{broken"
    elif change == "hash":
        md[b"ua_section_registry_hash"] = b"0" * 64
    elif change == "flag":
        md[b"ua_registration_complete"] = b"0"
    elif change == "pixel_count":
        rows = deepcopy(registration["section_registry"])
        rows[0]["pixel_count"] += 1
        md[b"ua_section_registry"] = json.dumps(rows).encode()
        md[b"ua_section_registry_hash"] = canonical_registry_hash(rows).encode()
    elif change == "annotation":
        table = table.set_column(table.schema.get_field_index("annotation"), "annotation", pa.array(["wrong"] * 4))
    elif change == "swapped_annotation":
        names = table.column("annotation").to_pylist()
        table = table.set_column(table.schema.get_field_index("annotation"), "annotation",
                                 pa.array(names[2:] + names[:2]))
    elif change == "coordinates":
        table = table.set_column(1, "x", pa.array([0., 1., 10., 12.]))
    else:
        table = table.set_column(0, "id", pa.array([1, 1, 3, 4], type=pa.int64()))
    broken = tmp_path / "broken.parquet"
    pq.write_table(table.replace_schema_metadata(md), broken)
    with pytest.raises(TimsParquetContractError):
        read_registered_sections(broken)


def test_export_rejects_corrupt_registered_footer_before_publication(tmp_path):
    source, registration = _source(tmp_path)
    output = tmp_path / "matrix.parquet"
    checked_import(source, output, registration=registration)
    table = pq.read_table(output)
    metadata = dict(table.schema.metadata)
    del metadata[b"ua_spatial_layout"]
    pq.write_table(table.replace_schema_metadata(metadata), output)
    archive = tmp_path / "export.zip"
    with pytest.raises(InputPreparationError):
        export_imzml(output, archive)
    assert not archive.exists()


def test_legacy_parquet_without_registration_markers_is_preserved(tmp_path):
    path = tmp_path / "legacy.parquet"
    pq.write_table(pa.table({"id": [1], "x": [0.], "y": [0.], "100.000000": [1.],
                            "annotation": ["Unannotated"]}), path)
    assert read_registered_sections(path) == []


@pytest.mark.parametrize("schema_version", [2, 3])
def test_legacy_component_parquet_still_exports_without_registry_migration(tmp_path, schema_version):
    source, registration = _source(tmp_path)
    output = tmp_path / "legacy.parquet"
    import_imzml(source, output, registration=registration)
    manifest_path = output.with_suffix(".imzml.json")
    manifest = json.loads(manifest_path.read_text())
    table = pq.read_table(output)
    table = table.add_column(3, "ua_coordinate_component", pa.array([
        row["component_id"] for row in manifest["source_coordinates"]]))
    table = table.set_column(table.schema.get_field_index("annotation"), "annotation",
                             pa.array(["Unannotated"] * 4))
    manifest["schema_version"] = schema_version
    for key in ("section_registry", "registration_hash", "source_intensity_dtype", "lossless_to_source"):
        manifest.pop(key, None)
    manifest["input_representation"] = "processed_centroid_sparse" if schema_version == 3 else "common_axis"
    if schema_version == 3:
        names = [f"{mass:.5f}" for mass in manifest["mz_axis"]]
        manifest["column_names"] = names
        table = table.rename_columns(["id", "x", "y", "ua_coordinate_component", *names, "annotation"])
        manifest["zero_semantics"] = "not_recorded_in_exported_centroid_spectrum"
        manifest["alignment_ppm"] = 5.0
    pq.write_table(table.replace_schema_metadata({b"mz_sorted": b"100,200"}), output)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    parquet_before, manifest_before = output.read_bytes(), manifest_path.read_bytes()
    assert read_registered_sections(output) == []
    receipt = export_imzml(output, tmp_path / "legacy_export.zip")
    assert receipt["lossless_to_source"] is (schema_version == 2)
    assert output.read_bytes() == parquet_before
    assert manifest_path.read_bytes() == manifest_before


@pytest.mark.parametrize("converter", [import_imzml, import_processed_imzml])
def test_float32_overflow_is_rejected_by_lowlevel_converter(tmp_path, converter):
    source, registration = _source(tmp_path, dtype=np.float64, intensities=[[1e100, 1.]] * 4)
    output = tmp_path / "overflow.parquet"
    with pytest.raises(ImzMLContractError, match="overflow"):
        converter(source, output, registration=registration)
    assert not output.exists()
    assert not output.with_suffix(".imzml.json").exists()


@pytest.mark.parametrize("converter", [import_imzml, import_processed_imzml])
def test_sidecar_publish_failure_rolls_back_lowlevel_output(tmp_path, monkeypatch, converter):
    import os
    source, registration = _source(tmp_path)
    output = tmp_path / "partial.parquet"
    replace = os.replace

    def failing_replace(src, dst):
        if Path(dst) == output.with_suffix(".imzml.json"):
            raise OSError("simulated sidecar failure")
        return replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="sidecar"):
        converter(source, output, registration=registration)
    assert not output.exists()
    assert not output.with_suffix(".imzml.json").exists()
    assert not output.with_suffix(".imzml.pending").exists()


def test_preflight_retains_public_success_status_and_peak_count(tmp_path):
    source, _ = _source(tmp_path)
    result = inspect_spectral_preflight(source)
    assert result["status"] == "ok"
    assert result["schema_version"] == 1
    binary = inspect_binary_contract(source)
    assert binary["source_peak_count"] == binary["mz_axis_contract"]["source_peak_count"] == 8
