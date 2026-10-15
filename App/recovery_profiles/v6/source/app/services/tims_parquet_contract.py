"""CSV変換とimzML変換で共有する標準TIMS Parquet契約。"""
from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from pathlib import Path

import numpy as np
import pyarrow as pa


DATASET_SCHEMA = "tims-standard-parquet-v1"
MZ_NAME_DECIMALS = 6
STANDARD_INTENSITY_DTYPE = np.dtype("float32")
BASE_COLUMNS = ("id", "x", "y")
TAIL_COLUMNS = ("annotation",)


class TimsParquetContractError(ValueError):
    pass


def feature_names(axis, *, decimals=MZ_NAME_DECIMALS) -> list[str]:
    values = np.asarray(axis, dtype=np.float64)
    if not len(values) or np.any(~np.isfinite(values)) or np.any(values <= 0) \
            or np.any(np.diff(values) <= 0):
        raise TimsParquetContractError("m/z軸は正の有限値かつ昇順である必要があります")
    names = [f"{float(value):.{int(decimals)}f}" for value in values]
    if len(names) != len(set(names)):
        raise TimsParquetContractError(
            f"m/zが小数{int(decimals)}桁の列名で衝突します"
        )
    return names


def standard_schema(axis, registration, *, extra_metadata=None) -> pa.Schema:
    validate_registration_spec(registration)
    names = feature_names(axis)
    fields = [pa.field("id", pa.int64()), pa.field("x", pa.float64()),
              pa.field("y", pa.float64())]
    fields.extend(pa.field(name, pa.float32()) for name in names)
    fields.append(pa.field("annotation", pa.string()))
    from app.services.imzml_registration import registry_metadata
    metadata = {
        # 従来SCiLS CSV converterと同じカンマ区切りの精密m/z metadata。
        b"mz_sorted": ",".join(
            f"{float(value):.17g}" for value in np.asarray(axis, dtype=np.float64)
        ).encode("utf-8"),
    }
    metadata.update({key.encode(): value for key, value in registry_metadata(registration).items()})
    for key, value in (extra_metadata or {}).items():
        metadata[str(key).encode()] = (value if isinstance(value, bytes)
                                       else str(value).encode("utf-8"))
    return pa.schema(fields, metadata=metadata)


def parse_section_registry(schema: pa.Schema) -> list[dict]:
    metadata = schema.metadata or {}
    try:
        registry = json.loads(metadata[b"ua_section_registry"].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TimsParquetContractError("Parquetに全切片の登録情報がありません") from exc
    if not isinstance(registry, list):
        raise TimsParquetContractError("Parquetのsection registryが不正です")
    return registry


def _validated_registry(registry, *, components=None) -> list[dict]:
    """保存された登録表を補完せず検査し、不正な型も同じ診断へまとめる。"""
    from app.services.section_completeness import (
        format_section_issues, validate_registered_sections,
    )
    if not isinstance(registry, list) or not registry or any(
            not isinstance(row, dict) for row in registry):
        raise TimsParquetContractError("全切片の登録情報がありません")
    for row in registry:
        if any(not isinstance(row.get(key), str) for key in
               ("section_id", "section_display_name", "subject_id", "group")):
            raise TimsParquetContractError("切片ID・名称・個体ID・群は文字列で登録してください")
        pixels = row.get("pixel_count")
        if isinstance(pixels, bool) or not isinstance(pixels, int) or pixels <= 0:
            raise TimsParquetContractError("登録切片のpixel数は正の整数である必要があります")
        ids = row.get("component_ids")
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(value, str) or not value.strip() for value in ids)
                or len(ids) != len(set(ids))):
            raise TimsParquetContractError("登録切片の座標componentが欠落または重複しています")
    try:
        issues = validate_registered_sections(registry, components=components)
    except (TypeError, ValueError, AttributeError) as exc:
        raise TimsParquetContractError("全切片の登録情報の形式が不正です") from exc
    if issues:
        raise TimsParquetContractError(format_section_issues(issues))
    return registry


