"""Pair-preserving input normalization and its published provenance."""
import hashlib
import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pyarrow.parquet as pq
import pytest
from pyimzml.ImzMLParser import ImzMLParser
from pyimzml.ImzMLWriter import ImzMLWriter

from app.services.imzml_io import (
    ImzMLContractError, _spectrum, _spectrum_with_diagnostics, export_imzml, import_imzml,
)
from app.services.imzml_registration import build_registration_spec
from app.services.imzml_spatial_layout import inspect_imzml_spatial_layout
from app.services.imzml_spectrum import (
    normalize_spectrum, new_order_qc, record_order_qc, order_metadata,
)
from app.services.imzml_validation import checked_import, validate_converted_table
from app.services.section_metadata import stable_file_id


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("indices", [[0, 1, 2], [2, 1, 0], [0, 2, 1]])
def test_normalization_preserves_pairs_dtype_and_readonly_source(dtype, indices):
    mz = np.array([100, 200, 300], dtype=dtype)[indices]
    intensity = np.array([11, 22, 33], dtype=dtype)[indices]
    before = (mz.tobytes(), intensity.tobytes())
    mz.flags.writeable = intensity.flags.writeable = False
    x, y, diagnostic = normalize_spectrum(mz, intensity, source_index=7)
    np.testing.assert_array_equal(x, [100, 200, 300])
    np.testing.assert_array_equal(y, [11, 22, 33])
    assert x.dtype == y.dtype == dtype
    assert before == (mz.tobytes(), intensity.tobytes())
    assert diagnostic["reordered"] is (indices != [0, 1, 2])
    if indices == [0, 1, 2]:
        assert x is mz and y is intensity


def test_ascending_input_never_calls_argsort(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("ascending spectrum was sorted")
    monkeypatch.setattr(np, "argsort", unexpected)
    normalize_spectrum(np.array([1., 2.]), np.array([0., 5.]), source_index=0)


@pytest.mark.parametrize("mz,intensity,reason", [
    ([], [], "array_length"),
    ([1., 2.], [1.], "array_length"),
    ([[1., 2.]], [[1., 2.]], "array_shape"),
    ([1., np.nan], [1., 2.], "nonfinite_mz"),
    ([1., 2.], [np.inf, 2.], "nonfinite_intensity"),
    ([0., 1.], [0., 1.], "nonpositive_mz"),
    ([-1., 1.], [0., 1.], "nonpositive_mz"),
    ([1., 2.], [-1., 1.], "negative_intensity"),
    ([1., 1.], [1., 2.], "duplicate_mz"),
    ([1., 2., 1.], [1., 2., 3.], "duplicate_mz"),
])
def test_invalid_values_remain_errors_with_context(mz, intensity, reason):
    with pytest.raises(ImzMLContractError) as caught:
        normalize_spectrum(mz, intensity, source_index=4,
                           context={"source_file": "example.imzML", "coordinate": [2, 3, 1]})
    assert caught.value.reason == reason
    assert caught.value.context["source_index"] == 4
    assert caught.value.context["source_file"] == "example.imzML"
    assert caught.value.context["coordinate"] == [2, 3, 1]
    assert len(caught.value.context.get("examples", [])) <= 3
    json.dumps(caught.value.context, allow_nan=False)


def test_duplicate_diagnostic_uses_original_array_positions():
    with pytest.raises(ImzMLContractError) as caught:
        normalize_spectrum([200., 100., 200.], [20., 10., 21.], source_index=0)
    assert caught.value.context["examples"] == [
        {"source_array_indices": [0, 2], "value": 200.}]


def test_memory_check_precedes_sort_allocation(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=1))
    monkeypatch.setattr(np, "argsort", lambda *a, **k: pytest.fail("allocated before guard"))
    with pytest.raises(ImzMLContractError) as caught:
        normalize_spectrum([2., 1.], [20., 10.], source_index=0)
    assert caught.value.reason == "sort_memory"
    assert caught.value.context["required_bytes"] > 1


def test_compatibility_wrapper_reads_once_and_compares_canonical_axes():
    class Parser:
        filename = "sample.imzML"
        coordinates = [(3, 4, 1)]
        calls = 0
        def getspectrum(self, i):
            self.calls += 1
            return np.array([2., 1.]), np.array([20., 10.])
    parser = Parser()
    result = _spectrum(parser, 0, np.array([1., 2.]))
    assert len(result) == 2 and parser.calls == 1
    with pytest.raises(ImzMLContractError, match="individual m/z axis") as caught:
        _spectrum_with_diagnostics(parser, 0, np.array([1., 2.00000001]))
    assert caught.value.reason == "individual_mz_axis"
    assert caught.value.context["coordinate"] == [3, 4, 1]


