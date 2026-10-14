"""A saved profile determines rows independently of runtime free RAM."""
import pytest

from app.services.imzml_block_policy import (
    deterministic_block_rows, block_working_bytes, require_available_memory,
)


def test_day4_feature_count_can_use_512_rows_at_default_budget():
    assert deterministic_block_rows(8927) == 512


def test_large_feature_count_reduces_rows_without_exceeding_working_budget():
    rows = deterministic_block_rows(100000)
    assert 1 <= rows < 512
    overhead = 4 * 1024 * 1024 + 100000 * 128
    assert overhead + rows * (100000 * 4 * 3 + 64) <= 64 * 1024 * 1024
    assert overhead + (rows + 1) * (100000 * 4 * 3 + 64) > 64 * 1024 * 1024


def test_explicit_smaller_requested_rows_are_preserved():
    assert deterministic_block_rows(100, requested=7) == 7


def test_runtime_ram_is_not_part_of_the_profile(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "virtual_memory", lambda: (_ for _ in ()).throw(AssertionError("RAM read")))
    assert deterministic_block_rows(8927) == 512


@pytest.mark.parametrize("kwargs", [
    {"features": 0}, {"features": -1}, {"features": True},
    {"features": 100, "requested": 0}, {"features": 100, "requested": 1.5},
    {"features": 100, "memory_budget_mb": 0}, {"features": 100, "width": 0},
    {"features": 999999999, "memory_budget_mb": 1},
])
def test_invalid_or_insufficient_budget_is_rejected(kwargs):
    with pytest.raises(ValueError):
        deterministic_block_rows(**kwargs)


@pytest.mark.parametrize("features,budget", [(8927, 64), (100000, 64), (400, 1)])
def test_shared_working_estimate_matches_row_policy(features, budget):
    rows = deterministic_block_rows(features, memory_budget_mb=budget)
    assert block_working_bytes(features, rows, memory_budget_mb=budget) <= budget * 1024 * 1024
    if rows < 512:
        assert block_working_bytes(features, rows + 1, memory_budget_mb=budget) > budget * 1024 * 1024


def test_ram_admission_accepts_boundary_and_rejects_without_changing_profile(monkeypatch):
    from types import SimpleNamespace
    import psutil
    rows = deterministic_block_rows(8927)
    required = block_working_bytes(8927, rows)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=required * 2))
    assert require_available_memory(required, "test") is None
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=required * 2 - 1))
    with pytest.raises(ValueError, match="required=.*available="):
        require_available_memory(required, "test")
    assert deterministic_block_rows(8927) == rows


@pytest.mark.parametrize("required", [0, -1, True, 1.5, float("inf")])
def test_invalid_admission_estimate_is_rejected(required):
    with pytest.raises(ValueError):
        require_available_memory(required, "test")
