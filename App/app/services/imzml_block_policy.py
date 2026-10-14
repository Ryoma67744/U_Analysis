"""Deterministic row groups: only persisted profile inputs decide the row count."""
from __future__ import annotations

BLOCK_PROFILE = "tims-f32-512-v1"
DEFAULT_BLOCK_ROWS = 512
DEFAULT_MEMORY_BUDGET_MB = 64


def _positive_integer(value, name):
    try:
        number = int(value)
        if isinstance(value, bool) or number < 1 or float(value) != number:
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    return number


def deterministic_block_rows(features, *, requested=DEFAULT_BLOCK_ROWS,
                             memory_budget_mb=DEFAULT_MEMORY_BUDGET_MB, width=4):
    """Budget the dense matrix, Arrow copy and working copy, plus column overhead.

    This is a working-block budget, not a cap on the complete parser/process RSS.
    Available RAM must be checked by the caller; it must never silently change
    the row-group layout of an already identified conversion revision.
    """
    features = _positive_integer(features, "features")
    requested = _positive_integer(requested, "requested rows")
    width = _positive_integer(width, "value width")
    budget = _positive_integer(memory_budget_mb, "memory budget MiB") * 1024 * 1024
    overhead = min(4 * 1024 * 1024, budget // 8) + features * 128
    per_row = features * width * 3 + 64
    safe_rows = (budget - overhead) // per_row
    if safe_rows < 1:
        raise ValueError("One pixel plus Arrow working space exceeds the imzML block memory budget")
    return min(requested, safe_rows)


def block_working_bytes(features, rows, *, memory_budget_mb=DEFAULT_MEMORY_BUDGET_MB,
                        width=4):
    """Estimate a fixed block using the same allocation model as its row policy.

    This does not select or shrink rows. Callers add any source-spectrum buffers
    that are independent of the saved matrix and then perform RAM admission.
    """
    features = _positive_integer(features, "features")
    rows = _positive_integer(rows, "rows")
    width = _positive_integer(width, "value width")
    budget = _positive_integer(memory_budget_mb, "memory budget MiB") * 1024 * 1024
    overhead = min(4 * 1024 * 1024, budget // 8) + features * 128
    return overhead + rows * (features * width * 3 + 64)


def require_available_memory(required_bytes, label):
    """Reject insufficient runtime RAM without changing a persisted profile."""
    import psutil
    required_bytes = _positive_integer(required_bytes, "required bytes")
    available = int(psutil.virtual_memory().available)
    if required_bytes > available // 2:
        raise ValueError(
            f"{label}に必要な作業メモリが不足しています "
            f"(required={required_bytes} bytes, available={available} bytes)"
        )
