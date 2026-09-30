"""軽量な候補発見と、検証済み結果の分類。走査時に巨大 RDS は読まない。"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path

from app.utils.integration_methods import is_auxiliary_rds
from app.services.result_contract import build_result_descriptor, descriptor_method, descriptor_label

_VERIFIED = OrderedDict()
_LOCK = threading.Lock()


def file_signature(path):
    p = Path(path).resolve()
    s = p.stat()
    return (str(p), s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino)


def file_sha256(path, cancel_event=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("結果の検証をキャンセルしました")
            block = stream.read(8 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def result_root(path):
    p = Path(path)
    p = p if p.is_dir() else p.parent
    return p.parent if p.name.casefold() == "rds_files" else p


def read_method_records(folder):
    try:
        data = json.loads((result_root(folder) / "analysis_methods.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def method_record(path, method=None):
    manifest = read_method_records(path)
    rows = manifest.get("methods", {})
    if not isinstance(rows, dict):
        return {}
    # ★ ver77.0: 同じ手法の古い残存 RDS を、新 run の完了状態で裏付けない。
    for row in rows.values():
        if not isinstance(row, dict):
            continue
        names = [row.get("rds_path")]
        names += [v.get("rds_path") for v in row.get("stages", {}).values() if isinstance(v, dict)]
        for name in filter(None, names):
            p = Path(name)
            if not p.is_absolute():
                p = result_root(path) / p
            if p.resolve() == Path(path).resolve():
                result = deepcopy(row)
                # ★ ver77.0: 最新段階が sidecar のとき、主 RDS を sidecar の SHA と比較しない。
                for stage in row.get("stages", {}).values():
                    if not isinstance(stage, dict) or not stage.get("rds_path"):
                        continue
                    stage_path = Path(stage["rds_path"])
                    if not stage_path.is_absolute():
                        stage_path = result_root(path) / stage_path
                    if stage_path.resolve() == Path(path).resolve() and stage.get("artifact_sha256"):
                        result["artifact_sha256"] = stage["artifact_sha256"]
                return result
    # v1 に path が無い場合だけキーの弱い記録を返す。RDS facts が常に優先。
    row = rows.get(str(method or "").casefold(), {})
    if isinstance(row, dict) and not row.get("rds_path") and manifest.get("schema_version", 1) == 1:
        return row
    return {}


def remember_descriptor(path, descriptor):
    try:
        key = file_signature(path)
    except OSError:
        return
    with _LOCK:
        _VERIFIED[key] = deepcopy(descriptor)
        _VERIFIED.move_to_end(key)
        while len(_VERIFIED) > 128:
            _VERIFIED.popitem(last=False)


def cached_descriptor(path):
    try:
        key = file_signature(path)
    except OSError:
        return None
    with _LOCK:
        return deepcopy(_VERIFIED.get(key))


def _hint(path):
    name = path.name.casefold()
    if "rpca" in name:
        return "RPCA"
    if "harmony" in name:
        return "Harmony"
    if "uncorrected" in name:
        return "PCA (uncorrected)"
    if "pca" in name or "single" in name:
        return "PCA"
    return None


def discover_results(folder):
    base = Path(folder)
    if not base.is_dir():
        return []
    dirs = [base / "RDS_Files", base] if (base / "RDS_Files").is_dir() else [base]
    paths = sorted({p.resolve() for d in dirs for p in d.glob("*.rds")
                    if p.is_file() and not is_auxiliary_rds(p)
                    and not p.name.casefold().startswith("step1_")
                    and "seuratlist" not in p.name.casefold()
                    and "seurat_list" not in p.name.casefold()})
    # ★ ver77.0: 子フォルダの別 run を再帰的に集めると、一つの解析結果として混ざる。
    candidates = []
    for path in paths:
        descriptor = cached_descriptor(path)
        method = descriptor_method(descriptor) if descriptor else _hint(path)
        if method is None:
            method = "未確認: " + path.stem
        if descriptor and descriptor.get("clusters", {}).get("kind") == "inherited":
            method += "（既存クラスタ）"
        candidates.append({"rds_path": str(path), "method_key": method,
                           "display_name": descriptor_label(descriptor) if descriptor else "未確認（" + method + "候補）",
                           "result_descriptor": descriptor,
                           "classification_state": "unverified" if descriptor is None else descriptor["classification"]["state"]})
    return candidates


def find_embedding_sidecar(path):
    """旧出力の候補を一意に選ぶ。内容と CellID の最終確認は R 抽出が行う。"""
    path = Path(path)
    # ★ ver77.0: v2 の明示した stage 成果物を、ファイル名による候補より優先する。
    record = method_record(path)
    stage = record.get("stages", {}).get("umap", {})
    if stage.get("status") == "complete" and stage.get("rds_path"):
        root = result_root(path).resolve()
        candidate = Path(stage["rds_path"])
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if candidate != path.resolve() and candidate.is_relative_to(root) and candidate.is_file():
            return str(candidate)
    hint = _hint(path)
    tag = "rpca" if hint == "RPCA" else "harmony" if hint == "Harmony" else "pca"
    matches = [p for p in path.parent.glob("*.rds") if p.name.casefold().startswith("umap_")
               and tag in p.name.casefold() and (tag != "pca" or "rpca" not in p.name.casefold())]
    return str(matches[0]) if len(matches) == 1 else None


def resume_result_paths(folder):
    """再開用候補。旧ファイルは候補に残し、実行側で reduction を検証する。"""
    paths = []
    manifest = read_method_records(folder)
    rows = manifest.get("methods", {})
    if manifest.get("schema_version") == 2 and isinstance(rows, dict):
        for row in rows.values():
            stage = row.get("stages", {}).get("reduction", {}) if isinstance(row, dict) else {}
            if stage.get("status") in {"complete", "completed"} and stage.get("rds_path"):
                path = Path(stage["rds_path"])
                if not path.is_absolute():
                    path = result_root(folder) / path
                if path.is_file():
                    paths.append(str(path.resolve()))
        return list(dict.fromkeys(paths))
    return [candidate["rds_path"] for candidate in discover_results(folder)]


def verify_embedding_origin(source_path, sidecar_path, sidecar_sha, facts, records):
    """CellID 一致と計算由来を分離し、v2 の保存記録が結ぶ sidecar だけ確定する。"""
    embedding = deepcopy(facts.get("embedding") or {})
    embedding.update(space=None, assay=None, origin_state="unknown")
    stage = records.get("stages", {}).get("umap", {})
    numerical = stage.get("numerical") or {}
    recorded_path = Path(stage.get("rds_path") or ".")
    if not recorded_path.is_absolute():
        recorded_path = result_root(source_path) / recorded_path
    reduction = numerical.get("reduction")
    available = facts.get("reductions", {}).get(reduction) or {}
    if (stage.get("status") == "complete" and stage.get("artifact_sha256") == sidecar_sha
            and recorded_path.resolve() == Path(sidecar_path).resolve()
            and facts.get("cell_ids_r_hash") and numerical.get("cell_ids_hash") == facts["cell_ids_r_hash"]
            and reduction and available.get("assay") == numerical.get("source_assay")):
        embedding.update(space=reduction, assay=available["assay"], origin_state="recorded",
                         parameters=deepcopy(numerical.get("effective") or {}))
    return embedding


def resolve_result(candidate, facts, records=None):
    source = deepcopy(candidate.get("source", {}))
    source.setdefault("path", candidate.get("rds_path"))
    return build_result_descriptor(source, facts, records)
