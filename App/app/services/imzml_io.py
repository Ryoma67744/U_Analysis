"""Lossless common-axis imzML exchange for TIMS Parquet samples.

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
    if len({f"{mz:.5f}" for mz in axis}) != len(names):
        raise ImzMLContractError("m/z values collide in the five-decimal R feature names")
    return names


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


def import_imzml(path: str | Path, output: str | Path, *, block_size=128,
                 progress=None) -> dict:
    """Atomically register a complete, common-axis single-plane spectrum table."""
    xml, ibd = _pair(path)
    output = Path(output).resolve()
    if not re.fullmatch(r"[^/\\]+\.parquet", output.name, flags=re.I):
        raise ImzMLContractError("Output must be a .parquet sample filename")
    manifest_path = output.with_suffix(".imzml.json")
    if output.exists() or manifest_path.exists():
        raise FileExistsError("Sample already exists; choose a new revision name")
    output.parent.mkdir(parents=True, exist_ok=True)
    binary = ibd.open("rb")
    parser = ImzMLParser(str(xml), ibd_file=binary)
    temp_dir = Path(tempfile.mkdtemp(prefix=".imzml-", dir=output.parent))
    try:
        coords = _coordinates(parser)
        spatial_layout = build_spatial_layout(coords, include_preview=False)
        component_by_source = spatial_layout["_source_components"]
        axis, first = _spectrum(parser, 0)
        names = _axis_names(axis)
        dtype = np.dtype(first.dtype)
        if dtype not in (np.dtype("float32"), np.dtype("float64")):
            raise ImzMLContractError(f"Unsupported intensity precision: {dtype}")
        order = sorted(range(len(coords)), key=lambda i: (coords[i][1], coords[i][0]))
        fields = [pa.field("id", pa.int64()), pa.field("x", pa.float64()),
                  pa.field("y", pa.float64()), pa.field("ua_coordinate_component", pa.string())]
        fields += [pa.field(name, pa.float32() if dtype == np.float32 else pa.float64())
                   for name in names]
        fields += [pa.field("annotation", pa.string())]
        persisted_layout = strip_runtime_layout(spatial_layout, strip_preview=True)
        schema = pa.schema(fields, metadata={
            b"mz_sorted": json.dumps(axis.tolist()).encode(),
            b"imzml_origin": b"1",
            b"ua_spatial_layout": json.dumps(persisted_layout, ensure_ascii=False,
                                               sort_keys=True).encode("utf-8"),
        })
        temp_parquet = temp_dir / output.name
        mapping = []
        with pq.ParquetWriter(temp_parquet, schema, compression="zstd") as writer:
            for start in range(0, len(order), block_size):
                indices = order[start:start + block_size]
                matrix = np.empty((len(indices), len(axis)), dtype=dtype)
                for j, i in enumerate(indices):
                    mz, intensities = _spectrum(parser, i, axis)
                    if intensities.dtype != dtype:
                        raise ImzMLContractError(f"Spectrum {i}: mixed intensity precision")
                    matrix[j] = intensities
                    mapping.append({"source_index": i, "coordinate": coords[i],
                                    "component_id": component_by_source[i]})
                arrays = [pa.array(range(start + 1, start + len(indices) + 1), type=pa.int64()),
                          pa.array([coords[i][0] for i in indices], type=pa.float64()),
                          pa.array([coords[i][1] for i in indices], type=pa.float64()),
                          pa.array([component_by_source[i] for i in indices], type=pa.string())]
                arrays.extend(pa.array(matrix[:, j]) for j in range(len(axis)))
                arrays.append(pa.array(["Unannotated"] * len(indices)))
                writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
                if progress:
                    progress(min(start + len(indices), len(order)), len(order))
        manifest = {"schema_version": 2, "source_xml": str(xml), "source_ibd": str(ibd),
                    "mz_axis": axis.tolist(), "mz_dtype": str(axis.dtype),
                    "intensity_dtype": str(dtype), "source_coordinates": mapping,
                    "spatial_layout": persisted_layout,
                    "normalization": "unknown", "spectrum_type": str(parser.spectrum_mode),
                    "pixel_count": len(coords), "feature_count": len(axis)}
        temp_manifest = temp_dir / manifest_path.name
        temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        # A manifest is the completion marker; publish it only after the data file.
        pending = output.with_suffix(".imzml.pending")
        pending.touch(exist_ok=False)
        try:
            os.replace(temp_parquet, output)
            os.replace(temp_manifest, manifest_path)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        finally:
            pending.unlink(missing_ok=True)
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
    if manifest.get("schema_version") not in {1, 2}:
        raise ImzMLContractError("Unsupported import manifest")
    parquet = pq.ParquetFile(sample)
    names = _axis_names(np.asarray(manifest["mz_axis"], dtype=np.float64))
    if not set(["id", *names]).issubset(parquet.schema_arrow.names):
        raise ImzMLContractError("Missing spectral columns")
    axis = np.asarray(manifest["mz_axis"], dtype=manifest["mz_dtype"])
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
        with ImzMLWriter(str(xml), mz_dtype=axis.dtype.type,
                         intensity_dtype=np.dtype(manifest["intensity_dtype"]).type,
                         spec_type=spectrum_type) as writer:
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
                    if not np.isfinite(values).all():
                        raise ImzMLContractError("Non-finite output intensity")
                    writer.addSpectrum(axis, values, tuple(mapping[i]["coordinate"]))
                    count += 1
                    if progress:
                        progress(count, len(ids) if ids is not None else len(mapping))
        if len(seen) != len(mapping) or not count:
            raise ImzMLContractError("Incomplete sample or empty selection")
        check = inspect_imzml(xml)
        if check["pixels"] != count or check["features"] != len(axis):
            raise ImzMLContractError("Written pair did not validate")
        receipt = {"schema_version": 1, "source": str(sample),
                   "pixels": count, "features": len(axis),
                   "pixel_ids": sorted(ids) if ids is not None else "all",
                   "normalization": manifest["normalization"]}
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
