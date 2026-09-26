"""processed-centroid imzMLの共通feature化・0補完・再現性契約。"""
from __future__ import annotations

import json
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pyarrow.parquet as pq
from pyimzml.ImzMLParser import ImzMLParser
from pyimzml.ImzMLWriter import ImzMLWriter

from app.services.imzml_processed import import_processed_imzml, ZERO_SEMANTICS
from app.services.imzml_validation import checked_import, validate_converted_table
from app.services.input_preparation import conversion_spec


def _processed_input(tmp_path, axes, intensities=None):
    path = tmp_path / "processed.imzML"
    coords = [(2, 1, 1), (1, 1, 1), (1, 2, 1), (2, 2, 1)]
    if intensities is None:
        intensities = [np.arange(1, len(axis) + 1, dtype=np.float32) + index * 10
                       for index, axis in enumerate(axes)]
    with ImzMLWriter(str(path), mz_dtype=np.float64, intensity_dtype=np.float32,
                     mode="processed", spec_type="centroid") as writer:
        for index, axis in enumerate(axes):
            writer.addSpectrum(np.asarray(axis, dtype=np.float64),
                               np.asarray(intensities[index], dtype=np.float32),
                               coords[index])
    tree = ET.parse(path)
    levels = [elem for elem in tree.iter() if elem.get("accession") == "MS:1000511"]
    if not levels:
        raise AssertionError("合成fixtureにMS level属性がありません")
    for level in levels:
        if level.get("value") == "0":
            level.set("value", "1")
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return path


def test_exact_union_zero_fills_variable_peak_lists(tmp_path):
    source = _processed_input(
        tmp_path,
        [[100.0, 200.0], [100.0, 300.0], [200.0, 400.0]],
        [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
    )
    output = tmp_path / "matrix.parquet"
    result = import_processed_imzml(source, output, alignment_ppm=0)
    manifest = json.loads(output.with_suffix(".imzml.json").read_text(encoding="utf-8"))
    names = manifest["column_names"]
    assert manifest["schema_version"] == 3
    assert manifest["zero_semantics"] == ZERO_SEMANTICS
    assert manifest["zero_is_absolute_absence"] is False
    assert manifest["mz_axis"] == [100.0, 200.0, 300.0, 400.0]
    table = pq.read_table(output).to_pydict()
    # 座標順は (1,1)=source1, (2,1)=source0, (1,2)=source2。
    assert list(zip(table["x"], table["y"])) == [(1.0, 1.0), (2.0, 1.0), (1.0, 2.0)]
    assert table[names[0]] == [3.0, 1.0, 0.0]
    assert table[names[1]] == [0.0, 2.0, 5.0]
    assert table[names[2]] == [4.0, 0.0, 0.0]
    assert table[names[3]] == [0.0, 0.0, 6.0]
    assert result["source_peaks"] == 6
    summary = validate_converted_table(output)
    assert summary["representation"] == "processed_centroid_sparse"
    assert summary["features"] == 4
    assert summary["output_nonzero_count"] == 6


def test_ppm_alignment_merges_and_sums_same_pixel_collision(tmp_path):
    source = _processed_input(
        tmp_path,
        [[100.0, 100.0004, 200.0], [100.0002, 300.0]],
        [[1.0, 2.0, 3.0], [4.0, 5.0]],
    )
    output = tmp_path / "aligned.parquet"
    import_processed_imzml(source, output, alignment_ppm=5)
    manifest = json.loads(output.with_suffix(".imzml.json").read_text(encoding="utf-8"))
    assert manifest["feature_count"] == 3
    assert manifest["conversion_qc"]["collision_peak_count"] == 1
    assert manifest["collision_intensity_rule"] == "sum_within_pixel_feature"
    names = manifest["column_names"]
    table = pq.read_table(output).to_pydict()
    # source1が先頭行、source0が2行目。100 m/z featureへ4、1+2を格納。
    assert table[names[0]] == [4.0, 3.0]
    assert table[names[1]] == [0.0, 3.0]
    assert table[names[2]] == [5.0, 0.0]
    assert abs(manifest["conversion_qc"]["source_intensity_sum"] - 15.0) < 1e-6
    assert abs(manifest["conversion_qc"]["output_intensity_sum"] - 15.0) < 1e-6


def test_checked_import_falls_back_when_equal_lengths_have_different_axes(tmp_path):
    source = _processed_input(
        tmp_path,
        [[100.0, 200.0], [100.0002, 300.0]],
        [[1.0, 2.0], [3.0, 4.0]],
    )
    output = tmp_path / "checked.parquet"
    result = checked_import(source, output, alignment_ppm=5)
    manifest = json.loads(output.with_suffix(".imzml.json").read_text(encoding="utf-8"))
    assert result["representation"] == "processed_centroid_sparse"
    assert manifest["binary_validation"]["mz_axis_contract"]["status"] == \
        "processed_sparse_matrix"
    assert manifest["binary_validation"]["ms_level_interpretation"]["status"] in {
        "explicit_ms1", "inferred_ms1"
    }
    validate_converted_table(output)


def test_processed_export_is_valid_but_not_source_lossless(tmp_path):
    from app.services.imzml_io import export_imzml

    source = _processed_input(tmp_path, [[100.0], [200.0]], [[1.0], [2.0]])
    output = tmp_path / "matrix.parquet"
    import_processed_imzml(source, output, alignment_ppm=0)
    archive = tmp_path / "export.zip"
    receipt = export_imzml(output, archive)
    assert receipt["lossless_to_source"] is False
    assert receipt["source_representation"] == "processed_centroid_sparse"
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(tmp_path / "unpacked")
    parser = ImzMLParser(str(tmp_path / "unpacked" / "matrix.imzML"))
    assert len(parser.coordinates) == 2
    assert all(len(parser.getspectrum(index)[0]) == 2 for index in range(2))


def test_alignment_ppm_is_part_of_conversion_spec():
    zero = conversion_spec(processed_alignment_ppm=0)
    five = conversion_spec(processed_alignment_ppm=5)
    assert zero["processed_alignment_ppm"] == 0.0
    assert five["processed_alignment_ppm"] == 5.0
    assert zero != five
