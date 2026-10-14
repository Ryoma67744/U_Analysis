"""Grouping bounds apply after ppm grouping and before large allocations."""
import numpy as np
import pytest

from app.services import imzml_processed as processed
from app.services.imzml_io import ImzMLContractError


def _raw_memmap(tmp_path, values):
    path = tmp_path / "sorted_unique.bin"
    np.asarray(values, dtype="<f8").tofile(path)
    return np.memmap(path, mode="r", dtype="<f8")


@pytest.mark.parametrize("explicit", [True, False])
def test_feature_limit_stops_before_median_or_mapping(tmp_path, monkeypatch, explicit):
    values = _raw_memmap(tmp_path, np.arange(100.0, 120.0))
    target = tmp_path / "mapping.bin"

    def unexpected(*args, **kwargs):
        pytest.fail("The grouped-feature limit must precede median and mapping allocation")

    monkeypatch.setattr(processed.np, "median", unexpected)
    monkeypatch.setattr(processed.np, "memmap", unexpected)
    monkeypatch.setenv("IMZML_PROCESSED_MAX_FEATURES", "3")
    options = {"max_features": 3} if explicit else {}
    try:
        with pytest.raises(ImzMLContractError, match=r"limit.*3"):
            processed._group_unique_mz(values, 0.0, target, **options)
        assert not target.exists()
    finally:
        values._mmap.close()


def test_raw_unique_count_can_exceed_limit_when_ppm_groups_fit(tmp_path):
    # Twenty distinct masses are valid when the requested ppm policy creates
    # one group; the raw dictionary length is not the feature limit.
    raw = np.linspace(100.0, 100.01, 20, dtype=np.float64)
    values = _raw_memmap(tmp_path, raw)
    mapping = None
    try:
        axis, mapping, counts = processed._group_unique_mz(
            values, 1000.0, tmp_path / "mapping.bin", max_features=1,
        )
        np.testing.assert_array_equal(axis, [np.median(raw)])
        np.testing.assert_array_equal(mapping, np.zeros(len(raw), dtype=np.int64))
        assert counts == [len(raw)]
    finally:
        if mapping is not None:
            mapping._mmap.close()
        values._mmap.close()


@pytest.mark.parametrize("raw", [
    np.asarray([100.0]),
    np.asarray([100.0, 100.25]),
    np.asarray([100.0, 100.125, 100.5]),
    np.asarray([100.0, 100.125, 100.25, 100.5]),
    np.asarray([1.0, np.nextafter(1.0, 2.0),
                np.nextafter(np.nextafter(1.0, 2.0), 2.0), 1.125]),
    np.linspace(100.0, 101.0, 4097),
    np.linspace(100.0, 101.0, 4098),
    np.asarray([1e100, np.nextafter(1e100, np.inf), 1.125e100, 1.5e100]),
])
def test_sorted_group_median_matches_old_arithmetic_with_constant_input(
        tmp_path, monkeypatch, raw):
    # Record the old full-group operation before observing the optimized call.
    expected = np.median(raw)
    original_median = np.median
    sizes = []

    def bounded_median(value, *args, **kwargs):
        sizes.append(np.asarray(value).size)
        assert sizes[-1] <= 2, "Sorted-run median must read only its central value(s)"
        return original_median(value, *args, **kwargs)

    values = _raw_memmap(tmp_path, raw)
    mapping = None
    monkeypatch.setattr(processed.np, "median", bounded_median)
    try:
        axis, mapping, counts = processed._group_unique_mz(
            values, 1_000_000.0, tmp_path / "mapping.bin", max_features=1,
        )
        assert axis.dtype == np.dtype("float64")
        assert axis.tobytes() == np.asarray([expected], dtype=np.float64).tobytes()
        np.testing.assert_array_equal(mapping, np.zeros(len(raw), dtype=np.int64))
        assert counts == [len(raw)]
        assert sizes
    finally:
        if mapping is not None:
            mapping._mmap.close()
        values._mmap.close()


def test_group_limit_allows_exact_boundary(tmp_path):
    values = _raw_memmap(tmp_path, [100.0, 200.0, 300.0])
    mapping = None
    try:
        axis, mapping, counts = processed._group_unique_mz(
            values, 0.0, tmp_path / "mapping.bin", max_features=3,
        )
        np.testing.assert_array_equal(axis, values)
        np.testing.assert_array_equal(mapping, [0, 1, 2])
        assert counts == [1, 1, 1]
    finally:
        if mapping is not None:
            mapping._mmap.close()
        values._mmap.close()


def test_single_large_group_mapping_checks_cancel_between_write_chunks(tmp_path, monkeypatch):
    from app.services.input_preparation import PreparationCancelled
    values = _raw_memmap(tmp_path, np.linspace(100.0, 101.0, 300_000))
    target = tmp_path / "mapping.bin"
    original = processed.np.memmap
    state = {"mapping_started": False, "checks": 0}
    mappings = []

    def observed_memmap(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("mode") == "w+":
            mappings.append(result)
            state["mapping_started"] = True
        return result

    def cancel():
        if state["mapping_started"]:
            state["checks"] += 1
        return state["checks"] >= 3

    monkeypatch.setattr(processed.np, "memmap", observed_memmap)
    try:
        with pytest.raises(PreparationCancelled):
            processed._group_unique_mz(values, 1_000_000.0, target, max_features=1, cancel=cancel)
        assert state["checks"] == 3
        assert mappings[0]._mmap.closed
        target.unlink()
    finally:
        values._mmap.close()
