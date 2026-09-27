"""explicit/inferred MS1と標準TIMS Parquet変換を検証する。"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import new as new_hash
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET

from app.services.input_preparation import InputPreparationError, check_cancel, pair_paths
from app.services.imzml_spatial_layout import build_spatial_layout, strip_runtime_layout


_MSN_STRUCTURE_TAGS = {
    "precursor", "precursorList", "product", "productList",
    "selectedIon", "selectedIonList", "activation", "isolationWindow",
}
_MSN_NAME_TOKENS = (
    "msn spectrum", "ms2 spectrum", "precursor ion spectrum",
    "product ion spectrum", "collision-induced dissociation",
    "higher energy collisional dissociation", "electron transfer dissociation",
)


def _tag(elem):
    return elem.tag.rsplit("}", 1)[-1]


def _scan_contract(path, *, cancel=None, verify_checksum=False):
    xml, ibd = pair_paths(path)
    size = ibd.stat().st_size
    with ibd.open("rb") as fh:
        binary_uuid = fh.read(16)
    if len(binary_uuid) != 16:
        raise InputPreparationError("ibdのUUIDヘッダーが欠落しています。")

    refs, file_cv, modes, polarities, coordinates = {}, {}, set(), set(), set()
    checksum, declared_uuid = {}, None
    count, byte_width, declared_count = 0, None, None
    array_widths = {}
    feature_counts = []
    explicit_ms1_spectra = inferred_ms1_spectra = 0

    def cv(node):
        result = {}

        def descendants(elem):
            yield elem
            for child in elem:
                if _tag(child) == "binaryDataArray" and _tag(node) != "binaryDataArray":
                    continue
                yield from descendants(child)

        for elem in descendants(node):
            if _tag(elem) == "referenceableParamGroupRef":
                name = elem.get("ref")
                if name not in refs:
                    raise InputPreparationError(f"未定義のparameter group: {name}")
                result.update(refs[name])
            elif _tag(elem) == "cvParam":
                key = elem.get("accession")
                value = (elem.get("value", ""), elem.get("name", ""))
                if key in result and result[key][0] != value[0]:
                    raise InputPreparationError(f"矛盾する測定属性: {key}")
                result[key] = value
        return result

    try:
        for _event, elem in ET.iterparse(xml, events=("end",)):
            kind = _tag(elem)
            if kind == "referenceableParamGroup":
                refs[elem.get("id")] = cv(elem)
                elem.clear()
            elif kind == "fileContent":
                file_cv = cv(elem)
                declared_uuid = file_cv.get("IMS:1000080", (None, ""))[0]
                for term, algo in (("IMS:1000090", "md5"),
                                   ("IMS:1000091", "sha1"),
                                   ("IMS:1000092", "sha256")):
                    if term in file_cv:
                        checksum[algo] = file_cv[term][0].strip().lower()
                elem.clear()
            elif kind == "chromatogram":
                raise InputPreparationError("クロマトグラムをMSIの2次元画像へ集約しません。")
            elif kind == "spectrumList":
                declared_count = elem.get("count")
            elif kind == "spectrum":
                check_cancel(cancel)
                attrs = cv(elem)
                effective = {**file_cv, **attrs}

                level = attrs.get("MS:1000511", file_cv.get("MS:1000511", (None, "")))[0]
                explicit_ms1 = "MS:1000579" in effective
                if level is not None:
                    raw_level = str(level).strip()
                    try:
                        numeric_level = float(raw_level)
                    except ValueError as exc:
                        raise InputPreparationError(
                            f"スペクトル {count}: MS level={raw_level!r} は解釈できません。"
                        ) from exc
                    if numeric_level != 1:
                        raise InputPreparationError(
                            f"スペクトル {count}: MS level={raw_level} は通常MS1解析の対象外です。"
                        )
                    explicit_ms1 = True

                structure_tags = {_tag(node) for node in elem.iter()}
                if "MS:1000580" in effective or structure_tags.intersection(_MSN_STRUCTURE_TAGS):
                    raise InputPreparationError(
                        "MS/MS・前駆体・生成物・fragmentation情報を持つ入力は対象外です。"
                    )
                names = [str(name or "").lower() for _value, name in effective.values()]
                if any(token in name for name in names for token in _MSN_NAME_TOKENS):
                    raise InputPreparationError("MSn／fragment spectrumを示す測定属性があります。")

                mode = {value for key, value in (("MS:1000127", "centroid"),
                                                  ("MS:1000128", "profile"))
                        if key in effective}
                if len(mode) != 1:
                    raise InputPreparationError("profile / centroidを一意に確認できません。")
                modes.update(mode)
                polarity = {value for key, value in (("MS:1000130", "positive"),
                                                      ("MS:1000129", "negative"))
                            if key in effective}
                polarities.update(polarity or {"unknown"})
                if len(modes) != 1 or len(polarities) != 1:
                    raise InputPreparationError("スペクトル種別または極性が混在しています。")
                for _value, name in attrs.values():
                    lowered = str(name or "").lower()
                    if "mobility" in lowered or "drift time" in lowered:
                        raise InputPreparationError("イオンモビリティ次元をMS1行列へ集約しません。")

                scans = [node for node in elem.iter() if _tag(node) == "scan"]
                if len(scans) != 1:
                    raise InputPreparationError("1画素の複数scanは対象外です。")
                xyz = []
                for term, default in (("IMS:1000050", None),
                                      ("IMS:1000051", None),
                                      ("IMS:1000052", "1")):
                    raw = attrs.get(term, (default, ""))[0]
                    if raw is None or not str(raw).isdigit():
                        raise InputPreparationError("画素座標は非負の整数である必要があります。")
                    xyz.append(int(raw))
                coords = tuple(xyz)
                if coords in coordinates:
                    raise InputPreparationError("画素座標が重複しています。")
                if coordinates and coords[2] != next(iter(coordinates))[2]:
                    raise InputPreparationError("複数のz面は別試料として登録してください。")
                coordinates.add(coords)

                arrays = [node for node in elem.iter() if _tag(node) == "binaryDataArray"]
                if len(arrays) != 2:
                    raise InputPreparationError("m/zと強度以外の配列・追加次元は対象外です。")
                lengths_by_type = {}
                array_types = set()
                for array in arrays:
                    array_cv = cv(array)
                    types = {key for key in ("MS:1000514", "MS:1000515") if key in array_cv}
                    widths = {value for key, value in (("MS:1000521", 4),
                                                        ("MS:1000523", 8))
                              if key in array_cv}
                    if len(types) != 1 or len(widths) != 1 or "MS:1000574" in array_cv:
                        raise InputPreparationError(
                            "配列型/精度が不明、または圧縮配列は現在の変換対象外です。"
                        )
                    if array_cv.get("IMS:1000101", ("", ""))[0].lower() not in ("true", "1"):
                        raise InputPreparationError("外部配列の宣言がありません。")
                    array_kind = next(iter(types))
                    width = next(iter(widths))
                    array_types.add(array_kind)
                    if array_kind in array_widths and array_widths[array_kind] != width:
                        raise InputPreparationError("m/zまたは強度の精度が混在しています。")
                    array_widths[array_kind] = width
                    try:
                        offset, length, encoded = (
                            int(array_cv[key][0])
                            for key in ("IMS:1000102", "IMS:1000103", "IMS:1000104")
                        )
                    except (KeyError, ValueError) as exc:
                        raise InputPreparationError("外部配列のoffset / lengthが不正です。") from exc
                    if offset < 16 or length < 1 or encoded != length * width or offset + encoded > size:
                        raise InputPreparationError(
                            "外部配列がibdの範囲外、または長さが矛盾しています。"
                        )
                    lengths_by_type[array_kind] = length
                    if array_kind == "MS:1000515":
                        if byte_width is not None and byte_width != width:
                            raise InputPreparationError("強度精度が混在しています。")
                        byte_width = width
                if array_types != {"MS:1000514", "MS:1000515"}:
                    raise InputPreparationError("m/z配列と強度配列の対応が不正です。")
                if lengths_by_type["MS:1000514"] != lengths_by_type["MS:1000515"]:
                    raise InputPreparationError("同一pixel内でm/z配列と強度配列の長さが異なります。")
                feature_counts.append(lengths_by_type["MS:1000514"])

                if explicit_ms1:
                    explicit_ms1_spectra += 1
                else:
                    if "MS:1000294" not in effective:
                        raise InputPreparationError(
                            "明示的なMS1タグがなく、full-scan mass spectrumとも確認できません。"
                        )
                    inferred_ms1_spectra += 1
                count += 1
                elem.clear()
    except ET.ParseError as exc:
        raise InputPreparationError(f"imzML XMLが不正です: {exc}") from exc

    try:
        if declared_uuid is None or uuid.UUID(declared_uuid).bytes != binary_uuid:
            raise InputPreparationError("imzMLとibdのUUIDが一致しません。")
        if not count or declared_count is None or int(declared_count) != count:
            raise InputPreparationError("スペクトル件数の宣言と内容が一致しません。")
    except (ValueError, AttributeError) as exc:
        raise InputPreparationError("UUID / スペクトル件数が不正です。") from exc

    if verify_checksum and checksum:
        digests = {algo: new_hash(algo) for algo in checksum}
        with ibd.open("rb") as fh:
            while True:
                check_cancel(cancel)
                chunk = fh.read(4 * 1024 * 1024)
                if not chunk:
                    break
                for digest in digests.values():
                    digest.update(chunk)
        if any(digests[algo].hexdigest() != value for algo, value in checksum.items()):
            raise InputPreparationError("ibdの既知checksumが一致しません。")

    representation = ("processed" if "IMS:1000031" in file_cv else
                      "continuous" if "IMS:1000030" in file_cv else "unknown")
    unique_counts = sorted(set(feature_counts))
    sparse = len(unique_counts) > 1
    spatial_layout = strip_runtime_layout(
        build_spatial_layout(sorted(coordinates, key=lambda value: (value[1], value[0], value[2])),
                             include_preview=False),
        strip_preview=True,
    )
    ms_status = "explicit_ms1" if explicit_ms1_spectra == count else "inferred_ms1"
    return {
        "pixels": count,
        # ★ ver74.0: nestedだけへ移すと既存API利用側の総peak診断が失われる。
        "source_peak_count": int(sum(feature_counts)),
        "features": unique_counts[0] if len(unique_counts) == 1 else None,
        "intensity_bytes": byte_width,
        "spectrum_type": next(iter(modes)),
        "polarity": next(iter(polarities)),
        "representation": representation,
        "uuid_verified": True,
        "checksums_verified": sorted(checksum) if verify_checksum else [],
        "spatial_layout": spatial_layout,
        "ms_level_interpretation": {
            "status": ms_status,
            "confidence": "explicit" if ms_status == "explicit_ms1" else "high",
            "explicit_spectra": explicit_ms1_spectra,
            "inferred_spectra": inferred_ms1_spectra,
        },
        "mz_axis_contract": {
            "status": "processed_sparse_candidate" if sparse else "common_length_unverified_values",
            "reason": "feature_count_varies_sparse_representation" if sparse else "mz_values_not_checked",
            "pixel_count": count,
            "feature_count_min": min(feature_counts),
            "feature_count_max": max(feature_counts),
            "feature_count_unique": len(unique_counts),
            "common_feature_count": unique_counts[0] if not sparse else None,
            "source_peak_count": int(sum(feature_counts)),
            "value_equality_verified": False,
            "requires_master_feature_matrix": sparse,
            "direct_matrix_conversion": True if sparse else None,
            "zero_semantics": "not_recorded_in_exported_centroid_spectrum" if sparse else None,
            "top_n_truncation": "unknown",
        },
    }


def inspect_spectral_preflight(path, *, cancel=None):
    meta = _scan_contract(path, cancel=cancel, verify_checksum=False)
    return {
        "schema_version": 1,
        "status": "ok",
        "pixels": meta["pixels"],
        "spectrum_type": meta["spectrum_type"],
        "polarity": meta["polarity"],
        "representation": meta["representation"],
        "ms_level_interpretation": deepcopy(meta["ms_level_interpretation"]),
        "mz_axis_contract": deepcopy(meta["mz_axis_contract"]),
    }


def inspect_binary_contract(path, *, cancel=None):
    return _scan_contract(path, cancel=cancel, verify_checksum=True)


def _bounded_block(features, width, requested, budget_mb):
    if int(requested) < 1 or int(budget_mb) < 1:
        raise InputPreparationError("変換ブロック・メモリ予算は正数で指定してください。")
    budget = int(budget_mb) * 1024 * 1024
    per_pixel = int(features) * int(width) * 3
    if per_pixel > budget:
        raise InputPreparationError("1画素の変換メモリがIMZML_BLOCK_MBを超えます。")
    return min(int(requested), max(1, budget // max(1, per_pixel)))


def checked_import(
    path,
    output,
    *,
    registration,
    block_size=128,
    memory_budget_mb=64,
    alignment_ppm=0.0,
    progress=None,
    cancel=None,
):
    """全切片登録済みimzMLを、標準Parquetへ変換・1回だけ全量検証する。"""
    import psutil
    from filelock import FileLock
    from app.services.imzml_io import import_imzml, ImzMLContractError
    from app.services.imzml_processed import import_processed_imzml, ZERO_SEMANTICS
    from app.services.tims_parquet_contract import (
        TimsParquetContractError, validate_registration_spec,
    )

    # ★ ver74.0: CLIも変換後のvalidatorに任せず、原本走査前に未確認登録を拒否する。
    try:
        validate_registration_spec(registration)
    except TimsParquetContractError as exc:
        raise InputPreparationError(str(exc)) from exc

    try:
        alignment_ppm = float(alignment_ppm)
    except (TypeError, ValueError) as exc:
        raise InputPreparationError("m/zアライメント(ppm)は数値で指定してください。") from exc
    if not math.isfinite(alignment_ppm) or alignment_ppm < 0:
        raise InputPreparationError("m/zアライメント(ppm)は0以上の有限値で指定してください。")
    meta = inspect_binary_contract(path, cancel=cancel)
    try:
        validate_registration_spec(registration, spatial_layout=meta["spatial_layout"])
    except TimsParquetContractError as exc:
        raise InputPreparationError(str(exc)) from exc
    axis_status = (meta.get("mz_axis_contract") or {}).get("status")
    processed_candidate = axis_status == "processed_sparse_candidate"
    if processed_candidate and not (
        meta.get("representation") == "processed" and meta.get("spectrum_type") == "centroid"
    ):
        raise InputPreparationError(
            "可変長m/z配列はprocessed-centroid MS1として確認できる場合だけ行列化します。"
        )

    common_block = None
    if not processed_candidate:
        common_block = _bounded_block(
            meta["features"], meta["intensity_bytes"], block_size, memory_budget_mb
        )
        available = psutil.virtual_memory().available
        required = int(meta["features"]) * int(meta["intensity_bytes"]) * common_block * 3
        if required > available * 0.5:
            raise InputPreparationError("変換ブロックを確保するメモリが不足しています。")

    def checked_progress(done, total):
        check_cancel(cancel)
        if progress:
            progress(done, total)

    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output) + ".import.lock", timeout=0):
        sidecar = output.with_suffix(".imzml.json")
        pending = output.with_suffix(".imzml.pending")
        if output.exists() or sidecar.exists() or pending.exists():
            raise FileExistsError("Sample already exists or is pending; choose a new revision name")
        stage = Path(tempfile.mkdtemp(prefix=".checked-imzml-", dir=output.parent))
        staged = stage / output.name
        published = owns_pending = False
        try:
            processed = processed_candidate
            if processed:
                result = import_processed_imzml(
                    path, staged, registration=registration, alignment_ppm=alignment_ppm,
                    block_size=block_size, memory_budget_mb=memory_budget_mb,
                    progress=checked_progress, cancel=cancel,
                )
            else:
                try:
                    result = import_imzml(
                        path, staged, registration=registration,
                        block_size=common_block, progress=checked_progress,
                    )
                except ImzMLContractError as exc:
                    if ("individual m/z axis" not in str(exc)
                            or meta.get("representation") != "processed"
                            or meta.get("spectrum_type") != "centroid"):
                        raise InputPreparationError(f"imzML変換契約に適合しません: {exc}") from exc
                    result = import_processed_imzml(
                        path, staged, registration=registration, alignment_ppm=alignment_ppm,
                        block_size=block_size, memory_budget_mb=memory_budget_mb,
                        progress=checked_progress, cancel=cancel,
                    )
                    processed = True

            staged_sidecar = staged.with_suffix(".imzml.json")
            manifest = json.loads(staged_sidecar.read_text(encoding="utf-8"))
            if processed:
                qc = manifest.get("conversion_qc") or {}
                meta["features"] = int(manifest["feature_count"])
                meta["mz_axis_contract"].update({
                    "status": "processed_sparse_matrix",
                    "alignment_ppm": alignment_ppm,
                    "master_feature_count": int(manifest["feature_count"]),
                    "source_peak_count": int(qc.get("source_peak_count", 0)),
                    "zero_semantics": ZERO_SEMANTICS,
                })
            else:
                meta["mz_axis_contract"].update({
                    "status": "common_axis", "value_equality_verified": True,
                    "requires_master_feature_matrix": False,
                })
            from app.services.input_preparation import write_json
            manifest["binary_validation"] = meta
            write_json(staged_sidecar, manifest)
            validation_summary = validate_converted_table(
                staged, cancel=cancel, memory_budget_mb=memory_budget_mb
            )
            if output.exists() or sidecar.exists():
                raise FileExistsError("Output appeared during conversion; no overwrite is allowed")
            pending.touch(exist_ok=False)
            owns_pending = True
            os.replace(staged, output)
            published = True
            os.replace(staged_sidecar, sidecar)
            return {
                **result,
                "parquet": str(output), "manifest": str(sidecar),
                "validation_summary": validation_summary,
            }
        except BaseException:
            if published:
                output.unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)
            raise
        finally:
            if owns_pending:
                pending.unlink(missing_ok=True)
            shutil.rmtree(stage, ignore_errors=True)


def _components_from_layout(layout):
    return {str(row["component_id"]): int(row.get("pixel_count", 0))
            for row in (layout or {}).get("components", []) if row.get("component_id")}


def validate_converted_table(output, *, cancel=None, memory_budget_mb=None):
    """新標準(v4)とlegacy(v2/v3)の全画素・全feature・全切片登録を検証する。"""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    from app.services.imzml_io import _axis_names
    from app.services.tims_parquet_contract import (
        canonical_registry_hash, feature_names, read_registered_sections,
    )
    from app.services.section_completeness import validate_registered_sections, format_section_issues

    output = Path(output)
    manifest = json.loads(output.with_suffix(".imzml.json").read_text(encoding="utf-8"))
    schema_version = manifest.get("schema_version")
    if schema_version not in {2, 3, 4}:
        raise InputPreparationError("変換manifestのschema版が不正です。")
    axis = np.asarray(manifest["mz_axis"], dtype=manifest["mz_dtype"])
    if (not len(axis) or not np.isfinite(axis).all() or np.any(axis <= 0)
            or np.any(np.diff(axis) <= 0)):
        raise InputPreparationError("保存したm/z軸が正の有限値・昇順になっていません。")
    names = list(manifest.get("column_names") or
                 (feature_names(axis) if schema_version == 4 else _axis_names(axis)))
    if len(axis) != int(manifest["feature_count"]) or len(names) != len(axis)             or len(set(names)) != len(names):
        raise InputPreparationError("保存したfeature数または列名対応が一致しません。")

    mapping = manifest["source_coordinates"]
    n = int(manifest["pixel_count"])
    if len(mapping) != n or sorted(int(row["source_index"]) for row in mapping) != list(range(n)):
        raise InputPreparationError("元スペクトルのindex対応が不正です。")
    coords = [tuple(row["coordinate"]) for row in mapping]
    if len(set(coords)) != n or len({coord[2] for coord in coords}) != 1:
        raise InputPreparationError("保存座標の重複または複数z面を検出しました。")
    rebuilt_layout = strip_runtime_layout(
        build_spatial_layout(coords, include_preview=False), strip_preview=True
    )
    saved_layout = manifest.get("spatial_layout") or {}
    if saved_layout.get("coordinate_hash") != rebuilt_layout.get("coordinate_hash"):
        raise InputPreparationError("保存した座標layoutと元画素座標が一致しません。")

    dtype = np.dtype(manifest["intensity_dtype"])
    if dtype not in (np.dtype("float32"), np.dtype("float64")):
        raise InputPreparationError("保存した強度dtypeが不正です。")
    is_standard = schema_version == 4
    if is_standard and dtype != np.dtype("float32"):
        raise InputPreparationError("標準TIMS Parquetの強度dtypeはfloat32である必要があります。")
    expected_type = pa.float32() if dtype == np.dtype("float32") else pa.float64()
    seen = 0
    matrix_sum = 0.0
    output_nonzero_count = 0
    annotations: list[str] = []

    with pq.ParquetFile(output) as parquet:
        expected_names = (["id", "x", "y", *names, "annotation"] if is_standard else
                          ["id", "x", "y", "ua_coordinate_component", *names, "annotation"])
        if parquet.metadata.num_rows != n or parquet.schema_arrow.names != expected_names:
            raise InputPreparationError("Parquetの列順または行数が一致しません。")
        base_types = [
            parquet.schema_arrow.field("id").type,
            parquet.schema_arrow.field("x").type,
            parquet.schema_arrow.field("y").type,
            parquet.schema_arrow.field("annotation").type,
        ]
        if base_types != [pa.int64(), pa.float64(), pa.float64(), pa.string()]:
            raise InputPreparationError("id・座標・annotationの保存型が標準契約と一致しません。")
        if any(parquet.schema_arrow.field(name).type != expected_type for name in names):
            raise InputPreparationError("強度dtypeが保存仕様と一致しません。")

        registry = None
        if is_standard:
            # ★ ver74.0: sidecarとの一致だけではfooter内の座標所属破損を見逃すため共通契約で検査する。
            try:
                registry = read_registered_sections(output)
            except Exception as exc:
                raise InputPreparationError(str(exc)) from exc
            manifest_registry = manifest.get("section_registry") or []
            if registry != manifest_registry:
                raise InputPreparationError("Parquetとsidecarの全切片登録情報が一致しません。")
            metadata = parquet.schema_arrow.metadata or {}
            if metadata.get(b"ua_dataset_schema") != b"tims-standard-parquet-v1"                     or metadata.get(b"ua_source_type") != b"imzml"                     or metadata.get(b"ua_annotation_role") != b"section"                     or metadata.get(b"ua_registration_complete") != b"1":
                raise InputPreparationError("Parquetの標準TIMS登録メタデータが不正です。")
            try:
                axis_text = metadata[b"mz_sorted"].decode("utf-8").strip()
                axis_values = (json.loads(axis_text) if axis_text.startswith("[") else
                               [value for value in axis_text.split(",") if value.strip()])
                stored_axis = np.asarray(axis_values, dtype=np.float64)
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise InputPreparationError("Parquetの精密m/z軸を復元できません。") from exc
            if not np.array_equal(stored_axis, np.asarray(axis, dtype=np.float64)):
                raise InputPreparationError("Parquetとsidecarの精密m/z軸が一致しません。")
            expected_registry_hash = canonical_registry_hash(registry).encode()
            if metadata.get(b"ua_section_registry_hash") != expected_registry_hash:
                raise InputPreparationError("Parquetの全切片registry hashが一致しません。")
            registration_hash = str(manifest.get("registration_hash") or "").encode()
            if not registration_hash or metadata.get(b"ua_registration_hash") != registration_hash:
                raise InputPreparationError("Parquetとsidecarのregistration hashが一致しません。")
            components = _components_from_layout(saved_layout)
            issues = validate_registered_sections(registry, components=components)
            if issues:
                raise InputPreparationError(format_section_issues(issues, filename=str(output)))
            component_owner = {}
            for section in registry:
                for component_id in section.get("component_ids", []):
                    component_owner[str(component_id)] = section
            for row in mapping:
                component_id = str(row.get("component_id") or "")
                owner = component_owner.get(component_id)
                if owner is None:
                    raise InputPreparationError("元pixel対応に未登録の座標componentがあります。")
                if (str(row.get("section_id") or "") != str(owner.get("section_id") or "")
                        or str(row.get("section_display_name") or "") !=
                        str(owner.get("section_display_name") or "")):
                    raise InputPreparationError("元pixel対応と全切片registryの所属が一致しません。")

        batch_size = _bounded_block(
            len(names), dtype.itemsize, 128,
            memory_budget_mb if memory_budget_mb is not None
            else int(os.environ.get("IMZML_BLOCK_MB", "64")),
        )
        for batch in parquet.iter_batches(batch_size=batch_size):
            check_cancel(cancel)
            ids = batch.column(batch.schema.get_field_index("id")).to_pylist()
            xs = batch.column(batch.schema.get_field_index("x")).to_pylist()
            ys = batch.column(batch.schema.get_field_index("y")).to_pylist()
            ann = batch.column(batch.schema.get_field_index("annotation")).to_pylist()
            annotations.extend(str(value or "") for value in ann)
            for name in names:
                values = batch.column(batch.schema.get_field_index(name)).to_numpy()
                if not np.isfinite(values).all() or np.any(values < 0):
                    raise InputPreparationError("保存後の強度に不正な値を検出しました。")
                matrix_sum += float(np.sum(values, dtype=np.float64))
                output_nonzero_count += int(np.count_nonzero(values))
            for offset, sample_id in enumerate(ids):
                absolute = seen + offset
                if sample_id != absolute + 1 or (xs[offset], ys[offset]) != coords[absolute][:2]:
                    raise InputPreparationError("id・座標・元スペクトル対応が一致しません。")
            seen += len(ids)

    if seen != n or any(not value for value in annotations):
        raise InputPreparationError("保存後の画素数またはannotationが不正です。")
    annotation_counts = dict(Counter(annotations))
    if is_standard:
        expected_counts = {row["section_display_name"]: int(row["pixel_count"])
                           for row in manifest["section_registry"]}
        if annotation_counts != expected_counts:
            raise InputPreparationError("annotation別pixel数が全切片登録情報と一致しません。")
        mapping_annotations = [str(row.get("section_display_name") or "") for row in mapping]
        if mapping_annotations != annotations:
            raise InputPreparationError("元pixel対応とParquet annotationの順序が一致しません。")

    zero_fraction = 1.0 - output_nonzero_count / max(1, n * len(axis))
    qc = manifest.get("conversion_qc") or {}
    expected_sum = qc.get("output_intensity_sum")
    if expected_sum is not None:
        tolerance = max(1e-5, abs(float(expected_sum)) * 2e-7)
        if abs(matrix_sum - float(expected_sum)) > tolerance:
            raise InputPreparationError("保存後の強度合計が変換QCと一致しません。")
    return {
        "pixels": n,
        "features": len(axis),
        "intensity_dtype": str(dtype),
        "spatial_components": len(_components_from_layout(rebuilt_layout)),
        "coordinate_hash": rebuilt_layout["coordinate_hash"],
        "all_pixels_checked": True,
        "all_sections_registered": bool(is_standard),
        "annotation_pixel_counts": annotation_counts,
        "output_nonzero_count": output_nonzero_count,
        "zero_fraction": zero_fraction,
        "representation": manifest.get("input_representation", "common_axis"),
    }
