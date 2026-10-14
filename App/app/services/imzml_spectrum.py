"""Validate and canonicalize paired spectra without modifying their source arrays."""
from __future__ import annotations

import json

import numpy as np


MZ_ORDER_POLICY = "stable_ascending_v1"
DUPLICATE_MZ_POLICY = "reject"
_EXAMPLE_LIMIT = 3
_INDEX_EXAMPLE_LIMIT = 10


class ImzMLContractError(ValueError):
    """An input cannot satisfy the import contract; optional diagnostics are bounded."""

    def __init__(self, message, *, reason=None, context=None):
        self.reason = reason
        self.context = dict(context or {})
        suffix = (" [" + json.dumps({"reason": reason, **self.context},
                                    ensure_ascii=False, allow_nan=False) + "]"
                  if reason else "")
        super().__init__(str(message) + suffix)


def _example_values(array, indices):
    examples = []
    for index in list(indices)[:_EXAMPLE_LIMIT]:
        value = array[int(index)]
        examples.append({"array_index": int(index),
                         "value": float(value) if np.isfinite(value) else str(value)})
    return examples


def feature_collision_details(axis, decimals=6):
    """Describe column-name collisions without rounding the stored masses."""
    seen, examples = {}, []
    for index, value in enumerate(axis):
        name = f"{float(value):.{decimals}f}"
        if name in seen:
            first = seen[name]
            examples.append({"column_name": name, "array_indices": [first, index],
                             "values": [float(axis[first]), float(value)]})
            if len(examples) == _EXAMPLE_LIMIT:
                break
        else:
            seen[name] = index
    return {"mz_dtype": str(np.asarray(axis).dtype), "examples": examples}


def normalize_spectrum(mz, intensity, *, source_index, context=None):
    """Return canonical arrays and one read's diagnostics, never accumulated QC.

    Only ordering is changed. Equal masses, invalid values and mismatched arrays
    remain errors. Advanced indexing preserves dtype and leaves raw arrays intact.
    """
    mz, intensity = np.asarray(mz), np.asarray(intensity)
    details = {**(context or {}), "source_index": int(source_index),
               "mz_dtype": str(mz.dtype), "intensity_dtype": str(intensity.dtype)}

    def fail(reason, message, **extra):
        raise ImzMLContractError(f"Spectrum {source_index}: {message}", reason=reason,
                                context={**details, **extra})

    if mz.ndim != 1 or intensity.ndim != 1:
        fail("array_shape", "spectral arrays must be one-dimensional",
             mz_shape=list(mz.shape), intensity_shape=list(intensity.shape))
    if not len(mz) or len(mz) != len(intensity):
        fail("array_length", "missing or mismatched arrays",
             mz_length=len(mz), intensity_length=len(intensity))
    for name, array in (("mz", mz), ("intensity", intensity)):
        invalid = ~np.isfinite(array)
        if invalid.any():
            fail(f"nonfinite_{name}", f"non-finite {name} values",
                 examples=_example_values(array, np.flatnonzero(invalid)[:_EXAMPLE_LIMIT]))
    invalid = mz <= 0
    if invalid.any():
        fail("nonpositive_mz", "m/z must be positive",
             examples=_example_values(mz, np.flatnonzero(invalid)[:_EXAMPLE_LIMIT]))
    invalid = intensity < 0
    if invalid.any():
        fail("negative_intensity", "negative intensities",
             examples=_example_values(intensity, np.flatnonzero(invalid)[:_EXAMPLE_LIMIT]))

    reordered = bool(np.any(mz[1:] < mz[:-1]))
    permutation = None
    moved = 0
    if reordered:
        # argsort indices/scratch, paired copies and comparison temporaries.
        required = int(len(mz) * (3 * np.dtype(np.intp).itemsize
                                  + mz.dtype.itemsize + intensity.dtype.itemsize))
        import psutil
        available = int(psutil.virtual_memory().available)
        if required > available // 2:
            fail("sort_memory", "insufficient memory for paired m/z sorting",
                 required_bytes=required, available_bytes=available)
        try:
            permutation = np.argsort(mz, kind="stable")
            moved = int(np.count_nonzero(permutation != np.arange(len(mz))))
            mz, intensity = mz[permutation], intensity[permutation]
        except MemoryError as exc:
            raise ImzMLContractError(
                f"Spectrum {source_index}: unable to allocate paired m/z sorting buffers",
                reason="sort_memory", context={**details, "required_bytes": required,
                                                "available_bytes": available},
            ) from exc
    duplicates = np.flatnonzero(mz[1:] == mz[:-1])
    if len(duplicates):
        examples = []
        for index in duplicates[:_EXAMPLE_LIMIT]:
            positions = [int(index), int(index + 1)]
            if permutation is not None:
                positions = [int(permutation[position]) for position in positions]
            examples.append({"source_array_indices": positions, "value": float(mz[index])})
        fail("duplicate_mz", "duplicate m/z values are unsupported", examples=examples)
    return mz, intensity, {"reordered": reordered, "moved_peak_count": moved}


def new_order_qc():
    """Create an accumulator owned by one final materialization attempt."""
    return {"checked_spectrum_count": 0, "reordered_spectrum_count": 0,
            "moved_peak_count": 0, "reordered_source_index_examples": [],
            "_seen_source_indices": set()}


def record_order_qc(qc, diagnostic, index):
    """Record each source pixel once, only in the final matrix-writing pass."""
    index = int(index)
    if index in qc["_seen_source_indices"]:
        raise ImzMLContractError(f"Spectrum {index}: order QC was counted twice")
    qc["_seen_source_indices"].add(index)
    qc["checked_spectrum_count"] += 1
    if diagnostic["reordered"]:
        qc["reordered_spectrum_count"] += 1
        qc["moved_peak_count"] += int(diagnostic["moved_peak_count"])
        if len(qc["reordered_source_index_examples"]) < _INDEX_EXAMPLE_LIMIT:
            qc["reordered_source_index_examples"].append(index)


def order_metadata(qc):
    return {"mz_order_policy": MZ_ORDER_POLICY,
            "duplicate_mz_policy": DUPLICATE_MZ_POLICY,
            "checked_spectrum_count": int(qc["checked_spectrum_count"]),
            "reordered_spectrum_count": int(qc["reordered_spectrum_count"]),
            "moved_peak_count": int(qc["moved_peak_count"]),
            "reordered_source_index_examples": list(qc["reordered_source_index_examples"]),
            "source_peak_order_preserved": qc["reordered_spectrum_count"] == 0,
            "order_normalization_preserves_pairs": True}
