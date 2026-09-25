"""Small independent expectations for the common-axis exchange boundary."""
import json
import zipfile

import numpy as np
import pyarrow.parquet as pq
import pytest
from pyimzml.ImzMLParser import ImzMLParser
from pyimzml.ImzMLWriter import ImzMLWriter

from app.services.imzml_io import ImzMLContractError, import_imzml, export_imzml


def _input(tmp_path, axes=None):
    path = tmp_path / "source.imzML"
    with ImzMLWriter(str(path), mz_dtype=np.float64, intensity_dtype=np.float32) as writer:
        for i, coords in enumerate([(2, 2, 1), (1, 1, 1), (2, 1, 1), (1, 2, 1)]):
            axis = axes[i] if axes is not None else [100.0, 101.0]
            writer.addSpectrum(np.asarray(axis), np.asarray([i + 1, i + 10], dtype=np.float32), coords)
    return path


def test_four_pixels_roundtrip_selection(tmp_path):
    source = _input(tmp_path)
    sample = tmp_path / "sample.parquet"
    import_imzml(source, sample, block_size=2)
    table = pq.read_table(sample).to_pydict()
    assert table["id"] == [1, 2, 3, 4]
    assert list(zip(table["x"], table["y"])) == [(1, 1), (2, 1), (1, 2), (2, 2)]
    assert table["100.000000"] == [2., 3., 4., 1.]
    assert json.loads(sample.with_suffix(".imzml.json").read_text())["source_coordinates"][0]["source_index"] == 1
    target = tmp_path / "selected.zip"
    export_imzml(sample, target, pixel_ids=[1, 4])
    with zipfile.ZipFile(target) as zf:
        assert {"sample.imzML", "sample.ibd", "export_manifest.json"} == set(zf.namelist())
        zf.extractall(tmp_path / "unpacked")
    parser = ImzMLParser(str(tmp_path / "unpacked" / "sample.imzML"))
    assert parser.coordinates == [(1, 1, 1), (2, 2, 1)]
    np.testing.assert_array_equal(parser.getspectrum(0)[1], [2, 11])
    np.testing.assert_array_equal(parser.getspectrum(1)[1], [1, 10])


def test_reject_different_axis_without_partial_registration(tmp_path):
    source = _input(tmp_path, [[100., 101.], [100., 101.], [100., 102.], [100., 101.]])
    output = tmp_path / "sample.parquet"
    with pytest.raises(ImzMLContractError, match="individual m/z axis"):
        import_imzml(source, output)
    assert not output.exists()
    assert not output.with_suffix(".imzml.json").exists()


def test_reject_feature_name_collision(tmp_path):
    source = _input(tmp_path, [[100.000001, 100.000002]] * 4)
    with pytest.raises(ImzMLContractError, match="collide"):
        import_imzml(source, tmp_path / "sample.parquet")