def _layout_components(layout) -> dict[str, int]:
    if not isinstance(layout, dict) or not isinstance(layout.get("components"), list):
        raise TimsParquetContractError("座標layoutの形式が不正です")
    components = {}
    for row in layout["components"]:
        if not isinstance(row, dict):
            raise TimsParquetContractError("座標componentの形式が不正です")
        cid, pixels = row.get("component_id"), row.get("pixel_count")
        if (not isinstance(cid, str) or not cid or cid in components
                or isinstance(pixels, bool) or not isinstance(pixels, int) or pixels <= 0):
            raise TimsParquetContractError("座標componentのIDまたはpixel数が不正です")
        components[cid] = pixels
    if not components or not layout.get("coordinate_hash"):
        raise TimsParquetContractError("座標layoutのcomponentまたはhashがありません")
    return components


def validate_registration_spec(registration, *, spatial_layout=None) -> dict:
    """全converter入口で、強度を読む前に同じ全切片契約を検証する。"""
    # ★ ver74.0: 非空dictだけでは未確認登録も変換されていたため、低レベルAPIも検査する。
    if not isinstance(registration, dict):
        raise TimsParquetContractError("全切片の登録情報を確定してから変換してください")
    components = None
    if spatial_layout is not None:
        components = _layout_components(spatial_layout)
        if (not components or registration.get("coordinate_hash") !=
                spatial_layout.get("coordinate_hash")):
            raise TimsParquetContractError("切片登録時と変換時の座標layoutが一致しません")
    registry = _validated_registry(registration.get("section_registry"), components=components)
    expected_map = {
        component: {key: row[key] for key in
                    ("section_id", "section_display_name", "subject_id", "group")}
        for row in registry for component in row["component_ids"]
    }
    if registration.get("component_to_section") != expected_map:
        raise TimsParquetContractError("座標componentの所属と全切片登録情報が一致しません")
    keys = ("schema_version", "source_file_id", "source_path", "coordinate_hash",
            "section_registry", "component_to_section")
    if (registration.get("schema_version") != 1 or
            any(not registration.get(key) for key in
                ("source_file_id", "source_path", "coordinate_hash", "registration_hash"))):
        raise TimsParquetContractError("全切片登録仕様の識別情報が不正です")
    expected_hash = sha256(json.dumps(
        {key: registration[key] for key in keys}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    if registration["registration_hash"] != expected_hash:
        raise TimsParquetContractError("全切片登録仕様のhashが一致しません")
    return registration


def is_registered_schema(schema: pa.Schema) -> bool:
    """標準識別子の一部だけ残る破損ファイルもlegacyに降格させない。"""
    metadata = schema.metadata or {}
    return (any(key in metadata for key in (
        b"ua_dataset_schema", b"ua_section_registry", b"ua_section_registry_hash",
        b"ua_registration_complete", b"ua_registration_hash",
    )) or metadata.get(b"ua_annotation_role") == b"section")


def read_registered_sections(path: str | Path) -> list[dict]:
    """legacyなら空、標準Parquetならfooterと画素所属を厳格に照合して返す。

    sidecarや原本を要求せず、強度列を全量読まずにid・座標・annotationを確認する。
    """
    import pyarrow.parquet as pq

    # ★ ver74.0: 旧readerは破損registryを[]にして完全性ゲートを回避できていた。
    with pq.ParquetFile(path) as parquet:
        schema = parquet.schema_arrow
        if not is_registered_schema(schema):
            return []
        metadata = schema.metadata or {}
        required = {
            b"ua_dataset_schema": DATASET_SCHEMA.encode(),
            b"ua_source_type": b"imzml", b"ua_annotation_role": b"section",
            b"ua_registration_complete": b"1",
        }
        if any(metadata.get(key) != value for key, value in required.items()):
            raise TimsParquetContractError("Parquetの標準TIMS登録メタデータが不正です")
        try:
            layout = json.loads(metadata[b"ua_spatial_layout"].decode("utf-8"))
            components = _layout_components(layout)
            registry = _validated_registry(parse_section_registry(schema), components=components)
            axis_text = metadata[b"mz_sorted"].decode("utf-8").strip()
            axis = np.asarray(json.loads(axis_text) if axis_text.startswith("[")
                              else axis_text.split(","), dtype=np.float64)
            registration_hash = metadata[b"ua_registration_hash"].decode("ascii")
        except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise TimsParquetContractError("Parquetの登録情報または精密m/z軸が不正です") from exc
        if (len(registration_hash) != 64 or
                any(char not in "0123456789abcdef" for char in registration_hash)):
            raise TimsParquetContractError("Parquetのregistration hashが不正です")
        if metadata.get(b"ua_section_registry_hash") != canonical_registry_hash(registry).encode():
            raise TimsParquetContractError("Parquetの全切片registry hashが一致しません")
        names = feature_names(axis)
        if schema.names != ["id", "x", "y", *names, "annotation"]:
            raise TimsParquetContractError("Parquetの列名・順序が標準TIMS契約と一致しません")
        if ([schema.field(name).type for name in ("id", "x", "y", "annotation")] !=
                [pa.int64(), pa.float64(), pa.float64(), pa.string()] or
                any(schema.field(name).type != pa.float32() for name in names)):
            raise TimsParquetContractError("Parquetの列型が標準TIMS契約と一致しません")
        expected = {row["section_display_name"]: row["pixel_count"] for row in registry}
        if sum(expected.values()) != parquet.metadata.num_rows:
            raise TimsParquetContractError("Parquetの行数と全切片pixel数が一致しません")
        counts = Counter()
        seen = 0
        coordinates = []
        annotations = []
        z = layout.get("z")
        if isinstance(z, bool) or not isinstance(z, int) or z < 0:
            raise TimsParquetContractError("Parquetのz座標情報が不正です")
        for batch in parquet.iter_batches(batch_size=65536, columns=["id", "x", "y", "annotation"]):
            if batch.column(0).null_count or batch.column(3).null_count:
                raise TimsParquetContractError("Parquetの画素IDまたはannotationが欠落しています")
            ids = batch.column(0).to_numpy()
            if not np.array_equal(ids, np.arange(seen + 1, seen + batch.num_rows + 1)):
                raise TimsParquetContractError("Parquetの画素IDが連番と一致しません")
            xy = []
            for coordinate in (batch.column(1), batch.column(2)):
                values = coordinate.to_numpy()
                if (coordinate.null_count or not np.isfinite(values).all() or np.any(values < 0)
                        or np.any(values != np.floor(values))):
                    raise TimsParquetContractError("Parquetの画素座標が不正です")
                xy.append(values.tolist())
            coordinates.extend((int(x), int(y), z) for x, y in zip(*xy))
            current_annotations = batch.column(3).to_pylist()
            annotations.extend(current_annotations)
            counts.update(current_annotations)
            seen += batch.num_rows
        if counts != expected:
            raise TimsParquetContractError("Parquet annotationの切片名またはpixel数が登録情報と一致しません")
        # ★ ver74.0: 件数だけの照合では同画素数の切片ラベル入替を検出できなかった。
        from app.services.imzml_spatial_layout import build_spatial_layout, SpatialLayoutError
        try:
            actual_layout = build_spatial_layout(coordinates, include_preview=False)
        except SpatialLayoutError as exc:
            raise TimsParquetContractError(str(exc)) from exc
        if (actual_layout["coordinate_hash"] != layout["coordinate_hash"] or
                _layout_components(actual_layout) != components):
            raise TimsParquetContractError("Parquetの実座標と登録layoutが一致しません")
        component_names = {cid: row["section_display_name"]
                           for row in registry for cid in row["component_ids"]}
        if any(component_names[cid] != annotation for cid, annotation in
               zip(actual_layout["_source_components"], annotations)):
            raise TimsParquetContractError("Parquetの各pixelの切片所属が登録情報と一致しません")
        return registry


def canonical_registry_hash(registry: list[dict]) -> str:
    return sha256(json.dumps(registry, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def expected_columns(axis) -> list[str]:
    return [*BASE_COLUMNS, *feature_names(axis), *TAIL_COLUMNS]
