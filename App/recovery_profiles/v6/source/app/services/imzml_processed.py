"""processed-centroid imzMLを全切片保持の標準TIMS Parquetへ変換する。"""
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

from app.services.imzml_io import (
    ImzMLContractError, _coordinates, _pair, _spectrum, _require_registration, _publish_pair,
)
from app.services.imzml_registration import section_for_component
from app.services.imzml_spatial_layout import build_spatial_layout, strip_runtime_layout
from app.services.tims_parquet_contract import feature_names, standard_schema


ALIGNMENT_METHOD = "ua_sorted_unique_greedy_ppm_v3_6dp_strict"
ZERO_SEMANTICS = "not_recorded_in_exported_centroid_spectrum"
DEFAULT_MAX_FEATURES = 100000
_BUCKET_WIDTH_DA = 1.0
_MAX_OPEN_BUCKETS = 64


def _check_cancel(cancel):
    if cancel and cancel():
        from app.services.input_preparation import PreparationCancelled
        raise PreparationCancelled("入力準備を停止しました。")


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
    source_intensity_dtype = None
    try:
        for pixel in range(n_pixels):
            _check_cancel(cancel)
            mz, intensity = _spectrum(parser, pixel)
            current_dtype = np.dtype(intensity.dtype)
            if current_dtype not in (np.dtype("float32"), np.dtype("float64")):
                raise ImzMLContractError(
                    f"Spectrum {pixel}: unsupported intensity precision {current_dtype}"
                )
            if source_intensity_dtype is None:
                source_intensity_dtype = current_dtype
            elif current_dtype != source_intensity_dtype:
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
    if total_peaks < 1 or source_intensity_dtype is None:
        raise ImzMLContractError("No centroid peaks in processed imzML")
    return sorted(writer.paths), total_peaks, source_intensity_dtype


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
    if not math.isfinite(ppm) or ppm < 0:
        raise ImzMLContractError("m/z alignment ppm must be finite and non-negative")
    boundaries: list[tuple[int, int]] = []
    start = 0
    multiplier = 1.0 + float(ppm) * 1e-6
    while start < len(unique_mz):
        end = start + 1 if ppm == 0 else int(np.searchsorted(
            unique_mz, float(unique_mz[start]) * multiplier, side="right"
        ))
        boundaries.append((start, max(start + 1, end)))
        start = max(start + 1, end)

    # ★ ver74.0: 桁丸めだけで別groupを合算するとppm=0でも異なる質量が消えていた。
    # 6桁で区別可能な軸は維持し、表現できない衝突は設定外の併合をせず明示拒否する。
    axis = np.asarray([float(np.median(unique_mz[a:b])) for a, b in boundaries], dtype=np.float64)
    try:
        feature_names(axis)
    except ValueError as exc:
        raise ImzMLContractError(str(exc)) from exc
    mapping = np.memmap(map_path, mode="w+", dtype="<i8", shape=(len(unique_mz),))
    source_value_counts = []
    for feature_id, (a, b) in enumerate(boundaries):
        mapping[a:b] = feature_id
        source_value_counts.append(int(b - a))
    mapping.flush()
    return axis, mapping, source_value_counts


def _feature_limit(axis_count: int, max_features=None):
    limit = int(max_features if max_features is not None else
                os.environ.get("IMZML_PROCESSED_MAX_FEATURES", DEFAULT_MAX_FEATURES))
    if limit < 1:
        raise ImzMLContractError("IMZML_PROCESSED_MAX_FEATURES must be positive")
    if axis_count > limit:
        raise ImzMLContractError(
            f"processed-centroid alignment produced {axis_count:,} features; "
            f"limit is {limit:,}. Review the ppm tolerance before raising the limit."
        )


def _validate_registration(registration: dict, spatial_layout: dict):
    _require_registration(registration, spatial_layout)


