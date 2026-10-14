"""Common-axis row groups use the saved dtype and reject low RAM explicitly."""
import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import numpy as np
import pyarrow.parquet as pq
from pyimzml.ImzMLWriter import ImzMLWriter
import pytest

from app.services import imzml_io
from app.services.imzml_block_policy import deterministic_block_rows, block_working_bytes
from app.services.imzml_validation import checked_import
from tests.test_imzml_io import _input, _registration


def test_direct_common_import_rejects_insufficient_ram_and_cleans_up(tmp_path, monkeypatch):
    import psutil
    source = _input(tmp_path)
    registration = _registration(source)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=1024))
    output = tmp_path / "low-ram.parquet"
    with pytest.raises(imzml_io.ImzMLContractError, match="メモリ"):
        imzml_io.import_imzml(source, output, registration=registration)
    assert not output.exists()
    assert not output.with_suffix(".imzml.json").exists()
    assert not list(tmp_path.glob(".imzml-*"))


@pytest.mark.parametrize("converter", [imzml_io.import_imzml, checked_import], ids=["direct", "checked"])
def test_float64_source_uses_float32_row_profile_and_separate_source_admission(
        tmp_path, monkeypatch, converter):
    source = tmp_path / "wide.imzML"
    axis = np.arange(100.0, 500.0, dtype=np.float64)
    with ImzMLWriter(str(source), mode="continuous", mz_dtype=np.float64,
                     intensity_dtype=np.float64) as writer:
        for index in range(200):
            writer.addSpectrum(axis, np.arange(400, dtype=np.float64) + index, (1, index + 1, 1))
    tree = ET.parse(source)
    for node in tree.iter():
        if node.get("accession") == "MS:1000511":
            node.set("value", "1")
    tree.write(source, encoding="utf-8", xml_declaration=True)
    registration = _registration(source)
    requested = []
    original = imzml_io.require_available_memory

    def observe(required, label):
        requested.append(required)
        return original(required, label)

    monkeypatch.setattr(imzml_io, "require_available_memory", observe)
    output = tmp_path / "wide.parquet"
    converter(source, output, registration=registration, memory_budget_mb=1)
    expected_rows = deterministic_block_rows(400, memory_budget_mb=1, width=4)
    assert expected_rows != deterministic_block_rows(400, memory_budget_mb=1, width=8)
    manifest = json.loads(output.with_suffix(".imzml.json").read_text(encoding="utf-8"))
    assert manifest["conversion_qc"]["parquet_block_rows"] == expected_rows
    assert manifest["source_intensity_dtype"] == "float64"
    assert manifest["intensity_dtype"] == "float32"
    assert requested == [block_working_bytes(400, expected_rows, memory_budget_mb=1)
                         + 400 * (8 * 2 + 8 + 8 * 3 + 4)]
    with pq.ParquetFile(output) as parquet:
        assert parquet.metadata.row_group(0).num_rows == expected_rows
        assert parquet.metadata.num_rows == 200
