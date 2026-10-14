"""Exact dictionary equivalence, bounded spill, cancellation and linear-log work."""
import errno
import math

import numpy as np
import pytest

from app.services.imzml_dictionary import DictionaryPolicy, SortedUniqueBuilder
from app.services.input_preparation import PreparationCancelled


@pytest.mark.parametrize("policy", [
    DictionaryPolicy(64, 8192, 16),
    DictionaryPolicy(7, 32, 3),
    DictionaryPolicy(1, 8, 1),
])
def test_exact_union_matches_independent_numpy_across_spill_policies(tmp_path, policy):
    rng = np.random.default_rng(177)
    arrays = [rng.integers(1, 151, size=45).astype(np.float64) / 8 for _ in range(15)]
    # Distinct binary masses must stay distinct even if a later 6dp schema rejects them.
    arrays += [np.array([100., np.nextafter(100., np.inf), 100.0000001, 100.0000002])]
    expected = np.unique(np.concatenate(arrays)).astype("<f8")
    output = tmp_path / "axis.bin"
    with SortedUniqueBuilder(tmp_path, policy=policy) as builder:
        for array in arrays:
            builder.add(array)
        result = builder.finish(output)
        assert result.count == len(expected)
        assert result.stats["source_values"] == sum(map(len, arrays))
        assert result.stats["max_retained_bytes"] <= policy.retained_bytes
        if policy.retained_bytes < 100:
            assert result.stats["external_merges"] > 0
    assert output.read_bytes() == expected.tobytes()
    assert not list(tmp_path.glob(".mz-runs-*"))


def test_one_dense_mass_region_does_not_create_an_unbounded_bucket(tmp_path):
    values = 100. + np.arange(4096, dtype=np.float64) / 100000
    policy = DictionaryPolicy(32, 128, 5)
    with SortedUniqueBuilder(tmp_path, policy=policy) as builder:
        builder.add(values[::-1])
        result = builder.finish(tmp_path / "axis.bin")
    np.testing.assert_array_equal(np.fromfile(result.path, dtype="<f8"), values)
    # A growing whole-dictionary union on every chunk violates this bound.
    runs = math.ceil(len(values) / policy.input_values)
    assert result.stats["merge_values_read"] <= len(values) * math.ceil(math.log2(runs))
    assert result.stats["external_merges"] > 0
    assert result.stats["file_opens"] < runs * 4


def test_many_repeated_values_keep_the_original_peak_count(tmp_path):
    values = np.arange(1, 18, dtype=np.float32)
    with SortedUniqueBuilder(tmp_path, policy=DictionaryPolicy(64, 512, 8)) as builder:
        for _ in range(101):
            builder.add(values)
        result = builder.finish(tmp_path / "axis.bin")
    assert result.count == 17
    assert result.stats["source_values"] == 1717
    np.testing.assert_array_equal(np.fromfile(result.path, dtype="<f8"), values)


def test_cancel_during_external_merge_removes_only_owned_temporary_runs(tmp_path, monkeypatch):
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    state = {"cancel": False}
    builder = SortedUniqueBuilder(tmp_path, policy=DictionaryPolicy(8, 8, 2),
                                  cancel=lambda: state["cancel"])
    original = builder._merge_external

    def cancel_merge(left, right):
        state["cancel"] = True
        return original(left, right)

    monkeypatch.setattr(builder, "_merge_external", cancel_merge)
    with pytest.raises(PreparationCancelled):
        with builder:
            builder.add(np.arange(1., 100.))
            builder.finish(tmp_path / "axis.bin")
    assert not (tmp_path / "axis.bin").exists()
    assert not list(tmp_path.glob(".mz-runs-*"))
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_failed_run_write_does_not_publish_dictionary(tmp_path, monkeypatch):
    builder = SortedUniqueBuilder(tmp_path, policy=DictionaryPolicy(8, 8, 2))

    def no_space(*_):
        raise OSError(errno.ENOSPC, "synthetic disk full")

    monkeypatch.setattr(builder, "_write", no_space)
    with pytest.raises(OSError, match="disk full"):
        with builder:
            builder.add(np.arange(1., 20.))
            builder.finish(tmp_path / "axis.bin")
    assert not (tmp_path / "axis.bin").exists()
    assert not list(tmp_path.glob(".mz-runs-*"))


def test_existing_dictionary_is_not_overwritten(tmp_path):
    output = tmp_path / "axis.bin"
    output.write_bytes(b"original")
    with SortedUniqueBuilder(tmp_path) as builder:
        builder.add([1., 2.])
        with pytest.raises(FileExistsError):
            builder.finish(output)
    assert output.read_bytes() == b"original"


@pytest.mark.parametrize("values", [[0., 1.], [-1., 2.], [1., np.inf], [np.nan]])
def test_invalid_dictionary_values_never_publish(tmp_path, values):
    with SortedUniqueBuilder(tmp_path) as builder:
        with pytest.raises(ValueError, match="positive finite"):
            builder.add(values)
    assert not list(tmp_path.glob(".mz-runs-*"))


def test_empty_dictionary_is_rejected(tmp_path):
    with SortedUniqueBuilder(tmp_path) as builder:
        with pytest.raises(ValueError, match="No m/z"):
            builder.finish(tmp_path / "axis.bin")


@pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf"), 1.5, True])
def test_invalid_memory_budget_rejected_before_creating_runs(tmp_path, budget):
    with pytest.raises(ValueError):
        SortedUniqueBuilder(tmp_path, memory_budget_mb=budget)
    assert not list(tmp_path.glob(".mz-runs-*"))
