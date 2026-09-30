"""★ ver77.0: 解析列の末尾 _1/_2 を m/z と誤認しない列役割契約。"""
from __future__ import annotations

import json
import re

ROLE_KEY = b"ua_column_roles"
MANIFEST_KEY = b"ua_export_manifest"
ROLES = frozenset({"identity", "spatial", "annotation", "intensity", "cluster",
                   "embedding", "quality", "roi", "statistic"})
_EMBEDDING = re.compile(r"(?:^|__)(?:UMAP|PC)_[12]$")
_QUALITY = re.compile(r"(?:^|__)(?:TotalCount|nFeature)$")


def column_role(name, cluster_columns=(), overrides=None):
    if overrides and name in overrides:
        item = overrides[name]
        return item.get("role") if isinstance(item, dict) else item
    from app.services.section_group_metadata import METADATA_COLUMNS
    if name == "id" or name in ("CellID", "source_file_id", "source_pixel_id"):
        return "identity"
    if name in ("x", "y", "SpatialX", "SpatialY"):
        return "spatial"
    if name in ("annotation", "Sample", "Method", *METADATA_COLUMNS):
        return "annotation"
    if name in cluster_columns or name in ("UMAP cluster", "Cluster", "Harmony", "RPCA", "PCA", "PCA (uncorrected)"):
        return "cluster"
    if _EMBEDDING.search(str(name)):
        return "embedding"
    if _QUALITY.search(str(name)):
        return "quality"
    if name == "領域名":
        return "roi"
    if name == "n" or str(name).endswith(("_mean", "_sd", "__n")):
        return "statistic"
    return "intensity"


def read_column_roles(schema):
    """新形式が壊れている場合は推測へ戻らず停止。旧形式は None。"""
    raw = (schema.metadata or {}).get(ROLE_KEY)
    if raw is None:
        return None
    try:
        obj = json.loads(raw)
        entries = obj["columns"]
        if obj.get("schema_version") != 1 or not isinstance(entries, list):
            raise ValueError()
        roles = {}
        for entry in entries:
            name, role = entry["name"], entry["role"]
            if not isinstance(name, str) or name in roles or role not in ROLES:
                raise ValueError()
            roles[name] = entry
        if len(schema.names) != len(set(schema.names)) or set(roles) != set(schema.names):
            raise ValueError()
        return roles
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError("Parquet の列役割メタデータが不正です。推測で再入力できません。") from exc


def feature_columns(names, roles=None):
    return [name for name in names if column_role(name, overrides=roles) == "intensity"]


def export_metadata(columns, *, cluster_columns=(), overrides=None, manifest=None):
    if len(columns) != len(set(columns)):
        raise ValueError("出力列名が重複しています。手法名または入力列名を確認してください。")
    entries = []
    for name in columns:
        entry = dict((overrides or {}).get(name) or {})
        entry.update(name=str(name), role=column_role(name, cluster_columns, overrides))
        entries.append(entry)
    dump = lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    sort_keys=True, separators=(",", ":")).encode("utf-8")
    from app.services.result_snapshot import public_metadata
    return {b"ua_artifact_type": b"analysis_export", b"ua_export_schema_version": b"1",
            ROLE_KEY: dump({"schema_version": 1, "columns": entries}),
            MANIFEST_KEY: dump(public_metadata(manifest or {}))}
