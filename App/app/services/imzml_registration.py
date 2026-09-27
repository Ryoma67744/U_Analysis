"""imzML座標componentを、全切片を保持する登録済みParquetへ固定する。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from app.services.section_completeness import require_complete_sections


REGISTRATION_SCHEMA_VERSION = 1


def _components(layout: dict) -> dict[str, int]:
    return {
        str(row["component_id"]): int(row.get("pixel_count", 0))
        for row in (layout or {}).get("components", [])
        if row.get("component_id")
    }


def build_registration_spec(file_entry: dict) -> dict:
    """解析選択を含めず、全切片・全componentの登録仕様を確定する。"""
    layout = deepcopy(file_entry.get("spatial_layout") or {})
    components = _components(layout)
    from app.services.section_metadata import effective_registered_sections
    effective = effective_registered_sections(file_entry) if file_entry.get("registered_sections") else []
    sections = require_complete_sections(
        effective
        or file_entry.get("spatial_sections")
        or file_entry.get("sections"),
        components=components,
        filename=file_entry.get("path", ""),
    )
    component_to_section: dict[str, dict] = {}
    for section in sections:
        for component_id in section["component_ids"]:
            component_to_section[component_id] = {
                "section_id": section["section_id"],
                "section_display_name": section["section_display_name"],
                "subject_id": section["subject_id"],
                "group": section["group"],
            }
    registry = [
        {
            "section_id": row["section_id"],
            "section_display_name": row["section_display_name"],
            "subject_id": row["subject_id"],
            "group": row["group"],
            "component_ids": list(row["component_ids"]),
            "pixel_count": int(row["pixel_count"]),
            "metadata_confirmed": True,
        }
        for row in sections
    ]
    core = {
        "schema_version": REGISTRATION_SCHEMA_VERSION,
        "source_file_id": str(file_entry.get("file_id") or ""),
        "source_path": str(Path(file_entry.get("path") or "").expanduser().resolve()),
        "coordinate_hash": str(layout.get("coordinate_hash") or ""),
        "section_registry": registry,
        "component_to_section": component_to_section,
    }
    digest = sha256(json.dumps(core, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")).hexdigest()
    return {**core, "registration_hash": digest}


def registration_for_converter(file_entry: dict | None) -> dict | None:
    if not file_entry:
        return None
    if not (file_entry.get("spatial_layout") or file_entry.get("registered_sections")
            or file_entry.get("spatial_sections")):
        return None
    return build_registration_spec(file_entry)


def section_for_component(registration: dict, component_id: str) -> dict:
    try:
        return registration["component_to_section"][str(component_id)]
    except (KeyError, TypeError) as exc:
        from app.services.imzml_io import ImzMLContractError
        raise ImzMLContractError(
            f"座標component {component_id} に対応する登録切片がありません"
        ) from exc


def registry_metadata(registration: dict) -> dict[str, bytes]:
    registry = registration.get("section_registry") or []
    from app.services.tims_parquet_contract import canonical_registry_hash
    return {
        "ua_dataset_schema": b"tims-standard-parquet-v1",
        "ua_source_type": b"imzml",
        "ua_annotation_role": b"section",
        "ua_registration_complete": b"1",
        "ua_section_registry": json.dumps(
            registry, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
        "ua_section_registry_hash": canonical_registry_hash(registry).encode(),
        "ua_registration_hash": str(registration.get("registration_hash") or "").encode(),
    }

def rewrite_registered_parquet(
    source_parquet: str | Path,
    source_manifest: str | Path,
    registration: dict,
    output_parquet: str | Path,
    *,
    progress=None,
    cancel=None,
) -> dict:
    """既存の標準Parquetの強度行列を再利用し、全切片登録だけを更新する。

    m/z feature作成・imzML/ibd読込は行わない。Parquetのannotationとfooter、
    監査sidecarだけを新しい登録revisionとして書き直す。
    """
    import os
    import shutil
    import tempfile

    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.services.imzml_io import ImzMLContractError
    from app.services.tims_parquet_contract import standard_schema

    # ★ ver74.0: 再登録APIも不完全metadataをI/O前に拒否する。
    from app.services.tims_parquet_contract import validate_registration_spec
    validate_registration_spec(registration)
    source_parquet = Path(source_parquet).expanduser().resolve()
    source_manifest = Path(source_manifest).expanduser().resolve()
    output_parquet = Path(output_parquet).expanduser().resolve()
    output_manifest = output_parquet.with_suffix(".imzml.json")
    if output_parquet.exists() or output_manifest.exists():
        raise FileExistsError("登録済みParquetの出力先が既に存在します")
    if not source_parquet.is_file() or not source_manifest.is_file():
        raise ImzMLContractError("再利用する標準Parquetまたはsidecarがありません")

    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 4:
        raise ImzMLContractError("登録情報だけを更新できるのは標準schema v4です")
    if registration.get("coordinate_hash") != \
            (manifest.get("spatial_layout") or {}).get("coordinate_hash"):
        raise ImzMLContractError("再利用Parquetと切片登録の座標hashが一致しません")

    mapping = manifest.get("source_coordinates") or []
    n_pixels = int(manifest.get("pixel_count", -1))
    if len(mapping) != n_pixels:
        raise ImzMLContractError("再利用Parquetの元pixel対応が不正です")
    annotations = []
    section_ids = []
    for row in mapping:
        section = section_for_component(registration, str(row.get("component_id") or ""))
        annotations.append(section["section_display_name"])
        section_ids.append(section["section_id"])

    axis = np.asarray(manifest.get("mz_axis") or [], dtype=np.float64)
    names = list(manifest.get("column_names") or [])
    if len(axis) != len(names):
        raise ImzMLContractError("再利用Parquetのm/z軸と列対応が不正です")
    old = pq.ParquetFile(source_parquet)
    expected = ["id", "x", "y", *names, "annotation"]
    if old.schema_arrow.names != expected or old.metadata.num_rows != n_pixels:
        raise ImzMLContractError("再利用Parquetが標準列契約に適合しません")

    extra = {}
    for key, value in (old.schema_arrow.metadata or {}).items():
        decoded = key.decode("utf-8", errors="strict")
        if decoded in {"mz_sorted", "ua_dataset_schema", "ua_source_type",
                       "ua_annotation_role", "ua_registration_complete",
                       "ua_section_registry", "ua_section_registry_hash",
                       "ua_registration_hash"}:
            continue
        extra[decoded] = value
    schema = standard_schema(axis, registration, extra_metadata=extra)

    output_parquet.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".registered-parquet-", dir=output_parquet.parent))
    temp_parquet = temp_dir / output_parquet.name
    temp_manifest = temp_dir / output_manifest.name
    try:
        seen = 0
        with pq.ParquetWriter(
            temp_parquet, schema, compression="zstd", use_dictionary=["annotation"]
        ) as writer:
            for batch in old.iter_batches(batch_size=128):
                if cancel and cancel():
                    from app.services.input_preparation import PreparationCancelled
                    raise PreparationCancelled("入力準備を停止しました。")
                count = batch.num_rows
                replacement = pa.array(annotations[seen:seen + count], type=pa.string())
                arrays = [batch.column(i) for i in range(batch.num_columns - 1)]
                arrays.append(replacement)
                writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
                seen += count
                if progress:
                    progress(seen, n_pixels)
        if seen != n_pixels:
            raise ImzMLContractError("登録情報更新時のpixel数が一致しません")

        counts = {}
        for value in annotations:
            counts[value] = counts.get(value, 0) + 1
        expected_counts = {
            row["section_display_name"]: int(row["pixel_count"])
            for row in registration.get("section_registry", [])
        }
        if counts != expected_counts:
            raise ImzMLContractError("更新後のannotation別pixel数が登録情報と一致しません")

        updated_mapping = []
        for index, row in enumerate(mapping):
            item = deepcopy(row)
            item["section_id"] = section_ids[index]
            item["section_display_name"] = annotations[index]
            updated_mapping.append(item)
        updated = deepcopy(manifest)
        updated["section_registry"] = deepcopy(registration["section_registry"])
        updated["registration_hash"] = registration["registration_hash"]
        updated["source_coordinates"] = updated_mapping
        updated.setdefault("conversion_qc", {})["annotation_pixel_counts"] = counts
        updated["registration_rewritten_from"] = str(source_parquet)
        temp_manifest.write_text(
            json.dumps(updated, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        pending = output_parquet.with_suffix(".imzml.pending")
        pending.touch(exist_ok=False)
        published = False
        try:
            os.replace(temp_parquet, output_parquet)
            published = True
            os.replace(temp_manifest, output_manifest)
        except BaseException:
            if published:
                output_parquet.unlink(missing_ok=True)
                output_manifest.unlink(missing_ok=True)
            raise
        finally:
            pending.unlink(missing_ok=True)
        return {
            "parquet": str(output_parquet),
            "manifest": str(output_manifest),
            "pixels": n_pixels,
            "features": len(axis),
            "registration_rewrite": True,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