def import_processed_imzml(
    path: str | Path,
    output: str | Path,
    *,
    registration: dict,
    alignment_ppm=0.0,
    block_size=128,
    memory_budget_mb=64,
    progress=None,
    cancel=None,
    max_features=None,
) -> dict:
    """可変長peak listを全切片保持・0補完の標準TIMS Parquetへ変換する。"""
    # ★ ver74.0: 直接APIも強度・bucketを生成する前に全切片登録を検査する。
    _require_registration(registration)
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
    unique_mz = feature_for_unique = None
    try:
        parser = ImzMLParser(str(xml), ibd_file=binary)
        coords = _coordinates(parser)
        spatial_layout = build_spatial_layout(coords, include_preview=False)
        _validate_registration(registration, spatial_layout)
        component_by_source = spatial_layout["_source_components"]

        bucket_root = temp_dir / "buckets"
        bucket_root.mkdir()
        buckets, source_peak_count, source_intensity_dtype = _write_mz_buckets(
            parser, bucket_root, progress=progress, cancel=cancel
        )
        unique_path = temp_dir / "unique_mz.bin"
        unique_count = _materialize_sorted_unique(bucket_root, buckets, unique_path)
        unique_mz = np.memmap(unique_path, mode="r", dtype="<f8", shape=(unique_count,))
        map_path = temp_dir / "feature_for_unique.bin"
        axis, feature_for_unique, source_value_counts = _group_unique_mz(
            unique_mz, float(alignment_ppm), map_path
        )
        _feature_limit(len(axis), max_features=max_features)
        names = feature_names(axis)

        # CSV互換標準はfloat32。原精度と丸め誤差はmanifestへ記録する。
        output_dtype = np.dtype("float32")
        budget = max(1, int(memory_budget_mb)) * 1024 * 1024
        safe_block = max(1, budget // max(1, len(axis) * output_dtype.itemsize * 3))
        actual_block = min(max(1, int(block_size)), safe_block)
        estimated_bytes = max(
            64 * 1024 * 1024,
            int(source_peak_count) * 16 + int(len(axis)) * 2048,
        )
        if shutil.disk_usage(output.parent).free < estimated_bytes:
            raise ImzMLContractError("processed-centroid変換先の空き容量が不足しています")

        order = sorted(range(len(coords)), key=lambda index: (coords[index][1], coords[index][0]))
        persisted_layout = strip_runtime_layout(spatial_layout, strip_preview=True)
        schema = standard_schema(axis, registration, extra_metadata={
            "imzml_origin": "1",
            "ua_processed_centroid": "1",
            "ua_alignment_ppm": float(alignment_ppm),
            "ua_zero_semantics": ZERO_SEMANTICS,
            "ua_spatial_layout": json.dumps(
                persisted_layout, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")
            ),
        })
        temp_parquet = temp_dir / output.name
        mapping = []
        annotation_counts: dict[str, int] = {}
        observed_peak_count = np.zeros(len(axis), dtype=np.int64)
        detected_pixel_count = np.zeros(len(axis), dtype=np.int64)
        min_observed_mz = np.full(len(axis), np.inf, dtype=np.float64)
        max_observed_mz = np.full(len(axis), -np.inf, dtype=np.float64)
        collision_peak_count = collision_pixel_count = output_nonzero_count = 0
        source_intensity_sum = output_intensity_sum = source_to_float32_abs_error = 0.0
        mass_error_sum = mass_error_max = 0.0

        with pq.ParquetWriter(
            temp_parquet, schema, compression="zstd", use_dictionary=["annotation"]
        ) as writer:
            for start in range(0, len(order), actual_block):
                _check_cancel(cancel)
                indices = order[start:start + actual_block]
                matrix = np.zeros((len(indices), len(axis)), dtype=output_dtype)
                annotations: list[str] = []
                for row, source_index in enumerate(indices):
                    mz, intensities = _spectrum(parser, source_index)
                    positions = np.searchsorted(unique_mz, mz)
                    if (np.any(positions >= len(unique_mz)) or
                            not np.array_equal(np.asarray(unique_mz[positions]),
                                               np.asarray(mz, dtype=np.float64))):
                        raise ImzMLContractError(
                            f"Spectrum {source_index}: m/z was lost from master dictionary"
                        )
                    feature_ids = np.asarray(feature_for_unique[positions], dtype=np.int64)
                    unique_features, multiplicity = np.unique(feature_ids, return_counts=True)
                    extra = int(np.sum(multiplicity - 1))
                    if extra:
                        collision_peak_count += extra
                        collision_pixel_count += 1
                    with np.errstate(over="ignore", under="ignore"):
                        cast = np.asarray(intensities, dtype=output_dtype)
                    if not np.isfinite(cast).all():
                        raise ImzMLContractError(f"Spectrum {source_index}: float32 intensity overflow")
                    source_to_float32_abs_error += float(np.sum(
                        np.abs(np.asarray(intensities, dtype=np.float64)
                               - np.asarray(cast, dtype=np.float64)), dtype=np.float64
                    ))
                    with np.errstate(over="ignore"):
                        np.add.at(matrix[row], feature_ids, cast)
                    if not np.isfinite(matrix[row]).all():
                        raise ImzMLContractError(f"Spectrum {source_index}: aligned intensity overflow")
                    np.add.at(observed_peak_count, feature_ids, 1)
                    detected_pixel_count[unique_features] += 1
                    np.minimum.at(min_observed_mz, feature_ids, mz)
                    np.maximum.at(max_observed_mz, feature_ids, mz)
                    ppm_error = np.abs(np.asarray(mz, dtype=np.float64) - axis[feature_ids]) \
                        / axis[feature_ids] * 1e6
                    mass_error_sum += float(np.sum(ppm_error, dtype=np.float64))
                    if len(ppm_error):
                        mass_error_max = max(mass_error_max, float(np.max(ppm_error)))
                    source_intensity_sum += float(np.sum(intensities, dtype=np.float64))
                    output_intensity_sum += float(np.sum(matrix[row], dtype=np.float64))
                    output_nonzero_count += int(np.count_nonzero(matrix[row]))
                    component_id = component_by_source[source_index]
                    section = section_for_component(registration, component_id)
                    annotation = section["section_display_name"]
                    annotations.append(annotation)
                    annotation_counts[annotation] = annotation_counts.get(annotation, 0) + 1
                    mapping.append({
                        "source_index": int(source_index),
                        "coordinate": list(coords[source_index]),
                        "component_id": component_id,
                        "section_id": section["section_id"],
                        "section_display_name": annotation,
                    })

                arrays = [
                    pa.array(range(start + 1, start + len(indices) + 1), type=pa.int64()),
                    pa.array([coords[index][0] for index in indices], type=pa.float64()),
                    pa.array([coords[index][1] for index in indices], type=pa.float64()),
                ]
                arrays.extend(pa.array(matrix[:, column], type=pa.float32())
                              for column in range(len(axis)))
                arrays.append(pa.array(annotations, type=pa.string()))
                writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
                if progress:
                    progress(len(coords) + min(start + len(indices), len(coords)),
                             max(1, len(coords) * 2))

        expected_counts = {
            row["section_display_name"]: int(row["pixel_count"])
            for row in registration["section_registry"]
        }
        if annotation_counts != expected_counts:
            raise ImzMLContractError(
                f"切片別pixel数が登録情報と一致しません: {annotation_counts} != {expected_counts}"
            )
        tolerance = max(1e-5, abs(output_intensity_sum) * 5e-7)
        if abs(source_intensity_sum - output_intensity_sum) > \
                tolerance + source_to_float32_abs_error:
            raise ImzMLContractError("float32変換後の総強度が許容誤差を超えて変化しました")

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
            "float32_absolute_error_sum": float(source_to_float32_abs_error),
            "zero_fraction": float(zero_fraction),
            "mean_mass_error_ppm": float(mass_error_sum / source_peak_count),
            "max_mass_error_ppm": float(mass_error_max),
            "annotation_pixel_counts": annotation_counts,
        }
        manifest = {
            "schema_version": 4,
            "source_xml": str(xml), "source_ibd": str(ibd),
            "input_representation": "processed_centroid_sparse",
            "output_representation": "tims_standard_all_sections",
            "alignment_method": ALIGNMENT_METHOD,
            "feature_name_precision_decimals": 6,
            "alignment_ppm": float(alignment_ppm),
            "zero_semantics": ZERO_SEMANTICS,
            "zero_is_absolute_absence": False,
            "collision_intensity_rule": "sum_within_pixel_feature",
            "mz_axis": axis.tolist(), "column_names": names,
            "mz_dtype": "float64", "intensity_dtype": "float32",
            "source_intensity_dtype": str(source_intensity_dtype),
            "source_coordinates": mapping,
            "spatial_layout": persisted_layout,
            "section_registry": registration["section_registry"],
            "registration_hash": registration["registration_hash"],
            "normalization": "unknown", "spectrum_type": "centroid",
            "pixel_count": len(coords), "feature_count": len(axis),
            "feature_qc": feature_qc, "conversion_qc": conversion_qc,
            "lossless_to_source": False,
        }
        temp_manifest = temp_dir / manifest_path.name
        temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False,
                                             separators=(",", ":")), encoding="utf-8")
        _publish_pair(temp_parquet, temp_manifest, output)
        return {
            "parquet": str(output), "manifest": str(manifest_path),
            "pixels": len(coords), "features": len(axis),
            "source_peaks": int(source_peak_count),
        }
    finally:
        if feature_for_unique is not None:
            del feature_for_unique
        if unique_mz is not None:
            del unique_mz
        binary.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
