"""Different dictionary storage and row groups must retain analytical values."""
import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import numpy as np
import pyarrow.parquet as pq
from pyimzml.ImzMLWriter import ImzMLWriter
import pytest

from app.services import imzml_processed
from app.services.imzml_dictionary import DictionaryPolicy, SortedUniqueBuilder
from app.services.imzml_registration import build_registration_spec
from app.services.imzml_spatial_layout import default_spatial_sections, inspect_imzml_spatial_layout
from app.services.imzml_validation import validate_converted_table
from app.services.input_preparation import PreparationCancelled


def _fixture(tmp_path, pixels=600):
    source = tmp_path / "source.imzML"
    with ImzMLWriter(str(source), mode="processed", spec_type="centroid",
                     mz_dtype=np.float64, intensity_dtype=np.float32) as writer:
        for index in range(pixels):
            writer.addSpectrum(np.array([100., 100.0004, 200., 300.]),
                               np.array([1., index + .25, index + 1., index + 2.], dtype=np.float32),
                               (index % 30, index // 30, 1))
    tree = ET.parse(source)
    for node in tree.iter():
        if node.get("accession") == "MS:1000511":
            node.set("value", "1")
    tree.write(source, encoding="utf-8", xml_declaration=True)
    layout = inspect_imzml_spatial_layout(source)
    sections = default_spatial_sections("synthetic", layout)
    for section in sections:
        section.update(section_display_name="Slice_1", subject_id="Synthetic", group="Control",
                       metadata_confirmed=True)
    registration = build_registration_spec({"path": str(source), "file_id": "synthetic",
                                            "spatial_layout": layout, "registered_sections": sections})
    return source, registration


def test_spilled_dictionary_and_512_row_groups_preserve_complete_matrix(tmp_path, monkeypatch):
    source, registration = _fixture(tmp_path)
    small = tmp_path / "small.parquet"
    imzml_processed.import_processed_imzml(source, small, registration=registration,
                                          alignment_ppm=5, block_size=128)

    def force_external(root, **kwargs):
        return SortedUniqueBuilder(root, **kwargs, policy=DictionaryPolicy(17, 32, 3))

    monkeypatch.setattr(imzml_processed, "SortedUniqueBuilder", force_external)
    large = tmp_path / "large.parquet"
    result = imzml_processed.import_processed_imzml(source, large, registration=registration,
                                                   alignment_ppm=5)
    assert result["dictionary_stats"]["external_merges"] > 0
    with pq.ParquetFile(small) as parquet:
        assert parquet.metadata.num_row_groups == 5
    with pq.ParquetFile(large) as parquet:
        assert parquet.metadata.num_row_groups == 2
    left, right = pq.read_table(small), pq.read_table(large)
    assert left.equals(right, check_metadata=True)
    assert right["100.000200"].to_pylist() == [i + 1.25 for i in range(600)]
    assert right["200.000000"].to_pylist() == [i + 1. for i in range(600)]
    assert right["300.000000"].to_pylist() == [i + 2. for i in range(600)]
    manifests = [json.loads(path.with_suffix(".imzml.json").read_text(encoding="utf-8"))
                 for path in (small, large)]
    assert manifests[0]["mz_axis"] == manifests[1]["mz_axis"]
    assert manifests[0]["feature_qc"] == manifests[1]["feature_qc"]
    for manifest in manifests:
        assert manifest["checked_spectrum_count"] == 600
        assert manifest["conversion_qc"]["collision_peak_count"] == 600
        assert manifest["conversion_qc"]["source_peak_count"] == 2400
    for manifest in manifests:
        manifest["conversion_qc"].pop("parquet_block_rows")
    assert manifests[0]["conversion_qc"] == manifests[1]["conversion_qc"]
    assert validate_converted_table(large)["all_pixels_checked"]
    assert not list(tmp_path.glob(".processed-imzml-*"))


def test_dictionary_cancellation_is_not_rewrapped_as_input_error(tmp_path):
    source, registration = _fixture(tmp_path, pixels=2)
    with pytest.raises(PreparationCancelled):
        imzml_processed.import_processed_imzml(source, tmp_path / "cancelled.parquet",
                                              registration=registration, cancel=lambda: True)
    assert not (tmp_path / "cancelled.parquet").exists()
    assert not list(tmp_path.glob(".processed-imzml-*"))


@pytest.mark.parametrize("available", [(1024 * 1024,), (1024**3, 1024 * 1024)])
def test_memory_admission_rejects_without_silently_changing_blocks(tmp_path, monkeypatch, available):
    import psutil
    source, registration = _fixture(tmp_path, pixels=2)
    samples = iter(available)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=next(samples)))
    with pytest.raises(imzml_processed.ImzMLContractError, match="メモリ"):
        imzml_processed.import_processed_imzml(source, tmp_path / "low-memory.parquet",
                                              registration=registration)
    assert not (tmp_path / "low-memory.parquet").exists()
    assert not list(tmp_path.glob(".processed-imzml-*"))


def test_final_matrix_loop_checks_cancel_between_rows(tmp_path, monkeypatch):
    source, registration = _fixture(tmp_path, pixels=4)
    state = {"cancel": False, "rows": 0}
    original = imzml_processed._spectrum_with_diagnostics

    def first_row_then_cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        state["rows"] += 1
        state["cancel"] = True
        return result

    monkeypatch.setattr(imzml_processed, "_spectrum_with_diagnostics", first_row_then_cancel)
    with pytest.raises(PreparationCancelled):
        imzml_processed.import_processed_imzml(source, tmp_path / "stopped.parquet",
                                              registration=registration, cancel=lambda: state["cancel"])
    assert state["rows"] == 1
    assert not (tmp_path / "stopped.parquet").exists()
    assert not list(tmp_path.glob(".processed-imzml-*"))


def test_cancel_during_mapping_closes_memmaps_before_cleanup(tmp_path, monkeypatch):
    source, registration = _fixture(tmp_path, pixels=2)
    state = {"cancel": False}
    original = imzml_processed.np.memmap

    def mapping_then_cancel(*args, **kwargs):
        mapped = original(*args, **kwargs)
        if kwargs.get("mode") == "w+":
            state["cancel"] = True
        return mapped

    monkeypatch.setattr(imzml_processed.np, "memmap", mapping_then_cancel)
    with pytest.raises(PreparationCancelled):
        imzml_processed.import_processed_imzml(source, tmp_path / "stopped.parquet",
                                              registration=registration, cancel=lambda: state["cancel"])
    assert not (tmp_path / "stopped.parquet").exists()
    assert not list(tmp_path.glob(".processed-imzml-*"))