def test_qc_rejects_double_count_and_bounds_examples():
    qc = new_order_qc()
    for index in range(20):
        record_order_qc(qc, {"reordered": True, "moved_peak_count": 2}, index)
    with pytest.raises(ImzMLContractError, match="counted twice"):
        record_order_qc(qc, {"reordered": True, "moved_peak_count": 2}, 0)
    metadata = order_metadata(qc)
    assert metadata["checked_spectrum_count"] == metadata["reordered_spectrum_count"] == 20
    assert metadata["moved_peak_count"] == 40
    assert len(metadata["reordered_source_index_examples"]) == 10
    json.dumps(metadata)


def _source(root, name, axes, values, *, dtype=np.float32, mode="processed"):
    path = root / (name + ".imzML")
    with ImzMLWriter(str(path), mz_dtype=np.float64, intensity_dtype=dtype,
                     mode=mode, spec_type="centroid", polarity="positive") as writer:
        for i, (axis, intensity) in enumerate(zip(axes, values)):
            writer.addSpectrum(np.asarray(axis), np.asarray(intensity), (i % 2 + 1, i // 2 + 1, 1))
    tree = ET.parse(path)
    for node in tree.iter():
        if node.get("accession") == "MS:1000511":
            node.set("value", "1")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


def _registration(path):
    layout = inspect_imzml_spatial_layout(path)
    rows = [{"section_id": f"s{i}", "section_display_name": f"Slice_{i}",
             "subject_id": f"Mouse_{i}", "group": "Group_A", "metadata_confirmed": True,
             "component_ids": [component["component_id"]], "pixel_count": component["pixel_count"],
             "selected": True} for i, component in enumerate(layout["components"], 1)]
    return build_registration_spec({"file_id": stable_file_id(str(path)), "path": str(path),
                                    "spatial_layout": layout, "registered_sections": rows})


def _hash_pair(path):
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, path.with_suffix(".ibd"))]


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_common_axis_reverse_matches_canonical_and_exports_truthfully(tmp_path, dtype):
    canonical = _source(tmp_path, "canonical", [[100., 200., 300.]] * 3,
                        [[1., 2., 3.], [4., 5., 6.], [7., 8., 9.]], dtype=dtype)
    reverse = _source(tmp_path, "reverse", [[300., 200., 100.], [100., 300., 200.], [100., 200., 300.]],
                      [[3., 2., 1.], [4., 6., 5.], [7., 8., 9.]], dtype=dtype)
    hashes = _hash_pair(reverse)
    outputs = [tmp_path / "canonical.parquet", tmp_path / "reverse.parquet"]
    for source, output in zip((canonical, reverse), outputs):
        import_imzml(source, output, registration=_registration(source), block_size=2)
        validate_converted_table(output)
    assert pq.read_table(outputs[0]).to_pydict() == pq.read_table(outputs[1]).to_pydict()
    manifest = json.loads(outputs[1].with_suffix(".imzml.json").read_text(encoding="utf-8"))
    assert manifest["checked_spectrum_count"] == 3  # first axis read was not counted again
    assert manifest["reordered_spectrum_count"] == 2
    assert manifest["moved_peak_count"] == 4
    assert manifest["source_peak_order_preserved"] is False
    assert manifest["lossless_to_source"] is False
    assert "mz_order_normalized" in manifest["nonlossless_reasons"]
    assert manifest["conversion_qc"]["mz_order"]["reordered_spectrum_count"] == 2
    receipt = export_imzml(outputs[1], tmp_path / "export.zip", pixel_ids=[1, 3])
    assert receipt["lossless_to_source"] is False
    assert "mz_order_normalized" in receipt["nonlossless_reasons"]
    with zipfile.ZipFile(tmp_path / "export.zip") as archive:
        folder = tmp_path / "exported"
        archive.extractall(folder)
        assert json.loads(archive.read("export_manifest.json"))["source_peak_order_preserved"] is False
    parser = ImzMLParser(str(folder / "reverse.imzML"))
    try:
        np.testing.assert_array_equal(parser.getspectrum(0)[0], [100., 200., 300.])
        np.testing.assert_array_equal(parser.getspectrum(0)[1], [1., 2., 3.])
        np.testing.assert_array_equal(parser.getspectrum(1)[1], [7., 8., 9.])
    finally:
        parser.m.close()
    assert _hash_pair(reverse) == hashes


