"""★ ver72.0: explicit/inferred MS1とm/z軸契約を分離して検証する。

CV 定義: imzML/imzML imagingMS.obo（IMS:1000080 / 1000090-92 / 1000102-04）。
完全な XSD/CV validator ではない。未知の次元を自動集約しない境界検査。
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from functools import lru_cache
from hashlib import new as new_hash
import json
import os
import shutil
import tempfile
from pathlib import Path
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


def individual_axis_message(meta: dict) -> str:
    """個別m/z軸を無断で共通行列化しない理由を、画面とログで共通化する。"""
    axis = (meta or {}).get("mz_axis_contract") or {}
    minimum = axis.get("feature_count_min")
    maximum = axis.get("feature_count_max")
    unique = axis.get("feature_count_unique")
    if isinstance(minimum, int) and isinstance(maximum, int):
        peak_range = f"{minimum:,}–{maximum:,} peaks/pixel"
        if isinstance(unique, int):
            peak_range += f"（{unique}種類）"
    else:
        peak_range = "peak数は未確定"
    representation = str((meta or {}).get("representation") or "unknown")
    mode = str((meta or {}).get("spectrum_type") or "unknown")
    form = f"{representation}-{mode}"
    return (
        f"画素ごとにm/zピークリストが異なる {form} imzMLです（{peak_range}）。"
        "現在のU_Analysisは、全pixelを同じm/z featureで表せる共通行列だけを直接解析します。"
        "SCiLS等のTop-N／閾値付きpeak listでは、未出力と真の0を区別できないため、"
        "自動union・0補完・広いbinning・mass alignmentは行いません。"
        "共通feature listに対する全pixel強度行列、または共通m/z軸を持つParquetを使用してください。"
    )


def _scan_contract(path, *, cancel=None, verify_checksum=False,
                   reject_individual_axis=False):
    """XML/外部配列を走査し、MS level解釈とm/z軸契約を分けて判定する。"""
    xml, ibd = pair_paths(path)
    size = ibd.stat().st_size
    with ibd.open("rb") as fh:
        binary_uuid = fh.read(16)
    if len(binary_uuid) != 16:
        raise InputPreparationError("ibd の UUID ヘッダーが欠落しています。")

    refs, file_cv, modes, polarities, coordinates = {}, {}, set(), set(), set()
    checksum, declared_uuid = {}, None
    count, byte_width, declared_count = 0, None, None
    array_widths = {}
    feature_counts = []
    explicit_ms1_spectra = 0
    inferred_ms1_spectra = 0

    def cv(node):
        result = {}

        def descendants(elem):
            yield elem
            for child in elem:
                # spectrum全体の属性に別々の外部offsetを混ぜない。
                if _tag(child) == "binaryDataArray" and _tag(node) != "binaryDataArray":
                    continue
                yield from descendants(child)

        for elem in descendants(node):
            if _tag(elem) == "referenceableParamGroupRef":
                name = elem.get("ref")
                if name not in refs:
                    raise InputPreparationError(f"未定義の parameter group: {name}")
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
                        "MS/MS・前駆体・生成物・fragmentation情報を持つ入力は通常MS1解析の対象外です。"
                    )
                names = [str(name or "").lower() for _value, name in effective.values()]
                if any(token in name for name in names for token in _MSN_NAME_TOKENS):
                    raise InputPreparationError("MSn／fragment spectrumを示す測定属性があります。")

                mode = {value for key, value in (("MS:1000127", "centroid"),
                                                  ("MS:1000128", "profile"))
                        if key in effective}
                if len(mode) != 1:
                    raise InputPreparationError("profile / centroid を一意に確認できません。")
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
                    # SCiLS等はMS levelタグを省略することがある。MSnの証拠がなく、
                    # full-scan mass spectrumの構造が揃う場合だけ推定MS1とする。
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

    representation = ("processed" if "IMS:1000031" in file_cv else
                      "continuous" if "IMS:1000030" in file_cv else "unknown")
    feature_counts_unique = sorted(set(feature_counts))
    individual_axis = len(feature_counts_unique) > 1
    axis_contract = {
        "status": "individual_axis" if individual_axis else "common_length_unverified_values",
        "reason": "feature_count_varies" if individual_axis else "mz_values_not_checked",
        "pixel_count": count,
        "feature_count_min": min(feature_counts),
        "feature_count_max": max(feature_counts),
        "feature_count_unique": len(feature_counts_unique),
        "common_feature_count": feature_counts_unique[0] if not individual_axis else None,
        "value_equality_verified": False,
        "direct_matrix_conversion": False if individual_axis else None,
        "top_n_truncation": "unknown",
    }
    if explicit_ms1_spectra == count:
        ms_status = "explicit_ms1"
        confidence = "explicit"
        basis = ["ms_level_1_or_ms1_spectrum"]
    else:
        ms_status = "inferred_ms1"
        confidence = "high" if next(iter(polarities)) != "unknown" else "moderate"
        basis = [
            "mass_spectrum", f"{next(iter(modes))}_spectrum",
            "mz_and_intensity_arrays_only", "single_scan_per_pixel",
            "consistent_polarity", "no_msn_terms", "no_precursor_or_product",
            "no_activation_or_fragmentation",
        ]
    meta = {
        "pixels": count,
        "features": feature_counts_unique[0] if not individual_axis else None,
        "intensity_bytes": byte_width,
        "spectrum_type": next(iter(modes)),
        "polarity": next(iter(polarities)),
        "representation": representation,
        "ms_level_interpretation": {
            "status": ms_status,
            "confidence": confidence,
            "explicit_ms_level": 1 if explicit_ms1_spectra == count else None,
            "explicit_spectra": explicit_ms1_spectra,
            "inferred_spectra": inferred_ms1_spectra,
            "basis": basis,
        },
        "mz_axis_contract": axis_contract,
        "uuid_verified": True,
        "checksums_verified": [],
    }

    if reject_individual_axis and individual_axis:
        raise InputPreparationError(individual_axis_message(meta))

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
        meta["checksums_verified"] = sorted(checksum)

    meta["spatial_layout"] = strip_runtime_layout(
        build_spatial_layout(sorted(coordinates, key=lambda coord: (coord[1], coord[0], coord[2])),
                             include_preview=False),
        strip_preview=True,
    )
    return meta


@lru_cache(maxsize=64)
def _cached_spectral_preflight(path, xml_size, xml_mtime_ns, ibd_size, ibd_mtime_ns):
    del xml_size, xml_mtime_ns, ibd_size, ibd_mtime_ns
    return _scan_contract(path, verify_checksum=False, reject_individual_axis=False)


def inspect_spectral_preflight(path, *, cancel=None):
    """選択画面用の軽量判定。checksumとm/z値一致は解析開始時に確認する。"""
    xml, ibd = pair_paths(path)
    if cancel is not None:
        meta = _scan_contract(xml, cancel=cancel, verify_checksum=False,
                              reject_individual_axis=False)
    else:
        xml_stat = xml.stat()
        ibd_stat = ibd.stat()
        meta = deepcopy(_cached_spectral_preflight(
            str(xml), xml_stat.st_size, xml_stat.st_mtime_ns,
            ibd_stat.st_size, ibd_stat.st_mtime_ns,
        ))
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
    """変換前の完全検査。個別m/z軸は説明付きでR開始前に停止する。"""
    return _scan_contract(path, cancel=cancel, verify_checksum=True,
                          reject_individual_axis=True)

def _bounded_block(features, width, requested, budget_mb):
    if int(requested) < 1 or int(budget_mb) < 1:
        raise InputPreparationError("変換ブロック・メモリ予算は正数で指定してください。")
    # 行列本体、Arrow 列コピー、一時バッファの3本分を上限として見積もる。
    budget = int(budget_mb) * 1024 * 1024
    per_pixel = features * width * 3
    if per_pixel > budget:
        raise InputPreparationError("1画素の変換メモリが IMZML_BLOCK_MB を超えます。設定を確認してください。")
    return min(int(requested), max(1, budget // per_pixel))


def checked_import(path, output, *, block_size=128, memory_budget_mb=64, progress=None, cancel=None):
    """全強度の検査は既存 import の1走査で行う。元の精度・正規化は変更しない。"""
    from app.services.imzml_io import import_imzml, ImzMLContractError
    from filelock import FileLock
    meta = inspect_binary_contract(path, cancel=cancel)
    block = _bounded_block(meta["features"], meta["intensity_bytes"], block_size, memory_budget_mb)
    # cgroup の空き量とホスト空き量の少ない側を使い、ブロックと対応表を確保できるか検査。
    import psutil
    available = psutil.virtual_memory().available
    for limit_path, usage_path in (("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
            ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes")):
        try:
            limit, usage = int(Path(limit_path).read_text()), int(Path(usage_path).read_text())
            available = min(available, max(0, limit - usage))
        except (OSError, ValueError):
            pass
    required = meta["features"] * meta["intensity_bytes"] * block * 3 + meta["pixels"] * 1024
    if required > available * 0.5:
        raise InputPreparationError("変換ブロックと画素対応表を確保するメモリが不足しています。")
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
        check_cancel(cancel)
        stage = Path(tempfile.mkdtemp(prefix=".checked-imzml-", dir=output.parent))
        staged = stage / output.name
        published = False
        owns_pending = False
        try:
            try:
                result = import_imzml(path, staged, block_size=block,
                                      progress=checked_progress)
            except ImzMLContractError as exc:
                if "individual m/z axis" in str(exc):
                    meta["mz_axis_contract"].update({
                        "status": "individual_axis",
                        "reason": "mz_values_differ",
                        "value_equality_verified": True,
                        "direct_matrix_conversion": False,
                    })
                    raise InputPreparationError(individual_axis_message(meta)) from exc
                raise InputPreparationError(f"imzML変換契約に適合しません: {exc}") from exc
            check_cancel(cancel)
            meta["mz_axis_contract"].update({
                "status": "common_axis",
                "reason": "all_mz_values_equal",
                "value_equality_verified": True,
                "direct_matrix_conversion": True,
            })
            from app.services.input_preparation import write_json
            staged_sidecar = staged.with_suffix(".imzml.json")
            manifest = json.loads(staged_sidecar.read_text(encoding="utf-8"))
            manifest["binary_validation"] = meta
            write_json(staged_sidecar, manifest)
            # ★ ver70.0: 手動登録でも保存後検査が終わる前に候補へ公開しない。
            validate_converted_table(staged, cancel=cancel, memory_budget_mb=memory_budget_mb)
            check_cancel(cancel)
            if output.exists() or sidecar.exists():
                raise FileExistsError("Output appeared during conversion; no overwrite is allowed")
            pending.touch(exist_ok=False)
            owns_pending = True
            os.replace(staged, output)
            published = True
            os.replace(staged_sidecar, sidecar)
            check_cancel(cancel)
            return {**result, "parquet": str(output), "manifest": str(sidecar)}
        except BaseException:
            if published:
                output.unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)
            raise
        finally:
            if owns_pending:
                pending.unlink(missing_ok=True)
            shutil.rmtree(stage, ignore_errors=True)


def validate_converted_table(output, *, cancel=None, memory_budget_mb=None):
    """保存後の schema / 全座標 / 元 index / 型 / 有限性 / 非負性を確認する。"""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    from app.services.imzml_io import _axis_names
    output = Path(output)
    manifest = json.loads(output.with_suffix(".imzml.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2:
        raise InputPreparationError("座標component付き変換manifestのschema版が不正です。")
    axis = np.asarray(manifest["mz_axis"], dtype=manifest["mz_dtype"])
    if not len(axis) or not np.isfinite(axis).all() or np.any(axis <= 0) or np.any(np.diff(axis) <= 0):
        raise InputPreparationError("保存した精密 m/z 軸が不正です。")
    names = _axis_names(axis)
    if len(axis) != int(manifest["feature_count"]):
        raise InputPreparationError("保存したfeature数が一致しません。")
    mapping = manifest["source_coordinates"]
    n = int(manifest["pixel_count"])
    if len(mapping) != n or sorted(m["source_index"] for m in mapping) != list(range(n)):
        raise InputPreparationError("元スペクトルの index 対応が不正です。")
    coords = [tuple(m["coordinate"]) for m in mapping]
    if len(set(coords)) != n or len({c[2] for c in coords}) != 1:
        raise InputPreparationError("保存座標の重複または複数 z 面を検出しました。")
    components = [str(m.get("component_id") or "") for m in mapping]
    if any(not value for value in components):
        raise InputPreparationError("元画素と座標componentの対応が欠落しています。")
    rebuilt_layout = strip_runtime_layout(build_spatial_layout(coords, include_preview=False),
                                           strip_preview=True)
    saved_layout = manifest.get("spatial_layout")
    if not isinstance(saved_layout, dict) or saved_layout.get("coordinate_hash") != rebuilt_layout.get("coordinate_hash"):
        raise InputPreparationError("保存した座標layoutと元画素座標が一致しません。")
    expected_components = {c["component_id"]: int(c["pixel_count"])
                           for c in rebuilt_layout.get("components", [])}
    saved_components = {str(c.get("component_id")): int(c.get("pixel_count", -1))
                        for c in saved_layout.get("components", [])}
    if saved_components != expected_components:
        raise InputPreparationError("保存した座標component要約が元画素座標と一致しません。")
    if dict(Counter(components)) != expected_components:
        raise InputPreparationError("座標componentの画素数が一致しません。")
    dtype = np.dtype(manifest["intensity_dtype"])
    if dtype not in (np.dtype("float32"), np.dtype("float64")):
        raise InputPreparationError("保存した強度精度が不正です。")
    expected_type = pa.float32() if dtype == np.dtype("float32") else pa.float64()
    header = manifest.get("binary_validation", {})
    if header and (header.get("pixels") != n or header.get("features") != len(axis)):
        raise InputPreparationError("原本ヘッダーと保存後の画素数・feature数が一致しません。")
    seen = 0
    with pq.ParquetFile(output) as pf:
        expected_names = ["id", "x", "y", "ua_coordinate_component", *names, "annotation"]
        if pf.metadata.num_rows != n or pf.schema_arrow.names != expected_names:
            raise InputPreparationError("Parquet の列順または行数が一致しません。")
        schema_metadata = pf.schema_arrow.metadata or {}
        try:
            parquet_layout = json.loads(schema_metadata[b"ua_spatial_layout"].decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InputPreparationError("Parquet の座標layoutメタデータがありません。") from exc
        if parquet_layout != saved_layout:
            raise InputPreparationError("Parquet と変換manifestの座標layoutが一致しません。")
        typed = ("id", "x", "y", "ua_coordinate_component", "annotation")
        expected_types = [pa.int64(), pa.float64(), pa.float64(), pa.string(), pa.string()]
        if [pf.schema_arrow.field(name).type for name in typed] != expected_types:
            raise InputPreparationError("画素ID/座標/component/annotationの保存型が不正です。")
        if any(pf.schema_arrow.field(name).type != expected_type for name in names):
            raise InputPreparationError("強度 dtype が保存前の型と一致しません。")
        batch_size = _bounded_block(len(names), dtype.itemsize, 128,
            memory_budget_mb if memory_budget_mb is not None else int(os.environ.get("IMZML_BLOCK_MB", "64")))
        for batch in pf.iter_batches(batch_size=batch_size):
            check_cancel(cancel)
            for name in names:
                values = batch.column(batch.schema.get_field_index(name)).to_numpy()
                if not np.isfinite(values).all() or np.any(values < 0):
                    raise InputPreparationError("保存後の強度に不正な値を検出しました。")
            ids = batch.column(batch.schema.get_field_index("id")).to_pylist()
            xs = batch.column(batch.schema.get_field_index("x")).to_pylist()
            ys = batch.column(batch.schema.get_field_index("y")).to_pylist()
            saved_components = batch.column(batch.schema.get_field_index("ua_coordinate_component")).to_pylist()
            for j, sample_id in enumerate(ids):
                absolute = seen + j
                if (sample_id != absolute + 1 or (xs[j], ys[j]) != coords[absolute][:2]
                        or saved_components[j] != components[absolute]):
                    raise InputPreparationError("id・座標・component・元スペクトル対応が一致しません。")
            seen += len(ids)
    if seen != n:
        raise InputPreparationError("保存後の画素数が一致しません。")
    return {"pixels": n, "features": len(axis), "intensity_dtype": str(dtype),
            "spatial_components": len(expected_components), "coordinate_hash": rebuilt_layout["coordinate_hash"],
            "all_pixels_checked": True}
