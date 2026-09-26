"""Processed-centroid imzML -> aligned, zero-filled TIMS Parquet.

Variable-length per-pixel peak lists are a sparse representation.  This module
builds a deterministic master m/z feature dictionary, assigns every observed
peak to that dictionary, and writes the existing wide-Parquet contract with
unrecorded features represented as zero.  It intentionally mirrors the TIMS R
pipeline's ``align_mz_features`` rule: sorted unique m/z values are greedily
grouped from the smallest unused value within the requested ppm tolerance and
the group's representative is its median.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pyimzml.ImzMLParser import ImzMLParser

from app.services.imzml_io import ImzMLContractError, _coordinates, _pair, _spectrum
from app.services.imzml_spatial_layout import build_spatial_layout, strip_runtime_layout


ALIGNMENT_METHOD = "ua_sorted_unique_greedy_ppm_v1"
ZERO_SEMANTICS = "not_recorded_in_exported_centroid_spectrum"
DEFAULT_MAX_FEATURES = 100000
FEATURE_NAME_DECIMALS = 5
_BUCKET_WIDTH_DA = 1.0
_MAX_OPEN_BUCKETS = 64


def _check_cancel(cancel):
    if cancel and cancel():
        from app.services.input_preparation import PreparationCancelled
        raise PreparationCancelled("入力準備を停止しました。")


def _processed_axis_names(axis: np.ndarray) -> list[str]:
    """Return the numeric feature names expected by the TIMS R reader.

    The existing TIMS contract displays and identifies m/z features at five
    decimal places.  ``_group_unique_mz`` therefore consolidates any residual
    five-decimal collision before this function is called.
    """
    values = np.asarray(axis, dtype=np.float64)
    names = [f"{float(value):.{FEATURE_NAME_DECIMALS}f}" for value in values]
    if len(set(names)) != len(names):
        raise ImzMLContractError(
            "Aligned features still collide at the TIMS five-decimal feature precision"
        )
    return names


class _BucketWriter:
    def __init__(self, root: Path, max_open: int = _MAX_OPEN_BUCKETS):
        self.root = root
        self.max_open = max_open
        self.handles: OrderedDict[int, object] = OrderedDict()
        self.paths: set[int] = set()

    def append(self, bucket: int, values: np.ndarray) -> None:
        handle = self.handles.pop(bucket, None)
        if handle is None:
            if len(self.handles) >= self.max_open:
                _old_bucket, old = self.handles.popitem(last=False)
                old.close()
            path = self.root / f"mz_{bucket:+08d}.bin"
            handle = path.open("ab")
            self.paths.add(bucket)
        self.handles[bucket] = handle
        np.asarray(values, dtype="<f8").tofile(handle)

    def close(self) -> None:
        while self.handles:
            _bucket, handle = self.handles.popitem(last=False)
            handle.close()


def _write_mz_buckets(parser, root: Path, *, progress=None, cancel=None):
    writer = _BucketWriter(root)
    total_peaks = 0
    n_pixels = len(parser.coordinates)
    intensity_dtype = None
    try:
        for pixel in range(n_pixels):
            _check_cancel(cancel)
            mz, intensity = _spectrum(parser, pixel)
            current_dtype = np.dtype(intensity.dtype)
            if current_dtype not in (np.dtype("float32"), np.dtype("float64")):
                raise ImzMLContractError(f"Spectrum {pixel}: unsupported intensity precision {current_dtype}")
            if intensity_dtype is None:
                intensity_dtype = current_dtype
            elif current_dtype != intensity_dtype:
                raise ImzMLContractError(f"Spectrum {pixel}: mixed intensity precision")
            bucket_ids = np.floor(np.asarray(mz, dtype=np.float64) / _BUCKET_WIDTH_DA).astype(np.int64)
            starts = np.r_[0, np.flatnonzero(np.diff(bucket_ids)) + 1]
            ends = np.r_[starts[1:], len(mz)]
            for start, end in zip(starts, ends):
                writer.append(int(bucket_ids[start]), mz[start:end])
            total_peaks += len(mz)
            if progress:
                progress(pixel + 1, max(1, n_pixels * 2))
    finally:
        writer.close()
    if total_peaks < 1 or intensity_dtype is None:
        raise ImzMLContractError("No centroid peaks in processed imzML")
    return sorted(writer.paths), total_peaks, intensity_dtype


def _materialize_sorted_unique(bucket_root: Path, buckets: list[int], output: Path) -> int:
    count = 0
    with output.open("wb") as destination:
        previous = None
        for bucket in buckets:
            path = bucket_root / f"mz_{bucket:+08d}.bin"
            values = np.unique(np.fromfile(path, dtype="<f8"))
            if len(values) and previous is not None and not values[0] > previous:
                raise ImzMLContractError("Internal m/z bucket ordering is inconsistent")
            if len(values):
                previous = float(values[-1])
                values.astype("<f8", copy=False).tofile(destination)
                count += len(values)
    if count < 1:
        raise ImzMLContractError("No unique m/z values were collected")
    return count


def _group_unique_mz(unique_mz: np.memmap, ppm: float, map_path: Path):
    """Build deterministic ppm groups and a unique five-decimal TIMS axis.

    The initial grouping mirrors the existing R ``align_mz_features`` rule:
    start at the smallest unused exact m/z, take all unused exact values within
    ``ppm`` of that anchor, and use the median as representative.  Adjacent
    groups that would receive the same five-decimal TIMS feature name are then
    consolidated.  The latter is a sub-0.00001-Da compatibility step required
    by the current R feature-name contract, not a broad extra binning rule.
    """
    if not math.isfinite(ppm) or ppm < 0:
        raise ImzMLContractError("m/z alignment ppm must be a finite non-negative number")

    boundaries: list[tuple[int, int]] = []
    start = 0
    multiplier = 1.0 + float(ppm) * 1e-6
    while start < len(unique_mz):
        if ppm == 0:
            end = start + 1
        else:
            end = int(np.searchsorted(
                unique_mz, float(unique_mz[start]) * multiplier, side="right"
            ))
            end = max(start + 1, end)
        boundaries.append((start, end))
        start = end

    # Consolidate only adjacent groups whose representatives collide under the
    # five-decimal naming convention used by the current TIMS R reader.
    while True:
        representatives = [
            float(np.median(unique_mz[start:end])) for start, end in boundaries
        ]
        names = [f"{value:.{FEATURE_NAME_DECIMALS}f}" for value in representatives]
        if len(set(names)) == len(names):
            break
        merged: list[tuple[int, int]] = []
        index = 0
        while index < len(boundaries):
            start, end = boundaries[index]
            name = names[index]
            index += 1
            while index < len(boundaries) and names[index] == name:
                end = boundaries[index][1]
                index += 1
            merged.append((start, end))
        if len(merged) == len(boundaries):
            raise ImzMLContractError("Unable to resolve five-decimal feature-name collisions")
        boundaries = merged

    axis = np.asarray([
        float(np.median(unique_mz[start:end])) for start, end in boundaries
    ], dtype=np.float64)
    if np.any(~np.isfinite(axis)) or np.any(axis <= 0) or np.any(np.diff(axis) <= 0):
        raise ImzMLContractError("Master m/z feature axis is invalid")

    mapping = np.memmap(map_path, mode="w+", dtype="<i8", shape=(len(unique_mz),))
    group_sizes = []
    for feature_id, (start, end) in enumerate(boundaries):
        mapping[start:end] = feature_id
        group_sizes.append(int(end - start))
    mapping.flush()
    return axis, mapping, group_sizes


def _feature_limit(axis_count: int, *, max_features: int | None = None) -> int:
    limit = int(max_features if max_features is not None else
                os.environ.get("IMZML_PROCESSED_MAX_FEATURES", DEFAULT_MAX_FEATURES))
    if limit < 1:
        raise ImzMLContractError("IMZML_PROCESSED_MAX_FEATURES must be positive")
    if axis_count > limit:
        raise ImzMLContractError(
            f"Processed-centroid alignment produced {axis_count:,} features, exceeding the "
            f"configured limit {limit:,}. Increase m/z alignment (ppm) or explicitly raise "
            "IMZML_PROCESSED_MAX_FEATURES after checking memory and Parquet footer size."
        )
    return limit


def import_processed_imzml(path: str | Path, output: str | Path, *, alignment_ppm=0.0,
                           block_size=128, memory_budget_mb=64, progress=None,
                           cancel=None, max_features=None) -> dict:
    """Convert variable-length centroid spectra to a zero-filled master-feature table."""
    xml, ibd = _pair(path)
    output = Path(output).resolve()
    if output.suffix.lower() != ".parquet":
        raise ImzMLContractError("Output must be a .parquet sample filename")
    manifest_path = output.with_suffix(".imzml.json")
    if output.exists() or manifest_path.exists():
        raise FileExistsError("Sample already exists; choose a new revision name")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".processed-imzml-", dir=output.parent))
    binary = ibd.open("rb")
    parser = ImzMLParser(str(xml), ibd_file=binary)
    unique_mz = None
    feature_for_unique = None
    try:
        coords = _coordinates(parser)
        spatial_layout = build_spatial_layout(coords, include_preview=False)
        component_by_source = spatial_layout["_source_components"]
        bucket_root = temp_dir / "buckets"
        bucket_root.mkdir()
        buckets, source_peak_count, intensity_dtype = _write_mz_buckets(
            parser, bucket_root, progress=progress, cancel=cancel)
        unique_path = temp_dir / "unique_mz.bin"
        unique_count = _materialize_sorted_unique(bucket_root, buckets, unique_path)
        unique_mz = np.memmap(unique_path, mode="r", dtype="<f8", shape=(unique_count,))
        map_path = temp_dir / "feature_for_unique.bin"
        axis, feature_for_unique, source_value_counts = _group_unique_mz(
            unique_mz, float(alignment_ppm), map_path)
        _feature_limit(len(axis), max_features=max_features)
        names = _processed_axis_names(axis)
        estimated_bytes = max(
            64 * 1024 * 1024,
            int(source_peak_count) * (8 + np.dtype(intensity_dtype).itemsize) * 2
            + int(len(axis)) * 2048,
        )
        if shutil.disk_usage(output.parent).free < estimated_bytes:
            raise ImzMLContractError(
                f"Insufficient free space for processed-centroid conversion "
                f"(estimated minimum {estimated_bytes:,} bytes)"
            )

        # Keep one dense pixel block only.  The resulting Parquet is zero-heavy,
        # while the R reader converts each feature block directly to dgCMatrix.
        bytes_per_value = np.dtype(intensity_dtype).itemsize
        budget = max(1, int(memory_budget_mb)) * 1024 * 1024
        safe_block = max(1, budget // max(1, len(axis) * bytes_per_value * 3))
        actual_block = min(max(1, int(block_size)), safe_block)

        order = sorted(range(len(coords)), key=lambda index: (coords[index][1], coords[index][0]))
        fields = [pa.field("id", pa.int64()), pa.field("x", pa.float64()),
                  pa.field("y", pa.float64()),
                  pa.field("ua_coordinate_component", pa.string())]
        feature_type = pa.float32() if intensity_dtype == np.dtype("float32") else pa.float64()
        fields.extend(pa.field(name, feature_type) for name in names)
        fields.append(pa.field("annotation", pa.string()))
        persisted_layout = strip_runtime_layout(spatial_layout, strip_preview=True)
        schema = pa.schema(fields, metadata={
            b"mz_sorted": json.dumps(axis.tolist(), separators=(",", ":")).encode(),
            b"imzml_origin": b"1",
            b"ua_processed_centroid": b"1",
            b"ua_alignment_ppm": str(float(alignment_ppm)).encode(),
            b"ua_zero_semantics": ZERO_SEMANTICS.encode(),
            b"ua_spatial_layout": json.dumps(persisted_layout, ensure_ascii=False,
                                               sort_keys=True).encode("utf-8"),
        })

        temp_parquet = temp_dir / output.name
        mapping = []
        observed_peak_count = np.zeros(len(axis), dtype=np.int64)
        detected_pixel_count = np.zeros(len(axis), dtype=np.int64)
        min_observed_mz = np.full(len(axis), np.inf, dtype=np.float64)
        max_observed_mz = np.full(len(axis), -np.inf, dtype=np.float64)
        collision_peak_count = 0
        collision_pixel_count = 0
        output_nonzero_count = 0
        source_intensity_sum = 0.0
        output_intensity_sum = 0.0
        mass_error_sum = 0.0
        mass_error_max = 0.0

        with pq.ParquetWriter(temp_parquet, schema, compression="zstd") as writer:
            for start in range(0, len(order), actual_block):
                _check_cancel(cancel)
                indices = order[start:start + actual_block]
                matrix = np.zeros((len(indices), len(axis)), dtype=intensity_dtype)
                for row, source_index in enumerate(indices):
                    mz, intensities = _spectrum(parser, source_index)
                    if np.dtype(intensities.dtype) != intensity_dtype:
                        raise ImzMLContractError(
                            f"Spectrum {source_index}: mixed intensity precision")
                    positions = np.searchsorted(unique_mz, mz)
                    if (np.any(positions >= len(unique_mz)) or
                            not np.array_equal(np.asarray(unique_mz[positions]),
                                               np.asarray(mz, dtype=np.float64))):
                        raise ImzMLContractError(
                            f"Spectrum {source_index}: m/z was lost from the master dictionary")
                    feature_ids = np.asarray(feature_for_unique[positions], dtype=np.int64)
                    unique_features, multiplicity = np.unique(feature_ids, return_counts=True)
                    extra = int(np.sum(multiplicity - 1))
                    if extra:
                        collision_peak_count += extra
                        collision_pixel_count += 1
                    np.add.at(matrix[row], feature_ids,
                              np.asarray(intensities, dtype=intensity_dtype))
                    np.add.at(observed_peak_count, feature_ids, 1)
                    detected_pixel_count[unique_features] += 1
                    np.minimum.at(min_observed_mz, feature_ids, mz)
                    np.maximum.at(max_observed_mz, feature_ids, mz)
                    ppm_error = np.abs(np.asarray(mz, dtype=np.float64) - axis[feature_ids]) / axis[feature_ids] * 1e6
                    mass_error_sum += float(np.sum(ppm_error, dtype=np.float64))
                    if len(ppm_error):
                        mass_error_max = max(mass_error_max, float(np.max(ppm_error)))
                    source_intensity_sum += float(np.sum(intensities, dtype=np.float64))
                    output_intensity_sum += float(np.sum(matrix[row], dtype=np.float64))
                    output_nonzero_count += int(np.count_nonzero(matrix[row]))
                    mapping.append({"source_index": int(source_index),
                                    "coordinate": list(coords[source_index]),
                                    "component_id": component_by_source[source_index]})

                arrays = [pa.array(range(start + 1, start + len(indices) + 1), type=pa.int64()),
                          pa.array([coords[index][0] for index in indices], type=pa.float64()),
                          pa.array([coords[index][1] for index in indices], type=pa.float64()),
                          pa.array([component_by_source[index] for index in indices], type=pa.string())]
                arrays.extend(pa.array(matrix[:, column], type=feature_type)
                              for column in range(len(axis)))
                arrays.append(pa.array(["Unannotated"] * len(indices), type=pa.string()))
                writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
                if progress:
                    progress(len(coords) + min(start + len(indices), len(coords)),
                             max(1, len(coords) * 2))

        tolerance = max(1e-6, abs(source_intensity_sum) * 2e-7)
        if abs(source_intensity_sum - output_intensity_sum) > tolerance:
            raise ImzMLContractError(
                "Intensity sum changed while constructing the zero-filled feature matrix")
        finite_min = np.where(np.isfinite(min_observed_mz), min_observed_mz, axis)
        finite_max = np.where(np.isfinite(max_observed_mz), max_observed_mz, axis)
        spread_ppm = (finite_max - finite_min) / axis * 1e6
        total_cells = len(coords) * len(axis)
        zero_fraction = 1.0 - (output_nonzero_count / total_cells if total_cells else 0.0)
        feature_qc = {
            "source_unique_mz_count": int(unique_count),
            "source_value_count_by_feature": [int(v) for v in source_value_counts],
            "observed_peak_count": observed_peak_count.tolist(),
            "detected_pixel_count": detected_pixel_count.tolist(),
            "min_observed_mz": finite_min.tolist(),
            "max_observed_mz": finite_max.tolist(),
            "mass_spread_ppm": spread_ppm.tolist(),
        }
        conversion_qc = {
            "source_peak_count": int(source_peak_count),
            "source_unique_mz_count": int(unique_count),
            "master_feature_count": int(len(axis)),
            "output_nonzero_count": int(output_nonzero_count),
            "collision_peak_count": int(collision_peak_count),
            "collision_pixel_count": int(collision_pixel_count),
            "source_intensity_sum": float(source_intensity_sum),
            "output_intensity_sum": float(output_intensity_sum),
            "zero_fraction": float(zero_fraction),
            "mean_mass_error_ppm": float(mass_error_sum / source_peak_count),
            "max_mass_error_ppm": float(mass_error_max),
        }
        manifest = {
            "schema_version": 3,
            "source_xml": str(xml), "source_ibd": str(ibd),
            "input_representation": "processed_centroid_sparse",
            "output_representation": "aligned_zero_filled_wide_parquet",
            "alignment_method": ALIGNMENT_METHOD,
            "feature_name_precision_decimals": FEATURE_NAME_DECIMALS,
            "alignment_ppm": float(alignment_ppm),
            "zero_semantics": ZERO_SEMANTICS,
            "zero_is_absolute_absence": False,
            "collision_intensity_rule": "sum_within_pixel_feature",
            "mz_axis": axis.tolist(), "column_names": names,
            "mz_dtype": "float64", "intensity_dtype": str(intensity_dtype),
            "source_coordinates": mapping, "spatial_layout": persisted_layout,
            "normalization": "unknown", "spectrum_type": "centroid",
            "pixel_count": len(coords), "feature_count": len(axis),
            "feature_qc": feature_qc, "conversion_qc": conversion_qc,
        }
        temp_manifest = temp_dir / manifest_path.name
        temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False,
                                             separators=(",", ":")), encoding="utf-8")
        pending = output.with_suffix(".imzml.pending")
        pending.touch(exist_ok=False)
        try:
            os.replace(temp_parquet, output)
            os.replace(temp_manifest, manifest_path)
        except BaseException:
            output.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            raise
        finally:
            pending.unlink(missing_ok=True)
        return {"parquet": str(output), "manifest": str(manifest_path),
                "pixels": len(coords), "features": len(axis),
                "source_peaks": int(source_peak_count),
                "alignment_ppm": float(alignment_ppm),
                "representation": "processed_centroid_sparse"}
    finally:
        try:
            if feature_for_unique is not None:
                feature_for_unique.flush()
        except Exception:
            pass
        del feature_for_unique
        del unique_mz
        binary.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