def test_continuous_shared_reverse_axis_preserves_all_intensities(tmp_path):
    source = _source(tmp_path, "shared_reverse", [[300., 200., 100.]] * 2,
                     [[30., 20., 10.], [60., 50., 40.]], mode="continuous")
    output = tmp_path / "shared.parquet"
    checked_import(source, output, registration=_registration(source))
    data = pq.read_table(output).to_pydict()
    assert data["100.000000"] == [10., 40.]
    assert data["200.000000"] == [20., 50.]
    assert data["300.000000"] == [30., 60.]
    meta = json.loads(output.with_suffix(".imzml.json").read_text(encoding="utf-8"))
    assert meta["checked_spectrum_count"] == meta["reordered_spectrum_count"] == 2
    assert meta["binary_validation"]["representation"] == "continuous"


@pytest.mark.parametrize("variable_lengths", [False, True])
@pytest.mark.parametrize("ppm", [0, 5])
def test_processed_reverse_and_fallback_match_canonical(tmp_path, variable_lengths, ppm):
    axes = [[100., 100.0004, 200.], [100.0002, 300., 400.]]
    values = [[1., 2., 3.], [4., 5., 6.]]
    if variable_lengths:
        axes[1], values[1] = axes[1][:-1], values[1][:-1]
    canonical = _source(tmp_path, "canonical", axes, values)
    reverse = _source(tmp_path, "reverse", [a[::-1] for a in axes], [v[::-1] for v in values])
    outputs = [tmp_path / "canonical.parquet", tmp_path / "reverse.parquet"]
    for source, output in zip((canonical, reverse), outputs):
        checked_import(source, output, registration=_registration(source), alignment_ppm=ppm)
    assert pq.read_table(outputs[0]).to_pydict() == pq.read_table(outputs[1]).to_pydict()
    meta = json.loads(outputs[1].with_suffix(".imzml.json").read_text(encoding="utf-8"))
    assert meta["input_representation"] == "processed_centroid_sparse"
    assert meta["checked_spectrum_count"] == meta["reordered_spectrum_count"] == 2
    assert meta["conversion_qc"]["source_peak_count"] == sum(map(len, axes))
    assert meta["lossless_to_source"] is False


@pytest.mark.parametrize("bad_axis,reason", [
    ([200., 100., 200.], "duplicate_mz"),
    ([200., 0., 100.], "nonpositive_mz"),
    ([100.0000002, 100.0000001], "feature_name_collision"),
])
def test_rejected_input_does_not_publish_pair(tmp_path, bad_axis, reason):
    source = _source(tmp_path, "bad", [bad_axis], [[1.] * len(bad_axis)])
    output = tmp_path / "output.parquet"
    with pytest.raises(ImzMLContractError) as caught:
        import_imzml(source, output, registration=_registration(source))
    assert caught.value.reason == reason
    assert not output.exists() and not output.with_suffix(".imzml.json").exists()


def test_later_pixel_duplicate_fails_without_publishing(tmp_path):
    source = _source(tmp_path, "later_bad", [[100., 200., 300.], [300., 100., 300.]],
                     [[1., 2., 3.], [6., 4., 5.]])
    output = tmp_path / "output.parquet"
    hashes = _hash_pair(source)
    with pytest.raises(ImzMLContractError) as caught:
        import_imzml(source, output, registration=_registration(source), block_size=1)
    assert caught.value.reason == "duplicate_mz"
    assert caught.value.context["source_index"] == 1
    assert not output.exists() and not output.with_suffix(".imzml.json").exists()
    assert not list(tmp_path.glob(".imzml-*"))
    assert _hash_pair(source) == hashes


def test_cancel_checks_each_pixel_and_never_publishes(tmp_path):
    from app.services.input_preparation import PreparationCancelled
    source = _source(tmp_path, "cancel", [[300., 100., 200.]] * 3, [[3., 1., 2.]] * 3)
    output = tmp_path / "output.parquet"
    calls = []
    def cancel():
        calls.append(True)
        return len(calls) == 3  # beginning, first pixel, second pixel
    with pytest.raises(PreparationCancelled):
        import_imzml(source, output, registration=_registration(source), cancel=cancel)
    assert len(calls) == 3
    assert not output.exists() and not output.with_suffix(".imzml.json").exists()
    assert not list(tmp_path.glob(".imzml-*"))
