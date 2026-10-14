"""★ ver78.0: 表の編集だけで全XMLを再走査しない。workerの正式検証は維持する。"""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from app.services.input_preparation import InputPreparationError, pair_paths


def source_stat(path):
    xml, ibd = pair_paths(path)
    result = {}
    for kind, p in (("xml", xml), ("ibd", ibd)):
        stat = p.stat()
        result[kind] = {"path": str(p), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return result


@lru_cache(maxsize=32)
def _inspect(xml, xml_size, xml_mtime, ibd, ibd_size, ibd_mtime):
    from app.services.imzml_spatial_layout import inspect_imzml_spatial_layout, SpatialLayoutError
    from app.services.imzml_validation import inspect_spectral_preflight
    result = {}
    try:
        result["spectral_preflight"] = inspect_spectral_preflight(xml)
    except (OSError, InputPreparationError) as exc:
        result["spectral_preflight"] = {"schema_version": 1, "status": "invalid", "error": str(exc)}
    try:
        result["spatial_layout"] = inspect_imzml_spatial_layout(xml)
    except (OSError, SpatialLayoutError) as exc:
        result["spatial_error"] = str(exc)
    return result


def inspect_selected_input(path):
    before = source_stat(path)
    x, b = before["xml"], before["ibd"]
    result = deepcopy(_inspect(x["path"], x["size"], x["mtime_ns"], b["path"], b["size"], b["mtime_ns"]))
    if source_stat(path) != before:
        raise InputPreparationError(f"{Path(path).name}: 選択の確認中に原本が変更されました。再読込してください。")
    result["source_stat"] = before
    return result
