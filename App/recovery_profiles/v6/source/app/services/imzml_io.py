"""全切片を保持する標準Parquetと、変換来歴付きimzMLの交換。

The source pair is never modified. A manifest next to each imported sample
retains information that cannot be represented by the legacy table schema.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pyimzml.ImzMLParser import ImzMLParser
from pyimzml.ImzMLWriter import ImzMLWriter

from app.services.imzml_spatial_layout import build_spatial_layout, strip_runtime_layout


class ImzMLContractError(ValueError):
    """The input cannot be represented without inventing or losing spectra."""


def _pair(path: str | Path) -> tuple[Path, Path]:
    xml = Path(path).resolve()
    if xml.suffix.lower() != ".imzml" or not xml.is_file():
        raise ImzMLContractError("An existing .imzML file is required")
    matches = [p for p in xml.parent.iterdir()
               if p.is_file() and p.stem == xml.stem and p.suffix.lower() == ".ibd"]
    if len(matches) != 1:
        raise ImzMLContractError("Exactly one matching .ibd file is required")
    return xml, matches[0]


def _axis_names(axis: np.ndarray) -> list[str]:
    names = [f"{mz:.6f}" for mz in axis]
    if len(set(names)) != len(names):
        raise ImzMLContractError("m/z values collide at six decimal places")
    return names


def _require_registration(registration, spatial_layout=None):
    from app.services.tims_parquet_contract import (
        TimsParquetContractError, validate_registration_spec,
    )
    try:
        return validate_registration_spec(registration, spatial_layout=spatial_layout)
    except TimsParquetContractError as exc:
        raise ImzMLContractError(str(exc)) from exc


def _publish_pair(temp_parquet, temp_manifest, output):
    """低レベルAPIでも既存出力を上書きせず、片方だけの完成品を残さない。"""
    from filelock import FileLock
    output = Path(output)
    manifest_path = output.with_suffix(".imzml.json")
    pending = output.with_suffix(".imzml.pending")
    # ★ ver74.0: processed経路もsidecar公開失敗時の巻戻しとpending保護を共通化する。
    with FileLock(str(output) + ".import.lock", timeout=0):
        if output.exists() or manifest_path.exists() or pending.exists():
            raise FileExistsError("Sample already exists or is pending; choose a new revision name")
        pending.touch(exist_ok=False)
        published = False
        try:
            os.replace(temp_parquet, output)
            published = True
            os.replace(temp_manifest, manifest_path)
        except BaseException:
            if published:
                output.unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
            raise
        finally:
            pending.unlink(missing_ok=True)


def _spectrum(parser: ImzMLParser, i: int, reference=None):
    mz, intensity = parser.getspectrum(i)
    if len(mz) != len(intensity) or not len(mz):
        raise ImzMLContractError(f"Spectrum {i}: missing or mismatched arrays")
    if not np.isfinite(mz).all() or not np.isfinite(intensity).all():
        raise ImzMLContractError(f"Spectrum {i}: non-finite values")
    if np.any(mz <= 0) or np.any(np.diff(mz) <= 0):
        raise ImzMLContractError(f"Spectrum {i}: m/z must be positive and strictly increasing")
    if np.any(intensity < 0):
        raise ImzMLContractError(f"Spectrum {i}: negative intensities")
    if reference is not None and not np.array_equal(mz, reference):
        raise ImzMLContractError(f"Spectrum {i}: individual m/z axis is unsupported")
    return mz, intensity


def _coordinates(parser):
    if not parser.coordinates:
        raise ImzMLContractError("No pixels in imzML")
    coords = [tuple(int(v) for v in row) for row in parser.coordinates]
    if len(set(coords)) != len(coords):
        raise ImzMLContractError("Duplicate pixel coordinates")
    if any(x < 0 or y < 0 or z < 0 for x, y, z in coords):
        raise ImzMLContractError("Negative pixel coordinates")
    if len({z for _, _, z in coords}) != 1:
        raise ImzMLContractError("Multiple z planes require separate samples")
    return coords


def inspect_imzml(path: str | Path) -> dict:
    xml, ibd = _pair(path)
    with ibd.open("rb") as binary:
        parser = ImzMLParser(str(xml), ibd_file=binary)
        coords = _coordinates(parser)
        # ★ ver71.0: annotationとは別に、座標由来の物理componentを全画素へ固定する。
        spatial_layout = build_spatial_layout(coords, include_preview=False)
        axis, first = _spectrum(parser, 0)
        _axis_names(axis)
        for i in range(1, len(coords)):
            _spectrum(parser, i, axis)
        return {"pixels": len(coords), "features": len(axis),
                "mz_dtype": str(axis.dtype), "intensity_dtype": str(first.dtype),
                "coordinates_min": list(map(min, zip(*coords))),
                "coordinates_max": list(map(max, zip(*coords))),
                "spatial_layout": strip_runtime_layout(spatial_layout, strip_preview=True),
                "xml": str(xml), "ibd": str(ibd)}


def import_imzml(path: str | Path, output: str | Path, *, registration: dict,
                 block_size=128, progress=None) -> dict:
    """共通軸imzMLの全pixelを標準TIMS Parquetへ登録する。"""
    from app.services.imzml_registration import section_for_component
    from app.services.tims_parquet_contract import standard_schema, feature_names

    # ★ ver74.0: API直接呼出しではUI検査を通らないため、原本を開く前に登録不足を拒否する。
    _require_registration(registration)
    xml, ibd = _pair(path)
    output = Path(output).resolve()
    if not re.fullmatch(r"[^/\\]+\.parquet", output.name, flags=re.I):
        raise ImzMLContractError("Output must be a .parquet sample filename")
    manifest_path = output.with_suffix(".imzml.json")
    if output.exists() or manifest_path.exists():
        raise FileExistsError("Sample already exists; choose a new revision name")
    output.parent.mkdir(parents=True, exist_ok=True)
    binary = ibd.open("rb")
    temp_dir = Path(tempfile.mkdtemp(prefix=".imzml-", dir=output.parent))
    try:
        parser = ImzMLParser(str(xml), ibd_file=binary)
        coords = _coordinates(parser)
        spatial_layout = build_spatial_layout(coords, include_preview=False)
        _require_registration(registration, spatial_layout)
        component_by_source = spatial_layout["_source_components"]
        registered_components = set((registration.get("component_to_section") or {}).keys())
        actual_components = {str(row["component_id"]) for row in spatial_layout.get("components", [])}
        if registered_components != actual_components:
            raise ImzMLContractError("登録切片とimzML座標componentの集合が一致しません")

        axis, first = _spectrum(parser, 0)
        try:
            names = feature_names(axis)
        except ValueError as exc:
            raise ImzMLContractError(str(exc)) from exc
        source_dtype = np.dtype(first.dtype)
        if source_dtype not in (np.dtype("float32"), np.dtype("float64")):
            raise ImzMLContractError(f"Unsupported intensity precision: {source_dtype}")
        output_dtype = np.dtype("float32")
        order = sorted(range(len(coords)), key=lambda i: (coords[i][1], coords[i][0]))
        persisted_layout = strip_runtime_layout(spatial_layout, strip_preview=True)
        schema = standard_schema(axis, registration, extra_metadata={
            "imzml_origin": "1",
            "ua_common_axis": "1",
            "ua_spatial_layout": json.dumps(
                persisted_layout, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")
            ),
        })
        temp_parquet = temp_dir / output.name
        mapping = []
        annotation_counts = {}
        source_sum = output_sum = float32_error = 0.0
        with pq.ParquetWriter(
            temp_parquet, schema, compression="zstd", use_dictionary=["annotation"]
        ) as writer:
            for start in range(0, len(order), block_size):
                indices = order[start:start + block_size]
                matrix = np.empty((len(indices), len(axis)), dtype=output_dtype)
                annotations = []
                for j, i in enumerate(indices):
                    _mz, intensities = _spectrum(parser, i, axis)
                    if np.dtype(intensities.dtype) != source_dtype:
                        raise ImzMLContractError(f"Spectrum {i}: mixed intensity precision")
                    # ★ ver74.0: float64では有限でもfloat32化でInfになる値を公開しない。
                    with np.errstate(over="ignore", under="ignore"):
                        cast = np.asarray(intensities, dtype=output_dtype)
                    if not np.isfinite(cast).all():
                        raise ImzMLContractError(f"Spectrum {i}: float32 intensity overflow")
                    matrix[j] = cast
                    source_sum += float(np.sum(intensities, dtype=np.float64))
                    output_sum += float(np.sum(cast, dtype=np.float64))
                    float32_error += float(np.sum(
                        np.abs(np.asarray(intensities, dtype=np.float64)
                               - np.asarray(cast, dtype=np.float64)), dtype=np.float64
                    ))
                    component_id = component_by_source[i]
                    section = section_for_component(registration, component_id)
                    annotation = section["section_display_name"]
                    annotations.append(annotation)
                    annotation_counts[annotation] = annotation_counts.get(annotation, 0) + 1
                    mapping.append({
                        "source_index": i, "coordinate": list(coords[i]),
                        "component_id": component_id,
                        "section_id": section["section_id"],
                        "section_display_name": annotation,
                    })
                arrays = [
                    pa.array(range(start + 1, start + len(indices) + 1), type=pa.int64()),
                    pa.array([coords[i][0] for i in indices], type=pa.float64()),
                    pa.array([coords[i][1] for i in indices], type=pa.float64()),
                ]
                arrays.extend(pa.array(matrix[:, j], type=pa.float32()) for j in range(len(axis)))
                arrays.append(pa.array(annotations, type=pa.string()))
                writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
                if progress:
                    progress(min(start + len(indices), len(order)), len(order))

        expected_counts = {row["section_display_name"]: int(row["pixel_count"])
                           for row in registration["section_registry"]}
        if annotation_counts != expected_counts:
            raise ImzMLContractError("切片別pixel数が登録情報と一致しません")
        tolerance = max(1e-5, abs(output_sum) * 5e-7) + float32_error
        if abs(source_sum - output_sum) > tolerance:
            raise ImzMLContractError("float32変換後の総強度が許容誤差を超えて変化しました")

        manifest = {
            "schema_version": 4,
            "source_xml": str(xml), "source_ibd": str(ibd),
            "input_representation": "continuous_common_axis",
            "output_representation": "tims_standard_all_sections",
            "mz_axis": axis.tolist(), "column_names": names,
            "mz_dtype": str(axis.dtype), "intensity_dtype": "float32",
            "source_intensity_dtype": str(source_dtype),
            "source_coordinates": mapping,
            "spatial_layout": persisted_layout,
            "section_registry": registration["section_registry"],
            "registration_hash": registration["registration_hash"],
            "normalization": "unknown", "spectrum_type": str(parser.spectrum_mode),
            "pixel_count": len(coords), "feature_count": len(axis),
            "conversion_qc": {
                "source_intensity_sum": source_sum,
                "output_intensity_sum": output_sum,
                "float32_absolute_error_sum": float32_error,
                "annotation_pixel_counts": annotation_counts,
            },
            "lossless_to_source": source_dtype == output_dtype,
        }
        temp_manifest = temp_dir / manifest_path.name
        temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False,
                                             separators=(",", ":")), encoding="utf-8")
        _publish_pair(temp_parquet, temp_manifest, output)
        return {"parquet": str(output), "manifest": str(manifest_path),
                "pixels": len(coords), "features": len(axis)}
    finally:
        binary.close()
        shutil.rmtree(temp_dir, ignore_errors=True)

def export_imzml(sample: str | Path, output_zip: str | Path, *, pixel_ids=None,
                 progress=None) -> dict:
    """Export stored spectra with original coordinates; package a verified pair."""
    sample = Path(sample).resolve()
    manifest_path = sample.with_suffix(".imzml.json")
    if not manifest_path.is_file():
        raise ImzMLContractError("An imzML import manifest is required for exact export")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema_version = manifest.get("schema_version")
    if schema_version not in {1, 2, 3, 4}:
        raise ImzMLContractError("Unsupported import manifest")
    # ★ ver74.0: 出力先だけ検査しても入力Parquetとsidecarの誤対応は検出できない。
    if schema_version in {2, 3, 4}:
        from app.services.imzml_validation import validate_converted_table
        validate_converted_table(sample)
    parquet = pq.ParquetFile(sample)
    names = list(manifest.get("column_names") or _axis_names(np.asarray(manifest["mz_axis"], dtype=np.float64)))
    if not set(["id", *names]).issubset(parquet.schema_arrow.names):
        raise ImzMLContractError("Missing spectral columns")
    axis = np.asarray(manifest["mz_axis"], dtype=manifest["mz_dtype"])
    if (len(names) != len(axis) or len(set(names)) != len(names)
            or not np.isfinite(axis).all() or np.any(axis <= 0) or np.any(np.diff(axis) <= 0)):
        raise ImzMLContractError("Invalid feature column mapping")
    mapping = manifest["source_coordinates"]
    ids = None if pixel_ids is None else set(int(i) for i in pixel_ids)
    if ids is not None and (not ids or min(ids) < 1 or max(ids) > len(mapping)):
        raise ImzMLContractError("Pixel selection is empty or outside the sample")
    output_zip = Path(output_zip).resolve()
    if output_zip.exists():
        raise FileExistsError(output_zip)
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".imzml-export-", dir=output_zip.parent))
    try:
        xml = temp_dir / f"{sample.stem}.imzML"
        count = 0
        seen = set()
        spectrum_type = manifest.get("spectrum_type")
        if spectrum_type not in {"profile", "centroid"}:
            raise ImzMLContractError("Unrecognized spectrum type")
        polarity = (manifest.get("binary_validation") or {}).get("polarity")
        polarity = polarity if polarity in {"positive", "negative"} else None
        with ImzMLWriter(str(xml), mz_dtype=axis.dtype.type,
                         intensity_dtype=np.dtype(manifest["intensity_dtype"]).type,
                         spec_type=spectrum_type, polarity=polarity) as writer:
            for batch in parquet.iter_batches(batch_size=128, columns=["id", *names]):
                data = batch.to_pydict()
                for row, sample_id in enumerate(data["id"]):
                    i = int(sample_id) - 1
                    if i < 0 or i >= len(mapping) or i in seen:
                        raise ImzMLContractError("Invalid or duplicate sample pixel ID")
                    seen.add(i)
                    if ids is not None and sample_id not in ids:
                        continue
                    values = np.asarray([data[name][row] for name in names],
                                        dtype=manifest["intensity_dtype"])
                    if not np.isfinite(values).all() or np.any(values < 0):
                        raise ImzMLContractError("Non-finite output intensity")
                    writer.addSpectrum(axis, values, tuple(mapping[i]["coordinate"]))
                    count += 1
                    if progress:
                        progress(count, len(ids) if ids is not None else len(mapping))
        if len(seen) != len(mapping) or not count:
            raise ImzMLContractError("Incomplete sample or empty selection")
        # ★ ver74.0: pyimzML 1.5.5はMS1にもlevel=0を出力し、自アプリで再入力できなかった。
        # この出力は検証済みMS1行列なので、writerのXMLを公開前に正しいMS1宣言へ直す。
        tree = ET.parse(xml)
        levels = [element for element in tree.iter()
                  if element.get("accession") == "MS:1000511"]
        if not levels:
            raise ImzMLContractError("Written pair is missing an MS level declaration")
        for level in levels:
            level.set("value", "1")
        tree.write(xml, encoding="utf-8", xml_declaration=True)
        from app.services.imzml_validation import inspect_binary_contract
        check = inspect_binary_contract(xml)
        spectra_check = inspect_imzml(xml)
        if (check["pixels"] != count or spectra_check["features"] != len(axis)
                or check["ms_level_interpretation"]["status"] != "explicit_ms1"):
            raise ImzMLContractError("Written pair did not validate")
        # ★ ver74.0: aligned/0補完/float32化を元の疎スペクトルと同一と誤認させない。
        processed = (schema_version == 3 or
                     manifest.get("input_representation") == "processed_centroid_sparse")
        source_dtype = manifest.get("source_intensity_dtype", manifest["intensity_dtype"])
        precision_changed = np.dtype(source_dtype) != np.dtype(manifest["intensity_dtype"])
        source_lossless = manifest.get("lossless_to_source", not processed) is True
        reasons = []
        if processed:
            reasons.append("aligned_zero_filled_common_axis")
        if precision_changed:
            reasons.append("intensity_precision_changed")
        receipt = {
            "schema_version": 2, "source": str(sample),
            "pixels": count, "features": len(axis),
            "pixel_ids": sorted(ids) if ids is not None else "all",
            "normalization": manifest["normalization"],
            "source_representation": manifest.get("input_representation", "common_axis"),
            "export_representation": ("aligned_zero_filled_common_axis" if processed
                                      else "source_common_axis"),
            "lossless_to_source": source_lossless and not processed and not precision_changed,
            "losslessness_scope": "selected_pixel_spectra" if ids is not None else "all_pixel_spectra",
            "source_intensity_dtype": str(np.dtype(source_dtype)),
            "export_intensity_dtype": str(np.dtype(manifest["intensity_dtype"])),
            "nonlossless_reasons": reasons,
            "alignment_ppm": manifest.get("alignment_ppm"),
            "zero_semantics": manifest.get("zero_semantics"),
            "conversion_qc": manifest.get("conversion_qc", {}),
            "ms_level": 1,
        }
        receipt_path = temp_dir / "export_manifest.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        tmp_zip = temp_dir / "output.zip"
        with zipfile.ZipFile(tmp_zip, "w", allowZip64=True) as zf:
            for entry in (xml, xml.with_suffix(".ibd"), receipt_path):
                zf.write(entry, entry.name, compress_type=zipfile.ZIP_STORED)
        os.replace(tmp_zip, output_zip)
        return {"zip": str(output_zip), **receipt}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
